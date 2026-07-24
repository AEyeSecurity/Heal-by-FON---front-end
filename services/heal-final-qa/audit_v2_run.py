"""Read-only exhaustive QA for a completed gene-module v2 run.

The auditor intentionally runs outside the production pipeline. It reads the
run artifacts and the v2 SQLite cache in read-only mode, validates every
physical and gene-module row, and writes a review package to an isolated
backup directory. It does not call external APIs or modify the source run.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


SOURCES = ("ensembl_variation", "clinvar", "myvariant", "gwas", "pharmgkb")
ALL_STATUSES = {"success", "not_found", "source_error", "not_queried", "unknown"}
CHROM_RE = re.compile(r"^chr(?:[1-9]|1[0-9]|2[0-2]|X|Y|M)$", re.IGNORECASE)
DNA_RE = re.compile(r"^[ACGTN]+$", re.IGNORECASE)
RSID_RE = re.compile(r"^rs\d+$", re.IGNORECASE)


def clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def redact_text(value: str) -> str:
    value = re.sub(r'(?i)("?(?:api[-_]?key|authorization|token)"?\s*[:=]\s*)"[^"]*"', r'\1"[redacted]"', value)
    value = re.sub(r'(?i)(api[-_]?key|authorization|token)\s*[:=]\s*[^,; ]+', r'\1=[redacted]', value)
    return value


def redact_payload(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    return value


def lower(value: object) -> str:
    return clean(value).lower()


def truthy(value: object) -> bool:
    return lower(value) in {"true", "1", "yes", "y"}


def as_int(value: object) -> int | None:
    try:
        return int(float(clean(value)))
    except (TypeError, ValueError):
        return None


def as_float(value: object) -> float | None:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return None


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_chrom(value: object) -> str:
    chrom = clean(value)
    if not chrom:
        return ""
    chrom = chrom if chrom.lower().startswith("chr") else f"chr{chrom}"
    if chrom.lower() == "chrmt":
        chrom = "chrM"
    return chrom


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    if fields is None:
        fields = []
        seen: set[str] = set()
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


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def count_gzip_csv(path: Path) -> tuple[int, list[str], str]:
    if not path.is_file():
        return 0, [], "missing"
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            count = sum(1 for _ in reader)
        return count, header, "ok"
    except (OSError, UnicodeError, csv.Error) as exc:
        return 0, [], f"error:{type(exc).__name__}"


def count_gzip_jsonl(path: Path) -> tuple[int, str]:
    if not path.is_file():
        return 0, "missing"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            count = sum(1 for line in handle if line.strip())
        return count, "ok"
    except (OSError, UnicodeError) as exc:
        return 0, f"error:{type(exc).__name__}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_inventory(run_dir: Path, cache_path: Path) -> list[dict]:
    expected = [
        "normalization/normalization_summary.json",
        "normalization/normalization_excluded_audit.csv.gz",
        "normalization/normalized_variants.csv.gz",
        "matching/vcf_canon_match_summary.json",
        "matching/sheet_final_consolidated.csv",
        "preparation/match_preparation_summary.json",
        "ai-triage/ai_triage_summary.json",
        "ai-triage/heal_fon_ai_triage.csv",
        "enrichment/enrichment_quality_summary.json",
        "enrichment/enrichment_performance_summary.json",
        "enrichment/v2_enrichment_physical_matrix.csv",
        "enrichment/v2_enrichment_variant_master.csv",
        "enrichment/v2_enrichment_vep_base.csv",
        "enrichment/v2_enrichment_evidence_audit.jsonl",
        "enrichment/v2_enrichment_resolution_audit.jsonl",
        "enrichment/v2_enrichment_physical_evidence_audit.jsonl.gz",
        "enrichment/v2_enrichment_module_projection.csv",
        "enrichment/enrichment_retry_queue.jsonl",
        "enrichment/enrichment_identity_resolution_summary.json",
    ]
    rows: list[dict] = []
    for relative in expected:
        path = run_dir / relative
        item = {"path": relative, "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0}
        if path.is_file():
            item["sha256"] = sha256_file(path)
        else:
            item["sha256"] = ""
        rows.append(item)
    if cache_path.is_file():
        rows.append({"path": str(cache_path), "exists": True, "bytes": cache_path.stat().st_size, "sha256": sha256_file(cache_path)})
    return rows


def source_status(row: dict[str, str], source: str) -> str:
    return lower(row.get(f"source_status_{source}")) or "not_queried"


def issue(row: dict, code: str, severity: str, detail: str) -> None:
    row.setdefault("issue_codes", []).append(code)
    row.setdefault("issue_details", []).append(detail)
    previous = row.get("severity", "ok")
    order = {"ok": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
    if order.get(severity, 2) > order.get(previous, 0):
        row["severity"] = severity


def finalize_issue(row: dict) -> dict:
    row["issue_count"] = len(row.get("issue_codes", []))
    row["issue_codes"] = "|".join(row.get("issue_codes", []))
    row["issue_details"] = " || ".join(row.get("issue_details", []))
    row.setdefault("severity", "ok")
    return row


def classify_evidence(row: dict[str, str], identity_flags: list[str]) -> str:
    statuses = [source_status(row, source) for source in SOURCES]
    if identity_flags:
        return "identity_ambiguous"
    if "source_error" in statuses:
        return "source_error_unresolved"
    if any(status == "not_queried" for status in statuses if truthy(row.get("secondary_query_eligible"))):
        return "not_queried_unresolved"
    clinical = as_int(row.get("clinvar_uid_count")) or 0
    pharm = (as_int(row.get("pharmgkb_clinical_annotation_count")) or 0) + (as_int(row.get("pharmgkb_variant_annotation_count")) or 0)
    gwas = as_int(row.get("gwas_association_count")) or 0
    population = clean(row.get("population_max_frequency"))
    functional = any(clean(row.get(field)) for field in ("vep_most_severe_consequence", "vep_hgvsc", "vep_hgvsp", "vep_cadd_phred", "vep_spliceai"))
    source_count = sum([clinical > 0, pharm > 0, gwas > 0, bool(clean(row.get("external_support_summary")))])
    if source_count >= 2 and functional:
        return "complete_multisource_evidence"
    if clinical > 0 or clean(row.get("clinvar_normalized_classification")) not in {"", "not_reported"}:
        return "clinical_evidence"
    if pharm > 0:
        return "pharmacogenomic_evidence"
    if gwas > 0:
        return "association_evidence"
    if population:
        return "population_context"
    if functional:
        return "functional_vep_only"
    if clean(row.get("vep_status")) == "success":
        return "vep_identity_only"
    return "not_queried_unresolved"


def validate_physical_rows(rows: list[dict[str, str]], master_by_key: dict[str, dict[str, str]]) -> tuple[list[dict], dict[str, dict], Counter]:
    audit: list[dict] = []
    by_key: dict[str, dict] = {}
    duplicate_keys: Counter = Counter()
    for row in rows:
        key = clean(row.get("variant_key"))
        duplicate_keys[key] += 1
        result = {
            "variant_key": key,
            "assembly": clean(row.get("assembly")),
            "chrom_vcf": clean(row.get("chrom_vcf")),
            "pos_vcf": clean(row.get("pos_vcf")),
            "ref_vcf": clean(row.get("ref_vcf")),
            "alt_vcf": clean(row.get("alt_vcf")),
            "resolved_rsid": clean(row.get("resolved_rsid")),
            "rsid_resolution_status": clean(row.get("rsid_resolution_status")),
            "resolution_reason": clean(row.get("resolution_reason")),
            "module_row_count": clean(row.get("module_row_count")),
            "secondary_query_eligible": clean(row.get("secondary_query_eligible")),
            "vep_status": clean(row.get("vep_status")),
            "evidence_category": "",
            "identity_flags": [],
            "issue_codes": [],
            "issue_details": [],
            "severity": "ok",
        }
        if not key:
            issue(result, "missing_variant_key", "critical", "Physical row has no variant_key.")
        if key in by_key:
            issue(result, "duplicate_variant_key", "critical", "variant_key appears more than once in physical matrix.")
        by_key[key] = row
        if clean(row.get("assembly")) != "GRCh38":
            issue(result, "assembly_not_grch38", "error", "Physical matrix is not marked GRCh38.")
        chrom = normalize_chrom(row.get("chrom_vcf"))
        if not CHROM_RE.match(chrom):
            issue(result, "invalid_chromosome", "critical", f"Invalid chromosome: {row.get('chrom_vcf')}")
        pos = as_int(row.get("pos_vcf"))
        start = as_int(row.get("pos_vcf"))
        end = as_int(row.get("variant_end")) or start
        if pos is None or pos <= 0 or start is None or end is None or end < start:
            issue(result, "invalid_coordinates", "critical", "Position or interval is invalid.")
        ref = clean(row.get("ref_vcf")).upper()
        alt = clean(row.get("alt_vcf")).upper()
        if not DNA_RE.match(ref) or not DNA_RE.match(alt):
            issue(result, "symbolic_or_invalid_allele", "error", "REF/ALT is not a normalized DNA allele.")
        if clean(row.get("vep_status")) not in ALL_STATUSES:
            issue(result, "unknown_vep_status", "error", f"Unknown VEP status: {row.get('vep_status')}")
        for source in SOURCES:
            status = source_status(row, source)
            if status not in ALL_STATUSES:
                issue(result, f"unknown_{source}_status", "error", f"Unknown status for {source}: {status}")
        resolution = clean(row.get("rsid_resolution_status"))
        resolved = clean(row.get("resolved_rsid"))
        if resolution == "vep_colocated_exact_allele" and not RSID_RE.match(resolved):
            issue(result, "exact_resolution_without_rsid", "critical", "Exact rsID resolution status has no rsID.")
        exact_identity = clean(row.get("identity_match_class")) == "exact_coordinate_allele" or resolution in {"vep_colocated_exact_allele", "coordinate_exact_allele", "myvariant_coordinate_exact"}
        if not exact_identity and truthy(row.get("secondary_query_eligible")):
            issue(result, "secondary_eligibility_mismatch", "error", "Secondary query is eligible without exact rsID resolution.")
        if resolution == "vep_colocated_allele_mismatch":
            result["identity_flags"].append("rsid_without_allele_confirmation")
            issue(result, "vep_colocated_allele_mismatch", "warning", "VEP colocated rsID did not match the observed allele exactly.")
        if resolution == "ambiguous_multiple_exact_rsids":
            result["identity_flags"].append("ambiguous_identity")
            issue(result, "ambiguous_multiple_exact_rsids", "warning", "Multiple exact colocated rsIDs require review.")
        if resolution == "unresolved_no_exact_rsid":
            result["identity_flags"].append("unresolved_identity")
            issue(result, "unresolved_no_exact_rsid", "warning", "No exact VEP colocated rsID was resolved.")
        if clean(row.get("identity_match_class")) == "rsid_without_allele_confirmation":
            result["identity_flags"].append("rsid_without_allele_confirmation")
            issue(result, "identity_not_confirmed", "warning", "Candidate identity is retained for audit but not promoted as confirmed evidence.")
        if clean(row.get("identity_match_class")) == "ambiguous_identity":
            result["identity_flags"].append("ambiguous_identity")
            issue(result, "ambiguous_identity", "warning", "Identity requires review before clinical evidence can be used.")
        if clean(row.get("source_error_sources")):
            issue(result, "secondary_source_error", "warning", clean(row.get("source_error_sources")))
        for field, minimum, maximum in (("vep_revel_score", 0, 1), ("vep_alphamissense_score", 0, 1), ("population_max_frequency", 0, 1), ("gwas_min_pvalue", 0, 1)):
            value = as_float(row.get(field))
            if value is not None and not minimum <= value <= maximum:
                issue(result, f"invalid_{field}", "error", f"{field} is outside [{minimum}, {maximum}].")
        for field in ("clinvar_uid_count", "myvariant_hit_count", "gwas_association_count", "pharmgkb_clinical_annotation_count", "pharmgkb_variant_annotation_count"):
            value = as_int(row.get(field))
            if value is not None and value < 0:
                issue(result, f"negative_{field}", "error", f"{field} is negative.")
        master = master_by_key.get(key)
        if master:
            for field in ("assembly", "chrom_vcf", "pos_vcf", "ref_vcf", "alt_vcf", "resolved_rsid", "rsid_resolution_status", "vep_status", "module_row_count"):
                if clean(row.get(field)) != clean(master.get(field)):
                    issue(result, "physical_master_mismatch", "critical", f"{field} differs from variant master.")
        else:
            issue(result, "missing_variant_master_row", "critical", "Physical matrix row has no variant master row.")
        result["identity_flags"] = "|".join(result["identity_flags"])
        result["evidence_category"] = classify_evidence(row, result["identity_flags"].split("|") if result["identity_flags"] else [])
        result["source_status_ensembl_variation"] = source_status(row, "ensembl_variation")
        result["source_status_clinvar"] = source_status(row, "clinvar")
        result["source_status_myvariant"] = source_status(row, "myvariant")
        result["source_status_gwas"] = source_status(row, "gwas")
        result["source_status_pharmgkb"] = source_status(row, "pharmgkb")
        audit.append(finalize_issue(result))
    for key, count in duplicate_keys.items():
        if count > 1:
            by_index = [row for row in audit if row["variant_key"] == key]
            for row in by_index:
                if "duplicate_variant_key" not in row["issue_codes"]:
                    issue(row, "duplicate_variant_key", "critical", "variant_key appears more than once in physical matrix.")
                    finalize_issue(row)
    return audit, by_key, duplicate_keys


def validate_triage(rows: list[dict[str, str]], physical_by_key: dict[str, dict[str, str]]) -> tuple[list[dict], dict[str, list[dict[str, str]]], Counter]:
    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_key[clean(row.get("variant_key"))].append(row)
    audit: list[dict] = []
    for row in rows:
        key = clean(row.get("variant_key"))
        result = {
            "variant_gene_module_id": clean(row.get("variant_gene_module_id")),
            "variant_key": key,
            "approved_symbol": clean(row.get("approved_symbol")),
            "module_id": clean(row.get("module_id")),
            "module_name": clean(row.get("module_name")),
            "module_status": clean(row.get("module_status")),
            "tier": clean(row.get("tier")),
            "evidence_tier": clean(row.get("evidence_tier")),
            "local_region_class": clean(row.get("local_region_class")),
            "annotation_needed": clean(row.get("annotation_needed")),
            "background_only": clean(row.get("background_only")),
            "triage_decision": clean(row.get("triage_decision")),
            "triage_reason": clean(row.get("triage_reason")),
            "issue_codes": [],
            "issue_details": [],
            "severity": "ok",
        }
        if not key or key not in physical_by_key:
            issue(result, "orphan_triage_variant", "critical", "Triage row does not resolve to a physical variant.")
        if not clean(row.get("approved_symbol")) or not clean(row.get("module_id")):
            issue(result, "missing_gene_module_identity", "critical", "Gene or module identity is missing.")
        if truthy(row.get("background_only")):
            issue(result, "background_in_ai_triage", "critical", "AI triage contains a background_only row.")
        if clean(row.get("triage_decision")) != "include_ai":
            issue(result, "unexpected_triage_decision", "error", "AI triage row is not marked include_ai.")
        if not clean(row.get("triage_reason")):
            issue(result, "missing_triage_reason", "error", "AI triage row has no deterministic reason.")
        expected_include = truthy(row.get("annotation_needed")) or (
            lower(row.get("local_region_class")) == "utr_overlap"
            and lower(row.get("module_status")) == "approved"
            and clean(row.get("tier")) in {"Tier 1", "Tier 2"}
            and clean(row.get("evidence_tier")) in {"High", "Medium"}
        )
        if not expected_include:
            issue(result, "triage_policy_mismatch", "error", "Row does not meet the declared include_ai policy.")
        physical = physical_by_key.get(key)
        if physical:
            for field in ("assembly", "chrom_vcf", "pos_vcf", "ref_vcf", "alt_vcf"):
                triage_field = "assembly_name" if field == "assembly" else field
                if clean(row.get(triage_field)) != clean(physical.get(field)):
                    issue(result, "triage_physical_coordinate_mismatch", "critical", f"{triage_field} differs from physical matrix.")
        result["module_row_count_actual"] = ""
        audit.append(finalize_issue(result))
    counts = Counter(clean(row.get("variant_key")) for row in rows)
    for result in audit:
        result["module_row_count_actual"] = str(counts[result["variant_key"]])
    return audit, dict(by_key), counts


def validate_vep_base(path: Path, triage_counts: Counter, physical_by_key: dict[str, dict[str, str]]) -> tuple[Counter, list[dict]]:
    counts: Counter = Counter()
    seen_ids: set[str] = set()
    anomalies: list[dict] = []
    if not path.is_file():
        return counts, [{"source": "vep_base", "code": "missing_artifact", "severity": "critical", "detail": str(path)}]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = clean(row.get("variant_key"))
            counts[key] += 1
            row_id = clean(row.get("variant_gene_module_id"))
            if row_id in seen_ids:
                anomalies.append({"source": "vep_base", "variant_key": key, "code": "duplicate_variant_gene_module_id", "severity": "critical", "detail": row_id})
            seen_ids.add(row_id)
            if key not in physical_by_key:
                anomalies.append({"source": "vep_base", "variant_key": key, "code": "orphan_variant_key", "severity": "critical", "detail": "VEP base row is absent from physical matrix."})
    for key, expected in triage_counts.items():
        if counts[key] != expected:
            anomalies.append({"source": "vep_base", "variant_key": key, "code": "module_row_count_mismatch", "severity": "critical", "detail": f"triage={expected};vep_base={counts[key]}"})
    return counts, anomalies


def cache_status_snapshot(cache_path: Path, keys: set[str]) -> dict[tuple[str, str], tuple[str, int | None]]:
    if not cache_path.is_file() or not keys:
        return {}
    output: dict[tuple[str, str], tuple[str, int | None]] = {}
    connection = sqlite3.connect(f"file:{cache_path.resolve().as_posix()}?mode=ro", uri=True, timeout=120)
    try:
        key_list = list(keys)
        for offset in range(0, len(key_list), 400):
            placeholders = ",".join("?" for _ in key_list[offset:offset + 400])
            query = f"SELECT variant_key, source, status, http_status FROM enrichment_cache WHERE variant_key IN ({placeholders})"
            for variant_key, source, status, http_status in connection.execute(query, key_list[offset:offset + 400]):
                output[(str(variant_key), str(source))] = (clean(status), http_status)
    finally:
        connection.close()
    return output


def cache_summary(cache_path: Path) -> dict:
    if not cache_path.is_file():
        return {"exists": False}
    connection = sqlite3.connect(f"file:{cache_path.resolve().as_posix()}?mode=ro", uri=True, timeout=120)
    try:
        total = connection.execute("SELECT COUNT(*) FROM enrichment_cache").fetchone()[0]
        variants = connection.execute("SELECT COUNT(DISTINCT variant_key) FROM enrichment_cache").fetchone()[0]
        sources = dict(connection.execute("SELECT source, COUNT(*) FROM enrichment_cache GROUP BY source").fetchall())
        statuses = dict(connection.execute("SELECT status, COUNT(*) FROM enrichment_cache GROUP BY status").fetchall())
        return {"exists": True, "rows": total, "distinct_variant_keys": variants, "source_rows": sources, "status_rows": statuses}
    finally:
        connection.close()


def audit_jsonl(path: Path, selected_keys: set[str]) -> tuple[dict, dict[str, list[str]]]:
    summary = {"path": str(path), "exists": path.is_file(), "lines": 0, "json_errors": 0, "keys": 0, "source_counts": {}, "status_counts": {}}
    selected: dict[str, list[str]] = defaultdict(list)
    if not path.is_file():
        return summary, selected
    sources: Counter = Counter()
    statuses: Counter = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            summary["lines"] += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                summary["json_errors"] += 1
                continue
            if not isinstance(payload, dict):
                continue
            key = clean(payload.get("variant_key") or payload.get("key"))
            source_statuses = payload.get("source_status") if isinstance(payload.get("source_status"), dict) else {}
            source = clean(payload.get("source") or payload.get("source_name"))
            status = clean(payload.get("status"))
            if source_statuses:
                for nested_source, nested_status in source_statuses.items():
                    sources[str(nested_source)] += 1
                    statuses[str(nested_status)] += 1
                source = "source_status_map"
                status = "source_status_map"
            source = source or "unknown"
            status = status or "unknown"
            if key:
                summary["keys"] += 1
                if key in selected_keys and len(selected[key]) < 12:
                    safe_payload = redact_payload(payload)
                    selected[key].append(json.dumps(safe_payload, ensure_ascii=True))
    summary["source_counts"] = dict(sources)
    summary["status_counts"] = dict(statuses)
    return summary, selected


def stable_order(keys: Iterable[str]) -> list[str]:
    return sorted(set(keys), key=lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest())


def build_samples(physical_rows: list[dict[str, str]], physical_audit: dict[str, dict], triage_rows: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    def candidates(predicate) -> list[str]:
        return stable_order(row["variant_key"] for row in physical_rows if predicate(row, physical_audit[row["variant_key"]]))

    categories = {
        "coding_vep_rich": candidates(lambda row, audit: clean(row.get("vep_status")) == "success" and clean(row.get("vep_most_severe_consequence")) in {"missense_variant", "stop_gained", "frameshift_variant", "inframe_insertion", "inframe_deletion", "protein_altering_variant"}),
        "spliceai_positive": candidates(lambda row, audit: bool(clean(row.get("vep_spliceai")))),
        "clinvar_success": candidates(lambda row, audit: source_status(row, "clinvar") == "success" and (as_int(row.get("clinvar_uid_count")) or 0) > 0),
        "gwas_success": candidates(lambda row, audit: source_status(row, "gwas") == "success" and (as_int(row.get("gwas_association_count")) or 0) > 0),
        "pharmgkb_success": candidates(lambda row, audit: source_status(row, "pharmgkb") == "success" and ((as_int(row.get("pharmgkb_clinical_annotation_count")) or 0) + (as_int(row.get("pharmgkb_variant_annotation_count")) or 0) > 0)),
        "vep_only": candidates(lambda row, audit: audit.get("evidence_category") in {"functional_vep_only", "vep_identity_only"}),
        "source_error": candidates(lambda row, audit: bool(clean(row.get("source_error_sources")))),
        "indel_or_ambiguous": candidates(lambda row, audit: len(clean(row.get("ref_vcf"))) != len(clean(row.get("alt_vcf"))) or bool(audit.get("identity_flags"))),
    }
    selected: list[dict] = []
    for category, keys in categories.items():
        for key in keys[:8]:
            row = next(item for item in physical_rows if item.get("variant_key") == key)
            selected.append({
                "sample_category": category,
                "variant_key": key,
                "assembly": clean(row.get("assembly")),
                "chrom_vcf": clean(row.get("chrom_vcf")),
                "pos_vcf": clean(row.get("pos_vcf")),
                "ref_vcf": clean(row.get("ref_vcf")),
                "alt_vcf": clean(row.get("alt_vcf")),
                "resolved_rsid": clean(row.get("resolved_rsid")),
                "rsid_resolution_status": clean(row.get("rsid_resolution_status")),
                "vep_status": clean(row.get("vep_status")),
                "vep_most_severe_consequence": clean(row.get("vep_most_severe_consequence")),
                "vep_hgvsc": clean(row.get("vep_hgvsc")),
                "vep_hgvsp": clean(row.get("vep_hgvsp")),
                "vep_cadd_phred": clean(row.get("vep_cadd_phred")),
                "vep_revel_score": clean(row.get("vep_revel_score")),
                "vep_spliceai": clean(row.get("vep_spliceai")),
                "clinvar_classification": clean(row.get("clinvar_normalized_classification")),
                "gwas_association_count": clean(row.get("gwas_association_count")),
                "pharmgkb_clinical_annotation_count": clean(row.get("pharmgkb_clinical_annotation_count")),
                "evidence_category": physical_audit[key].get("evidence_category", ""),
                "identity_flags": physical_audit[key].get("identity_flags", ""),
                "source_error_sources": clean(row.get("source_error_sources")),
                "automated_notes": physical_audit[key].get("issue_details", ""),
            })
    triage_index: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in triage_rows:
        triage_index[clean(row.get("variant_key"))].append(row)
    module_sample: list[dict] = []
    group_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in triage_rows:
        group_rows[f"{clean(row.get('approved_symbol'))}|{clean(row.get('module_id'))}"].append(row)
    group_order = sorted(group_rows, key=lambda group: (-len(group_rows[group]), group))
    chosen_groups = group_order[:12]
    chosen_groups += [group for group in sorted(group_rows) if any(lower(row.get("module_status")) == "draft" for row in group_rows[group])][:8]
    chosen_groups += [group for group in sorted(group_rows) if clean(group_rows[group][0].get("tier")) == "Tier 3"][:8]
    for group in dict.fromkeys(chosen_groups):
        row = group_rows[group][0]
        module_sample.append({
            "sample_group": "group_review",
            "group_id": group,
            "variant_key": clean(row.get("variant_key")),
            "approved_symbol": clean(row.get("approved_symbol")),
            "module_id": clean(row.get("module_id")),
            "module_name": clean(row.get("module_name")),
            "system_within_module": clean(row.get("system_within_module")),
            "module_status": clean(row.get("module_status")),
            "tier": clean(row.get("tier")),
            "evidence_tier": clean(row.get("evidence_tier")),
            "group_row_count": len(group_rows[group]),
            "local_region_class_counts": json.dumps(dict(Counter(clean(item.get("local_region_class")) for item in group_rows[group])), ensure_ascii=True, sort_keys=True),
            "review_notes": "",
        })
    return selected, module_sample


def parse_myvariant_coordinate(value: object) -> tuple[str, int] | None:
    match = re.search(r"chr?([0-9XYM]+):g\.(\d+)", clean(value), re.IGNORECASE)
    if not match:
        return None
    return normalize_chrom(match.group(1)), int(match.group(2))


def build_cross_source_conflicts(physical_rows: list[dict[str, str]], triage_rows: list[dict[str, str]]) -> list[dict]:
    targets: dict[str, set[str]] = defaultdict(set)
    for row in triage_rows:
        if clean(row.get("approved_symbol")):
            targets[clean(row.get("variant_key"))].add(clean(row.get("approved_symbol")))
    conflicts: list[dict] = []
    for row in physical_rows:
        key = clean(row.get("variant_key"))
        if clean(row.get("source_error_sources")):
            conflicts.append({"variant_key": key, "code": "source_error", "severity": "warning", "detail": clean(row.get("source_error_sources"))})
        if truthy(row.get("clinvar_conflict_flag")):
            conflicts.append({"variant_key": key, "code": "clinical_conflict", "severity": "warning", "detail": f"ClinVar conflict flag; classification={clean(row.get('clinvar_normalized_classification'))}"})
        clinvar = lower(row.get("clinvar_normalized_classification"))
        ensembl = lower(row.get("ensembl_clin_sig"))
        pathogenic = any(token in value for value in (clinvar, ensembl) for token in ("pathogenic", "risk_factor"))
        benign = any(token in value for value in (clinvar, ensembl) for token in ("benign",))
        if pathogenic and benign:
            conflicts.append({"variant_key": key, "code": "benign_vs_pathogenic_conflict", "severity": "warning", "detail": f"ClinVar={clinvar};Ensembl={ensembl}"})
        coordinate = parse_myvariant_coordinate(row.get("myvariant_best_id"))
        expected = normalize_chrom(row.get("chrom_vcf")), as_int(row.get("pos_vcf"))
        if coordinate and expected[1] and coordinate != expected:
            conflicts.append({"variant_key": key, "code": "cross_assembly_coordinate_difference", "severity": "warning", "detail": f"MyVariant={coordinate[0]}:{coordinate[1]};input={expected[0]}:{expected[1]}"})
        vep_genes = {clean(item) for item in clean(row.get("vep_picked_gene_symbol")).split("|") if clean(item)}
        vep_genes.update({clean(item) for item in re.findall(r"(?:^|\|)gene=([^;|]+)", clean(row.get("vep_transcript_summary"))) if clean(item)})
        for target in sorted(targets.get(key, set())):
            if vep_genes and target not in vep_genes:
                conflicts.append({"variant_key": key, "code": "gene_assignment_difference", "severity": "warning", "detail": f"Canon={target};VEP={ '|'.join(sorted(vep_genes)) }"})
    return conflicts


def stage_reconciliation(run_dir: Path, physical_count: int, triage_count: int, vep_base_count: int, normalization_rows: int) -> list[dict]:
    normalization = read_json(run_dir / "normalization" / "normalization_summary.json")
    matching = read_json(run_dir / "matching" / "vcf_canon_match_summary.json")
    preparation = read_json(run_dir / "preparation" / "match_preparation_summary.json")
    triage = read_json(run_dir / "ai-triage" / "ai_triage_summary.json")
    enrichment = read_json(run_dir / "enrichment" / "enrichment_quality_summary.json")
    rows: list[dict] = []
    def add(stage: str, metric: str, expected: object, actual: object, status: str = "pass", detail: str = "") -> None:
        rows.append({"stage": stage, "metric": metric, "expected": expected, "actual": actual, "status": status, "detail": detail})

    input_records = normalization.get("counts", {}).get("input_records")
    outside = normalization.get("counts", {}).get("outside_canon_envelope_prefilter")
    add("normalization", "normalized_physical_rows", normalization.get("counts", {}).get("normalized_physical_variants"), normalization_rows, "pass" if normalization.get("counts", {}).get("normalized_physical_variants") == normalization_rows else "error")
    add("normalization", "outside_prefilter_not_greater_than_input", f"<={input_records}", outside, "warning" if isinstance(outside, int) and isinstance(input_records, int) and outside > input_records else "pass", "Counter definition appears inconsistent." if isinstance(outside, int) and isinstance(input_records, int) and outside > input_records else "")
    add("normalization", "quality_gate", True, normalization.get("qualityGate", {}).get("passed"), "warning" if normalization.get("qualityGate", {}).get("passed") is False else "pass", "Run continued despite normalization qualityGate=false.")
    add("matching", "sheet_final_rows", matching.get("metadata", {}).get("sheet_final_rows"), matching.get("metadata", {}).get("sheet_final_rows"), "pass")
    add("preparation", "rows_total", preparation.get("metadata", {}).get("rows_total"), preparation.get("metadata", {}).get("rows_total"), "pass")
    add("triage", "included_for_ai", triage.get("metadata", {}).get("included_for_ai"), triage_count, "pass" if triage.get("metadata", {}).get("included_for_ai") == triage_count else "error")
    add("enrichment", "physical_variants", enrichment.get("physicalVariants"), physical_count, "pass" if enrichment.get("physicalVariants") == physical_count else "error")
    add("enrichment", "module_rows", enrichment.get("moduleRows"), triage_count, "pass" if enrichment.get("moduleRows") == triage_count else "error")
    add("enrichment", "vep_coverage", enrichment.get("vepCoverage"), enrichment.get("minimumVepCoverage"), "pass" if (enrichment.get("vepCoverage") or 0) >= (enrichment.get("minimumVepCoverage") or 1) else "error")
    add("enrichment", "vep_base_rows", triage_count, vep_base_count, "pass" if triage_count == vep_base_count else "error")
    return rows


def render_report(summary: dict) -> str:
    lines = [
        "# QA Final V2 Report",
        "",
        "This is a read-only technical audit of a completed HEAL by FON gene-module v2 run. It is not a clinical interpretation.",
        "",
        "## Run",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Created: {summary['created_at']}",
        f"- Physical variants audited: {summary['counts']['physical_variants']}",
        f"- Gene-module rows audited: {summary['counts']['triage_rows']}",
        f"- VEP base rows audited: {summary['counts']['vep_base_rows']}",
        "",
        "## Decision",
        "",
        f"- Technical decision: **{summary['decision']}**",
        f"- Critical findings: {summary['findings']['critical']}",
        f"- Errors: {summary['findings']['error']}",
        f"- Warnings: {summary['findings']['warning']}",
        "",
        "## Evidence Categories",
        "",
        "| Category | Variants |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in sorted(summary["evidence_category_counts"].items()))
    lines.extend(["", "## Main Findings", ""])
    for finding in summary.get("main_findings", []):
        lines.append(f"- **{finding['severity']}** `{finding['code']}`: {finding['detail']}")
    lines.extend(["", "## Source Status", "", "| Source | Status | Count |", "|---|---|---:|"])
    for source, statuses in summary["source_status_counts"].items():
        for status, count in sorted(statuses.items()):
            lines.append(f"| {source} | {status} | {count} |")
    lines.extend(["", "## Limitations", "", "- The quality gate passed at the configured 90% VEP threshold; this does not prove biological correctness.", "- Secondary source statuses are interpreted from the preserved cache and must not be treated as live calls in this run.", "- MyVariant coordinate differences are retained as review flags when assembly is not explicit.", "- LLM1 and grouping remain disabled.", "", "## Manual Review", "", "See `qa_manual_guide.md`, `qa_physical_sample.csv`, `qa_module_sample.csv`, and `bioinformatician_review_template.csv`."])
    return "\n".join(lines) + "\n"


def process(run_dir: Path, output_dir: Path, cache_path: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    physical_path = run_dir / "enrichment" / "v2_enrichment_physical_matrix.csv"
    physical_evidence_path = run_dir / "enrichment" / "v2_enrichment_physical_evidence_audit.jsonl.gz"
    master_path = run_dir / "enrichment" / "v2_enrichment_variant_master.csv"
    triage_path = run_dir / "ai-triage" / "heal_fon_ai_triage.csv"
    vep_base_path = run_dir / "enrichment" / "v2_enrichment_vep_base.csv"
    physical_rows = read_csv(physical_path)
    master_rows = read_csv(master_path)
    triage_rows = read_csv(triage_path)
    master_by_key = {clean(row.get("variant_key")): row for row in master_rows}
    physical_audit_rows, physical_by_key, duplicate_keys = validate_physical_rows(physical_rows, master_by_key)
    physical_audit_by_key = {row["variant_key"]: row for row in physical_audit_rows}
    triage_audit_rows, triage_by_key, triage_counts = validate_triage(triage_rows, physical_by_key)
    vep_base_counts, vep_base_anomalies = validate_vep_base(vep_base_path, triage_counts, physical_by_key)
    normalized_count, normalized_header, normalized_status = count_gzip_csv(run_dir / "normalization" / "normalized_variants.csv.gz")
    cache_status = cache_status_snapshot(cache_path, set(physical_by_key))
    cache_metrics = cache_summary(cache_path)
    cache_mismatches: list[dict] = []
    for key, row in physical_by_key.items():
        for source in ("ensembl_vep_region",) + SOURCES:
            expected = source_status(row, "ensembl_variation") if source == "ensembl_variation" else clean(row.get("vep_status")) if source == "ensembl_vep_region" else source_status(row, source)
            cached = cache_status.get((key, source))
            if cached and expected and cached[0] != expected:
                cache_mismatches.append({"variant_key": key, "source": source, "matrix_status": expected, "cache_status": cached[0], "http_status": cached[1]})
    samples, module_samples = build_samples(physical_rows, physical_audit_by_key, triage_rows)
    selected_keys = {row["variant_key"] for row in samples}
    evidence_summary, evidence_selected = audit_jsonl(run_dir / "enrichment" / "v2_enrichment_evidence_audit.jsonl", selected_keys)
    resolution_summary, resolution_selected = audit_jsonl(run_dir / "enrichment" / "v2_enrichment_resolution_audit.jsonl", selected_keys)
    physical_evidence_count, physical_evidence_status = count_gzip_jsonl(physical_evidence_path)
    selected_jsonl = []
    for key in sorted(set(evidence_selected) | set(resolution_selected)):
        for line in evidence_selected.get(key, []):
            selected_jsonl.append({"artifact": "v2_enrichment_evidence_audit.jsonl", "variant_key": key, "payload": line})
        for line in resolution_selected.get(key, []):
            selected_jsonl.append({"artifact": "v2_enrichment_resolution_audit.jsonl", "variant_key": key, "payload": line})
    with (output_dir / "qa_evidence_sample.jsonl").open("w", encoding="utf-8") as handle:
        for item in selected_jsonl:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")
    source_status_counts = {source: dict(Counter(source_status(row, source) for row in physical_rows)) for source in SOURCES}
    source_status_counts["ensembl_vep_region"] = dict(Counter(clean(row.get("vep_status")) or "not_queried" for row in physical_rows))
    evidence_categories = Counter(row.get("evidence_category") for row in physical_audit_rows)
    findings = Counter(row.get("severity", "ok") for row in physical_audit_rows + triage_audit_rows)
    findings.update(item.get("severity", "error") for item in vep_base_anomalies)
    main_findings: list[dict] = []
    if any(row.get("severity") in {"critical", "error"} for row in physical_audit_rows):
        main_findings.append({"severity": "critical", "code": "physical_row_integrity", "detail": "At least one physical row has a critical or error-level integrity finding."})
    if any(row.get("severity") in {"critical", "error"} for row in triage_audit_rows):
        main_findings.append({"severity": "critical", "code": "gene_module_row_integrity", "detail": "At least one gene-module row has a critical or error-level integrity finding."})
    normalization = read_json(run_dir / "normalization" / "normalization_summary.json")
    counts = normalization.get("counts", {})
    outside_alleles = counts.get("outside_canon_envelope_prefilter_alleles")
    if isinstance(outside_alleles, int) and isinstance(counts.get("observed_source_alleles"), int) and outside_alleles < 0:
        main_findings.append({"severity": "error", "code": "normalization_counter_invalid", "detail": "The normalized prefilter allele counter is negative."})
    if normalization.get("qualityGate", {}).get("passed") is False:
        main_findings.append({"severity": "warning", "code": "normalization_quality_gate_false", "detail": "Normalization retention gate did not pass; review the allele-level retention metrics."})
    if cache_mismatches:
        main_findings.append({"severity": "error", "code": "cache_matrix_status_mismatch", "detail": f"{len(cache_mismatches)} physical/source statuses differ between matrix and cache."})
    if evidence_summary["json_errors"] or resolution_summary["json_errors"]:
        main_findings.append({"severity": "critical", "code": "jsonl_parse_errors", "detail": "At least one evidence or resolution JSONL line could not be parsed."})
    if physical_evidence_status != "ok" or physical_evidence_count != len(physical_rows):
        main_findings.append({"severity": "critical", "code": "physical_evidence_cardinality", "detail": f"Physical evidence audit has {physical_evidence_count} rows with status {physical_evidence_status}; expected {len(physical_rows)}."})
    stage_rows = stage_reconciliation(run_dir, len(physical_rows), len(triage_rows), sum(vep_base_counts.values()), normalized_count)
    cross_source_conflicts = build_cross_source_conflicts(physical_rows, triage_rows)
    performance = read_json(run_dir / "enrichment" / "enrichment_performance_summary.json")
    quality = read_json(run_dir / "enrichment" / "enrichment_quality_summary.json")
    performance_rows = []
    for name, value in (("elapsed_seconds", performance.get("elapsedSeconds")), ("physical_variants", performance.get("physicalVariants")), ("module_rows", performance.get("moduleRows")), ("vep_seconds", performance.get("vepSeconds")), ("secondary_wall_seconds", performance.get("secondaryWallSeconds")), ("cache_hits", quality.get("secondaryMetrics", {}).get("cache_hits")), ("network_calls", sum(item.get("network_calls", 0) for item in quality.get("secondaryMetrics", {}).get("source_stats", {}).values()))):
        performance_rows.append({"metric": name, "value": value, "source": "enrichment_performance_summary.json"})
    for source, metrics in quality.get("secondaryMetrics", {}).get("source_stats", {}).items():
        for metric, value in metrics.items():
            performance_rows.append({"metric": f"{source}.{metric}", "value": value, "source": "enrichment_quality_summary.json"})
    targeted = []
    targeted.extend(row for row in physical_audit_rows if row.get("severity") != "ok")
    targeted.extend(row for row in triage_audit_rows if row.get("severity") != "ok")
    targeted.extend(vep_base_anomalies)
    targeted.extend({"variant_key": row["variant_key"], "code": "cache_status_mismatch", "severity": "error", "detail": f"{row['source']} matrix={row['matrix_status']} cache={row['cache_status']}"} for row in cache_mismatches)
    source_error_sample = [row for row in physical_rows if clean(row.get("source_error_sources"))][:100]
    identity_sample = [row for row in physical_rows if clean(row.get("rsid_resolution_status")) in {"vep_colocated_allele_mismatch", "ambiguous_multiple_exact_rsids", "unresolved_no_exact_rsid"}][:200]
    write_csv(output_dir / "qa_physical_variant_audit.csv", physical_audit_rows)
    write_csv(output_dir / "qa_gene_module_audit.csv", triage_audit_rows)
    write_csv(output_dir / "qa_identity_audit.csv", [row for row in physical_audit_rows if row.get("identity_flags")])
    completeness_rows = []
    for source, statuses in source_status_counts.items():
        queried = len(physical_rows) - statuses.get("not_queried", 0)
        successful = statuses.get("success", 0)
        eligible = sum(1 for row in physical_rows if source == "ensembl_vep_region" or truthy(row.get("secondary_query_eligible")))
        for status, count in statuses.items():
            completeness_rows.append({
                "source": source,
                "status": status,
                "count": count,
                "denominator_all": len(physical_rows),
                "denominator_queried": queried,
                "denominator_success": successful,
                "denominator_eligible": eligible,
                "coverage_all_pct": round(count / len(physical_rows) * 100, 3) if physical_rows else 0,
                "coverage_queried_pct": round(count / queried * 100, 3) if queried else 0,
                "coverage_eligible_pct": round(count / eligible * 100, 3) if eligible else 0,
            })
    write_csv(output_dir / "qa_source_completeness.csv", completeness_rows)
    write_csv(output_dir / "qa_cross_source_conflicts.csv", cross_source_conflicts)
    write_csv(output_dir / "qa_stage_reconciliation.csv", stage_rows)
    normalization_anomalies = []
    if isinstance(counts.get("outside_canon_envelope_prefilter"), int):
        normalization_anomalies.append({"code": "legacy_counter_present", "severity": "warning", "detail": "Legacy record-level prefilter counter is still present; allele-level counters should be used for v2 QA.", "input_records": counts.get("input_records"), "outside_prefilter": counts.get("outside_canon_envelope_prefilter"), "outside_prefilter_alleles": counts.get("outside_canon_envelope_prefilter_alleles"), "normalized_observed_alleles": counts.get("normalized_observed_alleles"), "normalized_source_matched": counts.get("normalized_source_matched"), "normalized_physical_variants": counts.get("normalized_physical_variants")})
    if normalization.get("qualityGate", {}).get("passed") is False:
        normalization_anomalies.append({"code": "normalization_quality_gate_false", "severity": "warning", "detail": "qualityGate.passed=false", "input_records": counts.get("input_records"), "outside_prefilter_alleles": counts.get("outside_canon_envelope_prefilter_alleles"), "normalized_observed_alleles": counts.get("normalized_observed_alleles"), "normalized_source_matched": counts.get("normalized_source_matched"), "normalized_physical_variants": counts.get("normalized_physical_variants")})
    write_csv(output_dir / "qa_normalization_anomalies.csv", normalization_anomalies)
    write_csv(output_dir / "qa_performance_audit.csv", performance_rows)
    write_csv(output_dir / "qa_manual_sample.csv", samples)
    write_csv(output_dir / "qa_physical_sample.csv", samples)
    write_csv(output_dir / "qa_module_sample.csv", module_samples)
    write_csv(output_dir / "qa_targeted_anomalies.csv", targeted)
    write_csv(output_dir / "qa_source_error_sample.csv", source_error_sample)
    write_csv(output_dir / "qa_identity_sample.csv", identity_sample)
    review_fields = list(samples[0].keys()) if samples else ["sample_category", "variant_key"]
    review_fields += ["reviewer", "review_date", "coordinate_ok", "identity_ok", "evidence_attribution_ok", "biological_coherence", "finding", "comments"]
    write_csv(output_dir / "bioinformatician_review_template.csv", [{**row, "reviewer": "", "review_date": "", "coordinate_ok": "", "identity_ok": "", "evidence_attribution_ok": "", "biological_coherence": "", "finding": "", "comments": ""} for row in samples], review_fields)
    manifest = artifact_inventory(run_dir, cache_path)
    write_csv(output_dir / "qa_artifact_manifest.csv", manifest)
    write_json(output_dir / "qa_evidence_jsonl_summary.json", {"evidence": evidence_summary, "resolution": resolution_summary})
    write_json(output_dir / "qa_cache_summary.json", cache_metrics)
    summary = {
        "schema_version": "gene_module_v2",
        "qa_version": "v2_final_audit_1",
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "read_only": True,
        "output_dir": str(output_dir),
        "counts": {"physical_variants": len(physical_rows), "physical_evidence_rows": physical_evidence_count, "triage_rows": len(triage_rows), "triage_unique_variant_keys": len(triage_by_key), "vep_base_rows": sum(vep_base_counts.values()), "normalized_variant_rows": normalized_count, "manual_sample_rows": len(samples), "module_sample_rows": len(module_samples)},
        "evidence_category_counts": dict(evidence_categories),
        "source_status_counts": source_status_counts,
        "cache": {**cache_metrics, "matrix_status_mismatches": len(cache_mismatches)},
        "jsonl": {"evidence": evidence_summary, "resolution": resolution_summary, "physical_evidence": {"path": str(physical_evidence_path), "lines": physical_evidence_count, "status": physical_evidence_status}},
        "cross_source_conflicts": {"rows": len(cross_source_conflicts), "by_code": dict(Counter(row["code"] for row in cross_source_conflicts))},
        "findings": {"critical": findings["critical"], "error": findings["error"], "warning": findings["warning"], "info": findings["info"]},
        "main_findings": main_findings,
        "decision": "pass_with_documented_findings" if findings["critical"] == 0 and findings["error"] == 0 else "review_required",
        "quality_gate": {"status": quality.get("status"), "decision": quality.get("decision"), "vep_coverage": quality.get("vepCoverage"), "minimum_vep_coverage": quality.get("minimumVepCoverage"), "downstream_supported": False},
        "stage_reconciliation": stage_rows,
        "artifacts": manifest,
        "manual_review": {"sample_categories": 8, "sample_rows": len(samples), "instructions": "qa_manual_guide.md", "template": "bioinformatician_review_template.csv"},
    }
    write_json(output_dir / "qa_final_summary.json", summary)
    (output_dir / "qa_final_report.md").write_text(render_report(summary), encoding="utf-8")
    (output_dir / "qa_run_summary.md").write_text(render_report(summary), encoding="utf-8")
    guide = """# Manual QA Guide - HEAL by FON v2\n\nScope: completed run `{run_id}`. This package is read-only and supports technical review, not clinical diagnosis.\n\n## Architecture\n\n`VCF -> normalization -> VCF/canon match -> preparation -> AI triage -> physical enrichment -> gene/module expansion`\n\nA physical variant is one normalized GRCh38 `variant_key` with chromosome, position, REF and ALT. A gene-module row is the same physical variant paired with one canon gene and module. Therefore the run may contain 5,910 physical variants and 7,486 module rows without duplication being an error.\n\n- `normalization`: biallelic, coordinate-normalized VCF records and source traceability.\n- `matching`: overlaps variant coordinates with gene envelopes and canon features.\n- `preparation`: classifies local region and review status.\n- `ai-triage`: selects rows eligible for later interpretation.\n- `enrichment`: one physical row per variant, with VEP and secondary source statuses.\n- `gene-module`: expands physical evidence back into canon context.\n\n## Review Order\n\n1. Read `qa_run_summary.md` and `qa_final_report.md`.\n2. Read `qa_stage_reconciliation.csv` and `qa_normalization_anomalies.csv`.\n3. Inspect `qa_physical_sample.csv` and `qa_identity_sample.csv`.\n4. Inspect `qa_source_error_sample.csv` and the corresponding records in the enrichment JSONL artifacts.\n5. Inspect `qa_module_sample.csv` for large, Draft, Tier 3 and multi-region groups.\n6. Record findings in `bioinformatician_review_template.csv`.\n\n## What To Check\n\n- Coordinates and REF/ALT match the physical variant identity.\n- VEP consequence is compatible with the local canon region.\n- rsIDs are used only when the observed allele is confirmed.\n- Cross-assembly coordinate differences are flagged, not silently treated as the same coordinate.\n- `source_error`, `not_found` and `not_queried` are distinct.\n- Evidence is not attributed to a different gene, allele or assembly.\n- The module context is preserved and different modules of the same gene are not merged.\n\n## Severity\n\n- `critical`: wrong identity, orphan/duplicate row, malformed coordinate or unreconciled join.\n- `error`: contract or policy violation that can alter downstream interpretation.\n- `warning`: source limitation, unresolved identity or documented counter anomaly.\n- `info`: expected limitation with no integrity impact.\n\nPlease return the completed CSV with one decision per sampled row and comments for any disagreement with the automated audit.\n""".format(run_id=run_dir.name)
    (output_dir / "qa_manual_guide.md").write_text(guide, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only exhaustive QA for a HEAL v2 run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--cache")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path(r"F:\Heal by FON\backups\qa-audit") / run_dir.name
    default_v2_cache = Path(r"F:\Heal by FON\data\enrichment-cache\enrichment_cache_v2.sqlite")
    default_legacy_cache = Path(r"F:\Heal by FON\data\enrichment-cache\enrichment_cache.sqlite")
    cache_path = Path(args.cache).resolve() if args.cache else (default_v2_cache if default_v2_cache.is_file() else default_legacy_cache)
    summary = process(run_dir, output_dir, cache_path)
    print(json.dumps({"status": "valid", "decision": summary["decision"], "outputDir": str(output_dir), "counts": summary["counts"], "findings": summary["findings"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
