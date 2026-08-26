"""Pure deterministic contracts for HEAL's isolated grouped prototype lane."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable


PROHIBITED = re.compile(
    r"\b(diagn[oó]stic|prescrib|recet|iniciar|suspender|ajustar dosis|tomar suplemento|"
    r"riesgo individual causado|homocigosis de referencia por ausencia)\w*",
    re.I,
)
SAFE_NEGATION = re.compile(
    r"\b(no|sin|nunca|tampoco|ni|evita(?:r)?|exclu(?:ye|yen|ir|ido|ida|idos|idas|si[oó]n|siones)|"
    r"descarta(?:r)?|prohibid[oa]s?|not|without|never|neither|nor|cannot|can't|doesn['’]?t|"
    r"does\s+not|do\s+not|must\s+not|rules?\s+out|excludes?|excluded|exclusion|exclusions|"
    r"rather\s+than|instead\s+of|en\s+vez\s+de|en\s+lugar\s+de|no\s+se\s+debe)\b",
    re.I,
)
CLAUSE_BOUNDARY = re.compile(r"[.;:!?\n]+|\b(?:pero|sin\s+embargo|but|however)\b", re.I)
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


def has_affirmative_pattern(value: str, pattern: re.Pattern) -> bool:
    """Flag a matched clause unless that clause contains a safety negation."""
    text = str(value or "")
    boundaries = [0]
    boundaries.extend(match.end() for match in CLAUSE_BOUNDARY.finditer(text))
    boundaries.append(len(text))
    for match in pattern.finditer(text):
        clause_start = max((value for value in boundaries if value <= match.start()), default=0)
        clause_end = min((value for value in boundaries if value >= match.end()), default=len(text))
        clause = text[clause_start:clause_end]
        polarity_window = re.sub(r"\b(?:not\s+only|no\s+solo)\b", "", clause, flags=re.I)
        if SAFE_NEGATION.search(polarity_window):
            continue
        return True
    return False


def has_prohibited_language(value: str) -> bool:
    return has_affirmative_pattern(value, PROHIBITED)


def normalize_evidence_used(item: dict) -> tuple[dict, list[str], list[dict]]:
    """Coalesce referential repetition without hiding scientific conflicts."""
    normalized = copy.deepcopy(item)
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in normalized.get("evidence_used") or []:
        evidence_id = str(row.get("evidence_id") or "")
        if evidence_id not in grouped:
            grouped[evidence_id] = []
            order.append(evidence_id)
        grouped[evidence_id].append(row)
    errors: list[str] = []
    audit: list[dict] = []
    coalesced: list[dict] = []
    for evidence_id in order:
        rows = grouped[evidence_id]
        variants = {str(row.get("variant_ref") or "") for row in rows}
        sources = {str(row.get("source") or "") for row in rows}
        if len(variants) > 1:
            errors.append("llm1_scientific_evidence_double_counting")
            coalesced.extend(rows)
            continue
        first = dict(rows[0])
        properties = sorted({str(row.get("field") or "") for row in rows if row.get("field")})
        values = sorted({str(row.get("value") or "") for row in rows if row.get("value")})
        if len(rows) > 1:
            first["supported_properties"] = properties
            first["supported_values"] = values
            first["supporting_sources"] = sorted(sources)
            audit.append({
                "evidence_id": evidence_id,
                "variant_ref": next(iter(variants), ""),
                "source": next(iter(sources), ""),
                "raw_entry_count": len(rows),
                "supported_properties": properties,
                "normalization": "referential_duplicates_coalesced",
            })
        coalesced.append(first)
    normalized["evidence_used"] = coalesced
    return normalized, sorted(set(errors)), audit


def runtime_evidence_ids(payload: dict) -> set[str]:
    """Resolve runtime evidence without consulting the active mechanism registry."""
    allowed = payload.get("traceability_allowlist", {}).get("allowed_evidence_ids") or []
    return {
        str(value).strip() for value in allowed
        if str(value).strip() and not str(value).startswith("evm_")
    }


def variant_specific_inference_gate(payload: dict, scientific_ceiling: str) -> dict:
    reasons: list[str] = []
    eligible_variants: set[str] = set()
    eligible_evidence: set[str] = set()
    if scientific_ceiling != "initial_guide_candidate":
        return {
            "eligible": False, "reason_codes": ["scientific_ceiling_context_only"],
            "variant_refs": [], "evidence_ids": [],
        }

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
        "variant_refs": sorted(eligible_variants),
        "evidence_ids": sorted(item for item in eligible_evidence if item),
    }


def effective_runtime_ceiling(scientific_ceiling: str, variant_gate: dict) -> str:
    if scientific_ceiling == "initial_guide_candidate" and not variant_gate.get("eligible"):
        return "context_only"
    return scientific_ceiling


def validate_envelope(envelope: dict) -> None:
    v1 = {"schema_version", "payload_v7", "scientific_decision", "coverage_status", "readiness", "allowlists", "prototype_hashes"}
    v2 = v1 | {"runtime_variant_gate", "technical_gates", "provenance"}
    v3 = v2 | {"source_payload_audit"}
    version = envelope.get("schema_version")
    expected = v3 if version == "llm1_prototype_envelope_v3" else v2 if version == "llm1_prototype_envelope_v2" else v1
    if set(envelope) != expected or version not in {
        "llm1_prototype_envelope_v1", "llm1_prototype_envelope_v2", "llm1_prototype_envelope_v3",
    }:
        raise ValueError("Invalid or open prototype envelope")
    if envelope["payload_v7"].get("group_id") != envelope["scientific_decision"].get("group_id"):
        raise ValueError("Prototype envelope identity mismatch")
    if envelope["payload_v7"].get("input_completeness", {}).get("absence_semantics") != "not_observed_callability_unknown":
        raise ValueError("Prototype requires safe sparse-VCF absence semantics")
    if version in {"llm1_prototype_envelope_v2", "llm1_prototype_envelope_v3"} and not all(envelope["technical_gates"].values()):
        raise ValueError("Prototype envelope failed a deterministic technical gate")
    if version == "llm1_prototype_envelope_v3":
        forbidden = {"curated_mechanism", "internal_scientific_curation", "operational_state", "canonical_status", "gates"}
        if forbidden & set(envelope["payload_v7"]):
            raise ValueError("Legacy scientific state leaked into the model-visible projection")
        if any(str(value).startswith("evm_") for value in envelope["allowlists"]["evidence_ids"]):
            raise ValueError("Legacy mechanism evidence leaked into the execution allowlist")


def validate_llm1_output(
    item: dict,
    envelope: dict,
    semantic_errors: Callable[[dict, dict], list[str]],
) -> list[str]:
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
        errors.append("llm1_duplicate_evidence_not_normalized")
    focus_refs = item.get("focus_variant_refs") or []
    if len(focus_refs) != len(set(focus_refs)):
        errors.append("llm1_duplicate_focus_variant")
    for row in evidence_rows:
        if row.get("evidence_id") not in allowed_evidence:
            errors.append("llm1_evidence_outside_allowlist")
        if row.get("variant_ref") and row.get("variant_ref") not in allowed_variants:
            errors.append("llm1_variant_outside_allowlist")
    if set(focus_refs) - allowed_focus:
        errors.append("llm1_focus_variant_outside_allowlist")
    priority = item.get("review_priority")
    if item.get("requires_professional_review") is not (priority in {"recommended", "urgent"}):
        errors.append("llm1_review_boolean_mismatch")
    if not item.get("interpretation_one_sentence_es") or not item.get("interpretation_one_sentence_en"):
        errors.append("llm1_bilingual_contract_incomplete")
    combined = "\n".join(str(value) for key, value in item.items() if key.endswith(("_es", "_en")))
    if has_prohibited_language(combined):
        errors.append("llm1_prohibited_language")
    errors.extend(semantic_errors(item, payload))
    return sorted(set(errors))


def validate_llm2_output(result: dict, payload: dict) -> list[str]:
    errors: list[str] = []
    if result.get("schema_version") != "grouped_global_interpretation_v1":
        errors.append("llm2_schema_version")
    llm2_text = "\n".join([
        str(result.get("summary_es") or ""), str(result.get("coverage_statement_es") or ""),
        str(result.get("disclaimer_es") or ""), *(result.get("limitations_es") or []),
        *(str(value) for row in result.get("key_findings") or [] for value in (
            row.get("headline_es") or "", row.get("explanation_es") or "",
        )),
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
