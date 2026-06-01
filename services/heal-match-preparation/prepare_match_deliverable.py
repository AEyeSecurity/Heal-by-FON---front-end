#!/usr/bin/env python3
"""Prepare HEAL VCF-canon match output for audit and downstream interpretation."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path


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


def is_missing(value) -> bool:
    return value is None or str(value).strip() == "" or str(value).strip().lower() in {"nan", "none", "<na>"}


def clean_str(value) -> str:
    return "" if is_missing(value) else str(value).strip()


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def genotype_for_deliverable(gt_alleles: str) -> str:
    text = clean_str(gt_alleles)
    if not text:
        return "NA"
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if not parts:
        return "NA"
    if all(len(part) == 1 for part in parts):
        return "".join(parts)
    return text


def ref_alt_for_deliverable(row: dict) -> str:
    ref_vcf = clean_str(row.get("ref_vcf"))
    alt_vcf = clean_str(row.get("alt_vcf"))
    ref_resolved = clean_str(row.get("ref"))
    alt_resolved = clean_str(row.get("alt"))
    if ref_vcf and alt_vcf:
        return f"{ref_vcf}/{alt_vcf}"
    if ref_resolved and alt_resolved:
        return f"{ref_resolved}/{alt_resolved}"
    return "NA"


def confidence_level(row: dict) -> str:
    match_status = clean_str(row.get("match_status"))
    source_group = clean_str(row.get("source_group"))
    has_genotype = as_bool(row.get("has_genotype"))
    zygosity = clean_str(row.get("zygosity"))

    if match_status == "match_strict" and has_genotype:
        if source_group == "revision_manual" or zygosity == "non_diploid_or_complex":
            return "Moderate"
        return "High"
    if match_status == "match_likely_needs_alt_review" and has_genotype:
        return "Moderate"
    if match_status in {"no_vcf_match_by_chr_pos", "no_rsid_detected", "rsid_without_coordinates"}:
        return "Low"
    return "Low"


def interpretation_placeholder(row: dict) -> str:
    has_genotype = as_bool(row.get("has_genotype"))
    effect = clean_str(row.get("effect"))
    rsid = clean_str(row.get("rsid")) or "this locus"
    if has_genotype:
        if effect:
            return f"Observed genotype at {rsid}; functional interpretation pending review against the curated effect."
        return f"Observed genotype at {rsid}; functional interpretation pending review."
    return "No genotype was observed at this queried locus in the current VCF; no genotype-specific interpretation assigned."


def review_status(row: dict) -> str:
    match_status = clean_str(row.get("match_status"))
    source_group = clean_str(row.get("source_group"))
    if match_status == "match_strict":
        return "ready_for_interpretation" if source_group != "revision_manual" else "ready_but_manual_row_review"
    if match_status == "match_likely_needs_alt_review":
        return "interpretable_with_alt_review"
    if match_status == "no_vcf_match_by_chr_pos":
        return "no_variant_observed_in_vcf_at_locus"
    if match_status == "rsid_without_coordinates":
        return "technical_resolution_pending"
    if match_status == "no_rsid_detected":
        return "missing_rsid"
    return "review_needed"


def notes(row: dict) -> str:
    source_group = clean_str(row.get("source_group"))
    match_status = clean_str(row.get("match_status"))
    if source_group == "revision_manual":
        return "Original sheet row needs manual structural review."
    if match_status == "match_likely_needs_alt_review":
        return "Position-level match found; ALT representation should be reviewed before final biological interpretation."
    if match_status == "no_vcf_match_by_chr_pos":
        return "Queried locus was not observed in the current VCF by CHROM+POS."
    if match_status == "no_rsid_detected":
        return "No usable rsID detected in the original row."
    return ""


def build_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    minimal_rows = []
    audit_rows = []

    for row in rows:
        minimal = {
            "Gene": clean_str(row.get("gene")),
            "SNP (rsID)": clean_str(row.get("rsid")),
            "Genotype": genotype_for_deliverable(row.get("gt_alleles")),
            "Zygosity": clean_str(row.get("zygosity")) or "NA",
            "Ref/Alt": ref_alt_for_deliverable(row),
            "Interpretation (1 sentence)": interpretation_placeholder(row),
            "Confidence Level": confidence_level(row),
        }
        audit = {
            **minimal,
            "Category / Module": clean_str(row.get("category")),
            "Review Status": review_status(row),
            "Notes": notes(row),
            "row_id": clean_str(row.get("row_id")),
            "source_group": clean_str(row.get("source_group")),
            "match_status": clean_str(row.get("match_status")),
            "gt_alleles": clean_str(row.get("gt_alleles")),
            "gt_raw": clean_str(row.get("gt_raw")),
            "ref": clean_str(row.get("ref")),
            "alt": clean_str(row.get("alt")),
            "ref_vcf": clean_str(row.get("ref_vcf")),
            "alt_vcf": clean_str(row.get("alt_vcf")),
            "has_genotype": "true" if as_bool(row.get("has_genotype")) else "false",
            "found_in_vcf_by_chr_pos": "true" if as_bool(row.get("found_in_vcf_by_chr_pos")) else "false",
        }
        minimal_rows.append(minimal)
        audit_rows.append(audit)

    return minimal_rows, audit_rows


def process(input_path: Path, output_dir: Path) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_rows = read_csv(input_path)
    if not source_rows:
        raise ValueError("sheet_final_consolidated.csv is empty.")

    minimal_rows, audit_rows = build_rows(source_rows)
    minimal_path = output_dir / "heal_fon_deliverable_presentation_min.csv"
    audit_path = output_dir / "heal_fon_deliverable_presentation_audit.csv"

    minimal_fields = [
        "Gene",
        "SNP (rsID)",
        "Genotype",
        "Zygosity",
        "Ref/Alt",
        "Interpretation (1 sentence)",
        "Confidence Level",
    ]
    audit_fields = minimal_fields + [
        "Category / Module",
        "Review Status",
        "Notes",
        "row_id",
        "source_group",
        "match_status",
        "gt_alleles",
        "gt_raw",
        "ref",
        "alt",
        "ref_vcf",
        "alt_vcf",
        "has_genotype",
        "found_in_vcf_by_chr_pos",
    ]

    write_csv(minimal_path, minimal_rows, minimal_fields)
    write_csv(audit_path, audit_rows, audit_fields)

    confidence_counts = Counter(row["Confidence Level"] for row in audit_rows)
    review_counts = Counter(row["Review Status"] for row in audit_rows)
    match_counts = Counter(row["match_status"] for row in audit_rows)
    source_counts = Counter(row["source_group"] for row in audit_rows)

    summary = {
        "status": "valid",
        "errors": [],
        "warnings": [],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            "rows_total": len(source_rows),
            "rows_with_genotype": sum(1 for row in audit_rows if row["has_genotype"] == "true"),
            "confidence_level_counts": dict(confidence_counts),
            "review_status_counts": dict(review_counts),
            "match_status_counts": dict(match_counts),
            "source_group_counts": dict(source_counts),
        },
        "outputs": {
            "deliverableMinCsv": str(minimal_path),
            "deliverableAuditCsv": str(audit_path),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "match_preparation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare HEAL match CSV for audit and deliverable review.")
    parser.add_argument("--input")
    parser.add_argument("--output-dir")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("sheetFinalConsolidatedCsv")
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
