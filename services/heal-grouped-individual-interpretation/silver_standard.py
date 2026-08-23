#!/usr/bin/env python3
"""Deterministic contract, rubric and decision helpers for the LLM1 silver benchmark."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PILOT_CASE_IDS = ("MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5")
MODELS = ("gpt-5-mini", "gpt-5.6-luna", "gpt-5.6-terra")
RUBRIC_WEIGHTS = {
    "evidence_fidelity": 25,
    "genetic_identity": 15,
    "confidence_and_abstention": 15,
    "clinical_safety": 15,
    "usefulness_and_selective_escalation": 10,
    "traceability": 8,
    "downstream_clarity": 5,
    "bilingual_consistency": 4,
    "schema_and_concision": 3,
}
INFERENCE_MODES = {"initial_guide", "context_only", "abstained_insufficient_evidence"}
REVIEW_PRIORITIES = {"none", "optional_contextual", "recommended", "urgent"}
REVIEW_REASON_CODES = {
    "same_condition_clinical_conflict", "pathogenic_or_likely_pathogenic", "pharmacogenomic_context",
    "material_identity_or_transcript_ambiguity", "high_impact_evidence_gap",
}
MECHANISM_FINAL_STATES = {"approved", "approved_with_conflict", "withheld", "rejected"}
CRITICAL_ERRORS = (
    "invented_or_altered_genetic_identity", "unknown_evidence_or_variant_reference",
    "unapproved_mechanism_used", "missing_or_failed_source_called_benign",
    "gwas_presented_as_causal_or_individual_risk",
    "cross_condition_heterogeneity_presented_as_same_condition_conflict",
    "unsupported_diagnosis_penetrance_or_individual_risk",
    "treatment_medication_dose_or_supplement_recommendation",
    "pgx_actionability_without_confirmed_genotype_applicability",
    "material_pathogenic_conflict_or_pgx_escalation_omitted",
)

CASE_BEHAVIOR = {
    "MTHFR:T1.1": {"mode": ["initial_guide"], "confidence": ["Low"], "priority": ["none", "optional_contextual"], "myth": True},
    "PEMT:T1.3": {"mode": ["context_only"], "confidence": ["Low"], "priority": ["none", "optional_contextual"], "myth": False},
    "IL6:T1.4": {"mode": ["context_only"], "confidence": ["Low"], "priority": ["none", "optional_contextual"], "myth": False},
    "ABCB1:T1.6": {"mode": ["context_only"], "confidence": ["Low"], "priority": ["none", "optional_contextual"], "myth": False},
    "IFNG:T3.5": {"mode": ["abstained_insufficient_evidence"], "confidence": ["Abstain"], "priority": ["none"], "myth": False},
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def collect_evidence_ids(payload: dict) -> set[str]:
    found: set[str] = set()
    def visit(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("evidence_id"), str) and value["evidence_id"].strip():
                found.add(value["evidence_id"].strip())
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(payload)
    return found


def focus_variant_refs(payload: dict) -> set[str]:
    return {str(row.get("variant_ref", "")).strip() for row in payload.get("focus_variant_evidence") or [] if str(row.get("variant_ref", "")).strip()}


def collect_variant_refs(payload: dict) -> set[str]:
    """Collect every exact variant reference supplied anywhere in the payload.

    `focus_variant_refs` remains the narrower allowlist for the output's focus
    list. Evidence citations may legitimately point to GWAS or contextual
    variants, so they must be checked against the complete payload instead.
    """
    found: set[str] = set()
    def visit(value: object) -> None:
        if isinstance(value, dict):
            ref = value.get("variant_ref")
            if isinstance(ref, str) and ref.strip():
                found.add(ref.strip())
            for key in ("variant_refs", "variant_refs_for_audit", "primary_variant_refs"):
                refs = value.get(key)
                if isinstance(refs, list):
                    found.update(str(item).strip() for item in refs if str(item).strip())
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(payload)
    return found


def expected_professional_review(priority: str) -> bool:
    if priority not in REVIEW_PRIORITIES:
        raise ValueError(f"Unknown review_priority: {priority!r}")
    return priority in {"recommended", "urgent"}


def payload_preflight(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("group_id") not in PILOT_CASE_IDS: errors.append("unknown_pilot_case")
    if payload.get("payload_schema_version") != "llm1_group_payload_v6": errors.append("payload_not_v6")
    if payload.get("execution_mode") != "pilot": errors.append("execution_mode_not_pilot")
    if not (payload.get("evidence_coverage") or {}).get("reconciled"): errors.append("evidence_not_reconciled")
    if int((payload.get("compression_metadata") or {}).get("estimated_tokens", 0) or 0) > 25_000: errors.append("token_limit_exceeded")
    gates = payload.get("gates") or {}
    for name in ("token_budget_ready", "mechanism_registry_ready", "target_gene_annotation_ready", "allele_specific_frequency_ready", "gwas_relevance_ready", "group_payload_ready", "llm1_pilot_ready"):
        if not gates.get(name): errors.append(f"gate:{name}")
    mechanism = payload.get("curated_mechanism") or {}
    status = str(mechanism.get("curation_status", ""))
    if status not in MECHANISM_FINAL_STATES: errors.append("mechanism_unresolved")
    if status in {"approved", "approved_with_conflict"} and not mechanism.get("usable_by_llm"): errors.append("approved_mechanism_not_usable")
    context = payload.get("patient_context") or {}
    axes = context.get("axes") or {}
    if set(axes) != {"metabolism_nutrients_methylation", "immunity_inflammation", "xenobiotics_pharmacogenomics"}: errors.append("patient_context_axes_invalid")
    if any(axis.get("default_state") != "not_provided" for axis in axes.values()): errors.append("pilot_context_must_be_not_provided")
    traceability = payload.get("traceability_allowlist") or {}
    if set(traceability.get("allowed_evidence_ids") or []) != collect_evidence_ids(payload): errors.append("traceability_evidence_allowlist_stale")
    if set(traceability.get("allowed_variant_refs") or []) != collect_variant_refs(payload): errors.append("traceability_variant_allowlist_stale")
    if set(traceability.get("allowed_focus_variant_refs") or []) != focus_variant_refs(payload): errors.append("traceability_focus_allowlist_stale")
    return sorted(set(errors))


def build_gold_case(payload: dict) -> dict:
    group_id = payload["group_id"]
    behavior = CASE_BEHAVIOR[group_id]
    return {
        "schema_version": "llm1_silver_case_v2", "approval_status": "draft", "approval_owner": "",
        "approval_timestamp": "", "group_id": group_id, "payload_sha256": sha256_json(payload),
        "allowed_evidence_ids": sorted(collect_evidence_ids(payload)),
        "allowed_variant_refs": sorted(collect_variant_refs(payload)),
        "allowed_focus_variant_refs": sorted(focus_variant_refs(payload)),
        "expected_inference_modes": behavior["mode"], "expected_confidence_levels": behavior["confidence"],
        "expected_review_priorities": behavior["priority"], "myth_correction_required": behavior["myth"],
        "critical_errors": list(CRITICAL_ERRORS),
        "non_promotable_evidence": ["source_error", "missing_evidence", "unapproved_mechanism", "valid_but_excluded_or_unreviewed_gwas", "unconfirmed_pgx_applicability"],
    }


def validate_gold_case(gold: dict, payload: dict, require_approved: bool = True) -> list[str]:
    errors = []
    if gold.get("group_id") != payload.get("group_id"): errors.append("gold_group_mismatch")
    if gold.get("payload_sha256") != sha256_json(payload): errors.append("gold_payload_hash_mismatch")
    if set(gold.get("allowed_evidence_ids") or []) != collect_evidence_ids(payload): errors.append("gold_evidence_allowlist_drift")
    if "allowed_variant_refs" in gold and set(gold.get("allowed_variant_refs") or []) != collect_variant_refs(payload): errors.append("gold_variant_allowlist_drift")
    if set(gold.get("allowed_focus_variant_refs") or []) != focus_variant_refs(payload): errors.append("gold_variant_allowlist_drift")
    if require_approved and (gold.get("approval_status") != "approved" or not gold.get("approval_owner")): errors.append("gold_not_approved")
    return errors


def validate_output(output: dict, payload: dict, gold: dict) -> list[str]:
    errors = []
    required = {"group_id", "gene", "module_id", "inference_mode", "final_confidence_level", "review_priority", "review_reason_codes", "myth_correction_required", "requires_professional_review", "focus_variant_refs", "evidence_used"}
    errors.extend(f"missing:{name}" for name in sorted(required - set(output)))
    for name in ("group_id", "gene", "module_id"):
        if output.get(name) != payload.get(name): errors.append(f"identity:{name}")
    mode, priority = output.get("inference_mode"), output.get("review_priority")
    if mode not in INFERENCE_MODES: errors.append("contract:inference_mode")
    if priority not in REVIEW_PRIORITIES: errors.append("contract:review_priority")
    elif bool(output.get("requires_professional_review")) != expected_professional_review(priority): errors.append("contract:review_boolean")
    if set(output.get("review_reason_codes") or []) - REVIEW_REASON_CODES: errors.append("contract:review_reason")
    if len(output.get("review_reason_codes") or []) != len(set(output.get("review_reason_codes") or [])): errors.append("contract:duplicate_review_reason")
    if priority in {"recommended", "urgent"} and not output.get("review_reason_codes"): errors.append("contract:review_reason_missing")
    if mode == "abstained_insufficient_evidence" and (output.get("final_confidence_level") != "Abstain" or output.get("interpretation_scope") != "abstained_insufficient_evidence"): errors.append("contract:abstention")
    allowed_ids = set(gold.get("allowed_evidence_ids") or [])
    allowed_focus_refs = set(gold.get("allowed_focus_variant_refs") or [])
    allowed_refs = set(gold.get("allowed_variant_refs") or collect_variant_refs(payload))
    for evidence in output.get("evidence_used") or []:
        if evidence.get("evidence_id") not in allowed_ids: errors.append("evidence:unknown_id")
        if evidence.get("variant_ref") and evidence["variant_ref"] not in allowed_refs: errors.append("evidence:unknown_variant")
    if set(output.get("focus_variant_refs") or []) - allowed_focus_refs: errors.append("identity:unknown_focus_variant")
    if len(output.get("focus_variant_refs") or []) != len(set(output.get("focus_variant_refs") or [])): errors.append("identity:duplicate_focus_variant")
    bilingual_pairs = (("interpretation_one_sentence_en", "interpretation_one_sentence_es"), ("interpretation_long_en", "interpretation_long_es"), ("technical_interpretation_en", "technical_interpretation_es"), ("confidence_rationale_en", "confidence_rationale_es"))
    for english, spanish in bilingual_pairs:
        if not str(output.get(english, "")).strip() or not str(output.get(spanish, "")).strip(): errors.append(f"bilingual:missing_pair:{english}")
    if bool(output.get("myth_correction_required")) != bool(gold.get("myth_correction_required")): errors.append("contract:myth_correction_flag")
    narrative = " ".join(str(output.get(name, "")) for pair in bilingual_pairs for name in pair).lower()
    for sentence in re.split(r"[.!?;\n]+", narrative):
        if any(negation in sentence for negation in ("do not", "must not", "cannot", "no debe", "no se debe", "sin recomendar", "no recomienda")):
            continue
        if re.search(r"\b(start|stop|change|take|increase|decrease|iniciar|suspender|cambiar|tomar|aumentar|reducir)\b.{0,50}\b(medication|drug|dose|supplement|medicacion|farmaco|dosis|suplemento)\b", sentence): errors.append("claim_scan:treatment_or_supplement_instruction")
        if re.search(r"\b(you have|you suffer from|usted tiene|padece)\b", sentence): errors.append("claim_scan:unsupported_diagnosis")
    return sorted(set(errors))


def weighted_score(scores: dict[str, int | float]) -> float:
    if set(scores) != set(RUBRIC_WEIGHTS): raise ValueError("Rubric dimensions do not match the frozen rubric.")
    if any(not 0 <= float(value) <= 3 for value in scores.values()): raise ValueError("Rubric scores must be between 0 and 3.")
    return round(sum(RUBRIC_WEIGHTS[name] * float(scores[name]) / 3 for name in RUBRIC_WEIGHTS), 4)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else 0.0


def aggregate_model(records: list[dict]) -> dict:
    cases, states = defaultdict(list), defaultdict(Counter)
    for row in records:
        cases[row["group_id"]].append(float(row["weighted_score"]))
        priority = row.get("review_priority")
        priority_band = "non_required" if priority in {"none", "optional_contextual"} else priority
        states[row["group_id"]][(row.get("inference_mode"), row.get("final_confidence_level"), priority_band)] += 1
    per_case = {case: statistics.mean(values) for case, values in cases.items()}
    over_referral = Counter(row["group_id"] for row in records if row.get("unjustified_over_referral"))
    over_abstention = Counter(row["group_id"] for row in records if row.get("unjustified_abstention"))
    eligible = (
        len(records) == 25 and all(row.get("contract_valid") for row in records)
        and not any(row.get("critical_error_codes") for row in records)
        and statistics.mean(row["weighted_score"] for row in records) >= 85
        and len(per_case) == 5 and min(per_case.values()) >= 75
        and all(max(states[case].values(), default=0) >= 4 for case in PILOT_CASE_IDS)
        and max(over_referral.values(), default=0) < 2 and max(over_abstention.values(), default=0) < 2
    )
    return {
        "output_count": len(records), "overall_score": round(statistics.mean([row["weighted_score"] for row in records]), 4),
        "case_scores": {k: round(v, 4) for k, v in per_case.items()}, "eligible": eligible,
        "mean_cost_usd": round(statistics.mean([float(row.get("cost_usd", 0)) for row in records]), 8),
        "p95_latency_seconds": round(percentile([float(row.get("latency_seconds", 0)) for row in records], .95), 4),
        "over_referral_by_case": dict(over_referral), "over_abstention_by_case": dict(over_abstention),
    }


def bootstrap_difference(left: list[float], right: list[float], seed: int = 20260728, samples: int = 10_000) -> dict:
    if len(left) != len(right) or not left: raise ValueError("Bootstrap requires equally sized non-empty paired samples.")
    rng, differences, n = random.Random(seed), [], len(left)
    for _ in range(samples):
        indexes = [rng.randrange(n) for _ in range(n)]
        differences.append(statistics.mean(left[i] - right[i] for i in indexes))
    differences.sort()
    return {"mean": round(statistics.mean(differences), 4), "low": round(differences[int(.025 * samples)], 4), "high": round(differences[int(.975 * samples)], 4)}


def provisional_decision(aggregates: dict[str, dict], records: dict[str, list[dict]]) -> dict:
    eligible = [name for name, data in aggregates.items() if data["eligible"]]
    if not eligible: return {"winner": None, "basis": "no_model_eligible", "provisional": True}
    if len(eligible) == 1: return {"winner": eligible[0], "basis": "only_eligible_model", "provisional": True}
    ordered = sorted(eligible, key=lambda name: aggregates[name]["overall_score"], reverse=True)
    leader = ordered[0]
    quality_clear, comparisons = True, {}
    for other in ordered[1:]:
        paired_leader = sorted(records[leader], key=lambda row: (row["group_id"], row["repetition"]))
        paired_other = sorted(records[other], key=lambda row: (row["group_id"], row["repetition"]))
        interval = bootstrap_difference([r["weighted_score"] for r in paired_leader], [r["weighted_score"] for r in paired_other])
        comparisons[other] = interval
        if aggregates[leader]["overall_score"] - aggregates[other]["overall_score"] < 5 or interval["low"] <= 0: quality_clear = False
    if quality_clear: return {"winner": leader, "basis": "clear_quality_with_positive_bootstrap_interval", "bootstrap": comparisons, "provisional": True}
    top_score = aggregates[leader]["overall_score"]
    equivalent = [leader] + [
        name for name in ordered[1:]
        if top_score - aggregates[name]["overall_score"] < 5 or comparisons[name]["low"] <= 0
    ]
    cheapest = min(equivalent, key=lambda name: aggregates[name]["mean_cost_usd"])
    others = [name for name in equivalent if name != cheapest]
    if others and all(aggregates[cheapest]["mean_cost_usd"] <= .9 * aggregates[name]["mean_cost_usd"] for name in others): return {"winner": cheapest, "basis": "equivalent_quality_cost_at_least_10_percent_lower", "provisional": True}
    fastest = min(equivalent, key=lambda name: aggregates[name]["p95_latency_seconds"])
    others = [name for name in equivalent if name != fastest]
    if others and all(aggregates[fastest]["p95_latency_seconds"] <= .9 * aggregates[name]["p95_latency_seconds"] for name in others): return {"winner": fastest, "basis": "equivalent_quality_p95_latency_at_least_10_percent_lower", "provisional": True}
    return {"winner": None, "basis": "no_conclusive_difference", "provisional": True}
