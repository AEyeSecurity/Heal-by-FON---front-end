#!/usr/bin/env python3
"""Build bounded, auditable LLM1 v5 payloads from curated HEAL evidence."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


PAYLOAD_VERSION = "llm1_group_payload_v5"
TARGET_TOKENS = 15_000
HARD_TOKENS = 25_000
CONFIRMED_IDENTITIES = {"exact_coordinate_allele", "normalized_indel_match"}
NON_BENIGN = {
    "pathogenic_or_likely_pathogenic",
    "conflicting_pathogenicity",
    "uncertain_significance",
    "risk_factor",
    "drug_response",
    "other_or_association",
}
STRONG_REGIONS = {"mane_cds_overlap", "splice_region_candidate", "alternative_protein_coding_cds_overlap"}
SOURCE_NAMES = ("ensembl_vep", "ensembl_variation", "clinvar", "myvariant", "gwas", "pharmgkb")
PUBLICATION_TYPE_TERMS = {
    "systematic_review": ("systematic review", "meta-analysis", "meta analysis"),
    "functional_study": ("functional", "activity", "expression", "enzyme", "assay", "in vitro", "in vivo"),
    "guideline_or_panel": ("guideline", "consensus", "expert panel", "recommendation"),
}
REQUIRED_PAYLOAD_SECTIONS = {
    "payload_schema_version", "execution_mode", "group_id", "gene", "module_id", "group_context",
    "canonical_status", "curated_mechanism", "focus_variant_evidence", "clinical_evidence_summary",
    "gwas_evidence_summary", "publication_evidence_digest", "supporting_context",
    "transcript_discordant_context", "identity_unresolved", "source_failures", "deterministic_summary",
    "evidence_coverage", "compression_metadata", "provenance", "gates",
}
VALID_COVERAGE_STATUSES = {
    "included_full", "included_as_digest", "summarized_deterministically",
    "referenced_only", "excluded_from_prompt_with_reason",
}


def clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def as_int(value, default: int = 0) -> int:
    try:
        return int(float(clean(value)))
    except (TypeError, ValueError):
        return default


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return default


def as_bool(value) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def stable_id(prefix: str, *parts) -> str:
    raw = "|".join(clean(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def variant_ref(row: dict) -> str:
    return clean(row.get("resolved_rsid")) or (
        f"{clean(row.get('chrom_vcf'))}:{clean(row.get('pos_vcf') or row.get('variant_start'))}:"
        f"{clean(row.get('ref_vcf'))}>{clean(row.get('alt_vcf'))}"
    )


def split_values(value) -> list[str]:
    return [item.strip() for item in re.split(r"\s*\|\s*|\s*;\s*", clean(value)) if item.strip()]


def estimate_tokens(value, model: str = "") -> tuple[int, str]:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        import tiktoken  # type: ignore

        encoding = tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(text)), "tiktoken"
    except Exception:
        return math.ceil(len(text.encode("utf-8")) / 3.5), "conservative_utf8_estimate"


def token_status(tokens: int) -> str:
    if tokens <= TARGET_TOKENS:
        return "within_target"
    if tokens <= HARD_TOKENS:
        return "large_but_allowed"
    return "requires_hierarchical_compression"


def validate_payload_contract(payload: dict, coverage_records: list[dict]) -> None:
    missing = REQUIRED_PAYLOAD_SECTIONS - set(payload)
    if missing:
        raise ValueError(f"V5 payload is missing required sections: {sorted(missing)}")
    if payload.get("payload_schema_version") != PAYLOAD_VERSION or payload.get("execution_mode") not in {"dry_run", "pilot"}:
        raise ValueError("V5 payload version or execution mode is invalid.")
    if not payload.get("canonical_status") or len(payload.get("focus_variant_evidence") or []) > 20:
        raise ValueError("V5 canonical status is empty or focus variant limit was exceeded.")
    coverage = payload.get("evidence_coverage") or {}
    counts = coverage.get("status_counts") or {}
    if set(counts) - VALID_COVERAGE_STATUSES:
        raise ValueError("V5 evidence coverage contains an unsupported status.")
    if coverage.get("records_total") != len(coverage_records) or sum(counts.values()) != len(coverage_records):
        raise ValueError("V5 evidence coverage does not reconcile to its ledger records.")
    if not all(clean(row.get("evidence_id")) and clean(row.get("record_id")) for row in coverage_records):
        raise ValueError("V5 evidence coverage contains an empty record or evidence reference.")
    if any(row.get("status") == "excluded_from_prompt_with_reason" and not clean(row.get("reason")) for row in coverage_records):
        raise ValueError("V5 excluded prompt evidence is missing its reason.")
    if as_int(payload.get("compression_metadata", {}).get("estimated_tokens")) > HARD_TOKENS:
        if payload.get("gates", {}).get("token_budget_ready"):
            raise ValueError("V5 token gate is inconsistent with the estimated token count.")


def source_statuses(row: dict) -> dict:
    output = {}
    for source in SOURCE_NAMES:
        key = "vep_status" if source == "ensembl_vep" else f"source_status_{source}"
        output[source] = {
            "status": clean(row.get(key)) or "not_queried",
            "reason": clean(row.get(f"source_status_reason_{source}")),
            "query_mode": clean(row.get(f"source_query_mode_{source}")),
        }
    return output


def transcript_class(row: dict) -> str:
    stored = clean(row.get("transcript_concordance"))
    if stored:
        return stored
    if clean(row.get("vep_status")) != "success":
        return "annotation_unavailable"
    if clean(row.get("local_region_class")) == "utr_overlap" and "intron" in clean(row.get("vep_most_severe_consequence")).lower():
        return "discordant_utr_vs_intron"
    return "concordant_or_contextual"


def mechanism_for_group(rows: list[dict], key: tuple[str, str]) -> dict:
    index = {(clean(row.get("gene") or row.get("approved_symbol")), clean(row.get("module_id"))): row for row in rows}
    row = index.get(key, {})
    usable = clean(row.get("curation_status")) == "approved" and bool(clean(row.get("source_ids_or_urls")))
    return {
        "evidence_id": stable_id("evm", *key),
        "source": "mechanism_registry_v1",
        "registry_version": clean(row.get("mechanism_registry_version")) or "mechanism_registry_v1",
        "curation_status": clean(row.get("curation_status")) or "missing",
        "usable_by_llm": usable,
        "biological_function": clean(row.get("biological_function")) if usable else "",
        "pathway": clean(row.get("pathway")) if usable else "",
        "directionality": clean(row.get("directionality")) if usable else "",
        "related_systems": clean(row.get("related_systems")) if usable else "",
        "related_modules": clean(row.get("related_modules")) if usable else "",
        "evidence_tier": clean(row.get("mechanism_evidence_tier")) if usable else "",
        "sources": split_values(row.get("source_ids_or_urls")) if usable else [],
        "limitation": "Mechanism withheld until professional curation." if not usable else "",
    }


def compact_focus_variant(row: dict) -> dict:
    ref = variant_ref(row)
    return {
        "evidence_id": stable_id("evv", row.get("variant_key"), row.get("module_id")),
        "variant_ref": ref,
        "variant_key": clean(row.get("variant_key")),
        "source": "observed_vcf_and_vep",
        "assembly": clean(row.get("assembly")) or "GRCh38",
        "chromosome": clean(row.get("chrom_vcf")),
        "position": as_int(row.get("pos_vcf") or row.get("variant_start")),
        "reference_allele": clean(row.get("ref_vcf")),
        "alternate_allele": clean(row.get("alt_vcf")),
        "genotype": clean(row.get("gt_alleles")),
        "zygosity": clean(row.get("zygosity")),
        "quality": clean(row.get("qual_vcf")),
        "filter": clean(row.get("filter_vcf")),
        "identity_match_class": clean(row.get("identity_match_class")),
        "local_region_class": clean(row.get("local_region_class")),
        "transcript_concordance": transcript_class(row),
        "functional_evidence": {
            "most_severe_consequence": clean(row.get("vep_most_severe_consequence")),
            "target_gene_effect_status": clean(row.get("vep_target_gene_effect_status")),
            "picked_transcript": clean(row.get("vep_picked_transcript")),
            "mane_select": clean(row.get("vep_mane_select")),
            "canonical": clean(row.get("vep_canonical")),
            "hgvsc": clean(row.get("vep_hgvsc")),
            "hgvsp": clean(row.get("vep_hgvsp")),
            "cadd_phred": clean(row.get("vep_cadd_phred")),
            "revel_score": clean(row.get("vep_revel_score")),
            "alphamissense_prediction": clean(row.get("vep_alphamissense_pred")),
            "spliceai": clean(row.get("vep_spliceai")),
        },
        "clinical_aggregate": {
            "classification": clean(row.get("curated_clinvar_classification") or row.get("clinvar_normalized_classification")),
            "review_status": clean(row.get("clinvar_review_status")),
            "traits": split_values(row.get("clinvar_trait_names")),
            "conflict": as_bool(row.get("clinvar_conflict_flag")),
        },
        "population": {
            "max_frequency": clean(row.get("population_max_frequency")),
            "population": clean(row.get("population_max_frequency_population")),
            "allele": clean(row.get("population_max_frequency_allele")),
        },
        "source_statuses": source_statuses(row),
        "attention_score": as_int(row.get("attention_score")),
        "artifact_ref": "gene_module_group_variant_detail_v4.csv",
    }


def focus_rows(rows: list[dict]) -> list[dict]:
    eligible = [
        row for row in rows
        if as_bool(row.get("focus_eligible"))
        and clean(row.get("downstream_role")) != "unresolved_review"
        and clean(row.get("identity_match_class")) in CONFIRMED_IDENTITIES
    ]
    eligible.sort(key=lambda row: (as_int(row.get("attention_rank"), 10**9), -as_int(row.get("attention_score"))))
    focus = eligible[:12]
    if len(eligible) > 12:
        threshold = as_int(focus[-1].get("attention_score")) if focus else -1
        for row in eligible[12:]:
            if len(focus) >= 20 or as_int(row.get("attention_score")) != threshold:
                break
            focus.append(row)
    return focus


def assertion_priority(row: dict) -> tuple:
    classification = clean(row.get("normalized_classification"))
    review = clean(row.get("review_status")).lower()
    return (
        0 if classification in NON_BENIGN else 1,
        0 if "expert panel" in review else 1,
        0 if as_bool(row.get("contributes_to_aggregate")) else 1,
        0 if clean(row.get("description")) else 1,
        clean(row.get("submission_date")),
        clean(row.get("scv_accession")),
    )


def summarize_assertions(
    assertions: list[dict], focus_keys: set[str], description_limit: int, context_group_limit: int
) -> tuple[dict, list[dict]]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in assertions:
        grouped[(
            clean(row.get("variant_key")),
            clean(row.get("normalized_classification")) or "not_reported",
            clean(row.get("conditions")) or "not_reported",
            clean(row.get("review_status")) or "not_reported",
        )].append(row)
    summaries = []
    coverage = []
    for key, items in sorted(grouped.items()):
        items.sort(key=assertion_priority)
        representative = items[0]
        variant_key, classification, condition, review = key
        evidence_id = stable_id("evc", *key)
        description = clean(representative.get("description"))
        summaries.append({
            "evidence_id": evidence_id,
            "variant_ref": clean(representative.get("resolved_rsid")) or variant_key,
            "variant_key": variant_key,
            "source": "clinvar_scv",
            "classification": classification,
            "condition": condition,
            "review_status": review,
            "assertion_count": len(items),
            "submitter_count": len({clean(item.get("submitter")) for item in items if clean(item.get("submitter"))}),
            "contributing_assertion_count": sum(as_bool(item.get("contributes_to_aggregate")) for item in items),
            "representative_assertion": {
                "scv_accession": clean(representative.get("scv_accession")),
                "vcv_accession": clean(representative.get("vcv_accession")),
                "submitter": clean(representative.get("submitter")),
                "description_excerpt": description[:description_limit],
                "pmids": split_values(representative.get("pmids")),
            },
            "assertion_refs": [clean(item.get("scv_accession")) for item in items if clean(item.get("scv_accession"))],
            "identity_match": all(as_bool(item.get("identity_match")) for item in items),
            "artifact_ref": "clinvar_submitter_assertions.csv",
        })
        for index, item in enumerate(items):
            status = "included_full" if index == 0 and variant_key in focus_keys and classification in NON_BENIGN else "summarized_deterministically"
            coverage.append({"record_id": clean(item.get("scv_accession")) or stable_id("scv", variant_key, index), "status": status, "evidence_id": evidence_id})
    summaries.sort(
        key=lambda row: (
            0 if row["classification"] in NON_BENIGN else 1,
            -row["assertion_count"],
            row["variant_key"],
            row["evidence_id"],
        )
    )
    primary_groups = [row for row in summaries if row["classification"] in NON_BENIGN]
    benign_context = [row for row in summaries if row["classification"] not in NON_BENIGN]
    included_groups = primary_groups + benign_context[:context_group_limit]
    referenced_groups = benign_context[context_group_limit:]
    classes = Counter(clean(row.get("normalized_classification")) or "not_reported" for row in assertions)
    conflicts = sorted({clean(row.get("variant_key")) for row in assertions if clean(row.get("normalized_classification")) == "conflicting_pathogenicity"})
    return {
        "assertions_total": len(assertions),
        "assertion_groups": included_groups,
        "referenced_group_count": len(referenced_groups),
        "referenced_group_ids_for_audit": [row["evidence_id"] for row in referenced_groups[:40]],
        "classification_counts": dict(classes),
        "conflicting_variant_keys": conflicts,
        "complete_artifact": "clinvar_submitter_assertions.csv",
    }, coverage


def compact_cluster(row: dict) -> dict:
    return {
        "evidence_id": clean(row.get("cluster_id")) or stable_id("gwc", row.get("approved_symbol"), row.get("module_id"), row.get("trait_id")),
        "source": "gwas_catalog",
        "trait_id": clean(row.get("trait_id")),
        "trait_labels": split_values(row.get("trait_labels")),
        "module_relevance_status": clean(row.get("module_relevance_status")) or "unreviewed",
        "evidence_band": clean(row.get("evidence_band")),
        "association_count": as_int(row.get("association_count")),
        "allele_confirmed_significant_count": as_int(row.get("allele_confirmed_significant_count")),
        "independent_publication_count": as_int(row.get("independent_publication_count")),
        "direction_status": clean(row.get("direction_status")) or "unknown",
        "directions": split_values(row.get("directions")),
        "minimum_p_value": clean(row.get("minimum_p_value")),
        "variant_refs": split_values(row.get("variant_keys")),
        "publication_ids": split_values(row.get("publication_ids")),
        "artifact_ref": "gwas_evidence_clusters.csv",
    }


def summarize_gwas(clusters: list[dict], detail_limit: int) -> tuple[dict, list[dict]]:
    compact = [compact_cluster(row) for row in clusters]
    prioritized = [row for row in compact if row["evidence_band"] == "high_confidence_replicated" and row["module_relevance_status"] == "approved"]
    replicated_review = [row for row in compact if row["independent_publication_count"] >= 2 and row not in prioritized]
    context = [row for row in compact if row not in prioritized and row not in replicated_review]
    coverage = []
    for row in prioritized:
        coverage.append({"record_id": row["evidence_id"], "status": "included_full", "evidence_id": row["evidence_id"]})
    for row in replicated_review:
        coverage.append({"record_id": row["evidence_id"], "status": "summarized_deterministically", "evidence_id": row["evidence_id"]})
    for row in context:
        coverage.append({"record_id": row["evidence_id"], "status": "referenced_only", "evidence_id": row["evidence_id"]})
    prioritized.sort(key=lambda row: (-row["independent_publication_count"], row["minimum_p_value"], row["evidence_id"]))
    replicated_review.sort(key=lambda row: (-row["independent_publication_count"], row["minimum_p_value"], row["evidence_id"]))
    context_trait_counts = Counter(row["trait_id"] or "unmapped" for row in context)
    context_direction_counts = Counter(row["direction_status"] for row in context)
    return {
        "clusters_total": len(compact),
        "prioritized_clusters": prioritized[:detail_limit],
        "prioritized_cluster_count": len(prioritized),
        "prioritized_cluster_refs_for_audit": [row["evidence_id"] for row in prioritized[detail_limit:detail_limit + 40]],
        "replicated_pending_relevance": replicated_review[:detail_limit],
        "replicated_pending_relevance_count": len(replicated_review),
        "replicated_cluster_refs_for_audit": [row["evidence_id"] for row in replicated_review[detail_limit:detail_limit + 40]],
        "context_summary": {
            "count": len(context),
            "unique_trait_count": len(context_trait_counts),
            "top_trait_counts": dict(context_trait_counts.most_common(30)),
            "direction_counts": dict(context_direction_counts),
            "cluster_refs_for_audit": [row["evidence_id"] for row in context[:40]],
        },
        "complete_artifact": "gwas_evidence_clusters.csv",
    }, coverage


def publication_type(row: dict) -> str:
    text = f"{clean(row.get('title'))} {clean(row.get('abstract'))}".lower()
    for kind, terms in PUBLICATION_TYPE_TERMS.items():
        if any(term in text for term in terms):
            return kind
    return "association_or_context"


def publication_priority(row: dict, priority_pmids: set[str]) -> tuple:
    pmid = clean(row.get("pmid"))
    kind = publication_type(row)
    kind_rank = {"guideline_or_panel": 0, "systematic_review": 1, "functional_study": 2, "association_or_context": 3}[kind]
    return (0 if pmid in priority_pmids else 1, kind_rank, -as_int(row.get("publication_year")), pmid)


def summarize_publications(rows: list[dict], priority_pmids: set[str], limit: int, abstract_limit: int) -> tuple[dict, list[dict]]:
    dedup = {}
    for row in rows:
        key = clean(row.get("pmid")) or clean(row.get("doi"))
        if key and key not in dedup:
            dedup[key] = row
    ordered = sorted(dedup.values(), key=lambda row: publication_priority(row, priority_pmids))
    selected = ordered[:limit]
    digests = []
    coverage = []
    for row in ordered:
        pmid = clean(row.get("pmid"))
        evidence_id = f"PMID:{pmid}" if pmid else stable_id("pub", row.get("doi"), row.get("title"))
        if row in selected:
            abstract = clean(row.get("abstract"))
            digests.append({
                "evidence_id": evidence_id,
                "source": "pubmed",
                "pmid": pmid,
                "doi": clean(row.get("doi")),
                "title": clean(row.get("title")),
                "publication_year": clean(row.get("publication_year")),
                "study_type": publication_type(row),
                "abstract_excerpt": abstract[:abstract_limit],
                "digest_mode": "deterministic_extractive",
                "artifact_ref": "publication_evidence.csv",
            })
            coverage.append({"record_id": evidence_id, "status": "included_as_digest", "evidence_id": evidence_id})
        else:
            coverage.append({"record_id": evidence_id, "status": "referenced_only", "evidence_id": evidence_id})
    return {
        "publications_total": len(ordered),
        "selected_publications": digests,
        "study_type_counts": dict(Counter(publication_type(row) for row in ordered)),
        "publication_ref_count": sum(bool(clean(row.get("pmid"))) for row in ordered),
        "publication_refs_for_audit": [f"PMID:{clean(row.get('pmid'))}" for row in ordered[:40] if clean(row.get("pmid"))],
        "complete_artifact": "publication_evidence.csv",
    }, coverage


def context_item(row: dict) -> dict:
    return {
        "variant_ref": variant_ref(row),
        "variant_key": clean(row.get("variant_key")),
        "local_region_class": clean(row.get("local_region_class")),
        "identity_match_class": clean(row.get("identity_match_class")),
        "transcript_concordance": transcript_class(row),
        "downstream_role": clean(row.get("downstream_role")),
        "source_error_sources": [source for source, status in source_statuses(row).items() if status["status"] == "source_error"],
    }


def compact_context(rows: list[dict], refs_limit: int = 40) -> dict:
    return {
        "count": len(rows),
        "by_local_region": dict(Counter(clean(row.get("local_region_class")) or "unknown" for row in rows)),
        "by_downstream_role": dict(Counter(clean(row.get("downstream_role")) or "unknown" for row in rows)),
        "variant_refs_for_audit": [variant_ref(row) for row in rows[:refs_limit]],
        "complete_artifact": "gene_module_group_variant_detail_v4.csv",
    }


def evidence_packet(group_id: str, rows: list[dict], assertions: list[dict], clusters: list[dict], publications: list[dict]) -> dict:
    variant_fields = [
        "variant_key", "resolved_rsid", "chrom_vcf", "pos_vcf", "ref_vcf", "alt_vcf", "gt_alleles", "zygosity",
        "identity_match_class", "local_region_class", "vep_status", "vep_most_severe_consequence", "vep_picked_transcript",
        "vep_mane_select", "vep_hgvsc", "vep_hgvsp", "vep_cadd_phred", "vep_revel_score", "vep_alphamissense_pred",
        "vep_spliceai", "clinvar_normalized_classification", "population_max_frequency", "downstream_role", "transcript_concordance",
    ]
    for source in SOURCE_NAMES:
        if source != "ensembl_vep":
            variant_fields.extend([
                f"source_status_{source}",
                f"source_status_reason_{source}",
                f"source_query_mode_{source}",
            ])
    return {
        "group_id": group_id,
        "ledger_version": "v2_curated_evidence_1",
        "variants": [
            {
                "evidence_id": stable_id("evv", row.get("variant_key"), row.get("module_id")),
                **{field: row.get(field, "") for field in variant_fields},
            }
            for row in rows
        ],
        "clinvar_assertions": assertions,
        "gwas_clusters": clusters,
        "publications": publications,
        "source_artifacts": {
            "variants": "gene_module_group_variant_detail_v4.csv",
            "clinvar": "clinvar_submitter_assertions.csv",
            "gwas": "gwas_evidence_clusters.csv",
            "publications": "publication_evidence.csv",
        },
    }


def build_group_payload(
    rows: list[dict], canonical: list[dict], mechanism: dict, assertions: list[dict], clusters: list[dict], publications: list[dict],
    model: str, compression_level: int = 0, generative_digest: dict | None = None,
) -> tuple[dict, list[dict], dict]:
    first = rows[0]
    group_id = f"{clean(first.get('approved_symbol'))}:{clean(first.get('module_id'))}"
    focus = focus_rows(rows)
    focus_keys = {clean(row.get("variant_key")) for row in focus}
    description_limit = 600 if compression_level == 0 else 300
    publication_limit = 12 if compression_level == 0 else 6
    abstract_limit = 900 if compression_level == 0 else 450
    clinical, assertion_coverage = summarize_assertions(
        assertions,
        focus_keys,
        description_limit,
        context_group_limit=12 if compression_level == 0 else 6,
    )
    gwas, gwas_coverage = summarize_gwas(clusters, detail_limit=20 if compression_level == 0 else 10)
    priority_pmids = {
        pmid
        for group in clinical["assertion_groups"]
        if group["classification"] in NON_BENIGN
        for pmid in group["representative_assertion"]["pmids"]
    }
    priority_pmids.update(pmid for cluster in gwas["prioritized_clusters"] for pmid in cluster["publication_ids"])
    publication_summary, publication_coverage = summarize_publications(publications, priority_pmids, publication_limit, abstract_limit)
    if generative_digest:
        for group in clinical["assertion_groups"]:
            group["representative_assertion"]["description_excerpt"] = ""
        for publication in publication_summary["selected_publications"]:
            publication["abstract_excerpt"] = ""
        publication_summary["generative_public_evidence_digest"] = generative_digest.get("items") or []
        publication_summary["generative_digest_model"] = clean(generative_digest.get("model"))

    transcript_discordant = [row for row in rows if transcript_class(row) == "discordant_utr_vs_intron"]
    identity_unresolved = [row for row in rows if clean(row.get("identity_match_class")) not in CONFIRMED_IDENTITIES]
    source_failed = [row for row in rows if any(status["status"] == "source_error" for status in source_statuses(row).values())]
    source_error_refs = [
        {
            "evidence_id": stable_id("eve", row.get("variant_key"), source),
            "variant_ref": variant_ref(row),
            "source": source,
            "status": "source_error",
            "reason": status["reason"] or "source_error",
            "artifact_ref": "group_evidence_packets.jsonl.gz",
        }
        for row in source_failed
        for source, status in source_statuses(row).items()
        if status["status"] == "source_error"
    ]
    excluded_keys = {clean(row.get("variant_key")) for row in transcript_discordant + identity_unresolved + source_failed}
    supporting = [row for row in rows if clean(row.get("variant_key")) not in focus_keys | excluded_keys]

    coverage_records = []
    for row in rows:
        key = clean(row.get("variant_key"))
        if key in focus_keys:
            status = "included_full"
            reason = "selected_as_focus_variant"
        elif key in excluded_keys:
            status = "excluded_from_prompt_with_reason"
            reasons = []
            if row in transcript_discordant:
                reasons.append("transcript_discordance")
            if row in identity_unresolved:
                reasons.append("identity_unresolved")
            if row in source_failed:
                reasons.append("source_failure")
            reason = "|".join(reasons) or "not_focus_eligible"
        else:
            status = "summarized_deterministically"
            reason = "supporting_context"
        coverage_records.append({"record_id": key, "status": status, "evidence_id": stable_id("evv", key, first.get("module_id")), "reason": reason})
        for source, source_status in source_statuses(row).items():
            if source_status["status"] == "source_error":
                coverage_records.append({
                    "record_id": f"{key}:{source}",
                    "status": "summarized_deterministically",
                    "evidence_id": stable_id("eve", key, source),
                    "reason": source_status["reason"] or "source_error",
                })
    coverage_records.extend(assertion_coverage + gwas_coverage + publication_coverage)
    coverage_counts = dict(Counter(item["status"] for item in coverage_records))

    primary_refs = [
        variant_ref(row) for row in focus
        if clean(row.get("curated_clinvar_classification") or row.get("clinvar_normalized_classification")) in NON_BENIGN
        or clean(row.get("local_region_class")) in STRONG_REGIONS
    ]
    canonical_payload = [
        {
            **row,
            "evidence_id": stable_id("evk", group_id, row.get("canon_row_id") or index),
            "source": "v2_canonical_gene_module_status",
            "artifact_ref": "v2_canonical_gene_module_status.csv",
        }
        for index, row in enumerate(canonical)
    ] or [{
        "evidence_id": stable_id("evk", group_id, "unknown"),
        "source": "v2_canonical_gene_module_status",
        "observed_status": "unknown",
        "callable": "unknown",
        "artifact_ref": "v2_canonical_gene_module_status.csv",
    }]
    payload = {
        "payload_schema_version": PAYLOAD_VERSION,
        "execution_mode": "dry_run",
        "group_id": group_id,
        "gene": clean(first.get("approved_symbol")),
        "module_id": clean(first.get("module_id")),
        "group_context": {
            "module_name": clean(first.get("module_name")),
            "system_within_module": clean(first.get("system_within_module")),
            "tier": clean(first.get("tier")),
            "module_status": clean(first.get("module_status")),
            "evidence_tier": clean(first.get("evidence_tier")),
        },
        "canonical_status": canonical_payload,
        "curated_mechanism": mechanism,
        "focus_variant_evidence": [compact_focus_variant(row) for row in focus],
        "clinical_evidence_summary": clinical,
        "gwas_evidence_summary": gwas,
        "publication_evidence_digest": publication_summary,
        "supporting_context": compact_context(supporting),
        "transcript_discordant_context": compact_context(transcript_discordant),
        "identity_unresolved": compact_context(identity_unresolved),
        "source_failures": {
            **compact_context(source_failed),
            "by_source": dict(Counter(source for row in source_failed for source, status in source_statuses(row).items() if status["status"] == "source_error")),
            "error_refs_for_audit": source_error_refs[:40],
            "error_refs_referenced_only": max(0, len(source_error_refs) - 40),
        },
        "deterministic_summary": {
            "group_size_total": len(rows),
            "focus_variant_count": len(focus),
            "primary_variant_refs": primary_refs,
            "local_region_class_counts": dict(Counter(clean(row.get("local_region_class")) for row in rows)),
            "identity_counts": dict(Counter(clean(row.get("identity_match_class")) for row in rows)),
            "statement": "Counts describe observed records and evidence availability; they do not imply causality or diagnosis.",
        },
        "evidence_coverage": {
            "records_total": len(coverage_records),
            "status_counts": coverage_counts,
            "reconciled": sum(coverage_counts.values()) == len(coverage_records),
            "ledger_artifact": "group_evidence_packets.jsonl.gz",
            "coverage_artifact": "group_evidence_coverage_audit.csv",
        },
        "compression_metadata": {
            "strategy": (
                "hybrid_validated_digest"
                if generative_digest
                else ("deterministic" if compression_level == 0 else "deterministic_compact")
            ),
            "compression_level": compression_level,
            "target_tokens": TARGET_TOKENS,
            "hard_limit_tokens": HARD_TOKENS,
            "generative_digest_used": bool(generative_digest),
        },
        "provenance": {
            "generated_at": utc_now(),
            "source_projection": "v2_curated_gene_module_projection.csv",
            "source_physical_matrix": "v2_curated_physical_variant_matrix.csv",
            "variant_detail_artifact": "gene_module_group_variant_detail_v4.csv",
        },
    }
    tokens, method = estimate_tokens(payload, model)
    if tokens > HARD_TOKENS and compression_level == 0:
        return build_group_payload(
            rows, canonical, mechanism, assertions, clusters, publications, model,
            compression_level=1, generative_digest=generative_digest,
        )
    status = token_status(tokens)
    if tokens > HARD_TOKENS:
        status = "compression_review_required"
    clinvar_focus_errors = sum(
        source_statuses(row)["clinvar"]["status"] == "source_error" for row in focus
    )
    category = pilot_category(payload)
    source_errors_acceptable = clinvar_focus_errors == 0 and not (
        category == "pharmgkb_contextual"
        and any(source_statuses(row)["pharmgkb"]["status"] == "source_error" for row in focus)
    )
    payload["compression_metadata"].update({"estimated_tokens": tokens, "estimation_method": method, "budget_status": status})
    payload["gates"] = {
        "evidence_ledger_complete": True,
        "evidence_packet_ready": True,
        "token_budget_ready": tokens <= HARD_TOKENS,
        "digest_validated": True,
        "mechanism_registry_ready": bool(mechanism.get("usable_by_llm")),
        "source_errors_acceptable_for_pilot": source_errors_acceptable,
        "group_payload_ready": tokens <= HARD_TOKENS and bool(mechanism.get("usable_by_llm")) and source_errors_acceptable,
        "llm1_pilot_ready": False,
    }
    validate_payload_contract(payload, coverage_records)
    audit = {
        "group_id": group_id,
        "estimated_tokens": tokens,
        "estimation_method": method,
        "budget_status": status,
        "compression_strategy": payload["compression_metadata"]["strategy"],
        "focus_variants": len(focus),
        "group_size_total": len(rows),
        "assertions_total": len(assertions),
        "gwas_clusters_total": len(clusters),
        "publications_total": publication_summary["publications_total"],
        "group_payload_ready": str(payload["gates"]["group_payload_ready"]).lower(),
    }
    return payload, coverage_records, audit


def pilot_category(payload: dict) -> str:
    primary = payload.get("deterministic_summary", {}).get("primary_variant_refs") or []
    if primary:
        return "functional_or_clinvar_strong"
    if payload.get("gwas_evidence_summary", {}).get("prioritized_clusters") or payload.get("gwas_evidence_summary", {}).get("replicated_pending_relevance"):
        return "gwas_replicated_relevant"
    if any(
        item.get("source_statuses", {}).get("pharmgkb", {}).get("status") == "success"
        for item in payload.get("focus_variant_evidence", [])
    ):
        return "pharmgkb_contextual"
    if payload.get("focus_variant_evidence"):
        return "benign_or_functional_only"
    return "abstention_or_conflict"


def select_manifest_candidates(payloads: list[dict]) -> list[dict]:
    quotas = {
        "functional_or_clinvar_strong": 6,
        "gwas_replicated_relevant": 5,
        "pharmgkb_contextual": 3,
        "benign_or_functional_only": 3,
        "abstention_or_conflict": 3,
    }
    buckets: dict[str, list[dict]] = defaultdict(list)
    for payload in payloads:
        buckets[pilot_category(payload)].append(payload)
    selected = []
    seen = set()
    for category, quota in quotas.items():
        ranked = sorted(
            buckets[category],
            key=lambda row: (
                not row["gates"]["token_budget_ready"],
                not row["gates"]["source_errors_acceptable_for_pilot"],
                -len(row.get("deterministic_summary", {}).get("primary_variant_refs") or []),
                row["compression_metadata"]["estimated_tokens"],
                row["group_id"],
            ),
        )
        for payload in ranked[:quota]:
            if payload["group_id"] not in seen:
                selected.append(payload)
                seen.add(payload["group_id"])
    if len(selected) < 20:
        remaining = sorted(
            (payload for payload in payloads if payload["group_id"] not in seen),
            key=lambda row: (not row["gates"]["token_budget_ready"], row["compression_metadata"]["estimated_tokens"], row["group_id"]),
        )
        selected.extend(remaining[: 20 - len(selected)])
    output = []
    for payload in selected[:20]:
        blockers = [name for name, ready in payload["gates"].items() if name != "llm1_pilot_ready" and not ready]
        output.append({
            "group_id": payload["group_id"],
            "gene": payload["gene"],
            "module_id": payload["module_id"],
            "selection_category": pilot_category(payload),
            "estimated_tokens": payload["compression_metadata"]["estimated_tokens"],
            "budget_status": payload["compression_metadata"]["budget_status"],
            "focus_variant_count": len(payload["focus_variant_evidence"]),
            "primary_evidence_count": len(payload["deterministic_summary"]["primary_variant_refs"]),
            "source_failure_count": payload["source_failures"]["count"],
            "mechanism_status": payload["curated_mechanism"]["curation_status"],
            "blockers": " | ".join(blockers),
            "approved_for_pilot": "false",
            "approval_reviewer": "",
            "approval_timestamp": "",
        })
    return output


def process(payload: dict) -> dict:
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_rows = read_csv(Path(payload["detailPath"]))
    if not detail_rows:
        raise ValueError("V5 payload builder input detail is empty.")
    canonical_rows = read_csv(Path(payload["canonicalStatusPath"])) if payload.get("canonicalStatusPath") else []
    mechanism_rows = read_csv(Path(payload["mechanismRegistryPath"])) if payload.get("mechanismRegistryPath") else []
    assertion_rows = read_csv(Path(payload["clinvarAssertionsPath"])) if payload.get("clinvarAssertionsPath") else []
    cluster_rows = read_csv(Path(payload["gwasClustersPath"])) if payload.get("gwasClustersPath") else []
    publication_rows = read_csv(Path(payload["publicationsPath"])) if payload.get("publicationsPath") else []
    digest_rows = read_jsonl(Path(payload["digestPath"])) if payload.get("digestPath") else []
    digest_index = {clean(row.get("group_id")): row for row in digest_rows}
    model = clean(payload.get("tokenizerModel"))

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in detail_rows:
        groups[(clean(row.get("approved_symbol")), clean(row.get("module_id")))].append(row)
    canonical_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in canonical_rows:
        canonical_index[(clean(row.get("approved_symbol") or row.get("gene")), clean(row.get("module_id")))].append(row)
    assertions_by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in assertion_rows:
        assertions_by_variant[clean(row.get("variant_key"))].append(row)

    payloads = []
    packets = []
    token_audit = []
    coverage_audit = []
    errors = []
    for key, rows in sorted(groups.items()):
        try:
            rows.sort(key=lambda row: (as_int(row.get("attention_rank"), 10**9), -as_int(row.get("attention_score"))))
            variant_keys = {clean(row.get("variant_key")) for row in rows}
            assertions = [item for variant_key in variant_keys for item in assertions_by_variant.get(variant_key, [])]
            clusters = [row for row in cluster_rows if clean(row.get("approved_symbol")) == key[0] and clean(row.get("module_id")) == key[1]]
            publication_ids = {
                pmid for row in assertions for pmid in re.findall(r"\d+", clean(row.get("pmids")))
            }
            publication_ids.update(pmid for row in clusters for pmid in split_values(row.get("publication_ids")))
            publications = [row for row in publication_rows if clean(row.get("pmid")) in publication_ids]
            mechanism = mechanism_for_group(mechanism_rows, key)
            group_id = f"{key[0]}:{key[1]}"
            built, coverage, audit = build_group_payload(
                rows,
                canonical_index.get(key, []),
                mechanism,
                assertions,
                clusters,
                publications,
                model,
                generative_digest=digest_index.get(group_id),
            )
            payloads.append(built)
            packets.append(evidence_packet(built["group_id"], rows, assertions, clusters, publications))
            token_audit.append(audit)
            for item in coverage:
                coverage_audit.append({"group_id": built["group_id"], "record_type": item["evidence_id"].split("_", 1)[0], **item})
        except Exception as error:  # noqa: BLE001
            errors.append({"group_id": f"{key[0]}:{key[1]}", "error": str(error)})

    payload_path = output_dir / "llm1_group_payloads_v5.jsonl"
    payload_csv = output_dir / "llm1_group_payloads_v5.csv"
    packet_path = output_dir / "group_evidence_packets.jsonl.gz"
    digest_path = output_dir / "group_evidence_digests.jsonl"
    token_path = output_dir / "group_token_budget_audit.csv"
    coverage_path = output_dir / "group_evidence_coverage_audit.csv"
    error_path = output_dir / "group_compression_errors.csv"
    summary_path = output_dir / "group_compression_summary.json"
    manifest_path = output_dir / "llm1_pilot_candidate_manifest_v2.csv"
    schema_path = output_dir / "llm1_group_payload_v5.schema.json"

    write_jsonl(payload_path, payloads)
    write_csv(payload_csv, [{"group_id": row["group_id"], "payload_json": json.dumps(row, ensure_ascii=False, separators=(",", ":"))} for row in payloads], ["group_id", "payload_json"])
    with gzip.open(packet_path, "wt", encoding="utf-8") as handle:
        for packet in packets:
            handle.write(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n")
    # Preserve validated digests when this rebuild is triggered by the optional
    # digest service. The input may already be the output path in this directory.
    write_jsonl(digest_path, digest_rows)
    write_csv(token_path, token_audit, list(token_audit[0]) if token_audit else ["group_id", "estimated_tokens", "budget_status"])
    write_csv(coverage_path, coverage_audit, ["group_id", "record_type", "record_id", "status", "evidence_id", "reason"])
    write_csv(error_path, errors, ["group_id", "error"])
    manifest = select_manifest_candidates(payloads)
    write_csv(manifest_path, manifest, list(manifest[0]) if manifest else ["group_id", "approved_for_pilot"])
    schema_source = Path(__file__).with_name("llm1_group_payload_v5.schema.json")
    schema_path.write_text(schema_source.read_text(encoding="utf-8"), encoding="utf-8")

    status_counts = dict(Counter(row["budget_status"] for row in token_audit))
    coverage_status_counts = dict(Counter(row["status"] for row in coverage_audit))
    summary = {
        "status": "valid" if not errors else "warning",
        "schemaVersion": "gene_module_v2",
        "payloadSchemaVersion": PAYLOAD_VERSION,
        "executionMode": "dry_run",
        "metadata": {
            "total_groups": len(payloads),
            "source_rows": len(detail_rows),
            "source_variants_total": len({clean(row.get("variant_key")) for row in detail_rows}),
            "groups_with_approved_mechanism": sum(row["gates"]["mechanism_registry_ready"] for row in payloads),
            "groups_payload_ready": sum(row["gates"]["group_payload_ready"] for row in payloads),
            "groups_within_hard_limit": sum(row["gates"]["token_budget_ready"] for row in payloads),
            "groups_requiring_compression_review": sum(not row["gates"]["token_budget_ready"] for row in payloads),
            "max_estimated_tokens": max((row["estimated_tokens"] for row in token_audit), default=0),
            "token_budget_status_counts": status_counts,
            "coverage_status_counts": coverage_status_counts,
            "coverage_records": len(coverage_audit),
            "coverage_reconciled": all(row["evidence_coverage"]["reconciled"] for row in payloads),
            "pilot_candidates": len(manifest),
            "llm_calls": 0,
        },
        "outputs": {
            "groupPayloadsJsonlV5": str(payload_path),
            "groupPayloadsCsvV5": str(payload_csv),
            "groupEvidencePacketsJsonlGz": str(packet_path),
            "groupEvidenceDigestsJsonl": str(digest_path),
            "groupTokenBudgetAuditCsv": str(token_path),
            "groupEvidenceCoverageAuditCsv": str(coverage_path),
            "groupCompressionErrorsCsv": str(error_path),
            "groupCompressionSummaryJson": str(summary_path),
            "llm1PilotCandidateManifestV2Csv": str(manifest_path),
            "groupPayloadSchemaV5Json": str(schema_path),
        },
        "gates": {
            "evidenceLedgerComplete": "pass" if len(packets) == len(payloads) else "fail",
            "coverageReconciled": "pass" if all(row["evidence_coverage"]["reconciled"] for row in payloads) else "fail",
            "tokenBudgetReady": "pass" if all(row["gates"]["token_budget_ready"] for row in payloads) else "review_required",
            "llm1PilotReady": "blocked",
        },
        "timestamps": {"completedAt": utc_now()},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build bounded HEAL LLM1 group payloads v5.")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if not args.input_json_base64:
        parser.error("--input-json-base64 is required")
    return args


def main() -> int:
    args = parse_args()
    payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    process(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
