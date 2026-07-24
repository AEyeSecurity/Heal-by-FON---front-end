#!/usr/bin/env python3
"""Build transcript-aware HEAL v2 gene-module payloads without invoking an LLM."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


PAYLOAD_VERSION = "llm1_group_payload_v3"
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


def process(input_path: Path, output_dir: Path, canonical_path: Path | None = None, mechanism_path: Path | None = None, provenance_path: Path | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_rows = read_csv(input_path)
    if not source_rows:
        raise ValueError("Enrichment module projection CSV is empty.")
    required = {"variant_key", "approved_symbol", "module_id", "local_region_class", "identity_match_class", "vep_status"}
    missing = sorted(required - set(source_rows[0]))
    if missing:
        raise ValueError(f"Grouping prep v3 missing required fields: {', '.join(missing)}")

    canonical_rows = read_csv(canonical_path)
    mechanism_rows = read_csv(mechanism_path)
    canonical_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in canonical_rows:
        canonical_index[(clean(row.get("gene")), clean(row.get("module_id")))].append(row)
    mechanism_index = {(clean(row.get("gene")), clean(row.get("module_id"))): row for row in mechanism_rows}
    provenance = {
        "generated_at": utc_now(),
        "input_sha256": sha256(input_path),
        "canonical_status_sha256": sha256(canonical_path),
        "mechanism_registry_sha256": sha256(mechanism_path),
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
    summary_rows = []
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (-row["focus_eligible"], -row["attention_score"], as_int(row.get("variant_start") or row.get("pos_vcf"), 10**15), variant_ref(row)))
        payload = build_payload(rows, canonical_index.get(key, []), mechanism_for_group(mechanism_index, key), provenance, detail_path)
        payloads.append(payload)
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
        })

    jsonl_path = output_dir / "gene_module_group_payloads_v3.jsonl"
    csv_path = output_dir / "gene_module_group_payloads_v3.csv"
    summary_path = output_dir / "gene_module_grouping_summary_v3.json"
    write_jsonl(jsonl_path, payloads)
    write_csv(csv_path, [{**row, "payload_json": json.dumps(payloads[index], ensure_ascii=False, separators=(",", ":"))} for index, row in enumerate(summary_rows)], list(summary_rows[0]) + ["payload_json"])
    detail_fields = list(source_rows[0]) + ["group_id", "attention_score", "attention_rank", "attention_reasons_json", "focus_eligible", "transcript_concordance"]
    write_csv(detail_path, detail_rows, detail_fields)
    metadata = {
        "source_rows": len(source_rows),
        "total_groups": len(payloads),
        "focus_variants_total": sum(row["focus_variant_count"] for row in summary_rows),
        "context_variants_total": sum(row["context_variant_count"] for row in summary_rows),
        "unresolved_or_failed_total": sum(row["unresolved_or_failed_count"] for row in summary_rows),
        "groups_with_approved_mechanism": sum(1 for row in summary_rows if row["mechanism_curation_status"] == "approved"),
        "groups_payload_ready": sum(1 for row in summary_rows if row["payload_ready"] == "true"),
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
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare dry-run HEAL v2 grouped LLM1 payloads v3.")
    parser.add_argument("--input")
    parser.add_argument("--output-dir")
    parser.add_argument("--canonical-status")
    parser.add_argument("--mechanism-registry")
    parser.add_argument("--provenance")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("enrichmentPlusCsv")
        args.output_dir = payload.get("outputDir")
        args.canonical_status = payload.get("canonicalStatusPath")
        args.mechanism_registry = payload.get("mechanismRegistryPath")
        args.provenance = payload.get("provenancePath")
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
