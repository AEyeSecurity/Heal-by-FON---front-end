#!/usr/bin/env python3
"""Normalize observed VCF alleles for the HEAL gene-module v2 pipeline.

The service deliberately keeps patient-specific genotype data out of the
reference and enrichment caches.  It delegates reference-aware normalization
to a versioned bcftools Docker image, then emits auditable physical variants.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


DEFAULT_IMAGE = "heal-vcf-normalizer:1.0.0"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def normalize_chromosome(value: object) -> str:
    chrom = clean(value).upper()
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "MT":
        chrom = "M"
    return f"chr{chrom}" if chrom else ""


def stable_variant_key(assembly: str, chrom: str, start: str | int, ref: str, alt: str) -> str:
    value = "|".join([clean(assembly), clean(chrom), str(start), clean(ref).upper(), clean(alt).upper()])
    return f"v2_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def split_gt(value: str) -> list[str]:
    return re.split(r"[|/]", value) if value else []


def dosage_for_allele(gt: str, allele_index: int) -> int:
    total = 0
    for part in split_gt(gt):
        try:
            total += int(part) == allele_index
        except ValueError:
            continue
    return total


def zygosity_for_dosage(gt: str, dosage: int) -> str:
    alleles = split_gt(gt)
    if not alleles or any(item in {"", "."} for item in alleles):
        return "unknown"
    if len(alleles) != 2:
        return "non_diploid_or_complex"
    return "homozygous" if dosage == 2 else "heterozygous" if dosage == 1 else "reference_only"


def parse_info(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in clean(value).split(";"):
        if not item:
            continue
        key, separator, item_value = item.partition("=")
        parsed[key] = item_value if separator else "true"
    return parsed


def extract_gt(format_value: str, sample_value: str) -> str:
    keys = clean(format_value).split(":")
    values = clean(sample_value).split(":")
    try:
        index = keys.index("GT")
    except ValueError:
        return ""
    return values[index] if index < len(values) else ""


def is_symbolic(alt: str) -> bool:
    return not alt or alt in {"*", "."} or (alt.startswith("<") and alt.endswith(">")) or "[" in alt or "]" in alt


def detect_assembly_from_header(path: Path) -> dict:
    references: list[str] = []
    contig_lengths: dict[str, int] = {}
    with open_text(path) as handle:
        for line in handle:
            if line.startswith("#CHROM"):
                break
            if not line.startswith("##"):
                continue
            low = line.lower()
            if low.startswith("##reference=") or low.startswith("##assembly="):
                references.append(line.strip())
            if low.startswith("##contig="):
                id_match = re.search(r"(?:^|,)ID=([^,>]+)", line, flags=re.I)
                length_match = re.search(r"(?:^|,)length=(\d+)", line, flags=re.I)
                if id_match and length_match:
                    contig_lengths[normalize_chromosome(id_match.group(1))] = int(length_match.group(1))

    evidence = " ".join(references).lower()
    detected = ""
    source = ""
    if any(token in evidence for token in ("grch38", "hg38", "b38")):
        detected, source = "GRCh38", "reference_header"
    elif any(token in evidence for token in ("grch37", "hg19", "b37")):
        detected, source = "GRCh37", "reference_header"
    elif contig_lengths.get("chr1") == 248956422:
        detected, source = "GRCh38", "contig_length_chr1"
    elif contig_lengths.get("chr1") == 249250621:
        detected, source = "GRCh37", "contig_length_chr1"

    return {
        "status": "detected" if detected else "unknown",
        "assembly": detected or None,
        "source": source or None,
        "headerReferences": references,
        "contigCount": len(contig_lengths),
        "chr1Length": contig_lengths.get("chr1"),
    }


def source_alleles(path: Path) -> tuple[dict[tuple[str, str, str, str, str], dict], list[dict], Counter]:
    """Index observed source ALT alleles to preserve original genotype/audit fields."""
    indexed: dict[tuple[str, str, str, str, str], dict] = {}
    excluded: list[dict] = []
    stats: Counter = Counter()
    with open_text(path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                stats["malformed_records"] += 1
                continue
            chrom, pos, record_id, ref, alt, qual, filter_value, info = parts[:8]
            gt = extract_gt(parts[8] if len(parts) > 8 else "", parts[9] if len(parts) > 9 else "")
            alts = [item.strip() for item in alt.split(",") if item.strip()]
            stats["input_records"] += 1
            if len(alts) > 1:
                stats["multiallelic_records"] += 1
            for allele_index, allele_alt in enumerate(alts, start=1):
                dosage = dosage_for_allele(gt, allele_index)
                if dosage <= 0:
                    continue
                base = {
                    "source_chrom_vcf": normalize_chromosome(chrom),
                    "source_pos_vcf": pos,
                    "source_id_vcf": record_id,
                    "source_ref_vcf": ref,
                    "source_alt_vcf": alt,
                    "source_alt_allele": allele_alt,
                    "source_alt_index": str(allele_index),
                    "source_gt_raw": gt,
                    "source_allele_dosage": str(dosage),
                    "source_zygosity": zygosity_for_dosage(gt, dosage),
                    "source_filter_vcf": filter_value,
                    "source_qual_vcf": qual,
                    "source_info_vcf": info,
                }
                if is_symbolic(allele_alt):
                    excluded.append({**base, "exclusion_reason": "symbolic_or_unsupported_alt"})
                    stats["symbolic_or_unsupported"] += 1
                    continue
                key = (base["source_chrom_vcf"], pos, ref, alt, str(allele_index))
                indexed[key] = base
                stats["observed_source_alleles"] += 1
    return indexed, excluded, stats


def parse_orig(info: dict[str, str], current: dict) -> tuple[str, str, str, str, str]:
    original = clean(info.get("ORIG"))
    if not original:
        return (
            current["chrom_vcf"], current["pos_vcf"], current["ref_vcf"], current["alt_vcf"], "1"
        )
    values = original.split("|")
    if len(values) < 4:
        return (
            current["chrom_vcf"], current["pos_vcf"], current["ref_vcf"], current["alt_vcf"], "1"
        )
    return (
        normalize_chromosome(values[0]),
        clean(values[1]),
        clean(values[2]),
        clean(values[3]),
        clean(values[4]) if len(values) > 4 else "1",
    )


def normalized_alleles(path: Path, assembly: str, source_index: dict, excluded: list[dict], stats: Counter) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    with open_text(path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos, record_id, ref, alt, qual, filter_value, info_value = parts[:8]
            if "," in alt or is_symbolic(alt):
                excluded.append({
                    "source_chrom_vcf": normalize_chromosome(chrom), "source_pos_vcf": pos,
                    "source_id_vcf": record_id, "source_ref_vcf": ref, "source_alt_vcf": alt,
                    "exclusion_reason": "unsupported_normalized_alt",
                })
                stats["unsupported_normalized"] += 1
                continue
            info = parse_info(info_value)
            gt = extract_gt(parts[8] if len(parts) > 8 else "", parts[9] if len(parts) > 9 else "")
            dosage = dosage_for_allele(gt, 1)
            if dosage <= 0:
                continue
            stats["normalized_observed_alleles"] += 1
            current = {"chrom_vcf": normalize_chromosome(chrom), "pos_vcf": pos, "ref_vcf": ref, "alt_vcf": alt}
            source_chrom, source_pos, source_ref, source_alt, source_index_value = parse_orig(info, current)
            source = source_index.get((source_chrom, source_pos, source_ref, source_alt, source_index_value))
            if source is None:
                # A record without ORIG was already biallelic and is valid on its own.
                source = {
                    "source_chrom_vcf": source_chrom, "source_pos_vcf": source_pos,
                    "source_id_vcf": record_id, "source_ref_vcf": source_ref,
                    "source_alt_vcf": source_alt, "source_alt_allele": alt,
                    "source_alt_index": source_index_value, "source_gt_raw": gt,
                    "source_allele_dosage": str(dosage),
                    "source_zygosity": zygosity_for_dosage(gt, dosage),
                    "source_filter_vcf": filter_value, "source_qual_vcf": qual,
                    "source_info_vcf": info_value,
                }
            key = stable_variant_key(assembly, current["chrom_vcf"], pos, ref, alt)
            if key in seen:
                # VCF duplicates are kept in the normalized VCF, but queried only once downstream.
                stats["duplicate_normalized_physical_variants"] += 1
                continue
            seen.add(key)
            rows.append({
                "variant_key": key,
                "assembly": assembly,
                "chrom_vcf": current["chrom_vcf"],
                "pos_vcf": pos,
                "variant_start": pos,
                "variant_end": str(int(pos) + max(len(ref), 1) - 1),
                "id_vcf": record_id,
                "ref_vcf": ref,
                "alt_vcf": alt,
                "allele_index": "1",
                "allele_dosage": str(dosage),
                "gt_raw": gt,
                "zygosity": zygosity_for_dosage(gt, dosage),
                "qual_vcf": qual,
                "filter_vcf": filter_value,
                "quality_flag": "pass" if filter_value in {"", ".", "PASS"} else "filtered",
                **source,
            })
    stats["normalized_physical_variants"] = len(rows)
    return rows


def run_bcftools(input_path: Path, output_path: Path, reference_path: Path, image: str) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "docker", "run", "--rm",
        "-v", f"{input_path.parent.resolve()}:/input:ro",
        "-v", f"{output_path.parent.resolve()}:/output",
        "-v", f"{reference_path.parent.resolve()}:/reference:ro",
        image,
        "norm", "-f", f"/reference/{reference_path.name}", "-m", "-any", "--check-ref", "x",
        "--old-rec-tag", "ORIG", "-Oz", "-o", f"/output/{output_path.name}", f"/input/{input_path.name}",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    result = {
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }
    if completed.returncode != 0:
        raise RuntimeError(f"bcftools normalization failed: {completed.stderr.strip()[-1000:]}")
    return result


def load_reference_manifest(value: str | None) -> dict:
    if not value:
        return {}
    path = Path(value)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def process(payload: dict) -> dict:
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    assembly = clean(payload.get("assembly"))
    reference_path = Path(payload["referenceFasta"]).resolve()
    if assembly not in {"GRCh38", "GRCh37"}:
        raise ValueError("A supported canonical assembly is required for VCF normalization.")
    if not input_path.is_file():
        raise FileNotFoundError(f"VCF input does not exist: {input_path}")
    if not reference_path.is_file() or not Path(f"{reference_path}.fai").is_file():
        raise FileNotFoundError("The managed reference FASTA and .fai index are required before normalization.")

    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_index, excluded_rows, stats = source_alleles(input_path)
    normalized_vcf = output_dir / "normalized.vcf.gz"
    bcftools = run_bcftools(input_path, normalized_vcf, reference_path, clean(payload.get("dockerImage")) or DEFAULT_IMAGE)
    normalized_rows = normalized_alleles(normalized_vcf, assembly, source_index, excluded_rows, stats)

    fields = [
        "variant_key", "assembly", "chrom_vcf", "pos_vcf", "variant_start", "variant_end", "id_vcf", "ref_vcf", "alt_vcf",
        "allele_index", "allele_dosage", "gt_raw", "zygosity", "qual_vcf", "filter_vcf", "quality_flag",
        "source_chrom_vcf", "source_pos_vcf", "source_id_vcf", "source_ref_vcf", "source_alt_vcf", "source_alt_allele",
        "source_alt_index", "source_gt_raw", "source_allele_dosage", "source_zygosity", "source_filter_vcf", "source_qual_vcf", "source_info_vcf",
    ]
    excluded_fields = [*fields, "exclusion_reason"]
    normalized_csv = output_dir / "normalized_variants.csv"
    excluded_csv = output_dir / "normalization_excluded_audit.csv"
    write_csv(normalized_csv, normalized_rows, fields)
    write_csv(excluded_csv, excluded_rows, excluded_fields)

    expected = int(stats.get("observed_source_alleles", 0))
    valid_rate = (int(stats.get("normalized_observed_alleles", 0)) / expected) if expected else 0.0
    summary = {
        "status": "valid" if expected else "invalid",
        "startedAt": started_at,
        "completedAt": utc_now(),
        "assembly": assembly,
        "inputPath": str(input_path),
        "normalizedVcfPath": str(normalized_vcf),
        "normalizedVariantsCsv": str(normalized_csv),
        "normalizationExcludedAuditCsv": str(excluded_csv),
        "reference": {"path": str(reference_path), **load_reference_manifest(payload.get("referenceManifestPath"))},
        "bcftools": bcftools,
        "counts": {**dict(stats), "excluded": len(excluded_rows), "normalizationValidRate": valid_rate},
        "qualityGate": {"minimumNormalizationValidRate": 0.99, "passed": valid_rate >= 0.99},
    }
    summary_path = output_dir / "normalization_summary.json"
    write_json(summary_path, summary)
    summary["normalizationSummaryJson"] = str(summary_path)
    print(json.dumps(summary, ensure_ascii=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64")
    parser.add_argument("--probe-assembly", action="store_true")
    parser.add_argument("--input")
    args = parser.parse_args()
    if args.probe_assembly:
        if not args.input:
            raise ValueError("--input is required with --probe-assembly")
        print(json.dumps(detect_assembly_from_header(Path(args.input)), ensure_ascii=True))
        return 0
    if not args.input_json_base64:
        raise ValueError("--input-json-base64 is required")
    payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    process(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=True))
        raise
