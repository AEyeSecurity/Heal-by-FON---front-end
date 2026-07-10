#!/usr/bin/env python3
"""Prepare grouped gene+module payloads for HEAL canon v2 grouped LLM1."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path


BASE_SCORES = {
    "mane_cds_overlap": 100,
    "splice_region_candidate": 95,
    "alternative_protein_coding_cds_overlap": 90,
    "protein_coding_exon_non_cds_overlap": 80,
    "utr_overlap": 60,
}

PATHOGENIC_ALPHAMISSENSE_TERMS = {
    "pathogenic",
    "likely_pathogenic",
    "damaging",
    "likely_damaging",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def is_missing(value) -> bool:
    return value is None or str(value).strip() == "" or str(value).strip().lower() in {"nan", "none", "<na>"}


def clean_str(value) -> str:
    return "" if is_missing(value) else str(value).strip()


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return clean_str(value).lower() in {"true", "1", "yes", "y"}


def as_float(value, default: float = 0.0) -> float:
    text = clean_str(value)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def as_int(value, default: int = 0) -> int:
    try:
        return int(float(clean_str(value) or default))
    except ValueError:
        return default


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def variant_ref(row: dict) -> str:
    rsid = clean_str(row.get("SNP (rsID)") or row.get("id_vcf"))
    start = clean_str(row.get("variant_start") or row.get("pos_vcf"))
    chrom = clean_str(row.get("chrom_vcf"))
    ref = clean_str(row.get("ref_vcf"))
    alt = clean_str(row.get("alt_vcf"))
    if rsid and rsid != ".":
        return rsid
    if chrom and start:
        return f"{chrom}:{start}:{ref}>{alt}"
    return clean_str(row.get("variant_gene_module_id")) or "variant"


def base_attention_score(local_region_class: str) -> int:
    return BASE_SCORES.get(local_region_class, 40)


def attention_adjustments(row: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    clinvar = clean_str(row.get("clinvar_normalized_classification"))
    if clinvar == "conflicting_pathogenicity":
        score += 40
        reasons.append("clinvar_conflicting_pathogenicity")
    elif clinvar == "pathogenic_or_likely_pathogenic":
        score += 35
        reasons.append("clinvar_pathogenic_or_likely_pathogenic")
    elif clinvar == "drug_response":
        score += 30
        reasons.append("clinvar_drug_response")
    elif clinvar == "risk_factor":
        score += 20
        reasons.append("clinvar_risk_factor")
    elif clinvar == "benign_or_likely_benign":
        score += 5
        reasons.append("clinvar_benign_or_likely_benign")

    if as_int(row.get("pharmgkb_clinical_annotation_count")) > 0:
        score += 25
        reasons.append("pharmgkb_clinical_annotation_count")
    if as_int(row.get("pharmgkb_variant_annotation_count")) > 0:
        score += 10
        reasons.append("pharmgkb_variant_annotation_count")

    if clean_str(row.get("vep_spliceai")):
        score += 20
        reasons.append("vep_spliceai")
    if clean_str(row.get("vep_hgvsp")):
        score += 15
        reasons.append("vep_hgvsp")

    cadd = as_float(row.get("vep_cadd_phred"))
    if cadd >= 20:
        score += 15
        reasons.append("vep_cadd_phred_gte_20")
    elif cadd >= 10:
        score += 8
        reasons.append("vep_cadd_phred_gte_10")

    revel = as_float(row.get("vep_revel_score"))
    if revel >= 0.75:
        score += 15
        reasons.append("vep_revel_score_gte_0_75")
    elif revel >= 0.50:
        score += 8
        reasons.append("vep_revel_score_gte_0_50")

    alphamissense = clean_str(row.get("vep_alphamissense_pred")).lower()
    if any(term in alphamissense for term in PATHOGENIC_ALPHAMISSENSE_TERMS):
        score += 10
        reasons.append("vep_alphamissense_pred")

    if as_int(row.get("gwas_association_count")) > 0:
        score += 5
        reasons.append("gwas_association_count")
    min_pvalue = as_float(row.get("gwas_min_pvalue"), default=1.0)
    if min_pvalue > 0 and min_pvalue <= 5e-8:
        score += 5
        reasons.append("gwas_min_pvalue_lte_5e_8")

    if as_float(row.get("population_max_frequency")) >= 0.05:
        score -= 10
        reasons.append("population_max_frequency_gte_0_05")
    return score, reasons


def attention_score(row: dict) -> tuple[int, list[str]]:
    local_region_class = clean_str(row.get("local_region_class"))
    base_score = base_attention_score(local_region_class)
    delta, reasons = attention_adjustments(row)
    reasons = [f"base:{local_region_class or 'other'}={base_score}"] + reasons
    return base_score + delta, reasons


def local_sort_key(row: dict) -> tuple:
    start = as_int(row.get("variant_start") or row.get("pos_vcf"), default=10**15)
    rsid = clean_str(row.get("SNP (rsID)") or row.get("id_vcf")) or "~"
    ref_alt = f"{clean_str(row.get('ref_vcf'))}>{clean_str(row.get('alt_vcf'))}"
    return (-as_int(row.get("attention_score")), start, rsid, ref_alt)


def counter_dict(values) -> dict[str, int]:
    return dict(Counter(value for value in values if clean_str(value)))


def pgx_signal_present(row: dict) -> bool:
    return as_int(row.get("pharmgkb_clinical_annotation_count")) > 0 or as_int(row.get("pharmgkb_variant_annotation_count")) > 0


def strong_vep_flags(row: dict) -> list[str]:
    flags = []
    if clean_str(row.get("vep_hgvsp")):
        flags.append("hgvsp")
    if clean_str(row.get("vep_spliceai")):
        flags.append("spliceai")
    if as_float(row.get("vep_cadd_phred")) >= 20:
        flags.append("cadd_gte_20")
    if as_float(row.get("vep_revel_score")) >= 0.75:
        flags.append("revel_gte_0_75")
    return flags


def focus_rows_for_group(rows: list[dict]) -> list[dict]:
    if len(rows) <= 25:
        return list(rows)
    focus = list(rows[:12])
    if len(rows) <= 12:
        return focus
    threshold = as_int(rows[11].get("attention_score"))
    for row in rows[12:]:
        if as_int(row.get("attention_score")) != threshold or len(focus) >= 20:
            break
        focus.append(row)
    return focus


def build_focus_variant(row: dict) -> dict:
    return {
        "variant_ref": variant_ref(row),
        "variant_gene_module_id": clean_str(row.get("variant_gene_module_id")),
        "variant_start": clean_str(row.get("variant_start") or row.get("pos_vcf")),
        "variant_end": clean_str(row.get("variant_end")),
        "chrom_vcf": clean_str(row.get("chrom_vcf")),
        "ref_vcf": clean_str(row.get("ref_vcf")),
        "alt_vcf": clean_str(row.get("alt_vcf")),
        "zygosity": clean_str(row.get("zygosity")),
        "gt_alleles": clean_str(row.get("gt_alleles")),
        "local_region_class": clean_str(row.get("local_region_class")),
        "local_feature_priority": clean_str(row.get("local_feature_priority")),
        "attention_score": as_int(row.get("attention_score")),
        "attention_reasons": row.get("attention_reasons") or [],
        "clinvar_normalized_classification": clean_str(row.get("clinvar_normalized_classification")),
        "clinvar_conflict_flag": clean_str(row.get("clinvar_conflict_flag")),
        "population_max_frequency": clean_str(row.get("population_max_frequency")),
        "vep_hgvsp": clean_str(row.get("vep_hgvsp")),
        "vep_spliceai": clean_str(row.get("vep_spliceai")),
        "vep_cadd_phred": clean_str(row.get("vep_cadd_phred")),
        "vep_revel_score": clean_str(row.get("vep_revel_score")),
        "vep_alphamissense_pred": clean_str(row.get("vep_alphamissense_pred")),
        "pharmgkb_clinical_annotation_count": clean_str(row.get("pharmgkb_clinical_annotation_count")),
        "pharmgkb_variant_annotation_count": clean_str(row.get("pharmgkb_variant_annotation_count")),
        "gwas_association_count": clean_str(row.get("gwas_association_count")),
        "gwas_min_pvalue": clean_str(row.get("gwas_min_pvalue")),
        "triage_reason": clean_str(row.get("triage_reason")),
    }


def build_payload(group_rows: list[dict], output_dir: Path) -> tuple[dict, dict]:
    first = group_rows[0]
    sorted_rows = sorted(group_rows, key=local_sort_key)
    focus_rows = focus_rows_for_group(sorted_rows)
    focus_ids = {clean_str(row.get("variant_gene_module_id")) for row in focus_rows}
    remaining_rows = [row for row in sorted_rows if clean_str(row.get("variant_gene_module_id")) not in focus_ids]
    group_id = f"{clean_str(first.get('approved_symbol'))}:{clean_str(first.get('module_id'))}"

    focus_variants = [build_focus_variant(row) for row in focus_rows]
    appendix = {
        "remaining_local_region_class_counts": counter_dict(clean_str(row.get("local_region_class")) for row in remaining_rows),
        "remaining_clinvar_classification_counts": counter_dict(clean_str(row.get("clinvar_normalized_classification")) for row in remaining_rows),
        "remaining_zygosity_counts": counter_dict(clean_str(row.get("zygosity")) for row in remaining_rows),
        "remaining_pgx_signal_counts": {
            "with_pgx_signal": sum(1 for row in remaining_rows if pgx_signal_present(row)),
            "without_pgx_signal": sum(1 for row in remaining_rows if not pgx_signal_present(row)),
        },
        "remaining_strong_vep_signal_counts": {
            "hgvsp": sum(1 for row in remaining_rows if clean_str(row.get("vep_hgvsp"))),
            "spliceai": sum(1 for row in remaining_rows if clean_str(row.get("vep_spliceai"))),
            "cadd_gte_20": sum(1 for row in remaining_rows if as_float(row.get("vep_cadd_phred")) >= 20),
            "revel_gte_0_75": sum(1 for row in remaining_rows if as_float(row.get("vep_revel_score")) >= 0.75),
        },
        "remaining_variant_refs": [variant_ref(row) for row in remaining_rows[:40]],
    }
    payload = {
        "group_id": group_id,
        "gene": clean_str(first.get("approved_symbol")),
        "module_id": clean_str(first.get("module_id")),
        "module_name": clean_str(first.get("module_name")),
        "system_within_module": clean_str(first.get("system_within_module")),
        "tier": clean_str(first.get("tier")),
        "module_status": clean_str(first.get("module_status")),
        "evidence_tier": clean_str(first.get("evidence_tier")),
        "group_size_total": len(sorted_rows),
        "focus_variant_count": len(focus_rows),
        "summarized_variant_count": len(remaining_rows),
        "variant_detail_artifact": str(output_dir / "gene_module_group_variant_detail.csv"),
        "group_counts": {
            "local_region_class_counts": counter_dict(clean_str(row.get("local_region_class")) for row in sorted_rows),
            "clinvar_classification_counts": counter_dict(clean_str(row.get("clinvar_normalized_classification")) for row in sorted_rows),
            "zygosity_counts": counter_dict(clean_str(row.get("zygosity")) for row in sorted_rows),
            "attention_score_bins": {
                "gte_120": sum(1 for row in sorted_rows if as_int(row.get("attention_score")) >= 120),
                "100_119": sum(1 for row in sorted_rows if 100 <= as_int(row.get("attention_score")) < 120),
                "80_99": sum(1 for row in sorted_rows if 80 <= as_int(row.get("attention_score")) < 100),
                "lt_80": sum(1 for row in sorted_rows if as_int(row.get("attention_score")) < 80),
            },
            "pgx_signal_rows": sum(1 for row in sorted_rows if pgx_signal_present(row)),
            "clinvar_conflict_rows": sum(1 for row in sorted_rows if clean_str(row.get("clinvar_normalized_classification")) == "conflicting_pathogenicity"),
            "population_common_rows": sum(1 for row in sorted_rows if as_float(row.get("population_max_frequency")) >= 0.05),
        },
        "focus_variants": focus_variants,
        "appendix_partial": appendix,
    }
    payload_csv_row = {
        "group_id": group_id,
        "gene": payload["gene"],
        "module_id": payload["module_id"],
        "module_name": payload["module_name"],
        "system_within_module": payload["system_within_module"],
        "tier": payload["tier"],
        "module_status": payload["module_status"],
        "evidence_tier": payload["evidence_tier"],
        "group_size_total": len(sorted_rows),
        "focus_variant_count": len(focus_rows),
        "summarized_variant_count": len(remaining_rows),
        "max_attention_score": max(as_int(row.get("attention_score")) for row in sorted_rows),
        "average_attention_score": round(sum(as_int(row.get("attention_score")) for row in sorted_rows) / len(sorted_rows), 2),
        "focus_variant_refs": "|".join(item["variant_ref"] for item in focus_variants),
        "remaining_variant_refs": "|".join(appendix["remaining_variant_refs"]),
        "payload_json": compact_json(payload),
    }
    return payload, payload_csv_row


def process(input_path: Path, output_dir: Path) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(input_path)
    if not rows:
        raise ValueError("Enrichment Plus CSV is empty.")
    required = {"approved_symbol", "module_id", "local_region_class"}
    missing = sorted(field for field in required if field not in rows[0])
    if missing:
        raise ValueError(f"Grouping prep requires gene_module_v2 enrichment rows with fields: {', '.join(missing)}")

    prepared_rows = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        group_gene = clean_str(row.get("approved_symbol"))
        group_module = clean_str(row.get("module_id"))
        if not group_gene or not group_module:
            continue
        score, reasons = attention_score(row)
        prepared = {
            **row,
            "group_id": f"{group_gene}:{group_module}",
            "attention_score": str(score),
            "attention_reasons": compact_json(reasons),
            "variant_ref": variant_ref(row),
        }
        prepared_rows.append(prepared)
        groups[(group_gene, group_module)].append({**row, "attention_score": score, "attention_reasons": reasons})

    if not groups:
        raise ValueError("Grouping prep found no eligible gene+module rows.")

    payload_rows = []
    payload_csv_rows = []
    detail_rows = []
    group_sizes = []
    groups_gt_25 = 0
    for _, group_rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        sorted_rows = sorted(group_rows, key=local_sort_key)
        focus_rows = focus_rows_for_group(sorted_rows)
        focus_ids = {clean_str(row.get("variant_gene_module_id")) for row in focus_rows}
        if len(sorted_rows) > 25:
            groups_gt_25 += 1
        group_sizes.append(len(sorted_rows))
        for rank, row in enumerate(sorted_rows, start=1):
            detail_rows.append(
                {
                    **row,
                    "group_id": f"{clean_str(row.get('approved_symbol'))}:{clean_str(row.get('module_id'))}",
                    "attention_score": str(as_int(row.get("attention_score"))),
                    "attention_rank": str(rank),
                    "is_focus_variant": "true" if clean_str(row.get("variant_gene_module_id")) in focus_ids else "false",
                    "attention_reasons": compact_json(row.get("attention_reasons") or []),
                    "variant_ref": variant_ref(row),
                    "strong_vep_flags": "|".join(strong_vep_flags(row)),
                    "pgx_signal_present": "true" if pgx_signal_present(row) else "false",
                }
            )
        payload, payload_csv = build_payload(sorted_rows, output_dir)
        payload_rows.append(payload)
        payload_csv_rows.append(payload_csv)

    payload_jsonl = output_dir / "gene_module_group_payloads.jsonl"
    payload_csv = output_dir / "gene_module_group_payloads.csv"
    detail_csv = output_dir / "gene_module_group_variant_detail.csv"
    summary_json = output_dir / "gene_module_grouping_summary.json"
    write_jsonl(payload_jsonl, payload_rows)
    write_csv(
        payload_csv,
        payload_csv_rows,
        [
            "group_id",
            "gene",
            "module_id",
            "module_name",
            "system_within_module",
            "tier",
            "module_status",
            "evidence_tier",
            "group_size_total",
            "focus_variant_count",
            "summarized_variant_count",
            "max_attention_score",
            "average_attention_score",
            "focus_variant_refs",
            "remaining_variant_refs",
            "payload_json",
        ],
    )
    write_csv(
        detail_csv,
        detail_rows,
        list(rows[0].keys())
        + [
            "group_id",
            "attention_score",
            "attention_rank",
            "is_focus_variant",
            "attention_reasons",
            "variant_ref",
            "strong_vep_flags",
            "pgx_signal_present",
        ],
    )

    metadata = {
        "source_rows": len(rows),
        "prepared_rows": len(prepared_rows),
        "total_groups": len(payload_rows),
        "source_variants_total": len(detail_rows),
        "average_group_size": round(sum(group_sizes) / len(group_sizes), 2),
        "max_group_size": max(group_sizes),
        "groups_with_single_variant": sum(1 for size in group_sizes if size == 1),
        "groups_gt_25": groups_gt_25,
        "focus_variants_total": sum(int(row["focus_variant_count"]) for row in payload_csv_rows),
        "summarized_variants_total": sum(int(row["summarized_variant_count"]) for row in payload_csv_rows),
        "largest_groups": [
            {
                "group_id": row["group_id"],
                "group_size_total": int(row["group_size_total"]),
                "focus_variant_count": int(row["focus_variant_count"]),
            }
            for row in sorted(payload_csv_rows, key=lambda item: int(item["group_size_total"]), reverse=True)[:10]
        ],
    }
    summary = {
        "status": "valid",
        "errors": [],
        "warnings": [],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "schemaVersion": "gene_module_v2",
        "metadata": metadata,
        "outputs": {
            "groupPayloadsJsonl": str(payload_jsonl),
            "groupPayloadsCsv": str(payload_csv),
            "groupVariantDetailCsv": str(detail_csv),
            "groupingSummaryJson": str(summary_json),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare grouped gene+module payloads for HEAL canon v2.")
    parser.add_argument("--input")
    parser.add_argument("--output-dir")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("enrichmentPlusCsv")
        args.output_dir = payload.get("outputDir")
    if not args.input or not args.output_dir:
        parser.error("--input and --output-dir are required.")
    return args


def main() -> int:
    args = parse_args()
    process(Path(args.input), Path(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
