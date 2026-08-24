#!/usr/bin/env python3
"""Run HEAL's isolated, human-signed grouped prototype lane end to end.

The service never reads scientific decisions from the active mechanism registry.
The registry is touched only to verify its immutable SHA-256 before and after.
"""

from __future__ import annotations

import argparse
import base64
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
PROHIBITED = re.compile(
    r"\b(diagn[oó]stic|prescrib|recet|iniciar|suspender|ajustar dosis|tomar suplemento|"
    r"riesgo individual causado|homocigosis de referencia por ausencia)\w*",
    re.I,
)
SAFE_NEGATION = re.compile(
    r"\b(no|sin|nunca|tampoco|evita(?:r)?|exclu(?:ye|ir|ido|ida)|prohibid[oa]s?|"
    r"not|without|never|cannot|can't|does\s+not|do\s+not|must\s+not|no\s+se\s+debe)\b",
    re.I,
)
CEILING_ALLOWED_MODES = {
    "none": {"abstained_insufficient_evidence"},
    "context_only": {"context_only", "abstained_insufficient_evidence"},
    "initial_guide_candidate": {"initial_guide", "context_only", "abstained_insufficient_evidence"},
}
PROTEIN_ALTERING_TERMS = {
    "missense_variant", "stop_gained", "stop_lost", "start_lost", "frameshift_variant",
    "protein_altering_variant", "inframe_insertion", "inframe_deletion", "splice_acceptor_variant",
    "splice_donor_variant", "transcript_ablation",
}


def import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


llm1 = import_module("heal_grouped_llm1", LLM1_DIR / "interpret_gene_module_groups.py")
final_report = import_module("heal_final_report", REPORT_PATH)


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


def has_affirmative_pattern(value: str, pattern: re.Pattern) -> bool:
    """Flag actionable/diagnostic language while allowing explicit safeguards.

    Reports must say that they do *not* diagnose or recommend treatment. A
    prohibited root is therefore safe when a negation or prohibition occurs in
    the same clause before it.
    """
    text = str(value or "")
    for match in pattern.finditer(text):
        clause_start = max(text.rfind(mark, 0, match.start()) for mark in ".;:!?\n") + 1
        prefix = text[clause_start:match.start()]
        if SAFE_NEGATION.search(prefix):
            continue
        return True
    return False


def has_prohibited_language(value: str) -> bool:
    return has_affirmative_pattern(value, PROHIBITED)


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


def runtime_evidence_ids(payload: dict) -> set[str]:
    # The active-registry mechanism ID (`evm_*`) is intentionally excluded.
    # Runtime facts come from the frozen v7 traceability allowlist; scientific
    # mechanism references come only from the signed sandbox snapshot.
    allowed = payload.get("traceability_allowlist", {}).get("allowed_evidence_ids") or []
    return {str(value).strip() for value in allowed if str(value).strip() and not str(value).startswith("evm_")}


def variant_specific_inference_gate(payload: dict, scientific_ceiling: str) -> dict:
    reasons: list[str] = []
    eligible_variants: set[str] = set()
    eligible_evidence: set[str] = set()
    if scientific_ceiling != "initial_guide_candidate":
        return {"eligible": False, "reason_codes": ["scientific_ceiling_context_only"], "variant_refs": [], "evidence_ids": []}

    assertions_by_variant: dict[str, list[dict]] = {}
    for assertion in payload.get("clinical_evidence_summary", {}).get("assertion_groups") or []:
        if assertion.get("identity_match") is True and assertion.get("variant_ref"):
            assertions_by_variant.setdefault(str(assertion["variant_ref"]), []).append(assertion)
    pgx = payload.get("professional_curation") or {}
    for variant in payload.get("focus_variant_evidence") or []:
        variant_ref = str(variant.get("variant_ref") or "")
        exact_identity = variant.get("identity_match_class") == "exact_coordinate_allele"
        target_status = str((variant.get("target_gene_annotation") or {}).get("status") or "")
        consequence = str((variant.get("functional_evidence") or {}).get("most_severe_consequence") or "")
        functional = target_status in {"confirmed", "alternative_transcript"} and consequence in PROTEIN_ALTERING_TERMS
        human_positive = any(
            row.get("classification") == "pathogenic_or_likely_pathogenic"
            for row in assertions_by_variant.get(variant_ref, [])
        )
        pgx_applicable = (
            any(row.get("classification") == "drug_response" for row in assertions_by_variant.get(variant_ref, []))
            and pgx.get("pgx_observed_genotype_applicability") == "confirmed"
            and pgx.get("pgx_relevant_structured_medication_present") is True
            and pgx.get("pgx_escalation_allowed") is True
        )
        if exact_identity and functional and (human_positive or pgx_applicable):
            eligible_variants.add(variant_ref)
            eligible_evidence.add(str(variant.get("evidence_id")))
            eligible_evidence.update(
                str(row.get("evidence_id")) for row in assertions_by_variant.get(variant_ref, [])
                if row.get("classification") in {"pathogenic_or_likely_pathogenic", "drug_response"}
            )
    if not eligible_variants:
        reasons.extend(["no_directly_applicable_human_variant_evidence", "no_compatible_variant_functional_support"])
    return {
        "eligible": bool(eligible_variants), "reason_codes": sorted(set(reasons)),
        "variant_refs": sorted(eligible_variants), "evidence_ids": sorted(item for item in eligible_evidence if item),
    }


def build_envelope(payload: dict, scientific: dict, candidate: dict, snapshot: dict) -> dict:
    focus_refs = sorted({str(row.get("variant_ref")) for row in payload.get("focus_variant_evidence") or [] if row.get("variant_ref")})
    variant_refs = sorted(llm1.v5_variant_refs(payload))
    scientific_ids = sorted(set(scientific["valid_evidence_ids"]))
    runtime_ids = sorted(runtime_evidence_ids(payload))
    evidence_ids = sorted(set(scientific_ids) | set(runtime_ids))
    scientific_ceiling = scientific["prototype_inference_ceiling"]
    variant_gate = variant_specific_inference_gate(payload, scientific_ceiling)
    effective_ceiling = scientific_ceiling if scientific_ceiling != "initial_guide_candidate" or variant_gate["eligible"] else "context_only"
    decision = {
        "group_id": scientific["group_id"], "core_status": scientific["core_status"],
        "scientific_inference_ceiling": scientific_ceiling, "effective_runtime_ceiling": effective_ceiling,
        "dominant_direction": scientific["dominant_direction"], "material_conflict": scientific["material_conflict"],
        "limitations": scientific["limitations"], "evidence_records": scientific.get("evidence_records") or [],
    }
    return {
        "schema_version": "llm1_prototype_envelope_v2",
        "payload_v7": payload,
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
        "prototype_hashes": {
            "candidate_manifest": candidate["manifest_sha256"], "snapshot": snapshot["snapshot_sha256"],
            "payload": sha256_json(payload), "packet": scientific.get("provenance", {}).get("packet_sha256", "0" * 64),
        },
    }


def validate_envelope(envelope: dict) -> None:
    v1 = {"schema_version", "payload_v7", "scientific_decision", "coverage_status", "readiness", "allowlists", "prototype_hashes"}
    v2 = v1 | {"runtime_variant_gate", "technical_gates", "provenance"}
    expected = v2 if envelope.get("schema_version") == "llm1_prototype_envelope_v2" else v1
    if set(envelope) != expected or envelope.get("schema_version") not in {"llm1_prototype_envelope_v1", "llm1_prototype_envelope_v2"}:
        raise ValueError("Invalid or open prototype envelope")
    if envelope["payload_v7"].get("group_id") != envelope["scientific_decision"].get("group_id"):
        raise ValueError("Prototype envelope identity mismatch")
    if envelope["payload_v7"].get("input_completeness", {}).get("absence_semantics") != "not_observed_callability_unknown":
        raise ValueError("Prototype requires safe sparse-VCF absence semantics")
    if envelope.get("schema_version") == "llm1_prototype_envelope_v2" and not all(envelope["technical_gates"].values()):
        raise ValueError("Prototype envelope failed a deterministic technical gate")


def validate_llm1_output(item: dict, envelope: dict) -> list[str]:
    errors: list[str] = []
    payload = envelope["payload_v7"]
    if item.get("group_id") != payload.get("group_id") or item.get("gene") != payload.get("gene") or item.get("module_id") != payload.get("module_id"):
        errors.append("llm1_identity_mismatch")
    mode = str(item.get("inference_mode") or "")
    decision = envelope["scientific_decision"]
    ceiling = decision.get("effective_runtime_ceiling", decision.get("prototype_inference_ceiling", "none"))
    if mode not in CEILING_ALLOWED_MODES[ceiling]:
        errors.append("llm1_inference_ceiling_exceeded")
    allowed_evidence = set(envelope["allowlists"]["evidence_ids"])
    allowed_variants = set(envelope["allowlists"]["variant_refs"])
    allowed_focus = set(envelope["allowlists"]["focus_variant_refs"])
    evidence_rows = item.get("evidence_used") or []
    evidence_ids = [row.get("evidence_id") for row in evidence_rows]
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("llm1_duplicate_evidence")
    focus_refs = item.get("focus_variant_refs") or []
    if len(focus_refs) != len(set(focus_refs)):
        errors.append("llm1_duplicate_focus_variant")
    for row in evidence_rows:
        if row.get("evidence_id") not in allowed_evidence:
            errors.append("llm1_evidence_outside_allowlist")
        if row.get("variant_ref") and row.get("variant_ref") not in allowed_variants:
            errors.append("llm1_variant_outside_allowlist")
    if set(item.get("focus_variant_refs") or []) - allowed_focus:
        errors.append("llm1_focus_variant_outside_allowlist")
    priority = item.get("review_priority")
    if item.get("requires_professional_review") is not (priority in {"recommended", "urgent"}):
        errors.append("llm1_review_boolean_mismatch")
    if not item.get("interpretation_one_sentence_es") or not item.get("interpretation_one_sentence_en"):
        errors.append("llm1_bilingual_contract_incomplete")
    combined = "\n".join(str(value) for key, value in item.items() if key.endswith(("_es", "_en")))
    if has_prohibited_language(combined):
        errors.append("llm1_prohibited_language")
    errors.extend(prototype_critical_semantic_errors(item, payload))
    return sorted(set(errors))


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
    errors: list[str] = []
    allowed = payload["allowlists"]
    if result.get("schema_version") != "grouped_global_interpretation_v1":
        errors.append("llm2_schema_version")
    llm2_text = "\n".join([
        str(result.get("summary_es") or ""),
        str(result.get("coverage_statement_es") or ""),
        str(result.get("disclaimer_es") or ""),
        *(result.get("limitations_es") or []),
        *(
            str(value)
            for row in result.get("key_findings") or []
            for value in (row.get("headline_es") or "", row.get("explanation_es") or "")
        ),
    ])
    if has_prohibited_language(llm2_text):
        errors.append("llm2_prohibited_language")
    by_group = {row["group_id"]: row for row in payload["valid_llm1_cards"]}
    finding_groups = [row.get("group_id") for row in result.get("key_findings") or []]
    if len(finding_groups) != len(set(finding_groups)):
        errors.append("llm2_duplicate_group")
    for finding in result.get("key_findings") or []:
        source = by_group.get(finding.get("group_id"))
        if source is None:
            errors.append("llm2_group_outside_llm1")
            continue
        if set(finding.get("variant_refs") or []) - set(source.get("focus_variant_refs") or []):
            errors.append("llm2_variant_outside_llm1")
        if len(finding.get("variant_refs") or []) != len(set(finding.get("variant_refs") or [])):
            errors.append("llm2_duplicate_variant")
        source_evidence = {row.get("evidence_id") for row in source.get("evidence_used") or []}
        if set(finding.get("evidence_ids") or []) - source_evidence:
            errors.append("llm2_evidence_outside_llm1")
        if len(finding.get("evidence_ids") or []) != len(set(finding.get("evidence_ids") or [])):
            errors.append("llm2_duplicate_evidence")
        if finding.get("inference_mode") != source.get("inference_mode"):
            errors.append("llm2_inference_mode_changed")
        if finding.get("confidence") != source.get("final_confidence_level"):
            errors.append("llm2_confidence_changed")
    covered_count = int(payload.get("coverage_summary", {}).get("scientifically_covered_groups") or 0)
    if str(covered_count) not in str(result.get("coverage_statement_es") or ""):
        errors.append("llm2_coverage_not_explicit")
    return sorted(set(errors))


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
    findings = []
    by_card = {row["group_id"]: row for row in cards}
    for finding in llm2_result["key_findings"]:
        row = dict(finding)
        card = by_card.get(row["group_id"], {})
        row["module_id"] = card.get("module_id") or row["group_id"].split(":", 1)[1]
        row["gene"] = card.get("gene") or row["group_id"].split(":", 1)[0]
        findings.append(row)
    modules = []
    for module_id in sorted({row["module_id"] for row in findings}):
        modules.append({"module_id": module_id, "findings": [row for row in findings if row["module_id"] == module_id]})
    ready = not any(row.get("status") == "quarantined" for row in cards) and any(row.get("status") == "valid" for row in cards)
    return {
        "schema_version": "report_view_model_v1", "source_file_name": source_name,
        "title": llm2_result["report_title_es"], "prototype_label": "Prototipo de desarrollo",
        "summary": llm2_result["summary_es"], "findings": findings, "modules": modules,
        "coverage_statement": llm2_result["coverage_statement_es"],
        "limitations": llm2_result["limitations_es"], "disclaimer": llm2_result["disclaimer_es"],
        "coverage": {
            "canonical": coverage["canonical_group_count"], "covered": coverage["covered_count"],
            "not_covered": coverage["not_covered_count"],
            "covered_no_observed_variant": sum(row.get("status") == "covered_no_observed_variant" for row in cards),
            "quarantined": sum(row.get("status") == "quarantined" for row in cards),
            "valid_cards": sum(row.get("status") == "valid" for row in cards),
        },
        "readiness": {"prototype_readiness": "prototype_demo_ready_automatic" if ready else "prototype_demo_incomplete", "formal_validation_readiness": "pending_new_unseen_holdout"},
    }


def write_docx(view: dict, path: Path) -> None:
    sections = [
        {"section_id": "resumen", "title": "Resumen", "blocks": [{"type": "paragraph", "text": view["summary"]}]},
        {"section_id": "hallazgos", "title": "Hallazgos por grupo", "blocks": [
            {"title": row["group_id"], "modo": row["inference_mode"], "confianza": row["confidence"],
             "resumen": row["headline_es"], "detalle": row["explanation_es"],
             "variantes": row["variant_refs"], "evidencia": row["evidence_ids"]}
            for row in view["findings"]
        ]},
        {"section_id": "cobertura", "title": "Cobertura y límites", "blocks": [
            {"type": "paragraph", "text": view["coverage_statement"]},
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
        "structured_report": {"version": "report_view_model_v1", "sections": sections},
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
    story += [Spacer(1, 3 * mm), table, PageBreak(), Paragraph("Hallazgos por módulo", styles["HealH1"])]
    for module in view["modules"]:
        story.append(Paragraph(f"Módulo {module['module_id']}", styles["HealH1"]))
        for row in module["findings"]:
            story.append(KeepTogether([
                Paragraph(f"{row['group_id']} - {row['headline_es']}", styles["Heading2"]),
                Paragraph(row["explanation_es"], styles["BodyText"]),
                Paragraph(f"Modo: {row['inference_mode']} | Confianza: {row['confidence']}", styles["BodyText"]),
                Spacer(1, 3 * mm),
            ]))
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
            errors = validate_llm1_output(item, envelope)
            raw_rows.append({"stage": "llm1", "group_id": group_id, "response": metadata.get("raw_response"), "output": item,
                             "errors": errors, "attempt_count": metadata.get("attempt_count", 1),
                             "prior_attempt_errors": metadata.get("prior_attempt_errors") or []})
            telemetry.append(usage_row(metadata, stage="llm1", group_id=group_id, role="group_interpreter", elapsed=time.perf_counter() - call_started))
            write_json(output_dir / "raw_responses_audit.json", raw_rows)
            write_csv(output_dir / "telemetry_costs.csv", telemetry)
            if errors:
                raise ValueError(";".join(errors))
            cards.append(llm1_card(item, envelope))
        except Exception as error:  # noqa: BLE001
            fatal_code = global_technical_blocker_code(error)
            if fatal_code:
                state["fatal_error_code"] = fatal_code
                persist_state()
                update_progress(progress_path, substage="llm1", processed=len(completed_groups), total=len(planned_envelopes),
                                message="Campaña detenida por un bloqueo técnico global", metrics={"error_code": fatal_code})
                raise RuntimeError(fatal_code) from error
            quarantine = {"group_id": group_id, "status": "quarantined", "error": str(error), "created_at": now_iso(),
                          "timeout": "timed out" in str(error).lower(), "retry_exhausted": " | " in str(error)}
            quarantines.append(quarantine)
            card = deterministic_coverage_card(group_id, "quarantined", scientific=science, payload=base)
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
    report_json = output_dir / "report_view_model_v1.json"
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
    status = "prototype_demo_ready_automatic" if not quarantines and valid_cards else "prototype_demo_incomplete"
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
