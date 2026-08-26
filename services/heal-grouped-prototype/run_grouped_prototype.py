#!/usr/bin/env python3
"""Run HEAL's isolated, human-signed grouped prototype lane end to end.

The service never reads scientific decisions from the active mechanism registry.
The registry is touched only to verify its immutable SHA-256 before and after.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parents[1]
LLM1_DIR = APP_ROOT / "services" / "heal-grouped-individual-interpretation"
REPORT_PATH = APP_ROOT / "services" / "heal-final-report" / "render_final_report.py"
DEFAULT_SNAPSHOT_ROOT = Path(r"F:\Heal by FON\data\prototype\tier1-105-human-signed-20260823")
DEFAULT_SNAPSHOT = DEFAULT_SNAPSHOT_ROOT / "tier1_prototype_snapshot_v1_human_signed.json"
DEFAULT_CANDIDATE = DEFAULT_SNAPSHOT_ROOT / "prototype_candidate_manifest.json"
DEFAULT_COVERAGE = DEFAULT_SNAPSHOT_ROOT / "prototype_coverage_manifest_v1.json"
ACTIVE_REGISTRY = Path(r"F:\Heal by FON\data\canon\curation\mechanism_registry_v1.csv")
EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"
MODEL = os.environ.get("HEAL_PROTOTYPE_LLM1_MODEL", "gpt-5.6-luna")
LLM2_MODEL = os.environ.get("HEAL_PROTOTYPE_LLM2_MODEL", "gpt-5.6-luna")
DEFAULT_ESTIMATE_CAP_USD = float(os.environ.get("HEAL_PROTOTYPE_MAX_ESTIMATED_COST_USD", "5"))
DEFAULT_HARD_CAP_USD = float(os.environ.get("HEAL_PROTOTYPE_HARD_CAP_USD", "10"))
LUNA_INPUT_PER_MILLION = 0.20
LUNA_CACHED_INPUT_PER_MILLION = 0.02
LUNA_OUTPUT_PER_MILLION = 1.20
PROTOTYPE_PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"


def import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


llm1 = import_module("heal_grouped_llm1", LLM1_DIR / "interpret_gene_module_groups.py")
final_report = import_module("heal_final_report", REPORT_PATH)
contracts = import_module("heal_grouped_prototype_contracts", SCRIPT_DIR / "grouped_contracts.py")

has_affirmative_pattern = contracts.has_affirmative_pattern
has_prohibited_language = contracts.has_prohibited_language
normalize_evidence_used = contracts.normalize_evidence_used
runtime_evidence_ids = contracts.runtime_evidence_ids
variant_specific_inference_gate = contracts.variant_specific_inference_gate


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_progress(path: Path | None, *, substage: str, processed: int, total: int,
                    message: str, metrics: dict | None = None) -> None:
    if path is None:
        return
    percent = 100 if total <= 0 else min(100, max(0, round(processed * 100 / total)))
    write_json(path, {
        "substage": substage, "processed": processed, "total": total, "percent": percent,
        "unit": "groups", "message": message, "metrics": metrics or {}, "updatedAt": now_iso(),
    })


def estimated_call_cost(payload: dict, prompt: str, *, reserved_output_tokens: int = 4000) -> float:
    # Conservative tokenizer-independent projection. JSON/prompt UTF-8 bytes / 3
    # is intentionally above the typical English/JSON token density.
    input_tokens = (len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + len(prompt.encode("utf-8"))) / 3
    return round((input_tokens * LUNA_INPUT_PER_MILLION + reserved_output_tokens * LUNA_OUTPUT_PER_MILLION) / 1_000_000, 8)


def technical_retry_allowed(error: Exception) -> bool:
    text = str(error).lower()
    return any(token in text for token in (
        "http_429", "http_500", "http_502", "http_503", "http_504",
        "temporarily unavailable", "connection reset", "remote end closed", "truncated",
    ))


def global_technical_blocker_code(error: Exception) -> str | None:
    """Classify campaign-wide failures without persisting provider error bodies."""
    message = str(error).lower()
    if "http_401" in message or "invalid_api_key" in message:
        return "openai_authentication_failed"
    if "http_403" in message:
        return "openai_access_forbidden"
    if "insufficient_quota" in message or "billing_hard_limit" in message:
        return "openai_quota_unavailable"
    return None


def call_with_one_technical_retry(*, payload: dict, api_key: str, model: str, prompt: str,
                                  schema: dict, timeout_seconds: int) -> tuple[dict, dict]:
    errors: list[str] = []
    for attempt in (1, 2):
        try:
            item, metadata = llm1.call_openai_structured(
                payload, api_key=api_key, model=model, system_prompt=prompt,
                schema=schema, timeout_seconds=timeout_seconds, reasoning_effort="low",
            )
            metadata["attempt_count"] = attempt
            metadata["retry_performed"] = attempt > 1
            metadata["prior_attempt_errors"] = list(errors)
            return item, metadata
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
            if attempt == 2 or not technical_retry_allowed(error):
                raise RuntimeError(" | ".join(errors)) from error
            time.sleep(2)
    raise AssertionError("unreachable")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def responses_compatible_schema(value: Any) -> Any:
    """Add explicit primitive types required by strict Responses schemas.

    The benchmark schema file remains byte-for-byte unchanged; this produces a
    deterministic runtime projection for API compatibility.
    """
    if isinstance(value, list):
        return [responses_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    # `uniqueItems` is part of general JSON Schema but is not accepted by the
    # Responses structured-output subset. Uniqueness remains enforced by the
    # deterministic validators below.
    result = {
        key: responses_compatible_schema(item)
        for key, item in value.items()
        if key != "uniqueItems"
    }
    if "const" in result and "type" not in result:
        constant = result["const"]
        inferred = "boolean" if isinstance(constant, bool) else "integer" if isinstance(constant, int) else "number" if isinstance(constant, float) else "string" if isinstance(constant, str) else None
        if inferred:
            result["type"] = inferred
    return result


def prototype_critical_semantic_errors(item: dict, payload: dict) -> list[str]:
    """Apply the legacy safety families with negation-aware semantics."""
    text = llm1.interpretation_text(item)
    errors = [
        code
        for code, pattern in llm1.CRITICAL_LANGUAGE_PATTERNS.items()
        if has_affirmative_pattern(text, pattern)
    ]
    if payload.get("professional_curation", {}).get("pgx_escalation_allowed") is not True:
        pgx_pattern = re.compile(
            r"\b(actionable|accionable|change medication|cambiar medicaci[oó]n|adjust dose|ajustar dosis)\b",
            re.I,
        )
        if has_affirmative_pattern(text, pgx_pattern):
            errors.append("unconfirmed_pgx_actionability")
    if payload.get("gwas_evidence_summary", {}).get("prioritized_clusters"):
        gwas_pattern = re.compile(
            r"\b(causes?|causa|predicts?|predice|individual risk|riesgo individual)\b",
            re.I,
        )
        if has_affirmative_pattern(text, gwas_pattern):
            errors.append("gwas_causality_or_individual_risk")
    return sorted(set(errors))


def load_payloads(path: Path) -> list[dict]:
    return llm1.read_payloads(path)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def usage_row(metadata: dict, *, stage: str, group_id: str, role: str, elapsed: float, model: str = MODEL) -> dict:
    usage = metadata.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    reasoning_tokens = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
    uncached = max(0, input_tokens - cached_tokens)
    cost = (
        uncached * LUNA_INPUT_PER_MILLION
        + cached_tokens * LUNA_CACHED_INPUT_PER_MILLION
        + output_tokens * LUNA_OUTPUT_PER_MILLION
    ) / 1_000_000
    return {
        "stage": stage, "group_id": group_id, "role": role,
        "requested_model": model, "effective_model": metadata.get("effective_model"),
        "response_id": metadata.get("response_id"), "reasoning_effort": "low",
        "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens, "reasoning_tokens": reasoning_tokens,
        "latency_seconds": round(elapsed, 3), "attempt_count": int(metadata.get("attempt_count") or 1),
        "estimated_cost_usd": round(cost, 8), "cost_observable": True,
    }


def validate_prototype_inputs(snapshot: dict, candidate: dict, coverage: dict) -> None:
    schema_version = snapshot.get("schema_version")
    group_count = len(snapshot.get("groups") or [])
    expected_count = 105 if schema_version == "tier1_prototype_snapshot_v1_human_signed" else 12
    if schema_version not in {"prototype_curation_snapshot_v1", "tier1_prototype_snapshot_v1_human_signed"} or group_count != expected_count:
        raise ValueError("Prototype snapshot schema or closed group count is invalid")
    if candidate.get("prototype_readiness") not in {"prototype_candidate", "approved_for_sandbox_smoke_test"}:
        raise ValueError("Prototype candidate manifest is not ready")
    if candidate.get("formal_validation_readiness") != "pending_new_unseen_holdout":
        raise ValueError("Formal validation state is inconsistent")
    if coverage.get("canonical_group_count") != 180 or coverage.get("covered_count") != group_count:
        raise ValueError("Prototype coverage manifest is not closed over 180 groups")
    if coverage.get("not_covered_count") != 180 - group_count or len(coverage.get("groups") or []) != 180:
        raise ValueError("Prototype coverage counts are inconsistent")
    group_ids = [row.get("group_id") for row in snapshot["groups"]]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("Prototype snapshot contains duplicate groups")
    statuses = {row["group_id"]: row["coverage_status"] for row in coverage["groups"]}
    for row in snapshot["groups"]:
        if statuses.get(row["group_id"]) != "covered_by_prototype_snapshot":
            raise ValueError(f"Covered snapshot group missing from coverage manifest: {row['group_id']}")
    if schema_version == "tier1_prototype_snapshot_v1_human_signed":
        if snapshot.get("prototype_readiness") != "approved_for_sandbox_smoke_test":
            raise ValueError("Human-signed prototype is not approved for sandbox smoke testing")
        if any(row.get("core_status") != "approved" for row in snapshot["groups"]):
            raise ValueError("Human-signed snapshot contains a non-approved decision")
        identity = {key: value for key, value in snapshot.items() if key not in {"created_at", "snapshot_sha256"}}
        if snapshot.get("snapshot_sha256") != sha256_json(identity):
            raise ValueError("Human-signed snapshot identity hash mismatch")
        if candidate.get("hashes", {}).get("snapshot") != snapshot.get("snapshot_sha256"):
            raise ValueError("Candidate manifest is not bound to the snapshot")


def build_envelope(payload: dict, scientific: dict, candidate: dict, snapshot: dict) -> dict:
    focus_refs = sorted({str(row.get("variant_ref")) for row in payload.get("focus_variant_evidence") or [] if row.get("variant_ref")})
    variant_refs = sorted(llm1.v5_variant_refs(payload))
    scientific_ids = sorted(set(scientific["valid_evidence_ids"]))
    runtime_ids = sorted(runtime_evidence_ids(payload))
    evidence_ids = sorted(set(scientific_ids) | set(runtime_ids))
    scientific_ceiling = scientific["prototype_inference_ceiling"]
    variant_gate = variant_specific_inference_gate(payload, scientific_ceiling)
    effective_ceiling = contracts.effective_runtime_ceiling(scientific_ceiling, variant_gate)
    decision = {
        "group_id": scientific["group_id"], "core_status": scientific["core_status"],
        "scientific_inference_ceiling": scientific_ceiling, "effective_runtime_ceiling": effective_ceiling,
        "dominant_direction": scientific["dominant_direction"], "material_conflict": scientific["material_conflict"],
        "limitations": scientific["limitations"], "evidence_records": scientific.get("evidence_records") or [],
    }
    model_payload_fields = {
        "payload_schema_version", "execution_mode", "group_id", "gene", "module_id", "group_context",
        "focus_variant_evidence", "clinical_evidence_summary", "gwas_evidence_summary",
        "publication_evidence_digest", "supporting_context", "transcript_discordant_context",
        "identity_unresolved", "source_failures", "deterministic_summary", "evidence_coverage",
        "compression_metadata", "provenance", "target_gene_discordant_context",
        "allele_specific_frequency_summary", "professional_curation", "patient_context",
        "traceability_allowlist", "input_completeness", "age_context", "release_context",
    }
    model_payload = {key: copy.deepcopy(value) for key, value in payload.items() if key in model_payload_fields}
    return {
        "schema_version": "llm1_prototype_envelope_v3",
        "payload_v7": model_payload,
        "scientific_decision": decision,
        "runtime_variant_gate": variant_gate,
        "technical_gates": {
            "identity": payload.get("group_id") == scientific.get("group_id") and payload.get("gene") == scientific.get("gene"),
            "input_completeness": payload.get("input_completeness", {}).get("absence_semantics") == "not_observed_callability_unknown",
            "evidence_allowlist": True, "variant_allowlist": True,
            "payload_schema": payload.get("payload_schema_version") == "llm1_group_payload_v7",
        },
        "coverage_status": "covered_by_prototype_snapshot",
        "readiness": {
            "prototype_readiness": snapshot.get("prototype_readiness", "prototype_candidate"),
            "formal_validation_readiness": "pending_new_unseen_holdout",
        },
        "allowlists": {"scientific_evidence_ids": scientific_ids, "runtime_evidence_ids": runtime_ids,
                       "evidence_ids": evidence_ids, "variant_refs": variant_refs, "focus_variant_refs": focus_refs},
        "provenance": {
            "decision_signature": scientific.get("provenance", {}).get("decision_signature", "legacy_signed_gold"),
            "allowlist": scientific.get("provenance", {}).get("allowlist", "signed_gold"),
            "legacy_curation_override_scope": "curation_fields_only",
        },
        "source_payload_audit": {
            "sha256": sha256_json(payload),
            "excluded_model_fields": sorted(set(payload) - set(model_payload)),
            "legacy_context_citable": False,
        },
        "prototype_hashes": {
            "candidate_manifest": candidate["manifest_sha256"], "snapshot": snapshot["snapshot_sha256"],
            "payload": sha256_json(payload), "packet": scientific.get("provenance", {}).get("packet_sha256", "0" * 64),
        },
    }


def validate_envelope(envelope: dict) -> None:
    contracts.validate_envelope(envelope)


def validate_llm1_output(item: dict, envelope: dict) -> list[str]:
    return contracts.validate_llm1_output(item, envelope, prototype_critical_semantic_errors)


CONTENT_SAFETY_ERRORS = {
    "diagnosis_claim", "individual_gwas_risk", "gwas_causality_or_individual_risk",
    "treatment_recommendation", "new_testing_recommendation", "generic_professional_referral",
    "unconfirmed_pgx_actionability", "llm1_prohibited_language",
    "llm1_evidence_outside_allowlist", "llm1_variant_outside_allowlist",
    "llm1_focus_variant_outside_allowlist", "llm1_inference_ceiling_exceeded",
    "llm1_scientific_evidence_double_counting",
}


def quarantine_class_for(errors: list[str] | None, error: Exception | str) -> str:
    codes = set(errors or [])
    if codes and codes <= CONTENT_SAFETY_ERRORS:
        return "content_safety"
    if codes:
        return "structural_contract"
    return "technical_isolated"


def deterministic_coverage_card(group_id: str, status: str, *, scientific: dict | None = None, payload: dict | None = None) -> dict:
    gene, module_id = group_id.split(":", 1)
    focus = (payload or {}).get("focus_variant_evidence") or []
    return {
        "group_id": group_id, "gene": gene, "module_id": module_id, "status": status,
        "coverage_status": "covered_by_prototype_snapshot" if scientific else "not_covered_by_prototype_snapshot",
        "inference_mode": "abstained_insufficient_evidence", "final_confidence_level": "Abstain",
        "review_priority": "none", "requires_professional_review": False,
        "interpretation_one_sentence_es": (
            "Grupo cubierto, sin variante foco observada; la capacidad de llamada es desconocida."
            if status == "covered_no_observed_variant" else
            "Este grupo no está cubierto por el snapshot científico del prototipo."
        ),
        "interpretation_long_es": (
            "La ausencia en un VCF sparse no demuestra homocigosis de referencia ni benignidad."
            if status == "covered_no_observed_variant" else
            "No se ejecutó una LLM ni se usaron decisiones del registro productivo para este grupo."
        ),
        "focus_variant_refs": [row.get("variant_ref") for row in focus if row.get("variant_ref")],
        "evidence_used": [], "evidence_limitations": list((scientific or {}).get("limitations") or []),
        "eligible_for_llm2": False, "interpretation_provenance": "deterministic",
        "disclaimer_required": True, "input_completeness_mode": "observed_variants_only",
    }


def llm1_card(item: dict, envelope: dict) -> dict:
    card = dict(item)
    card.update({
        "status": "valid", "coverage_status": "covered_by_prototype_snapshot",
        "eligible_for_llm2": True, "interpretation_provenance": "llm_generated",
        "disclaimer_required": True, "input_completeness_mode": envelope["payload_v7"].get("input_completeness", {}).get("mode"),
        "scientific_inference_ceiling": envelope["scientific_decision"].get(
            "scientific_inference_ceiling", envelope["scientific_decision"].get("prototype_inference_ceiling")
        ),
        "effective_runtime_ceiling": envelope["scientific_decision"].get(
            "effective_runtime_ceiling", envelope["scientific_decision"].get("prototype_inference_ceiling")
        ),
        "runtime_variant_gate": envelope.get("runtime_variant_gate") or {},
    })
    return card


def validate_llm2_output(result: dict, payload: dict) -> list[str]:
    return contracts.validate_llm2_output(result, payload)


def build_llm2_payload(valid_cards: list[dict], coverage_cards: list[dict], completeness: dict, coverage: dict) -> dict:
    group_ids = sorted(row["group_id"] for row in valid_cards)
    variant_refs = sorted({value for row in valid_cards for value in row.get("focus_variant_refs") or []})
    evidence_ids = sorted({item.get("evidence_id") for row in valid_cards for item in row.get("evidence_used") or [] if item.get("evidence_id")})
    return {
        "schema_version": "llm2_grouped_payload_v1",
        "prototype_readiness": "approved_for_sandbox_smoke_test",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "input_completeness": completeness,
        "coverage_summary": {
            "scientifically_covered_groups": coverage["covered_count"],
            "valid_llm1_cards": len(valid_cards),
            "covered_without_observed_variant": sum(row["status"] == "covered_no_observed_variant" for row in coverage_cards),
            "not_covered_groups": sum(row["status"] == "not_covered_by_prototype_snapshot" for row in coverage_cards),
        },
        "valid_llm1_cards": valid_cards,
        "excluded_groups": [{"group_id": row["group_id"], "reason": row["status"]} for row in coverage_cards if not row.get("eligible_for_llm2")],
        "allowlists": {"group_ids": group_ids, "variant_refs": variant_refs, "evidence_ids": evidence_ids},
    }


def deterministic_llm2(payload: dict) -> dict:
    findings = []
    for card in payload["valid_llm1_cards"]:
        findings.append({
            "group_id": card["group_id"], "headline_es": card["interpretation_one_sentence_es"],
            "explanation_es": card["interpretation_long_es"], "confidence": card["final_confidence_level"],
            "inference_mode": card["inference_mode"], "variant_refs": card.get("focus_variant_refs") or [],
            "evidence_ids": [row.get("evidence_id") for row in card.get("evidence_used") or [] if row.get("evidence_id")],
        })
    return {
        "schema_version": "grouped_global_interpretation_v1",
        "report_title_es": "HEAL by FON — Prototipo de desarrollo",
        "summary_es": "Síntesis determinística de prueba del pipeline agrupado.",
        "key_findings": findings,
        "coverage_statement_es": f"La cobertura científica de este prototipo está limitada a {payload['coverage_summary']['scientifically_covered_groups']} de 180 grupos canónicos.",
        "limitations_es": ["La validación formal con un nuevo holdout unseen permanece pendiente.", "La ausencia en un VCF sparse no demuestra homocigosis de referencia."],
        "disclaimer_es": "Las inferencias son generadas por una LLM y no constituyen diagnóstico ni indicación terapéutica.",
    }


def report_view_model(llm2_result: dict, cards: list[dict], coverage: dict, source_name: str) -> dict:
    module_titles_es = {
        "T1.1": "Resiliencia de sistemas fundamentales",
        "T1.2": "Sueño y ritmos circadianos",
        "T1.3": "Nutrientes y cofactores esenciales",
        "T1.4": "Inmunidad e inflamación",
        "T1.5": "Tejido conectivo y resiliencia física",
        "T1.6": "Detoxificación y manejo del estrés oxidativo",
    }
    findings = []
    by_card = {row["group_id"]: row for row in cards}
    for finding in llm2_result["key_findings"]:
        row = dict(finding)
        card = by_card.get(row["group_id"], {})
        row["module_id"] = card.get("module_id") or row["group_id"].split(":", 1)[1]
        row["gene"] = card.get("gene") or row["group_id"].split(":", 1)[0]
        row["module_name"] = card.get("module_name") or ""
        findings.append(row)
    def rank(row: dict) -> tuple:
        return (
            0 if row.get("inference_mode") == "initial_guide" else 1,
            0 if row.get("confidence") == "Conflicting" else 1,
            -len(row.get("evidence_ids") or []),
            row.get("group_id") or "",
        )

    module_ids = [f"T1.{index}" for index in range(1, 7)]
    modules = []
    primary: list[dict] = []
    secondary: list[dict] = []
    for module_id in module_ids:
        module_findings = sorted([row for row in findings if row["module_id"] == module_id], key=rank)
        selected = module_findings[:2]
        primary.extend(selected)
        secondary.extend(module_findings[2:])
        modules.append({
            "module_id": module_id,
            "title": module_titles_es[module_id],
            "summary": (
                f"{len(module_findings)} hallazgo(s) válido(s): "
                f"{sum(row.get('inference_mode') == 'initial_guide' for row in module_findings)} con guía inicial y "
                f"{sum(row.get('inference_mode') == 'context_only' for row in module_findings)} de interpretación contextual."
                if module_findings else "No se priorizaron hallazgos interpretables para este módulo en el VCF observado."
            ),
            "findings": module_findings,
        })
    primary = sorted(primary, key=lambda row: (module_ids.index(row["module_id"]), rank(row)))[:12]
    structural_quarantine = any(row.get("quarantine_class") == "structural_contract" for row in cards)
    ready = not structural_quarantine and any(row.get("status") == "valid" for row in cards)
    for row in findings:
        row["client_interpretation_type"] = "Guía inicial acotada" if row.get("inference_mode") == "initial_guide" else "Interpretación contextual"
        row["individual_applicability"] = "Moderada" if row.get("inference_mode") == "initial_guide" else "Limitada"
        row["scientific_relationship_confidence"] = (
            "Con evidencia conflictiva" if row.get("confidence") == "Conflicting" else
            "Respaldada con limitaciones" if row.get("confidence") in {"Low", "Moderate"} else "Respaldada"
        )
    valid_count = sum(row.get("status") == "valid" for row in cards)
    initial_count = sum(row.get("status") == "valid" and row.get("inference_mode") == "initial_guide" for row in cards)
    contextual_count = sum(row.get("status") == "valid" and row.get("inference_mode") == "context_only" for row in cards)
    no_observed_count = sum(row.get("status") == "covered_no_observed_variant" for row in cards)
    client_summary = (
        f"Este prototipo ofrece una interpretación inicial y acotada para contexto familiar. "
        f"El snapshot científico cubre {coverage['covered_count']} de {coverage['canonical_group_count']} grupos: "
        f"{valid_count} generaron tarjetas válidas, {no_observed_count} no presentaron una variante observada y "
        f"{coverage['not_covered_count']} quedaron explícitamente fuera de cobertura. Entre las tarjetas válidas, "
        f"{initial_count} permiten una guía inicial limitada y {contextual_count} aportan contexto biológico. "
        "El resultado no establece diagnósticos, causalidad, penetrancia, riesgo individual ni recomendaciones de tratamiento."
    )
    coverage_statement = (
        f"Cobertura cerrada: {coverage['covered_count']} grupos científicamente cubiertos de "
        f"{coverage['canonical_group_count']}; {valid_count} tarjetas válidas; {no_observed_count} grupos cubiertos "
        f"sin variante observada; {coverage['not_covered_count']} grupos no cubiertos. La ausencia de una variante "
        "en este VCF no se interpreta como homocigosis de referencia, benignidad ni falta de capacidad de llamada."
    )
    return {
        "schema_version": "report_view_model_v2", "source_file_name": source_name,
        "title": llm2_result["report_title_es"], "prototype_label": "Prototipo de desarrollo",
        "summary": client_summary, "findings": findings, "primary_findings": primary,
        "secondary_findings": secondary, "modules": modules,
        "coverage_statement": coverage_statement,
        "limitations": [
            "La entrada es un VCF de variantes observadas; la capacidad de llamada no está disponible. La ausencia de una variante no demuestra homocigosis de referencia ni falta de capacidad de llamada.",
            "La mayoría de las tarjetas sólo permite contexto biológico. Una guía inicial acotada no equivale a diagnóstico, predicción ni recomendación de conducta.",
            "La información GWAS es poblacional y contextual; no demuestra causalidad ni riesgo individual.",
            "Las clasificaciones benignas o probablemente benignas se mantienen limitadas a la variante, condición y evidencia informadas.",
            "No se proporcionó contexto clínico, bioquímico, sintomático, de exposición ni de medicación del paciente.",
            "Los grupos fuera del snapshot científico firmado no se interpretaron ni se completaron con el registry productivo.",
        ], "disclaimer": llm2_result["disclaimer_es"],
        "cannot_infer": [
            "El VCF no permite diagnosticar enfermedades ni estimar penetrancia individual.",
            "Las asociaciones poblacionales GWAS no predicen por sí solas el riesgo de esta persona.",
            "No deben iniciarse, suspenderse ni modificarse medicamentos o suplementos a partir de este prototipo.",
            "La ausencia de una variante en un VCF sparse no demuestra homocigosis de referencia ni capacidad de llamada.",
        ],
        "coverage": {
            "canonical": coverage["canonical_group_count"], "covered": coverage["covered_count"],
            "not_covered": coverage["not_covered_count"],
            "covered_no_observed_variant": no_observed_count,
            "quarantined": sum(row.get("status") == "quarantined" for row in cards),
            "valid_cards": valid_count,
        },
        "readiness": {"prototype_readiness": "prototype_demo_ready_automatic" if ready else "prototype_demo_incomplete", "formal_validation_readiness": "pending_new_unseen_holdout"},
    }


def write_docx(view: dict, path: Path) -> None:
    sections = [
        {"section_id": "resumen", "title": "Resumen general", "blocks": [{"type": "paragraph", "text": view["summary"]}]},
        {"section_id": "cobertura", "title": "Cobertura del análisis", "blocks": [{"type": "paragraph", "text": view["coverage_statement"]}]},
        {"section_id": "modulos", "title": "Resumen de los seis módulos", "blocks": [
            {"title": row["title"], "resumen": row["summary"]} for row in view["modules"]
        ]},
        {"section_id": "hallazgos", "title": "Hallazgos principales", "blocks": [
            {"title": row["group_id"], "modo": row["client_interpretation_type"], "confianza": row["scientific_relationship_confidence"],
             "aplicabilidad": row["individual_applicability"],
             "resumen": row["headline_es"], "detalle": row["explanation_es"],
             "variantes": row["variant_refs"], "evidencia": row["evidence_ids"]}
            for row in view["primary_findings"]
        ]},
        {"section_id": "contexto", "title": "Hallazgos contextuales relevantes", "blocks": [
            {"title": row["group_id"], "resumen": row["headline_es"], "aplicabilidad": "Limitada"}
            for row in view["secondary_findings"]
        ]},
        {"section_id": "no_inferir", "title": "Qué no puede inferirse", "blocks": [
            *({"type": "paragraph", "text": value} for value in view["cannot_infer"]),
        ]},
        {"section_id": "limites", "title": "Limitaciones del VCF", "blocks": [
            *({"type": "paragraph", "text": value} for value in view["limitations"]),
        ]},
        {"section_id": "estado", "title": "Estado de validación", "blocks": [
             {"type": "paragraph", "text": f"Readiness del prototipo: {view['readiness']['prototype_readiness']}."},
            {"type": "paragraph", "text": "Validación formal: pending_new_unseen_holdout."},
            {"type": "paragraph", "text": view["disclaimer"]},
        ]},
    ]
    legacy = {
        "metadata": {"language_mode": "es", "audience_mode": "family", "disclaimer_required": True,
                     "variant_count_observed": sum(len(row["variant_refs"]) for row in view["findings"]),
                     "unique_gene_count": len({row["group_id"].split(":")[0] for row in view["findings"]}),
                     "unique_rsid_count": len({value for row in view["findings"] for value in row["variant_refs"]})},
        "global_report": {"report_title": view["title"]},
        "structured_report": {"version": "report_view_model_v2", "sections": sections},
    }
    final_report.write_docx(path, legacy, {"fileName": view["source_file_name"], "languageMode": "es", "audienceMode": "family"})


def write_pdf(view: dict, path: Path) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HealTitle", parent=styles["Title"], textColor=colors.HexColor("#143A2B"), alignment=TA_CENTER, fontSize=20, leading=24))
    styles.add(ParagraphStyle(name="HealH1", parent=styles["Heading1"], textColor=colors.HexColor("#275D38"), spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="HealNote", parent=styles["BodyText"], backColor=colors.HexColor("#EEF5EE"), borderColor=colors.HexColor("#8BAA8B"), borderWidth=0.5, borderPadding=8, leading=14))
    story = [Paragraph(view["title"], styles["HealTitle"]), Spacer(1, 5 * mm), Paragraph(view["prototype_label"], styles["HealNote"]), Spacer(1, 5 * mm), Paragraph(view["summary"], styles["BodyText"])]
    story += [Spacer(1, 5 * mm), Paragraph("Cobertura científica", styles["HealH1"]), Paragraph(view["coverage_statement"], styles["BodyText"])]
    table = Table([["Grupos cubiertos", "Tarjetas válidas", "No cubiertos"], [view["coverage"]["covered"], view["coverage"]["valid_cards"], view["coverage"]["not_covered"]]], colWidths=[50 * mm] * 3)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#275D38")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#A0A0A0")), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("PADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 3 * mm), table, Spacer(1, 5 * mm), Paragraph("Resumen de los seis módulos", styles["HealH1"])]
    for module in view["modules"]:
        story.append(KeepTogether([Paragraph(module["title"], styles["Heading2"]), Paragraph(module["summary"], styles["BodyText"]), Spacer(1, 2 * mm)]))
    story += [PageBreak(), Paragraph("Hallazgos principales", styles["HealH1"])]
    for row in view["primary_findings"]:
        story.append(KeepTogether([
            Paragraph(f"{row['group_id']} - {row['headline_es']}", styles["Heading2"]),
            Paragraph(row["explanation_es"], styles["BodyText"]),
            Paragraph(f"{row['client_interpretation_type']} | Aplicabilidad individual: {row['individual_applicability']} | Relación científica: {row['scientific_relationship_confidence']}", styles["BodyText"]),
            Spacer(1, 3 * mm),
        ]))
    if view["secondary_findings"]:
        story += [Paragraph("Hallazgos contextuales relevantes", styles["HealH1"])]
        for row in view["secondary_findings"]:
            story.append(Paragraph(f"{row['group_id']}: {row['headline_es']}", styles["BodyText"], bulletText="•"))
    story += [Spacer(1, 5 * mm), Paragraph("Qué no puede inferirse", styles["HealH1"])]
    for value in view["cannot_infer"]:
        story.extend([Paragraph(value, styles["BodyText"], bulletText="•"), Spacer(1, 1.5 * mm)])
    story += [PageBreak(), Paragraph("Límites y estado de validación", styles["HealH1"])]
    for value in view["limitations"]:
        story.extend([Paragraph(value, styles["BodyText"], bulletText="•"), Spacer(1, 1.5 * mm)])
    story += [Spacer(1, 4 * mm), Paragraph(view["disclaimer"], styles["HealNote"]), Spacer(1, 3 * mm), Paragraph("Validación formal pendiente: se requiere un nuevo holdout unseen.", styles["BodyText"])]
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=view["title"], author="HEAL by FON").build(story)


def process(request: dict) -> dict:
    started = now_iso()
    payload_path = Path(request["payloadPath"]).resolve()
    output_dir = Path(request["outputDir"]).resolve()
    snapshot_path = Path(request.get("snapshotPath") or DEFAULT_SNAPSHOT).resolve()
    candidate_path = Path(request.get("candidateManifestPath") or DEFAULT_CANDIDATE).resolve()
    coverage_path = Path(request.get("coverageManifestPath") or DEFAULT_COVERAGE).resolve()
    progress_path = Path(request["progressPath"]).resolve() if request.get("progressPath") else output_dir / "grouped_prototype_progress.json"
    dry_run = bool(request.get("dryRun"))
    estimate_cap = float(request.get("maxEstimatedCostUsd") or DEFAULT_ESTIMATE_CAP_USD)
    hard_cap = float(request.get("hardCapUsd") or DEFAULT_HARD_CAP_USD)
    timeout_seconds = int(request.get("timeoutSeconds") or 180)
    api_key = str(request.get("apiKey") or os.environ.get("HEAL_OPENAI_API_KEY") or "").strip()
    if not dry_run and not api_key:
        raise RuntimeError("HEAL_OPENAI_API_KEY is required for the grouped prototype")
    registry_before = sha256_file(ACTIVE_REGISTRY)
    if registry_before != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("Active registry hash changed before prototype execution")
    snapshot, candidate, coverage = read_json(snapshot_path), read_json(candidate_path), read_json(coverage_path)
    validate_prototype_inputs(snapshot, candidate, coverage)
    payloads = {row["group_id"]: row for row in load_payloads(payload_path)}
    scientific = {row["group_id"]: row for row in snapshot["groups"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "grouped_prototype_run_summary.json"
    if summary_path.exists():
        existing = read_json(summary_path)
        if existing.get("snapshot_sha256") == snapshot.get("snapshot_sha256") and sha256_file(ACTIVE_REGISTRY) == registry_before:
            print(json.dumps(existing, ensure_ascii=False))
            return existing

    state_path = output_dir / "grouped_prototype_execution_state.json"
    source_binding = {"payload": sha256_file(payload_path), "snapshot": snapshot["snapshot_sha256"], "coverage": coverage["manifest_sha256"]}
    state = read_json(state_path) if state_path.exists() else {}
    if state and state.get("source_binding") != source_binding:
        raise RuntimeError("Existing grouped prototype state belongs to different immutable inputs")
    cards: list[dict] = list(state.get("cards") or [])
    raw_rows: list[dict] = list(state.get("raw_rows") or [])
    telemetry: list[dict] = list(state.get("telemetry") or [])
    quarantines: list[dict] = list(state.get("quarantines") or [])
    envelopes: list[dict] = list(state.get("envelopes") or [])
    completed_groups = {row["group_id"] for row in cards}
    llm1_schema = responses_compatible_schema(read_json(LLM1_DIR / "grouped_gene_module_interpretation_v7_schema.json"))
    llm1_prompt = (LLM1_DIR / "prompt_grouped_llm1_luna_v7.md").read_text(encoding="utf-8") + "\n\n" + (SCRIPT_DIR / "prompt_llm1_prototype_appendix.md").read_text(encoding="utf-8")
    planned_envelopes: dict[str, dict] = {}
    for row in coverage["groups"]:
        group_id = row["group_id"]
        if row["coverage_status"] != "covered_by_prototype_snapshot":
            continue
        base = payloads.get(group_id)
        if base is not None and (base.get("focus_variant_evidence") or []):
            envelope = build_envelope(base, scientific[group_id], candidate, snapshot)
            validate_envelope(envelope)
            planned_envelopes[group_id] = envelope
    projected_cost = sum(estimated_call_cost(envelope, llm1_prompt) for envelope in planned_envelopes.values())
    projected_cost += estimated_call_cost({"planned_valid_cards": len(planned_envelopes)}, (SCRIPT_DIR / "prompt_grouped_llm2_v1.md").read_text(encoding="utf-8"), reserved_output_tokens=8000)
    if not dry_run and projected_cost > estimate_cap:
        raise RuntimeError(f"Prototype projected cost USD {projected_cost:.4f} exceeds start guardrail USD {estimate_cap:.2f}")
    update_progress(progress_path, substage="preflight", processed=1, total=1,
                    message="Preflight científico y técnico completo",
                    metrics={"covered": coverage["covered_count"], "planned_llm1_calls": len(planned_envelopes), "projected_cost_usd": projected_cost})

    def persist_state() -> None:
        value = {"schema_version": "grouped_prototype_execution_state_v1", "source_binding": source_binding,
                 "cards": cards, "raw_rows": raw_rows, "telemetry": telemetry,
                 "quarantines": quarantines, "envelopes": envelopes, "updated_at": now_iso()}
        if state.get("llm2_result"):
            value["llm2_result"] = state["llm2_result"]
            value["llm2_metadata"] = state.get("llm2_metadata") or {}
        if state.get("fatal_error_code"):
            value["fatal_error_code"] = state["fatal_error_code"]
        write_json(state_path, value)

    ordered_coverage = coverage["groups"]
    for index, row in enumerate(ordered_coverage, 1):
        group_id = row["group_id"]
        if group_id in completed_groups:
            continue
        if row["coverage_status"] != "covered_by_prototype_snapshot":
            cards.append(deterministic_coverage_card(group_id, "not_covered_by_prototype_snapshot"))
            completed_groups.add(group_id); persist_state()
            continue
        base = payloads.get(group_id)
        science = scientific[group_id]
        if base is None or not (base.get("focus_variant_evidence") or []):
            cards.append(deterministic_coverage_card(group_id, "covered_no_observed_variant", scientific=science, payload=base))
            completed_groups.add(group_id); persist_state()
            continue
        envelope = planned_envelopes[group_id]
        envelopes.append(envelope)
        update_progress(progress_path, substage="llm1", processed=sum(item in completed_groups for item in planned_envelopes), total=len(planned_envelopes),
                        message=f"Interpretando {group_id}", metrics={"valid": sum(row.get("status") == "valid" for row in cards), "quarantined": len(quarantines)})
        call_started = time.perf_counter()
        errors: list[str] = []
        try:
            if dry_run:
                item = llm1.dry_run_interpretation(base)
                item["group_id"], item["gene"], item["module_id"] = base["group_id"], base["gene"], base["module_id"]
                if envelope["scientific_decision"]["effective_runtime_ceiling"] == "context_only":
                    item["inference_mode"] = "context_only"; item["interpretation_scope"] = "context_only"
                    item["final_confidence_level"] = "Low"
                metadata = {"response_id": "dry-run", "effective_model": MODEL, "usage": {}}
            else:
                accounted = sum(row["estimated_cost_usd"] for row in telemetry)
                call_reserve = estimated_call_cost(envelope, llm1_prompt)
                if accounted + call_reserve > hard_cap:
                    raise RuntimeError("prototype_hard_cost_cap_would_be_exceeded")
                item, metadata = call_with_one_technical_retry(
                    payload=envelope, api_key=api_key, model=MODEL, prompt=llm1_prompt,
                    schema=llm1_schema, timeout_seconds=timeout_seconds,
                )
            normalized_item, normalization_errors, normalization_audit = normalize_evidence_used(item)
            errors = sorted(set(normalization_errors + validate_llm1_output(normalized_item, envelope)))
            raw_rows.append({"stage": "llm1", "group_id": group_id, "response": metadata.get("raw_response"), "output": item,
                             "normalized_output": normalized_item, "normalization_audit": normalization_audit,
                             "errors": errors, "attempt_count": metadata.get("attempt_count", 1),
                             "prior_attempt_errors": metadata.get("prior_attempt_errors") or []})
            telemetry.append(usage_row(metadata, stage="llm1", group_id=group_id, role="group_interpreter", elapsed=time.perf_counter() - call_started))
            write_json(output_dir / "raw_responses_audit.json", raw_rows)
            write_csv(output_dir / "telemetry_costs.csv", telemetry)
            if errors:
                raise ValueError(";".join(errors))
            cards.append(llm1_card(normalized_item, envelope))
        except Exception as error:  # noqa: BLE001
            fatal_code = global_technical_blocker_code(error)
            if fatal_code:
                state["fatal_error_code"] = fatal_code
                persist_state()
                update_progress(progress_path, substage="llm1", processed=len(completed_groups), total=len(planned_envelopes),
                                message="Campaña detenida por un bloqueo técnico global", metrics={"error_code": fatal_code})
                raise RuntimeError(fatal_code) from error
            role_errors = errors
            quarantine = {"group_id": group_id, "status": "quarantined", "error": str(error),
                          "error_codes": role_errors, "quarantine_class": quarantine_class_for(role_errors, error), "created_at": now_iso(),
                          "timeout": "timed out" in str(error).lower(), "retry_exhausted": " | " in str(error)}
            quarantines.append(quarantine)
            card = deterministic_coverage_card(group_id, "quarantined", scientific=science, payload=base)
            card["quarantine_class"] = quarantine["quarantine_class"]
            card["error_codes"] = role_errors
            card["interpretation_one_sentence_es"] = "Resultado no disponible: la tarjeta quedó aislada por un error de validación."
            cards.append(card)
        completed_groups.add(group_id)
        persist_state()

    valid_cards = [row for row in cards if row.get("status") == "valid"]
    update_progress(progress_path, substage="normalization", processed=len(valid_cards), total=max(1, len(planned_envelopes)),
                    message="Normalización determinística completa", metrics={"valid": len(valid_cards), "quarantined": len(quarantines)})
    completeness = next((row.get("input_completeness") for row in payloads.values() if row.get("input_completeness")), {"mode": "observed_variants_only", "absence_semantics": "not_observed_callability_unknown"})
    llm2_payload = build_llm2_payload(valid_cards, cards, completeness, coverage)
    llm2_schema = responses_compatible_schema(read_json(SCRIPT_DIR / "grouped_global_interpretation_v1.schema.json"))
    llm2_prompt = (SCRIPT_DIR / "prompt_grouped_llm2_v1.md").read_text(encoding="utf-8")
    llm2_started = time.perf_counter()
    llm2_already_recorded = any(row.get("stage") == "llm2" for row in telemetry)
    update_progress(progress_path, substage="llm2", processed=0, total=1, message="Sintetizando hallazgos válidos", metrics={"cards": len(valid_cards)})
    if state.get("llm2_result"):
        llm2_result = state["llm2_result"]
        llm2_metadata = state.get("llm2_metadata") or {"response_id": "resumed", "effective_model": LLM2_MODEL, "usage": {}}
    elif dry_run or not valid_cards:
        llm2_result = deterministic_llm2(llm2_payload)
        llm2_metadata = {"response_id": "dry-run" if dry_run else "no-valid-cards", "effective_model": LLM2_MODEL, "usage": {}}
    else:
        accounted = sum(row["estimated_cost_usd"] for row in telemetry)
        call_reserve = estimated_call_cost(llm2_payload, llm2_prompt, reserved_output_tokens=8000)
        if accounted + call_reserve > hard_cap:
            raise RuntimeError("prototype_hard_cost_cap_would_be_exceeded_before_llm2")
        llm2_result, llm2_metadata = call_with_one_technical_retry(
            payload=llm2_payload, api_key=api_key, model=LLM2_MODEL, prompt=llm2_prompt,
            schema=llm2_schema, timeout_seconds=timeout_seconds,
        )
    llm2_errors = validate_llm2_output(llm2_result, llm2_payload)
    if not llm2_already_recorded:
        raw_rows.append({"stage": "llm2", "group_id": "global", "response": llm2_metadata.get("raw_response"), "output": llm2_result, "errors": llm2_errors})
        telemetry.append(usage_row(llm2_metadata, stage="llm2", group_id="global", role="group_synthesizer", elapsed=time.perf_counter() - llm2_started, model=LLM2_MODEL))
    write_json(output_dir / "raw_responses_audit.json", raw_rows)
    write_csv(output_dir / "telemetry_costs.csv", telemetry)
    if llm2_errors:
        raise RuntimeError("LLM2 grouped validation failed: " + ";".join(llm2_errors))
    state["llm2_result"] = llm2_result; state["llm2_metadata"] = {key: value for key, value in llm2_metadata.items() if key != "raw_response"}
    persist_state()
    update_progress(progress_path, substage="llm2", processed=1, total=1, message="Síntesis agrupada validada")

    source_name = str(request.get("fileName") or payload_path.stem)
    view = report_view_model(llm2_result, cards, coverage, source_name)
    report_json = output_dir / "report_view_model_v2.json"
    docx_path = output_dir / "HEAL_prototipo_desarrollo.docx"
    pdf_path = output_dir / "HEAL_prototipo_desarrollo.pdf"
    update_progress(progress_path, substage="reporting", processed=0, total=1, message="Generando DOCX y PDF desde un único view model")
    write_json(report_json, view); write_docx(view, docx_path); write_pdf(view, pdf_path)
    write_json(output_dir / "llm1_prototype_envelopes.json", envelopes)
    write_json(output_dir / "llm1_cards.json", cards)
    write_json(output_dir / "llm2_grouped_payload_v1.json", llm2_payload)
    write_json(output_dir / "grouped_global_interpretation_v1.json", llm2_result)
    write_json(output_dir / "raw_responses_audit.json", raw_rows)
    write_json(output_dir / "quarantine.json", quarantines)
    write_csv(output_dir / "cards.csv", cards)
    write_csv(output_dir / "coverage.csv", coverage["groups"])
    write_csv(output_dir / "telemetry_costs.csv", telemetry)
    registry_after = sha256_file(ACTIVE_REGISTRY)
    if registry_after != registry_before:
        raise RuntimeError("Active registry changed during prototype execution")
    structural_quarantines = sum(row.get("quarantine_class") == "structural_contract" for row in quarantines)
    status = "prototype_demo_ready_automatic" if valid_cards and not structural_quarantines else "prototype_demo_incomplete"
    initial_guides = sum(row.get("status") == "valid" and row.get("inference_mode") == "initial_guide" for row in cards)
    context_only = sum(row.get("status") == "valid" and row.get("inference_mode") == "context_only" for row in cards)
    summary = {
        "schema_version": "grouped_prototype_run_summary_v1", "status": status,
        "prototype_readiness": status, "formal_validation_readiness": "pending_new_unseen_holdout",
        "counts": {"canonical_groups": 180, "scientifically_covered": coverage["covered_count"],
                   "covered_with_observed_variant": len(planned_envelopes),
                   "covered_no_observed_variant": sum(row.get("status") == "covered_no_observed_variant" for row in cards),
                   "focus_variants": sum(len(row.get("focus_variant_refs") or []) for row in cards if row.get("coverage_status") == "covered_by_prototype_snapshot"),
                   "valid_llm1_cards": len(valid_cards), "initial_guide_findings": initial_guides,
                   "context_only_findings": context_only, "quarantined": len(quarantines),
                   "quarantined_content_safety": sum(row.get("quarantine_class") == "content_safety" for row in quarantines),
                   "quarantined_technical_isolated": sum(row.get("quarantine_class") == "technical_isolated" for row in quarantines),
                   "quarantined_structural_contract": structural_quarantines,
                   "not_covered": coverage["not_covered_count"]},
        "models": {"llm1": MODEL, "llm2": LLM2_MODEL, "reasoning_effort": "low"},
        "pricing": {"source": PROTOTYPE_PRICE_SOURCE, "input_per_million": LUNA_INPUT_PER_MILLION, "cached_input_per_million": LUNA_CACHED_INPUT_PER_MILLION, "output_per_million": LUNA_OUTPUT_PER_MILLION},
        "telemetry": {"estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in telemetry), 8), "calls": len(telemetry)},
        "budget": {"projected_cost_usd": projected_cost, "start_guardrail_usd": estimate_cap, "hard_cap_usd": hard_cap},
        "snapshot_sha256": snapshot["snapshot_sha256"], "snapshot_id": snapshot.get("snapshot_id"),
        "active_registry_sha256_before": registry_before, "active_registry_sha256_after": registry_after,
        "outputs": {"docx": str(docx_path), "pdf": str(pdf_path), "cards_csv": str(output_dir / "cards.csv"), "coverage_csv": str(output_dir / "coverage.csv"), "technical_audit": str(output_dir / "raw_responses_audit.json")},
        "started_at": started, "completed_at": now_iso(),
    }
    write_json(output_dir / "grouped_prototype_run_summary.json", summary)
    update_progress(progress_path, substage="packaging", processed=1, total=1, message="Artefactos del prototipo completos", metrics={"status": status})
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def load_request() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64")
    parser.add_argument("--payload-path")
    parser.add_argument("--output-dir")
    parser.add_argument("--snapshot-path")
    parser.add_argument("--file-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.input_json_base64:
        return json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    if not args.payload_path or not args.output_dir:
        parser.error("--payload-path and --output-dir are required without --input-json-base64")
    return {"payloadPath": args.payload_path, "outputDir": args.output_dir, "snapshotPath": args.snapshot_path, "fileName": args.file_name, "dryRun": args.dry_run}


if __name__ == "__main__":
    try:
        process(load_request())
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
