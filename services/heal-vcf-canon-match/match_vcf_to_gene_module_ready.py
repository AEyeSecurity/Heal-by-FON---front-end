#!/usr/bin/env python3
"""Match HEAL gene-module canon targets against a VCF by gene envelopes and merged features."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict]:
    opener = gzip.open if path.name.lower().endswith(".gz") else Path.open
    with opener(path, "rt" if opener is gzip.open else "r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_str(value) -> str:
    return "" if value is None else str(value).strip()


def open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def split_gt(gt: str) -> list[str]:
    if not gt:
        return []
    return re.split(r"[\/|]", gt)


def gt_to_alleles(gt: str, ref: str, alt: str) -> str:
    if not gt:
        return ""
    allele_map = [ref] + [item.strip() for item in clean_str(alt).split(",") if item.strip()]
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


def zygosity_from_gt(gt: str) -> str:
    parts = split_gt(gt)
    if not parts or any(part in {"", "."} for part in parts):
        return "unknown"
    if len(parts) != 2:
        return "non_diploid_or_complex"
    return "homozygous" if parts[0] == parts[1] else "heterozygous"


def allele_dosage(gt: str, allele_index: int) -> int:
    """Return copies of one ALT allele in a possibly multi-allelic genotype."""
    dosage = 0
    for token in split_gt(gt):
        try:
            if int(token) == allele_index:
                dosage += 1
        except ValueError:
            continue
    return dosage


def zygosity_for_alt(gt: str, dosage: int) -> str:
    parts = split_gt(gt)
    if not parts or any(part in {"", "."} for part in parts):
        return "unknown"
    if len(parts) != 2:
        return "non_diploid_or_complex"
    if dosage == 2:
        return "homozygous"
    if dosage == 1:
        return "heterozygous"
    return "reference_only"


def extract_gt(format_value: str, sample_value: str) -> str:
    format_keys = clean_str(format_value).split(":")
    sample_values = clean_str(sample_value).split(":")
    try:
        index = format_keys.index("GT")
    except ValueError:
        return ""
    return sample_values[index] if index < len(sample_values) else ""


def normalize_chromosome(value: str) -> str:
    chrom = clean_str(value).upper()
    if not chrom:
        return ""
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "MT":
        chrom = "M"
    return f"chr{chrom}"


def stable_variant_key(assembly: str, chrom: str, start: int, ref: str, alt: str) -> str:
    source = "|".join([clean_str(assembly), clean_str(chrom), str(start), clean_str(ref).upper(), clean_str(alt).upper()])
    return f"v2_{hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]}"


def parse_info(info_value: str) -> dict[str, str]:
    info = {}
    for part in clean_str(info_value).split(";"):
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            info[key] = value
        else:
            info[part] = "true"
    return info


def parse_original_record(info: dict[str, str], chrom: str, pos: str, ref: str, alt: str) -> dict[str, str]:
    """Read bcftools --old-rec-tag ORIG when normalization changed a record."""
    original = clean_str(info.get("ORIG"))
    if not original:
        return {
            "source_chrom_vcf": chrom,
            "source_pos_vcf": pos,
            "source_ref_vcf": ref,
            "source_alt_vcf": alt,
            "source_alt_index": "1",
        }
    parts = original.split("|")
    if len(parts) < 4:
        return {
            "source_chrom_vcf": chrom,
            "source_pos_vcf": pos,
            "source_ref_vcf": ref,
            "source_alt_vcf": alt,
            "source_alt_index": "",
        }
    return {
        "source_chrom_vcf": normalize_chromosome(parts[0]) or chrom,
        "source_pos_vcf": clean_str(parts[1]) or pos,
        "source_ref_vcf": clean_str(parts[2]) or ref,
        "source_alt_vcf": clean_str(parts[3]) or alt,
        "source_alt_index": clean_str(parts[4]) if len(parts) > 4 else "",
    }


def compute_variant_interval(pos: str, ref: str, alt: str, info: dict[str, str]) -> tuple[int, int, list[str]]:
    warnings = []
    start = int(pos)
    if "END" in info:
        try:
            end = int(info["END"])
            return start, end, warnings
        except ValueError:
            warnings.append(f"Invalid END={info['END']} at {pos}; falling back to REF length.")
    ref_text = clean_str(ref)
    alt_text = clean_str(alt)
    if alt_text.startswith("<") or alt_text.endswith(">"):
        warnings.append(f"Symbolic ALT {alt_text or '<missing>'} handled conservatively at {pos}.")
    end = start + max(1, len(ref_text)) - 1
    return start, end, warnings


def intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return min(end_a, end_b) >= max(start_a, start_b)


def feature_interval_list(value) -> list[dict]:
    """Accept both current list indexes and older singleton feature objects."""
    if isinstance(value, dict):
        value = [value] if "start" in value and "end" in value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and "start" in item and "end" in item]


def scan_vcf(vcf_path: Path, envelope_index: dict) -> tuple[list[dict], dict, list[str]]:
    candidates = []
    sample_name = ""
    scanned_rows = 0
    warnings = []
    chromosome_index = envelope_index.get("chromosomes") or {}
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
            chrom, pos, id_vcf, ref, alt, qual, filter_vcf, info_value = parts[:8]
            chrom_normalized = normalize_chromosome(chrom)
            envelopes = chromosome_index.get(chrom_normalized) or []
            # Accept compact single-envelope records emitted by older v2 canon
            # runs, while ignoring malformed index values safely.
            if isinstance(envelopes, dict):
                envelopes = [envelopes]
            if not isinstance(envelopes, list):
                continue
            if not envelopes:
                continue
            info = parse_info(info_value)
            fmt = parts[8] if len(parts) > 8 else ""
            sample = parts[9] if len(parts) > 9 else ""
            gt_raw = extract_gt(fmt, sample)
            gt_alleles = gt_to_alleles(gt_raw, ref, alt)
            for allele_index, allele_alt in enumerate([item.strip() for item in clean_str(alt).split(",") if item.strip()], start=1):
                dosage = allele_dosage(gt_raw, allele_index)
                if dosage <= 0:
                    continue
                variant_start, variant_end, interval_warnings = compute_variant_interval(pos, ref, allele_alt, info)
                warnings.extend(interval_warnings)
                overlapping_envelopes = [
                    envelope
                    for envelope in envelopes
                    if isinstance(envelope, dict)
                    and intervals_overlap(variant_start, variant_end, int(envelope["start"]), int(envelope["end"]))
                ]
                if not overlapping_envelopes:
                    continue
                original_record = parse_original_record(info, chrom_normalized, clean_str(pos), clean_str(ref), allele_alt)
                for envelope in overlapping_envelopes:
                    candidates.append(
                        {
                            "chrom_vcf": chrom_normalized,
                            "pos_vcf": clean_str(pos),
                            "variant_start": variant_start,
                            "variant_end": variant_end,
                            "id_vcf": clean_str(id_vcf),
                            "ref_vcf": clean_str(ref),
                            "alt_vcf": allele_alt,
                            "allele_index": str(allele_index),
                            "allele_dosage": str(dosage),
                            "variant_key": stable_variant_key(
                                clean_str(envelope_index.get("assembly")), chrom_normalized, variant_start, clean_str(ref), allele_alt
                            ),
                            "qual_vcf": clean_str(qual),
                            "filter_vcf": clean_str(filter_vcf),
                            "quality_flag": "pass" if clean_str(filter_vcf) in {"", ".", "PASS"} else "filtered",
                            "info_vcf": clean_str(info_value),
                            "gt_raw": gt_raw,
                            "gt_alleles": gt_alleles,
                            "zygosity": zygosity_for_alt(gt_raw, dosage),
                            **original_record,
                            "gene_id": envelope["gene_id"],
                            "approved_symbol": envelope["symbol"],
                            "gene_envelope_start": int(envelope["start"]),
                            "gene_envelope_end": int(envelope["end"]),
                            "gene_envelope_match": "true",
                        }
                    )
    return candidates, {"sample_name": sample_name, "scanned_variant_rows": scanned_rows, "vcf_parser_used": "streaming"}, warnings


def classify_local_region(merged_features: dict[str, list[dict]], variant_start: int, variant_end: int) -> dict:
    overlaps = {}
    for feature_type, intervals in merged_features.items():
        hits = [
            interval
            for interval in feature_interval_list(intervals)
            if intervals_overlap(variant_start, variant_end, int(interval["start"]), int(interval["end"]))
        ]
        if hits:
            overlaps[feature_type] = hits

    if overlaps.get("mane_cds_union"):
        return {
            "local_region_class": "mane_cds_overlap",
            "local_feature_priority": "high",
            "annotation_needed": "true",
            "background_only": "false",
            "overlap_feature_types": "mane_cds_union",
        }
    if overlaps.get("splice_window_union"):
        return {
            "local_region_class": "splice_region_candidate",
            "local_feature_priority": "high",
            "annotation_needed": "true",
            "background_only": "false",
            "overlap_feature_types": "splice_window_union",
        }
    if overlaps.get("protein_coding_cds_union"):
        return {
            "local_region_class": "alternative_protein_coding_cds_overlap",
            "local_feature_priority": "medium_high",
            "annotation_needed": "true",
            "background_only": "false",
            "overlap_feature_types": "protein_coding_cds_union",
        }
    if overlaps.get("protein_coding_exon_union"):
        return {
            "local_region_class": "protein_coding_exon_non_cds_overlap",
            "local_feature_priority": "medium",
            "annotation_needed": "true",
            "background_only": "false",
            "overlap_feature_types": "protein_coding_exon_union",
        }
    if overlaps.get("utr_union"):
        return {
            "local_region_class": "utr_overlap",
            "local_feature_priority": "medium",
            "annotation_needed": "optional",
            "background_only": "false",
            "overlap_feature_types": "utr_union",
        }
    if overlaps.get("noncoding_exon_union"):
        return {
            "local_region_class": "noncoding_exon_overlap",
            "local_feature_priority": "exploratory",
            "annotation_needed": "optional",
            "background_only": "false",
            "overlap_feature_types": "noncoding_exon_union",
        }
    if overlaps.get("transcript_body_union"):
        return {
            "local_region_class": "intronic_transcript_overlap",
            "local_feature_priority": "low",
            "annotation_needed": "false",
            "background_only": "true",
            "overlap_feature_types": "transcript_body_union",
        }
    return {
        "local_region_class": "gene_envelope_only",
        "local_feature_priority": "background",
        "annotation_needed": "false",
        "background_only": "true",
        "overlap_feature_types": "gene_envelope_only",
    }


def build_gene_indexes(clean_rows: list[dict], gene_master_rows: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    clean_by_row_id = {clean_str(row.get("canon_row_id")): row for row in clean_rows}
    modules_by_gene_id: dict[str, list[dict]] = defaultdict(list)
    gene_master_by_id = {}
    for gene in gene_master_rows:
        gene_id = clean_str(gene.get("gene_id"))
        gene_master_by_id[gene_id] = gene
        for row_id in [item.strip() for item in clean_str(gene.get("row_ids")).split(",") if item.strip()]:
            if row_id in clean_by_row_id:
                modules_by_gene_id[gene_id].append(clean_by_row_id[row_id])
    return modules_by_gene_id, gene_master_by_id


def normalization_source_index(path_value: str | None) -> dict[str, dict]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Normalized variant CSV does not exist: {path}")
    return {clean_str(row.get("variant_key")): row for row in read_csv(path) if clean_str(row.get("variant_key"))}


def process(canon_clean_path: Path, gene_master_path: Path, envelope_index_path: Path, merged_index_path: Path, vcf_path: Path, output_dir: Path, normalized_variants_csv: str | None = None) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_rows = read_csv(canon_clean_path)
    gene_master_rows = read_csv(gene_master_path)
    envelope_index = read_json(envelope_index_path)
    merged_index = read_json(merged_index_path)
    modules_by_gene_id, gene_master_by_id = build_gene_indexes(clean_rows, gene_master_rows)
    normalized_sources = normalization_source_index(normalized_variants_csv)

    vcf_candidates, scan_meta, warnings = scan_vcf(vcf_path, envelope_index)
    for candidate in vcf_candidates:
        source = normalized_sources.get(clean_str(candidate.get("variant_key")))
        if source:
            candidate.update(
                {
                    "source_chrom_vcf": clean_str(source.get("source_chrom_vcf")) or clean_str(candidate.get("source_chrom_vcf")),
                    "source_pos_vcf": clean_str(source.get("source_pos_vcf")) or clean_str(candidate.get("source_pos_vcf")),
                    "source_ref_vcf": clean_str(source.get("source_ref_vcf")) or clean_str(candidate.get("source_ref_vcf")),
                    "source_alt_vcf": clean_str(source.get("source_alt_vcf")) or clean_str(candidate.get("source_alt_vcf")),
                    "source_alt_index": clean_str(source.get("source_alt_index")) or clean_str(candidate.get("source_alt_index")),
                    "source_gt_raw": clean_str(source.get("source_gt_raw")),
                    "source_allele_dosage": clean_str(source.get("source_allele_dosage")),
                    "source_zygosity": clean_str(source.get("source_zygosity")),
                    "source_filter_vcf": clean_str(source.get("source_filter_vcf")),
                }
            )
        else:
            candidate.update(
                {
                    "source_gt_raw": clean_str(candidate.get("gt_raw")),
                    "source_allele_dosage": clean_str(candidate.get("allele_dosage")),
                    "source_zygosity": clean_str(candidate.get("zygosity")),
                    "source_filter_vcf": clean_str(candidate.get("filter_vcf")),
                }
            )
    variant_gene_rows = []
    consolidated_rows = []
    grouped_features = merged_index.get("features_by_gene") or {}
    gene_metadata = merged_index.get("gene_metadata") or {}

    for candidate_index, candidate in enumerate(vcf_candidates, start=1):
        gene_id = candidate["gene_id"]
        merged_features = grouped_features.get(gene_id) or {}
        local = classify_local_region(merged_features, int(candidate["variant_start"]), int(candidate["variant_end"]))
        gene_meta = gene_master_by_id.get(gene_id, {})
        variant_gene_row = {
            "variant_gene_id": f"variant-gene-{candidate_index:06d}",
            **candidate,
            "assembly_name": envelope_index.get("assembly") or "",
            "gene_chrom": clean_str(gene_meta.get("chrom")) or clean_str((gene_metadata.get(gene_id) or {}).get("chrom")),
            "gene_biotype": clean_str(gene_meta.get("biotype")),
            "module_count": len(modules_by_gene_id.get(gene_id) or []),
            **local,
        }
        variant_gene_rows.append(variant_gene_row)
        for module_row in modules_by_gene_id.get(gene_id) or []:
            consolidated_rows.append(
                {
                    "variant_gene_module_id": f"{variant_gene_row['variant_gene_id']}:{module_row['canon_row_id']}",
                    "gene_id": gene_id,
                    "canon_row_id": clean_str(module_row.get("canon_row_id")),
                    "module_id": clean_str(module_row.get("module_id")),
                    "module_name": clean_str(module_row.get("module_name")),
                    "system_within_module": clean_str(module_row.get("system_within_module")),
                    "tier": clean_str(module_row.get("tier")),
                    "module_status": clean_str(module_row.get("module_status")),
                    "is_draft": "true" if clean_str(module_row.get("is_draft")) in {"1", "true", "True"} else "false",
                    "epistemic_mode": clean_str(module_row.get("epistemic_mode")),
                    "harm_risk": clean_str(module_row.get("harm_risk")),
                    "evidence_tier": clean_str(module_row.get("evidence_tier")),
                    "misinterpretation_risk": clean_str(module_row.get("misinterpretation_risk")),
                    "myth_correction_required": clean_str(module_row.get("myth_correction_required")),
                    "canonical_version": clean_str(module_row.get("canonical_version")),
                    "module_purpose": clean_str(module_row.get("module_purpose")),
                    "explicit_exclusions": clean_str(module_row.get("explicit_exclusions")),
                    "notes": clean_str(module_row.get("notes")),
                    "gene_symbol_original": clean_str(module_row.get("gene_symbol_original")),
                    "full_gene_name": clean_str(module_row.get("full_gene_name")),
                    "approved_symbol": clean_str(variant_gene_row.get("approved_symbol")),
                    "assembly_name": clean_str(variant_gene_row.get("assembly_name")),
                    "chrom_vcf": clean_str(variant_gene_row.get("chrom_vcf")),
                    "pos_vcf": clean_str(variant_gene_row.get("pos_vcf")),
                    "variant_start": variant_gene_row["variant_start"],
                    "variant_end": variant_gene_row["variant_end"],
                    "id_vcf": clean_str(variant_gene_row.get("id_vcf")),
                    "ref_vcf": clean_str(variant_gene_row.get("ref_vcf")),
                    "alt_vcf": clean_str(variant_gene_row.get("alt_vcf")),
                    "allele_index": clean_str(variant_gene_row.get("allele_index")),
                    "allele_dosage": clean_str(variant_gene_row.get("allele_dosage")),
                    "variant_key": clean_str(variant_gene_row.get("variant_key")),
                    "qual_vcf": clean_str(variant_gene_row.get("qual_vcf")),
                    "filter_vcf": clean_str(variant_gene_row.get("filter_vcf")),
                    "quality_flag": clean_str(variant_gene_row.get("quality_flag")),
                    "source_chrom_vcf": clean_str(variant_gene_row.get("source_chrom_vcf")),
                    "source_pos_vcf": clean_str(variant_gene_row.get("source_pos_vcf")),
                    "source_ref_vcf": clean_str(variant_gene_row.get("source_ref_vcf")),
                    "source_alt_vcf": clean_str(variant_gene_row.get("source_alt_vcf")),
                    "source_alt_index": clean_str(variant_gene_row.get("source_alt_index")),
                    "source_gt_raw": clean_str(variant_gene_row.get("source_gt_raw")),
                    "source_allele_dosage": clean_str(variant_gene_row.get("source_allele_dosage")),
                    "source_zygosity": clean_str(variant_gene_row.get("source_zygosity")),
                    "source_filter_vcf": clean_str(variant_gene_row.get("source_filter_vcf")),
                    "gt_raw": clean_str(variant_gene_row.get("gt_raw")),
                    "gt_alleles": clean_str(variant_gene_row.get("gt_alleles")),
                    "zygosity": clean_str(variant_gene_row.get("zygosity")),
                    "has_genotype": "true" if int(variant_gene_row.get("allele_dosage") or 0) > 0 else "false",
                    "gene_envelope_match": "true",
                    "gene_envelope_start": variant_gene_row["gene_envelope_start"],
                    "gene_envelope_end": variant_gene_row["gene_envelope_end"],
                    "local_region_class": clean_str(variant_gene_row.get("local_region_class")),
                    "local_feature_priority": clean_str(variant_gene_row.get("local_feature_priority")),
                    "annotation_needed": clean_str(variant_gene_row.get("annotation_needed")),
                    "background_only": clean_str(variant_gene_row.get("background_only")),
                    "overlap_feature_types": clean_str(variant_gene_row.get("overlap_feature_types")),
                }
            )

    consolidated_rows.sort(
        key=lambda row: (
            clean_str(row.get("chrom_vcf")),
            int(row.get("variant_start") or 0),
            clean_str(row.get("approved_symbol")),
            clean_str(row.get("module_id")),
        )
    )

    candidate_fields = [
        "chrom_vcf",
        "pos_vcf",
        "variant_start",
        "variant_end",
        "id_vcf",
        "ref_vcf",
        "alt_vcf",
        "allele_index",
        "allele_dosage",
        "variant_key",
        "qual_vcf",
        "filter_vcf",
        "quality_flag",
        "source_chrom_vcf",
        "source_pos_vcf",
        "source_ref_vcf",
        "source_alt_vcf",
        "source_alt_index",
        "source_gt_raw",
        "source_allele_dosage",
        "source_zygosity",
        "source_filter_vcf",
        "gt_raw",
        "gt_alleles",
        "zygosity",
        "gene_id",
        "approved_symbol",
        "gene_envelope_start",
        "gene_envelope_end",
        "gene_envelope_match",
    ]
    variant_gene_fields = [
        "variant_gene_id",
        *candidate_fields,
        "assembly_name",
        "gene_chrom",
        "gene_biotype",
        "module_count",
        "local_region_class",
        "local_feature_priority",
        "annotation_needed",
        "background_only",
        "overlap_feature_types",
    ]
    consolidated_fields = [
        "variant_gene_module_id",
        "gene_id",
        "canon_row_id",
        "module_id",
        "module_name",
        "system_within_module",
        "tier",
        "module_status",
        "is_draft",
        "epistemic_mode",
        "harm_risk",
        "evidence_tier",
        "misinterpretation_risk",
        "myth_correction_required",
        "canonical_version",
        "module_purpose",
        "explicit_exclusions",
        "notes",
        "gene_symbol_original",
        "full_gene_name",
        "approved_symbol",
        "assembly_name",
        "chrom_vcf",
        "pos_vcf",
        "variant_start",
        "variant_end",
        "id_vcf",
        "ref_vcf",
        "alt_vcf",
        "allele_index",
        "allele_dosage",
        "variant_key",
        "qual_vcf",
        "filter_vcf",
        "quality_flag",
        "source_chrom_vcf",
        "source_pos_vcf",
        "source_ref_vcf",
        "source_alt_vcf",
        "source_alt_index",
        "source_gt_raw",
        "source_allele_dosage",
        "source_zygosity",
        "source_filter_vcf",
        "gt_raw",
        "gt_alleles",
        "zygosity",
        "has_genotype",
        "gene_envelope_match",
        "gene_envelope_start",
        "gene_envelope_end",
        "local_region_class",
        "local_feature_priority",
        "annotation_needed",
        "background_only",
        "overlap_feature_types",
    ]

    write_csv(output_dir / "vcf_gene_envelope_candidates.csv", vcf_candidates, candidate_fields)
    write_csv(output_dir / "vcf_variant_gene_matches.csv", variant_gene_rows, variant_gene_fields)
    write_csv(output_dir / "sheet_final_consolidated.csv", consolidated_rows, consolidated_fields)
    write_csv(
        output_dir / "sheet_final_annotation_needed.csv",
        [row for row in consolidated_rows if row["annotation_needed"] == "true"],
        consolidated_fields,
    )
    write_csv(
        output_dir / "sheet_final_optional_annotation.csv",
        [row for row in consolidated_rows if row["annotation_needed"] == "optional"],
        consolidated_fields,
    )
    write_csv(
        output_dir / "sheet_final_background_only.csv",
        [row for row in consolidated_rows if row["background_only"] == "true"],
        consolidated_fields,
    )
    write_csv(output_dir / "sheet_final_no_match_rows.csv", [], consolidated_fields)

    summary = {
        "status": "valid",
        "errors": [],
        "warnings": warnings,
        "schemaVersion": "gene_module_v2",
        "adapter": "gene_module_canon_adapter",
        "inputPaths": {
            "canonCleanCsv": str(canon_clean_path),
            "geneMasterCsv": str(gene_master_path),
            "geneEnvelopeIndexJson": str(envelope_index_path),
            "mergedFeatureIndexJson": str(merged_index_path),
            "vcfPath": str(vcf_path),
        },
        "outputDir": str(output_dir),
        "metadata": {
            "variant_gene_candidates": len(vcf_candidates),
            "variant_gene_rows": len(variant_gene_rows),
            "sheet_final_rows": len(consolidated_rows),
            "unique_gene_matches": len({row["gene_id"] for row in consolidated_rows}),
            "background_rows": sum(1 for row in consolidated_rows if row["background_only"] == "true"),
            "annotation_needed_rows": sum(1 for row in consolidated_rows if row["annotation_needed"] == "true"),
            "optional_annotation_rows": sum(1 for row in consolidated_rows if row["annotation_needed"] == "optional"),
            "downstream_supported": False,
            **scan_meta,
        },
        "outputs": {
            "vcfCandidatesCsv": str(output_dir / "vcf_gene_envelope_candidates.csv"),
            "vcfJoinedChrPosCsv": str(output_dir / "vcf_variant_gene_matches.csv"),
            "sheetFinalConsolidatedCsv": str(output_dir / "sheet_final_consolidated.csv"),
            "sheetFinalMatchStrictCsv": str(output_dir / "sheet_final_annotation_needed.csv"),
            "sheetFinalMatchLikelyNeedsAltReviewCsv": str(output_dir / "sheet_final_optional_annotation.csv"),
            "sheetFinalMatchByPositionNeedsReviewCsv": str(output_dir / "sheet_final_background_only.csv"),
            "sheetFinalNoVcfMatchByChrPosCsv": str(output_dir / "sheet_final_no_match_rows.csv"),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "vcf_canon_match_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match HEAL gene-module canon targets to a VCF.")
    parser.add_argument("--canon-clean")
    parser.add_argument("--gene-master")
    parser.add_argument("--gene-envelope-index")
    parser.add_argument("--merged-feature-index")
    parser.add_argument("--vcf")
    parser.add_argument("--output-dir")
    parser.add_argument("--normalized-variants-csv")
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.canon_clean = payload.get("canonCleanPath")
        args.gene_master = payload.get("geneMasterPath")
        args.gene_envelope_index = payload.get("geneEnvelopeIndexPath")
        args.merged_feature_index = payload.get("mergedFeatureIndexPath")
        args.vcf = payload.get("vcfPath")
        args.output_dir = payload.get("outputDir")
        args.normalized_variants_csv = payload.get("normalizedVariantsCsv")
    if not all([args.canon_clean, args.gene_master, args.gene_envelope_index, args.merged_feature_index, args.vcf, args.output_dir]):
        parser.error("--canon-clean, --gene-master, --gene-envelope-index, --merged-feature-index, --vcf, and --output-dir are required.")
    return args


def main() -> int:
    args = parse_args()
    process(
        Path(args.canon_clean),
        Path(args.gene_master),
        Path(args.gene_envelope_index),
        Path(args.merged_feature_index),
        Path(args.vcf),
        Path(args.output_dir),
        args.normalized_variants_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
