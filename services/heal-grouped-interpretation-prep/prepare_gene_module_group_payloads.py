#!/usr/bin/env python3
"""Build transcript-aware HEAL v2 gene-module payloads without invoking an LLM."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PAYLOAD_VERSION = "llm1_group_payload_v3"
PAYLOAD_VERSION_V4 = "llm1_group_payload_v4"
BASE_SCORES = {
    "mane_cds_overlap": 100,
    "splice_region_candidate": 95,
    "alternative_protein_coding_cds_overlap": 90,
    "protein_coding_exon_non_cds_overlap": 80,
    "utr_overlap": 60,
}
SOURCE_NAMES = ("ensembl_vep", "ensembl_variation", "clinvar", "myvariant", "gwas", "pharmgkb")
CONFIRMED_IDENTITIES = {"exact_coordinate_allele", "normalized_indel_match"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def read_csv(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def merge_projection_with_physical(projection_rows: list[dict], physical_rows: list[dict]) -> list[dict]:
    if not physical_rows:
        return projection_rows
    physical_by_key = {clean(row.get("variant_key")): row for row in physical_rows if clean(row.get("variant_key"))}
    missing = sorted({clean(row.get("variant_key")) for row in projection_rows if clean(row.get("variant_key")) not in physical_by_key})
    if missing:
        raise ValueError(f"Grouped payload projection has {len(missing)} variants without physical evidence.")
    return [{**physical_by_key[clean(row.get("variant_key"))], **row} for row in projection_rows]


def mechanism_registry_rows(groups: list[tuple[str, str]], existing_rows: list[dict]) -> list[dict]:
    existing = {(clean(row.get("gene") or row.get("approved_symbol")), clean(row.get("module_id"))): row for row in existing_rows}
    output = []
    for gene, module_id in sorted(groups):
        row = existing.get((gene, module_id), {})
        output.append(
            {
                "mechanism_registry_version": clean(row.get("mechanism_registry_version")) or "mechanism_registry_v1",
                "gene": gene,
                "module_id": module_id,
                "curation_status": clean(row.get("curation_status")) or "draft",
                "biological_function": clean(row.get("biological_function")),
                "pathway": clean(row.get("pathway")),
                "directionality": clean(row.get("directionality")),
                "related_systems": clean(row.get("related_systems")),
                "related_modules": clean(row.get("related_modules")),
                "mechanism_evidence_tier": clean(row.get("mechanism_evidence_tier")),
                "source_ids_or_urls": clean(row.get("source_ids_or_urls")),
                "reviewer": clean(row.get("reviewer")),
                "reviewed_at": clean(row.get("reviewed_at")),
                "review_notes": clean(row.get("review_notes")),
            }
        )
    return output


def sha256(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_spliceai(value) -> tuple[float, str]:
    text = clean(value)
    if not text:
        return 0.0, "not_reported"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    scores: list[float] = []

    def visit(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).upper().startswith("DS_"):
                    try:
                        scores.append(float(nested))
                    except (TypeError, ValueError):
                        pass
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            for token in item.replace("|", ";").split(";"):
                if "=" not in token:
                    continue
                key, nested = token.split("=", 1)
                if key.strip().upper().startswith("DS_"):
                    try:
                        scores.append(float(nested))
                    except ValueError:
                        pass

    visit(payload)
    maximum = max(scores, default=0.0)
    if maximum >= 0.80:
        return maximum, "very_strong"
    if maximum >= 0.50:
        return maximum, "strong"
    if maximum >= 0.20:
        return maximum, "candidate"
    if maximum >= 0.10:
        return maximum, "contextual"
    return maximum, "no_signal"


def variant_ref(row: dict) -> str:
    rsid = clean(row.get("resolved_rsid"))
    if rsid:
        return rsid
    return f"{clean(row.get('chrom_vcf'))}:{clean(row.get('pos_vcf') or row.get('variant_start'))}:{clean(row.get('ref_vcf'))}>{clean(row.get('alt_vcf'))}"


def source_statuses(row: dict) -> dict:
    result = {}
    for source in SOURCE_NAMES:
        status_key = "vep_status" if source == "ensembl_vep" else f"source_status_{source}"
        result[source] = {
            "status": clean(row.get(status_key)) or "not_queried",
            "status_reason": clean(row.get(f"source_status_reason_{source}")),
            "query_mode": clean(row.get(f"source_query_mode_{source}")),
        }
    return result


def transcript_concordance(row: dict) -> str:
    local = clean(row.get("local_region_class"))
    consequence = clean(row.get("vep_most_severe_consequence")).lower()
    target_effect = clean(row.get("vep_target_gene_effect_status"))
    if clean(row.get("vep_status")) != "success":
        return "annotation_unavailable"
    if target_effect and target_effect != "direct_target_transcript":
        return "target_transcript_unavailable"
    if local == "utr_overlap" and "intron" in consequence:
        return "discordant_utr_vs_intron"
    return "concordant_or_contextual"


def attention_score(row: dict) -> tuple[int, list[str], bool]:
    local = clean(row.get("local_region_class"))
    score = BASE_SCORES.get(local, 40)
    reasons = [f"base:{local or 'other'}={score}"]
    identity = clean(row.get("identity_match_class"))
    concordance = transcript_concordance(row)
    eligible = identity in CONFIRMED_IDENTITIES and clean(row.get("vep_status")) == "success" and concordance != "discordant_utr_vs_intron"
    if not eligible:
        reasons.append("excluded_from_focus:identity_annotation_or_transcript")
        return score, reasons, False

    clinvar = clean(row.get("clinvar_normalized_classification"))
    clinvar_status = clean(row.get("source_status_clinvar"))
    clinvar_points = {
        "conflicting_pathogenicity": 40,
        "pathogenic_or_likely_pathogenic": 35,
        "drug_response": 30,
        "risk_factor": 20,
        "benign_or_likely_benign": 5,
    }.get(clinvar, 0)
    if clinvar_status == "success" and clinvar_points:
        score += clinvar_points
        reasons.append(f"clinvar_confirmed:{clinvar}=+{clinvar_points}")

    splice_score, splice_class = parse_spliceai(row.get("vep_spliceai"))
    splice_points = {"candidate": 8, "strong": 15, "very_strong": 20}.get(splice_class, 0)
    if splice_points:
        score += splice_points
        reasons.append(f"spliceai_{splice_class}:{splice_score:.3f}=+{splice_points}")
    elif splice_class == "no_signal":
        reasons.append("spliceai_no_signal=+0")

    if clean(row.get("vep_hgvsp")) and clean(row.get("vep_target_gene_effect_status")) == "direct_target_transcript":
        score += 15
        reasons.append("target_transcript_hgvsp=+15")
    cadd = as_float(row.get("vep_cadd_phred"))
    if cadd >= 20:
        score += 15
        reasons.append("cadd_gte_20=+15")
    elif cadd >= 10:
        score += 8
        reasons.append("cadd_gte_10=+8")
    revel = as_float(row.get("vep_revel_score"))
    if revel >= 0.75:
        score += 15
        reasons.append("revel_gte_0_75=+15")
    elif revel >= 0.50:
        score += 8
        reasons.append("revel_gte_0_50=+8")
    alpha = clean(row.get("vep_alphamissense_pred")).lower()
    if any(term in alpha for term in ("pathogenic", "damaging")):
        score += 10
        reasons.append("alphamissense_damaging=+10")
    if as_float(row.get("population_max_frequency")) >= 0.05:
        score -= 10
        reasons.append("population_frequency_gte_0_05=-10")
    return score, reasons, True


def evidence_for_variant(row: dict) -> dict:
    splice_score, splice_class = parse_spliceai(row.get("vep_spliceai"))
    return {
        "variant_ref": variant_ref(row),
        "variant_key": clean(row.get("variant_key")),
        "functional": {
            "most_severe_consequence": clean(row.get("vep_most_severe_consequence")),
            "target_gene_effect_status": clean(row.get("vep_target_gene_effect_status")),
            "transcript_summary": clean(row.get("vep_transcript_summary")),
            "picked_transcript": clean(row.get("vep_picked_transcript")),
            "mane_select": clean(row.get("vep_mane_select")),
            "canonical": clean(row.get("vep_canonical")),
            "hgvsc": clean(row.get("vep_hgvsc")),
            "hgvsp": clean(row.get("vep_hgvsp")),
            "protein_id": clean(row.get("vep_protein_id")),
            "cadd_phred": clean(row.get("vep_cadd_phred")),
            "revel_score": clean(row.get("vep_revel_score")),
            "alphamissense_prediction": clean(row.get("vep_alphamissense_pred")),
            "spliceai_max_ds": splice_score,
            "spliceai_signal_class": splice_class,
        },
        "clinical": {
            "classification": clean(row.get("clinvar_normalized_classification")),
            "review_status": clean(row.get("clinvar_review_status")),
            "traits": clean(row.get("clinvar_trait_names")),
            "conflict": as_bool(row.get("clinvar_conflict_flag")),
        },
        "population": {
            "max_frequency": clean(row.get("population_max_frequency")),
            "population": clean(row.get("population_max_frequency_population")),
            "allele": clean(row.get("population_max_frequency_allele")),
        },
        "gwas": {
            "association_count": as_int(row.get("gwas_association_count")),
            "traits": clean(row.get("gwas_top_traits")),
            "reported_genes": clean(row.get("gwas_reported_genes")),
            "minimum_pvalue": clean(row.get("gwas_min_pvalue")),
            "summary": clean(row.get("gwas_top_associations")),
        },
        "pharmgkb": {
            "clinical_annotation_count": as_int(row.get("pharmgkb_clinical_annotation_count")),
            "clinical_evidence_levels": clean(row.get("pharmgkb_clinical_evidence_levels")),
            "chemicals": clean(row.get("pharmgkb_clinical_chemicals")),
            "clinical_summary": clean(row.get("pharmgkb_clinical_summary")),
            "variant_annotation_count": as_int(row.get("pharmgkb_variant_annotation_count")),
            "variant_summary": clean(row.get("pharmgkb_variant_annotation_summary")),
        },
        "source_statuses": source_statuses(row),
    }


def genetic_fact(row: dict) -> dict:
    return {
        "variant_ref": variant_ref(row),
        "variant_key": clean(row.get("variant_key")),
        "assembly": clean(row.get("assembly")) or clean(row.get("assembly_name")),
        "chromosome": clean(row.get("chrom_vcf")),
        "position": as_int(row.get("pos_vcf") or row.get("variant_start")),
        "reference_allele": clean(row.get("ref_vcf")),
        "alternate_allele": clean(row.get("alt_vcf")),
        "resolved_rsid": clean(row.get("resolved_rsid")),
        "candidate_rsid": clean(row.get("candidate_rsid")),
        "identity_match_class": clean(row.get("identity_match_class")),
        "genotype": clean(row.get("gt_alleles")),
        "zygosity": clean(row.get("zygosity")),
        "quality": clean(row.get("qual_vcf")),
        "filter": clean(row.get("filter_vcf")),
        "quality_flag": clean(row.get("quality_flag")),
        "local_region_class": clean(row.get("local_region_class")),
        "transcript_concordance": transcript_concordance(row),
    }


def count_values(rows: list[dict], field: str) -> dict[str, int]:
    return dict(Counter(clean(row.get(field)) for row in rows if clean(row.get(field))))


def focus_rows(rows: list[dict]) -> list[dict]:
    eligible = [row for row in rows if row["focus_eligible"]]
    if len(rows) <= 25:
        return eligible
    focus = eligible[:12]
    if len(focus) < 12:
        return focus
    threshold = focus[-1]["attention_score"]
    for row in eligible[12:]:
        if row["attention_score"] != threshold or len(focus) >= 20:
            break
        focus.append(row)
    return focus


def compact_variant(row: dict) -> dict:
    splice_score, splice_class = parse_spliceai(row.get("vep_spliceai"))
    return {
        "variant_ref": variant_ref(row),
        "variant_key": clean(row.get("variant_key")),
        "attention_score": row["attention_score"],
        "attention_reasons": row["attention_reasons"],
        "spliceai_max_ds": splice_score,
        "spliceai_signal_class": splice_class,
    }


def mechanism_for_group(index: dict, key: tuple[str, str]) -> dict:
    row = index.get(key)
    if not row:
        return {"curation_status": "missing", "usable_by_llm": False}
    usable = clean(row.get("curation_status")) == "approved" and bool(clean(row.get("source_ids_or_urls")))
    return {
        "registry_version": clean(row.get("mechanism_registry_version")),
        "curation_status": clean(row.get("curation_status")),
        "usable_by_llm": usable,
        "biological_function": clean(row.get("biological_function")) if usable else "",
        "pathway": clean(row.get("pathway")) if usable else "",
        "directionality": clean(row.get("directionality")) if usable else "",
        "related_systems": clean(row.get("related_systems")) if usable else "",
        "related_modules": clean(row.get("related_modules")) if usable else "",
        "evidence_tier": clean(row.get("mechanism_evidence_tier")) if usable else "",
        "sources": clean(row.get("source_ids_or_urls")) if usable else "",
        "limitation": "Mechanism is withheld until professional curation." if not usable else "",
    }


def build_payload(rows: list[dict], canonical: list[dict], mechanism: dict, provenance: dict, detail_path: Path) -> dict:
    first = rows[0]
    focus = focus_rows(rows)
    focus_keys = {clean(row.get("variant_key")) for row in focus}
    unresolved = [row for row in rows if not row["focus_eligible"]]
    context = [row for row in rows if clean(row.get("variant_key")) not in focus_keys and row["focus_eligible"]]
    source_error_counts = Counter()
    for row in rows:
        for source, status in source_statuses(row).items():
            if status["status"] == "source_error":
                source_error_counts[source] += 1
    group_id = f"{clean(first.get('approved_symbol'))}:{clean(first.get('module_id'))}"
    group_context = {
        "group_id": group_id,
        "gene": clean(first.get("approved_symbol")),
        "full_gene_name": clean(first.get("full_gene_name")),
        "module_id": clean(first.get("module_id")),
        "module_name": clean(first.get("module_name")),
        "system_within_module": clean(first.get("system_within_module")),
        "tier": clean(first.get("tier")),
        "module_status": clean(first.get("module_status")),
        "evidence_tier": clean(first.get("evidence_tier")),
        "module_purpose": clean(first.get("module_purpose")),
        "explicit_exclusions": clean(first.get("explicit_exclusions")),
        "canonical_version": clean(first.get("canonical_version")),
    }
    return {
        "payload_schema_version": PAYLOAD_VERSION,
        "dry_run_only": True,
        "group_id": group_id,
        "gene": group_context["gene"],
        "module_id": group_context["module_id"],
        "group_context": group_context,
        "canonical_status": canonical or [{"canonical_status": "unknown", "callable": "unknown", "hom_ref": "unknown"}],
        "genetic_facts": [genetic_fact(row) for row in focus],
        "scientific_evidence": [evidence_for_variant(row) for row in focus],
        "curated_mechanisms": mechanism,
        "deterministic_summary": {
            "group_size_total": len(rows),
            "focus_variant_count": len(focus),
            "context_variant_count": len(context),
            "unresolved_or_failed_count": len(unresolved),
            "local_region_class_counts": count_values(rows, "local_region_class"),
            "vep_consequence_counts": count_values(rows, "vep_most_severe_consequence"),
            "identity_counts": count_values(rows, "identity_match_class"),
            "transcript_concordance_counts": dict(Counter(transcript_concordance(row) for row in rows)),
            "clinvar_classification_counts": count_values(rows, "clinvar_normalized_classification"),
            "source_error_counts": dict(source_error_counts),
            "clinical_conflict_count": sum(1 for row in rows if as_bool(row.get("clinvar_conflict_flag"))),
            "statement": "Counts describe observed records and evidence availability; they do not imply effect, causality or diagnosis.",
        },
        "focus_variants": [compact_variant(row) for row in focus],
        "context_variants": {
            "count": len(context),
            "by_local_region": count_values(context, "local_region_class"),
            "by_consequence": count_values(context, "vep_most_severe_consequence"),
            "variant_refs_for_audit": [variant_ref(row) for row in context[:40]],
        },
        "unresolved_and_failed": {
            "count": len(unresolved),
            "by_identity": count_values(unresolved, "identity_match_class"),
            "by_transcript_concordance": dict(Counter(transcript_concordance(row) for row in unresolved)),
            "items": [
                {
                    "variant_ref": variant_ref(row),
                    "variant_key": clean(row.get("variant_key")),
                    "identity_match_class": clean(row.get("identity_match_class")),
                    "vep_status": clean(row.get("vep_status")),
                    "transcript_concordance": transcript_concordance(row),
                    "source_error_sources": clean(row.get("plus_source_error_sources")),
                }
                for row in unresolved[:100]
            ],
        },
        "provenance": {**provenance, "variant_detail_artifact": str(detail_path)},
    }


def v4_focus_rows(rows: list[dict], group_gwas_focus_keys: set[str] | None = None) -> list[dict]:
    eligible = [row for row in rows if row["focus_eligible"] and clean(row.get("downstream_role")) != "unresolved_review"]
    focus: list[dict] = []
    gwas_only = 0
    for row in eligible:
        clinvar_primary = clean(row.get("curated_clinvar_classification")) in {
            "pathogenic_or_likely_pathogenic", "conflicting_pathogenicity", "uncertain_significance", "risk_factor", "drug_response"
        }
        functional_primary = clean(row.get("local_region_class")) in {
            "mane_cds_overlap", "splice_region_candidate", "alternative_protein_coding_cds_overlap"
        }
        has_group_gwas_focus = (
            clean(row.get("variant_key")) in group_gwas_focus_keys
            if group_gwas_focus_keys is not None
            else as_int(row.get("curated_gwas_high_confidence_cluster_count")) > 0
        )
        is_gwas_only = has_group_gwas_focus and not (clinvar_primary or functional_primary)
        if is_gwas_only and gwas_only >= 6:
            continue
        if len(focus) < 12:
            focus.append(row)
            gwas_only += int(is_gwas_only)
            continue
        if len(focus) < 20 and row["attention_score"] == focus[11]["attention_score"]:
            focus.append(row)
            gwas_only += int(is_gwas_only)
            continue
        break
    return focus


def build_payload_v4(
    rows: list[dict],
    canonical: list[dict],
    mechanism: dict,
    provenance: dict,
    detail_path: Path,
    assertions: list[dict],
    gwas_clusters: list[dict],
    publications: list[dict],
) -> dict:
    base = build_payload(rows, canonical, mechanism, provenance, detail_path)
    prioritized_clusters = [row for row in gwas_clusters if clean(row.get("evidence_band")) == "high_confidence_replicated"]
    group_gwas_focus_keys = {
        item.strip()
        for cluster in prioritized_clusters
        for item in clean(cluster.get("variant_keys")).split("|")
        if item.strip()
    }
    focus = v4_focus_rows(rows, group_gwas_focus_keys)
    focus_keys = {clean(row.get("variant_key")) for row in focus}
    focus_assertions = [row for row in assertions if clean(row.get("variant_key")) in focus_keys]
    selected_pmids = {
        pmid
        for assertion in focus_assertions
        for pmid in re.findall(r"\d+", clean(assertion.get("pmids")))
    }
    selected_pmids.update(
        pmid.strip()
        for cluster in prioritized_clusters
        for pmid in clean(cluster.get("publication_ids")).split("|")
        if pmid.strip()
    )
    selected_publications = [
        {
            "pmid": clean(row.get("pmid")),
            "title": clean(row.get("title")),
            "publication_year": clean(row.get("publication_year")),
            "source_families": clean(row.get("source_families")),
            "retrieval_status": clean(row.get("retrieval_status")),
            "pmc_open_access": clean(row.get("pmc_open_access")),
        }
        for row in publications
        if clean(row.get("pmid")) in selected_pmids
    ]
    context = [
        row
        for row in rows
        if clean(row.get("variant_key")) not in focus_keys
        and row["focus_eligible"]
        and clean(row.get("downstream_role")) != "unresolved_review"
    ]
    unresolved = [row for row in rows if not row["focus_eligible"] or clean(row.get("downstream_role")) == "unresolved_review"]
    primary = [
        row
        for row in focus
        if clean(row.get("curated_clinvar_classification")) in NON_BENIGN_CLINVAR_CLASSES_V4
        or clean(row.get("local_region_class")) in {"mane_cds_overlap", "splice_region_candidate", "alternative_protein_coding_cds_overlap"}
    ]
    group_id = base["group_id"]
    payload_ready = bool(mechanism.get("usable_by_llm")) and not any(
        clean(row.get("identity_match_class")) not in CONFIRMED_IDENTITIES for row in focus
    )
    return {
        **base,
        "payload_schema_version": PAYLOAD_VERSION_V4,
        "genetic_facts": [genetic_fact(row) for row in focus],
        "scientific_evidence": [evidence_for_variant(row) for row in focus],
        "deterministic_summary": {
            **base["deterministic_summary"],
            "focus_variant_count": len(focus),
            "context_variant_count": len(context),
            "unresolved_or_failed_count": len(unresolved),
            "evidence_layer_counts": {
                "primary": len(primary),
                "prioritized_gwas_clusters": len(prioritized_clusters),
                "context_variants": len(context),
                "unresolved": len(unresolved),
            },
        },
        "focus_variants": [compact_variant(row) for row in focus],
        "context_variants": {
            "count": len(context),
            "benign_count": sum(clean(row.get("downstream_role")) == "benign_context" for row in context),
            "annotation_absent_count": sum(clean(row.get("downstream_role")) == "annotation_absent_context" for row in context),
            "pharmgkb_unconfirmed_context_count": sum(clean(row.get("source_evidence_status")) == "pharmacogenomic_context_unconfirmed" for row in context),
            "by_local_region": count_values(context, "local_region_class"),
            "variant_refs_for_audit": [variant_ref(row) for row in context[:40]],
        },
        "unresolved_and_failed": {
            "count": len(unresolved),
            "by_identity": count_values(unresolved, "identity_match_class"),
            "items": base["unresolved_and_failed"]["items"],
        },
        "evidence_layers": {
            "primary_variant_refs": [variant_ref(row) for row in primary],
            "prioritized_gwas_clusters": prioritized_clusters,
            "context_gwas_clusters": {
                "count": len(gwas_clusters) - len(prioritized_clusters),
                "by_evidence_band": dict(Counter(clean(row.get("evidence_band")) for row in gwas_clusters if row not in prioritized_clusters)),
                "trait_ids": [clean(row.get("trait_id")) for row in gwas_clusters if row not in prioritized_clusters][:40],
                "cluster_ids_for_audit": [clean(row.get("cluster_id")) for row in gwas_clusters if row not in prioritized_clusters][:40],
                "complete_artifact": "gwas_evidence_clusters.csv",
            },
            "clinvar_assertions": focus_assertions,
            "publication_records": selected_publications,
            "limitations": [
                "GWAS associations are population-level context and do not establish individual causality.",
                "PharmGKB base records have unconfirmed observed-allele applicability.",
                "Unresolved identities are retained for audit and cannot become focus variants.",
            ],
        },
        "gates": {
            "technical_pipeline_ready": True,
            "annotation_ready": not any(clean(row.get("vep_status")) == "source_error" for row in focus),
            "evidence_curation_ready": True,
            "mechanism_registry_ready": bool(mechanism.get("usable_by_llm")),
            "group_payload_ready": payload_ready,
            "llm1_pilot_ready": False,
        },
        "provenance": {**base["provenance"], "payload_hash_scope": group_id},
    }


NON_BENIGN_CLINVAR_CLASSES_V4 = {
    "pathogenic_or_likely_pathogenic",
    "uncertain_significance",
    "conflicting_pathogenicity",
    "risk_factor",
    "drug_response",
    "other_or_association",
}


def pilot_stratum(payload: dict) -> str:
    layer_counts = payload.get("deterministic_summary", {}).get("evidence_layer_counts", {})
    if as_int(layer_counts.get("primary")) > 0:
        return "functional_or_clinvar_strong"
    if as_int(layer_counts.get("prioritized_gwas_clusters")) > 0:
        return "gwas_replicated_relevant"
    if as_int(payload.get("context_variants", {}).get("pharmgkb_unconfirmed_context_count")) > 0:
        return "pharmgkb_contextual"
    if as_int(payload.get("context_variants", {}).get("benign_count")) > 0 or payload.get("focus_variants"):
        return "benign_or_functional_only"
    return "abstention_or_conflict"


def select_pilot_candidates(payloads: list[dict]) -> list[dict]:
    quotas = {
        "functional_or_clinvar_strong": 6,
        "gwas_replicated_relevant": 5,
        "pharmgkb_contextual": 3,
        "benign_or_functional_only": 3,
        "abstention_or_conflict": 3,
    }
    buckets: dict[str, list[dict]] = defaultdict(list)
    for payload in payloads:
        if payload.get("gates", {}).get("group_payload_ready"):
            buckets[pilot_stratum(payload)].append(payload)
    selected = []
    for stratum, limit in quotas.items():
        selected.extend(sorted(buckets[stratum], key=lambda row: row["group_id"])[:limit])
    return selected


def process(
    input_path: Path,
    output_dir: Path,
    canonical_path: Path | None = None,
    mechanism_path: Path | None = None,
    provenance_path: Path | None = None,
    physical_path: Path | None = None,
    clinvar_assertions_path: Path | None = None,
    gwas_clusters_path: Path | None = None,
    publications_path: Path | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    projection_rows = read_csv(input_path)
    if projection_rows and "triage_universe" in projection_rows[0]:
        projection_rows = [row for row in projection_rows if as_bool(row.get("triage_universe"))]
    source_rows = merge_projection_with_physical(projection_rows, read_csv(physical_path))
    if not source_rows:
        raise ValueError("Enrichment module projection CSV is empty.")
    required = {"variant_key", "approved_symbol", "module_id", "local_region_class", "identity_match_class", "vep_status"}
    missing = sorted(required - set(source_rows[0]))
    if missing:
        raise ValueError(f"Grouping prep v3 missing required fields: {', '.join(missing)}")

    canonical_rows = read_csv(canonical_path)
    mechanism_rows = read_csv(mechanism_path)
    group_keys = sorted({(clean(row.get("approved_symbol")), clean(row.get("module_id"))) for row in source_rows if clean(row.get("approved_symbol")) and clean(row.get("module_id"))})
    mechanism_rows = mechanism_registry_rows(group_keys, mechanism_rows)
    mechanism_registry_path = output_dir / "mechanism_registry_v1.csv"
    write_csv(mechanism_registry_path, mechanism_rows, list(mechanism_rows[0]) if mechanism_rows else ["gene", "module_id"])
    assertion_rows = read_csv(clinvar_assertions_path)
    gwas_cluster_rows = read_csv(gwas_clusters_path)
    publication_rows = read_csv(publications_path)
    canonical_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in canonical_rows:
        canonical_index[(clean(row.get("gene") or row.get("approved_symbol")), clean(row.get("module_id")))].append(row)
    mechanism_index = {(clean(row.get("gene")), clean(row.get("module_id"))): row for row in mechanism_rows}
    provenance = {
        "generated_at": utc_now(),
        "input_sha256": sha256(input_path),
        "canonical_status_sha256": sha256(canonical_path),
        "mechanism_registry_sha256": sha256(mechanism_registry_path),
        "pipeline_version": PAYLOAD_VERSION,
    }
    if provenance_path and provenance_path.exists():
        provenance["run_provenance"] = json.loads(provenance_path.read_text(encoding="utf-8"))

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    detail_rows = []
    for row in source_rows:
        key = (clean(row.get("approved_symbol")), clean(row.get("module_id")))
        if not all(key):
            continue
        score, reasons, eligible = attention_score(row)
        prepared = {**row, "attention_score": score, "attention_reasons": reasons, "focus_eligible": eligible}
        groups[key].append(prepared)

    detail_path = output_dir / "gene_module_group_variant_detail_v3.csv"
    payloads = []
    payloads_v4 = []
    summary_rows = []
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (-row["focus_eligible"], -row["attention_score"], as_int(row.get("variant_start") or row.get("pos_vcf"), 10**15), variant_ref(row)))
        payload = build_payload(rows, canonical_index.get(key, []), mechanism_for_group(mechanism_index, key), provenance, detail_path)
        payloads.append(payload)
        variant_keys = {clean(row.get("variant_key")) for row in rows}
        group_assertions = [row for row in assertion_rows if clean(row.get("variant_key")) in variant_keys]
        group_clusters = [row for row in gwas_cluster_rows if clean(row.get("approved_symbol")) == key[0] and clean(row.get("module_id")) == key[1]]
        group_publications = [
            row
            for row in publication_rows
            if variant_keys.intersection({item.strip() for item in clean(row.get("variant_keys")).split("|") if item.strip()})
        ]
        payload_v4 = build_payload_v4(
            rows,
            canonical_index.get(key, []),
            mechanism_for_group(mechanism_index, key),
            provenance,
            output_dir / "gene_module_group_variant_detail_v4.csv",
            group_assertions,
            group_clusters,
            group_publications,
        )
        payloads_v4.append(payload_v4)
        for rank, row in enumerate(rows, 1):
            detail_rows.append({
                **row,
                "group_id": payload["group_id"],
                "attention_rank": rank,
                "attention_reasons_json": json.dumps(row["attention_reasons"], ensure_ascii=False),
                "focus_eligible": str(row["focus_eligible"]).lower(),
                "transcript_concordance": transcript_concordance(row),
            })
        summary_rows.append({
            "group_id": payload["group_id"],
            "gene": key[0],
            "module_id": key[1],
            "group_size_total": len(rows),
            "focus_variant_count": payload["deterministic_summary"]["focus_variant_count"],
            "context_variant_count": payload["deterministic_summary"]["context_variant_count"],
            "unresolved_or_failed_count": payload["deterministic_summary"]["unresolved_or_failed_count"],
            "mechanism_curation_status": payload["curated_mechanisms"]["curation_status"],
            "payload_ready": str(payload["curated_mechanisms"]["usable_by_llm"] and not payload["unresolved_and_failed"]["count"]).lower(),
            "payload_v4_ready": str(payload_v4["gates"]["group_payload_ready"]).lower(),
        })

    jsonl_path = output_dir / "gene_module_group_payloads_v3.jsonl"
    csv_path = output_dir / "gene_module_group_payloads_v3.csv"
    summary_path = output_dir / "gene_module_grouping_summary_v3.json"
    jsonl_v4_path = output_dir / "gene_module_group_payloads_v4.jsonl"
    csv_v4_path = output_dir / "gene_module_group_payloads_v4.csv"
    detail_v4_path = output_dir / "gene_module_group_variant_detail_v4.csv"
    summary_v4_path = output_dir / "gene_module_grouping_summary_v4.json"
    pilot_manifest_path = output_dir / "llm1_pilot_manifest_v1.csv"
    write_jsonl(jsonl_path, payloads)
    write_jsonl(jsonl_v4_path, payloads_v4)
    write_csv(csv_path, [{**row, "payload_json": json.dumps(payloads[index], ensure_ascii=False, separators=(",", ":"))} for index, row in enumerate(summary_rows)], list(summary_rows[0]) + ["payload_json"])
    detail_fields = list(source_rows[0]) + ["group_id", "attention_score", "attention_rank", "attention_reasons_json", "focus_eligible", "transcript_concordance"]
    write_csv(detail_path, detail_rows, detail_fields)
    write_csv(detail_v4_path, detail_rows, detail_fields)
    write_csv(
        csv_v4_path,
        [{**row, "payload_json": json.dumps(payloads_v4[index], ensure_ascii=False, separators=(",", ":"))} for index, row in enumerate(summary_rows)],
        list(summary_rows[0]) + ["payload_json"],
    )
    pilot_candidates = select_pilot_candidates(payloads_v4)
    pilot_rows = [
        {
            "group_id": payload["group_id"],
            "gene": payload["gene"],
            "module_id": payload["module_id"],
            "approved_for_pilot": "false",
            "selection_category": pilot_stratum(payload),
            "approval_reviewer": "",
            "approval_timestamp": "",
        }
        for payload in pilot_candidates[:20]
    ]
    write_csv(
        pilot_manifest_path,
        pilot_rows,
        ["group_id", "gene", "module_id", "approved_for_pilot", "selection_category", "approval_reviewer", "approval_timestamp"],
    )
    metadata = {
        "source_rows": len(source_rows),
        "source_variants_total": len({clean(row.get("variant_key")) for row in source_rows}),
        "total_groups": len(payloads),
        "average_group_size": round(len(source_rows) / max(len(payloads), 1), 2),
        "groups_gt_25": sum(len(rows) > 25 for rows in groups.values()),
        "focus_variants_total": sum(row["focus_variant_count"] for row in summary_rows),
        "context_variants_total": sum(row["context_variant_count"] for row in summary_rows),
        "unresolved_or_failed_total": sum(row["unresolved_or_failed_count"] for row in summary_rows),
        "groups_with_approved_mechanism": sum(1 for row in summary_rows if row["mechanism_curation_status"] == "approved"),
        "groups_payload_ready": sum(1 for row in summary_rows if row["payload_ready"] == "true"),
        "groups_payload_v4_ready": sum(1 for row in summary_rows if row["payload_v4_ready"] == "true"),
        "focus_variants_v4_total": sum(len(payload["focus_variants"]) for payload in payloads_v4),
        "context_variants_v4_total": sum(payload["context_variants"]["count"] for payload in payloads_v4),
        "unresolved_or_failed_v4_total": sum(payload["unresolved_and_failed"]["count"] for payload in payloads_v4),
        "pilot_candidates": len(pilot_rows),
        "llm_calls": 0,
        "dry_run_only": True,
    }
    summary = {
        "status": "valid",
        "schemaVersion": "gene_module_v2",
        "payloadSchemaVersion": PAYLOAD_VERSION,
        "dryRunOnly": True,
        "metadata": metadata,
        "outputs": {
            "groupPayloadsJsonl": str(jsonl_path),
            "groupPayloadsCsv": str(csv_path),
            "groupVariantDetailCsv": str(detail_path),
            "groupingSummaryJson": str(summary_path),
        },
        "gates": {
            "groupPayloadReady": "pass" if metadata["groups_payload_ready"] == len(payloads) else "fail",
            "llm1PilotReady": "blocked",
        },
        "timestamps": {"completedAt": utc_now()},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_v4 = {
        **summary,
        "payloadSchemaVersion": PAYLOAD_VERSION_V4,
        "outputs": {
            **summary["outputs"],
            "groupPayloadsJsonlV4": str(jsonl_v4_path),
            "groupPayloadsCsvV4": str(csv_v4_path),
            "groupVariantDetailCsvV4": str(detail_v4_path),
            "groupingSummaryJsonV4": str(summary_v4_path),
            "mechanismRegistryV1Csv": str(mechanism_registry_path),
            "llm1PilotManifestCsv": str(pilot_manifest_path),
        },
        "gates": {
            "technicalPipelineReady": "pass",
            "annotationReady": "pass" if all(payload["gates"]["annotation_ready"] for payload in payloads_v4) else "review_required",
            "evidenceCurationReady": "pass",
            "mechanismRegistryReady": "pass" if all(payload["gates"]["mechanism_registry_ready"] for payload in payloads_v4) else "blocked",
            "groupPayloadReady": "pass" if all(payload["gates"]["group_payload_ready"] for payload in payloads_v4) else "blocked",
            "llm1PilotReady": "blocked",
            "llm1PilotReason": "pending_explicit_manifest_approval",
        },
    }
    summary_v4_path.write_text(json.dumps(summary_v4, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary_v4, ensure_ascii=False))
    return summary_v4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare dry-run HEAL v2 grouped LLM1 payloads v3.")
    parser.add_argument("--input")
    parser.add_argument("--output-dir")
    parser.add_argument("--canonical-status")
    parser.add_argument("--mechanism-registry")
    parser.add_argument("--provenance")
    parser.add_argument("--physical-matrix")
    parser.add_argument("--clinvar-assertions")
    parser.add_argument("--gwas-clusters")
    parser.add_argument("--publications")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("enrichmentPlusCsv")
        args.output_dir = payload.get("outputDir")
        args.canonical_status = payload.get("canonicalStatusPath")
        args.mechanism_registry = payload.get("mechanismRegistryPath")
        args.provenance = payload.get("provenancePath")
        args.physical_matrix = payload.get("physicalMatrixPath")
        args.clinvar_assertions = payload.get("clinvarAssertionsPath")
        args.gwas_clusters = payload.get("gwasClustersPath")
        args.publications = payload.get("publicationsPath")
    if not args.input or not args.output_dir:
        parser.error("--input and --output-dir are required")
    return args


def main() -> int:
    args = parse_args()
    process(
        Path(args.input),
        Path(args.output_dir),
        Path(args.canonical_status) if args.canonical_status else None,
        Path(args.mechanism_registry) if args.mechanism_registry else None,
        Path(args.provenance) if args.provenance else None,
        Path(args.physical_matrix) if args.physical_matrix else None,
        Path(args.clinvar_assertions) if args.clinvar_assertions else None,
        Path(args.gwas_clusters) if args.gwas_clusters else None,
        Path(args.publications) if args.publications else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
