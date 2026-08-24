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
import gzip
import hashlib
import json
import os
import random
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


PIPELINE_VERSION = "gene-module-v2-enrichment-2"
RESUME_CONTRACT_VERSION = "enrichment-resume-manifest-v1"
CACHE_SCHEMA_VERSION = 2
DEFAULT_MIN_VEP_COVERAGE = 0.90
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
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: object) -> str:
    return legacy.clean_str(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def transient_provider_error(error: str, http_status: int | None = None) -> bool:
    value = clean(error).lower()
    if http_status in TRANSIENT_HTTP_CODES:
        return True
    if any(re.search(rf"\bhttp[_ :/-]*{code}\b", value) for code in TRANSIENT_HTTP_CODES):
        return True
    return any(token in value for token in (
        "timed out", "timeout", "connection reset", "remote end closed", "temporarily unavailable",
    ))


def retry_delay_seconds(attempt: int, headers: dict | None = None) -> float:
    retry_after = clean((headers or {}).get("Retry-After") or (headers or {}).get("retry-after"))
    if retry_after.isdigit():
        return min(30.0, max(0.0, float(retry_after)))
    return min(30.0, 1.0 * (2 ** max(0, attempt - 1)) + random.uniform(0.0, 0.5))


def write_resume_manifest(output_dir: Path, *, input_sha256: str, assembly: str, phase: str,
                          processed: int, total: int, metrics: dict | None = None) -> None:
    write_json(output_dir / "enrichment_resume_manifest_v1.json", {
        "schema_version": "enrichment_resume_manifest_v1",
        "resume_contract_version": RESUME_CONTRACT_VERSION,
        "input_sha256": input_sha256,
        "assembly": assembly,
        "phase": phase,
        "processed": int(processed),
        "total": int(total),
        "metrics": metrics or {},
        "cache_is_checkpoint_authority": True,
        "updated_at": utc_now(),
    }, tolerate_replace_lock=True)


def configured_min_vep_coverage() -> float:
    try:
        value = float(os.environ.get("HEAL_V2_MIN_VEP_COVERAGE", DEFAULT_MIN_VEP_COVERAGE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_VEP_COVERAGE
    return min(1.0, max(0.0, value))


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict, *, tolerate_replace_lock: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt >= 7:
                if tolerate_replace_lock:
                    temporary.unlink(missing_ok=True)
                    return
                raise
            time.sleep(0.05 * (attempt + 1))


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
        # Progress is advisory; a transient Windows reader lock must not abort enrichment.
        tolerate_replace_lock=True,
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
    def __init__(self, path: Path, ttl_days: int, legacy_path: Path | None = None):
        self.path = path
        self.legacy_path = legacy_path if legacy_path and legacy_path != path else None
        self.ttl = dt.timedelta(days=ttl_days)
        self._lock = threading.RLock()
        self._legacy_connections = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        with self._lock:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    assembly TEXT NOT NULL,
                    variant_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    query_mode TEXT NOT NULL DEFAULT 'default',
                    request_fingerprint TEXT NOT NULL,
                    identity_fingerprint TEXT NOT NULL DEFAULT '',
                    response_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    status_reason TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    retry_after TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    PRIMARY KEY (assembly, variant_key, source, query_mode)
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
            legacy_connection = getattr(self._legacy_connections, "connection", None)
            if legacy_connection is not None:
                legacy_connection.close()
                self._legacy_connections.connection = None

    def legacy_connection(self) -> sqlite3.Connection | None:
        if not self.legacy_path or not self.legacy_path.exists():
            return None
        connection = getattr(self._legacy_connections, "connection", None)
        if connection is None:
            uri = f"file:{self.legacy_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=3, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=3000")
            self._legacy_connections.connection = connection
        return connection

    def legacy_get(
        self,
        assembly: str,
        variant_key: str,
        source: str,
        fingerprints: list[str],
        query_mode: str,
    ) -> dict | None:
        connection = self.legacy_connection()
        if connection is None or not fingerprints:
            return None
        try:
            row = connection.execute(
                """SELECT request_fingerprint, response_json, status, http_status, fetched_at, expires_at
                   FROM enrichment_cache
                   WHERE assembly = ? AND variant_key = ? AND source = ?""",
                (assembly, variant_key, source),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row or row["status"] == "source_error" or row["request_fingerprint"] not in fingerprints:
            return None
        try:
            expires_at = dt.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires_at <= dt.datetime.now(dt.UTC):
                return None
            payload = json.loads(row["response_json"])
        except (ValueError, json.JSONDecodeError, TypeError):
            return None
        return {
            "payload": payload,
            "status": row["status"],
            "status_reason": "legacy_cache_reused",
            "http_status": row["http_status"],
            "retry_after": "",
            "fetched_at": row["fetched_at"],
            "query_mode": query_mode,
            "identity_fingerprint": "",
        }

    def get(
        self,
        assembly: str,
        variant_key: str,
        source: str,
        fingerprint: str,
        query_mode: str = "default",
        connection: sqlite3.Connection | None = None,
        legacy_fingerprint: str | None = None,
    ) -> dict | None:
        connection = connection or self.thread_connection()
        with self._lock:
            row = connection.execute(
                """SELECT response_json, status, status_reason, http_status, retry_after, fetched_at, expires_at,
                          query_mode, identity_fingerprint
                   FROM enrichment_cache
                   WHERE assembly = ? AND variant_key = ? AND source = ? AND query_mode = ? AND request_fingerprint = ?""",
                (assembly, variant_key, source, query_mode, fingerprint),
            ).fetchone()
        if not row:
            return self.legacy_get(
                assembly,
                variant_key,
                source,
                [value for value in [legacy_fingerprint, fingerprint] if value],
                query_mode,
            )
        if row["status"] == "source_error":
            return None
        try:
            expires_at = dt.datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires_at <= dt.datetime.now(dt.UTC):
                return None
            payload = json.loads(row["response_json"])
        except (ValueError, json.JSONDecodeError):
            return None
        return {
            "payload": payload,
            "status": row["status"],
            "status_reason": row["status_reason"],
            "http_status": row["http_status"],
            "retry_after": row["retry_after"],
            "fetched_at": row["fetched_at"],
            "query_mode": row["query_mode"],
            "identity_fingerprint": row["identity_fingerprint"],
        }

    def put(
        self,
        assembly: str,
        variant_key: str,
        source: str,
        fingerprint: str,
        payload: object,
        status: str,
        http_status: int | None,
        connection: sqlite3.Connection | None = None,
        *,
        query_mode: str = "default",
        identity_fingerprint: str = "",
        status_reason: str = "",
        retry_after: str = "",
    ) -> None:
        fetched_at = dt.datetime.now(dt.UTC)
        ttl = dt.timedelta(hours=1) if status == "source_error" else self.ttl
        values = (
            assembly,
            variant_key,
            source,
            query_mode,
            fingerprint,
            identity_fingerprint,
            json.dumps(payload, ensure_ascii=True),
            status,
            status_reason,
            http_status,
            retry_after,
            fetched_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            (fetched_at + ttl).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            PIPELINE_VERSION,
        )
        connection = connection or self.thread_connection()
        for attempt in range(4):
            try:
                with self._lock:
                    connection.execute(
                        """INSERT INTO enrichment_cache
                           (assembly, variant_key, source, query_mode, request_fingerprint, identity_fingerprint,
                            response_json, status, status_reason, http_status, retry_after, fetched_at, expires_at, pipeline_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(assembly, variant_key, source, query_mode) DO UPDATE SET
                             request_fingerprint=excluded.request_fingerprint,
                             identity_fingerprint=excluded.identity_fingerprint,
                             response_json=excluded.response_json,
                             status=excluded.status,
                             status_reason=excluded.status_reason,
                             http_status=excluded.http_status,
                             retry_after=excluded.retry_after,
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


def get_json(url: str, timeout_seconds: int) -> tuple[object | None, str, int | None, dict]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": legacy.USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body), "", response.status, dict(response.headers.items())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        return None, f"http_{error.code}: {detail}", error.code, dict(error.headers.items()) if error.headers else {}
    except Exception as error:
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
        cached = cache.get(
            assembly,
            variant["variant_key"],
            "ensembl_vep_region",
            fingerprint(request_payload),
            query_mode="coordinate",
        )
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
    for attempt in range(1, 4):
        response, error, http_status, headers = post_json(query_url, payload, timeout_seconds)
        if not error or not transient_provider_error(error, http_status):
            break
        if attempt < 3:
            time.sleep(retry_delay_seconds(attempt, headers))
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
        cache.put(
            assembly,
            variant["variant_key"],
            "ensembl_vep_region",
            fingerprint({"variant": vep_region_line(variant), "params": VEP_PARAMS}),
            item,
            status,
            http_status,
            query_mode="coordinate",
            identity_fingerprint=fingerprint({"assembly": assembly, "variant_key": variant["variant_key"]}),
            status_reason="vep_response" if item else ("vep_request_error" if error else "vep_no_result"),
        )
        resolved[variant["variant_key"]] = {"item": item, "cache_hit": False, "status": status, "error": error if not item else ""}
    return resolved, cache_hits, len(misses), errors


def _vep_failure_can_split(result: dict) -> bool:
    if clean(result.get("status")) != "source_error":
        return False
    error = clean(result.get("error")).lower()
    if "429" in error or "too many requests" in error or "retry-after" in error:
        return False
    return any(
        token in error
        for token in (
            "timed out",
            "timeout",
            "connection reset",
            "remote end closed",
            "http error 413", "http_413",
            "http error 500", "http_500",
            "http error 502", "http_502",
            "http error 503", "http_503",
            "http error 504", "http_504",
        )
    )


def fetch_vep_adaptive(
    batch: list[dict],
    assembly: str,
    cache: EnrichmentCache,
    timeout_seconds: int,
    provenance: dict,
) -> tuple[dict[str, dict], int, int, list[str]]:
    """Recover transient failed VEP batches without losing successful rows.

    Failed 200-row requests are retried as 50, then 10, then individual
    variants. Rate-limit responses are intentionally left for the run retry
    queue instead of multiplying traffic against an already throttled source.
    """

    network_variant_keys: set[str] = set()

    def fetch(candidates: list[dict]) -> tuple[dict[str, dict], int]:
        response, cache_hits, network_variants, _warnings = fetch_vep_batch(
            candidates,
            assembly,
            cache,
            timeout_seconds,
            provenance,
        )
        if network_variants:
            network_variant_keys.update(
                clean(variant.get("variant_key"))
                for variant in candidates
                if not response.get(clean(variant.get("variant_key")), {}).get("cache_hit")
            )
        failed = [
            variant
            for variant in candidates
            if _vep_failure_can_split(response.get(clean(variant.get("variant_key")), {}))
        ]
        if len(failed) <= 1:
            return response, cache_hits
        split_size = 50 if len(failed) > 50 else 10 if len(failed) > 10 else 1
        for offset in range(0, len(failed), split_size):
            child_response, child_hits = fetch(failed[offset : offset + split_size])
            response.update(child_response)
            cache_hits += child_hits
        return response, cache_hits

    resolved, cache_hits = fetch(batch)
    unresolved_errors = sorted(
        {
            clean(result.get("error"))
            for result in resolved.values()
            if clean(result.get("status")) == "source_error" and clean(result.get("error"))
        }
    )
    warnings = [f"ensembl_vep_region: {error}" for error in unresolved_errors]
    return resolved, cache_hits, len(network_variant_keys), warnings


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


def coordinate_region(variant: dict) -> str:
    chrom = clean(variant.get("chrom_vcf")).removeprefix("chr")
    start = int(variant.get("pos_vcf") or variant.get("variant_start") or 0)
    end = int(variant.get("variant_end") or start + max(len(clean(variant.get("ref_vcf"))), 1) - 1)
    return f"{chrom}:{start}..{end}"


def coordinate_allele_match(variant: dict, entry: dict) -> tuple[bool, str]:
    """Match Ensembl/MyVariant coordinate records without trusting proximity alone."""
    try:
        variant_start = int(variant.get("pos_vcf"))
        variant_end = int(variant.get("variant_end") or variant_start + max(len(clean(variant.get("ref_vcf"))), 1) - 1)
        entry_start = int(entry.get("start") or entry.get("position"))
        entry_end = int(entry.get("end") or entry_start)
    except (TypeError, ValueError):
        return False, "invalid_coordinate"
    if not intervals_overlap(variant_start, variant_end, entry_start, entry_end):
        return False, "coordinate_mismatch"
    allele_string = entry.get("allele_string") or entry.get("alleles") or entry.get("_id") or ""
    ref = clean(variant.get("ref_vcf"))
    alt = clean(variant.get("alt_vcf"))
    if exact_allele_match(allele_string, ref, alt):
        return True, "exact_coordinate_allele"
    observed_signature = normalized_allele_signature(ref, alt)
    alleles = [clean(value) for value in re.split(r"[|/,]", clean(allele_string)) if clean(value)]
    for left_index, left in enumerate(alleles):
        for right_index, right in enumerate(alleles):
            if left_index == right_index:
                continue
            if normalized_allele_signature(left, right) == observed_signature:
                return True, "normalized_indel_match"
    return False, "allele_mismatch"


def resolve_coordinate_identity(variant: dict, timeout_seconds: int) -> dict:
    """Resolve an rsID through Ensembl variation overlap with exact allele checks."""
    url = (
        "https://rest.ensembl.org/overlap/region/human/"
        f"{urllib.parse.quote(coordinate_region(variant), safe=':..')}"
        "?feature=variation;content-type=application/json"
    )
    payload, error, http_status, headers = get_json(url, timeout_seconds)
    if error:
        return {
            "resolved_rsid": "",
            "candidate_rsids": [],
            "status": "source_error",
            "reason": "coordinate_identity_lookup_failed",
            "error": error,
            "http_status": http_status,
            "headers": headers,
            "url": url,
        }
    entries = payload if isinstance(payload, list) else []
    candidates: list[str] = []
    exact: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = normalize_rsid(entry.get("id"))
        if identifier:
            candidates.append(identifier)
        matched, match_class = coordinate_allele_match(variant, entry)
        if matched and identifier:
            exact.append((identifier, match_class))
    exact_ids = sorted({item[0] for item in exact})
    if len(exact_ids) == 1:
        match_classes = {item[1] for item in exact if item[0] == exact_ids[0]}
        return {
            "resolved_rsid": exact_ids[0],
            "candidate_rsids": sorted(set(candidates)),
            "status": "resolved_coordinate_exact",
            "reason": next(iter(match_classes)),
            "identity_match_class": next(iter(match_classes)),
            "http_status": http_status,
            "headers": headers,
            "url": url,
            "candidate_count": len(set(candidates)),
        }
    if len(exact_ids) > 1:
        return {
            "resolved_rsid": "",
            "candidate_rsids": sorted(set(exact_ids)),
            "status": "ambiguous_multiple_exact_rsids",
            "reason": "multiple_coordinate_exact_rsids",
            "identity_match_class": "ambiguous_identity",
            "http_status": http_status,
            "headers": headers,
            "url": url,
            "candidate_count": len(set(candidates)),
        }
    return {
        "resolved_rsid": "",
        "candidate_rsids": sorted(set(candidates)),
        "status": "coordinate_no_exact_allele",
        "reason": "coordinate_lookup_without_exact_allele",
        "identity_match_class": "rsid_without_allele_confirmation" if candidates else "no_identity_match",
        "http_status": http_status,
        "headers": headers,
        "url": url,
        "candidate_count": len(set(candidates)),
    }


def myvariant_coordinate_hgvs(variant: dict) -> str:
    chrom = clean(variant.get("chrom_vcf"))
    pos = clean(variant.get("pos_vcf"))
    ref = clean(variant.get("ref_vcf"))
    alt = clean(variant.get("alt_vcf"))
    return f"{chrom}:g.{pos}{ref}>{alt}"


def resolve_myvariant_coordinate(variant: dict, timeout_seconds: int) -> dict:
    """Query MyVariant's GRCh38 coordinate scope and validate returned HGVS."""
    chrom = clean(variant.get("chrom_vcf")).removeprefix("chr")
    pos = clean(variant.get("pos_vcf"))
    params = urllib.parse.urlencode({
        "q": f"{chrom}:{pos}",
        "scopes": "dbsnp.hg38.position",
        "fields": "dbsnp,clinvar,cadd,gnomad,dbnsfp,_id,_score",
        "size": "20",
    })
    url = f"https://myvariant.info/v1/query?{params}"
    payload, error, http_status, headers = get_json(url, timeout_seconds)
    if error:
        return {"status": "source_error", "reason": "myvariant_coordinate_lookup_failed", "error": error, "http_status": http_status, "headers": headers, "url": url}
    hits = payload.get("hits") if isinstance(payload, dict) else []
    exact_ids: list[str] = []
    candidates: list[str] = []
    variant_hgvs = myvariant_coordinate_hgvs(variant).upper()
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        dbsnp = hit.get("dbsnp") or {}
        rsid = normalize_rsid(dbsnp.get("rsid") or hit.get("_id"))
        if rsid:
            candidates.append(rsid)
        hit_id = clean(hit.get("_id")).upper().replace(" ", "")
        if hit_id == variant_hgvs.replace(" ", "") and rsid:
            exact_ids.append(rsid)
    exact_ids = sorted(set(exact_ids))
    if len(exact_ids) == 1:
        return {
            "status": "resolved_coordinate_exact",
            "reason": "myvariant_exact_hgvs",
            "identity_match_class": "exact_coordinate_allele",
            "resolved_rsid": exact_ids[0],
            "candidate_rsids": sorted(set(candidates)),
            "payload": payload,
            "http_status": http_status,
            "headers": headers,
            "url": url,
        }
    return {
        "status": "coordinate_no_exact_allele",
        "reason": "myvariant_coordinate_without_exact_hgvs",
        "identity_match_class": "rsid_without_allele_confirmation" if candidates else "no_identity_match",
        "resolved_rsid": "",
        "candidate_rsids": sorted(set(candidates)),
        "payload": payload if isinstance(payload, dict) else {},
        "http_status": http_status,
        "headers": headers,
        "url": url,
    }


def fetch_clinvar_coordinate(variant: dict, timeout_seconds: int) -> dict:
    """Query ClinVar by GRCh38 position and retain evidence only on allele confirmation."""
    chrom = clean(variant.get("chrom_vcf")).removeprefix("chr")
    pos = clean(variant.get("pos_vcf"))
    ref = clean(variant.get("ref_vcf"))
    alt = clean(variant.get("alt_vcf"))
    term_text = f"{chrom}:{pos}[chrpos38] AND human[orgn]"
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode({
        "db": "clinvar", "retmode": "json", "retmax": "10", "tool": "heal_fon_service", "term": term_text,
    })
    search_payload, search_error, search_status, headers = get_json(search_url, timeout_seconds)
    if search_error:
        return {"status": "source_error", "reason": "clinvar_coordinate_esearch_failed", "error": search_error, "http_status": search_status}
    ids = []
    if isinstance(search_payload, dict):
        ids = ((search_payload.get("esearchresult") or {}).get("idlist") or [])
    if not ids:
        return {"status": "not_found", "reason": "clinvar_coordinate_no_position_records", "payload": {"count": "0", "coordinate_query": term_text}}
    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({
        "db": "clinvar", "retmode": "json", "tool": "heal_fon_service", "id": ",".join(ids[:5]),
    })
    summary_payload, summary_error, summary_status, _ = get_json(summary_url, timeout_seconds)
    if summary_error:
        return {"status": "source_error", "reason": "clinvar_coordinate_esummary_failed", "error": summary_error, "http_status": summary_status}
    summary_result = (summary_payload or {}).get("result") or {} if isinstance(summary_payload, dict) else {}
    items = [summary_result.get(str(item)) or {} for item in ids[:5]]
    allele_token = f"{ref}>{alt}".upper()
    exact_items = [item for item in items if allele_token in json.dumps(item, ensure_ascii=True).upper()]
    payload = {
        "count": str(len(exact_items)),
        "ids": "|".join(str(item) for item in ids[:5]),
        "coordinate_query": term_text,
        "clinical_significance": legacy.unique_join([legacy.clinvar_classification(item) for item in exact_items], limit=8, sep=" | "),
        "review_status": legacy.unique_join([legacy.clinvar_review_status(item) for item in exact_items], limit=8, sep=" | "),
        "trait_names": legacy.unique_join([legacy.clinvar_trait_name(item) for item in exact_items], limit=8),
        "coordinate_match": bool(exact_items),
        "esearch_raw_json": legacy.compact_json(search_payload),
        "esummary_raw_json": legacy.compact_json(summary_payload),
    }
    return {"status": "success" if exact_items else "not_found", "reason": "clinvar_coordinate_exact_allele" if exact_items else "clinvar_coordinate_allele_not_confirmed", "payload": payload}


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
    if not isinstance(payload, dict) or not payload:
        return "not_found"
    if source == "clinvar" and clean(payload.get("count")) in {"", "0"}:
        return "not_found"
    if source == "myvariant" and clean(payload.get("hits")) in {"", "0"}:
        return "not_found"
    if source == "gwas" and clean(payload.get("association_count")) in {"", "0"}:
        return "not_found"
    if source == "pharmgkb" and clean(payload.get("variant_id")) == "" and clean(payload.get("clinical_annotation_count")) in {"", "0"}:
        return "not_found"
    return "success"


def secondary_status_reason(source: str, payload: dict, error: str, status: str) -> str:
    if error:
        if "429" in error:
            return "rate_limited"
        if "timeout" in error.lower():
            return "timeout"
        return "provider_error"
    if status == "not_found":
        return "provider_returned_no_evidence"
    if source in {"ensembl_variation", "myvariant"} and not payload:
        return "empty_payload"
    return "usable_payload"


def cached_secondary(
    cache: EnrichmentCache,
    assembly: str,
    variant: dict,
    identifier: str,
    source: str,
    func,
    timeout_seconds: int,
    *,
    query_mode: str = "exact_rsid",
) -> tuple[dict, str, bool, str, str, float]:
    request = {"identifier": identifier, "source": source, "query_mode": query_mode, "pipeline": PIPELINE_VERSION}
    key = fingerprint(request)
    legacy_key = fingerprint({"rsid": identifier, "source": source, "pipeline": "gene-module-v2-enrichment-1"})
    started = time.perf_counter()
    cached = cache.get(
        assembly,
        variant["variant_key"],
        source,
        key,
        query_mode=query_mode,
        legacy_fingerprint=legacy_key,
    )
    if cached:
        cached_payload = cached["payload"] if isinstance(cached["payload"], dict) else {}
        return (
            cached_payload.get("data") or {},
            clean(cached_payload.get("error")),
            True,
            cached.get("status") or "success",
            cached.get("status_reason") or "cached_result",
            time.perf_counter() - started,
        )
    payload: dict = {}
    error = ""
    for attempt in range(1, 4):
        payload, error = func(identifier, timeout_seconds)
        if not error or not transient_provider_error(error):
            break
        if attempt < 3:
            time.sleep(retry_delay_seconds(attempt))
    status = secondary_source_status(source, payload, error)
    reason = secondary_status_reason(source, payload, error, status)
    cache.put(
        assembly,
        variant["variant_key"],
        source,
        key,
        {"data": payload, "error": error},
        status,
        None,
        query_mode=query_mode,
        identity_fingerprint=fingerprint({"assembly": assembly, "variant_key": variant["variant_key"]}),
        status_reason=reason,
    )
    return payload, error, False, status, reason, time.perf_counter() - started


def fetch_secondary_source(variant: dict, assembly: str, cache: EnrichmentCache, timeout_seconds: int, source: str, output_key: str, func) -> dict:
    rsid = clean(variant.get("resolved_rsid"))
    if not rsid:
        if source == "ensembl_variation" and variant.get("coordinate_identity_status"):
            status = variant.get("coordinate_identity_status")
            return {
                "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
                "payload": variant.get("coordinate_identity_payload") or {}, "error": "" if status != "source_error" else variant.get("coordinate_identity_reason", ""),
                "cache_hit": False, "status": "success" if status == "success" else "source_error" if status == "source_error" else "not_found",
                "status_reason": variant.get("coordinate_identity_reason", "coordinate_identity_lookup"),
                "query_mode": "coordinate", "elapsed_seconds": 0.0,
            }
        if source == "myvariant" and variant.get("myvariant_coordinate_status"):
            status = variant.get("myvariant_coordinate_status")
            return {
                "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
                "payload": variant.get("myvariant_coordinate_payload") or {}, "error": "" if status != "source_error" else variant.get("myvariant_coordinate_reason", ""),
                "cache_hit": False, "status": "success" if status == "success" else "source_error" if status == "source_error" else "not_found",
                "status_reason": variant.get("myvariant_coordinate_reason", "myvariant_coordinate_lookup"),
                "query_mode": "coordinate", "elapsed_seconds": 0.0,
            }
        if source == "clinvar" and variant.get("clinvar_coordinate_status"):
            status = variant.get("clinvar_coordinate_status")
            return {
                "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
                "payload": variant.get("clinvar_coordinate_payload") or {}, "error": "" if status != "source_error" else variant.get("clinvar_coordinate_reason", ""),
                "cache_hit": False, "status": status,
                "status_reason": variant.get("clinvar_coordinate_reason", "clinvar_coordinate_lookup"),
                "query_mode": "coordinate", "elapsed_seconds": 0.0,
            }
        return {
            "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
            "payload": {}, "error": "", "cache_hit": False, "status": "not_queried",
            "status_reason": "source_requires_confirmed_rsid", "query_mode": "coordinate_unavailable", "elapsed_seconds": 0.0,
        }
    payload, error, cache_hit, status, reason, elapsed = cached_secondary(
        cache, assembly, variant, rsid, source, func, timeout_seconds, query_mode="exact_rsid"
    )
    return {
        "variant_key": variant["variant_key"], "source": source, "output_key": output_key,
        "payload": payload, "error": error, "cache_hit": cache_hit, "status": status,
        "status_reason": reason, "query_mode": "exact_rsid",
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
            "errors": {},
            "source_status": {source: "not_queried" for source in SECONDARY_SOURCE_ORDER},
            "source_status_reason": {source: "source_requires_confirmed_rsid" for source in SECONDARY_SOURCE_ORDER},
            "source_query_mode": {source: "coordinate_unavailable" for source in SECONDARY_SOURCE_ORDER},
            "cache_hits": 0, "ensemblVariation": {}, "clinVar": {}, "myVariant": {}, "gwasCatalog": {}, "clinPgx": {},
        }
        for variant in variants
    }
    metrics = {
        "calls_total": len(variants) * len(calls), "calls_completed": 0, "cache_hits": 0,
        "source_stats": {
            source: {
                "workers": SOURCE_WORKERS[source], "completed": 0, "cache_hits": 0, "network_calls": 0,
                "errors": 0, "not_found": 0, "not_queried": 0, "elapsed_seconds": 0.0,
            }
            for source in calls
        },
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
                        "payload": {}, "error": str(error), "cache_hit": False, "status": "source_error",
                        "status_reason": "worker_error", "query_mode": "exact_rsid", "elapsed_seconds": 0.0,
                    }
                target = enrichments[result["variant_key"]]
                target[output_key] = result["payload"] or {}
                target["source_status"][source] = result["status"]
                target["source_status_reason"][source] = result.get("status_reason") or ""
                target["source_query_mode"][source] = result.get("query_mode") or ""
                target["cache_hits"] += int(result["cache_hit"])
                if result["error"]:
                    target["errors"][source] = result["error"]
                source_metrics = metrics["source_stats"][source]
                source_metrics["completed"] += 1
                source_metrics["cache_hits"] += int(result["cache_hit"])
                source_metrics["network_calls"] += int(
                    not result["cache_hit"] and result["status"] != "not_queried"
                )
                source_metrics["errors"] += int(result["status"] == "source_error")
                source_metrics["not_found"] += int(result["status"] == "not_found")
                source_metrics["not_queried"] += int(result["status"] == "not_queried")
                source_metrics["elapsed_seconds"] += float(result["elapsed_seconds"] or 0.0)
                metrics["calls_completed"] += 1
                metrics["cache_hits"] += int(result["cache_hit"])
                metrics["elapsed_seconds"] = round(time.perf_counter() - secondary_started, 3)
                metrics["calls_per_second"] = round(metrics["calls_completed"] / max(metrics["elapsed_seconds"], 0.001), 3)
                source_metrics["wall_seconds"] = round(time.perf_counter() - source_started, 3)
                if on_progress:
                    on_progress(metrics, source)
    return enrichments, metrics


def resolve_coordinate_variants(
    variants: list[dict],
    enrichments_by_variant: dict[str, dict],
    assembly: str,
    cache: EnrichmentCache,
    timeout_seconds: int,
    on_progress=None,
) -> dict:
    """Resolve rsIDs for VEP-unresolved variants using coordinate+allele overlap."""
    candidates = [variant for variant in variants if not clean(variant.get("resolved_rsid"))]
    metrics = {
        "total": len(candidates),
        "completed": 0,
        "cache_hits": 0,
        "network_calls": 0,
        "resolved": 0,
        "not_found": 0,
        "source_errors": 0,
        "elapsed_seconds": 0.0,
        "source": "ensembl_variation",
        "query_mode": "coordinate",
    }
    started = time.perf_counter()

    def resolve_one(variant: dict) -> dict:
        request = {
            "assembly": assembly,
            "variant_key": variant["variant_key"],
            "region": coordinate_region(variant),
            "ref": clean(variant.get("ref_vcf")),
            "alt": clean(variant.get("alt_vcf")),
            "pipeline": PIPELINE_VERSION,
        }
        key = fingerprint(request)
        cached = cache.get(assembly, variant["variant_key"], "ensembl_variation", key, query_mode="coordinate")
        if cached:
            return {"variant": variant, "resolution": (cached.get("payload") or {}).get("resolution") or {}, "cache_hit": True}
        resolution = resolve_coordinate_identity(variant, timeout_seconds)
        status = "source_error" if resolution.get("status") == "source_error" else "success" if resolution.get("resolved_rsid") else "not_found"
        reason = resolution.get("reason") or "coordinate_identity_lookup"
        cache.put(
            assembly,
            variant["variant_key"],
            "ensembl_variation",
            key,
            {"resolution": resolution},
            status,
            resolution.get("http_status"),
            query_mode="coordinate",
            identity_fingerprint=fingerprint({"assembly": assembly, "variant_key": variant["variant_key"]}),
            status_reason=reason,
        )
        return {"variant": variant, "resolution": resolution, "cache_hit": False}

    with ThreadPoolExecutor(max_workers=max(1, int(SOURCE_WORKERS["ensembl_variation"]))) as executor:
        futures = {executor.submit(resolve_one, variant): variant for variant in candidates}
        for future in as_completed(futures):
            variant = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "variant": variant,
                    "resolution": {"status": "source_error", "reason": "coordinate_worker_error", "error": str(error)},
                    "cache_hit": False,
                }
            resolution = result.get("resolution") or {}
            variant_key = variant["variant_key"]
            enrichment = enrichments_by_variant[variant_key]
            status = resolution.get("status") or "coordinate_no_exact_allele"
            if resolution.get("resolved_rsid"):
                variant["resolved_rsid"] = resolution["resolved_rsid"]
                enrichment["resolved_rsid"] = resolution["resolved_rsid"]
                enrichment["rsid_resolution_status"] = "coordinate_exact_allele"
                enrichment["resolution_reason"] = resolution.get("reason") or "exact_coordinate_allele"
                enrichment["identity_match_class"] = resolution.get("identity_match_class") or "exact_coordinate_allele"
                enrichment["candidate_rsid"] = "|".join(resolution.get("candidate_rsids") or [])
                metrics["resolved"] += 1
            else:
                enrichment["candidate_rsid"] = "|".join(resolution.get("candidate_rsids") or [])
                enrichment["identity_match_class"] = resolution.get("identity_match_class") or "no_identity_match"
                enrichment["identity_resolution_status"] = status
                enrichment["identity_resolution_reason"] = resolution.get("reason") or "coordinate_identity_lookup"
            variant["coordinate_identity_status"] = "source_error" if status == "source_error" else "success" if resolution.get("resolved_rsid") else "not_found"
            variant["coordinate_identity_reason"] = resolution.get("reason") or ""
            variant["coordinate_identity_payload"] = resolution
            enrichment["coordinate_identity_status"] = "source_error" if status == "source_error" else "success" if resolution.get("resolved_rsid") else "not_found"
            enrichment["coordinate_identity_reason"] = resolution.get("reason") or ""
            enrichment["coordinate_identity_query_mode"] = "coordinate"
            enrichment["ensemblVariation"] = {
                **(enrichment.get("ensemblVariation") or {}),
                "coordinate_candidate_rsids": enrichment.get("candidate_rsid", ""),
                "coordinate_identity_status": enrichment.get("coordinate_identity_status", ""),
                "coordinate_identity_reason": enrichment.get("coordinate_identity_reason", ""),
            }
            if status == "source_error":
                enrichment.setdefault("errors", {})["ensembl_variation_coordinate"] = resolution.get("error", "coordinate identity lookup failed")
                metrics["source_errors"] += 1
            elif not resolution.get("resolved_rsid"):
                metrics["not_found"] += 1
            metrics["cache_hits"] += int(result.get("cache_hit"))
            metrics["network_calls"] += int(
                not result.get("cache_hit") and result.get("status") != "not_queried"
            )
            metrics["completed"] += 1
            metrics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            metrics["calls_per_second"] = round(metrics["completed"] / max(metrics["elapsed_seconds"], 0.001), 3)
            if on_progress:
                on_progress(metrics)
    metrics["wall_seconds"] = round(time.perf_counter() - started, 3)
    return metrics


def resolve_myvariant_coordinate_variants(
    variants: list[dict],
    enrichments_by_variant: dict[str, dict],
    assembly: str,
    cache: EnrichmentCache,
    timeout_seconds: int,
    on_progress=None,
) -> dict:
    candidates = [variant for variant in variants if not clean(variant.get("resolved_rsid"))]
    metrics = {"total": len(candidates), "completed": 0, "cache_hits": 0, "network_calls": 0, "resolved": 0, "not_found": 0, "source_errors": 0, "elapsed_seconds": 0.0, "source": "myvariant", "query_mode": "coordinate"}
    started = time.perf_counter()

    def resolve_one(variant: dict) -> dict:
        request = {"assembly": assembly, "variant_key": variant["variant_key"], "query": myvariant_coordinate_hgvs(variant), "pipeline": PIPELINE_VERSION}
        key = fingerprint(request)
        cached = cache.get(assembly, variant["variant_key"], "myvariant", key, query_mode="coordinate")
        if cached:
            return {"variant": variant, "resolution": (cached.get("payload") or {}).get("resolution") or {}, "cache_hit": True}
        resolution = resolve_myvariant_coordinate(variant, timeout_seconds)
        status = "source_error" if resolution.get("status") == "source_error" else "success" if resolution.get("resolved_rsid") else "not_found"
        cache.put(
            assembly,
            variant["variant_key"],
            "myvariant",
            key,
            {"resolution": resolution},
            status,
            resolution.get("http_status"),
            query_mode="coordinate",
            identity_fingerprint=fingerprint({"assembly": assembly, "variant_key": variant["variant_key"]}),
            status_reason=resolution.get("reason", "myvariant_coordinate_lookup"),
        )
        return {"variant": variant, "resolution": resolution, "cache_hit": False}

    with ThreadPoolExecutor(max_workers=max(1, int(SOURCE_WORKERS["myvariant"]))) as executor:
        futures = {executor.submit(resolve_one, variant): variant for variant in candidates}
        for future in as_completed(futures):
            variant = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"variant": variant, "resolution": {"status": "source_error", "reason": "myvariant_coordinate_worker_error", "error": str(error)}, "cache_hit": False}
            resolution = result.get("resolution") or {}
            key = variant["variant_key"]
            enrichment = enrichments_by_variant[key]
            if resolution.get("resolved_rsid"):
                variant["resolved_rsid"] = resolution["resolved_rsid"]
                enrichment["resolved_rsid"] = resolution["resolved_rsid"]
                enrichment["rsid_resolution_status"] = "myvariant_coordinate_exact"
                enrichment["resolution_reason"] = resolution.get("reason", "myvariant_exact_hgvs")
                enrichment["identity_match_class"] = "exact_coordinate_allele"
                enrichment["candidate_rsid"] = "|".join(resolution.get("candidate_rsids") or [])
                metrics["resolved"] += 1
            else:
                enrichment["candidate_rsid"] = "|".join(sorted(set((enrichment.get("candidate_rsid", "").split("|") if enrichment.get("candidate_rsid") else []) + (resolution.get("candidate_rsids") or []))))
                if resolution.get("identity_match_class") == "rsid_without_allele_confirmation" and enrichment.get("identity_match_class") == "no_identity_match":
                    enrichment["identity_match_class"] = "rsid_without_allele_confirmation"
            status = resolution.get("status") or "coordinate_no_exact_allele"
            enrichment["myvariant_coordinate_status"] = "source_error" if status == "source_error" else "success" if resolution.get("resolved_rsid") else "not_found"
            enrichment["myvariant_coordinate_reason"] = resolution.get("reason", "")
            enrichment["myvariant_coordinate_payload"] = resolution.get("payload") or {}
            variant["myvariant_coordinate_status"] = enrichment["myvariant_coordinate_status"]
            variant["myvariant_coordinate_reason"] = enrichment["myvariant_coordinate_reason"]
            variant["myvariant_coordinate_payload"] = enrichment["myvariant_coordinate_payload"]
            if status == "source_error":
                enrichment.setdefault("errors", {})["myvariant_coordinate"] = resolution.get("error", "myvariant coordinate lookup failed")
                metrics["source_errors"] += 1
            elif not resolution.get("resolved_rsid"):
                metrics["not_found"] += 1
            metrics["cache_hits"] += int(result.get("cache_hit"))
            metrics["network_calls"] += int(
                not result.get("cache_hit") and result.get("status") != "not_queried"
            )
            metrics["completed"] += 1
            metrics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            metrics["calls_per_second"] = round(metrics["completed"] / max(metrics["elapsed_seconds"], 0.001), 3)
            if on_progress:
                on_progress(metrics)
    metrics["wall_seconds"] = round(time.perf_counter() - started, 3)
    return metrics


def fetch_clinvar_coordinate_variants(
    variants: list[dict],
    enrichments_by_variant: dict[str, dict],
    assembly: str,
    cache: EnrichmentCache,
    timeout_seconds: int,
    on_progress=None,
) -> dict:
    candidates = [variant for variant in variants if not clean(variant.get("resolved_rsid"))]
    metrics = {"total": len(candidates), "completed": 0, "cache_hits": 0, "network_calls": 0, "success": 0, "not_found": 0, "source_errors": 0, "elapsed_seconds": 0.0, "source": "clinvar", "query_mode": "coordinate"}
    started = time.perf_counter()

    def fetch_one(variant: dict) -> dict:
        request = {"assembly": assembly, "variant_key": variant["variant_key"], "query": f"{variant.get('chrom_vcf')}:{variant.get('pos_vcf')}:{variant.get('ref_vcf')}>{variant.get('alt_vcf')}", "pipeline": PIPELINE_VERSION}
        key = fingerprint(request)
        cached = cache.get(assembly, variant["variant_key"], "clinvar", key, query_mode="coordinate")
        if cached:
            return {"variant": variant, "result": (cached.get("payload") or {}).get("result") or {}, "cache_hit": True}
        result = fetch_clinvar_coordinate(variant, timeout_seconds)
        status = result.get("status") or "not_found"
        cache.put(
            assembly,
            variant["variant_key"],
            "clinvar",
            key,
            {"result": result},
            status,
            result.get("http_status"),
            query_mode="coordinate",
            identity_fingerprint=fingerprint({"assembly": assembly, "variant_key": variant["variant_key"]}),
            status_reason=result.get("reason", "clinvar_coordinate_lookup"),
        )
        return {"variant": variant, "result": result, "cache_hit": False}

    with ThreadPoolExecutor(max_workers=max(1, int(SOURCE_WORKERS["clinvar"]))) as executor:
        futures = {executor.submit(fetch_one, variant): variant for variant in candidates}
        for future in as_completed(futures):
            variant = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"variant": variant, "result": {"status": "source_error", "reason": "clinvar_coordinate_worker_error", "error": str(error)}, "cache_hit": False}
            item = result.get("result") or {}
            key = variant["variant_key"]
            enrichment = enrichments_by_variant[key]
            status = item.get("status") or "not_found"
            enrichment["clinvar_coordinate_status"] = status
            enrichment["clinvar_coordinate_reason"] = item.get("reason", "")
            enrichment["clinvar_coordinate_payload"] = item.get("payload") or {}
            variant["clinvar_coordinate_status"] = status
            variant["clinvar_coordinate_reason"] = item.get("reason", "")
            variant["clinvar_coordinate_payload"] = item.get("payload") or {}
            if status == "success":
                metrics["success"] += 1
            elif status == "source_error":
                metrics["source_errors"] += 1
                enrichment.setdefault("errors", {})["clinvar_coordinate"] = item.get("error", "clinvar coordinate lookup failed")
            else:
                metrics["not_found"] += 1
            metrics["cache_hits"] += int(result.get("cache_hit"))
            metrics["network_calls"] += int(
                not result.get("cache_hit") and result.get("status") != "not_queried"
            )
            metrics["completed"] += 1
            metrics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            if on_progress:
                on_progress(metrics)
    metrics["wall_seconds"] = round(time.perf_counter() - started, 3)
    return metrics


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
        "candidate_rsid": clean(enrichment.get("candidate_rsid")),
        "rsid_resolution_status": clean(enrichment.get("rsid_resolution_status")),
        "vep_status": clean(enrichment.get("vep_status")),
        "identity_match_class": clean(enrichment.get("identity_match_class")),
        "identity_resolution_status": clean(enrichment.get("identity_resolution_status")),
        "identity_resolution_reason": clean(enrichment.get("identity_resolution_reason")),
        "vep_target_gene_effect_status": clean(vep.get("status")),
        "vep_vrs": clean(vep.get("vrs")),
        "source_status_ensembl_vep": clean(statuses.get("ensembl_vep")),
        "source_status_ensembl_variation": clean(statuses.get("ensembl_variation")),
        "source_status_clinvar": clean(statuses.get("clinvar")),
        "source_status_myvariant": clean(statuses.get("myvariant")),
        "source_status_gwas": clean(statuses.get("gwas")),
        "source_status_pharmgkb": clean(statuses.get("pharmgkb")),
        **{
            key: value
            for source in SECONDARY_SOURCE_ORDER
            for key, value in (
                (f"source_status_reason_{source}", clean((enrichment.get("source_status_reason") or {}).get(source))),
                (f"source_query_mode_{source}", clean((enrichment.get("source_query_mode") or {}).get(source))),
            )
        },
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
        "candidate_rsid": enrichment.get("candidate_rsid", ""),
        "rsid_resolution_status": resolution_status,
        "resolution_reason": enrichment.get("resolution_reason", ""),
        "identity_match_class": enrichment.get("identity_match_class", ""),
        "identity_resolution_status": enrichment.get("identity_resolution_status", ""),
        "identity_resolution_reason": enrichment.get("identity_resolution_reason", ""),
        "coordinate_identity_status": enrichment.get("coordinate_identity_status", ""),
        "coordinate_identity_reason": enrichment.get("coordinate_identity_reason", ""),
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
        "source_status": json.dumps(enrichment.get("source_status") or {}, ensure_ascii=True, sort_keys=True),
        "source_status_reason": json.dumps(enrichment.get("source_status_reason") or {}, ensure_ascii=True, sort_keys=True),
        "source_query_mode": json.dumps(enrichment.get("source_query_mode") or {}, ensure_ascii=True, sort_keys=True),
    }


def build_physical_matrix_row(
    variant: dict,
    representative_row: dict,
    enrichment: dict,
    module_count: int,
) -> dict:
    """Build one compact, source-oriented row per normalized physical variant."""
    view = legacy.build_plus_output_row_v2(
        {
            **representative_row,
            "variant_key": variant["variant_key"],
            "assembly_name": variant["assembly"],
            "chrom_vcf": variant["chrom_vcf"],
            "pos_vcf": variant["pos_vcf"],
            "ref_vcf": variant["ref_vcf"],
            "alt_vcf": variant["alt_vcf"],
            "id_vcf": variant.get("id_vcf", ""),
        },
        enrichment,
    )
    row = {
        "variant_key": variant["variant_key"],
        "assembly": variant["assembly"],
        "chrom_vcf": variant["chrom_vcf"],
        "pos_vcf": variant["pos_vcf"],
        "ref_vcf": variant["ref_vcf"],
        "alt_vcf": variant["alt_vcf"],
        "id_vcf": variant.get("id_vcf", ""),
        "resolved_rsid": enrichment.get("resolved_rsid", ""),
        "candidate_rsid": enrichment.get("candidate_rsid", ""),
        "rsid_resolution_status": clean(enrichment.get("rsid_resolution_status")),
        "resolution_reason": clean(enrichment.get("resolution_reason")),
        "identity_match_class": clean(enrichment.get("identity_match_class")),
        "identity_resolution_status": clean(enrichment.get("identity_resolution_status")),
        "identity_resolution_reason": clean(enrichment.get("identity_resolution_reason")),
        "module_row_count": module_count,
        "secondary_query_eligible": "true" if clean(enrichment.get("resolved_rsid")) else "false",
        "secondary_query_mode": "exact_rsid" if clean(enrichment.get("resolved_rsid")) else "coordinate_unavailable",
        "coordinate_identity_status": clean(enrichment.get("coordinate_identity_status")),
        "coordinate_identity_reason": clean(enrichment.get("coordinate_identity_reason")),
        "vep_status": clean(enrichment.get("vep_status")),
        "source_error_sources": "|".join(sorted((enrichment.get("errors") or {}).keys())),
    }
    statuses = enrichment.get("source_status") or {}
    for source in SECONDARY_SOURCE_ORDER:
        row[f"source_status_{source}"] = clean(statuses.get(source) or "not_queried")
        row[f"source_status_reason_{source}"] = clean((enrichment.get("source_status_reason") or {}).get(source))
        row[f"source_query_mode_{source}"] = clean((enrichment.get("source_query_mode") or {}).get(source))

    # Keep biological summaries while excluding raw payloads and module-specific fields.
    prefixes = (
        "ensembl_",
        "vep_",
        "clinvar_",
        "myvariant_",
        "gwas_",
        "pharmgkb_",
        "population_",
        "external_",
        "interpretation_",
    )
    for key, value in view.items():
        if not key.startswith(prefixes) or key.endswith("_json") or key.endswith("_raw_json"):
            continue
        row[key] = clean(value)
    return row


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


def build_physical_evidence_row(variant: dict, enrichment: dict, vep_item: dict, module_count: int) -> dict:
    """Compact, one-row-per-physical-variant evidence audit."""
    vep = enrichment.get("ensemblVep") or {}
    return {
        "audit_schema_version": "v2_physical_1",
        "variant_key": variant["variant_key"],
        "assembly": variant["assembly"],
        "chrom_vcf": variant["chrom_vcf"],
        "pos_vcf": variant["pos_vcf"],
        "ref_vcf": variant["ref_vcf"],
        "alt_vcf": variant["alt_vcf"],
        "id_vcf": variant.get("id_vcf", ""),
        "module_row_count": module_count,
        "resolved_rsid": enrichment.get("resolved_rsid", ""),
        "candidate_rsid": enrichment.get("candidate_rsid", ""),
        "rsid_resolution_status": enrichment.get("rsid_resolution_status", ""),
        "identity_match_class": enrichment.get("identity_match_class", ""),
        "identity_resolution_status": enrichment.get("identity_resolution_status", ""),
        "identity_resolution_reason": enrichment.get("identity_resolution_reason", ""),
        "vep_status": enrichment.get("vep_status", ""),
        "vep_most_severe_consequence": vep.get("most_severe_consequence", ""),
        "vep_gene_symbols": vep.get("gene_symbols", ""),
        "vep_hgvsc": vep.get("picked_hgvsc", ""),
        "vep_hgvsp": vep.get("picked_hgvsp", ""),
        "vep_cadd_phred": vep.get("picked_cadd_phred", ""),
        "vep_revel_score": vep.get("picked_revel_score", ""),
        "vep_spliceai": vep.get("picked_spliceai", ""),
        "vep_raw_available": "true" if vep_item else "false",
        "source_status": enrichment.get("source_status") or {},
        "source_status_reason": enrichment.get("source_status_reason") or {},
        "source_query_mode": enrichment.get("source_query_mode") or {},
        "source_errors": enrichment.get("errors") or {},
        "secondary_evidence_usable": any(
            (enrichment.get("source_status") or {}).get(source) == "success"
            for source in SECONDARY_SOURCE_ORDER
        ),
    }


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
    cache_path = Path(payload.get("cachePath") or cache_dir / "enrichment_cache_v2.sqlite")
    legacy_cache_path = Path(payload.get("legacyCachePath") or cache_path.with_name("enrichment_cache.sqlite"))
    cache = EnrichmentCache(
        cache_path,
        int(payload.get("cacheTtlDays") or DEFAULT_CACHE_TTL_DAYS),
        legacy_path=legacy_cache_path,
    )
    atexit.register(cache.close)
    analysis_mode = clean(payload.get("analysisMode") or payload.get("analysis_mode") or "quick").lower()
    if analysis_mode not in {"quick", "complete", "qa"}:
        analysis_mode = "quick"
    input_sha256 = sha256_file(input_path)
    coordinate_identity_enabled = analysis_mode in {"complete", "qa"}
    rows = [row for row in read_csv(input_path) if clean(row.get("variant_key")) and clean(row.get("has_genotype")).lower() in {"true", "1", "yes"}]
    if not rows:
        raise ValueError("AI triage input contains no observed v2 physical variants.")
    write_progress(output_dir, substage="deduplicating_physical_variants", total=len(rows), unit="module rows", message="Building unique physical variant set")

    variants: dict[str, dict] = {}
    representative_rows: dict[str, dict] = {}
    module_counts: dict[str, int] = {}
    for row in rows:
        variant_key = clean(row.get("variant_key")) or stable_variant_key(assembly, normalize_chromosome(row.get("chrom_vcf")), clean(row.get("pos_vcf")), clean(row.get("ref_vcf")), clean(row.get("alt_vcf")))
        if variant_key not in variants:
            representative_rows[variant_key] = row
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
        response, cache_hits, requests, batch_warnings = fetch_vep_adaptive(
            physical_variants[offset : offset + VEP_BATCH_SIZE],
            assembly,
            cache,
            timeout_seconds,
            provenance,
        )
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
        write_resume_manifest(
            output_dir, input_sha256=input_sha256, assembly=assembly, phase="vep_base",
            processed=min(offset + VEP_BATCH_SIZE, len(physical_variants)), total=len(physical_variants),
            metrics={"cache_hits": vep_cache_hits, "network_variants": vep_requests},
        )
    vep_elapsed_seconds = time.perf_counter() - vep_started

    enrichments_by_variant: dict[str, dict] = {}
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
            "candidate_rsid": "|".join(sorted({normalize_rsid(entry.get("id")) for entry in item.get("colocated_variants") or [] if isinstance(entry, dict) and normalize_rsid(entry.get("id"))})),
            "rsid_resolution_status": rsid_status,
            "resolution_reason": resolution["reason"],
            "identity_match_class": "exact_coordinate_allele" if rsid else "ambiguous_identity" if rsid_status in {"vep_colocated_allele_mismatch", "ambiguous_multiple_exact_rsids"} else "no_identity_match",
            "identity_resolution_status": rsid_status,
            "identity_resolution_reason": resolution["reason"],
            "vep_colocated_count": resolution["colocated_count"],
            "vep_colocated_rsid_count": resolution["colocated_rsid_count"],
            "vep_status": clean(raw.get("status")) or "not_found",
            "errors": {"ensembl_vep": clean(raw.get("error"))} if clean(raw.get("error")) else {},
            "source_status": {
                "ensembl_vep": clean(raw.get("status")) or "not_found",
                **{source: "not_queried" for source in SECONDARY_SOURCE_ORDER},
            },
            "source_status_reason": {
                "ensembl_vep": "vep_response" if clean(raw.get("status")) == "success" else "vep_request_error",
                **{source: "pending_identity_resolution" for source in SECONDARY_SOURCE_ORDER},
            },
            "source_query_mode": {source: "pending_identity_resolution" for source in SECONDARY_SOURCE_ORDER},
            "ensemblVep": parse_vep_for_gene(item, ""),
            "cacheHit": bool(raw.get("cache_hit")),
        }
        enrichments_by_variant[variant["variant_key"]] = enrichment

    write_progress(
        output_dir,
        stage="enrichment_identity",
        phase="identity_resolution",
        substage="coordinate_overlap",
        processed=0,
        total=sum(1 for variant in physical_variants if not clean(variant.get("resolved_rsid"))),
        unit="physical variants",
        message="Resolving unresolved identities by GRCh coordinates and alleles",
    )

    def on_identity_progress(metrics: dict) -> None:
        write_progress(
            output_dir,
            stage="enrichment_identity",
            phase="identity_resolution",
            substage="coordinate_overlap",
            processed=int(metrics.get("completed") or 0),
            total=int(metrics.get("total") or 0),
            unit="physical variants",
            message="Resolving unresolved identities by Ensembl variation overlap",
            metrics=metrics,
        )

    if coordinate_identity_enabled:
        identity_metrics = resolve_coordinate_variants(
            physical_variants,
            enrichments_by_variant,
            assembly,
            cache,
            timeout_seconds,
            on_progress=on_identity_progress,
        )
        myvariant_identity_metrics = resolve_myvariant_coordinate_variants(
            physical_variants,
            enrichments_by_variant,
            assembly,
            cache,
            timeout_seconds,
            on_progress=on_identity_progress,
        )
        clinvar_coordinate_metrics = fetch_clinvar_coordinate_variants(
            physical_variants,
            enrichments_by_variant,
            assembly,
            cache,
            timeout_seconds,
            on_progress=on_identity_progress,
        )
    else:
        skipped_metrics = {
            "total": 0,
            "completed": 0,
            "cache_hits": 0,
            "network_calls": 0,
            "resolved": 0,
            "not_found": 0,
            "source_errors": 0,
            "elapsed_seconds": 0.0,
            "skipped": True,
            "skip_reason": "coordinate_identity_resolution_disabled_for_quick_analysis",
            "query_mode": "coordinate",
        }
        identity_metrics = {**skipped_metrics, "source": "ensembl_variation"}
        myvariant_identity_metrics = {**skipped_metrics, "source": "myvariant"}
        clinvar_coordinate_metrics = {**skipped_metrics, "source": "clinvar"}
        write_progress(
            output_dir,
            stage="enrichment_identity",
            phase="identity_resolution",
            substage="skipped_quick_analysis",
            processed=0,
            total=0,
            unit="physical variants",
            message="Coordinate/HGVS identity rescue skipped for superficial analysis",
            metrics={
                "analysisMode": analysis_mode,
                "coordinateIdentityEnabled": False,
                "reason": "quick_analysis_keeps_vep_and_exact_rsid_enrichment_only",
            },
        )
    resolution_counts["coordinate_exact_allele"] = int(identity_metrics.get("resolved") or 0) + int(myvariant_identity_metrics.get("resolved") or 0)

    base_rows, base_evidence_rows = materialize_module_rows(rows, variants, enrichments_by_variant, vep_raw)
    base_csv = output_dir / "v2_enrichment_vep_base.csv"
    resolution_audit_path = output_dir / "v2_enrichment_resolution_audit.jsonl"
    write_csv(base_csv, base_rows, source_fields(base_rows))
    with resolution_audit_path.open("w", encoding="utf-8") as handle:
        for variant in physical_variants:
            enrichment = enrichments_by_variant[variant["variant_key"]]
            handle.write(json.dumps({
                "variant_key": variant["variant_key"],
                "resolved_rsid": enrichment.get("resolved_rsid", ""),
                "candidate_rsid": enrichment.get("candidate_rsid", ""),
                "rsid_resolution_status": enrichment.get("rsid_resolution_status", ""),
                "identity_match_class": enrichment.get("identity_match_class", ""),
                "identity_resolution_status": enrichment.get("identity_resolution_status", ""),
                "identity_resolution_reason": enrichment.get("identity_resolution_reason", ""),
                "coordinate_identity_status": enrichment.get("coordinate_identity_status", ""),
                "coordinate_identity_reason": enrichment.get("coordinate_identity_reason", ""),
            }, ensure_ascii=True) + "\n")
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
        total=len(physical_variants) * len(SECONDARY_SOURCE_ORDER),
        unit="source calls",
        message="Querying secondary sources with confirmed identities",
        metrics={"eligibleVariants": len(physical_variants), "resolutionCounts": resolution_counts, "identity": identity_metrics, "myvariantIdentity": myvariant_identity_metrics, "clinvarCoordinate": clinvar_coordinate_metrics},
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
            write_resume_manifest(
                output_dir, input_sha256=input_sha256, assembly=assembly, phase=f"secondary_{source}",
                processed=completed, total=total, metrics={"source_stats": metrics.get("source_stats") or {}},
            )

    secondary_started = time.perf_counter()
    secondary_enrichments, secondary_metrics = fetch_secondary_sources(
        physical_variants,
        assembly,
        cache,
        timeout_seconds,
        on_progress=on_secondary_progress,
    )
    secondary_metrics["wall_seconds"] = round(time.perf_counter() - secondary_started, 3)
    for variant_key, secondary in secondary_enrichments.items():
        enrichment = enrichments_by_variant[variant_key]
        enrichment.update({key: value for key, value in secondary.items() if key not in {"errors", "source_status", "source_status_reason", "source_query_mode"}})
        enrichment["errors"].update(secondary.get("errors") or {})
        enrichment["source_status"].update(secondary.get("source_status") or {})
        enrichment["source_status_reason"].update(secondary.get("source_status_reason") or {})
        enrichment["source_query_mode"].update(secondary.get("source_query_mode") or {})

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
        total=sum(1 for variant in physical_variants if not clean(variant.get("resolved_rsid"))),
        unit="physical variants",
        message="Auditing VEP-only, ambiguous and VEP-error variants",
        metrics={"secondary": secondary_metrics, "identity": identity_metrics, "resolutionCounts": resolution_counts},
    )

    output_rows, evidence_rows = materialize_module_rows(rows, variants, enrichments_by_variant, vep_raw)

    master_rows = [build_variant_master_row(variant, enrichments_by_variant[variant["variant_key"]], module_counts[variant["variant_key"]]) for variant in physical_variants]
    vep_only_master_rows = [row for row in master_rows if not clean(row.get("resolved_rsid"))]
    vep_only_csv = output_dir / "v2_enrichment_vep_only_audit.csv"
    write_csv(vep_only_csv, vep_only_master_rows, source_fields(master_rows))
    physical_matrix_rows = [
        build_physical_matrix_row(
            variant,
            representative_rows[variant["variant_key"]],
            enrichments_by_variant[variant["variant_key"]],
            module_counts[variant["variant_key"]],
        )
        for variant in physical_variants
    ]
    physical_matrix_csv = output_dir / "v2_enrichment_physical_matrix.csv"
    write_csv(physical_matrix_csv, physical_matrix_rows, source_fields(physical_matrix_rows))
    write_progress(
        output_dir,
        stage="enrichment_vep_only",
        phase="vep_only_remediation",
        substage="complete",
        processed=len(vep_only_master_rows),
        total=len(vep_only_master_rows),
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
    physical_evidence_path = output_dir / "v2_enrichment_physical_evidence_audit.jsonl.gz"
    module_projection_path = output_dir / "v2_enrichment_module_projection.csv"
    write_csv(output_csv, output_rows, fields)
    write_csv(observed_csv, output_rows, fields)
    write_csv(plus_csv, output_rows, fields)
    write_csv(master_csv, master_rows, master_fields)
    with evidence_path.open("w", encoding="utf-8") as handle:
        for item in output_rows:
            handle.write(json.dumps({
                "variant_key": item.get("variant_key", ""),
                "gene": item.get("approved_symbol", ""),
                "module_id": item.get("module_id", ""),
                "resolved_rsid": item.get("resolved_rsid", ""),
                "identity_match_class": item.get("identity_match_class", ""),
                "source_status": {source: item.get(f"source_status_{source}", "") for source in SECONDARY_SOURCE_ORDER},
                "source_status_reason": {source: item.get(f"source_status_reason_{source}", "") for source in SECONDARY_SOURCE_ORDER},
            }, ensure_ascii=True) + "\n")
    projection_rows = [
        {key: value for key, value in row.items() if not key.endswith("_raw_json") and key not in {"raw_json", "vep_raw"}}
        for row in output_rows
    ]
    write_csv(module_projection_path, projection_rows, source_fields(projection_rows))
    with gzip.open(physical_evidence_path, "wt", encoding="utf-8") as handle:
        for variant in physical_variants:
            key = variant["variant_key"]
            handle.write(json.dumps(
                build_physical_evidence_row(
                    variant,
                    enrichments_by_variant[key],
                    (vep_raw.get(key) or {}).get("item") or {},
                    module_counts[key],
                ),
                ensure_ascii=True,
            ) + "\n")

    retry_queue_path = output_dir / "enrichment_retry_queue.jsonl"
    with retry_queue_path.open("w", encoding="utf-8") as handle:
        for variant in physical_variants:
            key = variant["variant_key"]
            enrichment = enrichments_by_variant[key]
            for source in SECONDARY_SOURCE_ORDER:
                status = (enrichment.get("source_status") or {}).get(source)
                if status == "source_error":
                    handle.write(json.dumps({
                        "variant_key": key,
                        "source": source,
                        "query_mode": (enrichment.get("source_query_mode") or {}).get(source, ""),
                        "reason": (enrichment.get("source_status_reason") or {}).get(source, "source_error"),
                        "priority": "high" if source == "clinvar" else "normal",
                    }, ensure_ascii=True) + "\n")
            if enrichment.get("coordinate_identity_status") == "source_error":
                handle.write(json.dumps({
                    "variant_key": key,
                    "source": "ensembl_variation",
                    "query_mode": "coordinate",
                    "reason": enrichment.get("coordinate_identity_reason", "coordinate_identity_error"),
                    "priority": "high",
                }, ensure_ascii=True) + "\n")

    identity_summary = {
        "schemaVersion": "gene_module_v2",
        "analysisMode": analysis_mode,
        "coordinateIdentityEnabled": coordinate_identity_enabled,
        "physicalVariants": len(physical_variants),
        "vepResolved": sum(1 for value in enrichments_by_variant.values() if clean(value.get("rsid_resolution_status", "")).startswith("vep_")),
        "coordinateResolved": int(identity_metrics.get("resolved") or 0),
        "ambiguous": sum(1 for value in enrichments_by_variant.values() if value.get("identity_match_class") == "ambiguous_identity"),
        "candidateWithoutConfirmation": sum(1 for value in enrichments_by_variant.values() if value.get("identity_match_class") == "rsid_without_allele_confirmation"),
        "crossAssembly": sum(1 for value in enrichments_by_variant.values() if value.get("identity_match_class") == "cross_assembly_match"),
        "noIdentityMatch": sum(1 for value in enrichments_by_variant.values() if value.get("identity_match_class") == "no_identity_match"),
        "coordinateMetrics": {
            "ensembl": identity_metrics,
            "myvariant": myvariant_identity_metrics,
            "clinvar": clinvar_coordinate_metrics,
        },
        "outputs": {"physicalEvidenceAuditJsonlGz": str(physical_evidence_path), "retryQueueJsonl": str(retry_queue_path)},
    }
    identity_summary_path = output_dir / "enrichment_identity_resolution_summary.json"
    write_json(identity_summary_path, identity_summary)
    write_resume_manifest(
        output_dir, input_sha256=input_sha256, assembly=assembly, phase="complete",
        processed=len(physical_variants), total=len(physical_variants),
        metrics={"retry_queue": str(retry_queue_path), "source_errors": sum(len(value.get("errors") or {}) for value in enrichments_by_variant.values())},
    )

    performance = {
        "schemaVersion": "gene_module_v2",
        "analysisMode": analysis_mode,
        "coordinateIdentityEnabled": coordinate_identity_enabled,
        "startedAt": started_at,
        "completedAt": utc_now(),
        "elapsedSeconds": time.perf_counter() - process_started,
        "physicalVariants": len(physical_variants),
        "moduleRows": len(rows),
        "vepSeconds": vep_elapsed_seconds,
        "identity": {
            "ensembl": identity_metrics,
            "myvariant": myvariant_identity_metrics,
            "clinvar": clinvar_coordinate_metrics,
        },
        "cachePath": str(cache_path),
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
    minimum_vep_coverage = configured_min_vep_coverage()
    matrix_keys = {row.get("variant_key") for row in physical_matrix_rows if row.get("variant_key")}
    physical_keys = {variant["variant_key"] for variant in physical_variants}
    status_complete = all(
        all((enrichments_by_variant[key].get("source_status") or {}).get(source) in {"success", "not_found", "source_error", "not_queried"} for source in SECONDARY_SOURCE_ORDER)
        for key in physical_keys
    )
    technical_gate = {
        "status": "pass" if len(matrix_keys) == len(physical_keys) == len(physical_matrix_rows) and status_complete else "fail",
        "physicalMatrixRows": len(physical_matrix_rows),
        "physicalVariantKeys": len(physical_keys),
        "matrixUniqueVariantKeys": len(matrix_keys),
        "sourceStatusesComplete": status_complete,
        "targetLeakageRows": int((normalization_summary.get("qualityGate") or {}).get("targetLeakageRows") or 0),
    }
    unresolved_identity_count = sum(
        1 for value in enrichments_by_variant.values()
        if not clean(value.get("resolved_rsid")) and value.get("identity_match_class") in {"ambiguous_identity", "rsid_without_allele_confirmation", "no_identity_match"}
    )
    retry_debt = sum(
        1 for value in enrichments_by_variant.values()
        for source in SECONDARY_SOURCE_ORDER
        if (value.get("source_status") or {}).get(source) == "source_error"
    )
    evidence_readiness_gate = {
        "status": "pass" if technical_gate["status"] == "pass" and normalization_rate >= 0.99 and vep_coverage >= minimum_vep_coverage and unresolved_identity_count == 0 and retry_debt == 0 else "fail",
        "normalizationRetentionPassed": normalization_rate >= 0.99,
        "vepCoveragePassed": vep_coverage >= minimum_vep_coverage,
        "unresolvedIdentityCount": unresolved_identity_count,
        "retryDebt": retry_debt,
        "blockingReasons": [
            reason for reason, condition in [
                ("technical_gate_failed", technical_gate["status"] != "pass"),
                ("normalization_retention_below_threshold", normalization_rate < 0.99),
                ("vep_coverage_below_threshold", vep_coverage < minimum_vep_coverage),
                ("unresolved_identity", unresolved_identity_count > 0),
                ("source_retry_debt", retry_debt > 0),
            ] if condition
        ],
    }
    gate_status = technical_gate["status"]
    quality = {
        "schemaVersion": "gene_module_v2", "analysisMode": analysis_mode,
        "coordinateIdentityEnabled": coordinate_identity_enabled,
        "status": gate_status, "createdAt": utc_now(),
        "normalizationValidRate": normalization_rate, "minimumNormalizationValidRate": 0.99,
        "physicalVariants": len(physical_variants), "moduleRows": len(rows),
        "vepSuccessfulVariants": vep_success_count, "vepCoverage": vep_coverage, "minimumVepCoverage": minimum_vep_coverage,
        "exactRsidsResolved": sum(1 for value in enrichments_by_variant.values() if clean(value.get("resolved_rsid"))),
        "resolutionCounts": resolution_counts,
        "vepOnlyVariants": len(vep_only_master_rows),
        "identityUnresolvedVariants": unresolved_identity_count,
        "secondaryMetrics": secondary_metrics,
        "identityMetrics": {
            "ensembl": identity_metrics,
            "myvariant": myvariant_identity_metrics,
            "clinvar": clinvar_coordinate_metrics,
        },
        "vepCacheHits": vep_cache_hits, "vepNetworkVariants": vep_requests,
        "sourceErrors": source_errors, "warnings": warnings,
        "technicalGate": technical_gate,
        "evidenceReadinessGate": evidence_readiness_gate,
        "evidenceReady": evidence_readiness_gate["status"] == "pass",
        "decision": "pass" if evidence_readiness_gate["status"] == "pass" else "technical_pass_evidence_not_ready" if gate_status == "pass" else "block_downstream_until_enrichment_is_remediated",
        "provenance": provenance,
        "reference": normalization_summary.get("reference") or {},
    }
    quality_path = output_dir / "enrichment_quality_summary.json"
    write_json(quality_path, quality)
    summary = {
        "status": "valid", "schemaVersion": "gene_module_v2", "adapter": "gene_module_coordinate_enrichment",
        "analysisMode": analysis_mode,
        "startedAt": started_at, "completedAt": utc_now(), "inputPath": str(input_path),
        "observedVariantEnrichmentCsv": str(output_csv), "observedVariantEnrichmentColabCsv": str(observed_csv),
        "observedVariantEnrichmentPlusCsv": str(plus_csv), "v2EnrichmentVariantMasterCsv": str(master_csv),
        "v2EnrichmentEvidenceAuditJsonl": str(evidence_path), "enrichmentQualitySummaryJson": str(quality_path),
        "v2EnrichmentVepBaseCsv": str(base_csv), "v2EnrichmentCompleteCsv": str(complete_csv),
        "v2EnrichmentVepOnlyAuditCsv": str(vep_only_csv), "v2EnrichmentResolutionAuditJsonl": str(resolution_audit_path),
        "v2EnrichmentPhysicalMatrixCsv": str(physical_matrix_csv),
        "v2EnrichmentPhysicalEvidenceAuditJsonlGz": str(physical_evidence_path),
        "v2EnrichmentModuleProjectionCsv": str(module_projection_path),
        "enrichmentRetryQueueJsonl": str(retry_queue_path),
        "enrichmentIdentityResolutionSummaryJson": str(identity_summary_path),
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
