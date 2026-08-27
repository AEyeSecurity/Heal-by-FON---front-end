"""Deterministic client-readiness projections for the grouped HEAL prototype.

Raw LLM and legacy payloads remain audit artifacts.  This module creates the
clean downstream and public representations used by future synthesis, the UI,
and client reports without changing scientific decisions.
"""

from __future__ import annotations

import copy
import re
from typing import Any


LEGACY_AUTHORITY = re.compile(
    r"\b(?:legacy\s+(?:payload|mechanism)|mechanism\s+(?:registry\s+)?(?:status\s+)?(?:is\s+)?(?:draft|withheld)|"
    r"(?:estado\s+del\s+)?registro\s+(?:del\s+)?mecan(?:i|í)sm(?:o|ico)\s+(?:es|est(?:a|á))\s+(?:draft|borrador|withheld|retenido)|"
    r"mecanismo\s+(?:est(?:a|á)\s+)?(?:en\s+)?(?:draft|borrador|withheld|retenido)|"
    r"mechanism\s+not\s+usable)\b",
    re.I,
)
SOURCE_FAILURE = re.compile(
    r"\b(?:source[_\s-]?error|provider\s+error|returned\s+(?:a\s+)?(?:provider\s+)?error|"
    r"fall(?:o|ó|os)\s+(?:de\s+)?(?:la\s+)?fuente|errores?\s+de\s+fuente|pharmgkb\s+(?:queries?\s+)?(?:had|returned|tuvo|tuvieron|fall(?:o|ó)))\b",
    re.I,
)
IDENTITY_UNRESOLVED = re.compile(
    r"\b(?:identity[-_\s]?(?:ambiguous|unresolved)|unresolved\s+identit|identidad\s+(?:ambigua|no\s+resuelta)|"
    r"registros?\s+(?:no\s+resueltos?|con\s+identidad\s+ambigua))\b",
    re.I,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

PARTIAL_SOURCE_LIMITATION_ES = (
    "Algunas fuentes externas no estuvieron completamente disponibles. Esta disponibilidad parcial no se "
    "interpreta como benignidad, ausencia de evidencia, ausencia de asociación ni resultado negativo."
)
UNRESOLVED_IDENTITY_LIMITATION_ES = (
    "Algunos registros no pudieron resolverse con identidad suficiente y no se utilizaron para producir "
    "inferencias individuales."
)
SPARSE_VCF_LIMITATION_ES = (
    "La entrada contiene variantes observadas; una ausencia no demuestra homocigosis de referencia ni "
    "capacidad de llamada confirmada."
)
MODULE_LABELS_ES = {
    "T1.1": "Resiliencia de sistemas fundamentales",
    "T1.2": "Sueño y ritmos circadianos",
    "T1.3": "Nutrientes y cofactores esenciales",
    "T1.4": "Inmunidad e inflamación",
    "T1.5": "Tejido conectivo y resiliencia física",
    "T1.6": "Detoxificación y manejo del estrés oxidativo",
}


def _sentences(value: str) -> list[str]:
    return [item.strip() for item in SENTENCE_SPLIT.split(str(value or "").strip()) if item.strip()]


def clean_authoritative_text(value: str) -> str:
    """Remove sentences whose authority is solely a historical registry state."""
    cleaned = " ".join(sentence for sentence in _sentences(value) if not LEGACY_AUTHORITY.search(sentence)).strip()
    replacements = (
        (r"\bnot_reported\b", "sin clasificación reportada"),
        (r"\bbenigna_o_probablemente_benigna\b", "benigna o probablemente benigna"),
        (r"\bbenign_or_likely_benign\b", "benign or likely benign"),
        (r"\brisk_factor\b", "factor de riesgo"),
        (r"\bcontext_only\b", "interpretación contextual"),
    )
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
    return cleaned


def _clean_limitations(values: list[Any]) -> tuple[list[str], dict[str, bool]]:
    cleaned: list[str] = []
    flags = {"partial_external_sources": False, "unresolved_identity": False}
    for raw in values or []:
        text = clean_authoritative_text(str(raw or ""))
        if not text:
            continue
        if SOURCE_FAILURE.search(text):
            flags["partial_external_sources"] = True
            continue
        if IDENTITY_UNRESOLVED.search(text):
            flags["unresolved_identity"] = True
            continue
        cleaned.append(text)
    return list(dict.fromkeys(cleaned)), flags


def _clean_evidence(rows: list[dict]) -> list[dict]:
    output: list[dict] = []
    for row in rows or []:
        clean = {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key not in {"raw_response", "provider_body", "request", "headers", "stack", "path"}
        }
        output.append(clean)
    return output


def normalize_card(card: dict, prioritized: dict[str, int] | None = None) -> dict:
    priorities = prioritized or {}
    limitations, limitation_flags = _clean_limitations(card.get("evidence_limitations") or [])
    rank = priorities.get(str(card.get("group_id") or ""))
    return {
        "group_id": card.get("group_id"),
        "gene": card.get("gene"),
        "module_id": card.get("module_id"),
        "module_name": card.get("module_name") or "",
        "status": card.get("status"),
        "coverage_status": card.get("coverage_status"),
        "eligible_for_llm2": bool(card.get("eligible_for_llm2")),
        "inference_mode": card.get("inference_mode"),
        "final_confidence_level": card.get("final_confidence_level"),
        "review_priority": card.get("review_priority") or "none",
        "requires_professional_review": bool(card.get("requires_professional_review")),
        "scientific_inference_ceiling": card.get("scientific_inference_ceiling") or "none",
        "effective_runtime_ceiling": card.get("effective_runtime_ceiling") or "none",
        "runtime_variant_gate": copy.deepcopy(card.get("runtime_variant_gate") or {}),
        "input_completeness_mode": card.get("input_completeness_mode") or "observed_variants_only",
        "focus_variant_refs": list(card.get("focus_variant_refs") or []),
        "evidence_used": _clean_evidence(card.get("evidence_used") or []),
        "evidence_limitations": limitations,
        "limitation_flags": limitation_flags,
        "interpretation_one_sentence_es": clean_authoritative_text(card.get("interpretation_one_sentence_es") or ""),
        "interpretation_one_sentence_en": clean_authoritative_text(card.get("interpretation_one_sentence_en") or ""),
        "interpretation_long_es": clean_authoritative_text(card.get("interpretation_long_es") or ""),
        "interpretation_long_en": clean_authoritative_text(card.get("interpretation_long_en") or ""),
        "technical_interpretation_es": clean_authoritative_text(card.get("technical_interpretation_es") or ""),
        "technical_interpretation_en": clean_authoritative_text(card.get("technical_interpretation_en") or ""),
        "confidence_rationale_es": clean_authoritative_text(card.get("confidence_rationale_es") or ""),
        "confidence_rationale_en": clean_authoritative_text(card.get("confidence_rationale_en") or ""),
        "family_notes_es": clean_authoritative_text(card.get("family_notes_es") or ""),
        "family_notes_en": clean_authoritative_text(card.get("family_notes_en") or ""),
        "prioritization": {"prioritized": rank is not None, "rank": rank},
    }


def _coverage_counts(cards: list[dict], canonical: int, covered: int, uncovered: int) -> dict:
    valid = sum(row.get("status") == "valid" for row in cards)
    no_observed = sum(row.get("status") == "covered_no_observed_variant" for row in cards)
    if len(cards) != canonical:
        raise ValueError("client_readiness_coverage_card_count_mismatch")
    if covered + uncovered != canonical:
        raise ValueError("client_readiness_coverage_total_mismatch")
    if valid + no_observed != covered:
        raise ValueError("client_readiness_covered_breakdown_mismatch")
    return {
        "canonical_group_count": canonical,
        "scientifically_covered_count": covered,
        "valid_interpretation_count": valid,
        "covered_no_observed_variant_count": no_observed,
        "not_covered_count": uncovered,
    }


def build_downstream_result(
    cards: list[dict],
    coverage: dict,
    llm2_result: dict,
    *,
    input_completeness: dict | None = None,
    external_evidence_partial: bool = False,
) -> dict:
    priorities = {
        str(row.get("group_id")): index
        for index, row in enumerate(llm2_result.get("key_findings") or [], 1)
        if row.get("group_id")
    }
    normalized = [normalize_card(row, priorities) for row in cards]
    counts = _coverage_counts(
        normalized,
        int(coverage.get("canonical_group_count") or 0),
        int(coverage.get("covered_count") or 0),
        int(coverage.get("not_covered_count") or 0),
    )
    counts["prioritized_finding_count"] = len(priorities)
    counts["initial_guide_count"] = sum(
        row.get("status") == "valid" and row.get("inference_mode") == "initial_guide" for row in normalized
    )
    counts["context_only_count"] = sum(
        row.get("status") == "valid" and row.get("inference_mode") == "context_only" for row in normalized
    )
    return {
        "schema_version": "grouped_downstream_result_v1",
        "processing": {"status": "completed", "label_es": "Procesamiento completado"},
        "external_evidence_availability": {
            "status": "partial" if external_evidence_partial else "complete",
            "label_es": (
                "Algunas fuentes externas no estuvieron completamente disponibles"
                if external_evidence_partial else "Fuentes externas disponibles para esta ejecución"
            ),
            "interpretation_policy": "availability_is_not_negative_evidence",
        },
        "coverage": counts,
        "input_completeness": copy.deepcopy(input_completeness or {
            "mode": "observed_variants_only", "absence_semantics": "not_observed_callability_unknown",
        }),
        "cards": normalized,
        "formal_validation_readiness": "pending_new_unseen_holdout",
    }


def _confidence_label(value: str) -> str:
    return {
        "High": "Alta", "Moderate": "Moderada", "Low": "Baja",
        "Conflicting": "Con evidencia conflictiva", "Abstain": "Sin inferencia",
    }.get(str(value or ""), str(value or "No informada"))


def _mode_label(value: str) -> str:
    return {
        "initial_guide": "Guía inicial acotada",
        "context_only": "Interpretación contextual",
        "abstained_insufficient_evidence": "Sin inferencia individual",
    }.get(str(value or ""), "Sin inferencia individual")


def build_client_result(downstream: dict) -> dict:
    cards = []
    for row in downstream["cards"]:
        source_labels = sorted({str(item.get("source") or "Evidencia científica") for item in row.get("evidence_used") or []})
        notices: list[str] = []
        if row.get("limitation_flags", {}).get("partial_external_sources"):
            notices.append(PARTIAL_SOURCE_LIMITATION_ES)
        if row.get("limitation_flags", {}).get("unresolved_identity"):
            notices.append(UNRESOLVED_IDENTITY_LIMITATION_ES)
        if row.get("status") == "covered_no_observed_variant":
            notices.append(SPARSE_VCF_LIMITATION_ES)
        cards.append({
            "group_id": row.get("group_id"), "gene": row.get("gene"),
            "module_id": row.get("module_id"),
            "module_name": MODULE_LABELS_ES.get(str(row.get("module_id") or ""), row.get("module_name") or "Módulo científico"),
            "status": row.get("status"), "coverage_status": row.get("coverage_status"),
            "inference_mode": row.get("inference_mode"), "inference_mode_label_es": _mode_label(row.get("inference_mode")),
            "scientific_confidence": row.get("final_confidence_level"),
            "scientific_confidence_label_es": _confidence_label(row.get("final_confidence_level")),
            "interpretation_es": {
                "summary": row.get("interpretation_one_sentence_es") or "",
                "detail": row.get("interpretation_long_es") or "",
            },
            "observed_variant_refs": list(row.get("focus_variant_refs") or []),
            "evidence_summary": {"record_count": len(row.get("evidence_used") or []), "sources": source_labels},
            "limitations_es": list(dict.fromkeys(notices)),
            "prioritization": copy.deepcopy(row.get("prioritization") or {"prioritized": False, "rank": None}),
            "input_completeness_label_es": "VCF de variantes observadas; capacidad de llamada no confirmada",
        })
    return {
        "schema_version": "grouped_client_result_v1",
        "processing": copy.deepcopy(downstream["processing"]),
        "external_evidence_availability": copy.deepcopy(downstream["external_evidence_availability"]),
        "coverage": copy.deepcopy(downstream["coverage"]),
        "cards": cards,
        "prototype_label": "Prototipo de desarrollo",
        "formal_validation_label_es": "Validación formal pendiente de un nuevo holdout independiente",
        "formal_validation_readiness": "pending_new_unseen_holdout",
    }


def build_client_coverage_rows(client: dict) -> list[dict]:
    coverage_labels = {
        "valid": ("Científicamente cubierto", "Variante foco observada", "Interpretación válida"),
        "covered_no_observed_variant": (
            "Científicamente cubierto", "Sin variante foco observada", "Sin interpretación individual",
        ),
        "not_covered_by_prototype_snapshot": (
            "Fuera del snapshot científico", "No evaluado por el prototipo", "No interpretado",
        ),
    }
    rows = []
    for card in client.get("cards") or []:
        scientific, observed, interpretation = coverage_labels.get(
            card.get("status"), ("Estado no disponible", "Estado no disponible", "Estado no disponible"),
        )
        rows.append({
            "gen": card.get("gene") or "",
            "modulo": card.get("module_name") or "Módulo científico",
            "cobertura_cientifica": scientific,
            "variante_foco": observed,
            "resultado": interpretation,
        })
    return rows


def assert_client_clean(value: Any) -> None:
    serialized = str(value)
    if LEGACY_AUTHORITY.search(serialized):
        raise ValueError("legacy_authority_leaked_to_client_contract")
    if re.search(r"(?:[A-Za-z]:\\|HEAL_OPENAI_API_KEY|authorization|raw_response|stack trace)", serialized, re.I):
        raise ValueError("internal_metadata_leaked_to_client_contract")


def build_report_view_model(client: dict, llm2_result: dict, source_name: str) -> dict:
    module_titles = MODULE_LABELS_ES
    cards = client["cards"]
    by_group = {row["group_id"]: row for row in cards}
    findings: list[dict] = []
    for rank, finding in enumerate(llm2_result.get("key_findings") or [], 1):
        card = by_group.get(finding.get("group_id"), {})
        if not card:
            continue
        findings.append({
            "group_id": finding.get("group_id"), "gene": card.get("gene"),
            "module_id": card.get("module_id"), "module_name": card.get("module_name"),
            "headline_es": clean_authoritative_text(finding.get("headline_es") or ""),
            "explanation_es": clean_authoritative_text(finding.get("explanation_es") or ""),
            "client_interpretation_type": card.get("inference_mode_label_es"),
            "individual_applicability": "Moderada" if card.get("inference_mode") == "initial_guide" else "Limitada",
            "scientific_relationship_confidence": card.get("scientific_confidence_label_es"),
            "prioritization_rank": rank,
        })
    counts = client["coverage"]
    modules = []
    for module_id, title in module_titles.items():
        valid_in_module = [
            row for row in cards if row.get("module_id") == module_id and row.get("status") == "valid"
        ]
        prioritized = [row for row in findings if row.get("module_id") == module_id]
        contextual = sum(row.get("inference_mode") == "context_only" for row in valid_in_module)
        initial = sum(row.get("inference_mode") == "initial_guide" for row in valid_in_module)
        if prioritized:
            prioritized_label = (
                "1 hallazgo priorizado" if len(prioritized) == 1
                else f"{len(prioritized)} hallazgos priorizados"
            )
            summary = (
                f"{prioritized_label} en este resumen. "
                f"El módulo contiene {len(valid_in_module)} interpretaciones válidas en total "
                f"({initial} con guía inicial y {contextual} contextuales)."
            )
        elif valid_in_module:
            summary = (
                "No se priorizaron hallazgos para este resumen, aunque existen "
                f"{len(valid_in_module)} resultados contextuales válidos en el módulo."
            )
        else:
            summary = "No se generaron interpretaciones válidas para este módulo en el VCF observado."
        modules.append({
            "module_id": module_id, "title": title, "summary": summary,
            "valid_interpretation_count": len(valid_in_module),
            "prioritized_finding_count": len(prioritized),
            "findings": prioritized,
        })
    summary = (
        "Este prototipo completó el procesamiento y ofrece una interpretación inicial y acotada para contexto "
        f"familiar. Cubre científicamente {counts['scientifically_covered_count']} de "
        f"{counts['canonical_group_count']} grupos: {counts['valid_interpretation_count']} generaron "
        f"interpretaciones válidas, {counts['covered_no_observed_variant_count']} no presentaron una variante "
        f"foco observada y {counts['not_covered_count']} quedaron fuera del snapshot. "
        f"El resumen prioriza {counts['prioritized_finding_count']} hallazgos; priorización no significa que sean "
        "las únicas interpretaciones válidas. El resultado no establece diagnósticos, causalidad, riesgo "
        "individual ni recomendaciones de tratamiento."
    )
    coverage_statement = (
        f"Cobertura: {counts['canonical_group_count']} grupos totales; "
        f"{counts['scientifically_covered_count']} científicamente cubiertos; "
        f"{counts['valid_interpretation_count']} con interpretación válida; "
        f"{counts['covered_no_observed_variant_count']} cubiertos sin variante foco observada; "
        f"{counts['not_covered_count']} fuera del snapshot."
    )
    view = {
        "schema_version": "report_view_model_v3",
        "source_file_name": source_name,
        "title": "HEAL by FON - Reporte del prototipo funcional",
        "prototype_label": "Prototipo de desarrollo",
        "processing_status": client["processing"],
        "external_evidence_availability": client["external_evidence_availability"],
        "summary": summary,
        "coverage_statement": coverage_statement,
        "coverage": counts,
        "modules": modules,
        "primary_findings": findings[:12],
        "secondary_findings": findings[12:],
        "valid_interpretation_count": counts["valid_interpretation_count"],
        "prioritized_finding_count": counts["prioritized_finding_count"],
        "limitations": [
            SPARSE_VCF_LIMITATION_ES,
            "La mayoría de las interpretaciones sólo aporta contexto biológico; una guía inicial no equivale a diagnóstico ni predicción.",
            "Las asociaciones GWAS son poblacionales y no demuestran causalidad ni riesgo individual.",
            PARTIAL_SOURCE_LIMITATION_ES if client["external_evidence_availability"]["status"] == "partial" else
            "La disponibilidad de fuentes externas se registró para esta ejecución.",
            "No se proporcionó contexto clínico, bioquímico, sintomático, de exposición ni de medicación.",
            "Los grupos fuera del snapshot científico firmado no fueron interpretados.",
        ],
        "cannot_infer": [
            "Este prototipo no diagnostica enfermedades ni estima penetrancia individual.",
            "Las asociaciones poblacionales no predicen por sí solas el riesgo de una persona.",
            "No deben iniciarse, suspenderse ni modificarse medicamentos, suplementos o dosis a partir de este reporte.",
            SPARSE_VCF_LIMITATION_ES,
        ],
        "disclaimer": clean_authoritative_text(llm2_result.get("disclaimer_es") or "") or
        "Las inferencias fueron generadas por una LLM y son orientativas; no constituyen diagnóstico ni indicación terapéutica.",
        "readiness": {
            "prototype_e2e_v1_closed": True,
            "client_prototype_ready": True,
            "formal_validation_readiness": "pending_new_unseen_holdout",
        },
    }
    assert_client_clean(view)
    return view
