#!/usr/bin/env python3
"""Deterministic AI triage for HEAL gene-module canon v2 match rows."""

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
    return clean_str(value).lower() in {"true", "1", "yes", "y"}


def is_strong_utr_row(row: dict) -> bool:
    return (
        clean_str(row.get("local_region_class")) == "utr_overlap"
        and clean_str(row.get("module_status")) == "Approved"
        and clean_str(row.get("tier")) in {"Tier 1", "Tier 2"}
        and clean_str(row.get("evidence_tier")) in {"High", "Medium"}
    )


def triage_row(row: dict) -> tuple[str, str]:
    annotation_needed = clean_str(row.get("annotation_needed"))
    local_region_class = clean_str(row.get("local_region_class"))
    module_status = clean_str(row.get("module_status"))

    if annotation_needed == "true":
        return "include_ai", f"Included for AI: strong region class {local_region_class or 'candidate'}."

    if as_bool(row.get("background_only")):
        return "exclude_background", f"Excluded from AI: background-only {local_region_class or 'candidate'}."

    if module_status == "Draft":
        return "exclude_draft_optional", f"Excluded from AI: Draft optional {local_region_class or 'candidate'}."

    if local_region_class == "noncoding_exon_overlap":
        return "exclude_optional_noncoding", "Excluded from AI: optional noncoding exon overlap."

    if is_strong_utr_row(row):
        return "include_ai", "Included for AI: strong UTR in Approved Tier 1/2 module with Medium/High evidence."

    if annotation_needed == "optional" and local_region_class == "utr_overlap":
        return "exclude_utr_weak", "Excluded from AI: UTR did not meet Approved Tier 1/2 and Medium/High evidence gate."

    return "exclude_optional_noncoding", f"Excluded from AI: optional low-priority region {local_region_class or 'candidate'}."


def build_summary(source_rows: list[dict], evaluated_rows: list[dict]) -> dict:
    triage_counts = Counter(clean_str(row.get("triage_decision")) for row in evaluated_rows)
    include_reason_counts = Counter(clean_str(row.get("triage_reason")) for row in evaluated_rows if clean_str(row.get("triage_decision")) == "include_ai")
    return {
        "rows_total": len(source_rows),
        "rows_with_genotype": sum(1 for row in evaluated_rows if as_bool(row.get("has_genotype"))),
        "included_for_ai": triage_counts.get("include_ai", 0),
        "excluded_background": triage_counts.get("exclude_background", 0),
        "excluded_utr_weak": triage_counts.get("exclude_utr_weak", 0),
        "excluded_optional_noncoding": triage_counts.get("exclude_optional_noncoding", 0),
        "excluded_draft_optional": triage_counts.get("exclude_draft_optional", 0),
        "included_strong_region": sum(
            1 for row in evaluated_rows if clean_str(row.get("triage_decision")) == "include_ai" and clean_str(row.get("annotation_needed")) == "true"
        ),
        "included_strong_utr": sum(
            1 for row in evaluated_rows if clean_str(row.get("triage_decision")) == "include_ai" and clean_str(row.get("local_region_class")) == "utr_overlap"
        ),
        "triage_decision_counts": dict(triage_counts),
        "include_reason_counts": dict(include_reason_counts),
        "local_region_class_counts": dict(Counter(clean_str(row.get("local_region_class")) for row in evaluated_rows if clean_str(row.get("local_region_class")))),
        "module_status_counts": dict(Counter(clean_str(row.get("module_status")) for row in evaluated_rows if clean_str(row.get("module_status")))),
        "tier_counts": dict(Counter(clean_str(row.get("tier")) for row in evaluated_rows if clean_str(row.get("tier")))),
        "evidence_tier_counts": dict(Counter(clean_str(row.get("evidence_tier")) for row in evaluated_rows if clean_str(row.get("evidence_tier")))),
    }


def process(input_path: Path, output_dir: Path) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_rows = read_csv(input_path)
    if not source_rows:
        raise ValueError("sheet_final_consolidated.csv is empty.")
    if "module_id" not in source_rows[0] or "local_region_class" not in source_rows[0]:
        raise ValueError("AI triage currently supports only gene_module_v2 match outputs.")

    evaluated_rows = []
    for row in source_rows:
        decision, reason = triage_row(row)
        evaluated_rows.append(
            {
                **row,
                "triage_decision": decision,
                "triage_reason": reason,
            }
        )

    included_rows = [row for row in evaluated_rows if clean_str(row.get("triage_decision")) == "include_ai"]
    excluded_rows = [row for row in evaluated_rows if clean_str(row.get("triage_decision")) != "include_ai"]

    fieldnames = list(source_rows[0].keys()) + ["triage_decision", "triage_reason"]
    ai_triage_path = output_dir / "heal_fon_ai_triage.csv"
    excluded_audit_path = output_dir / "heal_fon_ai_triage_excluded_audit.csv"
    summary_path = output_dir / "ai_triage_summary.json"
    write_csv(ai_triage_path, included_rows, fieldnames)
    write_csv(excluded_audit_path, excluded_rows, fieldnames)

    metadata = build_summary(source_rows, evaluated_rows)
    summary = {
        "status": "valid",
        "errors": [],
        "warnings": [],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "schemaVersion": "gene_module_v2",
        "metadata": metadata,
        "outputs": {
            "aiTriageCsv": str(ai_triage_path),
            "aiTriageExcludedAuditCsv": str(excluded_audit_path),
            "aiTriageSummaryJson": str(summary_path),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic AI triage for HEAL gene-module canon matches.")
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
