#!/usr/bin/env python3
"""Deterministic rules for HEAL Tier 1 mechanism curation v2.

This module deliberately contains no activation side effects. Model decisions are
candidates until a complete review manifest is approved by the internal owner.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


CUTOFF = dt.date(2026, 7, 28)
MODEL = "gpt-5.6-sol"
CORE_STATUSES = {"approved", "approved_with_conflict", "withheld", "rejected"}
INFERENCE_CEILINGS = {"none", "context_only", "initial_guide_candidate"}
POSITIVE_REJECTION_REASONS = {"wrong_gene_mapping", "irrelevant_to_module", "material_contradiction"}
SEMANTIC_REASON_CODES = {
    "insufficient_primary_evidence", "only_authoritative_source", "only_review_evidence",
    "only_functional_evidence", "only_human_evidence", "identity_unresolved",
    "module_relation_indirect", "wrong_gene_mapping", "irrelevant_to_module",
    "material_contradiction", "metadata_insufficient",
}
SEMANTIC_EXCLUSION_REASONS = {
    "not_used_in_final_assessment", "not_directly_relevant", "review_only",
    "metadata_insufficient", "duplicate_or_derived", "result_not_interpretable",
    "outside_valid_evidence_allowlist",
}
SEMANTIC_ROLES = {"functional", "human", "canonical", "conflict_or_null"}
SEMANTIC_DIRECTIONS = {"positive", "negative", "null", "mixed", "not_applicable"}
CORE_CONFLICT_CLASSES = {
    "direct_material_contradiction", "contextual_heterogeneity", "downstream_null",
    "limited_generalizability", "none",
}
GOLD_GROUPS = (
    "MTHFR:T1.1", "CYCS:T1.1", "CLOCK:T1.2", "ASMT:T1.2",
    "PEMT:T1.3", "FADS1:T1.3", "IL6:T1.4", "IL10:T1.4",
    "RUNX2:T1.5", "COL14A1:T1.5", "ABCB1:T1.6", "GCLM:T1.6",
)
CALIBRATION_GROUPS = (
    "MTHFR:T1.1", "ASMT:T1.2", "FADS1:T1.3", "IL10:T1.4", "RUNX2:T1.5", "GCLM:T1.6",
)
HOLDOUT_GROUPS = tuple(group for group in GOLD_GROUPS if group not in CALIBRATION_GROUPS)
WEIGHTS = {
    "methodology": 0.30,
    "applicability": 0.25,
    "independence": 0.20,
    "precision_size": 0.15,
    "recency": 0.10,
}

# Prompts and Semantic V2 remain frozen.  This version identifies the purely
# deterministic eligibility rule used to derive the public inference ceiling.
NORMALIZER_VERSION = "semantic-v2-normalizer-prototype-v2"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: object, *, immutable: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if immutable and target.exists():
        raise FileExistsError(f"Immutable artifact already exists: {target}")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_evidence_manifest_artifacts(path: str | Path) -> list[str]:
    manifest_path = Path(path).resolve()
    manifest = read_json(manifest_path)
    errors: list[str] = []
    unhashed = dict(manifest)
    declared = unhashed.pop("manifest_sha256", "")
    if not declared or sha256_json(unhashed) != declared:
        errors.append("evidence_manifest_hash_mismatch")
    if manifest.get("schema_version") == "tier1_evidence_packet_manifest_v4":
        qa_path = Path(manifest.get("metadata_qa_path") or "")
        snapshot_path = Path(manifest.get("pubmed_snapshot_manifest_path") or "")
        if not qa_path.is_file() or sha256_file(qa_path) != manifest.get("metadata_qa_sha256"):
            errors.append("metadata_qa_file_hash_mismatch")
        else:
            qa_rows = read_csv(qa_path)
            if not qa_rows or any(row.get("status_after") != "consistent" or row.get("title_consistent") != "true" for row in qa_rows):
                errors.append("metadata_qa_contains_errors")
        if not snapshot_path.is_file():
            errors.append("pubmed_snapshot_manifest_missing")
        else:
            snapshot = read_json(snapshot_path)
            snapshot_unhashed = dict(snapshot)
            snapshot_declared = snapshot_unhashed.pop("manifest_sha256", "")
            if snapshot_declared != manifest.get("pubmed_snapshot_manifest_sha256") or sha256_json(snapshot_unhashed) != snapshot_declared:
                errors.append("pubmed_snapshot_manifest_hash_mismatch")
            if snapshot.get("missing_pmids") or snapshot.get("record_count") != snapshot.get("requested_count"):
                errors.append("pubmed_snapshot_incomplete")
            for batch in snapshot.get("batches") or []:
                response_path = Path(batch.get("response_path") or "")
                if not response_path.is_file() or sha256_file(response_path) != batch.get("response_sha256"):
                    errors.append("pubmed_snapshot_response_hash_mismatch")
                    break
    return sorted(set(errors))


def parse_date(value: str | None) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in (r"^(\d{4})-(\d{2})-(\d{2})", r"^(\d{4})-(\d{2})$", r"^(\d{4})$"):
        match = re.match(pattern, text)
        if match:
            parts = [int(part) for part in match.groups()]
            return dt.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    return None


def source_key(source: dict) -> str:
    pmid = str(source.get("pmid") or "").strip()
    doi = str(source.get("doi") or "").strip().lower()
    if pmid:
        return f"pmid:{pmid}"
    if doi:
        return f"doi:{doi}"
    return "title:" + re.sub(r"\W+", "", str(source.get("title") or "").lower())[:160]


def normalize_title(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title_identity(value: str | None) -> str:
    """Stable title identity key insensitive to markup/typographic spacing."""
    return re.sub(r"[^a-z0-9]+", "", normalize_title(value).casefold())


def normalize_doi(value: str | None) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)
    return text.rstrip(" .;,)")


def publication_metadata_errors(sources: Iterable[dict]) -> list[str]:
    """Reject contradictory PMID/title/DOI rows before a packet is selected.

    The packet builder obtains publication records from authoritative services.
    If two retrieved rows claim the same PMID but disagree on stable metadata,
    silently picking one would make the evidence ledger non-traceable.
    """
    by_pmid: dict[str, list[dict]] = defaultdict(list)
    errors: list[str] = []
    for source in sources:
        if source.get("source_kind") != "primary_publication":
            continue
        pmid = str(source.get("pmid") or "").strip()
        title = re.sub(r"\W+", "", normalize_title(source.get("title")).lower())
        doi = normalize_doi(source.get("doi"))
        # Europe PMC legitimately returns some primary articles with a DOI but
        # no PMID. They remain traceable primary sources when title and DOI are
        # present; only records lacking both authoritative identifiers are
        # unusable. PMID-linked rows still require a numeric PMID.
        identifier_valid = (bool(pmid) and pmid.isdigit()) or bool(doi)
        if not identifier_valid or not title:
            errors.append(f"metadata_unusable:{source.get('evidence_id') or 'unknown'}")
        if doi and not doi.startswith("10."):
            errors.append(f"doi_format_invalid:{source.get('evidence_id') or 'unknown'}")
        if pmid:
            by_pmid[pmid].append(source)
    for pmid, rows in by_pmid.items():
        titles = {re.sub(r"\W+", "", normalize_title(row.get("title")).lower()) for row in rows if row.get("title")}
        dois = {normalize_doi(row.get("doi")) for row in rows if row.get("doi")}
        if len(titles) > 1:
            errors.append(f"pmid_title_mismatch:{pmid}")
        if len(dois) > 1:
            errors.append(f"pmid_doi_mismatch:{pmid}")
    return sorted(set(errors))


def deduplicate_sources(sources: Iterable[dict]) -> list[dict]:
    """Deduplicate publications while preserving the most complete metadata row."""
    best: dict[str, dict] = {}
    for source in sources:
        key = source_key(source)
        current = best.get(key)
        score = sum(bool(source.get(field)) for field in ("title", "abstract_or_summary", "publication_date", "pmid", "doi", "cohort_key"))
        current_score = -1 if current is None else sum(bool(current.get(field)) for field in ("title", "abstract_or_summary", "publication_date", "pmid", "doi", "cohort_key"))
        if score > current_score:
            best[key] = dict(source)
    return sorted(best.values(), key=lambda row: (str(row.get("publication_date") or ""), source_key(row)), reverse=True)


def _selection_score(source: dict) -> tuple:
    roles = set(source.get("role_candidates") or [])
    # Recall-first ordering: direct human evidence comes before broad/contextual
    # evidence, while functional and conflict/null evidence retain dedicated
    # slots below. This ordering only ranks candidates; it does not turn a
    # study into individual-level evidence.
    human_direct = "human_study" in roles and (
        "variant_specific" in roles or source.get("identity_match") == "exact"
    )
    functional_direct = "functional_study" in roles and source.get("identity_match") == "exact"
    return (
        1 if source.get("identity_match") == "exact" else 0,
        1 if source.get("module_relevance_candidate") else 0,
        1 if source.get("metadata_complete") else 0,
        1 if human_direct else 0,
        1 if functional_direct else 0,
        1 if "conflict_or_null" in roles else 0,
        1 if source.get("cohort_key") and not source.get("derived_publication") else 0,
        1 if "variant_specific" in roles else 0,
        str(source.get("publication_date") or ""),
        source_key(source),
    )


def selection_lineage_key(source: dict) -> str:
    """Identify preprint/version-of-record candidates without deleting the ledger."""
    if source.get("source_kind") != "primary_publication":
        return source_key(source)
    explicit = str(source.get("lineage_id") or "").strip()
    if explicit:
        return f"lineage:{explicit.lower()}"
    title = re.sub(r"\W+", "", str(source.get("title") or "").lower())
    return f"title:{title}" if len(title) >= 24 else source_key(source)


def deduplicate_selection_lineages(sources: Iterable[dict]) -> list[dict]:
    """Keep one selection candidate per lineage, preferring version of record.

    The complete ledger is intentionally not changed; excluded lineage members
    remain auditable and cannot be counted as independent replication.
    """
    winners: dict[str, dict] = {}
    for source in sources:
        key = selection_lineage_key(source)
        current = winners.get(key)
        score = (
            1 if source.get("is_version_of_record") else 0,
            1 if not source.get("derived_publication") else 0,
            bool(source.get("metadata_complete")),
            str(source.get("publication_date") or ""),
            source_key(source),
        )
        if current is None:
            winners[key] = source
            continue
        current_score = (
            1 if current.get("is_version_of_record") else 0,
            1 if not current.get("derived_publication") else 0,
            bool(current.get("metadata_complete")),
            str(current.get("publication_date") or ""),
            source_key(current),
        )
        if score > current_score:
            winners[key] = source
    return list(winners.values())


def select_sources(
    source_ledger: list[dict], *, required_evidence_ids: Iterable[str] | None = None,
    excluded_evidence_ids: Iterable[str] | None = None,
) -> tuple[list[str], dict]:
    """Select a deterministic ≤20-source window without discarding the ledger.

    A reviewed gold packet may pin previously adjudicated evidence into the
    model window and exclude sources that a human already found inapplicable.
    The pins never bypass cutoff, review-only, eligibility or lineage rules.
    """
    required = set(required_evidence_ids or [])
    excluded = set(excluded_evidence_ids or [])
    overlap = required & excluded
    if overlap:
        raise ValueError(f"Evidence cannot be both required and excluded: {sorted(overlap)}")
    by_id = {source.get("evidence_id"): source for source in source_ledger}
    missing = required - set(by_id)
    if missing:
        raise ValueError(f"Required evidence is absent from the ledger: {sorted(missing)}")
    canonical = sorted(
        [
            source for source in source_ledger
            if source.get("source_kind") == "authoritative_database"
            and source.get("evidence_id") not in excluded
        ],
        key=_selection_score, reverse=True,
    )[:3]
    eligible = [
        source for source in source_ledger
        if source.get("source_kind") == "primary_publication"
        and source.get("eligibility_status", "eligible") == "eligible"
        and not source.get("review_discovery_only")
        and source.get("evidence_id") not in excluded
        and (parse_date(source.get("publication_date")) is None or parse_date(source.get("publication_date")) <= CUTOFF)
    ]
    eligible = deduplicate_selection_lineages(eligible)
    eligible_ids = {source.get("evidence_id") for source in eligible}
    selectable_ids = eligible_ids | {source.get("evidence_id") for source in canonical}
    unselectable = required - selectable_ids
    if unselectable:
        raise ValueError(f"Required evidence is not selectable: {sorted(unselectable)}")

    required_rows = deduplicate_selection_lineages([by_id[evidence_id] for evidence_id in required])
    required_after_lineage = {source.get("evidence_id") for source in required_rows}
    lost_to_lineage = required - required_after_lineage
    if lost_to_lineage:
        raise ValueError(f"Required evidence duplicates a selected publication lineage: {sorted(lost_to_lineage)}")
    if len(required_rows) > 20:
        raise ValueError(f"Required evidence exceeds the 20-source window: {len(required_rows)}")
    functional = sorted(
        [source for source in eligible if "functional_study" in set(source.get("role_candidates") or [])],
        key=_selection_score, reverse=True,
    )[:8]
    human = sorted(
        [source for source in eligible if "human_study" in set(source.get("role_candidates") or [])],
        key=_selection_score, reverse=True,
    )[:8]
    conflicts = sorted(
        [source for source in eligible if "conflict_or_null" in set(source.get("role_candidates") or [])],
        key=_selection_score, reverse=True,
    )[:4]
    chosen: list[dict] = sorted(required_rows, key=_selection_score, reverse=True)
    seen: set[str] = {source["evidence_id"] for source in chosen}
    for source in canonical + functional + human + conflicts:
        if len(chosen) >= 20:
            break
        if source["evidence_id"] not in seen:
            chosen.append(source)
            seen.add(source["evidence_id"])
    # Fill unused capacity with remaining high-value primary evidence.
    for source in sorted(eligible, key=_selection_score, reverse=True):
        if len(chosen) >= 20:
            break
        if source["evidence_id"] not in seen:
            chosen.append(source)
            seen.add(source["evidence_id"])
    role_count = lambda role: sum(role in set(source.get("role_candidates") or []) for source in chosen)  # noqa: E731
    summary = {
        "ledger_total": len(source_ledger),
        "selected_total": len(chosen),
        "functional_selected": min(role_count("functional_study"), 8),
        "human_selected": min(role_count("human_study"), 8),
        "conflict_or_null_selected": min(role_count("conflict_or_null"), 4),
        "overflow_total": max(0, len(eligible) + len(canonical) - len(chosen)),
        "review_required_selected": len(required),
        "review_excluded_total": len(excluded),
    }
    return [source["evidence_id"] for source in chosen], summary


def weighted_quality(quality: dict) -> float:
    return round(sum(float(quality.get(name, 0)) * weight for name, weight in WEIGHTS.items()), 4)


def execution_evidence_allowlist(packet: dict) -> dict:
    """Resolve the immutable evidence window allowed to influence a model decision.

    Human-reviewed Gold packets use only the signed-valid IDs. Other Tier 1 packets
    use the deterministic selected window and retain distinct provenance so that an
    automated selection is never represented as a human scientific signature.
    """
    selected = set(packet.get("selected_evidence_ids") or [])
    policy = packet.get("human_review_policy") or {}
    signed = set(policy.get("valid_evidence_ids") or [])
    invalid = set(policy.get("invalid_evidence_ids") or [])
    if signed:
        allowed = signed
        provenance = "signed_gold"
    else:
        allowed = selected - invalid
        provenance = "packet_selected"
    return {
        "allowed_ids": sorted(allowed),
        "invalid_ids": sorted(invalid),
        "selected_ids": sorted(selected),
        "outside_allowlist_ids": sorted(selected - allowed),
        "provenance": provenance,
    }


def compute_direction(source_assessments: list[dict]) -> dict:
    support, opposition = [], []
    for assessment in source_assessments:
        direction = assessment.get("supports_direction")
        score = weighted_quality(assessment.get("quality") or {})
        if direction == "positive":
            support.append(score)
        elif direction == "negative":
            opposition.append(score)
        elif direction == "mixed":
            support.append(score * 0.5)
            opposition.append(score * 0.5)
    support_score = sum(support) / len(support) if support else 0.0
    opposition_score = sum(opposition) / len(opposition) if opposition else 0.0
    margin = abs(support_score - opposition_score)
    material_conflict = bool(support and opposition)
    if not support and not opposition:
        dominant = "not_applicable"
    elif material_conflict and margin < 15:
        dominant = "no_dominant_direction"
    elif support_score >= opposition_score:
        dominant = "supports_relation"
    else:
        dominant = "opposes_relation"
    return {
        "dominant_direction": dominant,
        "support_score": round(support_score, 2),
        "opposition_score": round(opposition_score, 2),
        "margin": round(margin, 2),
        "material_conflict": material_conflict,
    }


def _validate_v2_conflict_fields(row: dict, errors: list[str]) -> None:
    evidence_id = row.get("evidence_id")
    classification = row.get("core_conflict_class")
    rationale = str(row.get("core_conflict_rationale") or "").strip()
    direction = row.get("supports_direction")
    if classification not in CORE_CONFLICT_CLASSES:
        errors.append(f"semantic_core_conflict_class_invalid:{evidence_id}")
        return
    if classification == "direct_material_contradiction":
        if direction not in {"negative", "mixed"}:
            errors.append(f"direct_material_conflict_requires_negative_or_mixed:{evidence_id}")
        if not rationale:
            errors.append(f"direct_material_conflict_rationale_missing:{evidence_id}")
    elif classification != "none" and not rationale:
        errors.append(f"contextual_conflict_rationale_missing:{evidence_id}")


def validate_semantic_assessment_v2(assessment: dict, packet: dict) -> list[str]:
    """Validate Semantic V2, including strict per-role execution allowlists."""
    errors = validate_packet(packet)
    if assessment.get("schema_version") != "mechanism_curation_semantic_v2":
        errors.append("semantic_schema_version_invalid")
    if assessment.get("core_status") not in CORE_STATUSES:
        errors.append("invalid_core_status")
    if not isinstance(assessment.get("identity_exact"), bool):
        errors.append("identity_exact_not_boolean")
    if not isinstance(assessment.get("module_relation_direct"), bool):
        errors.append("module_relation_direct_not_boolean")
    if not assessment.get("limitations") or not all(str(value).strip() for value in assessment.get("limitations") or []):
        errors.append("limitations_missing_or_empty")
    confidence = assessment.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        errors.append("confidence_invalid")

    reason_codes = assessment.get("scientific_reason_codes") or []
    if len(reason_codes) != len(set(reason_codes)):
        errors.append("duplicate_scientific_reason_code")
    if set(reason_codes) - SEMANTIC_REASON_CODES:
        errors.append("invalid_scientific_reason_code")

    used = assessment.get("used_evidence") or []
    excluded = assessment.get("excluded_evidence") or []
    used_ids = [str(row.get("evidence_id") or "") for row in used]
    excluded_ids = [str(row.get("evidence_id") or "") for row in excluded]
    resolved = execution_evidence_allowlist(packet)
    selected = set(resolved["selected_ids"])
    allowed = set(resolved["allowed_ids"])
    invalid = set(resolved["invalid_ids"])
    outside = set(resolved["outside_allowlist_ids"])
    if len(used_ids) != len(set(used_ids)):
        errors.append("duplicate_used_evidence_id")
    if len(excluded_ids) != len(set(excluded_ids)):
        errors.append("duplicate_excluded_evidence_id")
    if set(used_ids) & set(excluded_ids):
        errors.append("evidence_used_and_excluded")
    if set(used_ids) | set(excluded_ids) != selected:
        errors.append("semantic_evidence_coverage_mismatch")
    if set(used_ids) - selected or set(excluded_ids) - selected:
        errors.append("semantic_evidence_not_in_selected_window")
    if set(used_ids) - allowed:
        errors.append("semantic_used_evidence_outside_execution_allowlist")
    if set(used_ids) & invalid:
        errors.append("semantic_invalid_evidence_used")

    excluded_by_id = {str(row.get("evidence_id") or ""): row for row in excluded}
    for evidence_id in outside:
        row = excluded_by_id.get(evidence_id)
        if row is None or row.get("reason_code") != "outside_valid_evidence_allowlist":
            errors.append(f"outside_allowlist_not_explicitly_excluded:{evidence_id}")

    ledger = {row.get("evidence_id"): row for row in packet.get("source_ledger") or []}
    for row in used:
        roles = row.get("roles") or []
        if not roles or set(roles) - SEMANTIC_ROLES:
            errors.append(f"semantic_roles_invalid:{row.get('evidence_id')}")
        if len(roles) != len(set(roles)):
            errors.append(f"duplicate_semantic_role:{row.get('evidence_id')}")
        if row.get("supports_direction") not in SEMANTIC_DIRECTIONS:
            errors.append(f"semantic_direction_invalid:{row.get('evidence_id')}")
        _validate_v2_conflict_fields(row, errors)
        for field in ("human_applicability", "functional_compatibility"):
            if not isinstance(row.get(field), bool):
                errors.append(f"semantic_{field}_not_boolean:{row.get('evidence_id')}")
        quality = row.get("quality") or {}
        if set(quality) != set(WEIGHTS):
            errors.append(f"semantic_quality_incomplete:{row.get('evidence_id')}")
        for field in WEIGHTS:
            value = quality.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                errors.append(f"semantic_quality_invalid:{row.get('evidence_id')}:{field}")
        source = ledger.get(row.get("evidence_id"), {})
        if source.get("source_kind") == "review" or source.get("review_discovery_only"):
            errors.append(f"review_used_as_evidence:{row.get('evidence_id')}")
    for row in excluded:
        if row.get("reason_code") not in SEMANTIC_EXCLUSION_REASONS:
            errors.append(f"semantic_exclusion_reason_invalid:{row.get('evidence_id')}")

    approved = assessment.get("core_status") in {"approved", "approved_with_conflict"}
    if approved and not (assessment.get("identity_exact") and assessment.get("module_relation_direct")):
        errors.append("approved_without_exact_identity_and_direct_module_relation")
    if assessment.get("core_status") == "rejected" and not (set(reason_codes) & POSITIVE_REJECTION_REASONS):
        errors.append("rejected_without_positive_evidence")
    return sorted(set(errors))


def normalize_semantic_assessment_v2(assessment: dict, packet: dict) -> dict:
    """Normalize V2 semantics into the unchanged public decision contract."""
    source_assessments: list[dict] = []
    evidence_ids = {role: [] for role in ("functional", "human", "canonical", "conflict_or_null")}
    direct_conflict_ids: set[str] = set()
    ledger = {source.get("evidence_id"): source for source in packet.get("source_ledger") or []}
    for row in assessment.get("used_evidence") or []:
        roles = list(row.get("roles") or [])
        source_direction = row.get("supports_direction")
        conflict_class = row.get("core_conflict_class")
        if conflict_class == "direct_material_contradiction":
            normalized_direction = source_direction
            direct_conflict_ids.add(row.get("evidence_id"))
            if "conflict_or_null" not in roles:
                roles.append("conflict_or_null")
        elif conflict_class in {"contextual_heterogeneity", "downstream_null", "limited_generalizability"}:
            # These classes describe a limitation or a non-core endpoint.  They
            # must never create opposition to the central mechanism, but a
            # source that independently supports that mechanism still counts
            # as support.  A source-level positive direction can therefore
            # coexist with (for example) a downstream null endpoint.
            normalized_direction = "positive" if source_direction == "positive" else "not_applicable"
            if "conflict_or_null" not in roles:
                roles.append("conflict_or_null")
        else:
            normalized_direction = source_direction
            if normalized_direction in {"negative", "mixed", "null"} and "conflict_or_null" not in roles:
                roles.append("conflict_or_null")
        for role in roles:
            if row.get("evidence_id") not in evidence_ids[role]:
                evidence_ids[role].append(row.get("evidence_id"))
        source = ledger.get(row.get("evidence_id"), {})
        is_primary = (
            source.get("source_kind") == "primary_publication"
            and not source.get("review_discovery_only")
        )
        # These booleans are the normalized eligibility signals consumed by
        # evidence_facts.  They intentionally do not repeat the broader
        # scientific-context judgment emitted by Sol: only direct, positive,
        # primary evidence about the central mechanism may raise the ceiling.
        role_candidates = set(source.get("role_candidates") or [])
        direct_individual_relation = bool(
            "variant_specific" in role_candidates or conflict_class == "none"
        )
        directly_applicable_human = bool(
            is_primary
            and "human" in roles
            and row.get("human_applicability")
            and source_direction == "positive"
            and conflict_class not in {"downstream_null"}
            and direct_individual_relation
        )
        human_compatible_functional = bool(
            is_primary
            and "functional" in roles
            and row.get("functional_compatibility")
            and (row.get("human_applicability") or "human" in roles)
            and source_direction == "positive"
            and conflict_class not in {"downstream_null"}
        )
        source_assessments.append({
            "evidence_id": row.get("evidence_id"), "roles": roles,
            "supports_direction": normalized_direction,
            "human_applicability": directly_applicable_human,
            "functional_compatibility": human_compatible_functional,
            "quality": {name: int((row.get("quality") or {}).get(name, 0)) for name in WEIGHTS},
        })
    for values in evidence_ids.values():
        values.sort()
    source_assessments.sort(key=lambda row: row["evidence_id"])
    direction = compute_direction(source_assessments)
    # Only direct contradictions can make the deterministic conflict flag true.
    direction["material_conflict"] = bool(direct_conflict_ids and direction["support_score"] > 0 and direction["opposition_score"] > 0)
    if not direction["material_conflict"] and direction["opposition_score"] == 0 and direction["support_score"] > 0:
        direction["dominant_direction"] = "supports_relation"
        direction["margin"] = direction["support_score"]

    approved = assessment.get("core_status") in {"approved", "approved_with_conflict"}
    provisional = {"evidence_ids": evidence_ids, "source_assessments": source_assessments}
    facts = evidence_facts(provisional, packet)
    base_a = bool(facts["canonical"] and facts["primaries"])
    base_b = facts["independent_primary_count"] >= 2 and bool(facts["functional"])
    if not approved:
        ceiling = "none"
    elif facts["directly_applicable_human"] and facts["compatible_functional"]:
        ceiling = "initial_guide_candidate"
    else:
        ceiling = "context_only"
    reason_codes = set(assessment.get("scientific_reason_codes") or [])
    if approved and base_a:
        reason_codes.add("authoritative_plus_primary")
    if approved and base_b:
        reason_codes.add("two_independent_primaries_one_functional")
    if ceiling == "initial_guide_candidate":
        reason_codes.add("human_and_functional_applicable")
    elif approved:
        reason_codes.add("mechanism_context_without_direct_individual_support")
    if not facts["primaries"]:
        reason_codes.add("insufficient_primary_evidence")
    if facts["canonical"] and not facts["primaries"]:
        reason_codes.add("only_authoritative_source")
    if facts["functional"] and not facts["human"] and not facts["canonical"]:
        reason_codes.add("only_functional_evidence")
    if facts["human"] and not facts["functional"] and not facts["canonical"]:
        reason_codes.add("only_human_evidence")
    if not assessment.get("identity_exact"):
        reason_codes.add("identity_unresolved")
    if not assessment.get("module_relation_direct"):
        reason_codes.add("module_relation_indirect")
    if direction["material_conflict"] and direction["margin"] < 15:
        reason_codes.add("conflict_margin_below_15")
    strong_evidence = bool(approved and facts["primaries"] and (facts["canonical"] or facts["independent_primary_count"] >= 2))
    return {
        "schema_version": "mechanism_curation_v2_decision", "group_id": packet.get("group_id"),
        "core_status": assessment.get("core_status"), "context_usable": approved,
        "inference_ceiling": ceiling, "identity_exact": bool(assessment.get("identity_exact")),
        "module_relation_direct": bool(assessment.get("module_relation_direct")),
        "expert_review_basis": {
            "strong_evidence": strong_evidence,
            "material_scientific_conflict": direction["material_conflict"],
            "conflict_evidence_ids": sorted(direct_conflict_ids),
            "runtime_variant_context_required": True,
        },
        "evidence_ids": evidence_ids, "source_assessments": source_assessments,
        "direction_assessment": direction, "decision_reason_codes": sorted(reason_codes),
        "limitations": list(assessment.get("limitations") or []),
        "confidence": float(assessment.get("confidence", 0)),
    }


def validate_packet(packet: dict) -> list[str]:
    errors: list[str] = []
    ledger = packet.get("source_ledger") or []
    ids = [str(source.get("evidence_id") or "") for source in ledger]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_evidence_id")
    selected = packet.get("selected_evidence_ids") or []
    if len(selected) > 20:
        errors.append("selected_evidence_exceeds_20")
    if set(selected) - set(ids):
        errors.append("selected_evidence_not_in_ledger")
    review_policy = packet.get("human_review_policy") or {}
    required_reviewed = set(review_policy.get("valid_evidence_ids") or [])
    excluded_reviewed = set(review_policy.get("invalid_evidence_ids") or [])
    if required_reviewed - set(ids):
        errors.append("review_valid_evidence_not_in_ledger")
    if required_reviewed - set(selected):
        errors.append("review_valid_evidence_not_selected")
    if excluded_reviewed & set(selected):
        errors.append("review_invalid_evidence_selected")
    for source in ledger:
        published = parse_date(source.get("publication_date"))
        if source.get("source_kind") in {"primary_publication", "review"} and published and published > CUTOFF and source.get("eligibility_status") != "excluded":
            errors.append(f"publication_after_cutoff:{source.get('evidence_id')}")
        if source.get("source_kind") == "review" and not source.get("review_discovery_only"):
            errors.append(f"review_not_discovery_only:{source.get('evidence_id')}")
        if source.get("eligibility_status") == "excluded" and source.get("evidence_id") in selected:
            errors.append(f"excluded_source_selected:{source.get('evidence_id')}")
        if source.get("evidence_id") in required_reviewed:
            if source.get("source_kind") == "review" or source.get("review_discovery_only"):
                errors.append(f"review_valid_evidence_is_discovery_only:{source.get('evidence_id')}")
            if published and published > CUTOFF:
                errors.append(f"review_valid_evidence_after_cutoff:{source.get('evidence_id')}")
            if source.get("eligibility_status") != "eligible":
                errors.append(f"review_valid_evidence_not_eligible:{source.get('evidence_id')}")
    metadata_snapshot = packet.get("publication_metadata_snapshot")
    if metadata_snapshot:
        if metadata_snapshot.get("database") != "PubMed" or metadata_snapshot.get("consistency_status") != "consistent":
            errors.append("publication_metadata_snapshot_invalid")
        for source in ledger:
            pmid = str(source.get("pmid") or "").strip()
            expected_status = "consistent" if pmid else "not_applicable"
            if source.get("metadata_consistency_status") != expected_status:
                errors.append(f"metadata_consistency_status_invalid:{source.get('evidence_id')}")
            if pmid:
                provenance = source.get("metadata_provenance") or {}
                record_hash = source.get("authoritative_record_sha256")
                if provenance.get("database") != "PubMed" or not record_hash:
                    errors.append(f"pubmed_provenance_missing:{source.get('evidence_id')}")
                if record_hash != sha256_json({
                    "pmid": pmid,
                    "title": normalize_title(source.get("title")),
                    "doi": normalize_doi(source.get("doi")) or None,
                }):
                    errors.append(f"authoritative_record_hash_mismatch:{source.get('evidence_id')}")
        errors.extend(publication_metadata_errors(ledger))
    packet_copy = dict(packet)
    declared_hash = packet_copy.pop("packet_sha256", "")
    if declared_hash and sha256_json(packet_copy) != declared_hash:
        errors.append("packet_hash_mismatch")
    return sorted(set(errors))


def evidence_facts(decision: dict, packet: dict) -> dict:
    ledger = {source["evidence_id"]: source for source in packet.get("source_ledger") or []}
    ids_by_role = decision.get("evidence_ids") or {}
    all_ids = [evidence_id for values in ids_by_role.values() for evidence_id in values]
    primaries = {
        evidence_id for evidence_id in all_ids
        if ledger.get(evidence_id, {}).get("source_kind") == "primary_publication"
        and not ledger.get(evidence_id, {}).get("review_discovery_only")
    }
    canonical = {
        evidence_id for evidence_id in ids_by_role.get("canonical", [])
        if ledger.get(evidence_id, {}).get("source_kind") == "authoritative_database"
    }
    functional = set(ids_by_role.get("functional") or []) & primaries
    human = set(ids_by_role.get("human") or []) & primaries
    cohorts = {
        ledger[evidence_id].get("cohort_key") or evidence_id
        for evidence_id in primaries
        if not ledger[evidence_id].get("derived_publication")
    }
    directly_applicable_human = any(
        assessment.get("evidence_id") in human
        and assessment.get("human_applicability")
        and assessment.get("supports_direction") == "positive"
        for assessment in decision.get("source_assessments") or []
    )
    compatible_functional = any(
        assessment.get("evidence_id") in functional
        and assessment.get("functional_compatibility")
        and assessment.get("supports_direction") == "positive"
        for assessment in decision.get("source_assessments") or []
    )
    return {
        "primaries": primaries, "canonical": canonical, "functional": functional, "human": human,
        "independent_primary_count": len(cohorts),
        "directly_applicable_human": directly_applicable_human,
        "compatible_functional": compatible_functional,
    }


def validate_semantic_assessment(assessment: dict, packet: dict) -> list[str]:
    """Validate only scientific inputs emitted by Sol, before deterministic derivation."""
    errors = validate_packet(packet)
    if assessment.get("schema_version") != "mechanism_curation_semantic_v1":
        errors.append("semantic_schema_version_invalid")
    if assessment.get("core_status") not in CORE_STATUSES:
        errors.append("invalid_core_status")
    if not isinstance(assessment.get("identity_exact"), bool):
        errors.append("identity_exact_not_boolean")
    if not isinstance(assessment.get("module_relation_direct"), bool):
        errors.append("module_relation_direct_not_boolean")
    if not assessment.get("limitations") or not all(str(value).strip() for value in assessment.get("limitations") or []):
        errors.append("limitations_missing_or_empty")
    confidence = assessment.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        errors.append("confidence_invalid")

    reason_codes = assessment.get("scientific_reason_codes") or []
    if len(reason_codes) != len(set(reason_codes)):
        errors.append("duplicate_scientific_reason_code")
    if set(reason_codes) - SEMANTIC_REASON_CODES:
        errors.append("invalid_scientific_reason_code")

    used = assessment.get("used_evidence") or []
    excluded = assessment.get("excluded_evidence") or []
    used_ids = [str(row.get("evidence_id") or "") for row in used]
    excluded_ids = [str(row.get("evidence_id") or "") for row in excluded]
    selected = set(packet.get("selected_evidence_ids") or [])
    if len(used_ids) != len(set(used_ids)):
        errors.append("duplicate_used_evidence_id")
    if len(excluded_ids) != len(set(excluded_ids)):
        errors.append("duplicate_excluded_evidence_id")
    if set(used_ids) & set(excluded_ids):
        errors.append("evidence_used_and_excluded")
    if set(used_ids) | set(excluded_ids) != selected:
        errors.append("semantic_evidence_coverage_mismatch")
    if set(used_ids) - selected or set(excluded_ids) - selected:
        errors.append("semantic_evidence_not_in_selected_window")

    ledger = {row.get("evidence_id"): row for row in packet.get("source_ledger") or []}
    for row in used:
        roles = row.get("roles") or []
        if not roles or set(roles) - SEMANTIC_ROLES:
            errors.append(f"semantic_roles_invalid:{row.get('evidence_id')}")
        if len(roles) != len(set(roles)):
            errors.append(f"duplicate_semantic_role:{row.get('evidence_id')}")
        if row.get("supports_direction") not in SEMANTIC_DIRECTIONS:
            errors.append(f"semantic_direction_invalid:{row.get('evidence_id')}")
        for field in ("human_applicability", "functional_compatibility"):
            if not isinstance(row.get(field), bool):
                errors.append(f"semantic_{field}_not_boolean:{row.get('evidence_id')}")
        quality = row.get("quality") or {}
        if set(quality) != set(WEIGHTS):
            errors.append(f"semantic_quality_incomplete:{row.get('evidence_id')}")
        for field in WEIGHTS:
            value = quality.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                errors.append(f"semantic_quality_invalid:{row.get('evidence_id')}:{field}")
        source = ledger.get(row.get("evidence_id"), {})
        if source.get("source_kind") == "review" or source.get("review_discovery_only"):
            errors.append(f"review_used_as_evidence:{row.get('evidence_id')}")

    for row in excluded:
        if row.get("reason_code") not in SEMANTIC_EXCLUSION_REASONS:
            errors.append(f"semantic_exclusion_reason_invalid:{row.get('evidence_id')}")

    approved = assessment.get("core_status") in {"approved", "approved_with_conflict"}
    if approved and not (assessment.get("identity_exact") and assessment.get("module_relation_direct")):
        errors.append("approved_without_exact_identity_and_direct_module_relation")
    if assessment.get("core_status") == "rejected" and not (set(reason_codes) & POSITIVE_REJECTION_REASONS):
        errors.append("rejected_without_positive_evidence")
    return sorted(set(errors))


def normalize_semantic_assessment(assessment: dict, packet: dict) -> dict:
    """Build the stable v2 decision while deriving every mathematical/policy field once."""
    source_assessments: list[dict] = []
    evidence_ids = {role: [] for role in ("functional", "human", "canonical", "conflict_or_null")}
    for row in assessment.get("used_evidence") or []:
        roles = list(row.get("roles") or [])
        direction = row.get("supports_direction")
        if direction in {"negative", "mixed", "null"} and "conflict_or_null" not in roles:
            roles.append("conflict_or_null")
        for role in roles:
            if row.get("evidence_id") not in evidence_ids[role]:
                evidence_ids[role].append(row.get("evidence_id"))
        source_assessments.append({
            "evidence_id": row.get("evidence_id"),
            "roles": roles,
            "supports_direction": direction,
            "human_applicability": bool(row.get("human_applicability")),
            "functional_compatibility": bool(row.get("functional_compatibility")),
            "quality": {name: int((row.get("quality") or {}).get(name, 0)) for name in WEIGHTS},
        })
    for values in evidence_ids.values():
        values.sort()
    source_assessments.sort(key=lambda row: row["evidence_id"])
    direction = compute_direction(source_assessments)
    approved = assessment.get("core_status") in {"approved", "approved_with_conflict"}

    provisional = {
        "evidence_ids": evidence_ids,
        "source_assessments": source_assessments,
    }
    facts = evidence_facts(provisional, packet)
    base_a = bool(facts["canonical"] and facts["primaries"])
    base_b = facts["independent_primary_count"] >= 2 and bool(facts["functional"])
    context_usable = approved
    if not approved:
        ceiling = "none"
    elif facts["directly_applicable_human"] and facts["compatible_functional"]:
        ceiling = "initial_guide_candidate"
    else:
        ceiling = "context_only"

    reason_codes = set(assessment.get("scientific_reason_codes") or [])
    if approved and base_a:
        reason_codes.add("authoritative_plus_primary")
    if approved and base_b:
        reason_codes.add("two_independent_primaries_one_functional")
    if ceiling == "initial_guide_candidate":
        reason_codes.add("human_and_functional_applicable")
    if not facts["primaries"]:
        reason_codes.add("insufficient_primary_evidence")
    if facts["canonical"] and not facts["primaries"]:
        reason_codes.add("only_authoritative_source")
    if facts["functional"] and not facts["human"] and not facts["canonical"]:
        reason_codes.add("only_functional_evidence")
    if facts["human"] and not facts["functional"] and not facts["canonical"]:
        reason_codes.add("only_human_evidence")
    if not assessment.get("identity_exact"):
        reason_codes.add("identity_unresolved")
    if not assessment.get("module_relation_direct"):
        reason_codes.add("module_relation_indirect")
    if direction["material_conflict"] and direction["margin"] < 15:
        reason_codes.add("conflict_margin_below_15")

    conflict_ids = sorted({
        row["evidence_id"] for row in source_assessments
        if row["supports_direction"] in {"negative", "mixed"}
    })
    strong_evidence = bool(approved and facts["primaries"] and (facts["canonical"] or facts["independent_primary_count"] >= 2))
    return {
        "schema_version": "mechanism_curation_v2_decision",
        "group_id": packet.get("group_id"),
        "core_status": assessment.get("core_status"),
        "context_usable": context_usable,
        "inference_ceiling": ceiling,
        "identity_exact": bool(assessment.get("identity_exact")),
        "module_relation_direct": bool(assessment.get("module_relation_direct")),
        "expert_review_basis": {
            "strong_evidence": strong_evidence,
            "material_scientific_conflict": direction["material_conflict"],
            "conflict_evidence_ids": conflict_ids,
            "runtime_variant_context_required": True,
        },
        "evidence_ids": evidence_ids,
        "source_assessments": source_assessments,
        "direction_assessment": direction,
        "decision_reason_codes": sorted(reason_codes),
        "limitations": list(assessment.get("limitations") or []),
        "confidence": float(assessment.get("confidence", 0)),
    }


def legacy_decision_to_semantic(decision: dict, packet: dict) -> dict:
    """Adapt immutable run5/run6 outputs for local regression only, never as a new evaluation."""
    role_by_id: dict[str, set[str]] = defaultdict(set)
    for role, ids in (decision.get("evidence_ids") or {}).items():
        for evidence_id in ids or []:
            role_by_id[evidence_id].add(role)
    used_by_id = {row.get("evidence_id"): row for row in decision.get("source_assessments") or []}
    used_ids = set(role_by_id) | set(used_by_id)
    used_evidence = []
    for evidence_id in sorted(used_ids):
        row = used_by_id.get(evidence_id) or {}
        roles = sorted(set(row.get("roles") or []) | role_by_id.get(evidence_id, set()))
        if not roles:
            roles = ["canonical"]
        quality = row.get("quality") or {}
        used_evidence.append({
            "evidence_id": evidence_id,
            "roles": roles,
            "supports_direction": row.get("supports_direction", "not_applicable"),
            "human_applicability": bool(row.get("human_applicability")),
            "functional_compatibility": bool(row.get("functional_compatibility")),
            "quality": {name: max(0, min(100, int(round(float(quality.get(name, 0)))))) for name in WEIGHTS},
        })
    selected = set(packet.get("selected_evidence_ids") or [])
    excluded_evidence = [
        {"evidence_id": evidence_id, "reason_code": "not_used_in_final_assessment"}
        for evidence_id in sorted(selected - used_ids)
    ]
    semantic_reasons = sorted(set(decision.get("decision_reason_codes") or []) & SEMANTIC_REASON_CODES)
    return {
        "schema_version": "mechanism_curation_semantic_v1",
        "core_status": decision.get("core_status"),
        "identity_exact": bool(decision.get("identity_exact")),
        "module_relation_direct": bool(decision.get("module_relation_direct")),
        "used_evidence": used_evidence,
        "excluded_evidence": excluded_evidence,
        "scientific_reason_codes": semantic_reasons,
        "limitations": list(decision.get("limitations") or []),
        "confidence": float(decision.get("confidence", 0)),
    }


def normalized_pair_agreement(first: dict, second: dict) -> bool:
    fields = ("core_status", "context_usable", "inference_ceiling", "identity_exact", "module_relation_direct")
    if any(first.get(field) != second.get(field) for field in fields):
        return False
    first_direction = first.get("direction_assessment") or {}
    second_direction = second.get("direction_assessment") or {}
    return all(first_direction.get(field) == second_direction.get(field) for field in ("dominant_direction", "material_conflict"))


def semantic_arbitration_reasons(curator_semantic: dict, critic_semantic: dict,
                                 curator_decision: dict, critic_decision: dict) -> list[str]:
    reasons: list[str] = []
    comparisons = {
        "core_status": (curator_decision.get("core_status"), critic_decision.get("core_status")),
        "identity_exact": (curator_decision.get("identity_exact"), critic_decision.get("identity_exact")),
        "module_relation_direct": (curator_decision.get("module_relation_direct"), critic_decision.get("module_relation_direct")),
        "inference_ceiling": (curator_decision.get("inference_ceiling"), critic_decision.get("inference_ceiling")),
        "dominant_direction": (
            (curator_decision.get("direction_assessment") or {}).get("dominant_direction"),
            (critic_decision.get("direction_assessment") or {}).get("dominant_direction"),
        ),
        "material_conflict": (
            (curator_decision.get("direction_assessment") or {}).get("material_conflict"),
            (critic_decision.get("direction_assessment") or {}).get("material_conflict"),
        ),
    }
    for field, values in comparisons.items():
        if values[0] != values[1]:
            reasons.append(f"scientific_disagreement:{field}")
    if float(curator_semantic.get("confidence", 0)) < 0.8 or float(critic_semantic.get("confidence", 0)) < 0.8:
        reasons.append("confidence_below_0_80")
    return sorted(set(reasons))


def structural_error_family(error: str) -> str:
    """Return a stable error family without leaking group, role, PMID or evidence IDs.

    Error strings are frequently prefixed with ``group:role`` and may append a
    concrete identifier.  Structural-stop accounting must group by the validator
    code, not by those variable identifiers.
    """
    parts = [part.strip() for part in str(error).split(":") if part.strip()]
    if not parts:
        return "unknown_error"
    if parts[0].startswith("attempt_"):
        return parts[1] if len(parts) > 1 else parts[0]
    known_prefixes = {"technical", "structural", "deterministic", "scientific_disagreement"}
    if parts[0] in known_prefixes:
        return parts[1] if len(parts) > 1 else parts[0]
    deterministic_part = next(
        (part for part in parts if part.endswith("_mismatch") or part.startswith("deterministic_")),
        "",
    )
    if deterministic_part:
        return deterministic_part
    # Group IDs are of the form GENE:T1.n and are normally followed by role/code.
    if len(parts) >= 4 and parts[1].startswith("T") and parts[2] in {"curator", "critic", "arbiter"}:
        return parts[3]
    if len(parts) >= 3 and parts[1] in {"curator", "critic", "arbiter"}:
        return parts[2]
    # Validator errors often append PMID/evidence IDs. Their first segment is the
    # stable code (for example used_evidence_outside_execution_allowlist:PMID:1).
    return parts[0]


def structural_stop_reason(errors: list[str]) -> str:
    """Stop prompt iteration when a concentrated or deterministic failure needs architecture work."""
    codes = [structural_error_family(error) for error in errors]
    deterministic = sorted({code for code in codes if code.endswith("_mismatch") or code.startswith("deterministic_")})
    if deterministic:
        return f"deterministic_failure:{deterministic[0]}"
    if len(codes) >= 4:
        counts = Counter(codes)
        code, count = counts.most_common(1)[0]
        if count / len(codes) >= 0.80:
            return f"dominant_failure_80_percent:{code}:{count}/{len(codes)}"
    return ""


def validate_decision(decision: dict, packet: dict) -> list[str]:
    errors = validate_packet(packet)
    packet_ids = {source["evidence_id"] for source in packet.get("source_ledger") or []}
    selected = set(packet.get("selected_evidence_ids") or [])
    if decision.get("group_id") != packet.get("group_id"):
        errors.append("group_id_mismatch")
    if decision.get("core_status") not in CORE_STATUSES:
        errors.append("invalid_core_status")
    if decision.get("inference_ceiling") not in INFERENCE_CEILINGS:
        errors.append("invalid_inference_ceiling")
    if not decision.get("limitations") or not all(str(value).strip() for value in decision.get("limitations") or []):
        errors.append("limitations_missing_or_empty")
    ids_by_role = decision.get("evidence_ids") or {}
    if any(len(values or []) != len(set(values or [])) for values in ids_by_role.values()):
        errors.append("duplicate_evidence_id_within_role")
    assessments = decision.get("source_assessments") or []
    assessment_ids = [row.get("evidence_id") for row in assessments]
    if len(assessment_ids) != len(set(assessment_ids)):
        errors.append("duplicate_source_assessment")
    if any(len(row.get("roles") or []) != len(set(row.get("roles") or [])) for row in assessments):
        errors.append("duplicate_assessment_role")
    reason_codes = decision.get("decision_reason_codes") or []
    if len(reason_codes) != len(set(reason_codes)):
        errors.append("duplicate_decision_reason_code")
    conflict_ids_list = (decision.get("expert_review_basis") or {}).get("conflict_evidence_ids") or []
    if len(conflict_ids_list) != len(set(conflict_ids_list)):
        errors.append("duplicate_expert_conflict_evidence_id")
    cited = {
        evidence_id
        for values in (decision.get("evidence_ids") or {}).values()
        for evidence_id in values
    }
    if cited - packet_ids:
        errors.append("citation_not_in_packet")
    if cited - selected:
        errors.append("citation_not_in_selected_window")
    execution_allowlist = set(execution_evidence_allowlist(packet)["allowed_ids"])
    if cited - execution_allowlist:
        errors.append("citation_not_in_execution_allowlist")
    assessed = {row.get("evidence_id") for row in decision.get("source_assessments") or []}
    if assessed - selected:
        errors.append("assessment_not_in_selected_window")
    if assessed - execution_allowlist:
        errors.append("assessment_not_in_execution_allowlist")
    facts = evidence_facts(decision, packet)
    core_status = decision.get("core_status")
    approved = core_status in {"approved", "approved_with_conflict"}
    base_a = bool(facts["canonical"] and facts["primaries"])
    base_b = facts["independent_primary_count"] >= 2 and bool(facts["functional"])
    if approved and not (decision.get("identity_exact") and decision.get("module_relation_direct")):
        errors.append("approved_without_exact_identity_and_direct_module_relation")
    if approved and not (base_a or base_b):
        errors.append("unsupported_approval")
    if approved and not facts["primaries"]:
        errors.append("authoritative_database_alone_cannot_approve")
    if core_status == "rejected" and not (set(decision.get("decision_reason_codes") or []) & POSITIVE_REJECTION_REASONS):
        errors.append("rejected_without_positive_evidence")
    expected_context = approved
    if bool(decision.get("context_usable")) != expected_context:
        errors.append("context_usable_inconsistent_with_status")
    ceiling = decision.get("inference_ceiling")
    if not approved and ceiling != "none":
        errors.append("nonapproved_ceiling_must_be_none")
    if approved and ceiling == "none":
        errors.append("approved_ceiling_cannot_be_none")
    if ceiling == "initial_guide_candidate" and not (facts["directly_applicable_human"] and facts["compatible_functional"]):
        errors.append("initial_guide_without_applicable_human_and_functional_evidence")
    direction = compute_direction(decision.get("source_assessments") or [])
    declared = decision.get("direction_assessment") or {}
    if any(abs(float(declared.get(field, -999)) - float(direction[field])) > 0.02 for field in ("support_score", "opposition_score", "margin")):
        errors.append("direction_score_mismatch")
    if declared.get("dominant_direction") != direction["dominant_direction"] or bool(declared.get("material_conflict")) != direction["material_conflict"]:
        errors.append("direction_classification_mismatch")
    if direction["material_conflict"] and direction["margin"] < 15 and approved:
        errors.append("conflict_margin_below_15_cannot_approve")
    if core_status == "approved_with_conflict" and not direction["material_conflict"]:
        errors.append("approved_with_conflict_without_conflict")
    expert = decision.get("expert_review_basis") or {}
    conflict_ids = set(expert.get("conflict_evidence_ids") or [])
    if expert.get("runtime_variant_context_required") is not True:
        errors.append("expert_review_runtime_variant_gate_missing")
    if conflict_ids - selected:
        errors.append("expert_review_conflict_evidence_not_selected")
    if bool(expert.get("material_scientific_conflict")) != bool(direction["material_conflict"]):
        errors.append("expert_review_material_conflict_mismatch")
    if expert.get("material_scientific_conflict") and not conflict_ids:
        errors.append("expert_review_conflict_evidence_missing")
    if conflict_ids - set((decision.get("evidence_ids") or {}).get("conflict_or_null") or []):
        errors.append("expert_review_conflict_evidence_not_classified")
    strong_expected = bool(approved and facts["primaries"] and (facts["canonical"] or facts["independent_primary_count"] >= 2))
    if bool(expert.get("strong_evidence")) != strong_expected:
        errors.append("expert_review_strong_evidence_mismatch")
    for assessment in decision.get("source_assessments") or []:
        quality = assessment.get("quality") or {}
        if set(quality) != set(WEIGHTS):
            errors.append("incomplete_quality_scores")
    return sorted(set(errors))


def arbitration_reasons(curator: dict, critic: dict) -> list[str]:
    reasons: list[str] = []
    for field in ("core_status", "context_usable", "inference_ceiling"):
        if curator.get(field) != critic.get(field):
            reasons.append(f"disagreement:{field}")
    if float(curator.get("confidence", 0)) < 0.8 or float(critic.get("confidence", 0)) < 0.8:
        reasons.append("confidence_below_0_80")
    for label, decision in (("curator", curator), ("critic", critic)):
        direction = decision.get("direction_assessment") or {}
        if direction.get("material_conflict"):
            reasons.append(f"{label}:material_conflict")
        if direction.get("dominant_direction") != "not_applicable" and float(direction.get("margin", 0)) < 15:
            reasons.append(f"{label}:direction_margin_below_15")
        if "metadata_insufficient" in set(decision.get("decision_reason_codes") or []):
            reasons.append(f"{label}:metadata_or_method_missing")
    return sorted(set(reasons))


def prompt_identifier_errors(prompt: str) -> list[str]:
    lower = prompt.lower()
    forbidden = {group.lower() for group in GOLD_GROUPS} | {group.split(":", 1)[0].lower() for group in GOLD_GROUPS}
    return [f"gold_identifier_in_prompt:{token}" for token in sorted(forbidden) if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower)]


def gold_manifest_template(packets: dict[str, dict]) -> dict:
    cards = []
    for group_id in GOLD_GROUPS:
        packet = packets[group_id]
        cards.append({
            "group_id": group_id,
            "split": "calibration" if group_id in CALIBRATION_GROUPS else "holdout",
            "packet_sha256": packet["packet_sha256"],
            "approval_status": "pending",
            "acceptable_core_statuses": [],
            "context_usable": None,
            "acceptable_inference_ceilings": [],
            "valid_evidence_ids": [],
            "invalid_evidence_ids": [],
            "acceptable_directions": [],
            "required_limitations": [],
            "reviewer": "",
            "reviewed_at": "",
            "signature_reconciliation": "",
            "signature_source_csv_sha256": "",
            "signature_source_xlsx_sha256": "",
            "notes": "",
        })
    body = {
        "schema_version": "tier1_curation_gold_v2",
        "approval_status": "pending",
        "created_at": utc_now(),
        "evidence_cutoff": CUTOFF.isoformat(),
        "calibration_groups": list(CALIBRATION_GROUPS),
        "holdout_groups": list(HOLDOUT_GROUPS),
        "cards": cards,
    }
    body["manifest_sha256"] = sha256_json(body)
    return body


def validate_gold_manifest(manifest: dict, packets: dict[str, dict], *, evidence_manifest: dict | None = None) -> list[str]:
    errors: list[str] = []
    strict_v4 = bool(evidence_manifest and evidence_manifest.get("schema_version") == "tier1_evidence_packet_manifest_v4")
    unhashed = dict(manifest)
    declared_hash = unhashed.pop("manifest_sha256", "")
    if not declared_hash or sha256_json(unhashed) != declared_hash:
        errors.append("gold_manifest_hash_mismatch")
    cards = manifest.get("cards") or []
    by_group = {card.get("group_id"): card for card in cards}
    if set(by_group) != set(GOLD_GROUPS) or len(cards) != 12:
        errors.append("gold_must_contain_exactly_12_fixed_groups")
    if manifest.get("approval_status") != "approved":
        errors.append("gold_not_approved")
    if tuple(manifest.get("calibration_groups") or []) != CALIBRATION_GROUPS:
        errors.append("gold_calibration_split_mismatch")
    if tuple(manifest.get("holdout_groups") or []) != HOLDOUT_GROUPS:
        errors.append("gold_holdout_split_mismatch")
    if evidence_manifest:
        if manifest.get("evidence_manifest_sha256") != evidence_manifest.get("manifest_sha256"):
            errors.append("gold_evidence_manifest_hash_mismatch")
        for field in ("metadata_qa_sha256", "pubmed_snapshot_manifest_sha256"):
            if not manifest.get(field) or manifest.get(field) != evidence_manifest.get(field):
                errors.append(f"gold_{field}_mismatch")
    for group_id in GOLD_GROUPS:
        card = by_group.get(group_id) or {}
        packet = packets.get(group_id) or {}
        if card.get("approval_status") != "approved":
            errors.append(f"gold_card_not_approved:{group_id}")
        if card.get("packet_sha256") != packet.get("packet_sha256"):
            errors.append(f"gold_packet_hash_mismatch:{group_id}")
        if not card.get("acceptable_core_statuses"):
            errors.append(f"gold_missing_status:{group_id}")
        if not isinstance(card.get("context_usable"), bool):
            errors.append(f"gold_missing_context:{group_id}")
        if not card.get("acceptable_inference_ceilings"):
            errors.append(f"gold_missing_ceiling:{group_id}")
        if card.get("split") != ("calibration" if group_id in CALIBRATION_GROUPS else "holdout"):
            errors.append(f"gold_card_split_mismatch:{group_id}")
        if not card.get("acceptable_directions") or set(card.get("acceptable_directions") or []) - {
            "supports_relation", "opposes_relation", "no_dominant_direction", "not_applicable"
        }:
            errors.append(f"gold_missing_or_invalid_direction:{group_id}")
        if not card.get("required_limitations") or not all(str(value).strip() for value in card.get("required_limitations") or []):
            errors.append(f"gold_missing_limitations:{group_id}")
        allowed_ids = {source["evidence_id"] for source in packet.get("source_ledger") or []}
        if set(card.get("valid_evidence_ids") or []) - allowed_ids:
            errors.append(f"gold_unknown_valid_evidence:{group_id}")
        selected_ids = set(packet.get("selected_evidence_ids") or [])
        if set(card.get("valid_evidence_ids") or []) - selected_ids:
            errors.append(f"gold_valid_evidence_not_selected:{group_id}")
        if set(card.get("invalid_evidence_ids") or []) & selected_ids:
            errors.append(f"gold_invalid_evidence_selected:{group_id}")
        if not card.get("reviewer") or not card.get("reviewed_at"):
            errors.append(f"gold_missing_review_attribution:{group_id}")
        if card.get("signature_reconciliation") not in {"original_signed", "technical_metadata_only"}:
            errors.append(f"gold_missing_signature_reconciliation:{group_id}")
        if strict_v4 and (not card.get("signature_source_csv_sha256") or not card.get("signature_source_xlsx_sha256")):
            errors.append(f"gold_missing_signature_provenance:{group_id}")
        if strict_v4 and packet.get("publication_metadata_snapshot", {}).get("consistency_status") != "consistent":
            errors.append(f"gold_metadata_not_consistent:{group_id}")
    return sorted(set(errors))


def evaluate_against_gold(decision: dict, card: dict) -> list[str]:
    errors: list[str] = []
    if decision.get("core_status") not in set(card.get("acceptable_core_statuses") or []):
        errors.append("gold:core_status")
    if decision.get("context_usable") is not card.get("context_usable"):
        errors.append("gold:context_usable")
    if decision.get("inference_ceiling") not in set(card.get("acceptable_inference_ceilings") or []):
        errors.append("gold:inference_ceiling")
    cited = {evidence_id for ids in (decision.get("evidence_ids") or {}).values() for evidence_id in ids}
    valid_ids = set(card.get("valid_evidence_ids") or [])
    invalid_ids = set(card.get("invalid_evidence_ids") or [])
    if cited & invalid_ids:
        errors.append("gold:invalid_evidence_used")
    if cited - valid_ids:
        errors.append("gold:evidence_not_signed_valid")
    if valid_ids and not (cited & valid_ids):
        errors.append("gold:no_valid_evidence_used")
    if decision.get("direction_assessment", {}).get("dominant_direction") not in set(card.get("acceptable_directions") or []):
        errors.append("gold:direction")
    return sorted(set(errors))


def calibration_acceptance(records: list[dict], gold_manifest: dict) -> dict:
    cards = {card["group_id"]: card for card in gold_manifest.get("cards") or []}
    errors: list[str] = []
    pair_agreement = 0
    for record in records:
        group_id = record.get("group_id")
        curator, critic = record.get("curator") or {}, record.get("critic") or {}
        packet_errors = record.get("packet_errors") or []
        errors.extend(f"{group_id}:{error}" for error in packet_errors)
        for label, decision in (("curator", curator), ("critic", critic)):
            errors.extend(f"{group_id}:{label}:{error}" for error in record.get(f"{label}_errors") or [])
        final = record.get("final_decision") or {}
        errors.extend(f"{group_id}:gold:{error}" for error in evaluate_against_gold(final, cards.get(group_id, {})))
        if all(curator.get(field) == critic.get(field) for field in ("core_status", "context_usable", "inference_ceiling")):
            pair_agreement += 1
    complete = len(records) == 12 and {row.get("group_id") for row in records} == set(GOLD_GROUPS)
    report = {
        "complete_12_of_12": complete,
        "pair_agreement": pair_agreement,
        "pair_agreement_required": 11,
        "error_count": len(errors),
        "errors": errors,
        "passed": complete and pair_agreement >= 11 and not errors,
    }
    return report


def phase_acceptance(records: list[dict], gold_manifest: dict, phase: str) -> dict:
    if phase not in {"calibration", "holdout", "complete"}:
        raise ValueError(f"Unknown evaluation phase: {phase}")
    expected = set(GOLD_GROUPS if phase == "complete" else (CALIBRATION_GROUPS if phase == "calibration" else HOLDOUT_GROUPS))
    cards = {card["group_id"]: card for card in gold_manifest.get("cards") or []}
    errors: list[str] = []
    pair_agreement = 0
    unsupported_approvals = 0
    for record in records:
        group_id = record.get("group_id")
        curator, critic = record.get("curator") or {}, record.get("critic") or {}
        for error in record.get("packet_errors") or []:
            errors.append(f"{group_id}:packet:{error}")
        for label in ("curator", "critic", "arbiter"):
            for error in record.get(f"{label}_errors") or []:
                errors.append(f"{group_id}:{label}:{error}")
                unsupported_approvals += int(error == "unsupported_approval")
        final = record.get("final_decision") or {}
        errors.extend(f"{group_id}:{error}" for error in evaluate_against_gold(final, cards.get(group_id, {})))
        if all(curator.get(field) == critic.get(field) for field in ("core_status", "context_usable", "inference_ceiling")):
            pair_agreement += 1
    complete = len(records) == len(expected) and {row.get("group_id") for row in records} == expected
    required_agreement = 11 if phase == "complete" else 5
    return {
        "phase": phase, "complete": complete, "expected_groups": sorted(expected),
        "pair_agreement": pair_agreement, "pair_agreement_required": required_agreement,
        "unsupported_approvals": unsupported_approvals, "error_count": len(errors), "errors": errors,
        "passed": complete and pair_agreement >= required_agreement and unsupported_approvals == 0 and not errors,
    }


def repeated_dominant_failure(reports: list[dict], threshold: int = 3) -> str:
    failed = [report.get("dominant_failure_signature") for report in reports if not report.get("passed")]
    if len(failed) < threshold:
        return ""
    tail = failed[-threshold:]
    return tail[0] if tail[0] and len(set(tail)) == 1 else ""


def validate_manual_review(candidate_rows: list[dict], reviews: list[dict]) -> list[str]:
    """Require all approvals/conflicts plus a stratified 20% nonapproval sample."""
    errors: list[str] = []
    review_by_group = {row.get("group_id"): row for row in reviews if row.get("approved") is True}
    required_full = {
        row["group_id"] for row in candidate_rows
        if row.get("core_status") in {"approved", "approved_with_conflict"}
    }
    missing_full = required_full - set(review_by_group)
    errors.extend(f"approved_group_not_manually_reviewed:{group_id}" for group_id in sorted(missing_full))
    nonapproved = [row for row in candidate_rows if row.get("core_status") in {"withheld", "rejected"}]
    reviewed_nonapproved = [row for row in nonapproved if row["group_id"] in review_by_group]
    required_count = (len(nonapproved) + 4) // 5
    if len(reviewed_nonapproved) < required_count:
        errors.append(f"nonapproval_sample_below_20_percent:{len(reviewed_nonapproved)}/{required_count}")
    modules = {row["group_id"].split(":", 1)[1] for row in nonapproved}
    reviewed_modules = {row["group_id"].split(":", 1)[1] for row in reviewed_nonapproved}
    for module in sorted(modules - reviewed_modules):
        errors.append(f"nonapproval_sample_missing_module:{module}")
    low_evidence = {row["group_id"] for row in nonapproved if row.get("evidence_selected_count", 0) <= 1}
    disagreements = {row["group_id"] for row in nonapproved if row.get("adjudicated")}
    if low_evidence and not (low_evidence & set(review_by_group)):
        errors.append("nonapproval_sample_missing_low_evidence")
    if disagreements and not (disagreements & set(review_by_group)):
        errors.append("nonapproval_sample_missing_disagreement")
    return errors


def cross_group_flags(candidate_rows: list[dict]) -> list[dict]:
    flags: list[dict] = []
    by_module: dict[str, list[dict]] = defaultdict(list)
    for row in candidate_rows:
        by_module[row["group_id"].split(":", 1)[1]].append(row)
    for module, rows in sorted(by_module.items()):
        statuses = Counter(row.get("core_status") for row in rows)
        if statuses.get("rejected", 0) > len(rows) * 0.5:
            flags.append({"module_id": module, "flag": "majority_rejected_review_identity_or_module_mapping", "count": statuses["rejected"]})
        if rows and all(row.get("inference_ceiling") == "none" for row in rows):
            flags.append({"module_id": module, "flag": "all_groups_have_no_inference_ceiling", "count": len(rows)})
        low_confidence = sum(float(row.get("confidence", 0)) < 0.8 for row in rows)
        if low_confidence:
            flags.append({"module_id": module, "flag": "low_confidence_decisions", "count": low_confidence})
    return flags
