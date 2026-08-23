#!/usr/bin/env python3
"""Build the production-candidate HEAL LLM1 grouped payload contract v7.

V7 deliberately wraps v6 instead of changing it.  This keeps the frozen silver
benchmark reproducible while adding deterministic input-completeness, age,
release, curation, eligibility and per-group operational state.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import importlib.util
import json
from collections import Counter
from collections import defaultdict
from copy import deepcopy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
V6_PATH = SCRIPT_DIR.parent / "heal-llm1-payload-v6" / "build_llm1_payload_v6.py"
SCHEMA_PATH = SCRIPT_DIR / "llm1_group_payload_v7.schema.json"
PAYLOAD_VERSION = "llm1_group_payload_v7"
EVIDENCE_CUTOFF = "2026-07-28"
CURATION_STATUSES = {"approved", "approved_with_conflict", "withheld", "rejected"}
ACTIVE_AGE_BANDS = {"age_0_7"}
KNOWN_AGE_BANDS = {"age_0_7", "age_8_17"}
KNOWN_SOURCE_TYPES = {"vcf", "gvcf", "all_sites_vcf"}
KNOWN_COMPLETENESS_MODES = {"observed_variants_only", "callability_aware"}
GROUP_STATES = {
    "preflight_blocked", "eligible", "llm1_running", "valid", "quarantined",
    "technical_failure", "curation_excluded",
}


def load_v6_module():
    spec = importlib.util.spec_from_file_location("heal_llm1_payload_v6_for_v7", V6_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load v6 payload builder: {V6_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v6 = load_v6_module()


def clean(value) -> str:
    return str(value or "").strip()


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return clean(value).lower() in {"1", "true", "yes", "y"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def split_csv(value, default: list[str]) -> list[str]:
    result = [item.strip() for item in clean(value).split(",") if item.strip()]
    return result or list(default)


def tier_id(module_id: str) -> str:
    return clean(module_id).split(".", 1)[0]


def exact_keys(value: dict, allowed: set[str], name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    extras = set(value) - allowed
    if extras:
        raise ValueError(f"{name} contains unsupported fields: {sorted(extras)}")


def validate_input_completeness(value: dict) -> None:
    allowed = {
        "mode", "source_type", "reference_build", "reference_version", "sample_count",
        "callability_method", "can_assert_hom_ref", "can_assert_not_callable", "absence_semantics",
    }
    exact_keys(value, allowed, "input_completeness")
    missing = allowed - set(value)
    if missing:
        raise ValueError(f"input_completeness is missing fields: {sorted(missing)}")
    if value["mode"] not in KNOWN_COMPLETENESS_MODES:
        raise ValueError("Unsupported input completeness mode.")
    if value["source_type"] not in KNOWN_SOURCE_TYPES:
        raise ValueError("Unsupported genomic source type.")
    if int(value["sample_count"]) != 1:
        raise ValueError("HEAL grouped LLM1 accepts exactly one sample per input.")
    if value["mode"] == "observed_variants_only":
        if value["can_assert_hom_ref"] or value["can_assert_not_callable"]:
            raise ValueError("Sparse observed-only input cannot assert hom-ref or not-callable states.")
        if value["absence_semantics"] != "not_observed_callability_unknown":
            raise ValueError("Sparse absence must remain not_observed_callability_unknown.")
    else:
        if value["source_type"] not in {"gvcf", "all_sites_vcf"}:
            raise ValueError("callability_aware mode requires gVCF or all-sites VCF input.")
        if not value["callability_method"]:
            raise ValueError("callability_aware mode requires a documented callability method.")
        if not (value["can_assert_hom_ref"] or value["can_assert_not_callable"]):
            raise ValueError("callability_aware mode must support at least one explicit absence state.")
        if value["absence_semantics"] != "explicit_hom_ref_or_not_callable":
            raise ValueError("Callability-aware absence must be explicit.")


def validate_v7_payload(payload: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    allowed = set(schema["properties"])
    required = set(schema["required"])
    exact_keys(payload, allowed, "payload")
    missing = required - set(payload)
    if missing:
        raise ValueError(f"V7 payload is missing fields: {sorted(missing)}")
    if payload["payload_schema_version"] != PAYLOAD_VERSION:
        raise ValueError("Unexpected v7 payload version.")
    validate_input_completeness(payload["input_completeness"])
    age = payload["age_context"]
    exact_keys(age, {"band", "source", "enabled"}, "age_context")
    if age["band"] not in KNOWN_AGE_BANDS:
        raise ValueError("Unsupported age band.")
    release = payload["release_context"]
    exact_keys(
        release,
        {
            "active_tiers", "curation_snapshot_id", "evidence_cutoff", "experimental_canary",
            "client_visible", "eligible_for_llm2", "execution_scope",
        },
        "release_context",
    )
    operational = payload["operational_state"]
    exact_keys(
        operational,
        {"status", "preflight_valid", "llm1_eligible", "blocker_codes", "decision_reason"},
        "operational_state",
    )
    if operational["status"] not in GROUP_STATES:
        raise ValueError("Unsupported group operational state.")
    curation = payload["internal_scientific_curation"]
    exact_keys(
        curation,
        {
            "authority", "snapshot_id", "evidence_cutoff", "mechanism_status", "gwas_status",
            "fully_classified", "source_policy",
        },
        "internal_scientific_curation",
    )
    expert = payload["expert_review_gate"]
    exact_keys(expert, {
        "condition_1_strong_evidence", "condition_2_material_scientific_conflict",
        "condition_3_observed_variant_directly_relevant", "condition_4_material_individual_change",
        "condition_5_unresolved_from_available_context", "conflict_evidence_ids",
        "directly_relevant_focus_variant_refs",
    }, "expert_review_gate")
    if expert["condition_4_material_individual_change"] != "model_assessment_required" or expert["condition_5_unresolved_from_available_context"] != "model_assessment_required":
        raise ValueError("Expert review conditions 4 and 5 must remain model-assessed.")
    if expert["condition_3_observed_variant_directly_relevant"] is not bool(expert["directly_relevant_focus_variant_refs"]):
        raise ValueError("Expert review observed-variant gate is inconsistent.")
    context = payload["patient_context"]
    if context.get("structured_medications") or context.get("free_note"):
        raise ValueError("Production-candidate v7 is genetics-only; patient context must be not_provided.")
    if any(axis.get("items") or axis.get("default_state") != "not_provided" for axis in context.get("axes", {}).values()):
        raise ValueError("All patient-context axes must remain not_provided in this release.")


def pgx_signal(payload: dict) -> bool:
    semantics = payload.get("clinical_evidence_summary", {}).get("condition_conflict_semantics", {})
    if semantics.get("drug_response_context_variant_keys"):
        return True
    for group in payload.get("clinical_evidence_summary", {}).get("assertion_groups") or []:
        text = json.dumps(group, ensure_ascii=False).lower()
        if "drug_response" in text or "pharmacogen" in text or "clinpgx" in text:
            return True
    return False


def generalize_pgx(payload: dict) -> None:
    curation = dict(payload.get("professional_curation") or {})
    signal = pgx_signal(payload)
    strong = False
    applicable = False
    for item in payload.get("focus_variant_evidence") or []:
        pgx = item.get("pharmacogenomic_evidence") or item.get("pgx") or {}
        if clean(pgx.get("genotype_applicability")).lower() in {"confirmed", "applicable"}:
            applicable = True
        if clean(pgx.get("evidence_strength")).lower() in {"strong", "high"}:
            strong = True
    medication_present = bool(payload.get("patient_context", {}).get("structured_medications"))
    curation.update({
        "pgx_observed_genotype_applicability": "confirmed" if applicable else "not_confirmed" if signal else "not_applicable",
        "pgx_evidence_strength": "strong" if strong else "weak_context_only" if signal else "not_applicable",
        "pgx_relevant_structured_medication_present": medication_present,
        "pgx_escalation_allowed": bool(signal and strong and applicable and medication_present),
    })
    payload["professional_curation"] = curation


def expert_review_gate(payload: dict) -> dict:
    curation = payload.get("professional_curation") or {}
    basis = curation.get("expert_review_basis") or payload.get("curated_mechanism", {}).get("expert_review_basis") or {}
    conflict_ids = sorted(set(basis.get("conflict_evidence_ids") or []))
    direct_refs = []
    for row in payload.get("focus_variant_evidence") or []:
        serialized = json.dumps(row, ensure_ascii=False)
        if conflict_ids and any(evidence_id in serialized for evidence_id in conflict_ids):
            variant_ref = clean(row.get("variant_ref"))
            if variant_ref:
                direct_refs.append(variant_ref)
    return {
        "condition_1_strong_evidence": bool(basis.get("strong_evidence")),
        "condition_2_material_scientific_conflict": bool(basis.get("material_scientific_conflict")),
        "condition_3_observed_variant_directly_relevant": bool(direct_refs),
        "condition_4_material_individual_change": "model_assessment_required",
        "condition_5_unresolved_from_available_context": "model_assessment_required",
        "conflict_evidence_ids": conflict_ids,
        "directly_relevant_focus_variant_refs": sorted(set(direct_refs)),
    }


def completeness_from_request(request: dict) -> dict:
    supplied = dict(request.get("inputCompleteness") or {})
    mode = clean(supplied.get("mode")) or "observed_variants_only"
    source_type = clean(supplied.get("source_type")) or "vcf"
    result = {
        "mode": mode,
        "source_type": source_type,
        "reference_build": clean(supplied.get("reference_build")) or clean(request.get("assembly")) or "GRCh38",
        "reference_version": clean(supplied.get("reference_version")) or "HEAL_GRCh38_runtime_reference",
        "sample_count": int(supplied.get("sample_count", 1)),
        "callability_method": clean(supplied.get("callability_method")) or ("not_available" if mode == "observed_variants_only" else ""),
        "can_assert_hom_ref": as_bool(supplied.get("can_assert_hom_ref")),
        "can_assert_not_callable": as_bool(supplied.get("can_assert_not_callable")),
        "absence_semantics": clean(supplied.get("absence_semantics")) or (
            "not_observed_callability_unknown" if mode == "observed_variants_only" else "explicit_hom_ref_or_not_callable"
        ),
    }
    validate_input_completeness(result)
    return result


def scientific_state(payload: dict) -> tuple[bool, list[str]]:
    blockers = []
    curation = payload.get("professional_curation") or {}
    mechanism = clean(curation.get("mechanism_status")) or "missing"
    if mechanism not in CURATION_STATUSES:
        blockers.append("mechanism_not_classified")
    if int(curation.get("unreviewed_gwas_cluster_count") or 0) > 0:
        blockers.append("gwas_not_classified")
    return not blockers, blockers


def has_approved_interpretable_content(payload: dict) -> bool:
    curation = payload.get("professional_curation") or {}
    mechanism = clean(curation.get("mechanism_status"))
    if mechanism in {"approved", "approved_with_conflict"} and payload.get("curated_mechanism", {}).get("usable_by_llm"):
        return True
    if int(curation.get("approved_gwas_cluster_count") or 0) > 0:
        return True
    if payload.get("focus_variant_evidence") and (
        payload.get("clinical_evidence_summary", {}).get("assertions_total", 0)
        or payload.get("publication_evidence_digest", {}).get("selected_publications")
    ):
        return mechanism in {"approved", "approved_with_conflict"}
    return False


def convert_payload(source: dict, request: dict) -> dict:
    result = json.loads(json.dumps(source))
    result["payload_schema_version"] = PAYLOAD_VERSION
    result["execution_mode"] = "preflight"
    result["input_completeness"] = completeness_from_request(request)
    age_band = clean(request.get("ageBand")) or "age_0_7"
    if age_band not in KNOWN_AGE_BANDS:
        raise ValueError(f"Unsupported age band: {age_band}")
    result["age_context"] = {
        "band": age_band,
        "source": "operator_provided",
        "enabled": age_band in set(split_csv(request.get("activeAgeBands"), ["age_0_7"])),
    }
    active_tiers = split_csv(request.get("activeTiers"), ["T1"])
    canaries = set(split_csv(request.get("experimentalCanaryGroups"), ["IFNG:T3.5"]))
    group_tier = tier_id(result["module_id"])
    is_canary = result["group_id"] in canaries
    tier_active = group_tier in active_tiers
    curation_ready, blockers = scientific_state(result)
    technical_gates = result.get("gates") or {}
    for name in (
        "token_budget_ready", "target_gene_annotation_ready", "allele_specific_frequency_ready",
        "source_errors_acceptable_for_pilot", "evidence_ledger_complete",
    ):
        if technical_gates.get(name) is False:
            blockers.append(name)
    if not result["age_context"]["enabled"]:
        blockers.append("age_band_not_enabled")
    active_or_canary = tier_active or is_canary
    if not active_or_canary:
        status = "curation_excluded"
        reason = "Tier is not active for this release."
    elif blockers:
        status = "preflight_blocked"
        reason = "Scientific or technical preflight is incomplete."
    elif is_canary:
        status = "eligible"
        reason = "Approved experimental canary; excluded from Tier 1 coverage and LLM2."
    elif has_approved_interpretable_content(result):
        status = "eligible"
        reason = "Active Tier 1 group has approved interpretable content."
    else:
        status = "curation_excluded"
        reason = "Scientific curation is complete but no approved interpretable content is available."
    llm1_eligible = status == "eligible"
    client_visible = bool(llm1_eligible and tier_active and not is_canary)
    curation = result.get("professional_curation") or {}
    snapshot = clean(request.get("curationSnapshotId")) or "internal-scientific-curation-20260728"
    result["release_context"] = {
        "active_tiers": active_tiers,
        "curation_snapshot_id": snapshot,
        "evidence_cutoff": EVIDENCE_CUTOFF,
        "experimental_canary": is_canary,
        "client_visible": client_visible,
        "eligible_for_llm2": client_visible,
        "execution_scope": "internal_auto",
    }
    result["internal_scientific_curation"] = {
        "authority": "internal_ai",
        "snapshot_id": snapshot,
        "evidence_cutoff": EVIDENCE_CUTOFF,
        "mechanism_status": clean(curation.get("mechanism_status")) or "missing",
        "gwas_status": clean(curation.get("gwas_relevance_status")) or "not_applicable",
        "fully_classified": curation_ready,
        "source_policy": "registered_sources_only_no_studies_after_cutoff",
    }
    result["expert_review_gate"] = expert_review_gate(result)
    result["operational_state"] = {
        "status": status,
        "preflight_valid": status != "preflight_blocked",
        "llm1_eligible": llm1_eligible,
        "blocker_codes": sorted(set(blockers)),
        "decision_reason": reason,
    }
    result["gates"].update({
        "scientific_curation_ready": curation_ready,
        "age_band_ready": result["age_context"]["enabled"],
        "tier_or_canary_ready": active_or_canary,
        "llm1_internal_ready": llm1_eligible,
        "llm2_eligible": client_visible,
    })
    result["provenance"].update({
        "payload_builder": PAYLOAD_VERSION,
        "source_payload_version": source.get("payload_schema_version"),
        "curation_snapshot_id": snapshot,
        "evidence_cutoff": EVIDENCE_CUTOFF,
    })
    generalize_pgx(result)
    result["traceability_allowlist"] = v6.traceability_allowlist(result)
    validate_v7_payload(result)
    return result


def card_from_payload(payload: dict) -> dict:
    state = payload["operational_state"]
    return {
        "group_id": payload["group_id"],
        "gene": payload["gene"],
        "module_id": payload["module_id"],
        "module_name": payload.get("group_context", {}).get("module_name", ""),
        "tier": tier_id(payload["module_id"]),
        "status": state["status"],
        "coverage_status": "interpretable" if state["llm1_eligible"] else "not_interpreted",
        "experimental_canary": payload["release_context"]["experimental_canary"],
        "client_visible": payload["release_context"]["client_visible"],
        "eligible_for_llm2": payload["release_context"]["eligible_for_llm2"],
        "decision_reason": state["decision_reason"],
        "blocker_codes": state["blocker_codes"],
        "input_completeness": payload["input_completeness"],
        "age_band": payload["age_context"]["band"],
        "curation_snapshot_id": payload["release_context"]["curation_snapshot_id"],
        "evidence_cutoff": EVIDENCE_CUTOFF,
        "focus_variant_count": len(payload.get("focus_variant_evidence") or []),
        "focus_variants": [
            {
                "variant_ref": row.get("variant_ref", ""),
                "variant_key": row.get("variant_key", ""),
                "genotype": row.get("genotype") or row.get("zygosity") or row.get("gt_alleles") or "",
                "identity_match_class": row.get("identity_match_class", ""),
                "target_gene_annotation": row.get("target_gene_annotation") or {},
                "population": row.get("population") or {},
            }
            for row in payload.get("focus_variant_evidence") or []
        ],
        "curated_mechanism": payload.get("curated_mechanism") or {},
        "clinical_conflicts": payload.get("clinical_evidence_summary", {}).get("condition_conflict_semantics") or {},
        "gwas_context": {
            "approved_cluster_count": payload.get("professional_curation", {}).get("approved_gwas_cluster_count", 0),
            "valid_but_excluded_cluster_count": payload.get("professional_curation", {}).get("valid_but_excluded_gwas_cluster_count", 0),
            "unreviewed_cluster_count": payload.get("professional_curation", {}).get("unreviewed_gwas_cluster_count", 0),
            "prioritized_clusters": payload.get("gwas_evidence_summary", {}).get("prioritized_clusters") or [],
        },
        "source_failures": payload.get("source_failures") or {},
        "gates": payload.get("gates") or {},
        "provenance": payload.get("provenance") or {},
        "deterministic_summary": payload.get("deterministic_summary") or {},
        "allowed_evidence_ids": payload["traceability_allowlist"]["allowed_evidence_ids"],
        "allowed_variant_refs": payload["traceability_allowlist"]["allowed_variant_refs"],
    }


def empty_group_payload(mechanism: dict, canonical_rows: list[dict], patient_context: dict) -> dict:
    gene = clean(mechanism.get("gene"))
    module_id = clean(mechanism.get("module_id"))
    group_id = f"{gene}:{module_id}"
    canonical = canonical_rows[0] if canonical_rows else {}
    status = clean(mechanism.get("curation_status")) or "missing"
    usable = status in {"approved", "approved_with_conflict"}
    payload = {
        "payload_schema_version": "llm1_group_payload_v6",
        "execution_mode": "dry_run",
        "group_id": group_id,
        "gene": gene,
        "module_id": module_id,
        "group_context": {
            "module_name": clean(canonical.get("module_name")),
            "system_within_module": clean(canonical.get("system_within_module")),
            "tier": clean(canonical.get("tier")) or tier_id(module_id),
            "module_status": clean(canonical.get("module_status")),
            "evidence_tier": clean(canonical.get("evidence_tier")),
        },
        "canonical_status": canonical_rows,
        "curated_mechanism": {
            "registry_version": clean(mechanism.get("mechanism_registry_version")) or "mechanism_registry_v1",
            "curation_status": status,
            "biological_function": clean(mechanism.get("biological_function")),
            "pathway": clean(mechanism.get("pathway")),
            "directionality": clean(mechanism.get("directionality")),
            "related_systems": clean(mechanism.get("related_systems")),
            "related_modules": clean(mechanism.get("related_modules")),
            "evidence_tier": clean(mechanism.get("mechanism_evidence_tier")),
            "source": "internal_scientific_curation",
            "sources": [item for item in clean(mechanism.get("source_ids_or_urls")).split("|") if item],
            "evidence_id": f"mechanism:{group_id}",
            "limitation": clean(mechanism.get("review_notes")),
            "usable_by_llm": usable,
        },
        "focus_variant_evidence": [],
        "clinical_evidence_summary": {
            "assertion_groups": [], "assertions_total": 0, "classification_counts": {},
            "complete_artifact": True, "condition_conflict_semantics": {
                "same_condition_conflict_variant_keys": [], "cross_condition_heterogeneity_variant_keys": [],
                "drug_response_context_variant_keys": [],
            }, "conflicting_variant_keys": [], "legacy_conflicting_variant_keys": [],
            "referenced_group_count": 0, "referenced_group_ids_for_audit": [],
        },
        "gwas_evidence_summary": {
            "clusters_total": 0, "complete_artifact": True, "context_summary": "No observed variant GWAS context.",
            "prioritized_cluster_count": 0, "prioritized_cluster_refs_for_audit": [], "prioritized_clusters": [],
            "replicated_cluster_refs_for_audit": [], "replicated_pending_relevance": [],
            "replicated_pending_relevance_count": 0,
        },
        "publication_evidence_digest": {
            "complete_artifact": True, "publication_ref_count": 0, "publication_refs_for_audit": [],
            "publications_total": 0, "selected_publications": [], "study_type_counts": {},
        },
        "supporting_context": {"count": 0, "complete_artifact": True, "variant_refs_for_audit": [], "by_downstream_role": {}, "by_local_region": {}},
        "transcript_discordant_context": {"count": 0, "complete_artifact": True, "variant_refs_for_audit": [], "by_downstream_role": {}, "by_local_region": {}},
        "target_gene_discordant_context": {"count": 0, "complete_artifact": True, "variant_refs_for_audit": [], "by_downstream_role": {}, "by_local_region": {}},
        "identity_unresolved": {"count": 0, "complete_artifact": True, "variant_refs_for_audit": [], "by_downstream_role": {}, "by_local_region": {}},
        "source_failures": {"count": 0, "complete_artifact": True, "variant_refs_for_audit": [], "error_refs_for_audit": [], "error_refs_referenced_only": [], "by_downstream_role": {}, "by_local_region": {}, "by_source": {}},
        "deterministic_summary": {
            "focus_variant_count": 0, "group_size_total": 0, "identity_counts": {}, "local_region_class_counts": {},
            "primary_variant_refs": [],
            "statement": "No observed variants were available for this canonical gene-module group; callability is unknown.",
        },
        "evidence_coverage": {"coverage_artifact": "", "ledger_artifact": "", "reconciled": True, "records_total": 0, "status_counts": {}},
        "compression_metadata": {
            "budget_status": "within_target", "compression_level": "none", "estimated_tokens": 0,
            "estimation_method": "deterministic_empty_group", "generative_digest_used": False,
            "hard_limit_tokens": 25000, "strategy": "coverage_only", "target_tokens": 12000,
        },
        "provenance": {
            "generated_at": utc_now(), "payload_builder": "llm1_group_payload_v6_compatibility_stub",
            "source_physical_matrix": "", "source_projection": "", "variant_detail_artifact": "",
        },
        "gates": {
            "digest_validated": True, "evidence_ledger_complete": True, "evidence_packet_ready": True,
            "source_errors_acceptable_for_pilot": True, "token_budget_ready": True,
            "mechanism_registry_ready": status in CURATION_STATUSES, "target_gene_annotation_ready": True,
            "allele_specific_frequency_ready": True, "gwas_relevance_ready": True,
            "group_payload_ready": False, "llm1_pilot_ready": False,
        },
        "allele_specific_frequency_summary": {
            "variants_with_observed_alt_frequency": 0, "variants_without_observed_alt_frequency": 0,
            "other_allele_context_present": 0, "audit_artifact": "allele_specific_frequency_audit.csv",
        },
        "professional_curation": {
            "mechanism_registry_version": clean(mechanism.get("mechanism_registry_version")) or "mechanism_registry_v1",
            "mechanism_status": status, "gwas_registry_version": "gwas_module_relevance_registry_v1",
            "gwas_relevance_status": "not_applicable", "approved_gwas_cluster_count": 0,
            "valid_but_excluded_gwas_cluster_count": 0, "unreviewed_gwas_cluster_count": 0,
            "pgx_observed_genotype_applicability": "not_applicable", "pgx_evidence_strength": "not_applicable",
            "pgx_relevant_structured_medication_present": False, "pgx_escalation_allowed": False,
        },
        "patient_context": deepcopy(patient_context),
        "traceability_allowlist": {"allowed_evidence_ids": [], "allowed_variant_refs": [], "allowed_focus_variant_refs": []},
    }
    payload["traceability_allowlist"] = v6.traceability_allowlist(payload)
    return payload


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "blocker_codes": "|".join(row.get("blocker_codes") or [])})


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def human_review_foundation_state(path_value: str) -> dict:
    """Validate the non-active gold foundation before internal-auto can run."""
    path = Path(clean(path_value)).resolve() if clean(path_value) else None
    if path is None or not path.exists():
        return {"ready": False, "reason": "human_review_foundation_missing", "path": str(path or "")}
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "reason": "human_review_foundation_unreadable", "path": str(path)}
    if snapshot.get("schema_version") != "tier1_human_review_foundation_v1":
        return {"ready": False, "reason": "human_review_foundation_schema_invalid", "path": str(path)}
    expected_hash = snapshot.get("snapshot_sha256")
    hashed = dict(snapshot); hashed.pop("snapshot_sha256", None)
    actual_hash = hashlib.sha256(canonical_json(hashed)).hexdigest()
    if expected_hash != actual_hash:
        return {"ready": False, "reason": "human_review_foundation_hash_invalid", "path": str(path)}
    groups = snapshot.get("groups") or {}
    if len(groups) != 12:
        return {"ready": False, "reason": "human_review_foundation_group_count_invalid", "path": str(path)}
    unresolved = [group_id for group_id, row in groups.items() if not row.get("release_ready") or row.get("technical_flags")]
    if not snapshot.get("release_ready") or unresolved:
        return {
            "ready": False, "reason": "human_review_foundation_not_released", "path": str(path),
            "unresolved_group_count": len(unresolved),
        }
    return {"ready": True, "reason": "pass", "path": str(path), "snapshot_sha256": expected_hash}


def process(request: dict) -> dict:
    output_dir = Path(request["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    v6_summary = v6.process(request, emit_summary=False)
    source_path = Path(v6_summary["outputs"]["groupPayloadsJsonlV6"])
    source_payloads = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    mechanisms = v6.read_csv(Path(v6_summary["outputs"]["persistentMechanismRegistryCsv"]))
    registry_ids = {f"{clean(row.get('gene'))}:{clean(row.get('module_id'))}" for row in mechanisms}
    unexpected_source_group_ids = sorted(row["group_id"] for row in source_payloads if row["group_id"] not in registry_ids)
    source_payloads = [row for row in source_payloads if row["group_id"] in registry_ids]
    existing_ids = {row["group_id"] for row in source_payloads}
    canonical_rows = v6.read_csv(Path(request["canonicalStatusPath"]))
    canonical_by_group: dict[str, list[dict]] = defaultdict(list)
    for row in canonical_rows:
        group_id = f"{clean(row.get('approved_symbol') or row.get('gene'))}:{clean(row.get('module_id'))}"
        canonical_by_group[group_id].append(row)
    patient_context = deepcopy(source_payloads[0]["patient_context"]) if source_payloads else {
        "schema_version": "llm1_patient_context_v1", "axes": {}, "structured_medications": [],
        "free_note": "", "free_note_authoritative": False, "interpretation_constraints": {},
    }
    for mechanism in mechanisms:
        group_id = f"{clean(mechanism.get('gene'))}:{clean(mechanism.get('module_id'))}"
        if group_id and group_id not in existing_ids:
            source_payloads.append(empty_group_payload(mechanism, canonical_by_group.get(group_id, []), patient_context))
            existing_ids.add(group_id)
    source_payloads.sort(key=lambda row: row["group_id"])
    human_review_foundation = human_review_foundation_state(str(request.get("humanReviewFoundationPath") or ""))
    payloads = [convert_payload(item, request) for item in source_payloads]
    cards = [card_from_payload(item) for item in payloads]
    payload_path = output_dir / "llm1_group_payloads_v7.jsonl"
    payload_csv = output_dir / "llm1_group_payloads_v7.csv"
    schema_copy = output_dir / SCHEMA_PATH.name
    preflight_path = output_dir / "llm1_group_preflight_v7.csv"
    cards_path = output_dir / "llm1_group_cards_v7.json"
    summary_path = output_dir / "llm1_group_payload_v7_summary.json"
    payload_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in payloads), encoding="utf-8")
    write_csv(payload_csv, [{"group_id": row["group_id"], "payload_json": json.dumps(row, ensure_ascii=False, separators=(",", ":"))} for row in payloads], ["group_id", "payload_json"])
    write_csv(preflight_path, cards, [
        "group_id", "gene", "module_id", "tier", "status", "coverage_status", "experimental_canary",
        "client_visible", "eligible_for_llm2", "decision_reason", "blocker_codes", "age_band",
        "curation_snapshot_id", "evidence_cutoff", "focus_variant_count",
    ])
    cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    schema_copy.write_text(SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    status_counts = dict(Counter(row["operational_state"]["status"] for row in payloads))
    tier1 = [row for row in payloads if tier_id(row["module_id"]) == "T1"]
    registry_concordant = not unexpected_source_group_ids
    preflight_valid = len(payloads) == 180 and registry_concordant and all(row["operational_state"]["preflight_valid"] for row in payloads)
    tier1_classified = len(tier1) == 105 and all(row["internal_scientific_curation"]["fully_classified"] for row in tier1)
    summary = {
        "status": "valid" if preflight_valid else "blocked",
        "schemaVersion": "gene_module_v2",
        "payloadSchemaVersion": PAYLOAD_VERSION,
        "executionMode": "preflight",
        "metadata": {
            "total_groups": len(payloads),
            "tier1_groups": len(tier1),
            "tier1_fully_classified": sum(row["internal_scientific_curation"]["fully_classified"] for row in tier1),
            "llm1_eligible_groups": sum(row["operational_state"]["llm1_eligible"] for row in payloads),
            "client_visible_groups": sum(row["release_context"]["client_visible"] for row in payloads),
            "experimental_canaries": sum(row["release_context"]["experimental_canary"] for row in payloads),
            "status_counts": status_counts,
            "input_completeness_mode": payloads[0]["input_completeness"]["mode"] if payloads else "",
            "age_band": payloads[0]["age_context"]["band"] if payloads else "",
            "active_tiers": payloads[0]["release_context"]["active_tiers"] if payloads else [],
            "evidence_cutoff": EVIDENCE_CUTOFF,
            "unexpected_source_group_ids": unexpected_source_group_ids,
            "payload_sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
            "human_review_foundation": human_review_foundation,
        },
        "gates": {
            "all180StructurallyPresent": "pass" if len(payloads) == 180 else "fail",
            "groupRegistryConcordance": "pass" if registry_concordant else "blocked",
            "preflightValid": "pass" if preflight_valid else "blocked",
            "tier1CurationComplete": "pass" if tier1_classified else "blocked",
            "humanReviewFoundation": "pass" if human_review_foundation["ready"] else "blocked",
            "llm1InternalAutoReady": "pass" if preflight_valid and tier1_classified and human_review_foundation["ready"] else "blocked",
            "age8To17Enabled": "blocked",
        },
        "outputs": {
            **v6_summary.get("outputs", {}),
            "groupPayloadsJsonlV7": str(payload_path),
            "groupPayloadsCsvV7": str(payload_csv),
            "groupPayloadSchemaV7Json": str(schema_copy),
            "groupPreflightV7Csv": str(preflight_path),
            "groupCardsV7Json": str(cards_path),
            "groupPayloadV7SummaryJson": str(summary_path),
        },
        "timestamps": {"completedAt": utc_now()},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build HEAL LLM1 grouped payloads v7.")
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    request = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    process(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
