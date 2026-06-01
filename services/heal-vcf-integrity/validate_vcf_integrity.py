#!/usr/bin/env python3
"""
Streaming VCF integrity validator for HEAL by FON.

The script accepts a local file reference, never loads the full VCF in memory,
and returns a single JSON object on stdout for n8n to parse.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ALLOWED_ROOTS = [
    SCRIPT_DIR / "incoming",
    SCRIPT_DIR / "samples",
    Path(r"C:\ServerCIT\n8n\tmp\heal-vcf-integrity"),
]
VCF_BASE_COLUMNS = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def resolve_allowed_roots(values: Iterable[str | os.PathLike[str]] | None) -> list[Path]:
    roots = list(values or DEFAULT_ALLOWED_ROOTS)
    resolved = []
    for root in roots:
        if root:
            resolved.append(Path(root).expanduser().resolve(strict=False))
    return resolved


def is_under_allowed_root(path: Path, roots: list[Path]) -> bool:
    if not roots:
        return True
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def read_magic(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(2)


def open_text_stream(path: Path, is_gzip: bool):
    if is_gzip:
        return gzip.open(path, mode="rt", encoding="utf-8", errors="replace", newline="")
    return path.open("rt", encoding="utf-8", errors="replace", newline="")


def sha256_file(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            bytes_read += len(chunk)
            digest.update(chunk)
    return {
        "algorithm": "sha256",
        "value": digest.hexdigest(),
        "bytes_read": bytes_read,
        "status": "calculated",
    }


def parse_gt(format_value: str | None, sample_value: str | None) -> str | None:
    if not format_value or not sample_value:
        return None
    keys = format_value.split(":")
    values = sample_value.split(":")
    try:
        gt_index = keys.index("GT")
    except ValueError:
        return None
    if gt_index >= len(values):
        return None
    return values[gt_index]


def classify_gt(gt: str | None) -> str:
    if not gt or gt == ".":
        return "gt_missing_or_partial"
    alleles = gt.replace("|", "/").split("/")
    if any(allele in {"", "."} for allele in alleles):
        return "gt_missing_or_partial"
    if len(alleles) != 2:
        return "gt_non_diploid_or_complex"
    if alleles[0] == "0" and alleles[1] == "0":
        return "gt_hom_ref"
    if alleles[0] == alleles[1]:
        return "gt_hom_alt"
    return "gt_het"


def calculate_variant_stats(path: Path, is_gzip: bool) -> dict[str, Any]:
    stats: Counter[str] = Counter()
    chrom_counts: Counter[str] = Counter()
    sample_name = None
    column_count = None
    started = time.perf_counter()

    with open_text_stream(path, is_gzip) as handle:
        for raw_line in handle:
            if not raw_line or raw_line.startswith("##"):
                continue
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#CHROM"):
                columns = line.split("\t")
                column_count = len(columns)
                sample_name = columns[9] if len(columns) > 9 else None
                continue
            if line.startswith("#"):
                continue

            fields = line.split("\t")
            if len(fields) < 8:
                stats["rows_malformed"] += 1
                continue

            stats["total_variant_rows"] += 1
            chrom_counts[fields[0]] += 1

            variant_id = fields[2]
            if variant_id and variant_id != ".":
                stats["rows_with_id"] += 1
                ids = [part.strip().lower() for part in variant_id.split(";") if part.strip()]
                if any(part.startswith("rs") for part in ids):
                    stats["rows_with_rsid"] += 1

            filter_value = fields[6]
            if filter_value in {"PASS", ".", ""}:
                stats["rows_pass"] += 1

            alts = [alt for alt in fields[4].split(",") if alt]
            if len(alts) > 1:
                stats["rows_multiallelic"] += 1

            is_snv = len(fields[3]) == 1 and all(len(alt) == 1 for alt in alts)
            stats["rows_snv" if is_snv else "rows_non_snv"] += 1

            gt = None
            if len(fields) >= 10:
                gt = parse_gt(fields[8], fields[9])
            stats[classify_gt(gt)] += 1

    elapsed = time.perf_counter() - started
    expected_keys = [
        "total_variant_rows",
        "rows_with_id",
        "rows_with_rsid",
        "rows_pass",
        "rows_multiallelic",
        "rows_snv",
        "rows_non_snv",
        "gt_missing_or_partial",
        "gt_non_diploid_or_complex",
        "gt_hom_ref",
        "gt_hom_alt",
        "gt_het",
        "rows_malformed",
    ]
    counts = {key: int(stats.get(key, 0)) for key in expected_keys}
    return {
        "status": "calculated",
        "sample_name": sample_name,
        "column_count": column_count,
        "counts": counts,
        "top_chromosomes": [
            {"chrom": chrom, "count": count}
            for chrom, count in chrom_counts.most_common(15)
        ],
        "duration_ms": round(elapsed * 1000, 3),
        "parser": "streaming",
    }


def classify_pysam_gt(gt: Any) -> str:
    if gt is None:
        return "gt_missing_or_partial"
    alleles = list(gt) if isinstance(gt, tuple) else [gt]
    if not alleles or any(allele is None or allele < 0 for allele in alleles):
        return "gt_missing_or_partial"
    if len(alleles) != 2:
        return "gt_non_diploid_or_complex"
    if alleles[0] == 0 and alleles[1] == 0:
        return "gt_hom_ref"
    if alleles[0] == alleles[1]:
        return "gt_hom_alt"
    return "gt_het"


def calculate_variant_stats_pysam(path: Path) -> dict[str, Any]:
    try:
        import pysam  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pysam is not installed in the configured Python environment.") from exc

    stats: Counter[str] = Counter()
    chrom_counts: Counter[str] = Counter()
    sample_name = None
    column_count = None
    started = time.perf_counter()

    with pysam.VariantFile(str(path)) as vcf:
        samples = list(vcf.header.samples)
        sample_name = samples[0] if samples else None
        column_count = 9 + len(samples) if samples else 8

        for record in vcf:
            stats["total_variant_rows"] += 1
            chrom_counts[str(record.chrom)] += 1

            variant_id = str(record.id or "")
            if variant_id and variant_id != ".":
                stats["rows_with_id"] += 1
                ids = [part.strip().lower() for part in variant_id.split(";") if part.strip()]
                if any(part.startswith("rs") for part in ids):
                    stats["rows_with_rsid"] += 1

            filters = list(record.filter.keys())
            if not filters or "PASS" in filters or "." in filters:
                stats["rows_pass"] += 1

            alts = list(record.alts or [])
            if len(alts) > 1:
                stats["rows_multiallelic"] += 1

            is_snv = len(record.ref or "") == 1 and all(len(alt or "") == 1 for alt in alts)
            stats["rows_snv" if is_snv else "rows_non_snv"] += 1

            gt = None
            if sample_name:
                gt = record.samples[sample_name].get("GT")
            stats[classify_pysam_gt(gt)] += 1

    elapsed = time.perf_counter() - started
    expected_keys = [
        "total_variant_rows",
        "rows_with_id",
        "rows_with_rsid",
        "rows_pass",
        "rows_multiallelic",
        "rows_snv",
        "rows_non_snv",
        "gt_missing_or_partial",
        "gt_non_diploid_or_complex",
        "gt_hom_ref",
        "gt_hom_alt",
        "gt_het",
        "rows_malformed",
    ]
    counts = {key: int(stats.get(key, 0)) for key in expected_keys}
    return {
        "status": "calculated",
        "sample_name": sample_name,
        "column_count": column_count,
        "counts": counts,
        "top_chromosomes": [
            {"chrom": chrom, "count": count}
            for chrom, count in chrom_counts.most_common(15)
        ],
        "duration_ms": round(elapsed * 1000, 3),
        "parser": "pysam",
    }


def validate_full_gzip(path: Path) -> dict[str, Any]:
    decompressed_bytes = 0
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            decompressed_bytes += len(chunk)
    return {
        "status": "readable",
        "decompressed_bytes_read": decompressed_bytes,
    }


def parse_vcf_headers_and_variants(path: Path, is_gzip: bool, max_variants: int) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {
        "fileformat": None,
        "column_header": None,
        "column_count": None,
        "sample_count": None,
        "samples": [],
        "metadata_header_lines_seen": 0,
        "variant_rows_checked": 0,
        "first_variant": None,
    }

    seen_column_header = False
    variant_row_number = 0

    with open_text_stream(path, is_gzip) as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue

            if line.startswith("##"):
                metadata["metadata_header_lines_seen"] += 1
                if line.startswith("##fileformat=") and metadata["fileformat"] is None:
                    metadata["fileformat"] = line.split("=", 1)[1]
                continue

            if line.startswith("#CHROM"):
                seen_column_header = True
                columns = line.split("\t")
                metadata["column_header"] = columns
                metadata["column_count"] = len(columns)
                if columns[:8] != VCF_BASE_COLUMNS:
                    errors.append("Column header does not start with required VCF base columns.")
                if len(columns) < 8:
                    errors.append("Column header has fewer than 8 VCF columns.")
                if len(columns) == 8:
                    warnings.append("VCF has no FORMAT/sample columns.")
                elif len(columns) == 9:
                    warnings.append("VCF has FORMAT but no sample column.")
                else:
                    metadata["samples"] = columns[9:]
                    metadata["sample_count"] = len(columns[9:])
                    if len(columns[9:]) > 1:
                        warnings.append("VCF appears to contain more than one sample.")
                continue

            variant_row_number += 1
            if not seen_column_header:
                errors.append(f"Variant row found before #CHROM header at line {line_number}.")
                break

            fields = line.split("\t")
            if metadata["first_variant"] is None:
                metadata["first_variant"] = {
                    "line_number": line_number,
                    "chrom": fields[0] if len(fields) > 0 else None,
                    "pos": fields[1] if len(fields) > 1 else None,
                    "field_count": len(fields),
                }

            metadata["variant_rows_checked"] += 1
            prefix = f"Variant row {variant_row_number} (line {line_number})"
            expected_cols = metadata["column_count"] or 8

            if len(fields) < 8:
                errors.append(f"{prefix} has fewer than 8 fields.")
            elif len(fields) != expected_cols:
                errors.append(f"{prefix} has {len(fields)} fields; expected {expected_cols}.")

            if len(fields) >= 2:
                try:
                    if int(fields[1]) <= 0:
                        errors.append(f"{prefix} POS must be a positive integer.")
                except ValueError:
                    errors.append(f"{prefix} POS is not an integer.")

            if len(fields) >= 5:
                if not fields[0] or not fields[3] or not fields[4]:
                    errors.append(f"{prefix} has empty CHROM, REF, or ALT.")

            if metadata["variant_rows_checked"] >= max_variants:
                break

    if metadata["fileformat"] is None:
        errors.append("Missing ##fileformat=VCF header.")
    elif not str(metadata["fileformat"]).upper().startswith("VCFV"):
        errors.append("##fileformat header is present but does not look like VCFv*.")

    if not seen_column_header:
        errors.append("Missing #CHROM column header.")

    if seen_column_header and metadata["variant_rows_checked"] == 0:
        warnings.append("No variant rows were found in the checked portion of the file.")

    return {"errors": errors, "warnings": warnings, "metadata": metadata}


def validate(config: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    start_perf = time.perf_counter()
    errors: list[str] = []
    warnings: list[str] = []

    file_path_value = config.get("filePath") or config.get("path") or config.get("localPath") or config.get("vcfPath")
    calculate_checksum = boolish(config.get("calculateChecksum"), default=False)
    calculate_stats = boolish(config.get("calculateStats"), default=False)
    full_gzip_check = boolish(config.get("fullGzipCheck"), default=True)
    max_variants = as_int(config.get("maxVariantsToCheck"), 20)
    vcf_parser = str(config.get("vcfParser") or config.get("parser") or "streaming").strip().lower()
    if vcf_parser not in {"streaming", "pysam"}:
        warnings.append(f"Unknown VCF parser '{vcf_parser}', using streaming parser.")
        vcf_parser = "streaming"
    allowed_roots = resolve_allowed_roots(config.get("allowedRoots"))

    metadata: dict[str, Any] = {
        "input_reference_type": "local_path",
        "path": file_path_value,
        "allowed_roots": [str(root) for root in allowed_roots],
        "exists": False,
        "accessible": False,
        "size_bytes": None,
        "detected_format": None,
        "extension": None,
    }
    checksum: dict[str, Any] = {
        "algorithm": "sha256",
        "status": "not_requested",
        "value": None,
    }
    gzip_integrity: dict[str, Any] = {
        "status": "not_applicable",
    }
    variant_stats: dict[str, Any] = {
        "status": "not_requested",
    }

    if not file_path_value:
        errors.append("Missing filePath/path input.")
    else:
        candidate = Path(str(file_path_value)).expanduser().resolve(strict=False)
        metadata["path"] = str(candidate)
        metadata["extension"] = "".join(candidate.suffixes[-2:]) if candidate.name.endswith(".vcf.gz") else candidate.suffix

        if not is_under_allowed_root(candidate, allowed_roots):
            errors.append("Path is outside the configured allowed roots.")
        elif not candidate.exists():
            errors.append("File does not exist.")
        elif not candidate.is_file():
            errors.append("Path exists but is not a file.")
        else:
            metadata["exists"] = True
            try:
                size_bytes = candidate.stat().st_size
                metadata["size_bytes"] = size_bytes
                if size_bytes <= 0:
                    errors.append("File size is zero.")
                else:
                    metadata["accessible"] = True
                    magic = read_magic(candidate)
                    has_gzip_magic = magic == b"\x1f\x8b"
                    extension_says_gzip = candidate.name.lower().endswith(".vcf.gz") or candidate.name.lower().endswith(".gz")
                    extension_says_vcf = candidate.name.lower().endswith(".vcf")
                    is_gzip = has_gzip_magic or extension_says_gzip

                    if extension_says_gzip and not has_gzip_magic:
                        errors.append("File extension indicates gzip, but gzip magic bytes are missing.")
                    if has_gzip_magic and not extension_says_gzip:
                        warnings.append("File has gzip magic bytes but does not use a .gz extension.")

                    if is_gzip:
                        metadata["detected_format"] = "vcf.gz"
                    elif extension_says_vcf:
                        metadata["detected_format"] = "vcf"
                    else:
                        metadata["detected_format"] = "unknown_text_vcf_candidate"
                        warnings.append("File extension is not .vcf or .vcf.gz; attempting VCF validation anyway.")

                    parsed = parse_vcf_headers_and_variants(candidate, is_gzip, max_variants)
                    errors.extend(parsed["errors"])
                    warnings.extend(parsed["warnings"])
                    metadata.update(parsed["metadata"])

                    if is_gzip:
                        if full_gzip_check:
                            gzip_integrity = validate_full_gzip(candidate)
                        else:
                            gzip_integrity = {
                                "status": "prepared",
                                "note": "Set fullGzipCheck=true to stream-read the full gzip payload.",
                            }

                    if calculate_checksum:
                        checksum = sha256_file(candidate)
                    else:
                        checksum = {
                            "algorithm": "sha256",
                            "status": "prepared",
                            "value": None,
                            "note": "Set calculateChecksum=true to calculate SHA-256 by streaming the file.",
                        }

                    if calculate_stats:
                        if vcf_parser == "pysam":
                            try:
                                variant_stats = calculate_variant_stats_pysam(candidate)
                            except Exception as exc:  # noqa: BLE001 - parser fallback is intentional.
                                warnings.append(f"pysam metrics failed, used streaming parser instead: {exc}")
                                variant_stats = calculate_variant_stats(candidate, is_gzip)
                                variant_stats["requested_parser"] = "pysam"
                        else:
                            variant_stats = calculate_variant_stats(candidate, is_gzip)
            except gzip.BadGzipFile as exc:
                errors.append(f"Gzip read failed: {exc}")
            except OSError as exc:
                errors.append(f"File access failed: {exc}")
            except UnicodeError as exc:
                errors.append(f"Text decode failed: {exc}")

    status = "invalid" if errors else ("warning" if warnings else "valid")
    ended = utc_now()
    return {
        "status": status,
        "valid": status == "valid",
        "errors": errors,
        "warnings": warnings,
        "metadata": metadata,
        "gzip_integrity": gzip_integrity,
        "checksum": checksum,
        "variant_stats": variant_stats,
        "timestamps": {
            "started_at": started,
            "ended_at": ended,
            "duration_ms": round((time.perf_counter() - start_perf) * 1000, 3),
        },
        "validator": {
            "name": "heal-vcf-integrity",
            "version": "0.1.0",
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate VCF integrity using streaming reads.")
    parser.add_argument("--path", dest="path")
    parser.add_argument("--input-json-base64", dest="input_json_base64")
    parser.add_argument("--checksum", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--allowed-root", action="append", default=None)
    parser.add_argument("--skip-full-gzip-check", action="store_true")
    parser.add_argument("--vcf-parser", choices=["streaming", "pysam"], default=None)
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if args.input_json_base64:
        decoded = base64.b64decode(args.input_json_base64).decode("utf-8")
        loaded = json.loads(decoded)
        if not isinstance(loaded, dict):
            raise ValueError("Input JSON must be an object.")
        config.update(loaded)
    if args.path:
        config["filePath"] = args.path
    if args.checksum:
        config["calculateChecksum"] = True
    if args.stats:
        config["calculateStats"] = True
    if args.max_variants:
        config["maxVariantsToCheck"] = args.max_variants
    if args.allowed_root:
        config["allowedRoots"] = args.allowed_root
    if args.skip_full_gzip_check:
        config["fullGzipCheck"] = False
    if args.vcf_parser:
        config["vcfParser"] = args.vcf_parser
    return config


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        config = config_from_args(args)
        result = validate(config)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result["status"] in {"valid", "warning"} else 2
    except Exception as exc:
        result = {
            "status": "invalid",
            "valid": False,
            "errors": [f"Validator failed before file validation: {exc}"],
            "warnings": [],
            "metadata": {},
            "timestamps": {"started_at": utc_now(), "ended_at": utc_now()},
            "validator": {"name": "heal-vcf-integrity", "version": "0.1.0"},
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
