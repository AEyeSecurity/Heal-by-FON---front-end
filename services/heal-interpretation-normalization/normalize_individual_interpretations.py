#!/usr/bin/env python3
"""Normalize HEAL LLM1 individual variant interpretations before global grouping."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict


CONFIDENCE_ORDER = {"Low": 1, "Moderate": 2, "High": 3, "Conflicting": 4}
VALID_CONFIDENCE = set(CONFIDENCE_ORDER)
NONCODING_TERMS = {
    "intron_variant",
    "intergenic_variant",
    "upstream_gene_variant",
    "downstream_gene_variant",
    "5_prime_utr_variant",
    "3_prime_utr_variant",
    "regulatory_region_variant",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def truthy(value) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def lower_blob(*values) -> str:
    return " ".join(clean(value).lower() for value in values if clean(value))


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_json_field(row: dict, field: str) -> list | dict:
    raw = clean(row.get(field))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def evidence_blob(row: dict) -> str:
    return lower_blob(
        row.get("evidence_used"),
        row.get("evidence_limitations"),
        row.get("technical_notes_en"),
        row.get("confidence_rationale_en"),
        row.get("interpretation_long_en"),
        row.get("technical_interpretation_en"),
    )


def duplicate_key(row: dict) -> tuple[str, str, str, str, str]:
    return (
        clean(row.get("gene")).upper(),
        clean(row.get("rsID")).lower(),
        clean(row.get("observed_genotype")).upper(),
        clean(row.get("zygosity")).lower(),
        clean(row.get("ref_alt")).upper(),
    )


def row_has_gwas_support(row: dict) -> bool:
    items = load_json_field(row, "evidence_used")
    blob = lower_blob(items, row.get("confidence_rationale_en"))
    if "gwas" not in blob:
        return False
    if "no gwas" in blob or "gwas_association_count=0" in blob:
        return False
    return True


def row_has_strong_gwas_signal(row: dict) -> bool:
    blob = evidence_blob(row)
    if not row_has_gwas_support(row):
        return False
    # Capture p=5.000e-26, min p=1.000e-78, p-value 2.0e-08, etc.
    p_values = []
    for match in re.finditer(r"(?:p(?:-?value)?|min p)\s*[=:]\s*([0-9]+(?:\.[0-9]+)?e[-+]?\d+)", blob):
        try:
            p_values.append(float(match.group(1)))
        except ValueError:
            pass
    return any(value <= 5e-8 for value in p_values) or "multiple associations" in blob


def has_quantitative_biomarker_association(row: dict) -> bool:
    blob = evidence_blob(row)
    biomarker_patterns = (
        "serum ige",
        "ige amount",
        "ige level",
        "ige levels",
        "plasma metabolite",
        "serum metabolite",
        "blood metabolite",
        "enzyme activity",
        "biomarker concentration",
    )
    return any(pattern in blob for pattern in biomarker_patterns)


def is_noncoding_association_marker(row: dict) -> bool:
    blob = evidence_blob(row)
    return any(term in blob for term in NONCODING_TERMS) and row_has_gwas_support(row)


def high_synonymous_limited_context(row: dict) -> bool:
    blob = evidence_blob(row)
    if clean(row.get("final_confidence_level")) != "High":
        return False
    if "synonymous_variant" not in blob:
        return False
    if clean(row.get("zygosity")).lower() != "non_diploid_or_complex":
        return False
    has_limited_or_benign = (
        "not_reported" in blob
        or "no clinvar" in blob
        or "benign" in blob
        or "low cadd" in blob
        or "cadd 4." in blob
    )
    has_stronger_support = (
        "drug_response" in blob
        or "pathogenic_or_likely_pathogenic" in blob
        or "conflicting_pathogenicity" in blob
        or row_has_strong_gwas_signal(row)
    )
    return has_limited_or_benign and not has_stronger_support


def normalize_duplicate_confidence(group: list[dict]) -> tuple[str | None, str]:
    levels = [clean(row.get("final_confidence_level")) for row in group if clean(row.get("final_confidence_level"))]
    unique = sorted(set(levels), key=lambda value: CONFIDENCE_ORDER.get(value, 0))
    if len(unique) <= 1:
        return None, ""
    if "Conflicting" in unique:
        return "Conflicting", "Duplicate group normalized to Conflicting because one row has material evidence conflict."
    if any(truthy(row.get("gene_or_locus_ambiguity_flag")) for row in group):
        return "Moderate", "Duplicate group normalized to Moderate because the same observed variant has locus/gene ambiguity."
    counts = Counter(levels)
    most_common = counts.most_common()
    if most_common and most_common[0][1] > 1:
        return most_common[0][0], "Duplicate group normalized to the majority confidence across repeated module rows."
    if set(unique) == {"High", "Moderate"}:
        return "Moderate", "Duplicate group normalized to Moderate because repeated module rows disagreed without a majority."
    return min(unique, key=lambda value: CONFIDENCE_ORDER.get(value, 99)), "Duplicate group normalized conservatively because repeated module rows disagreed."


def shorten_sentence(text: str, language: str) -> tuple[str, bool]:
    original = clean(text)
    words = re.findall(r"\b\w+\b", original, flags=re.UNICODE)
    if len(words) <= 45 and len(original) <= 300:
        return original, False

    separators = ["; ", " but ", " and that ", " and should ", ", but "]
    if language == "es":
        separators = ["; ", " pero ", " y que ", " y debe ", ", pero "]

    for separator in separators:
        if separator in original:
            candidate = original.split(separator, 1)[0].strip()
            candidate_words = re.findall(r"\b\w+\b", candidate, flags=re.UNICODE)
            if 18 <= len(candidate_words) <= 42:
                if not candidate.endswith((".", "!", "?")):
                    candidate += "."
                return candidate, True

    trimmed_words = words[:42]
    if not trimmed_words:
        return original, False
    trimmed = " ".join(trimmed_words).strip()
    if not trimmed.endswith((".", "!", "?")):
        trimmed += "."
    return trimmed, True


def normalize_rows(rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[duplicate_key(row)].append(row)

    duplicate_actions = {}
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        normalized, reason = normalize_duplicate_confidence(group)
        if normalized:
            duplicate_actions[key] = (normalized, reason)

    normalized_rows = []
    warnings = []
    for index, row in enumerate(rows, start=1):
        out = dict(row)
        original_confidence = clean(row.get("final_confidence_level"))
        final_confidence = original_confidence if original_confidence in VALID_CONFIDENCE else "Moderate"
        reasons = []
        warning_values = []

        key = duplicate_key(row)
        group_size = len(groups[key])
        if key in duplicate_actions:
            final_confidence, reason = duplicate_actions[key]
            reasons.append(reason)
            warning_values.append("duplicate_confidence_normalized")

        if high_synonymous_limited_context(row):
            final_confidence = "Moderate"
            reasons.append(
                "High confidence capped at Moderate because the observed variant is synonymous, non-diploid/complex, common or benign/limited, and lacks stronger direct support."
            )
            warning_values.append("high_synonymous_limited_context_capped")

        if (
            final_confidence == "Low"
            and is_noncoding_association_marker(row)
            and row_has_strong_gwas_signal(row)
            and has_quantitative_biomarker_association(row)
        ):
            final_confidence = "Moderate"
            reasons.append(
                "Low confidence raised to Moderate because the non-coding marker has strong repeated association evidence and remains non-diagnostic."
            )
            warning_values.append("strong_association_marker_raised")

        original_en = clean(row.get("interpretation_one_sentence_en"))
        original_es = clean(row.get("interpretation_one_sentence_es"))
        short_en, changed_en = shorten_sentence(original_en, "en")
        short_es, changed_es = shorten_sentence(original_es, "es")
        if changed_en:
            out["original_interpretation_one_sentence_en"] = original_en
            out["interpretation_one_sentence_en"] = short_en
            warning_values.append("english_one_sentence_shortened")
        else:
            out["original_interpretation_one_sentence_en"] = ""
        if changed_es:
            out["original_interpretation_one_sentence_es"] = original_es
            out["interpretation_one_sentence_es"] = short_es
            warning_values.append("spanish_one_sentence_shortened")
        else:
            out["original_interpretation_one_sentence_es"] = ""

        out["original_final_confidence_level"] = original_confidence
        out["final_confidence_level"] = final_confidence
        out["normalization_status"] = "changed" if final_confidence != original_confidence or warning_values else "unchanged"
        out["normalization_reason"] = " ".join(reasons)
        out["duplicate_group_size"] = str(group_size)
        out["qa_warnings"] = "|".join(dict.fromkeys(warning_values))
        out["normalized_at"] = utc_now()

        if out["normalization_status"] == "changed":
            warnings.append(
                {
                    "row_id": clean(row.get("row_id")) or str(index),
                    "gene": clean(row.get("gene")),
                    "rsID": clean(row.get("rsID")),
                    "category": clean(row.get("category")),
                    "original_final_confidence_level": original_confidence,
                    "normalized_final_confidence_level": final_confidence,
                    "qa_warnings": out["qa_warnings"],
                    "normalization_reason": out["normalization_reason"],
                }
            )
        normalized_rows.append(out)

    metadata = {
        "input_rows": len(rows),
        "output_rows": len(normalized_rows),
        "changed_rows": sum(1 for row in normalized_rows if row["normalization_status"] == "changed"),
        "duplicate_groups": sum(1 for group in groups.values() if len(group) > 1),
        "duplicate_groups_normalized": len(duplicate_actions),
        "confidence_level_counts": dict(Counter(row.get("final_confidence_level") for row in normalized_rows)),
        "original_confidence_level_counts": dict(Counter(row.get("original_final_confidence_level") for row in normalized_rows)),
        "qa_warning_counts": dict(
            Counter(
                warning
                for row in normalized_rows
                for warning in clean(row.get("qa_warnings")).split("|")
                if warning
            )
        ),
    }
    return normalized_rows, warnings, metadata


def run(payload: dict) -> dict:
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    normalized_rows, warnings, metadata = normalize_rows(rows)

    extra_fields = [
        "original_final_confidence_level",
        "original_interpretation_one_sentence_en",
        "original_interpretation_one_sentence_es",
        "normalization_status",
        "normalization_reason",
        "duplicate_group_size",
        "qa_warnings",
        "normalized_at",
    ]
    fieldnames = list(rows[0].keys()) if rows else []
    for field in extra_fields:
        if field not in fieldnames:
            fieldnames.append(field)

    normalized_csv = output_dir / "individual_variant_interpretations_normalized.csv"
    warnings_csv = output_dir / "individual_variant_interpretation_normalization_warnings.csv"
    summary_json = output_dir / "individual_variant_interpretation_normalization_summary.json"

    write_csv(normalized_csv, normalized_rows, fieldnames)
    write_csv(
        warnings_csv,
        warnings,
        [
            "row_id",
            "gene",
            "rsID",
            "category",
            "original_final_confidence_level",
            "normalized_final_confidence_level",
            "qa_warnings",
            "normalization_reason",
        ],
    )

    summary = {
        "status": "valid",
        "errors": [],
        "warnings": [f"{len(warnings)} row(s) adjusted or flagged by deterministic normalization."] if warnings else [],
        "metadata": metadata,
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "outputs": {
            "individualInterpretationsNormalizedCsv": str(normalized_csv),
            "individualInterpretationNormalizationWarningsCsv": str(warnings_csv),
            "individualInterpretationNormalizationSummaryJson": str(summary_json),
        },
        "timestamps": {
            "startedAt": payload.get("requestedAt") or utc_now(),
            "completedAt": utc_now(),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        result = run(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        result = {
            "status": "invalid",
            "errors": [str(error)],
            "warnings": [],
            "metadata": {},
            "outputs": {},
            "timestamps": {"completedAt": utc_now()},
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
