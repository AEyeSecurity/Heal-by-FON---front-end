#!/usr/bin/env python3
"""Coordinate-first enrichment for normalized HEAL gene-module v2 variants.

V2 intentionally does not assume that a VCF ID is an rsID. Ensembl VEP is
queried with the normalized GRCh coordinate, REF and ALT first; rsIDs are only
used for secondary sources after an exact colocated-allele confirmation.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import enrich_observed_variants as legacy


PIPELINE_VERSION = "gene-module-v2-enrichment-1"
VEP_URL = "https://rest.ensembl.org/vep/human/region"
VEP_INFO_URL = "https://rest.ensembl.org/info/data"
VEP_BATCH_SIZE = 200
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CACHE_TTL_DAYS = 14
DEFAULT_SECONDARY_WORKERS = 4
SOURCE_WORKERS = {
    "ensembl_variation": 4,
    "clinvar": 2,
    "myvariant": 4,
    "gwas": 2,
    "pharmgkb": 1,
}
SECONDARY_SOURCE_ORDER = (
    "ensembl_variation",
    "clinvar",
    "myvariant",
    "gwas",
    "pharmgkb",
)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: object) -> str:
    return legacy.clean_str(value)


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_progress(
    output_dir: Path,
    *,
    substage: str,
    processed: int = 0,
    total: int = 0,
    unit: str = "variants",
    message: str = "",
    stage: str = "enriching",
    phase: str = "",
    metrics: dict | None = None,
) -> None:
    write_json(
        output_dir / "enrichment_progress.json",
        {
            "stage": stage,
            "phase": phase or stage,
            "substage": substage,
            "processed": max(0, int(processed)),
            "total": max(0, int(total)),
            "unit": unit,
            "message": message,
            "metrics": metrics or {},
            "updatedAt": utc_now(),
        },
    )


def normalize_chromosome(value: object) -> str:
    chrom = clean(value).upper()
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "MT":
        chrom = "M"
    return f"chr{chrom}" if chrom else ""


def stable_variant_key(assembly: str, chrom: str, pos: str, ref: str, alt: str) -> str:
    value = "|".join([assembly, chrom, pos, ref.upper(), alt.upper()])
    return f"v2_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def normalize_rsid(value: object) -> str:
    return legacy.normalize_rsid(value)


def source_fields(rows: list[dict]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    return fields


class EnrichmentCache:
    def __init__(self, path: Path, ttl_days: int):
        self.path = path
        self.ttl = dt.timedelta(days=ttl_days)
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        with self._lock:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    assembly TEXT NOT NULL,
                    variant_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    PRIMARY KEY (assembly, variant_key, source)
                )
                """
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.commit()
        self._connection = connection

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=45, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=45000")
        return connection

    def thread_connection(self) -> sqlite3.Connection:
        return self._connection

    def close_thread_connection(self) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None

    def get(self, assembly: str, variant_key: str, source: str, fingerprint: str, connection: sqlite3.Connection | None = None) -> dict | None:
        connection = connection or self.thread_connection()
        with self._lock:
            row = connection.execute(
                """SELECT response_json, status, http_status, fetched_at, expires_at
                   FROM enrichment_cache
                   WHERE assembly = ? AND variant_key = ? AND source = ? AND request_fingerprint = ?""",
                (assembly, variant_key, source, fingerprint),
            ).fetchone()
        if not row:
            return None
        try:
            expires_at = dt.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires_at <= dt.datetime.now(dt.UTC):
                return None
            payload = json.loads(row["response_json"])
        except (ValueError, json.JSONDecodeError):
            return None
        return {"payload": payload, "status": row["status"], "http_status": row["http_status"], "fetched_at": row["fetched_at"]}

    def put(self, assembly: str, variant_key: str, source: str, fingerprint: str, payload: object, status: str, http_status: int | None, connection: sqlite3.Connection | None = None) -> None:
        fetched_at = dt.datetime.now(dt.UTC)
        values = (
            assembly,
            variant_key,
            source,
            fingerprint,
            json.dumps(payload, ensure_ascii=True),
            status,
            http_status,
            fetched_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            (fetched_at + self.ttl).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            PIPELINE_VERSION,
        )
        connection = connection or self.thread_connection()
        for attempt in range(4):
            try:
                with self._lock:
                    connection.execute(
                        """INSERT INTO enrichment_cache
                           (assembly, variant_key, source, request_fingerprint, response_json, status, http_status, fetched_at, expires_at, pipeline_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(assembly, variant_key, source) DO UPDATE SET
                             request_fingerprint=excluded.request_fingerprint,
                             response_json=excluded.response_json,
                             status=excluded.status,
                             http_status=excluded.http_status,
                             fetched_at=excluded.fetched_at,
                             expires_at=excluded.expires_at,
                             pipeline_version=excluded.pipeline_version""",
                        values,
                    )
                    connection.commit()
                    return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 3:
                    raise
                time.sleep(0.25 * (attempt + 1))


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()


def post_json(url: str, payload: dict, timeout_seconds: int) -> tuple[object | None, str, int | None, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": legacy.USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body), "", response.status, dict(response.headers.items())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        return None, f"http_{error.code}: {detail}", error.code, dict(error.headers.items()) if error.headers else {}
    except Exception as error:  # External services must not terminate the audited run.
        return None, str(error), None, {}


def endpoint_with_params(url: str, params: dict[str, str]) -> str:
    return f"{url}?{urllib.parse.urlencode(params)}"


VEP_PARAMS = {
    "content-type": "application/json",
    "Phenotypes": "1",
    "CADD": "1",
    "AlphaMissense": "1",
    "REVEL": "1",
    "SpliceAI": "2",
    "canonical": "1",
    "domains": "1",
    "hgvs": "1",
    "mane": "1",
    "numbers": "1",
    "protein": "1",
    "uniprot": "1",
    "variant_class": "1",
    "minimal": "1",
    "ga4gh_vrs": "1",
}


def vep_region_line(variant: dict) -> str:
    # Ensembl region input is VCF-style: chromosome position id ref alt quality filter info.
    return " ".join(
        [
            clean(variant["chrom_vcf"]).removeprefix("chr"), clean(variant["pos_vcf"]), clean(variant["variant_key"]),
            clean(variant["ref_vcf"]), clean(variant["alt_vcf"]), ".", ".", ".",
        ]
    )


def fetch_vep_batch(batch: list[dict], assembly: str, cache: EnrichmentCache, timeout_seconds: int, provenance: dict) -> tuple[dict[str, dict], int, int, list[str]]:
    """Fetch non-cached region annotations; returns raw items keyed by variant_key."""
    resolved: dict[str, dict] = {}
    misses: list[dict] = []
    cache_hits = 0
    for variant in batch:
        request_payload = {"variant": vep_region_line(variant), "params": VEP_PARAMS}
        cached = cache.get(assembly, variant["variant_key"], "ensembl_vep_region", fingerprint(request_payload))
        if cached:
            resolved[variant["variant_key"]] = {"item": cached["payload"], "cache_hit": True, "status": cached["status"], "error": ""}
            cache_hits += 1
        else:
            misses.append(variant)
    if not misses:
        return resolved, cache_hits, 0, []

    query_url = endpoint_with_params(VEP_URL, VEP_PARAMS)
    payload = {"variants": [vep_region_line(variant) for variant in misses]}
    response: object | None = None
    error = ""
    http_status: int | None = None
    headers: dict = {}
    for attempt in range(3):
        response, error, http_status, headers = post_json(query_url, payload, timeout_seconds)
        if not error or (http_status is not None and http_status < 500 and http_status != 429):
            break
        time.sleep(1.2 * (attempt + 1))
    provenance.setdefault("ensembl_vep_region", {"url": query_url, "requestParameters": VEP_PARAMS, "responses": []})
    provenance["ensembl_vep_region"]["responses"].append({"at": utc_now(), "httpStatus": http_status, "headers": headers})
    items_by_id: dict[str, dict] = {}
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict) and clean(item.get("input")):
                items_by_id[clean(item.get("input")).split()[2] if len(clean(item.get("input")).split()) > 2 else ""] = item
    errors: list[str] = []
    if error:
        errors.append(f"ensembl_vep_region: {error}")
    for variant in misses:
        item = items_by_id.get(variant["variant_key"], {})
        status = "success" if item else "source_error" if error else "not_found"
        cache.put(assembly, variant["variant_key"], "ensembl_vep_region", fingerprint({"variant": vep_region_line(variant), "params": VEP_PARAMS}), item, status, http_status)
        resolved[variant["variant_key"]] = {"item": item, "cache_hit": False, "status": status, "error": error if not item else ""}
    return resolved, cache_hits, len(misses), errors


def as_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return legacy.compact_json(value)
    return clean(value)


def transcript_rank(entry: dict) -> tuple:
    return (
        0 if clean(entry.get("mane_select")) else 1,
        0 if clean(entry.get("mane_plus_clinical")) else 1,
        0 if clean(entry.get("canonical")) == "1" else 1,
        0 if clean(entry.get("biotype")) == "protein_coding" else 1,
        clean(entry.get("transcript_id")),
    )


def transcript_summary(entry: dict) -> str:
    values = [
        ("gene", entry.get("gene_symbol")), ("tx", entry.get("transcript_id")),
        ("consequence", legacy.unique_join(entry.get("consequence_terms") or [], limit=6)), ("impact", entry.get("impact")),
        ("biotype", entry.get("biotype")), ("hgvsc", entry.get("hgvsc")), ("hgvsp", entry.get("hgvsp")),
        ("cadd_phred", entry.get("cadd_phred")), ("revel", entry.get("revel_score") or entry.get("revel")),
        ("spliceai", as_text(entry.get("spliceai"))),
    ]
    return "; ".join(f"{key}={clean(value)}" for key, value in values if clean(value))


def exact_allele_match(allele_string: object, ref: str, alt: str) -> bool:
    alleles = [clean(value).upper() for value in re.split(r"[|/,]", clean(allele_string)) if clean(value)]
    return clean(ref).upper() in alleles and clean(alt).upper() in alleles


def intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return min(end_a, end_b) >= max(start_a, start_b)


def normalized_allele_signature(ref: object, alt: object) -> tuple[str, str]:
    ref_text = clean(ref).upper()
    alt_text = clean(alt).upper()
    while len(ref_text) > 1 and len(alt_text) > 1 and ref_text[0] == alt_text[0]:
        ref_text = ref_text[1:]
        alt_text = alt_text[1:]
    while len(ref_text) > 1 and len(alt_text) > 1 and ref_text[-1] == alt_text[-1]:
        ref_text = ref_text[:-1]
        alt_text = alt_text[:-1]
    return ref_text, alt_text


def vep_entry_matches_variant(variant: dict, entry: dict) -> tuple[bool, str]:
    if not isinstance(entry, dict):
        return False, ""
    entry_chrom = normalize_chromosome(entry.get("seq_region_name"))
    variant_chrom = normalize_chromosome(variant.get("chrom_vcf"))
    if entry_chrom and variant_chrom and entry_chrom != variant_chrom:
        return False, ""
    try:
        variant_start = int(variant.get("pos_vcf"))
        variant_end = variant_start + max(len(clean(variant.get("ref_vcf"))), 1) - 1
        entry_start = int(entry.get("start"))
        entry_end = int(entry.get("end") or entry_start)
    except (TypeError, ValueError):
        return (True, "exact_allele") if exact_allele_match(entry.get("allele_string"), clean(variant.get("ref_vcf")), clean(variant.get("alt_vcf"))) else (False, "")
    if not intervals_overlap(variant_start, variant_end, entry_start, entry_end):
        return False, ""
    ref = clean(variant.get("ref_vcf"))
    alt = clean(variant.get("alt_vcf"))
    if exact_allele_match(entry.get("allele_string"), ref, alt):
        return True, "exact_allele"
    observed_signature = normalized_allele_signature(ref, alt)
    alleles = [clean(value) for value in re.split(r"[|/,]", clean(entry.get("allele_string"))) if clean(value)]
    for left_index, left in enumerate(alleles):
        for right_index, right in enumerate(alleles):
            if left_index == right_index:
                continue
            if normalized_allele_signature(left, right) == observed_signature:
                return True, "normalized_indel_allele"
    return False, ""


def resolve_rsid(variant: dict, item: dict) -> dict:
    colocated = item.get("colocated_variants") if isinstance(item, dict) else []
    colocated = colocated if isinstance(colocated, list) else []
    colocated_rsids = sorted({normalize_rsid(entry.get("id")) for entry in colocated if isinstance(entry, dict) and normalize_rsid(entry.get("id"))})
    exact: list[tuple[dict, str]] = []
    for entry in colocated:
        matched, match_type = vep_entry_matches_variant(variant, entry)
        if matched and normalize_rsid(entry.get("id")):
            exact.append((entry, match_type))
    vcf_id = normalize_rsid(variant.get("id_vcf"))
    resolved = sorted({normalize_rsid(entry.get("id")) for entry, _ in exact if normalize_rsid(entry.get("id"))})
    if vcf_id and vcf_id in resolved:
        return {
            "resolved_rsid": vcf_id,
            "status": "vcf_id_confirmed_by_vep_exact_allele",
            "reason": "vcf_id_and_vep_allele_match",
            "colocated_count": len(colocated),
            "colocated_rsid_count": len(colocated_rsids),
        }
    if resolved:
        match_types = {match_type for entry, match_type in exact if normalize_rsid(entry.get("id")) in resolved}
        status = "vep_colocated_normalized_indel" if "normalized_indel_allele" in match_types else "vep_colocated_exact_allele"
        if len(resolved) > 1:
            status = "ambiguous_multiple_exact_rsids"
        return {
            "resolved_rsid": resolved[0] if len(resolved) == 1 else "",
            "status": status,
            "reason": "unique_vep_colocated_allele_match" if len(resolved) == 1 else "multiple_vep_colocated_allele_matches",
            "colocated_count": len(colocated),
            "colocated_rsid_count": len(colocated_rsids),
        }
    if colocated_rsids:
        return {
            "resolved_rsid": "",
            "status": "vep_colocated_allele_mismatch",
            "reason": "colocated_rsid_requires_coordinate_allele_reconciliation",
            "colocated_count": len(colocated),
            "colocated_rsid_count": len(colocated_rsids),
        }
    return {
        "resolved_rsid": "",
        "status": "unresolved_no_exact_rsid",
        "reason": "vep_response_has_no_colocated_rsid",
        "colocated_count": len(colocated),
        "colocated_rsid_count": 0,
    }


def exact_rsid(variant: dict, item: dict) -> tuple[str, str]:
    resolved = resolve_rsid(variant, item)
    return resolved["resolved_rsid"], resolved["status"]


def parse_vep_for_gene(item: dict, target_gene: str) -> dict:
    transcripts = item.get("transcript_consequences") if isinstance(item, dict) else []
    transcripts = [entry for entry in (transcripts or []) if isinstance(entry, dict)]
    target_entries = [entry for entry in transcripts if clean(entry.get("gene_symbol")).upper() == clean(target_gene).upper()]
    selected = sorted(target_entries, key=transcript_rank)[0] if target_entries else {}
    all_selected = sorted(transcripts, key=transcript_rank)[0] if transcripts else {}
    selected_for_summary = selected or all_selected
    colocated = item.get("colocated_variants") if isinstance(item, dict) else []
    colocated = colocated if isinstance(colocated, list) else []
    transcript_summaries = [transcript_summary(entry) for entry in target_entries[:10] if transcript_summary(entry)]
    if not transcript_summaries:
        transcript_summaries = [transcript_summary(entry) for entry in transcripts[:5] if transcript_summary(entry)]
    colocated_summary = legacy.unique_join(
        [
            "; ".join(
                part for part in [
                    f"id={clean(entry.get('id'))}" if clean(entry.get("id")) else "",
                    f"alleles={clean(entry.get('allele_string'))}" if clean(entry.get("allele_string")) else "",
                    f"maf={clean(entry.get('minor_allele_freq'))}" if clean(entry.get("minor_allele_freq")) else "",
                ] if part
            )
            for entry in colocated[:10] if isinstance(entry, dict)
        ],
        limit=10,
    )
    picked = selected_for_summary
    return {
        "status": "direct_target_transcript" if selected else "other_gene_only" if transcripts else "no_transcript_consequence",
        "most_severe_consequence": clean(item.get("most_severe_consequence")),
        "variant_class": clean(item.get("variant_class")),
        "gene_symbols": legacy.unique_join([entry.get("gene_symbol") for entry in transcripts]),
        "impacts": legacy.unique_join([entry.get("impact") for entry in target_entries or transcripts]),
        "consequence_terms": legacy.unique_join([term for entry in target_entries or transcripts for term in entry.get("consequence_terms") or []], limit=12),
        "transcript_summary": legacy.unique_join(transcript_summaries, limit=10),
        "picked_gene_symbol": clean(picked.get("gene_symbol")),
        "picked_transcript_id": clean(picked.get("transcript_id")),
        "picked_canonical": clean(picked.get("canonical")),
        "picked_mane_select": clean(picked.get("mane_select")),
        "picked_hgvsc": clean(picked.get("hgvsc")),
        "picked_hgvsp": clean(picked.get("hgvsp")),
        "picked_protein_id": clean(picked.get("protein_id")),
        "picked_exon": clean(picked.get("exon")),
        "picked_intron": clean(picked.get("intron")),
        "picked_cdna": "-".join(value for value in [clean(picked.get("cdna_start")), clean(picked.get("cdna_end"))] if value),
        "picked_cds": "-".join(value for value in [clean(picked.get("cds_start")), clean(picked.get("cds_end"))] if value),
        "picked_amino_acids": clean(picked.get("amino_acids")),
        "picked_protein_position": "-".join(value for value in [clean(picked.get("protein_start")), clean(picked.get("protein_end"))] if value),
        "picked_sift_prediction": clean(picked.get("sift_prediction") or picked.get("sift_pred")),
        "picked_sift_score": clean(picked.get("sift_score")),
        "picked_polyphen_prediction": clean(picked.get("polyphen_prediction") or picked.get("polyphen2_hdiv_pred")),
        "picked_polyphen_score": clean(picked.get("polyphen_score")),
        "picked_cadd_phred": clean(picked.get("cadd_phred")),
        "picked_revel_score": clean(picked.get("revel_score") or picked.get("revel")),
        "picked_alphamissense_score": clean(picked.get("alphamissense_score") or picked.get("alphamissense")),
        "picked_alphamissense_pred": clean(picked.get("alphamissense_pred")),
        "picked_mutationtaster_pred": clean(picked.get("mutationtaster_pred")),
        "picked_metasvm_pred": clean(picked.get("metasvm_pred")),
        "picked_spliceai": as_text(picked.get("spliceai")),
        "picked_uniprot": legacy.unique_join([picked.get("swissprot"), picked.get("trembl"), picked.get("uniparc"), picked.get("uniprot_isoform")]),
        "domains_summary": legacy.unique_join([f"{clean(domain.get('db'))}:{clean(domain.get('name'))}" for domain in picked.get("domains") or [] if isinstance(domain, dict)]),
        "colocated_variants": colocated_summary,
        "vrs": legacy.compact_json(item.get("vrs") or item.get("ga4gh_vrs") or ""),
        "raw_json": legacy.compact_json(item),
    }


def secondary_source_status(source: str, payload: dict, error: str) -> str:
    if error:
        return "source_error"
    if source == "clinvar" and clean(payload.get("count")) in {"", "0"}:
        return "not_found"
    if source == "gwas" and clean(payload.get("association_count")) in {"", "0"}:
        return "not_found"
    if source == "pharmgkb" and clean(payload.get("variant_id")) == "" and clean(payload.get("clinical_annotation_count")) in {"", "0"}:
        return "not_found"
    return "success"


def cached_secondary(cache: EnrichmentCache, assembly: str, variant: dict, rsid: str, source: str, func, timeout_seconds: int) -> tuple[dict, str, bool, str, float]:
    request = {"rsid": rsid, "source": source, "pipeline": PIPELINE_VERSION}
    key = fingerprint(request)
    started = time.perf_counter()
    cached = cache.get(assembly, variant["variant_key"], source, key)
    if cached:
        cached_payload = cached["payload"] if isinstance(cached["payload"], dict) else {}
        return cached_payload.get("data") or {}, clean(cached_payload.get("error")), True, cached.get("status") or "success", time.perf_counter() - started
    payload, error = func(rsid, timeout_seconds)
    status = secondary_source_status(source, payload, error)
    cache.put(assembly, variant["variant_key"], source, key, {"data": payload, "error": error}, status, None)
    return payload, error, False, status, time.perf_counter() - started


def fetch_secondary_source(variant: dict, assembly: str, cache: EnrichmentCache, timeout_seconds: int, source: str, output_key: str, func) -> dict:
    rsid = clean(variant.get("resolved_rsid"))
    if not rsid:
        return {"variant_key": variant["variant_key"], "source": source, "output_key": output_key, "payload": {}, "error": "", "cache_hit": False, "status": "not_queried", "elapsed_seconds": 0.0}
    payload, error, cache_hit, status, elapsed = cached_secondary(cache, assembly, variant, rsid, source, func, timeout_seconds)
    return {
        "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
        "payload": payload, "error": error, "cache_hit": cache_hit, "status": status,
        "elapsed_seconds": elapsed,
    }


def fetch_secondary_sources(
    variants: list[dict],
    assembly: str,
    cache: EnrichmentCache,
    timeout_seconds: int,
    on_progress=None,
) -> tuple[dict[str, dict], dict]:
    calls = {
        "ensembl_variation": ("ensemblVariation", legacy.fetch_ensembl_variation),
        "clinvar": ("clinVar", legacy.fetch_clinvar),
        "myvariant": ("myVariant", legacy.fetch_myvariant),
        "gwas": ("gwasCatalog", legacy.fetch_gwas_catalog),
        "pharmgkb": ("clinPgx", legacy.fetch_clinpgx),
    }
    enrichments = {
        variant["variant_key"]: {
            "errors": {}, "source_status": {source: "not_queried" for source in SECONDARY_SOURCE_ORDER},
            "cache_hits": 0, "ensemblVariation": {}, "clinVar": {}, "myVariant": {}, "gwasCatalog": {}, "clinPgx": {},
        }
        for variant in variants
    }
    metrics = {
        "calls_total": len(variants) * len(calls), "calls_completed": 0, "cache_hits": 0,
        "source_stats": {source: {"workers": SOURCE_WORKERS[source], "completed": 0, "cache_hits": 0, "network_calls": 0, "errors": 0, "not_found": 0, "elapsed_seconds": 0.0} for source in calls},
    }
    secondary_started = time.perf_counter()
    for source, (output_key, func) in calls.items():
        source_started = time.perf_counter()
        workers = max(1, int(SOURCE_WORKERS.get(source, DEFAULT_SECONDARY_WORKERS)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_secondary_source, variant, assembly, cache, timeout_seconds, source, output_key, func): variant
                for variant in variants
            }
            for future in as_completed(futures):
                variant = futures[future]
                try:
                    result = future.result()
                except Exception as error:  # Secondary sources must not erase VEP evidence.
                    result = {
                        "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
                        "payload": {}, "error": str(error), "cache_hit": False, "status": "source_error", "elapsed_seconds": 0.0,
                    }
                target = enrichments[result["variant_key"]]
                target[output_key] = result["payload"] or {}
                target["source_status"][source] = result["status"]
                target["cache_hits"] += int(result["cache_hit"])
                if result["error"]:
                    target["errors"][source] = result["error"]
                source_metrics = metrics["source_stats"][source]
                source_metrics["completed"] += 1
                source_metrics["cache_hits"] += int(result["cache_hit"])
                source_metrics["network_calls"] += int(not result["cache_hit"])
                source_metrics["errors"] += int(result["status"] == "source_error")
                source_metrics["not_found"] += int(result["status"] == "not_found")
                source_metrics["elapsed_seconds"] += float(result["elapsed_seconds"] or 0.0)
                metrics["calls_completed"] += 1
                metrics["cache_hits"] += int(result["cache_hit"])
                metrics["elapsed_seconds"] = round(time.perf_counter() - secondary_started, 3)
                metrics["calls_per_second"] = round(metrics["calls_completed"] / max(metrics["elapsed_seconds"], 0.001), 3)
                source_metrics["wall_seconds"] = round(time.perf_counter() - source_started, 3)
                if on_progress:
                    on_progress(metrics, source)
    return enrichments, metrics


def snake_case_only(row: dict) -> dict:
    return {key: value for key, value in row.items() if re.fullmatch(r"[a-z][a-z0-9_]*", key)}


def build_module_row(row: dict, enrichment: dict) -> dict:
    # Reuse legacy field derivations, then remove historical display aliases from v2 output.
    plus = snake_case_only(legacy.build_plus_output_row_v2(row, enrichment))
    vep = enrichment.get("ensemblVep") or {}
    statuses = enrichment.get("source_status") or {}
    return {
        **row,
        **plus,
        "schema_version": "gene_module_v2",
        "assembly": clean(row.get("assembly_name") or row.get("assembly")),
        "variant_key": clean(row.get("variant_key")),
        "resolved_rsid": clean(enrichment.get("resolved_rsid")),
        "rsid_resolution_status": clean(enrichment.get("rsid_resolution_status")),
        "vep_status": clean(enrichment.get("vep_status")),
        "vep_target_gene_effect_status": clean(vep.get("status")),
        "vep_vrs": clean(vep.get("vrs")),
        "source_status_ensembl_vep": clean(statuses.get("ensembl_vep")),
        "source_status_ensembl_variation": clean(statuses.get("ensembl_variation")),
        "source_status_clinvar": clean(statuses.get("clinvar")),
        "source_status_myvariant": clean(statuses.get("myvariant")),
        "source_status_gwas": clean(statuses.get("gwas")),
        "source_status_pharmgkb": clean(statuses.get("pharmgkb")),
    }


def build_variant_master_row(variant: dict, enrichment: dict, module_count: int) -> dict:
    vep = enrichment.get("ensemblVep") or {}
    variation = enrichment.get("ensemblVariation") or {}
    resolution_status = clean(enrichment.get("rsid_resolution_status"))
    if clean(enrichment.get("vep_status")) == "source_error":
        vep_only_class = "vep_error"
    elif resolution_status == "vep_colocated_allele_mismatch":
        vep_only_class = "colocated_rsid_allele_mismatch"
    elif resolution_status == "ambiguous_multiple_exact_rsids":
        vep_only_class = "ambiguous_multiple_exact_rsids"
    elif clean(vep.get("vrs")) or clean(vep.get("picked_hgvsc")) or clean(vep.get("picked_hgvsp")):
        vep_only_class = "functional_annotation_without_exact_rsid"
    else:
        vep_only_class = "no_usable_vep_annotation"
    fallback_query_mode = (
        "vrs_or_hgvs_available"
        if clean(vep.get("vrs")) or clean(vep.get("picked_hgvsc")) or clean(vep.get("picked_hgvsp"))
        else "coordinate_only"
    )
    return {
        "variant_key": variant["variant_key"], "assembly": variant["assembly"], "chrom_vcf": variant["chrom_vcf"],
        "pos_vcf": variant["pos_vcf"], "ref_vcf": variant["ref_vcf"], "alt_vcf": variant["alt_vcf"],
        "id_vcf": variant.get("id_vcf", ""), "resolved_rsid": enrichment.get("resolved_rsid", ""),
        "rsid_resolution_status": resolution_status,
        "resolution_reason": enrichment.get("resolution_reason", ""),
        "vep_only_class": vep_only_class,
        "vep_only_fallback_query_mode": fallback_query_mode,
        "vep_colocated_count": enrichment.get("vep_colocated_count", ""),
        "vep_colocated_rsid_count": enrichment.get("vep_colocated_rsid_count", ""),
        "module_row_count": module_count,
        "vep_status": enrichment.get("vep_status", ""), "vep_most_severe_consequence": vep.get("most_severe_consequence", ""),
        "vep_gene_symbols": vep.get("gene_symbols", ""), "vep_hgvsc": vep.get("picked_hgvsc", ""),
        "vep_hgvsp": vep.get("picked_hgvsp", ""), "vep_cadd_phred": vep.get("picked_cadd_phred", ""),
        "vep_revel_score": vep.get("picked_revel_score", ""), "vep_spliceai": vep.get("picked_spliceai", ""),
        "ensembl_population_summary": variation.get("populations", ""),
        "source_error_sources": "|".join(sorted((enrichment.get("errors") or {}).keys())),
    }


def materialize_module_rows(
    rows: list[dict],
    variants: dict[str, dict],
    enrichments_by_variant: dict[str, dict],
    vep_raw: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    output_rows: list[dict] = []
    evidence_rows: list[dict] = []
    for row in rows:
        key = clean(row.get("variant_key"))
        variant = variants[key]
        base = enrichments_by_variant[key]
        item = (vep_raw.get(key) or {}).get("item") or {}
        per_gene = {**base, "ensemblVep": parse_vep_for_gene(item, clean(row.get("approved_symbol")))}
        output_rows.append(build_module_row(row, per_gene))
        evidence_rows.append({
            "variant_key": key, "gene": clean(row.get("approved_symbol")), "module_id": clean(row.get("module_id")),
            "resolved_rsid": per_gene.get("resolved_rsid"), "rsid_resolution_status": per_gene.get("rsid_resolution_status"),
            "resolution_reason": per_gene.get("resolution_reason"), "source_status": per_gene.get("source_status"),
            "errors": per_gene.get("errors"), "vep": per_gene.get("ensemblVep"), "vep_raw": item,
            "ensembl_variation": per_gene.get("ensemblVariation"), "clinvar": per_gene.get("clinVar"),
            "myvariant": per_gene.get("myVariant"), "gwas": per_gene.get("gwasCatalog"), "pharmgkb": per_gene.get("clinPgx"),
        })
    return output_rows, evidence_rows


def main_process(payload: dict) -> dict:
    started_at = utc_now()
    process_started = time.perf_counter()
    input_path = Path(payload["inputPath"])
    output_dir = Path(payload["outputDir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(payload.get("cacheDir") or output_dir.parent / "cache")
    assembly = clean(payload.get("assembly") or "GRCh38")
    if assembly not in {"GRCh38", "GRCh37"}:
        raise ValueError("V2 enrichment requires an explicit supported assembly.")
    timeout_seconds = int(payload.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
    cache = EnrichmentCache(cache_dir / "enrichment_cache.sqlite", int(payload.get("cacheTtlDays") or DEFAULT_CACHE_TTL_DAYS))
    atexit.register(cache.close)
    rows = [row for row in read_csv(input_path) if clean(row.get("variant_key")) and clean(row.get("has_genotype")).lower() in {"true", "1", "yes"}]
    if not rows:
        raise ValueError("AI triage input contains no observed v2 physical variants.")
    write_progress(output_dir, substage="deduplicating_physical_variants", total=len(rows), unit="module rows", message="Building unique physical variant set")

    variants: dict[str, dict] = {}
    module_counts: dict[str, int] = {}
    for row in rows:
        variant_key = clean(row.get("variant_key")) or stable_variant_key(assembly, normalize_chromosome(row.get("chrom_vcf")), clean(row.get("pos_vcf")), clean(row.get("ref_vcf")), clean(row.get("alt_vcf")))
        if variant_key not in variants:
            variants[variant_key] = {
                "variant_key": variant_key, "assembly": assembly, "chrom_vcf": normalize_chromosome(row.get("chrom_vcf")),
                "pos_vcf": clean(row.get("pos_vcf")), "ref_vcf": clean(row.get("ref_vcf")), "alt_vcf": clean(row.get("alt_vcf")),
                "id_vcf": clean(row.get("id_vcf")),
            }
        module_counts[variant_key] = module_counts.get(variant_key, 0) + 1
    physical_variants = list(variants.values())
    write_progress(
        output_dir,
        stage="enrichment_vep",
        phase="vep_base",
        substage="vep",
        processed=0,
        total=len(physical_variants),
        unit="physical variants",
        message="Preparing coordinate-based VEP enrichment",
    )

    provenance = {"retrievedAt": utc_now(), "pipelineVersion": PIPELINE_VERSION, "assembly": assembly}
    vep_started = time.perf_counter()
    vep_raw: dict[str, dict] = {}
    vep_cache_hits = 0
    vep_requests = 0
    warnings: list[str] = []
    for offset in range(0, len(physical_variants), VEP_BATCH_SIZE):
        response, cache_hits, requests, batch_warnings = fetch_vep_batch(physical_variants[offset : offset + VEP_BATCH_SIZE], assembly, cache, timeout_seconds, provenance)
        vep_raw.update(response)
        vep_cache_hits += cache_hits
        vep_requests += requests
        warnings.extend(batch_warnings)
        vep_elapsed = time.perf_counter() - vep_started
        write_progress(
            output_dir,
            stage="enrichment_vep",
            phase="vep_base",
            substage="vep",
            processed=min(offset + VEP_BATCH_SIZE, len(physical_variants)),
            total=len(physical_variants),
            unit="physical variants",
            message="Enriching normalized variants with Ensembl VEP",
            metrics={
                "cache_hits": vep_cache_hits,
                "network_batches": vep_requests,
                "elapsed_seconds": round(vep_elapsed, 3),
                "items_per_second": round(min(offset + VEP_BATCH_SIZE, len(physical_variants)) / max(vep_elapsed, 0.001), 3),
            },
        )
    vep_elapsed_seconds = time.perf_counter() - vep_started

    enrichments_by_variant: dict[str, dict] = {}
    variants_for_secondary: list[dict] = []
    resolution_counts: dict[str, int] = {}
    for variant in physical_variants:
        raw = vep_raw.get(variant["variant_key"], {})
        item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
        resolution = resolve_rsid(variant, item)
        rsid = resolution["resolved_rsid"]
        rsid_status = resolution["status"]
        variant["resolved_rsid"] = rsid
        resolution_counts[rsid_status] = resolution_counts.get(rsid_status, 0) + 1
        enrichment = {
            "resolved_rsid": rsid,
            "rsid_resolution_status": rsid_status,
            "resolution_reason": resolution["reason"],
            "vep_colocated_count": resolution["colocated_count"],
            "vep_colocated_rsid_count": resolution["colocated_rsid_count"],
            "vep_status": clean(raw.get("status")) or "not_found",
            "errors": {"ensembl_vep": clean(raw.get("error"))} if clean(raw.get("error")) else {},
            "source_status": {
                "ensembl_vep": clean(raw.get("status")) or "not_found",
                **{source: "not_queried" for source in SECONDARY_SOURCE_ORDER},
            },
            "ensemblVep": parse_vep_for_gene(item, ""),
            "cacheHit": bool(raw.get("cache_hit")),
        }
        enrichments_by_variant[variant["variant_key"]] = enrichment
        if rsid:
            variants_for_secondary.append(variant)

    base_rows, base_evidence_rows = materialize_module_rows(rows, variants, enrichments_by_variant, vep_raw)
    base_csv = output_dir / "v2_enrichment_vep_base.csv"
    resolution_audit_path = output_dir / "v2_enrichment_resolution_audit.jsonl"
    write_csv(base_csv, base_rows, source_fields(base_rows))
    with resolution_audit_path.open("w", encoding="utf-8") as handle:
        for item in base_evidence_rows:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")
    write_json(
        output_dir / "enrichment_vep_base_summary.json",
        {
            "status": "valid",
            "schemaVersion": "gene_module_v2",
            "physicalVariants": len(physical_variants),
            "moduleRows": len(rows),
            "vepSuccessfulVariants": sum(1 for value in enrichments_by_variant.values() if value.get("vep_status") == "success"),
            "resolutionCounts": resolution_counts,
            "vepCacheHits": vep_cache_hits,
            "vepNetworkVariants": vep_requests,
            "outputs": {"vepBaseCsv": str(base_csv), "resolutionAuditJsonl": str(resolution_audit_path)},
        },
    )

    write_progress(
        output_dir,
        stage="enrichment_complete",
        phase="secondary_sources",
        substage="secondary_sources",
        processed=0,
        total=len(variants_for_secondary) * len(SECONDARY_SOURCE_ORDER),
        unit="source calls",
        message="Querying secondary sources for exact variant identities",
        metrics={"eligibleVariants": len(variants_for_secondary), "resolutionCounts": resolution_counts},
    )

    def on_secondary_progress(metrics: dict, source: str) -> None:
        completed = int(metrics.get("calls_completed") or 0)
        total = int(metrics.get("calls_total") or 0)
        if completed % 10 == 0 or completed == total:
            write_progress(
                output_dir,
                stage="enrichment_complete",
                phase="secondary_sources",
                substage=f"secondary_{source}",
                processed=completed,
                total=total,
                unit="source calls",
                message=f"Querying {source}",
                metrics=metrics,
            )

    secondary_started = time.perf_counter()
    secondary_enrichments, secondary_metrics = fetch_secondary_sources(
        variants_for_secondary,
        assembly,
        cache,
        timeout_seconds,
        on_progress=on_secondary_progress,
    )
    secondary_metrics["wall_seconds"] = round(time.perf_counter() - secondary_started, 3)
    for variant_key, secondary in secondary_enrichments.items():
        enrichment = enrichments_by_variant[variant_key]
        enrichment.update({key: value for key, value in secondary.items() if key not in {"errors", "source_status"}})
        enrichment["errors"].update(secondary.get("errors") or {})
        enrichment["source_status"].update(secondary.get("source_status") or {})

    complete_rows, _complete_evidence_rows = materialize_module_rows(rows, variants, enrichments_by_variant, vep_raw)
    complete_rows = [row for row in complete_rows if clean(row.get("resolved_rsid"))]
    complete_csv = output_dir / "v2_enrichment_complete.csv"
    write_csv(complete_csv, complete_rows, source_fields(complete_rows))
    write_progress(
        output_dir,
        stage="enrichment_vep_only",
        phase="vep_only_remediation",
        substage="audit_unresolved",
        processed=0,
        total=len(physical_variants) - len(variants_for_secondary),
        unit="physical variants",
        message="Auditing VEP-only, ambiguous and VEP-error variants",
        metrics={"secondary": secondary_metrics, "resolutionCounts": resolution_counts},
    )

    output_rows, evidence_rows = materialize_module_rows(rows, variants, enrichments_by_variant, vep_raw)

    master_rows = [build_variant_master_row(variant, enrichments_by_variant[variant["variant_key"]], module_counts[variant["variant_key"]]) for variant in physical_variants]
    vep_only_master_rows = [row for row in master_rows if not clean(row.get("resolved_rsid"))]
    vep_only_csv = output_dir / "v2_enrichment_vep_only_audit.csv"
    write_csv(vep_only_csv, vep_only_master_rows, source_fields(master_rows))
    write_progress(
        output_dir,
        stage="enrichment_vep_only",
        phase="vep_only_remediation",
        substage="complete",
        processed=len(vep_only_master_rows),
        total=len(physical_variants) - len(variants_for_secondary),
        unit="physical variants",
        message="VEP-only resolution audit completed",
        metrics={
            "vepOnlyVariants": len(vep_only_master_rows),
            "resolutionCounts": resolution_counts,
            "secondary": secondary_metrics,
        },
    )
    fields = source_fields(output_rows)
    master_fields = source_fields(master_rows)
    output_csv = output_dir / "heal_observed_variant_enrichment_v2.csv"
    observed_csv = output_dir / "heal_fon_interpretation_enriched_observed_v2.csv"
    plus_csv = output_dir / "heal_fon_interpretation_enrichment_plus_v2.csv"
    master_csv = output_dir / "v2_enrichment_variant_master.csv"
    evidence_path = output_dir / "v2_enrichment_evidence_audit.jsonl"
    write_csv(output_csv, output_rows, fields)
    write_csv(observed_csv, output_rows, fields)
    write_csv(plus_csv, output_rows, fields)
    write_csv(master_csv, master_rows, master_fields)
    with evidence_path.open("w", encoding="utf-8") as handle:
        for item in evidence_rows:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")

    performance = {
        "schemaVersion": "gene_module_v2",
        "startedAt": started_at,
        "completedAt": utc_now(),
        "elapsedSeconds": time.perf_counter() - process_started,
        "physicalVariants": len(physical_variants),
        "moduleRows": len(rows),
        "vepSeconds": vep_elapsed_seconds,
        "secondary": secondary_metrics,
        "secondaryWallSeconds": secondary_metrics.get("wall_seconds", 0),
        "resolutionCounts": resolution_counts,
    }
    performance_path = output_dir / "enrichment_performance_summary.json"
    write_json(performance_path, performance)

    normalization_summary = {}
    normalization_summary_path = clean(payload.get("normalizationSummaryPath"))
    if normalization_summary_path and Path(normalization_summary_path).is_file():
        normalization_summary = json.loads(Path(normalization_summary_path).read_text(encoding="utf-8"))
    normalization_rate = float(((normalization_summary.get("counts") or {}).get("normalizationValidRate")) or 0)
    vep_success_count = sum(1 for variant in physical_variants if clean(enrichments_by_variant[variant["variant_key"]].get("vep_status")) == "success")
    vep_coverage = vep_success_count / len(physical_variants) if physical_variants else 0.0
    source_errors = {}
    for value in enrichments_by_variant.values():
        for source in (value.get("errors") or {}):
            source_errors[source] = source_errors.get(source, 0) + 1
    gate_status = "pass" if normalization_rate >= 0.99 and vep_coverage >= 0.95 else "fail"
    quality = {
        "schemaVersion": "gene_module_v2", "status": gate_status, "createdAt": utc_now(),
        "normalizationValidRate": normalization_rate, "minimumNormalizationValidRate": 0.99,
        "physicalVariants": len(physical_variants), "moduleRows": len(rows),
        "vepSuccessfulVariants": vep_success_count, "vepCoverage": vep_coverage, "minimumVepCoverage": 0.95,
        "exactRsidsResolved": sum(1 for value in enrichments_by_variant.values() if clean(value.get("resolved_rsid"))),
        "resolutionCounts": resolution_counts,
        "vepOnlyVariants": len(vep_only_master_rows),
        "secondaryMetrics": secondary_metrics,
        "vepCacheHits": vep_cache_hits, "vepNetworkVariants": vep_requests,
        "sourceErrors": source_errors, "warnings": warnings,
        "decision": "pass" if gate_status == "pass" else "block_downstream_until_enrichment_is_remediated",
        "provenance": provenance,
        "reference": normalization_summary.get("reference") or {},
    }
    quality_path = output_dir / "enrichment_quality_summary.json"
    write_json(quality_path, quality)
    summary = {
        "status": "valid", "schemaVersion": "gene_module_v2", "adapter": "gene_module_coordinate_enrichment",
        "startedAt": started_at, "completedAt": utc_now(), "inputPath": str(input_path),
        "observedVariantEnrichmentCsv": str(output_csv), "observedVariantEnrichmentColabCsv": str(observed_csv),
        "observedVariantEnrichmentPlusCsv": str(plus_csv), "v2EnrichmentVariantMasterCsv": str(master_csv),
        "v2EnrichmentEvidenceAuditJsonl": str(evidence_path), "enrichmentQualitySummaryJson": str(quality_path),
        "v2EnrichmentVepBaseCsv": str(base_csv), "v2EnrichmentCompleteCsv": str(complete_csv),
        "v2EnrichmentVepOnlyAuditCsv": str(vep_only_csv), "v2EnrichmentResolutionAuditJsonl": str(resolution_audit_path),
        "enrichmentPerformanceSummaryJson": str(performance_path),
        "metadata": {"qualityGate": quality, "downstreamSupported": False, "performance": performance},
    }
    write_json(output_dir / "observed_variant_enrichment_summary.json", summary)
    write_progress(
        output_dir,
        stage="enrichment_quality_gate",
        phase="quality_gate",
        substage="complete",
        processed=1,
        total=1,
        unit="quality gates",
        message="External enrichment completed; quality gate ready",
        metrics={"qualityGate": quality, "performance": performance},
    )
    print(json.dumps(summary, ensure_ascii=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    main_process(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=True))
        raise
