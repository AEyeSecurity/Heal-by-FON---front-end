#!/usr/bin/env python3
"""Match HEAL rsID-ready canon targets against a VCF by CHROM:POS."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import json
import re
from collections import Counter
from pathlib import Path


RSID_RE = re.compile(r"\brs\d+\b", re.IGNORECASE)


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


def open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def first_rsid(*values: str) -> str:
    found = []
    for value in values:
        found.extend(RSID_RE.findall(str(value or "")))
    return sorted({item.lower() for item in found})[0] if found else ""


def split_gt(gt: str) -> list[str]:
    if not gt:
        return []
    return re.split(r"[\/|]", gt)


def gt_to_alleles(gt: str, ref: str, alt: str) -> str:
    if not gt:
        return ""
    allele_map = [ref] + [item.strip() for item in str(alt or "").split(",") if item.strip()]
    out = []
    for token in split_gt(gt):
        if token == ".":
            out.append(".")
            continue
        try:
            index = int(token)
        except ValueError:
            out.append("?")
            continue
        out.append(allele_map[index] if 0 <= index < len(allele_map) else "?")
    return "/".join(out)


def pysam_gt_to_raw(gt, phased: bool = False) -> str:
    if gt is None:
        return ""
    separator = "|" if phased else "/"
    values = []
    for allele in gt:
        values.append("." if allele is None or allele < 0 else str(allele))
    return separator.join(values)


def zygosity_from_gt(gt: str) -> str:
    parts = split_gt(gt)
    if not parts or any(part in {"", "."} for part in parts):
        return "unknown"
    if len(parts) != 2:
        return "non_diploid_or_complex"
    return "homozygous" if parts[0] == parts[1] else "heterozygous"


def extract_gt(format_value: str, sample_value: str) -> str:
    format_keys = str(format_value or "").split(":")
    sample_values = str(sample_value or "").split(":")
    try:
        index = format_keys.index("GT")
    except ValueError:
        return ""
    return sample_values[index] if index < len(sample_values) else ""


def normalize_alt_set(value: str) -> set[str]:
    text = str(value or "").strip().upper()
    if not text or text == "<NA>":
        return set()
    return {item.strip() for item in text.split(",") if item.strip()}


def compare_ref(expected: str, observed: str) -> str:
    expected_text = str(expected or "").strip().upper()
    observed_text = str(observed or "").strip().upper()
    if not expected_text or expected_text == "<NA>":
        return "missing_expected_ref"
    if not observed_text or observed_text == "<NA>":
        return "missing_vcf_ref"
    return "ref_match" if expected_text == observed_text else "ref_mismatch"


def compare_alt(expected: str, observed: str) -> str:
    expected_set = normalize_alt_set(expected)
    observed_set = normalize_alt_set(observed)
    if not expected_set:
        return "missing_expected_alt"
    if not observed_set:
        return "missing_vcf_alt"
    if expected_set == observed_set:
        return "alt_exact_match"
    if expected_set.intersection(observed_set):
        return "alt_partial_overlap"
    return "alt_mismatch"


def classify_match(row: dict) -> str:
    if row["has_rsid"] != "true":
        return "no_rsid_detected"
    if row["has_coords"] != "true":
        return "rsid_without_coordinates"
    if row["found_in_vcf_by_chr_pos"] != "true":
        return "no_vcf_match_by_chr_pos"
    if row["ref_check"] == "ref_match" and row["alt_check"] == "alt_exact_match":
        return "match_strict"
    if row["ref_check"] == "ref_match" and row["alt_check"] in {"alt_partial_overlap", "missing_expected_alt"}:
        return "match_likely_needs_alt_review"
    if row["found_in_vcf_by_chr_pos"] == "true":
        return "match_by_position_needs_review"
    return "review"


def scan_vcf_streaming(vcf_path: Path, target_keys: set[str]) -> tuple[list[dict], dict]:
    matched = []
    sample_name = ""
    scanned_rows = 0
    with open_text(vcf_path) as handle:
        header = []
        for line in handle:
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                header = line.rstrip("\n").split("\t")
                sample_name = header[9] if len(header) > 9 else ""
                break
        if not header:
            raise ValueError("VCF #CHROM header was not found.")

        for line in handle:
            if not line or line.startswith("#"):
                continue
            scanned_rows += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos, id_vcf, ref, alt, qual, filter_vcf = parts[:7]
            key = f"{chrom}:{pos}"
            if key not in target_keys:
                continue
            fmt = parts[8] if len(parts) > 8 else ""
            sample = parts[9] if len(parts) > 9 else ""
            gt_raw = extract_gt(fmt, sample)
            matched.append(
                {
                    "match_key_chr_pos": key,
                    "chrom_vcf": chrom,
                    "pos_vcf": pos,
                    "id_vcf": id_vcf,
                    "ref_vcf": ref,
                    "alt_vcf": alt,
                    "qual_vcf": qual,
                    "filter_vcf": filter_vcf,
                    "gt_raw": gt_raw,
                    "gt_alleles": gt_to_alleles(gt_raw, ref, alt),
                    "zygosity": zygosity_from_gt(gt_raw),
                }
            )
    return matched, {"sample_name": sample_name, "scanned_variant_rows": scanned_rows, "vcf_parser_used": "streaming"}


def scan_vcf_pysam(vcf_path: Path, target_keys: set[str]) -> tuple[list[dict], dict]:
    try:
        import pysam  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pysam is not installed in the configured Python environment.") from exc

    matched = []
    scanned_rows = 0
    with pysam.VariantFile(str(vcf_path)) as vcf:
        samples = list(vcf.header.samples)
        sample_name = samples[0] if samples else ""
        for record in vcf:
            scanned_rows += 1
            key = f"{record.chrom}:{record.pos}"
            if key not in target_keys:
                continue
            gt_raw = ""
            if sample_name:
                sample_data = record.samples[sample_name]
                gt_raw = pysam_gt_to_raw(sample_data.get("GT"), bool(getattr(sample_data, "phased", False)))
            alt = ",".join(record.alts or [])
            filters = ";".join(record.filter.keys()) if record.filter.keys() else "PASS"
            matched.append(
                {
                    "match_key_chr_pos": key,
                    "chrom_vcf": str(record.chrom),
                    "pos_vcf": str(record.pos),
                    "id_vcf": str(record.id or ""),
                    "ref_vcf": str(record.ref or ""),
                    "alt_vcf": alt,
                    "qual_vcf": "" if record.qual is None else str(record.qual),
                    "filter_vcf": filters,
                    "gt_raw": gt_raw,
                    "gt_alleles": gt_to_alleles(gt_raw, str(record.ref or ""), alt),
                    "zygosity": zygosity_from_gt(gt_raw),
                }
            )
    return matched, {"sample_name": sample_name, "scanned_variant_rows": scanned_rows, "vcf_parser_used": "pysam"}


def scan_vcf(vcf_path: Path, target_keys: set[str], vcf_parser: str) -> tuple[list[dict], dict, list[str]]:
    warnings = []
    if vcf_parser == "pysam":
        try:
            matched, meta = scan_vcf_pysam(vcf_path, target_keys)
            return matched, meta, warnings
        except Exception as exc:  # noqa: BLE001 - parser fallback is intentional.
            warnings.append(f"pysam VCF scan failed, used streaming parser instead: {exc}")
    matched, meta = scan_vcf_streaming(vcf_path, target_keys)
    if vcf_parser == "pysam":
        meta["requested_vcf_parser"] = "pysam"
    return matched, meta, warnings


def process(canon_clean_path: Path, rsid_ready_path: Path, vcf_path: Path, output_dir: Path, vcf_parser: str = "streaming") -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    vcf_parser = str(vcf_parser or "streaming").strip().lower()
    warnings = []
    if vcf_parser not in {"streaming", "pysam"}:
        warnings.append(f"Unknown VCF parser '{vcf_parser}', using streaming parser.")
        vcf_parser = "streaming"
    canon_rows = read_csv(canon_clean_path)
    rsid_ready_rows = read_csv(rsid_ready_path)
    ready_by_rsid = {}
    target_keys = set()
    for row in rsid_ready_rows:
        rsid = str(row.get("rsid", "")).strip().lower()
        if rsid and rsid not in ready_by_rsid:
            ready_by_rsid[rsid] = row
        key = str(row.get("match_key_chr_pos", "")).strip()
        if key:
            target_keys.add(key)

    vcf_candidates, scan_meta, scan_warnings = scan_vcf(vcf_path, target_keys, vcf_parser)
    warnings.extend(scan_warnings)
    evidence_by_key = {}
    for row in vcf_candidates:
        evidence_by_key.setdefault(row["match_key_chr_pos"], row)

    sheet_final = []
    for source in canon_rows:
        rsid = str(source.get("rsid") or "").strip().lower()
        if not rsid:
            rsid = first_rsid(source.get("col_A"), source.get("col_B"), source.get("col_C"), source.get("col_D"))
        ready = ready_by_rsid.get(rsid, {})
        key = str(ready.get("match_key_chr_pos", "")).strip()
        evidence = evidence_by_key.get(key, {})
        row = {
            "row_id": source.get("row_id", ""),
            "source_group": source.get("source_group", ""),
            "category": source.get("col_A") or source.get("category", ""),
            "gene": source.get("col_B") or source.get("gene", ""),
            "rsid": rsid,
            "effect": source.get("col_D") or source.get("effect", ""),
            "assembly_name": ready.get("assembly_name", ""),
            "chrom": ready.get("chrom", ""),
            "pos": ready.get("pos", ""),
            "end": ready.get("end", ""),
            "ref": ready.get("ref", ""),
            "alt": ready.get("alt", ""),
            "allele_string": ready.get("allele_string", ""),
            "chrom_match": ready.get("chrom_match", ""),
            "pos_match": ready.get("pos_match", ""),
            "ref_match": ready.get("ref_match", ""),
            "alt_match": ready.get("alt_match", ""),
            "match_key_chr_pos": key,
            "match_key_full": ready.get("match_key_full", ""),
            "api_status": ready.get("api_status", ""),
            "api_note": ready.get("api_note", ""),
            "chrom_vcf": evidence.get("chrom_vcf", ""),
            "pos_vcf": evidence.get("pos_vcf", ""),
            "id_vcf": evidence.get("id_vcf", ""),
            "ref_vcf": evidence.get("ref_vcf", ""),
            "alt_vcf": evidence.get("alt_vcf", ""),
            "gt_raw": evidence.get("gt_raw", ""),
            "gt_alleles": evidence.get("gt_alleles", ""),
            "zygosity": evidence.get("zygosity", ""),
            "filter_vcf": evidence.get("filter_vcf", ""),
            "qual_vcf": evidence.get("qual_vcf", ""),
        }
        row["has_rsid"] = "true" if rsid.startswith("rs") else "false"
        row["has_coords"] = "true" if row["chrom_match"] and row["pos_match"] else "false"
        row["found_in_vcf_by_chr_pos"] = "true" if row["chrom_vcf"] and row["pos_vcf"] else "false"
        row["has_genotype"] = "true" if row["gt_alleles"] else "false"
        row["ref_check"] = compare_ref(row["ref_match"], row["ref_vcf"])
        row["alt_check"] = compare_alt(row["alt_match"], row["alt_vcf"])
        row["match_status"] = classify_match(row)
        sheet_final.append(row)

    final_fields = [
        "row_id",
        "source_group",
        "category",
        "gene",
        "rsid",
        "effect",
        "assembly_name",
        "chrom",
        "pos",
        "end",
        "ref",
        "alt",
        "allele_string",
        "chrom_match",
        "pos_match",
        "ref_match",
        "alt_match",
        "match_key_chr_pos",
        "match_key_full",
        "api_status",
        "api_note",
        "chrom_vcf",
        "pos_vcf",
        "id_vcf",
        "ref_vcf",
        "alt_vcf",
        "gt_raw",
        "gt_alleles",
        "zygosity",
        "filter_vcf",
        "qual_vcf",
        "has_rsid",
        "has_coords",
        "found_in_vcf_by_chr_pos",
        "has_genotype",
        "ref_check",
        "alt_check",
        "match_status",
    ]
    candidate_fields = [
        "match_key_chr_pos",
        "chrom_vcf",
        "pos_vcf",
        "id_vcf",
        "ref_vcf",
        "alt_vcf",
        "qual_vcf",
        "filter_vcf",
        "gt_raw",
        "gt_alleles",
        "zygosity",
    ]
    write_csv(output_dir / "vcf_candidates_chr_pos.csv", vcf_candidates, candidate_fields)
    write_csv(output_dir / "sheet_final_consolidated.csv", sheet_final, final_fields)
    for status in [
        "match_strict",
        "match_likely_needs_alt_review",
        "match_by_position_needs_review",
        "no_vcf_match_by_chr_pos",
    ]:
        write_csv(
            output_dir / f"sheet_final_{status}.csv",
            [row for row in sheet_final if row["match_status"] == status],
            final_fields,
        )

    match_counts = Counter(row["match_status"] for row in sheet_final)
    source_counts = Counter(row["source_group"] for row in sheet_final)
    summary = {
        "status": "valid",
        "errors": [],
        "warnings": warnings,
        "inputPaths": {
            "canonCleanCsv": str(canon_clean_path),
            "rsidMatchReadyCsv": str(rsid_ready_path),
            "vcfPath": str(vcf_path),
        },
        "outputDir": str(output_dir),
        "metadata": {
            "sheet_final_rows": len(sheet_final),
            "vcf_candidates_rows": len(vcf_candidates),
            "target_keys": len(target_keys),
            "match_status_counts": dict(match_counts),
            "source_group_counts": dict(source_counts),
            **scan_meta,
        },
        "outputs": {
            "vcfCandidatesCsv": str(output_dir / "vcf_candidates_chr_pos.csv"),
            "sheetFinalConsolidatedCsv": str(output_dir / "sheet_final_consolidated.csv"),
            "sheetFinalMatchStrictCsv": str(output_dir / "sheet_final_match_strict.csv"),
            "sheetFinalMatchLikelyNeedsAltReviewCsv": str(output_dir / "sheet_final_match_likely_needs_alt_review.csv"),
            "sheetFinalMatchByPositionNeedsReviewCsv": str(output_dir / "sheet_final_match_by_position_needs_review.csv"),
            "sheetFinalNoVcfMatchByChrPosCsv": str(output_dir / "sheet_final_no_vcf_match_by_chr_pos.csv"),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "vcf_canon_match_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match HEAL canon targets to VCF.")
    parser.add_argument("--canon-clean")
    parser.add_argument("--rsid-ready")
    parser.add_argument("--vcf")
    parser.add_argument("--output-dir")
    parser.add_argument("--vcf-parser", choices=["streaming", "pysam"], default="streaming")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.canon_clean = payload.get("canonCleanPath")
        args.rsid_ready = payload.get("rsidReadyPath")
        args.vcf = payload.get("vcfPath")
        args.output_dir = payload.get("outputDir")
        args.vcf_parser = payload.get("vcfParser") or payload.get("parser") or args.vcf_parser
    if not args.canon_clean or not args.rsid_ready or not args.vcf or not args.output_dir:
        parser.error("--canon-clean, --rsid-ready, --vcf, and --output-dir are required.")
    return args


def main() -> int:
    args = parse_args()
    process(Path(args.canon_clean), Path(args.rsid_ready), Path(args.vcf), Path(args.output_dir), args.vcf_parser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
