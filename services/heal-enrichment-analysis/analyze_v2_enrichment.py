"""Offline quality and utility analysis for a v2 enrichment cache.

The analyzer is intentionally separate from the enrichment runtime. It reads a
SQLite cache snapshot plus match/triage CSVs and writes audit artifacts to a
dedicated output directory. It never calls external APIs and never mutates the
input cache or production run artifacts.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable


SOURCES = (
    "ensembl_vep_region",
    "ensembl_variation",
    "clinvar",
    "myvariant",
    "gwas",
    "pharmgkb",
)
SECONDARY_SOURCES = SOURCES[1:]
SAMPLE_PER_CATEGORY = 6
EXPECTED_ASSEMBLIES = {"GRCh38", "GRCh37"}


def clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def nonempty(value: object) -> bool:
    return value not in (None, "", "0", [], {})


def number(value: object) -> float | None:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return None


def normalize_chromosome(value: object) -> str:
    chrom = clean(value).upper()
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "MT":
        chrom = "M"
    return f"chr{chrom}" if chrom else ""


def normalize_allele_signature(ref: object, alt: object) -> tuple[str, str]:
    ref_text = clean(ref).upper()
    alt_text = clean(alt).upper()
    while len(ref_text) > 1 and len(alt_text) > 1 and ref_text[0] == alt_text[0]:
        ref_text = ref_text[1:]
        alt_text = alt_text[1:]
    while len(ref_text) > 1 and len(alt_text) > 1 and ref_text[-1] == alt_text[-1]:
        ref_text = ref_text[:-1]
        alt_text = alt_text[:-1]
    return ref_text, alt_text


def normalized_indel_signature(ref: object, alt: object) -> tuple[str, str]:
    """Trim shared anchors while allowing an empty allele (VEP uses '-')."""
    ref_text = clean(ref).replace("-", "").upper()
    alt_text = clean(alt).replace("-", "").upper()
    while ref_text and alt_text and ref_text[0] == alt_text[0]:
        ref_text = ref_text[1:]
        alt_text = alt_text[1:]
    while ref_text and alt_text and ref_text[-1] == alt_text[-1]:
        ref_text = ref_text[:-1]
        alt_text = alt_text[:-1]
    return ref_text, alt_text


def split_alleles(value: object) -> list[str]:
    return [clean(part).upper() for part in re.split(r"[|/,]", clean(value)) if clean(part)]


def alleles_match(allele_string: object, ref: object, alt: object) -> tuple[bool, bool]:
    alleles = split_alleles(allele_string)
    ref_text = clean(ref).upper()
    alt_text = clean(alt).upper()
    if ref_text in alleles and alt_text in alleles:
        return True, False
    observed = normalized_indel_signature(ref_text, alt_text)
    for left_index, left in enumerate(alleles):
        for right_index, right in enumerate(alleles):
            if left_index == right_index:
                continue
            if normalized_indel_signature(left, right) == observed:
                return True, len(ref_text) != len(alt_text)
    return False, False


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    if fields is None:
        fields = []
        seen = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")


def read_cache(path: Path) -> dict[str, dict[str, dict]]:
    records: dict[str, dict[str, dict]] = defaultdict(dict)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    try:
        for row in connection.execute(
            """SELECT variant_key, source, response_json, status, http_status,
                      fetched_at, expires_at, pipeline_version, assembly
               FROM enrichment_cache"""
        ):
            try:
                payload = json.loads(row["response_json"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if row["source"] == "ensembl_vep_region":
                data = payload if isinstance(payload, dict) else {}
                error = ""
            else:
                data = payload.get("data") if isinstance(payload, dict) else {}
                error = clean(payload.get("error")) if isinstance(payload, dict) else ""
            records[row["variant_key"]][row["source"]] = {
                "status": clean(row["status"]) or "unknown",
                "http_status": row["http_status"],
                "fetched_at": clean(row["fetched_at"]),
                "expires_at": clean(row["expires_at"]),
                "pipeline_version": clean(row["pipeline_version"]),
                "assembly": clean(row["assembly"]),
                "data": data if isinstance(data, dict) else {},
                "error": error,
            }
    finally:
        connection.close()
    return dict(records)


def progress_snapshot(path: Path) -> dict:
    if not path.is_file():
        return {"available": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False}
    return {"available": True, **payload}


SAFE_TRIAGE_FIELDS = (
    "variant_key", "approved_symbol", "gene_symbol_original", "module_id", "module_name",
    "system_within_module", "tier", "module_status", "evidence_tier", "is_draft",
    "assembly_name", "chrom_vcf", "pos_vcf", "variant_start", "variant_end", "ref_vcf", "alt_vcf",
    "local_region_class", "local_feature_priority", "annotation_needed", "background_only",
    "triage_decision", "triage_reason", "gene_envelope_match",
)


def index_triage(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    indexed: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = clean(row.get("variant_key"))
        if key:
            indexed[key].append(row)
    return dict(indexed)


def safe_triage_row(row: dict[str, str]) -> dict[str, str]:
    return {field: clean(row.get(field)) for field in SAFE_TRIAGE_FIELDS if clean(row.get(field))}


def vep_data(record: dict[str, dict]) -> dict:
    return (record.get("ensembl_vep_region") or {}).get("data") or {}


def source_data(record: dict[str, dict], source: str) -> dict:
    return (record.get(source) or {}).get("data") or {}


def source_status(record: dict[str, dict], source: str) -> str:
    return clean((record.get(source) or {}).get("status")) or "not_queried"


def transcript_entries(data: dict) -> list[dict]:
    return [item for item in data.get("transcript_consequences") or [] if isinstance(item, dict)]


def has_transcript_value(data: dict, keys: tuple[str, ...]) -> bool:
    return any(nonempty(item.get(key)) for item in transcript_entries(data) for key in keys)


def colocated_rsids(data: dict) -> list[str]:
    return sorted({
        clean(item.get("id"))
        for item in data.get("colocated_variants") or []
        if isinstance(item, dict) and re.fullmatch(r"rs\d+", clean(item.get("id")), re.IGNORECASE)
    })


def expected_variant(row: dict[str, str]) -> dict[str, object]:
    chrom = normalize_chromosome(row.get("chrom_vcf"))
    try:
        start = int(clean(row.get("variant_start") or row.get("pos_vcf")))
    except (TypeError, ValueError):
        start = 0
    ref = clean(row.get("ref_vcf"))
    try:
        end = int(clean(row.get("variant_end")))
    except (TypeError, ValueError):
        end = start + max(len(ref), 1) - 1 if start else 0
    return {"chrom": chrom, "start": start, "end": end, "ref": ref, "alt": clean(row.get("alt_vcf"))}


def vep_identity(row: dict[str, str], record: dict[str, dict]) -> dict[str, object]:
    data = vep_data(record)
    source = record.get("ensembl_vep_region") or {}
    status = source_status(record, "ensembl_vep_region")
    expected = expected_variant(row)
    returned_chrom = normalize_chromosome(data.get("seq_region_name"))
    returned_start = data.get("start")
    returned_end = data.get("end")
    returned_alleles = clean(data.get("allele_string"))
    rsids = colocated_rsids(data)
    notes: list[str] = []
    if status == "source_error" or not data:
        return {
            "identity_class": "identity_error" if status != "not_queried" else "identity_error",
            "vep_status": status,
            "returned_chrom": returned_chrom,
            "returned_start": returned_start,
            "returned_end": returned_end,
            "returned_allele_string": returned_alleles,
            "vep_assembly": clean(data.get("assembly_name")),
            "colocated_rsids": "|".join(rsids),
            "notes": clean(source.get("error")) or "VEP response did not contain a usable item.",
        }
    if clean(data.get("assembly_name")) and clean(data.get("assembly_name")) != clean(row.get("assembly_name") or "GRCh38"):
        notes.append("VEP assembly differs from input assembly")
    try:
        coordinate_match = (
            returned_chrom == expected["chrom"]
            and int(returned_start) <= int(expected["end"])
            and int(returned_end or returned_start) >= int(expected["start"])
        )
    except (TypeError, ValueError):
        coordinate_match = False
    allele_match, normalized_indel = alleles_match(returned_alleles, expected["ref"], expected["alt"])
    if not coordinate_match and len(clean(expected["ref"])) != len(clean(expected["alt"])):
        # VEP represents anchored insertions/deletions with '-' and can report
        # an empty interval one base to the right of the VCF anchor.
        try:
            returned_start_int = int(returned_start)
            returned_end_int = int(returned_end or returned_start)
            candidate_coordinates = (
                returned_chrom == expected["chrom"]
                and returned_start_int in {int(expected["start"]), int(expected["start"]) + 1}
                and returned_end_int in {int(expected["start"]) - 1, int(expected["start"]), int(expected["end"])}
            )
            if candidate_coordinates and "-" in returned_alleles:
                expected_signature = normalized_indel_signature(expected["ref"], expected["alt"])
                returned_alleles_list = split_alleles(returned_alleles)
                candidate_coordinates = any(
                    normalized_indel_signature(left, right) == expected_signature
                    for left_index, left in enumerate(returned_alleles_list)
                    for right_index, right in enumerate(returned_alleles_list)
                    if left_index != right_index
                )
            if candidate_coordinates:
                coordinate_match = True
                allele_match = True
                normalized_indel = True
                notes.append("VEP anchored indel representation accepted")
        except (TypeError, ValueError):
            coordinate_match = False
    if not coordinate_match:
        notes.append("VEP returned coordinates do not overlap normalized input")
        identity_class = "identity_error"
    elif allele_match and normalized_indel:
        identity_class = "normalized_indel_match"
    elif allele_match:
        identity_class = "exact_coordinate_allele_match"
    elif rsids:
        identity_class = "rsid_without_allele_confirmation"
        notes.append("VEP colocated rsID exists but returned allele was not confirmed")
    else:
        identity_class = "identity_error"
        notes.append("VEP allele was not confirmed")
    if len(rsids) > 1 and identity_class in {"exact_coordinate_allele_match", "normalized_indel_match"}:
        notes.append("Multiple colocated rsIDs require review")
        identity_class = "ambiguous_identity"
    if not clean(data.get("assembly_name")):
        notes.append("VEP assembly was not reported")
    return {
        "identity_class": identity_class,
        "vep_status": status,
        "returned_chrom": returned_chrom,
        "returned_start": returned_start,
        "returned_end": returned_end,
        "returned_allele_string": returned_alleles,
        "vep_assembly": clean(data.get("assembly_name")),
        "colocated_rsids": "|".join(rsids),
        "notes": "; ".join(notes),
    }


def parse_coordinate_from_id(value: object) -> tuple[str, int, str, str] | None:
    match = re.search(r"(?:^|:)chr?([0-9XYM]+):g\.(\d+)([ACGT]+)>([ACGT]+)$", clean(value), re.IGNORECASE)
    if not match:
        return None
    return f"chr{match.group(1).upper()}", int(match.group(2)), match.group(3).upper(), match.group(4).upper()


def secondary_identity_flags(row: dict[str, str], record: dict[str, dict], identity: dict) -> list[str]:
    flags: list[str] = []
    expected = expected_variant(row)
    expected_assembly = clean(row.get("assembly_name") or "GRCh38")
    variation = source_data(record, "ensembl_variation")
    mapping_assembly = clean(variation.get("mapping_assembly"))
    if mapping_assembly and mapping_assembly != expected_assembly:
        flags.append("cross_assembly_match")
    best_id = source_data(record, "myvariant").get("best_id")
    parsed = parse_coordinate_from_id(best_id)
    if parsed and expected["chrom"] and (parsed[0] != expected["chrom"] or parsed[1] != expected["start"]):
        if identity["identity_class"] in {"exact_coordinate_allele_match", "normalized_indel_match"}:
            flags.append("assembly_representation_difference")
        else:
            flags.append("source_coordinate_difference")
    return flags


def clinical_values(record: dict[str, dict]) -> list[str]:
    values = []
    for source in ("ensembl_variation", "clinvar", "myvariant"):
        data = source_data(record, source)
        for key in ("clinical_significance", "clinvar_significance"):
            value = clean(data.get(key))
            if value:
                values.append(value)
    return values


def normalized_clinical_kind(value: object) -> str:
    text = clean(value).lower()
    if any(token in text for token in ("pathogenic", "likely pathogenic")):
        return "pathogenic"
    if any(token in text for token in ("benign", "likely benign")):
        return "benign"
    if "conflict" in text:
        return "conflicting"
    if "risk factor" in text:
        return "risk_factor"
    return "other" if text else ""


def conflict_flags(row: dict[str, str], record: dict[str, dict], identity: dict) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    clinical = clinical_values(record)
    kinds = {normalized_clinical_kind(value) for value in clinical if normalized_clinical_kind(value)}
    clinvar = source_data(record, "clinvar")
    review_text = clean(clinvar.get("review_status")).lower()
    if "conflict" in review_text or "conflict" in clean(clinvar.get("clinical_significance")).lower():
        flags.append({"type": "clinical_conflict", "details": "ClinVar reports conflicting evidence or review text.", "values": "|".join(clinical)})
    if "pathogenic" in kinds and "benign" in kinds:
        flags.append({"type": "benign_vs_pathogenic_conflict", "details": "Clinical classifications disagree across sources.", "values": "|".join(clinical)})
    flags.extend({"type": flag, "details": "Secondary source coordinate requires assembly/representation review.", "values": ""} for flag in secondary_identity_flags(row, record, identity))
    local_class = clean(row.get("local_region_class"))
    all_transcripts = transcript_entries(vep_data(record))
    target_gene = clean(row.get("approved_symbol")).upper()
    target_transcripts = [item for item in all_transcripts if clean(item.get("gene_symbol")).upper() == target_gene]
    relevant_transcripts = target_transcripts or all_transcripts
    consequences = "|".join(
        term
        for item in relevant_transcripts
        for term in (item.get("consequence_terms") or [])
        if clean(term)
    )
    consequence = consequences or clean(vep_data(record).get("most_severe_consequence"))
    coding_local = local_class in {"mane_cds_overlap", "alternative_protein_coding_cds_overlap", "protein_coding_exon_non_cds_overlap"}
    noncoding_vep = any(token in consequence for token in ("intron_variant", "non_coding_transcript_exon_variant"))
    if coding_local and noncoding_vep and not any(token in consequence for token in ("missense", "stop_gained", "frameshift", "splice_acceptor", "splice_donor")):
        flags.append({"type": "consequence_region_disagreement", "details": "Local canon feature is coding but selected VEP consequence is noncoding/intronic.", "values": consequence})
    for source in SOURCES:
        item = record.get(source)
        if item and item["status"] == "success" and not item["data"]:
            flags.append({"type": "source_payload_incomplete", "details": f"{source} returned success with an empty payload.", "values": ""})
    return flags


def field_specs() -> list[tuple[str, str, str, Callable[[dict], bool]]]:
    return [
        ("ensembl_vep_region", "most_severe_consequence", "VEP most severe consequence", lambda d: nonempty(d.get("most_severe_consequence"))),
        ("ensembl_vep_region", "transcript_consequences", "VEP transcript consequences", lambda d: nonempty(d.get("transcript_consequences"))),
        ("ensembl_vep_region", "gene_symbol", "VEP gene symbol", lambda d: has_transcript_value(d, ("gene_symbol",))),
        ("ensembl_vep_region", "hgvsc", "VEP HGVS cDNA", lambda d: has_transcript_value(d, ("hgvsc",))),
        ("ensembl_vep_region", "hgvsp", "VEP HGVS protein", lambda d: has_transcript_value(d, ("hgvsp",))),
        ("ensembl_vep_region", "mane_or_canonical", "VEP MANE or canonical transcript", lambda d: has_transcript_value(d, ("mane_select", "mane_plus_clinical", "canonical"))),
        ("ensembl_vep_region", "cadd_phred", "VEP CADD", lambda d: has_transcript_value(d, ("cadd_phred",))),
        ("ensembl_vep_region", "revel", "VEP REVEL", lambda d: has_transcript_value(d, ("revel_score", "revel"))),
        ("ensembl_vep_region", "alphamissense", "VEP AlphaMissense", lambda d: has_transcript_value(d, ("alphamissense", "alphamissense_pred"))),
        ("ensembl_vep_region", "spliceai", "VEP SpliceAI", lambda d: has_transcript_value(d, ("spliceai",))),
        ("ensembl_vep_region", "colocated_variants", "VEP colocated variants", lambda d: nonempty(d.get("colocated_variants"))),
        ("ensembl_variation", "clinical_significance", "Ensembl clinical significance", lambda d: nonempty(d.get("clinical_significance"))),
        ("ensembl_variation", "phenotypes", "Ensembl phenotypes", lambda d: nonempty(d.get("phenotypes"))),
        ("ensembl_variation", "population_frequency", "Ensembl population frequency", lambda d: nonempty(d.get("maf")) or nonempty(d.get("populations"))),
        ("clinvar", "record_count", "ClinVar records", lambda d: nonempty(d.get("count")) and clean(d.get("count")) != "0"),
        ("clinvar", "classification", "ClinVar classification", lambda d: nonempty(d.get("clinical_significance"))),
        ("clinvar", "review_status", "ClinVar review status", lambda d: nonempty(d.get("review_status"))),
        ("clinvar", "traits", "ClinVar traits", lambda d: nonempty(d.get("trait_names"))),
        ("myvariant", "hits", "MyVariant hits", lambda d: nonempty(d.get("hits")) and clean(d.get("hits")) != "0"),
        ("myvariant", "clinvar_significance", "MyVariant ClinVar significance", lambda d: nonempty(d.get("clinvar_significance"))),
        ("myvariant", "cadd_phred", "MyVariant CADD", lambda d: nonempty(d.get("cadd_phred"))),
        ("gwas", "association_count", "GWAS associations", lambda d: nonempty(d.get("association_count")) and clean(d.get("association_count")) != "0"),
        ("gwas", "min_pvalue", "GWAS minimum p-value", lambda d: nonempty(d.get("min_pvalue"))),
        ("gwas", "traits", "GWAS traits", lambda d: nonempty(d.get("top_traits"))),
        ("pharmgkb", "clinical_annotation_count", "PharmGKB clinical annotations", lambda d: nonempty(d.get("clinical_annotation_count")) and clean(d.get("clinical_annotation_count")) != "0"),
        ("pharmgkb", "variant_annotation_count", "PharmGKB variant annotations", lambda d: nonempty(d.get("variant_annotation_count")) and clean(d.get("variant_annotation_count")) != "0"),
        ("pharmgkb", "variant_id", "PharmGKB variant ID", lambda d: nonempty(d.get("variant_id"))),
        ("pharmgkb", "clinical_significance", "PharmGKB clinical significance", lambda d: nonempty(d.get("variant_clinical_significance"))),
    ]


def usable_flags(record: dict[str, dict], identity: dict) -> dict[str, bool]:
    vep = vep_data(record)
    vep_functional = source_status(record, "ensembl_vep_region") == "success" and any([
        nonempty(vep.get("most_severe_consequence")),
        has_transcript_value(vep, ("hgvsc", "hgvsp")),
        has_transcript_value(vep, ("cadd_phred", "revel_score", "revel", "alphamissense", "alphamissense_pred", "spliceai")),
    ])
    clinical = any(
        source_status(record, source) == "success" and nonempty(value)
        for source in ("ensembl_variation", "clinvar", "myvariant")
        for value in clinical_values({source: record.get(source)})
    )
    pharm = source_status(record, "pharmgkb") == "success" and (
        nonempty(source_data(record, "pharmgkb").get("clinical_annotation_count"))
        and clean(source_data(record, "pharmgkb").get("clinical_annotation_count")) != "0"
        or nonempty(source_data(record, "pharmgkb").get("variant_annotation_count"))
        and clean(source_data(record, "pharmgkb").get("variant_annotation_count")) != "0"
    )
    gwas = source_status(record, "gwas") == "success" and nonempty(source_data(record, "gwas").get("association_count")) and clean(source_data(record, "gwas").get("association_count")) != "0"
    population = source_status(record, "ensembl_variation") == "success" and (nonempty(source_data(record, "ensembl_variation").get("maf")) or nonempty(source_data(record, "ensembl_variation").get("populations")))
    secondary_success = any(source_status(record, source) == "success" for source in SECONDARY_SOURCES)
    source_errors = any(source_status(record, source) == "source_error" for source in SOURCES)
    not_queried = all(source_status(record, source) == "not_queried" for source in SECONDARY_SOURCES)
    return {
        "vep_functional": vep_functional,
        "clinical": clinical,
        "pharm": pharm,
        "gwas": gwas,
        "population": population,
        "secondary_success": secondary_success,
        "source_errors": source_errors,
        "not_queried": not_queried,
        "identity_ambiguous": identity["identity_class"] in {"ambiguous_identity", "rsid_without_allele_confirmation"},
        "vep_success": source_status(record, "ensembl_vep_region") == "success",
    }


def evidence_category(record: dict[str, dict], identity: dict) -> tuple[str, list[str]]:
    flags = usable_flags(record, identity)
    remediation: list[str] = []
    if flags["source_errors"]:
        remediation.append("source_error")
    if flags["not_queried"]:
        remediation.append("not_queried")
    if identity["identity_class"] not in {"exact_coordinate_allele_match", "normalized_indel_match"}:
        remediation.append(identity["identity_class"])
    if flags["identity_ambiguous"]:
        return "identity_ambiguous", remediation
    if not flags["vep_success"] and flags["source_errors"]:
        return "source_error_unresolved", remediation
    if flags["not_queried"] and not flags["secondary_success"]:
        return "not_queried_unresolved", remediation
    if flags["clinical"]:
        return "clinical_evidence", remediation
    if flags["pharm"]:
        return "pharmacogenomic_evidence", remediation
    if flags["gwas"]:
        return "association_evidence", remediation
    if flags["secondary_success"] and flags["vep_functional"] and not flags["population"]:
        return "complete_multisource_evidence", remediation
    if flags["population"]:
        return "population_context", remediation
    if flags["source_errors"]:
        return "source_error_unresolved", remediation
    if flags["vep_functional"]:
        return "functional_vep_only", remediation
    if flags["vep_success"]:
        return "vep_identity_only", remediation
    return "source_error_unresolved", remediation


def source_statuses(record: dict[str, dict]) -> dict[str, str]:
    return {source: source_status(record, source) for source in SOURCES}


def source_error_text(record: dict[str, dict], source: str) -> str:
    return clean((record.get(source) or {}).get("error"))


def source_time_bounds(record: dict[str, dict]) -> tuple[str, str]:
    timestamps = sorted(clean((record.get(source) or {}).get("fetched_at")) for source in SOURCES if clean((record.get(source) or {}).get("fetched_at")))
    return (timestamps[0], timestamps[-1]) if timestamps else ("", "")


def compact_source_values(record: dict[str, dict]) -> dict:
    vep = vep_data(record)
    transcripts = transcript_entries(vep)
    selected = next((item for item in transcripts if nonempty(item.get("hgvsp") or item.get("hgvsc"))), transcripts[0] if transcripts else {})
    variation = source_data(record, "ensembl_variation")
    clinvar = source_data(record, "clinvar")
    myvariant = source_data(record, "myvariant")
    gwas = source_data(record, "gwas")
    pharm = source_data(record, "pharmgkb")
    return {
        "vep": {
            "input": clean(vep.get("input")), "consequence": clean(vep.get("most_severe_consequence")),
            "gene": clean(selected.get("gene_symbol")), "hgvsc": clean(selected.get("hgvsc")),
            "hgvsp": clean(selected.get("hgvsp")), "cadd": clean(selected.get("cadd_phred")),
            "revel": clean(selected.get("revel_score") or selected.get("revel")),
            "alphamissense": clean(selected.get("alphamissense_pred") or selected.get("alphamissense")),
            "spliceai": selected.get("spliceai") or "", "colocated_rsids": colocated_rsids(vep),
        },
        "ensembl_variation": {"clinical_significance": clean(variation.get("clinical_significance")), "maf": clean(variation.get("maf")), "phenotypes": clean(variation.get("phenotypes"))},
        "clinvar": {"count": clean(clinvar.get("count")), "classification": clean(clinvar.get("clinical_significance")), "review_status": clean(clinvar.get("review_status")), "traits": clean(clinvar.get("trait_names"))},
        "myvariant": {"hits": clean(myvariant.get("hits")), "best_id": clean(myvariant.get("best_id")), "clinvar_significance": clean(myvariant.get("clinvar_significance")), "cadd": clean(myvariant.get("cadd_phred"))},
        "gwas": {"association_count": clean(gwas.get("association_count")), "min_pvalue": clean(gwas.get("min_pvalue")), "top_traits": clean(gwas.get("top_traits"))},
        "pharmgkb": {"variant_id": clean(pharm.get("variant_id")), "clinical_annotation_count": clean(pharm.get("clinical_annotation_count")), "variant_annotation_count": clean(pharm.get("variant_annotation_count")), "clinical_significance": clean(pharm.get("variant_clinical_significance"))},
    }


def analyze_variant(key: str, rows: list[dict[str, str]], record: dict[str, dict]) -> tuple[dict, dict, list[dict]]:
    row = rows[0] if rows else {"variant_key": key}
    identity = vep_identity(row, record)
    category, remediation = evidence_category(record, identity)
    conflicts = conflict_flags(row, record, identity)
    start_time, end_time = source_time_bounds(record)
    safe = safe_triage_row(row)
    modules = sorted({f"{clean(item.get('approved_symbol'))}|{clean(item.get('module_id'))}" for item in rows if clean(item.get("approved_symbol")) and clean(item.get("module_id"))})
    status = source_statuses(record)
    matrix = {
        "variant_key": key,
        **safe,
        "triage_row_count": len(rows),
        "module_count": len(modules),
        "gene_module_ids": "|".join(modules),
        "vep_identity_class": identity["identity_class"],
        "vep_status": identity["vep_status"],
        "vep_returned_chrom": identity["returned_chrom"],
        "vep_returned_start": identity["returned_start"],
        "vep_returned_end": identity["returned_end"],
        "vep_returned_allele_string": identity["returned_allele_string"],
        "vep_assembly": identity["vep_assembly"],
        "vep_colocated_rsids": identity["colocated_rsids"],
        "vep_identity_notes": identity["notes"],
        "evidence_category": category,
        "remediation_flags": "|".join(remediation),
        "conflict_flags": "|".join(item["type"] for item in conflicts),
        "source_fetched_at_first": start_time,
        "source_fetched_at_last": end_time,
        "secondary_source_statuses": json.dumps(status, ensure_ascii=True, sort_keys=True),
        "source_error_sources": "|".join(source for source in SOURCES if status[source] == "source_error"),
        "not_queried_sources": "|".join(source for source in SOURCES if status[source] == "not_queried"),
        "vep_consequence": clean(vep_data(record).get("most_severe_consequence")),
        "vep_hgvs_present": str(has_transcript_value(vep_data(record), ("hgvsc", "hgvsp"))).lower(),
        "vep_cadd_present": str(has_transcript_value(vep_data(record), ("cadd_phred",))).lower(),
        "vep_revel_present": str(has_transcript_value(vep_data(record), ("revel_score", "revel"))).lower(),
        "vep_alphamissense_present": str(has_transcript_value(vep_data(record), ("alphamissense", "alphamissense_pred"))).lower(),
        "vep_spliceai_present": str(has_transcript_value(vep_data(record), ("spliceai",))).lower(),
        "clinvar_record_present": str(field_specs()[14][3](source_data(record, "clinvar"))).lower(),
        "gwas_association_present": str(field_specs()[21][3](source_data(record, "gwas"))).lower(),
        "pharmgkb_annotation_present": str(field_specs()[24][3](source_data(record, "pharmgkb")) or field_specs()[25][3](source_data(record, "pharmgkb"))).lower(),
        "ensembl_maf": clean(source_data(record, "ensembl_variation").get("maf")),
        "myvariant_best_id": clean(source_data(record, "myvariant").get("best_id")),
    }
    for source in SOURCES:
        item = record.get(source) or {}
        matrix[f"source_status_{source}"] = status[source]
        matrix[f"source_http_status_{source}"] = item.get("http_status") if item else ""
        matrix[f"source_error_{source}"] = source_error_text(record, source)
    identity_row = {
        "variant_key": key,
        **safe,
        "expected_assembly": clean(row.get("assembly_name") or "GRCh38"),
        "expected_chrom": expected_variant(row)["chrom"],
        "expected_start": expected_variant(row)["start"],
        "expected_end": expected_variant(row)["end"],
        "expected_ref": expected_variant(row)["ref"],
        "expected_alt": expected_variant(row)["alt"],
        "identity_class": identity["identity_class"],
        "vep_status": identity["vep_status"],
        "vep_assembly": identity["vep_assembly"],
        "vep_returned_chrom": identity["returned_chrom"],
        "vep_returned_start": identity["returned_start"],
        "vep_returned_end": identity["returned_end"],
        "vep_returned_allele_string": identity["returned_allele_string"],
        "vep_colocated_rsids": identity["colocated_rsids"],
        "identity_notes": identity["notes"],
        "ensembl_mapping_assembly": clean(source_data(record, "ensembl_variation").get("mapping_assembly")),
        "ensembl_mapping_location": clean(source_data(record, "ensembl_variation").get("mapping_location")),
        "myvariant_best_id": clean(source_data(record, "myvariant").get("best_id")),
        "secondary_identity_flags": "|".join(secondary_identity_flags(row, record, identity)),
    }
    return matrix, identity_row, conflicts


def completeness_rows(records: dict[str, dict[str, dict]]) -> list[dict]:
    total = len(records)
    output = []
    for source, field, description, predicate in field_specs():
        queried = [record for record in records.values() if source_status(record, source) != "not_queried"]
        successful = [record for record in records.values() if source_status(record, source) == "success"]
        populated_all = sum(predicate(source_data(record, source) if source != "ensembl_vep_region" else vep_data(record)) for record in records.values())
        populated_queried = sum(predicate(source_data(record, source) if source != "ensembl_vep_region" else vep_data(record)) for record in queried)
        populated_success = sum(predicate(source_data(record, source) if source != "ensembl_vep_region" else vep_data(record)) for record in successful)
        output.append({
            "source": source, "field": field, "description": description,
            "all_variants": total, "queried_variants": len(queried), "successful_variants": len(successful),
            "populated_all": populated_all, "populated_queried": populated_queried, "populated_success": populated_success,
            "coverage_all_pct": round(populated_all / total * 100, 3) if total else 0,
            "coverage_queried_pct": round(populated_queried / len(queried) * 100, 3) if queried else 0,
            "coverage_success_pct": round(populated_success / len(successful) * 100, 3) if successful else 0,
        })
    return output


def choose_sample_categories(matrix_by_key: dict[str, dict], records: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    def ordered(predicate: Callable[[dict], bool]) -> list[str]:
        return [key for key, row in sorted(matrix_by_key.items()) if predicate(row)]

    categories = {
        "coding_or_missense_vep_rich": ordered(lambda row: clean(row.get("local_region_class")) in {"mane_cds_overlap", "alternative_protein_coding_cds_overlap", "protein_coding_exon_non_cds_overlap"} and row.get("vep_status") == "success" and row.get("vep_hgvs_present") == "true"),
        "spliceai_positive": ordered(lambda row: row.get("vep_spliceai_present") == "true"),
        "clinvar_success": ordered(lambda row: row.get("source_status_clinvar") == "success" and row.get("clinvar_record_present") == "true"),
        "gwas_success": ordered(lambda row: row.get("source_status_gwas") == "success" and row.get("gwas_association_present") == "true"),
        "pharmgkb_success": ordered(lambda row: row.get("source_status_pharmgkb") == "success" and row.get("pharmgkb_annotation_present") == "true"),
        "vep_only": ordered(lambda row: row.get("evidence_category") in {"functional_vep_only", "vep_identity_only", "not_queried_unresolved"}),
        "source_error": ordered(lambda row: row.get("source_error_clinvar") or row.get("source_error_ensembl_vep_region")),
        "indel_or_ambiguous": ordered(lambda row: len(clean(row.get("ref_vcf"))) != len(clean(row.get("alt_vcf"))) or row.get("vep_identity_class") in {"normalized_indel_match", "ambiguous_identity", "rsid_without_allele_confirmation"}),
    }
    selected: dict[str, list[str]] = {}
    already_selected: set[str] = set()
    for category, candidates in categories.items():
        fresh = [key for key in candidates if key not in already_selected]
        chosen = fresh[:SAMPLE_PER_CATEGORY]
        if len(chosen) < SAMPLE_PER_CATEGORY:
            chosen.extend(key for key in candidates if key not in chosen and key not in already_selected)
        selected[category] = chosen[:SAMPLE_PER_CATEGORY]
        already_selected.update(selected[category])
    return selected


def build_sample_rows(matrix_by_key: dict[str, dict], records: dict[str, dict[str, dict]], selected: dict[str, list[str]]) -> list[dict]:
    categories_by_key: dict[str, list[str]] = defaultdict(list)
    for category, keys in selected.items():
        for key in keys:
            categories_by_key[key].append(category)
    rows = []
    for key in sorted(categories_by_key):
        matrix = matrix_by_key[key]
        conflicts = matrix.get("conflict_flags", "").split("|") if matrix.get("conflict_flags") else []
        rows.append({
            "sample_categories": categories_by_key[key],
            "variant": {field: matrix.get(field, "") for field in SAFE_TRIAGE_FIELDS if field in matrix},
            "identity": {field: matrix.get(field, "") for field in ("vep_identity_class", "vep_status", "vep_returned_chrom", "vep_returned_start", "vep_returned_end", "vep_returned_allele_string", "vep_assembly", "vep_colocated_rsids", "vep_identity_notes")},
            "evidence_category": matrix.get("evidence_category"),
            "remediation_flags": matrix.get("remediation_flags"),
            "conflict_flags": conflicts,
            "source_statuses": {source: matrix.get(f"source_status_{source}") for source in SOURCES},
            "source_errors": {source: matrix.get(f"source_error_{source}") for source in SOURCES if matrix.get(f"source_error_{source}")},
            "evidence": compact_source_values(records[key]),
        })
    return rows


def group_quality_rows(triage_by_key: dict[str, list[dict[str, str]]], matrix_by_key: dict[str, dict]) -> list[dict]:
    groups: dict[str, list[str]] = defaultdict(list)
    group_meta: dict[str, dict] = {}
    for key, rows in triage_by_key.items():
        selected_rows = [row for row in rows if clean(row.get("triage_decision")) in {"", "include_ai"}]
        for row in selected_rows:
            gene = clean(row.get("approved_symbol"))
            module = clean(row.get("module_id"))
            if not gene or not module:
                continue
            group_id = f"{gene}|{module}"
            if key not in groups[group_id]:
                groups[group_id].append(key)
            group_meta[group_id] = row
    output = []
    for group_id in sorted(groups):
        keys = groups[group_id]
        evidence = Counter(matrix_by_key[key].get("evidence_category") for key in keys)
        local_classes = Counter(matrix_by_key[key].get("local_region_class") for key in keys if matrix_by_key[key].get("local_region_class"))
        source_errors = Counter(source for key in keys for source in SOURCES if matrix_by_key[key].get(f"source_status_{source}") == "source_error")
        output.append({
            "group_id": group_id,
            "approved_symbol": clean(group_meta[group_id].get("approved_symbol")),
            "module_id": clean(group_meta[group_id].get("module_id")),
            "module_name": clean(group_meta[group_id].get("module_name")),
            "system_within_module": clean(group_meta[group_id].get("system_within_module")),
            "tier": clean(group_meta[group_id].get("tier")),
            "module_status": clean(group_meta[group_id].get("module_status")),
            "evidence_tier": clean(group_meta[group_id].get("evidence_tier")),
            "variant_count": len(keys),
            "evidence_category_counts": json.dumps(dict(evidence), ensure_ascii=True, sort_keys=True),
            "local_region_class_counts": json.dumps(dict(local_classes), ensure_ascii=True, sort_keys=True),
            "source_error_counts": json.dumps(dict(source_errors), ensure_ascii=True, sort_keys=True),
            "variants_with_clinical_evidence": sum(matrix_by_key[key].get("evidence_category") == "clinical_evidence" for key in keys),
            "variants_with_pharmacogenomic_evidence": sum(matrix_by_key[key].get("evidence_category") == "pharmacogenomic_evidence" for key in keys),
            "variants_with_gwas_evidence": sum(matrix_by_key[key].get("evidence_category") == "association_evidence" for key in keys),
            "variants_vep_only": sum(matrix_by_key[key].get("evidence_category") in {"functional_vep_only", "vep_identity_only"} for key in keys),
            "variants_with_errors": sum(bool(matrix_by_key[key].get("remediation_flags")) for key in keys),
        })
    return output


def error_metrics(records: dict[str, dict[str, dict]]) -> dict:
    source_status_counts = {source: dict(Counter(source_status(record, source) for record in records.values())) for source in SOURCES}
    errors = Counter()
    error_times = Counter()
    http_statuses = Counter()
    for record in records.values():
        for source in SOURCES:
            item = record.get(source) or {}
            if item.get("status") == "source_error":
                errors[(source, clean(item.get("error"))[-240:] or "<empty_error>")] += 1
                fetched_at = clean(item.get("fetched_at"))
                error_times[(source, fetched_at[:13] if fetched_at else "unknown")] += 1
            if item.get("http_status") is not None:
                http_statuses[(source, str(item.get("http_status")))] += 1
    return {
        "source_status_counts": source_status_counts,
        "top_errors": [{"source": source, "error": error, "count": count} for (source, error), count in errors.most_common(30)],
        "error_time_buckets": [{"source": source, "hour": hour, "count": count} for (source, hour), count in sorted(error_times.items())],
        "http_status_counts": [{"source": source, "http_status": status, "count": count} for (source, status), count in sorted(http_statuses.items())],
        "cache_hit_metrics_available": False,
        "cache_hit_metrics_note": "The baseline cache schema stores responses but not per-run cache hit/miss events.",
    }


def render_report(summary: dict, completeness: list[dict], matrix: list[dict], conflicts: list[dict], samples: list[dict], groups: list[dict]) -> str:
    categories = Counter(row.get("evidence_category") for row in matrix)
    lines = [
        "# V2 Enrichment Analysis Report",
        "",
        "This report is an offline audit of a preserved enrichment cache. It is not a clinical interpretation.",
        "",
        "## Scope",
        "",
        f"- Physical variants analyzed: {summary['physical_variants']}",
        f"- Triage rows joined: {summary['triage_rows']}",
        f"- Gene-module groups analyzed: {summary['gene_module_groups']}",
        f"- Manual sample records: {len(samples)}",
        "",
        "## Evidence Categories",
        "",
        "| Category | Variants |",
        "|---|---:|",
    ]
    lines.extend(f"| {category} | {count} |" for category, count in sorted(categories.items()))
    lines.extend(["", "## Source Status", "", "| Source | Status | Count |", "|---|---|---:|"])
    for source, statuses in summary["source_status_counts"].items():
        for status, count in sorted(statuses.items()):
            lines.append(f"| {source} | {status} | {count} |")
    lines.extend(["", "## Field Completeness", "", "| Source | Field | All variants % | Queried % | Successful % |", "|---|---|---:|---:|---:|"])
    for row in completeness:
        lines.append(f"| {row['source']} | {row['field']} | {row['coverage_all_pct']} | {row['coverage_queried_pct']} | {row['coverage_success_pct']} |")
    lines.extend(["", "## Conflicts", "", f"- Conflict records: {len(conflicts)}", "- Source errors are not treated as not-found evidence.", "- Coordinate differences from sources without explicit assembly are retained as review flags.", "", "## Limitations", ""])
    for limitation in summary.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.extend(["", "## Outputs", "", "The companion CSV/JSONL artifacts contain the complete matrix, identity audit, conflict records, deterministic sample and group-level quality summary."])
    return "\n".join(lines) + "\n"


def process(payload: dict) -> dict:
    cache_path = Path(payload["cachePath"])
    triage_path = Path(payload["triagePath"])
    matching_path = Path(payload.get("matchingPath") or "")
    output_dir = Path(payload["outputDir"])
    if not cache_path.is_file():
        raise ValueError(f"Cache SQLite was not found: {cache_path}")
    records = read_cache(cache_path)
    triage_rows = read_csv_rows(triage_path)
    matching_rows = read_csv_rows(matching_path) if matching_path.is_file() else []
    triage_by_key = index_triage(triage_rows)
    all_keys = sorted(set(records) | set(triage_by_key))
    matrix_by_key: dict[str, dict] = {}
    identity_rows: list[dict] = []
    conflict_rows: list[dict] = []
    for key in all_keys:
        matrix, identity, conflicts = analyze_variant(key, triage_by_key.get(key, []), records.get(key, {}))
        matrix_by_key[key] = matrix
        identity_rows.append(identity)
        for conflict in conflicts:
            conflict_rows.append({"variant_key": key, **{field: matrix.get(field, "") for field in SAFE_TRIAGE_FIELDS if field in matrix}, **conflict})
    matrix_rows = [matrix_by_key[key] for key in all_keys]
    completeness = completeness_rows(records)
    selected_samples = choose_sample_categories(matrix_by_key, records)
    sample_rows = build_sample_rows(matrix_by_key, records, selected_samples)
    group_rows = group_quality_rows(triage_by_key, matrix_by_key)
    status_metrics = error_metrics(records)
    category_counts = Counter(row.get("evidence_category") for row in matrix_rows)
    progress_path = Path(payload.get("progressPath") or output_dir.parent / "enrichment_progress.json")
    progress = progress_snapshot(progress_path)
    limitations = [
        "The preserved baseline stopped before final enrichment artifacts and quality gate were written.",
        "The baseline cache does not store per-run cache hit/miss events, retry counts or request latency per variant.",
        "Secondary cache rows are keyed by resolved identity and do not prove that every VEP-only variant was eligible for every secondary source.",
        "MyVariant coordinate strings do not expose an explicit assembly in the cached payload; coordinate differences are review flags, not automatic mismatches.",
        "This analysis supports technical pre-prioritization only and does not make a clinical diagnosis.",
    ]
    summary = {
        "schema_version": "gene_module_v2",
        "analysis_version": "v2_enrichment_analysis_1",
        "created_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cache_path": str(cache_path),
        "triage_path": str(triage_path),
        "matching_path": str(matching_path) if matching_path.is_file() else "",
        "physical_variants": len(all_keys),
        "cache_variants": len(records),
        "triage_rows": len(triage_rows),
        "matching_rows_loaded": len(matching_rows),
        "gene_module_groups": len(group_rows),
        "evidence_category_counts": dict(category_counts),
        "source_status_counts": status_metrics["source_status_counts"],
        "performance": {
            "progress_snapshot": progress,
            "cache_hit_metrics_available": status_metrics["cache_hit_metrics_available"],
            "note": "Use a future runtime performance summary for live latency and throughput; this artifact is the baseline audit.",
        },
        "errors": {key: value for key, value in status_metrics.items() if key not in {"source_status_counts"}},
        "sample_categories": {category: len(keys) for category, keys in selected_samples.items()},
        "limitations": limitations,
        "outputs": {},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = output_dir / "enrichment_variant_evidence_matrix.csv"
    completeness_path = output_dir / "enrichment_source_completeness.csv"
    identity_path = output_dir / "enrichment_identity_audit.csv"
    conflicts_path = output_dir / "enrichment_cross_source_conflicts.csv"
    samples_path = output_dir / "enrichment_sample_review.jsonl"
    groups_path = output_dir / "enrichment_group_quality_summary.csv"
    summary_path = output_dir / "enrichment_analysis_summary.json"
    report_path = output_dir / "enrichment_analysis_report.md"
    write_csv(matrix_path, matrix_rows)
    write_csv(completeness_path, completeness)
    write_csv(identity_path, identity_rows)
    write_csv(conflicts_path, conflict_rows)
    write_csv(groups_path, group_rows)
    with samples_path.open("w", encoding="utf-8") as handle:
        for row in sample_rows:
            handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")
    summary["outputs"] = {
        "report": str(report_path), "summary": str(summary_path), "source_completeness": str(completeness_path),
        "variant_evidence_matrix": str(matrix_path), "identity_audit": str(identity_path),
        "cross_source_conflicts": str(conflicts_path), "sample_review": str(samples_path),
        "group_quality_summary": str(groups_path),
    }
    write_json(summary_path, summary)
    report_path.write_text(render_report(summary, completeness, matrix_rows, conflict_rows, sample_rows, group_rows), encoding="utf-8")
    return {"status": "valid", "summary": summary}


def decode_payload(value: str) -> dict:
    return json.loads(base64.b64decode(value).decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64")
    parser.add_argument("--cache")
    parser.add_argument("--triage")
    parser.add_argument("--matching")
    parser.add_argument("--output-dir")
    parser.add_argument("--progress")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = decode_payload(args.input_json_base64)
    else:
        payload = {"cachePath": args.cache, "triagePath": args.triage, "matchingPath": args.matching, "outputDir": args.output_dir, "progressPath": args.progress}
    result = process(payload)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
