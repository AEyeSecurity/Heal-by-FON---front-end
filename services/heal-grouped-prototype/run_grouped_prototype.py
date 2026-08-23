#!/usr/bin/env python3
"""Run HEAL's isolated 12-group prototype lane end to end.

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
DEFAULT_SNAPSHOT = Path(r"F:\Heal by FON\data\prototype\tier1-12-snapshot-prototype-v2\prototype_curation_snapshot_v1.json")
DEFAULT_CANDIDATE = Path(r"F:\Heal by FON\data\prototype\tier1-12-snapshot-prototype-v2\prototype_candidate_manifest.json")
DEFAULT_COVERAGE = Path(r"F:\Heal by FON\data\prototype\tier1-12-snapshot-prototype-v2\prototype_coverage_manifest_v1.json")
ACTIVE_REGISTRY = Path(r"F:\Heal by FON\data\canon\curation\mechanism_registry_v1.csv")
EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"
MODEL = "gpt-5.6-luna"
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


def usage_row(metadata: dict, *, stage: str, group_id: str, role: str, elapsed: float) -> dict:
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
        "requested_model": MODEL, "effective_model": metadata.get("effective_model"),
        "response_id": metadata.get("response_id"), "reasoning_effort": "low",
        "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens, "reasoning_tokens": reasoning_tokens,
        "latency_seconds": round(elapsed, 3), "attempt_count": 1,
        "estimated_cost_usd": round(cost, 8), "cost_observable": True,
    }


def validate_prototype_inputs(snapshot: dict, candidate: dict, coverage: dict) -> None:
    if snapshot.get("schema_version") != "prototype_curation_snapshot_v1" or len(snapshot.get("groups") or []) != 12:
        raise ValueError("Prototype snapshot must contain exactly 12 groups")
    if candidate.get("prototype_readiness") != "prototype_candidate":
        raise ValueError("Prototype candidate manifest is not ready")
    if candidate.get("formal_validation_readiness") != "pending_new_unseen_holdout":
        raise ValueError("Formal validation state is inconsistent")
    if coverage.get("canonical_group_count") != 180 or coverage.get("covered_count") != 12:
        raise ValueError("Prototype coverage manifest is not closed over 180 groups")
    statuses = {row["group_id"]: row["coverage_status"] for row in coverage["groups"]}
    for row in snapshot["groups"]:
        if statuses.get(row["group_id"]) != "covered_by_prototype_snapshot":
            raise ValueError(f"Covered snapshot group missing from coverage manifest: {row['group_id']}")


def runtime_evidence_ids(payload: dict) -> set[str]:
    ids: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {"evidence_id", "cluster_ref"} and isinstance(item, str) and item.strip():
                ids.add(item.strip())
            else:
                collect(item)

    collect(payload)
    return ids


def build_envelope(payload: dict, scientific: dict, candidate: dict, snapshot: dict) -> dict:
    focus_refs = sorted({str(row.get("variant_ref")) for row in payload.get("focus_variant_evidence") or [] if row.get("variant_ref")})
    variant_refs = sorted(llm1.v5_variant_refs(payload))
    evidence_ids = sorted(set(scientific["valid_evidence_ids"]) | runtime_evidence_ids(payload))
    decision = {
        key: scientific[key]
        for key in ("group_id", "core_status", "prototype_inference_ceiling", "dominant_direction", "material_conflict", "limitations", "evidence_records")
    }
    return {
        "schema_version": "llm1_prototype_envelope_v1",
        "payload_v7": payload,
        "scientific_decision": decision,
        "coverage_status": "covered_by_prototype_snapshot",
        "readiness": {
            "prototype_readiness": "prototype_candidate",
            "formal_validation_readiness": "pending_new_unseen_holdout",
        },
        "allowlists": {"evidence_ids": evidence_ids, "variant_refs": variant_refs, "focus_variant_refs": focus_refs},
        "prototype_hashes": {
            "candidate_manifest": candidate["manifest_sha256"],
            "snapshot": snapshot["snapshot_sha256"], "payload": sha256_json(payload),
        },
    }


def validate_envelope(envelope: dict) -> None:
    required = {"schema_version", "payload_v7", "scientific_decision", "coverage_status", "readiness", "allowlists", "prototype_hashes"}
    if set(envelope) != required or envelope["schema_version"] != "llm1_prototype_envelope_v1":
        raise ValueError("Invalid or open prototype envelope")
    if envelope["payload_v7"].get("group_id") != envelope["scientific_decision"].get("group_id"):
        raise ValueError("Prototype envelope identity mismatch")
    if envelope["payload_v7"].get("input_completeness", {}).get("absence_semantics") != "not_observed_callability_unknown":
        raise ValueError("Prototype requires safe sparse-VCF absence semantics")


def validate_llm1_output(item: dict, envelope: dict) -> list[str]:
    errors: list[str] = []
    payload = envelope["payload_v7"]
    if item.get("group_id") != payload.get("group_id") or item.get("gene") != payload.get("gene") or item.get("module_id") != payload.get("module_id"):
        errors.append("llm1_identity_mismatch")
    mode = str(item.get("inference_mode") or "")
    ceiling = envelope["scientific_decision"]["prototype_inference_ceiling"]
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
        "prototype_inference_ceiling": envelope["scientific_decision"]["prototype_inference_ceiling"],
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
    if "12" not in str(result.get("coverage_statement_es") or ""):
        errors.append("llm2_coverage_not_explicit")
    return sorted(set(errors))


def build_llm2_payload(valid_cards: list[dict], coverage_cards: list[dict], completeness: dict) -> dict:
    group_ids = sorted(row["group_id"] for row in valid_cards)
    variant_refs = sorted({value for row in valid_cards for value in row.get("focus_variant_refs") or []})
    evidence_ids = sorted({item.get("evidence_id") for row in valid_cards for item in row.get("evidence_used") or [] if item.get("evidence_id")})
    return {
        "schema_version": "llm2_grouped_payload_v1",
        "prototype_readiness": "prototype_candidate",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "input_completeness": completeness,
        "coverage_summary": {
            "scientifically_covered_groups": 12,
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
        "coverage_statement_es": "La cobertura científica de este prototipo está limitada a 12 grupos.",
        "limitations_es": ["La validación formal con un nuevo holdout unseen permanece pendiente.", "La ausencia en un VCF sparse no demuestra homocigosis de referencia."],
        "disclaimer_es": "Las inferencias son generadas por una LLM y no constituyen diagnóstico ni indicación terapéutica.",
    }


def report_view_model(llm2_result: dict, cards: list[dict], coverage: dict, source_name: str) -> dict:
    return {
        "schema_version": "report_view_model_v1", "source_file_name": source_name,
        "title": llm2_result["report_title_es"], "prototype_label": "Prototipo de desarrollo",
        "summary": llm2_result["summary_es"], "findings": llm2_result["key_findings"],
        "coverage_statement": llm2_result["coverage_statement_es"],
        "limitations": llm2_result["limitations_es"], "disclaimer": llm2_result["disclaimer_es"],
        "coverage": {"covered": 12, "not_covered": coverage["not_covered_count"], "valid_cards": sum(row.get("status") == "valid" for row in cards)},
        "readiness": {"prototype_readiness": "prototype_demo_ready_automatic", "formal_validation_readiness": "pending_new_unseen_holdout"},
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
            {"type": "paragraph", "text": "Readiness del prototipo: prototype_demo_ready_automatic."},
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
    table = Table([["Grupos cubiertos", "Tarjetas válidas", "No cubiertos"], [12, view["coverage"]["valid_cards"], view["coverage"]["not_covered"]]], colWidths=[50 * mm] * 3)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#275D38")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#A0A0A0")), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("PADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 3 * mm), table, PageBreak(), Paragraph("Hallazgos por grupo", styles["HealH1"])]
    for row in view["findings"]:
        story.append(KeepTogether([
            Paragraph(f"{row['group_id']} — {row['headline_es']}", styles["Heading2"]),
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
    dry_run = bool(request.get("dryRun"))
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
    cards: list[dict] = []
    raw_rows: list[dict] = []
    telemetry: list[dict] = []
    quarantines: list[dict] = []
    envelopes: list[dict] = []
    llm1_schema = responses_compatible_schema(read_json(LLM1_DIR / "grouped_gene_module_interpretation_v7_schema.json"))
    llm1_prompt = (LLM1_DIR / "prompt_grouped_llm1_luna_v7.md").read_text(encoding="utf-8") + "\n\n" + (SCRIPT_DIR / "prompt_llm1_prototype_appendix.md").read_text(encoding="utf-8")
    for row in coverage["groups"]:
        group_id = row["group_id"]
        if row["coverage_status"] != "covered_by_prototype_snapshot":
            cards.append(deterministic_coverage_card(group_id, "not_covered_by_prototype_snapshot"))
            continue
        base = payloads.get(group_id)
        science = scientific[group_id]
        if base is None or not (base.get("focus_variant_evidence") or []):
            cards.append(deterministic_coverage_card(group_id, "covered_no_observed_variant", scientific=science, payload=base))
            continue
        envelope = build_envelope(base, science, candidate, snapshot)
        validate_envelope(envelope)
        envelopes.append(envelope)
        call_started = time.perf_counter()
        try:
            if dry_run:
                item = llm1.dry_run_interpretation(base)
                item["group_id"], item["gene"], item["module_id"] = base["group_id"], base["gene"], base["module_id"]
                if science["prototype_inference_ceiling"] == "context_only":
                    item["inference_mode"] = "context_only"; item["interpretation_scope"] = "context_only"
                    item["final_confidence_level"] = "Low"
                metadata = {"response_id": "dry-run", "effective_model": MODEL, "usage": {}}
            else:
                item, metadata = llm1.call_openai_structured(
                    envelope, api_key=api_key, model=MODEL, system_prompt=llm1_prompt,
                    schema=llm1_schema, timeout_seconds=int(request.get("timeoutSeconds") or 180),
                    reasoning_effort="low",
                )
            errors = validate_llm1_output(item, envelope)
            raw_rows.append({"stage": "llm1", "group_id": group_id, "response": metadata.get("raw_response"), "output": item, "errors": errors})
            telemetry.append(usage_row(metadata, stage="llm1", group_id=group_id, role="group_interpreter", elapsed=time.perf_counter() - call_started))
            write_json(output_dir / "raw_responses_audit.json", raw_rows)
            write_csv(output_dir / "telemetry_costs.csv", telemetry)
            if errors:
                raise ValueError(";".join(errors))
            cards.append(llm1_card(item, envelope))
        except Exception as error:  # noqa: BLE001
            quarantine = {"group_id": group_id, "status": "quarantined", "error": str(error), "created_at": now_iso()}
            quarantines.append(quarantine)
            card = deterministic_coverage_card(group_id, "quarantined", scientific=science, payload=base)
            card["interpretation_one_sentence_es"] = "Resultado no disponible: la tarjeta quedó aislada por un error de validación."
            cards.append(card)

    valid_cards = [row for row in cards if row.get("status") == "valid"]
    completeness = next((row.get("input_completeness") for row in payloads.values() if row.get("input_completeness")), {"mode": "observed_variants_only", "absence_semantics": "not_observed_callability_unknown"})
    llm2_payload = build_llm2_payload(valid_cards, cards, completeness)
    llm2_schema = responses_compatible_schema(read_json(SCRIPT_DIR / "grouped_global_interpretation_v1.schema.json"))
    llm2_prompt = (SCRIPT_DIR / "prompt_grouped_llm2_v1.md").read_text(encoding="utf-8")
    llm2_started = time.perf_counter()
    if dry_run:
        llm2_result = deterministic_llm2(llm2_payload)
        llm2_metadata = {"response_id": "dry-run", "effective_model": MODEL, "usage": {}}
    else:
        llm2_result, llm2_metadata = llm1.call_openai_structured(
            llm2_payload, api_key=api_key, model=MODEL, system_prompt=llm2_prompt,
            schema=llm2_schema, timeout_seconds=int(request.get("timeoutSeconds") or 180), reasoning_effort="low",
        )
    llm2_errors = validate_llm2_output(llm2_result, llm2_payload)
    raw_rows.append({"stage": "llm2", "group_id": "global", "response": llm2_metadata.get("raw_response"), "output": llm2_result, "errors": llm2_errors})
    telemetry.append(usage_row(llm2_metadata, stage="llm2", group_id="global", role="group_synthesizer", elapsed=time.perf_counter() - llm2_started))
    write_json(output_dir / "raw_responses_audit.json", raw_rows)
    write_csv(output_dir / "telemetry_costs.csv", telemetry)
    if llm2_errors:
        raise RuntimeError("LLM2 grouped validation failed: " + ";".join(llm2_errors))

    source_name = str(request.get("fileName") or payload_path.stem)
    view = report_view_model(llm2_result, cards, coverage, source_name)
    report_json = output_dir / "report_view_model_v1.json"
    docx_path = output_dir / "HEAL_prototipo_desarrollo.docx"
    pdf_path = output_dir / "HEAL_prototipo_desarrollo.pdf"
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
    summary = {
        "schema_version": "grouped_prototype_run_summary_v1", "status": status,
        "prototype_readiness": status, "formal_validation_readiness": "pending_new_unseen_holdout",
        "counts": {"canonical_groups": 180, "scientifically_covered": 12, "valid_llm1_cards": len(valid_cards), "quarantined": len(quarantines), "not_covered": 168},
        "models": {"llm1": MODEL, "llm2": MODEL, "reasoning_effort": "low"},
        "pricing": {"source": PROTOTYPE_PRICE_SOURCE, "input_per_million": LUNA_INPUT_PER_MILLION, "cached_input_per_million": LUNA_CACHED_INPUT_PER_MILLION, "output_per_million": LUNA_OUTPUT_PER_MILLION},
        "telemetry": {"estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in telemetry), 8), "calls": len(telemetry)},
        "active_registry_sha256_before": registry_before, "active_registry_sha256_after": registry_after,
        "outputs": {"docx": str(docx_path), "pdf": str(pdf_path), "cards_csv": str(output_dir / "cards.csv"), "coverage_csv": str(output_dir / "coverage.csv"), "technical_audit": str(output_dir / "raw_responses_audit.json")},
        "started_at": started, "completed_at": now_iso(),
    }
    write_json(output_dir / "grouped_prototype_run_summary.json", summary)
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
