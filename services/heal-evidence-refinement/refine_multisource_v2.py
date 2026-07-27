"""Curate v2 public evidence without dropping observed variants.

This stage consumes the deterministic extraction/match/enrichment artifacts. It
selectively expands ClinVar, ClinPGx and GWAS records while retaining every
matched physical variant and gene-module projection in compact audit tables.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable


PIPELINE_VERSION = "gene-module-v2-evidence-refinement-1"
VALID_SOURCE_STATUSES = {"success", "not_found", "source_error", "not_queried"}
NON_BENIGN_CLINVAR_CLASSES = {
    "pathogenic_or_likely_pathogenic",
    "uncertain_significance",
    "conflicting_pathogenicity",
    "risk_factor",
    "drug_response",
    "other_or_association",
}
CLINPGX_ATTRIBUTION = "ClinPGx/PharmGKB"
CLINPGX_LICENSE = "CC BY-SA 4.0"
GWAS_BASE_URL = "https://www.ebi.ac.uk/gwas/rest/api/v2"
CLINPGX_BASE_URL = "https://api.pharmgkb.org/v1"
EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_bool(value: object) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(clean(value)))
    except (TypeError, ValueError):
        return default


def as_float(value: object) -> float | None:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return None


def normalize_chromosome(value: object) -> str:
    text = clean(value).replace("chr", "", 1).upper()
    if text in {"MT", "M"}:
        text = "M"
    return f"chr{text}" if text else ""


def physical_evidence_id(variant_key: str) -> str:
    digest = hashlib.sha256(f"physical:{variant_key}".encode("utf-8")).hexdigest()[:24]
    return f"pev_{digest}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    handle = (
        gzip.open(path, "rt", encoding="utf-8-sig", newline="")
        if path.suffix.lower() == ".gz"
        else path.open("r", encoding="utf-8-sig", newline="")
    )
    with handle:
        return [dict(row) for row in csv.DictReader(handle)]


def field_order(rows: Iterable[dict]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    return fields


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fields or field_order(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(temporary.read_text(encoding="utf-8"), encoding="utf-8")
        temporary.unlink(missing_ok=True)


def write_progress(
    output_dir: Path,
    *,
    substage: str,
    processed: int,
    total: int,
    unit: str,
    message: str,
    metrics: dict | None = None,
) -> None:
    write_json(
        output_dir / "evidence_refinement_progress.json",
        {
            "stage": "evidence_refinement",
            "phase": "multisource_curation",
            "substage": substage,
            "processed": processed,
            "total": max(total, 1),
            "unit": unit,
            "message": message,
            "metrics": metrics or {},
            "updatedAt": utc_now(),
        },
    )


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join(part.strip() for part in element.itertext() if part and part.strip())


def iter_local(element: ET.Element, name: str) -> Iterable[ET.Element]:
    for child in element.iter():
        if local_name(child.tag) == name:
            yield child


def unique_text(values: Iterable[object], separator: str = " | ") -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean(value)
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return separator.join(output)


def json_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def recursive_values(value: object, key_names: set[str]) -> list[object]:
    matches: list[object] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in key_names:
                matches.append(item)
            matches.extend(recursive_values(item, key_names))
    elif isinstance(value, list):
        for item in value:
            matches.extend(recursive_values(item, key_names))
    return matches


class RefinementCache:
    """Public-source cache. Request fingerprints never include patient facts."""

    def __init__(self, path: Path, ttl_days: int = 30):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.ttl_days = ttl_days
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_refinement_cache (
                source TEXT NOT NULL,
                query_mode TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                request_url TEXT NOT NULL,
                response_text TEXT NOT NULL,
                status TEXT NOT NULL,
                status_reason TEXT NOT NULL,
                http_status INTEGER,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                PRIMARY KEY (source, query_mode, request_fingerprint)
            )
            """
        )
        self.connection.commit()

    @staticmethod
    def fingerprint(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get(self, source: str, query_mode: str, url: str) -> dict | None:
        fingerprint = self.fingerprint(url)
        row = self.connection.execute(
            """
            SELECT response_text, status, status_reason, http_status, expires_at
            FROM evidence_refinement_cache
            WHERE source = ? AND query_mode = ? AND request_fingerprint = ?
            """,
            (source, query_mode, fingerprint),
        ).fetchone()
        if not row:
            return None
        response_text, status, status_reason, http_status, expires_at = row
        if status == "source_error" or datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
            return None
        return {
            "text": response_text,
            "status": status,
            "status_reason": status_reason,
            "http_status": http_status,
            "cache_hit": True,
        }

    def put(
        self,
        source: str,
        query_mode: str,
        url: str,
        response_text: str,
        status: str,
        status_reason: str,
        http_status: int | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        ttl = timedelta(hours=1) if status == "source_error" else timedelta(days=self.ttl_days)
        self.connection.execute(
            """
            INSERT OR REPLACE INTO evidence_refinement_cache
            (source, query_mode, request_fingerprint, request_url, response_text, status,
             status_reason, http_status, fetched_at, expires_at, pipeline_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                query_mode,
                self.fingerprint(url),
                url,
                response_text,
                status,
                status_reason,
                http_status,
                now.isoformat(),
                (now + ttl).isoformat(),
                PIPELINE_VERSION,
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class PublicHttpClient:
    def __init__(self, cache: RefinementCache, timeout_seconds: int = 30):
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.last_request: dict[str, float] = {}
        self.cache_hits = Counter()
        self.network_calls = Counter()
        self.retries = Counter()
        self.consecutive_failures = Counter()
        self.open_circuits: set[str] = set()
        self.api_key = clean(os.environ.get("HEAL_NCBI_API_KEY") or os.environ.get("NCBI_API_KEY"))

    def _delay(self, source: str) -> None:
        delay = {
            "ncbi": 0.11 if self.api_key else 0.36,
            "clinpgx": 0.55,
            "gwas": 0.08,
            "pmc": 0.36,
        }.get(source, 0.1)
        elapsed = time.monotonic() - self.last_request.get(source, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request[source] = time.monotonic()

    def request(self, source: str, query_mode: str, url: str, *, retries: int = 3) -> dict:
        cache_url = url
        request_url = url
        if source in {"ncbi", "pmc"} and self.api_key:
            separator = "&" if "?" in url else "?"
            request_url = f"{url}{separator}api_key={urllib.parse.quote(self.api_key)}"
        cached = self.cache.get(source, query_mode, cache_url)
        if cached:
            self.cache_hits[source] += 1
            return cached
        if source in self.open_circuits:
            return {
                "text": "",
                "status": "source_error",
                "status_reason": "provider_circuit_open_after_consecutive_failures",
                "http_status": None,
                "cache_hit": False,
            }
        last_error = ""
        last_status: int | None = None
        for attempt in range(retries):
            self._delay(source)
            request = urllib.request.Request(
                request_url,
                headers={
                    "Accept": "application/json, application/xml, text/xml, text/plain",
                    "User-Agent": "HEAL-by-FON-evidence-refinement/1.0",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    status = int(response.status)
                self.network_calls[source] += 1
                self.consecutive_failures[source] = 0
                result_status = "not_found" if status == 404 else "success"
                reason = "provider_returned_not_found" if status == 404 else "provider_response"
                self.cache.put(source, query_mode, cache_url, body, result_status, reason, status)
                return {
                    "text": body,
                    "status": result_status,
                    "status_reason": reason,
                    "http_status": status,
                    "cache_hit": False,
                }
            except urllib.error.HTTPError as error:
                last_status = int(error.code)
                body = error.read().decode("utf-8", errors="replace")
                if error.code == 404:
                    self.network_calls[source] += 1
                    self.cache.put(source, query_mode, cache_url, body, "not_found", "provider_returned_not_found", 404)
                    return {
                        "text": body,
                        "status": "not_found",
                        "status_reason": "provider_returned_not_found",
                        "http_status": 404,
                        "cache_hit": False,
                    }
                last_error = f"HTTP {error.code}: {body[:500]}"
                retry_after = clean(error.headers.get("Retry-After"))
                if retry_after:
                    try:
                        time.sleep(min(float(retry_after), 30.0))
                    except ValueError:
                        pass
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = str(error)
            self.retries[source] += 1
            if attempt + 1 < retries:
                time.sleep(min(1.0 * (2**attempt), 8.0))
        self.cache.put(source, query_mode, cache_url, "", "source_error", last_error or "request_failed", last_status)
        self.consecutive_failures[source] += 1
        if self.consecutive_failures[source] >= 5:
            self.open_circuits.add(source)
        return {
            "text": "",
            "status": "source_error",
            "status_reason": last_error or "request_failed",
            "http_status": last_status,
            "cache_hit": False,
        }

    def get_json(self, source: str, query_mode: str, url: str) -> tuple[object | None, dict]:
        result = self.request(source, query_mode, url)
        if result["status"] != "success":
            return None, result
        try:
            return json.loads(result["text"]), result
        except json.JSONDecodeError as error:
            return None, {**result, "status": "source_error", "status_reason": f"invalid_json: {error}"}

    def get_xml(self, source: str, query_mode: str, url: str) -> tuple[ET.Element | None, dict]:
        result = self.request(source, query_mode, url)
        if result["status"] != "success":
            return None, result
        try:
            return ET.fromstring(result["text"]), result
        except ET.ParseError as error:
            return None, {**result, "status": "source_error", "status_reason": f"invalid_xml: {error}"}


def source_status(row: dict, source: str) -> str:
    status = clean(row.get(f"source_status_{source}")) or "not_queried"
    return status if status in VALID_SOURCE_STATUSES else "source_error"


def exact_identity(row: dict) -> bool:
    return clean(row.get("identity_match_class")) in {"exact_coordinate_allele", "normalized_indel_match"} and bool(
        clean(row.get("resolved_rsid"))
    )


def normalized_clinvar_class(value: object) -> str:
    text = clean(value).lower().replace("_", " ")
    if not text or text in {"not reported", "not_reported", "none"}:
        return "not_reported"
    if "conflict" in text:
        return "conflicting_pathogenicity"
    if "uncertain" in text or "vus" in text:
        return "uncertain_significance"
    if "pathogenic" in text and "benign" not in text:
        return "pathogenic_or_likely_pathogenic"
    if "benign" in text:
        return "benign_or_likely_benign"
    if "drug response" in text:
        return "drug_response"
    if "risk factor" in text:
        return "risk_factor"
    return "other_or_association"


def split_accessions(value: object, prefix: str) -> list[str]:
    return list(dict.fromkeys(re.findall(rf"{re.escape(prefix)}\d+(?:\.\d+)?", clean(value), flags=re.IGNORECASE)))


def clinvar_location_matches(root: ET.Element, row: dict) -> tuple[bool, str]:
    target_chrom = normalize_chromosome(row.get("chrom_vcf"))
    target_pos = clean(row.get("pos_vcf"))
    target_ref = clean(row.get("ref_vcf")).upper()
    target_alt = clean(row.get("alt_vcf")).upper()
    saw_grch38 = False
    for location in iter_local(root, "SequenceLocation"):
        if clean(location.attrib.get("Assembly")).upper() != clean(row.get("assembly") or "GRCh38").upper():
            continue
        saw_grch38 = True
        chrom = normalize_chromosome(location.attrib.get("Chr"))
        pos = clean(location.attrib.get("positionVCF"))
        ref = clean(location.attrib.get("referenceAlleleVCF")).upper()
        alt = clean(location.attrib.get("alternateAlleleVCF")).upper()
        if chrom == target_chrom and pos == target_pos and ref == target_ref and alt == target_alt:
            return True, "exact_coordinate_allele"
    return False, "clinvar_assembly_present_without_exact_allele" if saw_grch38 else "clinvar_assembly_not_present"


def clinvar_aggregate_classification(root: ET.Element) -> tuple[str, str, str]:
    descriptions: list[str] = []
    review_statuses: list[str] = []
    conditions: list[str] = []
    for classifications in iter_local(root, "Classifications"):
        for item in classifications.iter():
            name = local_name(item.tag)
            if name == "Description" and text_content(item):
                descriptions.append(text_content(item))
            elif name == "ReviewStatus" and text_content(item):
                review_statuses.append(text_content(item))
            elif name in {"ClassifiedCondition", "ElementValue"} and text_content(item):
                conditions.append(text_content(item))
    classification = unique_text(descriptions)
    return classification, unique_text(review_statuses), unique_text(conditions)


def clinvar_citations(element: ET.Element) -> tuple[list[str], list[str]]:
    pmids: list[str] = []
    dois: list[str] = []
    for citation in iter_local(element, "Citation"):
        for identifier in iter_local(citation, "ID"):
            source = clean(identifier.attrib.get("Source")).lower()
            value = text_content(identifier)
            if source == "pubmed" and value:
                pmids.append(value)
            elif source == "doi" and value:
                dois.append(value)
    return list(dict.fromkeys(pmids)), list(dict.fromkeys(dois))


def parse_clinvar_vcv(root: ET.Element, row: dict) -> dict:
    archive = next(iter_local(root, "VariationArchive"), root)
    aggregate_classification, review_status, conditions = clinvar_aggregate_classification(archive)
    exact_match, identity_reason = clinvar_location_matches(archive, row)
    assertions: list[dict] = []
    for assertion in iter_local(archive, "ClinicalAssertion"):
        accession_node = next(iter_local(assertion, "ClinVarAccession"), None)
        classification_node = next(iter_local(assertion, "Classification"), None)
        classification_values: list[str] = []
        assertion_review: list[str] = []
        if classification_node is not None:
            for node in classification_node.iter():
                name = local_name(node.tag)
                if name in {"GermlineClassification", "SomaticClinicalImpact", "OncogenicityClassification"}:
                    classification_values.append(text_content(node))
                elif name == "ReviewStatus":
                    assertion_review.append(text_content(node))
        assertion_conditions = [
            text_content(node)
            for node in assertion.iter()
            if local_name(node.tag) in {"ClassifiedCondition", "ElementValue"} and text_content(node)
        ]
        descriptions = [
            text_content(node)
            for node in assertion.iter()
            if local_name(node.tag) in {"Description", "Comment"} and text_content(node)
        ]
        observed_descriptions = [
            text_content(node)
            for node in iter_local(assertion, "Attribute")
            if clean(node.attrib.get("Type")).lower() == "description" and text_content(node)
        ]
        methods = [
            text_content(node)
            for node in assertion.iter()
            if local_name(node.tag) in {"MethodType", "AssertionMethod"} and text_content(node)
        ]
        pmids, dois = clinvar_citations(assertion)
        assertion_value = text_content(next(iter_local(assertion, "Assertion"), None))
        assertions.append(
            {
                "variant_key": clean(row.get("variant_key")),
                "physical_evidence_id": physical_evidence_id(clean(row.get("variant_key"))),
                "vcv_accession": clean(archive.attrib.get("Accession")),
                "scv_accession": clean(accession_node.attrib.get("Accession")) if accession_node is not None else "",
                "scv_version": clean(accession_node.attrib.get("Version")) if accession_node is not None else "",
                "submitter": clean(accession_node.attrib.get("SubmitterName")) if accession_node is not None else "",
                "organization_category": clean(accession_node.attrib.get("OrganizationCategory")) if accession_node is not None else "",
                "classification": unique_text(classification_values),
                "normalized_classification": normalized_clinvar_class(unique_text(classification_values)),
                "review_status": unique_text(assertion_review),
                "conditions": unique_text(assertion_conditions),
                "assertion_type": assertion_value,
                "method": unique_text(methods),
                "description": unique_text(descriptions, separator=" || "),
                "observed_description": unique_text(observed_descriptions, separator=" || "),
                "date_last_evaluated": clean(classification_node.attrib.get("DateLastEvaluated")) if classification_node is not None else "",
                "submission_date": clean(assertion.attrib.get("SubmissionDate")),
                "pmids": " | ".join(pmids),
                "dois": " | ".join(dois),
                "identity_match": "true" if exact_match else "false",
                "identity_match_reason": identity_reason,
                "contributes_to_aggregate": clean(assertion.attrib.get("ContributesToAggregateClassification")),
                "source_attribution": "ClinVar",
            }
        )
    aggregate_pmids, aggregate_dois = clinvar_citations(archive)
    return {
        "vcv_accession": clean(archive.attrib.get("Accession")),
        "vcv_version": clean(archive.attrib.get("Version")),
        "variation_id": clean(archive.attrib.get("VariationID")),
        "variation_name": clean(archive.attrib.get("VariationName")),
        "variation_type": clean(archive.attrib.get("VariationType")),
        "aggregate_classification": aggregate_classification,
        "normalized_classification": normalized_clinvar_class(aggregate_classification),
        "review_status": review_status,
        "conditions": conditions,
        "number_of_submissions": clean(archive.attrib.get("NumberOfSubmissions")),
        "number_of_submitters": clean(archive.attrib.get("NumberOfSubmitters")),
        "date_last_updated": clean(archive.attrib.get("DateLastUpdated")),
        "identity_match": exact_match,
        "identity_match_reason": identity_reason,
        "pmids": aggregate_pmids,
        "dois": aggregate_dois,
        "assertions": assertions,
    }


def discover_clinvar_accessions(row: dict, client: PublicHttpClient) -> tuple[list[str], dict]:
    rsid = clean(row.get("resolved_rsid"))
    if not rsid:
        return [], {"status": "not_queried", "status_reason": "confirmed_rsid_required"}
    params = urllib.parse.urlencode(
        {
            "db": "clinvar",
            "retmode": "json",
            "retmax": "20",
            "tool": "heal_fon_service",
            "term": f'"{rsid}"[Variant Name]',
        }
    )
    payload, result = client.get_json("ncbi", "clinvar_esearch", f"{EUTILS_BASE_URL}/esearch.fcgi?{params}")
    if not isinstance(payload, dict):
        return [], result
    ids = ((payload.get("esearchresult") or {}).get("idlist") or [])
    if not ids:
        return [], {**result, "status": "not_found", "status_reason": "clinvar_no_records_for_rsid"}
    summary_params = urllib.parse.urlencode(
        {"db": "clinvar", "retmode": "json", "tool": "heal_fon_service", "id": ",".join(ids[:20])}
    )
    summary, summary_result = client.get_json("ncbi", "clinvar_esummary", f"{EUTILS_BASE_URL}/esummary.fcgi?{summary_params}")
    if not isinstance(summary, dict):
        return [], summary_result
    accessions: list[str] = []
    for uid in ids:
        item = ((summary.get("result") or {}).get(str(uid)) or {})
        accession = item.get("accession")
        if isinstance(accession, dict):
            accession = accession.get("accession")
        accessions.extend(split_accessions(accession, "VCV"))
    return list(dict.fromkeys(accessions)), summary_result


def fetch_clinvar_records(row: dict, client: PublicHttpClient) -> tuple[list[dict], dict]:
    accessions = split_accessions(row.get("clinvar_accessions"), "VCV")
    discovery = {"status": "success", "status_reason": "base_accession_reused"}
    if not accessions:
        accessions, discovery = discover_clinvar_accessions(row, client)
    if not accessions:
        return [], discovery
    records: list[dict] = []
    errors: list[str] = []
    for accession in accessions:
        params = urllib.parse.urlencode({"db": "clinvar", "rettype": "vcv", "id": accession, "tool": "heal_fon_service"})
        root, result = client.get_xml("ncbi", "clinvar_vcv", f"{EUTILS_BASE_URL}/efetch.fcgi?{params}")
        if root is None:
            errors.append(clean(result.get("status_reason")))
            continue
        parsed = parse_clinvar_vcv(root, row)
        parsed["_raw_xml"] = clean(result.get("text"))
        records.append(parsed)
    if records:
        return records, {"status": "success", "status_reason": "clinvar_vcv_records_retrieved"}
    return [], {"status": "source_error", "status_reason": unique_text(errors) or "clinvar_vcv_fetch_failed"}


def extract_data_rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def extract_pmids(payload: object) -> list[str]:
    values = recursive_values(payload, {"pmid", "pubmedid", "resourceid"})
    output: list[str] = []
    for value in values:
        if isinstance(value, (str, int)) and re.fullmatch(r"\d+", clean(value)):
            output.append(clean(value))
    for cross_reference in recursive_values(payload, {"crossreferences"}):
        if isinstance(cross_reference, list):
            for item in cross_reference:
                if isinstance(item, dict) and clean(item.get("resource")).lower() == "pubmed":
                    output.append(clean(item.get("resourceId")))
    return list(dict.fromkeys(item for item in output if item))


def patient_alleles(row: dict) -> set[str]:
    genotype = clean(row.get("gt_alleles") or row.get("patient_gt_alleles"))
    alleles = {item.upper() for item in re.findall(r"[ACGT]+", genotype.upper())}
    if not alleles:
        alleles = {clean(row.get("ref_vcf")).upper(), clean(row.get("alt_vcf")).upper()}
    return {item for item in alleles if item}


def annotation_allele_match(annotation: object, row: dict) -> str:
    text = clean(annotation).upper()
    if not text:
        return "not_specified"
    raw_tokens = re.findall(r"(?<![A-Z])[ACGT]+(?![A-Z])", text)
    tokens: set[str] = set()
    for token in raw_tokens:
        if len(token) == 2:
            tokens.update(token)
        else:
            tokens.add(token)
    if not tokens:
        return "unparseable"
    observed = patient_alleles(row)
    if tokens <= observed:
        return "compatible_observed_genotype"
    if tokens.isdisjoint(observed):
        return "incompatible_observed_genotype"
    return "partially_compatible"


def evidence_level_rank(value: object) -> int:
    text = clean(value).upper().replace("LEVEL", "").strip()
    return {"1A": 1, "1B": 1, "2A": 2, "2B": 2, "3": 3, "4": 4}.get(text, 99)


def fetch_clinpgx_bundle(rsid: str, client: PublicHttpClient) -> tuple[dict, dict]:
    requests = {
        "variant": f"{CLINPGX_BASE_URL}/data/variant/?{urllib.parse.urlencode({'symbol': rsid, 'view': 'max'})}",
        "clinical": f"{CLINPGX_BASE_URL}/data/clinicalAnnotation?{urllib.parse.urlencode({'location.fingerprint': rsid, 'view': 'max'})}",
        "variant_annotation": f"{CLINPGX_BASE_URL}/data/variantAnnotation?{urllib.parse.urlencode({'location.fingerprint': rsid, 'view': 'max'})}",
    }
    bundle: dict[str, object] = {}
    errors: list[str] = []
    statuses: dict[str, str] = {}
    for key, url in requests.items():
        payload, result = client.get_json("clinpgx", f"clinpgx_{key}", url)
        statuses[key] = clean(result.get("status"))
        if payload is not None:
            bundle[key] = payload
        elif result.get("status") == "source_error":
            errors.append(f"{key}: {clean(result.get('status_reason'))}")
            bundle[key] = {"data": []}
        else:
            bundle[key] = {"data": []}
    record_count = sum(len(extract_data_rows(bundle.get(key))) for key in requests)
    if errors:
        status = "source_error"
        reason = unique_text(errors)
    elif record_count:
        status = "success"
        reason = "clinpgx_records_retrieved"
    else:
        status = "not_found"
        reason = "clinpgx_queries_completed_without_records"
    return bundle, {"status": status, "status_reason": reason, "requests": statuses, "record_count": record_count}


def parse_clinpgx_bundle(bundle: dict, row: dict) -> tuple[list[dict], list[dict]]:
    clinical_rows: list[dict] = []
    annotation_rows: list[dict] = []
    for item in extract_data_rows(bundle.get("clinical")):
        annotation_id = clean(item.get("id"))
        level_value = item.get("levelOfEvidence")
        if isinstance(level_value, dict):
            level = clean(level_value.get("term"))
        else:
            level = clean(level_value)
        chemicals = unique_text(
            chemical.get("name") for chemical in item.get("relatedChemicals") or [] if isinstance(chemical, dict)
        )
        allele_phenotypes = item.get("allelePhenotypes") or []
        if not allele_phenotypes:
            allele_phenotypes = [{}]
        for allele_item in allele_phenotypes:
            if not isinstance(allele_item, dict):
                allele_item = {}
            allele = clean(allele_item.get("allele"))
            match = annotation_allele_match(allele, row)
            eligible = match in {"compatible_observed_genotype", "not_specified"} and evidence_level_rank(level) <= 3
            clinical_rows.append(
                {
                    "variant_key": clean(row.get("variant_key")),
                    "physical_evidence_id": physical_evidence_id(clean(row.get("variant_key"))),
                    "resolved_rsid": clean(row.get("resolved_rsid")),
                    "clinical_annotation_id": annotation_id,
                    "name": clean(item.get("name")),
                    "evidence_level": level,
                    "phenotype_category": clean(item.get("phenotypeCategory") or item.get("type")),
                    "drugs": chemicals,
                    "allele_or_genotype": allele,
                    "phenotype": clean(allele_item.get("phenotype")),
                    "allele_match_status": match,
                    "publication_followup_eligible": "true" if eligible else "false",
                    "pmids": " | ".join(extract_pmids(item)),
                    "source_attribution": CLINPGX_ATTRIBUTION,
                    "source_license": CLINPGX_LICENSE,
                }
            )
    for item in extract_data_rows(bundle.get("variant_annotation")):
        genotype = clean(item.get("alleleGenotype"))
        match = annotation_allele_match(genotype, row)
        chemicals = unique_text(
            chemical.get("name") for chemical in item.get("relatedChemicals") or [] if isinstance(chemical, dict)
        )
        annotation_rows.append(
            {
                "variant_key": clean(row.get("variant_key")),
                "physical_evidence_id": physical_evidence_id(clean(row.get("variant_key"))),
                "resolved_rsid": clean(row.get("resolved_rsid")),
                "variant_annotation_id": clean(item.get("id")),
                "allele_or_genotype": genotype,
                "allele_match_status": match,
                "drugs": chemicals,
                "sentence": clean(item.get("sentence")),
                "score": clean(item.get("score")),
                "pmids": " | ".join(extract_pmids(item)),
                "source_attribution": CLINPGX_ATTRIBUTION,
                "source_license": CLINPGX_LICENSE,
            }
        )
    return clinical_rows, annotation_rows


def effect_allele_for_rsid(association: dict, rsid: str) -> str:
    for item in association.get("snp_allele") or []:
        if isinstance(item, dict) and clean(item.get("rs_id")).lower() == rsid.lower():
            return clean(item.get("effect_allele")).upper()
    for value in association.get("snp_effect_allele") or []:
        text = clean(value)
        match = re.fullmatch(rf"{re.escape(rsid)}-(.+)", text, flags=re.IGNORECASE)
        if match:
            return clean(match.group(1)).upper()
    return ""


def gwas_effect_match(effect_allele: str, row: dict) -> str:
    effect = clean(effect_allele).upper()
    if not effect or effect in {"?", "NR", "-"}:
        return "effect_allele_missing"
    if effect == clean(row.get("alt_vcf")).upper():
        return "exact_observed_alt"
    if effect == clean(row.get("ref_vcf")).upper():
        return "reference_allele_context"
    return "effect_allele_mismatch"


def fetch_gwas_associations(rsid: str, client: PublicHttpClient) -> tuple[list[dict], dict]:
    page = 0
    associations: list[dict] = []
    while page < 200:
        params = urllib.parse.urlencode({"rs_id": rsid, "page": page, "size": 100})
        payload, result = client.get_json("gwas", "gwas_v2_associations", f"{GWAS_BASE_URL}/associations?{params}")
        if not isinstance(payload, dict):
            return associations, result
        page_rows = ((payload.get("_embedded") or {}).get("associations") or [])
        associations.extend(item for item in page_rows if isinstance(item, dict))
        page_info = payload.get("page") or {}
        total_pages = as_int(page_info.get("totalPages"), 0)
        if total_pages <= page + 1 or not page_rows:
            break
        page += 1
    status = "success" if associations else "not_found"
    reason = "gwas_v2_associations_retrieved" if associations else "gwas_v2_no_associations"
    return associations, {"status": status, "status_reason": reason}


def fetch_gwas_study(accession_id: str, client: PublicHttpClient) -> tuple[dict, dict]:
    study, study_result = client.get_json(
        "gwas", "gwas_v2_study", f"{GWAS_BASE_URL}/studies/{urllib.parse.quote(accession_id)}"
    )
    ancestries, ancestry_result = client.get_json(
        "gwas", "gwas_v2_ancestry", f"{GWAS_BASE_URL}/studies/{urllib.parse.quote(accession_id)}/ancestries?size=100"
    )
    errors = [
        clean(item.get("status_reason"))
        for item in (study_result, ancestry_result)
        if item.get("status") == "source_error"
    ]
    return {
        "study": study if isinstance(study, dict) else {},
        "ancestries": ancestries if isinstance(ancestries, dict) else {},
    }, {
        "status": "source_error" if errors else "success",
        "status_reason": unique_text(errors) or "gwas_study_metadata_retrieved",
    }


def parse_gwas_association(association: dict, row: dict, study_bundle: dict | None = None) -> dict:
    rsid = clean(row.get("resolved_rsid"))
    effect_allele = effect_allele_for_rsid(association, rsid)
    match = gwas_effect_match(effect_allele, row)
    pvalue = as_float(association.get("p_value"))
    significant = pvalue is not None and pvalue <= 5e-8
    focus = match == "exact_observed_alt" and significant
    study = (study_bundle or {}).get("study") or {}
    ancestries_payload = (study_bundle or {}).get("ancestries") or {}
    ancestry_rows = ((ancestries_payload.get("_embedded") or {}).get("ancestries") or [])
    ancestry_values: list[str] = []
    for ancestry in ancestry_rows:
        if not isinstance(ancestry, dict):
            continue
        for value in ancestry.get("ancestral_groups") or []:
            if isinstance(value, dict):
                ancestry_values.append(clean(value.get("ancestral_group")))
            elif clean(value):
                ancestry_values.append(clean(value))
        for value in ancestry.get("country_of_recruitment") or []:
            if isinstance(value, dict) and clean(value.get("country_name")):
                ancestry_values.append(f"country:{clean(value.get('country_name'))}")
    efo_traits = association.get("efo_traits") or []
    return {
        "variant_key": clean(row.get("variant_key")),
        "physical_evidence_id": physical_evidence_id(clean(row.get("variant_key"))),
        "resolved_rsid": rsid,
        "association_id": clean(association.get("association_id")),
        "study_accession": clean(association.get("accession_id")),
        "pubmed_id": clean(association.get("pubmed_id")),
        "first_author": clean(association.get("first_author")),
        "reported_traits": unique_text(association.get("reported_trait") or []),
        "mapped_traits": unique_text(item.get("efo_trait") for item in efo_traits if isinstance(item, dict)),
        "mapped_trait_ids": unique_text(item.get("efo_id") for item in efo_traits if isinstance(item, dict)),
        "mapped_genes": unique_text(association.get("mapped_genes") or []),
        "locations": unique_text(association.get("locations") or []),
        "effect_allele": effect_allele,
        "effect_allele_match": match,
        "risk_frequency": clean(association.get("risk_frequency")),
        "p_value": clean(association.get("p_value")),
        "or_value": clean(association.get("or_value") or association.get("or_per_copy_num")),
        "beta": clean(association.get("beta_num") or association.get("beta")),
        "beta_direction": clean(association.get("beta_direction")),
        "confidence_interval": clean(association.get("range")),
        "is_genome_wide_significant": "true" if significant else "false",
        "focus_eligible": "true" if focus else "false",
        "initial_sample_size": clean(study.get("initial_sample_size")),
        "replication_sample_size": clean(study.get("replication_sample_size")),
        "ancestry": unique_text(ancestry_values),
        "source_attribution": "NHGRI-EBI GWAS Catalog",
        "evidence_scope": "population_association_not_individual_causality",
    }


def parse_pubmed_article(root: ET.Element, pmid: str) -> dict:
    article = next(iter_local(root, "PubmedArticle"), root)
    title = text_content(next(iter_local(article, "ArticleTitle"), None))
    abstract_parts: list[str] = []
    for abstract in iter_local(article, "AbstractText"):
        label = clean(abstract.attrib.get("Label"))
        text = text_content(abstract)
        if text:
            abstract_parts.append(f"{label}: {text}" if label else text)
    journal = text_content(next(iter_local(article, "Title"), None))
    year = text_content(next(iter_local(article, "Year"), None))
    if not year:
        medline_date = text_content(next(iter_local(article, "MedlineDate"), None))
        year_match = re.search(r"\b(19|20)\d{2}\b", medline_date)
        year = year_match.group(0) if year_match else ""
    authors: list[str] = []
    for author in iter_local(article, "Author"):
        collective = text_content(next(iter_local(author, "CollectiveName"), None))
        family = text_content(next(iter_local(author, "LastName"), None))
        initials = text_content(next(iter_local(author, "Initials"), None))
        value = collective or " ".join(item for item in (family, initials) if item)
        if value:
            authors.append(value)
    doi = ""
    pmcid = ""
    for identifier in iter_local(article, "ArticleId"):
        identifier_type = clean(identifier.attrib.get("IdType")).lower()
        if identifier_type == "doi":
            doi = text_content(identifier)
        elif identifier_type == "pmc":
            pmcid = text_content(identifier)
    return {
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "title": title,
        "journal": journal,
        "publication_year": year,
        "authors": unique_text(authors),
        "abstract": "\n".join(abstract_parts),
    }


def fetch_publication(pmid: str, client: PublicHttpClient, publication_dir: Path) -> tuple[dict, dict]:
    params = urllib.parse.urlencode({"db": "pubmed", "rettype": "xml", "id": pmid, "tool": "heal_fon_service"})
    root, result = client.get_xml("ncbi", "pubmed_efetch", f"{EUTILS_BASE_URL}/efetch.fcgi?{params}")
    if root is None:
        return {"pmid": pmid}, result
    publication = parse_pubmed_article(root, pmid)
    publication.update(
        {
            "pubmed_status": "success",
            "pmc_open_access": "false",
            "pmc_full_text_path": "",
            "pmc_full_text_sha256": "",
            "pmc_full_text_characters": 0,
        }
    )
    pmcid = clean(publication.get("pmcid"))
    if pmcid:
        oa_root, oa_result = client.get_xml("pmc", "pmc_oa_check", f"{PMC_OA_URL}?{urllib.parse.urlencode({'id': pmcid})}")
        oa_record = next(iter_local(oa_root, "record"), None) if oa_root is not None else None
        if oa_record is not None:
            pmc_params = urllib.parse.urlencode({"db": "pmc", "rettype": "xml", "id": pmcid, "tool": "heal_fon_service"})
            pmc_root, pmc_result = client.get_xml("pmc", "pmc_efetch", f"{EUTILS_BASE_URL}/efetch.fcgi?{pmc_params}")
            if pmc_root is not None:
                full_text = text_content(pmc_root)
                if full_text:
                    publication_dir.mkdir(parents=True, exist_ok=True)
                    full_text_path = publication_dir / f"{pmcid}.txt.gz"
                    with gzip.open(full_text_path, "wt", encoding="utf-8") as handle:
                        handle.write(full_text)
                    publication.update(
                        {
                            "pmc_open_access": "true",
                            "pmc_full_text_path": str(full_text_path),
                            "pmc_full_text_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
                            "pmc_full_text_characters": len(full_text),
                        }
                    )
            elif pmc_result.get("status") == "source_error":
                publication["pmc_status_reason"] = clean(pmc_result.get("status_reason"))
        elif oa_result.get("status") == "source_error":
            publication["pmc_status_reason"] = clean(oa_result.get("status_reason"))
    return publication, result


def identity_status(row: dict) -> str:
    match_class = clean(row.get("identity_match_class"))
    if match_class == "exact_coordinate_allele":
        return "exact"
    if match_class == "normalized_indel_match":
        return "normalized_indel"
    if match_class == "rsid_without_allele_confirmation":
        return "candidate_unconfirmed"
    if match_class == "ambiguous_identity":
        return "ambiguous"
    return "unresolved"


def functional_annotation_status(row: dict) -> str:
    if source_status(row, "ensembl_vep") == "source_error" or clean(row.get("vep_status")) == "source_error":
        return "vep_error"
    consequence = clean(row.get("vep_most_severe_consequence"))
    if consequence and (clean(row.get("vep_hgvsc")) or clean(row.get("vep_hgvsp"))):
        return "functional_complete"
    if consequence:
        return "functional_partial"
    if clean(row.get("vep_status")) == "success":
        return "functional_minimal"
    return "functional_unavailable"


def classify_curated_variant(
    row: dict,
    clinvar_summary: dict,
    pgx_summary: dict,
    gwas_summary: dict,
) -> dict:
    identity = identity_status(row)
    functional = functional_annotation_status(row)
    clinvar_class = clean(clinvar_summary.get("normalized_classification")) or normalized_clinvar_class(
        row.get("clinvar_normalized_classification")
    )
    pgx_usable = as_int(pgx_summary.get("compatible_clinical_annotations")) > 0
    gwas_focus = as_int(gwas_summary.get("focus_associations")) > 0
    clinvar_focus = clinvar_class in NON_BENIGN_CLINVAR_CLASSES and as_bool(clinvar_summary.get("identity_confirmed"))
    population = bool(clean(row.get("population_frequency_summary")))
    external_evidence = clinvar_class != "not_reported" or pgx_usable or as_int(gwas_summary.get("total_associations")) > 0
    source_errors = [
        source
        for source in ("clinvar", "pharmgkb", "gwas")
        if clean(row.get(f"refined_status_{source}")) == "source_error" or source_status(row, source) == "source_error"
    ]
    if clinvar_class != "not_reported":
        evidence_status = "clinical"
    elif pgx_usable:
        evidence_status = "pharmacogenomic"
    elif as_int(gwas_summary.get("total_associations")) > 0:
        evidence_status = "gwas_population_association"
    elif source_errors:
        evidence_status = "source_error"
    elif population:
        evidence_status = "population_context"
    elif functional.startswith("functional_") and functional != "functional_unavailable":
        evidence_status = "functional_only"
    else:
        evidence_status = "not_found_or_not_queried"
    if identity in {"ambiguous", "unresolved", "candidate_unconfirmed"}:
        curation_depth = "unresolved"
        role = "unresolved_review"
    elif source_errors and not external_evidence:
        curation_depth = "unresolved"
        role = "unresolved_review"
    elif clinvar_focus or pgx_usable or gwas_focus:
        curation_depth = "deep_curated"
        role = "focus_candidate"
    elif clinvar_class == "benign_or_likely_benign" and not (pgx_usable or gwas_focus):
        curation_depth = "structured_summary"
        role = "benign_context"
    elif external_evidence:
        curation_depth = "structured_summary"
        role = "supporting_context"
    elif functional != "functional_unavailable":
        curation_depth = "functional_only"
        role = "annotation_absent_context"
    else:
        curation_depth = "unresolved"
        role = "unresolved_review"
    return {
        "identity_status": identity,
        "functional_annotation_status": functional,
        "source_evidence_status": evidence_status,
        "curation_depth": curation_depth,
        "downstream_role": role,
    }


def projection_key(row: dict) -> str:
    explicit = clean(row.get("variant_gene_module_id"))
    if explicit:
        return explicit
    return "|".join(
        [clean(row.get("variant_key")), clean(row.get("approved_symbol")), clean(row.get("module_id")), clean(row.get("canon_row_id"))]
    )


def public_fact_fields(row: dict) -> dict:
    allowed = [
        "variant_key",
        "assembly",
        "assembly_name",
        "chrom_vcf",
        "pos_vcf",
        "variant_start",
        "variant_end",
        "ref_vcf",
        "alt_vcf",
        "id_vcf",
        "allele_index",
        "allele_dosage",
        "gt_raw",
        "gt_alleles",
        "zygosity",
        "qual_vcf",
        "filter_vcf",
        "quality_flag",
    ]
    return {key: clean(row.get(key)) for key in allowed}


def process(payload: dict) -> dict:
    started_at = utc_now()
    started_clock = time.perf_counter()
    output_dir = Path(payload["outputDir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis_mode = clean(payload.get("analysisMode") or "quick").lower()
    if analysis_mode not in {"quick", "complete", "qa"}:
        analysis_mode = "quick"
    complete_mode = analysis_mode in {"complete", "qa"}
    physical_path = Path(payload["physicalMatrixPath"])
    match_path = Path(payload["matchPath"])
    triage_path = Path(payload["triagePath"])
    triage_excluded_path = Path(payload["triageExcludedPath"]) if clean(payload.get("triageExcludedPath")) else None
    canon_path = Path(payload["canonCleanPath"])
    normalized_path = Path(payload["normalizedVariantsPath"]) if clean(payload.get("normalizedVariantsPath")) else None
    cache_path = Path(payload.get("cachePath") or output_dir.parent.parent / "enrichment-cache" / "evidence_refinement_cache.sqlite")
    timeout_seconds = as_int(payload.get("timeoutSeconds"), 30)

    write_progress(
        output_dir,
        substage="indexing_contracts",
        processed=2,
        total=100,
        unit="percent",
        message="Indexing physical, match, triage and canon contracts",
    )
    physical_rows = read_csv(physical_path)
    match_rows = read_csv(match_path)
    triage_rows = read_csv(triage_path)
    triage_excluded_rows = (
        read_csv(triage_excluded_path) if triage_excluded_path and triage_excluded_path.is_file() else []
    )
    canon_rows = read_csv(canon_path)
    if not physical_rows or not match_rows or not triage_rows or not canon_rows:
        raise ValueError("Evidence refinement requires non-empty physical, match, triage and canon artifacts.")
    physical_keys = [clean(row.get("variant_key")) for row in physical_rows]
    if any(not key for key in physical_keys) or len(set(physical_keys)) != len(physical_keys):
        raise ValueError("Physical enrichment matrix must contain one unique non-empty variant_key per row.")
    physical_by_key = {clean(row.get("variant_key")): row for row in physical_rows}
    representative_match: dict[str, dict] = {}
    for row in match_rows:
        key = clean(row.get("variant_key"))
        if key and key not in representative_match:
            representative_match[key] = row
    normalized_contract_available = bool(normalized_path and normalized_path.is_file())
    normalized_rows = read_csv(normalized_path) if normalized_contract_available else list(representative_match.values())
    normalized_keys = [clean(row.get("variant_key")) for row in normalized_rows]
    if any(not key for key in normalized_keys):
        raise ValueError("Normalized variant registry contains an empty variant_key.")
    normalized_duplicate_count = len(normalized_keys) - len(set(normalized_keys))
    normalized_by_key: dict[str, dict] = {}
    for row in normalized_rows:
        normalized_by_key.setdefault(clean(row.get("variant_key")), row)
    triage_keys = {projection_key(row) for row in triage_rows}
    triage_variant_keys = {clean(row.get("variant_key")) for row in triage_rows if clean(row.get("variant_key"))}
    triage_audit_by_projection = {
        projection_key(row): row for row in [*triage_rows, *triage_excluded_rows] if projection_key(row)
    }
    triage_audit_keys = [projection_key(row) for row in [*triage_rows, *triage_excluded_rows]]
    match_projection_keys = [projection_key(row) for row in match_rows]
    triage_audit_contract_available = bool(triage_excluded_path and triage_excluded_path.is_file())

    cache = RefinementCache(cache_path, ttl_days=as_int(payload.get("cacheTtlDays"), 30))
    client = PublicHttpClient(cache, timeout_seconds=timeout_seconds)
    raw_path = output_dir / "evidence_refinement_raw.jsonl.gz"
    retry_rows: list[dict] = []
    publication_refs: dict[str, set[str]] = defaultdict(set)
    publication_variants: dict[str, set[str]] = defaultdict(set)

    clinvar_aggregate_rows: list[dict] = []
    clinvar_assertion_rows: list[dict] = []
    clinvar_summaries: dict[str, dict] = {}
    clinpgx_clinical_rows: list[dict] = []
    clinpgx_annotation_rows: list[dict] = []
    clinpgx_summaries: dict[str, dict] = {}
    gwas_rows: list[dict] = []
    gwas_summaries: dict[str, dict] = {}

    with gzip.open(raw_path, "wt", encoding="utf-8") as raw_handle:
        clinvar_candidates = [
            row
            for row in physical_rows
            if exact_identity(row)
            and (
                source_status(row, "clinvar") == "success"
                or bool(split_accessions(row.get("clinvar_accessions"), "VCV"))
                or normalized_clinvar_class(row.get("clinvar_normalized_classification")) != "not_reported"
                or (complete_mode and source_status(row, "clinvar") == "source_error")
            )
        ]
        candidate_keys = {clean(row.get("variant_key")) for row in clinvar_candidates}
        for index, row in enumerate(physical_rows, start=1):
            key = clean(row.get("variant_key"))
            base_class = normalized_clinvar_class(row.get("clinvar_normalized_classification"))
            aggregate = {
                "variant_key": key,
                "physical_evidence_id": physical_evidence_id(key),
                "resolved_rsid": clean(row.get("resolved_rsid")),
                "base_status": source_status(row, "clinvar"),
                "base_status_reason": clean(row.get("source_status_reason_clinvar")),
                "base_classification": clean(row.get("clinvar_germline_classification")),
                "base_normalized_classification": base_class,
                "refinement_status": "not_queried",
                "refinement_status_reason": "no_base_clinvar_record",
                "identity_confirmed": "false",
                "vcv_accessions": "",
                "aggregate_classification": clean(row.get("clinvar_germline_classification")),
                "normalized_classification": base_class,
                "review_status": clean(row.get("clinvar_review_status")),
                "conditions": clean(row.get("clinvar_trait_names")),
                "assertion_count": 0,
                "submitter_count": 0,
                "publication_followup_eligible": "false",
                "pmids": "",
                "source_attribution": "ClinVar",
            }
            if key in candidate_keys:
                records, status = fetch_clinvar_records(row, client)
                aggregate["refinement_status"] = clean(status.get("status"))
                aggregate["refinement_status_reason"] = clean(status.get("status_reason"))
                exact_records = [record for record in records if record.get("identity_match")]
                aggregate["identity_confirmed"] = "true" if exact_records else "false"
                if exact_records:
                    aggregate["vcv_accessions"] = unique_text(record.get("vcv_accession") for record in exact_records)
                    aggregate["aggregate_classification"] = unique_text(record.get("aggregate_classification") for record in exact_records)
                    aggregate["normalized_classification"] = normalized_clinvar_class(aggregate["aggregate_classification"])
                    aggregate["review_status"] = unique_text(record.get("review_status") for record in exact_records)
                    aggregate["conditions"] = unique_text(record.get("conditions") for record in exact_records)
                    assertions = [assertion for record in exact_records for assertion in record.get("assertions") or []]
                    clinvar_assertion_rows.extend(assertions)
                    aggregate["assertion_count"] = len(assertions)
                    aggregate["submitter_count"] = len({clean(item.get("submitter")) for item in assertions if clean(item.get("submitter"))})
                    all_pmids = list(dict.fromkeys(
                        [pmid for record in exact_records for pmid in record.get("pmids") or []]
                        + [pmid for assertion in assertions for pmid in split_accessions(assertion.get("pmids"), "")]
                    ))
                    normalized = clean(aggregate["normalized_classification"])
                    followup = normalized in NON_BENIGN_CLINVAR_CLASSES
                    aggregate["publication_followup_eligible"] = "true" if followup else "false"
                    aggregate["pmids"] = " | ".join(all_pmids)
                    if followup:
                        for pmid in all_pmids:
                            publication_refs[pmid].add("clinvar")
                            publication_variants[pmid].add(key)
                        for assertion in assertions:
                            if clean(assertion.get("normalized_classification")) in NON_BENIGN_CLINVAR_CLASSES:
                                for pmid in re.findall(r"\d+", clean(assertion.get("pmids"))):
                                    publication_refs[pmid].add("clinvar_scv")
                                    publication_variants[pmid].add(key)
                if status.get("status") == "source_error" or (records and not exact_records):
                    retry_rows.append(
                        {
                            "variant_key": key,
                            "source": "clinvar",
                            "query_mode": "vcv_scv",
                            "reason": clean(status.get("status_reason")) if not records else "clinvar_vcv_identity_mismatch",
                            "priority": "high",
                            "next_attempt": "complete_analysis_or_manual_retry",
                        }
                    )
                raw_handle.write(json.dumps({"source": "clinvar", "variant_key": key, "records": records}, ensure_ascii=True) + "\n")
            elif source_status(row, "clinvar") == "source_error":
                aggregate["refinement_status"] = "not_queried" if not complete_mode else "source_error"
                aggregate["refinement_status_reason"] = "quick_mode_preserves_base_source_error" if not complete_mode else "clinvar_retry_not_resolved"
                retry_rows.append(
                    {
                        "variant_key": key,
                        "source": "clinvar",
                        "query_mode": "base_retry",
                        "reason": aggregate["refinement_status_reason"],
                        "priority": "high",
                        "next_attempt": "complete_analysis",
                    }
                )
            clinvar_aggregate_rows.append(aggregate)
            clinvar_summaries[key] = aggregate
            if index % 25 == 0 or index == len(physical_rows):
                progress = 5 + round(25 * index / len(physical_rows))
                write_progress(
                    output_dir,
                    substage="clinvar_vcv_scv",
                    processed=progress,
                    total=100,
                    unit="percent",
                    message=f"Curating ClinVar VCV/SCV records ({index}/{len(physical_rows)} physical variants)",
                    metrics={"deep_candidates": len(clinvar_candidates), "assertions": len(clinvar_assertion_rows)},
                )

        clinpgx_candidates = [
            row
            for row in physical_rows
            if exact_identity(row)
            and (
                as_int(row.get("pharmgkb_clinical_annotation_count")) > 0
                or as_int(row.get("pharmgkb_variant_annotation_count")) > 0
                or source_status(row, "pharmgkb") == "success"
                or (complete_mode and source_status(row, "pharmgkb") == "source_error")
            )
        ]
        for index, row in enumerate(clinpgx_candidates, start=1):
            key = clean(row.get("variant_key"))
            bundle, status = fetch_clinpgx_bundle(clean(row.get("resolved_rsid")), client)
            clinical, annotations = parse_clinpgx_bundle(bundle, {**row, **representative_match.get(key, {})})
            clinpgx_clinical_rows.extend(clinical)
            clinpgx_annotation_rows.extend(annotations)
            compatible = [item for item in clinical if item.get("allele_match_status") in {"compatible_observed_genotype", "not_specified"}]
            eligible = [item for item in clinical if as_bool(item.get("publication_followup_eligible"))]
            for item in eligible:
                for pmid in re.findall(r"\d+", clean(item.get("pmids"))):
                    publication_refs[pmid].add("clinpgx")
                    publication_variants[pmid].add(key)
            clinpgx_summaries[key] = {
                "refinement_status": clean(status.get("status")),
                "refinement_status_reason": clean(status.get("status_reason")),
                "clinical_annotations": len(clinical),
                "compatible_clinical_annotations": len(compatible),
                "variant_annotations": len(annotations),
                "publication_followup_annotations": len(eligible),
            }
            if status.get("status") == "source_error":
                retry_rows.append(
                    {
                        "variant_key": key,
                        "source": "clinpgx",
                        "query_mode": "full_annotations",
                        "reason": clean(status.get("status_reason")),
                        "priority": "normal",
                        "next_attempt": "automatic_retry",
                    }
                )
            raw_handle.write(json.dumps({"source": "clinpgx", "variant_key": key, "bundle": bundle}, ensure_ascii=True) + "\n")
            write_progress(
                output_dir,
                substage="clinpgx_annotations",
                processed=30 + round(20 * index / max(len(clinpgx_candidates), 1)),
                total=100,
                unit="percent",
                message=f"Curating ClinPGx annotations ({index}/{len(clinpgx_candidates)})",
                metrics={"clinical_annotations": len(clinpgx_clinical_rows), "variant_annotations": len(clinpgx_annotation_rows)},
            )

        gwas_candidates = [
            row
            for row in physical_rows
            if exact_identity(row)
            and (
                as_int(row.get("gwas_association_count")) > 0
                or source_status(row, "gwas") == "success"
                or (complete_mode and source_status(row, "gwas") == "source_error")
            )
        ]
        study_cache: dict[str, dict] = {}
        for index, row in enumerate(gwas_candidates, start=1):
            key = clean(row.get("variant_key"))
            associations, status = fetch_gwas_associations(clean(row.get("resolved_rsid")), client)
            parsed_rows = [parse_gwas_association(item, row) for item in associations]
            focus_rows = [item for item in parsed_rows if as_bool(item.get("focus_eligible"))]
            focus_accessions = {clean(item.get("study_accession")) for item in focus_rows if clean(item.get("study_accession"))}
            for accession in focus_accessions:
                if accession not in study_cache:
                    study_cache[accession], study_status = fetch_gwas_study(accession, client)
                    if study_status.get("status") == "source_error":
                        retry_rows.append(
                            {
                                "variant_key": key,
                                "source": "gwas",
                                "query_mode": "study_ancestry",
                                "reason": clean(study_status.get("status_reason")),
                                "priority": "normal",
                                "next_attempt": "automatic_retry",
                            }
                        )
            final_rows = [
                parse_gwas_association(item, row, study_cache.get(clean(item.get("accession_id")))) for item in associations
            ]
            gwas_rows.extend(final_rows)
            focus_rows = [item for item in final_rows if as_bool(item.get("focus_eligible"))]
            for item in focus_rows:
                pmid = clean(item.get("pubmed_id"))
                if pmid:
                    publication_refs[pmid].add("gwas")
                    publication_variants[pmid].add(key)
            gwas_summaries[key] = {
                "refinement_status": clean(status.get("status")),
                "refinement_status_reason": clean(status.get("status_reason")),
                "total_associations": len(final_rows),
                "genome_wide_significant_associations": sum(as_bool(item.get("is_genome_wide_significant")) for item in final_rows),
                "focus_associations": len(focus_rows),
            }
            if status.get("status") == "source_error":
                retry_rows.append(
                    {
                        "variant_key": key,
                        "source": "gwas",
                        "query_mode": "v2_associations",
                        "reason": clean(status.get("status_reason")),
                        "priority": "normal",
                        "next_attempt": "automatic_retry",
                    }
                )
            raw_handle.write(json.dumps({"source": "gwas", "variant_key": key, "associations": associations}, ensure_ascii=True) + "\n")
            write_progress(
                output_dir,
                substage="gwas_allele_aware",
                processed=50 + round(20 * index / max(len(gwas_candidates), 1)),
                total=100,
                unit="percent",
                message=f"Curating GWAS associations ({index}/{len(gwas_candidates)})",
                metrics={"associations": len(gwas_rows), "focus_associations": sum(as_bool(item.get("focus_eligible")) for item in gwas_rows)},
            )

        publication_rows: list[dict] = []
        publication_dir = output_dir / "publications"
        sorted_pmids = sorted(publication_refs, key=lambda value: int(value) if value.isdigit() else value)
        for index, pmid in enumerate(sorted_pmids, start=1):
            publication, status = fetch_publication(pmid, client, publication_dir)
            publication.update(
                {
                    "source_families": unique_text(sorted(publication_refs[pmid])),
                    "variant_keys": unique_text(sorted(publication_variants[pmid])),
                    "retrieval_status": clean(status.get("status")),
                    "retrieval_status_reason": clean(status.get("status_reason")),
                    "retrieved_at": utc_now(),
                }
            )
            publication_rows.append(publication)
            if status.get("status") == "source_error":
                retry_rows.append(
                    {
                        "variant_key": unique_text(sorted(publication_variants[pmid])),
                        "source": "pubmed",
                        "query_mode": "abstract_and_pmc_oa",
                        "reason": clean(status.get("status_reason")),
                        "priority": "normal",
                        "next_attempt": "automatic_retry",
                        "pmid": pmid,
                    }
                )
            raw_handle.write(json.dumps({"source": "publication", "pmid": pmid, "record": publication}, ensure_ascii=True) + "\n")
            write_progress(
                output_dir,
                substage="publication_resolution",
                processed=70 + round(20 * index / max(len(sorted_pmids), 1)),
                total=100,
                unit="percent",
                message=f"Resolving selected PubMed/PMC evidence ({index}/{len(sorted_pmids)})",
                metrics={"unique_publications": len(sorted_pmids), "pmc_open_access": sum(as_bool(item.get("pmc_open_access")) for item in publication_rows)},
            )

    for row in physical_rows:
        key = clean(row.get("variant_key"))
        clinpgx_summaries.setdefault(
            key,
            {
                "refinement_status": "not_queried",
                "refinement_status_reason": "no_base_clinpgx_annotation",
                "clinical_annotations": 0,
                "compatible_clinical_annotations": 0,
                "variant_annotations": 0,
                "publication_followup_annotations": 0,
            },
        )
        gwas_summaries.setdefault(
            key,
            {
                "refinement_status": "not_queried",
                "refinement_status_reason": "no_base_gwas_association",
                "total_associations": 0,
                "genome_wide_significant_associations": 0,
                "focus_associations": 0,
            },
        )

    publication_count_by_variant = Counter(
        variant_key for variants_for_publication in publication_variants.values() for variant_key in variants_for_publication
    )
    curated_rows: list[dict] = []
    for row in physical_rows:
        key = clean(row.get("variant_key"))
        representative = representative_match.get(key, {})
        clinvar = clinvar_summaries[key]
        pgx = clinpgx_summaries[key]
        gwas = gwas_summaries[key]
        refinement_statuses = {
            "refined_status_clinvar": clean(clinvar.get("refinement_status")),
            "refined_status_pharmgkb": clean(pgx.get("refinement_status")),
            "refined_status_gwas": clean(gwas.get("refinement_status")),
        }
        classification = classify_curated_variant({**row, **refinement_statuses}, clinvar, pgx, gwas)
        curated_rows.append(
            {
                **row,
                "physical_evidence_id": physical_evidence_id(key),
                "gt_raw": clean(representative.get("gt_raw")),
                "gt_alleles": clean(representative.get("gt_alleles") or representative.get("patient_gt_alleles")),
                "zygosity": clean(representative.get("zygosity")),
                "qual_vcf": clean(representative.get("qual_vcf")),
                "filter_vcf": clean(representative.get("filter_vcf")),
                "quality_flag": clean(representative.get("quality_flag")),
                **refinement_statuses,
                "refined_reason_clinvar": clean(clinvar.get("refinement_status_reason")),
                "refined_reason_pharmgkb": clean(pgx.get("refinement_status_reason")),
                "refined_reason_gwas": clean(gwas.get("refinement_status_reason")),
                "curated_clinvar_classification": clean(clinvar.get("normalized_classification")),
                "curated_clinvar_assertion_count": as_int(clinvar.get("assertion_count")),
                "curated_clinvar_submitter_count": as_int(clinvar.get("submitter_count")),
                "curated_clinvar_identity_confirmed": clean(clinvar.get("identity_confirmed")),
                "curated_clinpgx_clinical_annotation_count": as_int(pgx.get("clinical_annotations")),
                "curated_clinpgx_compatible_annotation_count": as_int(pgx.get("compatible_clinical_annotations")),
                "curated_clinpgx_variant_annotation_count": as_int(pgx.get("variant_annotations")),
                "curated_gwas_association_count": as_int(gwas.get("total_associations")),
                "curated_gwas_significant_count": as_int(gwas.get("genome_wide_significant_associations")),
                "curated_gwas_focus_count": as_int(gwas.get("focus_associations")),
                "curated_publication_count": publication_count_by_variant.get(key, 0),
                **classification,
                "curation_pipeline_version": PIPELINE_VERSION,
                "curation_timestamp": utc_now(),
            }
        )
    curated_by_key = {clean(row.get("variant_key")): row for row in curated_rows}

    registry_rows: list[dict] = []
    for key, normalized in normalized_by_key.items():
        curated = curated_by_key.get(key)
        representative = {**normalized, **representative_match.get(key, {})}
        registry_rows.append(
            {
                **public_fact_fields(representative),
                "physical_evidence_id": physical_evidence_id(key),
                "is_enrichment_universe": "true" if curated else "false",
                "is_triage_universe": "true" if key in triage_variant_keys else "false",
                "identity_status": clean(curated.get("identity_status")) if curated else "not_assessed",
                "functional_annotation_status": clean(curated.get("functional_annotation_status")) if curated else "local_classification_only",
                "source_evidence_status": clean(curated.get("source_evidence_status")) if curated else "not_queried",
                "curation_depth": clean(curated.get("curation_depth")) if curated else "functional_only",
                "downstream_role": clean(curated.get("downstream_role")) if curated else "background_observed",
                "evidence_matrix_available": "true" if curated else "false",
            }
        )
    registry_keys = {clean(row.get("variant_key")) for row in registry_rows}

    projection_rows: list[dict] = []
    projection_fields = [
        "variant_gene_module_id",
        "gene_id",
        "canon_row_id",
        "module_id",
        "module_name",
        "system_within_module",
        "tier",
        "module_status",
        "is_draft",
        "evidence_tier",
        "approved_symbol",
        "full_gene_name",
        "variant_key",
        "chrom_vcf",
        "pos_vcf",
        "ref_vcf",
        "alt_vcf",
        "gt_raw",
        "gt_alleles",
        "zygosity",
        "qual_vcf",
        "filter_vcf",
        "quality_flag",
        "local_region_class",
        "local_feature_priority",
        "annotation_needed",
        "background_only",
        "overlap_feature_types",
    ]
    for row in match_rows:
        key = clean(row.get("variant_key"))
        triaged = projection_key(row) in triage_keys
        curated = curated_by_key.get(key)
        triage_audit = triage_audit_by_projection.get(projection_key(row), {})
        projection_rows.append(
            {
                **{field: clean(row.get(field)) for field in projection_fields},
                "physical_evidence_id": physical_evidence_id(key),
                "evidence_matrix_available": "true" if curated else "false",
                "triage_decision": clean(triage_audit.get("triage_decision")) or (
                    "include_ai" if triaged else "exclude_background_or_optional"
                ),
                "triage_reason": clean(triage_audit.get("triage_reason")) or "triage_audit_not_available",
                "triage_universe": "true" if triaged else "false",
                "curation_depth": clean(curated.get("curation_depth")) if curated else "functional_only",
                "downstream_role": clean(curated.get("downstream_role")) if triaged and curated else "background_observed",
                "source_evidence_status": clean(curated.get("source_evidence_status")) if curated else "not_queried",
            }
        )

    matched_counts = Counter((clean(row.get("approved_symbol")), clean(row.get("module_id"))) for row in match_rows)
    triage_counts = Counter((clean(row.get("approved_symbol")), clean(row.get("module_id"))) for row in triage_rows)
    enriched_counts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in triage_rows:
        key = clean(row.get("variant_key"))
        if key in curated_by_key:
            enriched_counts[(clean(row.get("approved_symbol")), clean(row.get("module_id")))].add(key)
    canonical_rows: list[dict] = []
    for canon in canon_rows:
        gene = clean(canon.get("gene_symbol_normalized") or canon.get("gene_symbol_original"))
        module_id = clean(canon.get("module_id"))
        count = matched_counts[(gene, module_id)]
        non_gene = clean(canon.get("row_status")) == "non_gene_module" or not gene
        observed_status = "not_assessed" if non_gene else "observed_alt" if count else "not_observed"
        canonical_rows.append(
            {
                **canon,
                "approved_symbol": gene,
                "observed_status": observed_status,
                "present": "Y" if count else "N",
                "callable": "unknown",
                "callability_reason": "sparse_vcf_does_not_establish_hom_ref_or_not_callable",
                "matched_gene_module_rows": count,
                "triage_gene_module_rows": triage_counts[(gene, module_id)],
                "enriched_physical_variants": len(enriched_counts[(gene, module_id)]),
                "snv_indel_assessment": "assessed_from_observed_vcf_records" if not non_gene else "not_assessed",
                "cnv_assessment": "not_assessed",
                "vntr_assessment": "not_assessed",
                "genome_build": clean(payload.get("assembly") or "GRCh38"),
                "curation_pipeline_version": PIPELINE_VERSION,
            }
        )

    write_progress(
        output_dir,
        substage="materializing_contracts",
        processed=94,
        total=100,
        unit="percent",
        message="Materializing curated physical, registry, module and canon contracts",
        metrics={"physical": len(curated_rows), "registry": len(registry_rows), "module_projection": len(projection_rows)},
    )
    curated_matrix_path = output_dir / "v2_curated_physical_variant_matrix.csv"
    registry_path = output_dir / "v2_curated_physical_variant_registry.csv"
    projection_path = output_dir / "v2_curated_gene_module_projection.csv"
    canonical_path = output_dir / "v2_canonical_gene_module_status.csv"
    clinvar_aggregate_path = output_dir / "clinvar_variant_aggregate.csv"
    clinvar_assertions_path = output_dir / "clinvar_submitter_assertions.csv"
    clinpgx_clinical_path = output_dir / "clinpgx_clinical_annotations.csv"
    clinpgx_annotations_path = output_dir / "clinpgx_variant_annotations.csv"
    gwas_path = output_dir / "gwas_variant_associations.csv"
    publications_path = output_dir / "publication_evidence.csv"
    retry_path = output_dir / "evidence_refinement_retry_queue.jsonl"
    summary_path = output_dir / "evidence_refinement_summary.json"
    write_csv(curated_matrix_path, curated_rows)
    write_csv(registry_path, registry_rows)
    write_csv(projection_path, projection_rows)
    write_csv(canonical_path, canonical_rows)
    write_csv(clinvar_aggregate_path, clinvar_aggregate_rows)
    write_csv(clinvar_assertions_path, clinvar_assertion_rows, field_order(clinvar_assertion_rows) or ["variant_key"])
    write_csv(clinpgx_clinical_path, clinpgx_clinical_rows, field_order(clinpgx_clinical_rows) or ["variant_key"])
    write_csv(clinpgx_annotations_path, clinpgx_annotation_rows, field_order(clinpgx_annotation_rows) or ["variant_key"])
    write_csv(gwas_path, gwas_rows, field_order(gwas_rows) or ["variant_key"])
    write_csv(publications_path, publication_rows, field_order(publication_rows) or ["pmid"])
    with retry_path.open("w", encoding="utf-8") as handle:
        if retry_rows:
            for row in retry_rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        else:
            handle.write(json.dumps({"record_type": "summary", "pending_retries": 0}, ensure_ascii=True) + "\n")

    projection_orphans = sum(1 for row in projection_rows if clean(row.get("variant_key")) not in registry_keys)
    role_counts = Counter(clean(row.get("downstream_role")) for row in curated_rows)
    depth_counts = Counter(clean(row.get("curation_depth")) for row in curated_rows)
    evidence_counts = Counter(clean(row.get("source_evidence_status")) for row in curated_rows)
    conservation_gate = {
        "status": "pass"
        if len(curated_rows) == len(physical_rows)
        and len({clean(row.get("variant_key")) for row in curated_rows}) == len(curated_rows)
        and normalized_contract_available
        and len(registry_rows) == len(normalized_rows)
        and normalized_duplicate_count == 0
        and triage_audit_contract_available
        and len(triage_audit_keys) == len(match_projection_keys)
        and set(triage_audit_keys) == set(match_projection_keys)
        and len(projection_rows) == len(match_rows)
        and projection_orphans == 0
        and len(canonical_rows) == len(canon_rows)
        else "fail",
        "physicalMatrixRows": len(curated_rows),
        "physicalRegistryRows": len(registry_rows),
        "normalizedContractAvailable": normalized_contract_available,
        "normalizedInputRows": len(normalized_rows),
        "normalizedDuplicateVariantKeys": normalized_duplicate_count,
        "triageAuditContractAvailable": triage_audit_contract_available,
        "triageAuditRows": len(triage_audit_keys),
        "moduleProjectionRows": len(projection_rows),
        "moduleProjectionExpectedRows": len(match_rows),
        "projectionOrphans": projection_orphans,
        "canonicalRows": len(canonical_rows),
        "canonicalExpectedRows": len(canon_rows),
    }
    attribution_gate = {
        "status": "pass"
        if not any(as_bool(row.get("focus_eligible")) and row.get("effect_allele_match") != "exact_observed_alt" for row in gwas_rows)
        and not any(
            row.get("downstream_role") == "focus_candidate"
            and row.get("identity_status") not in {"exact", "normalized_indel"}
            for row in curated_rows
        )
        else "fail",
        "gwasFocusAssociations": sum(as_bool(row.get("focus_eligible")) for row in gwas_rows),
        "clinvarIdentityMismatches": sum(row.get("identity_match") == "false" for row in clinvar_assertion_rows),
    }
    summary = {
        "status": "valid" if conservation_gate["status"] == "pass" and attribution_gate["status"] == "pass" else "warning",
        "schemaVersion": "gene_module_v2",
        "adapter": "multisource_evidence_refinement",
        "pipelineVersion": PIPELINE_VERSION,
        "analysisMode": analysis_mode,
        "startedAt": started_at,
        "completedAt": utc_now(),
        "elapsedSeconds": round(time.perf_counter() - started_clock, 3),
        "counts": {
            "normalizedPhysicalRows": len(normalized_rows),
            "normalizedPhysicalUniqueVariants": len(normalized_by_key),
            "physicalRegistryRows": len(registry_rows),
            "matchedPhysicalRegistryRows": len(registry_rows),
            "variantGeneModuleRows": len(match_rows),
            "triageGeneModuleRows": len(triage_rows),
            "enrichedPhysicalVariants": len(physical_rows),
            "deepCuratedVariants": depth_counts["deep_curated"],
            "structuredSummaryVariants": depth_counts["structured_summary"],
            "functionalOnlyVariants": depth_counts["functional_only"],
            "unresolvedVariants": depth_counts["unresolved"],
            "benignContextVariants": role_counts["benign_context"],
            "annotationAbsentVariants": role_counts["annotation_absent_context"],
            "focusCandidateVariants": role_counts["focus_candidate"],
            "retryQueueRows": len(retry_rows),
            "uniquePublications": len(publication_rows),
        },
        "sourceCounts": {
            "clinvar": {
                "aggregateRows": len(clinvar_aggregate_rows),
                "assertions": len(clinvar_assertion_rows),
                "benignVariants": sum(row.get("normalized_classification") == "benign_or_likely_benign" for row in clinvar_aggregate_rows),
                "nonBenignVariants": sum(row.get("normalized_classification") in NON_BENIGN_CLINVAR_CLASSES for row in clinvar_aggregate_rows),
            },
            "clinpgx": {
                "clinicalAnnotations": len(clinpgx_clinical_rows),
                "compatibleClinicalAnnotations": sum(row.get("allele_match_status") in {"compatible_observed_genotype", "not_specified"} for row in clinpgx_clinical_rows),
                "variantAnnotations": len(clinpgx_annotation_rows),
            },
            "gwas": {
                "associations": len(gwas_rows),
                "significantAssociations": sum(as_bool(row.get("is_genome_wide_significant")) for row in gwas_rows),
                "focusAssociations": sum(as_bool(row.get("focus_eligible")) for row in gwas_rows),
            },
        },
        "downstreamRoleCounts": dict(role_counts),
        "curationDepthCounts": dict(depth_counts),
        "sourceEvidenceStatusCounts": dict(evidence_counts),
        "network": {
            "cacheHits": dict(client.cache_hits),
            "networkCalls": dict(client.network_calls),
            "retries": dict(client.retries),
            "openCircuits": sorted(client.open_circuits),
        },
        "gates": {
            "conservationGate": conservation_gate,
            "attributionGate": attribution_gate,
            "publicationGate": {"status": "pass" if not any(row.get("source") == "pubmed" for row in retry_rows) else "review_required"},
            "llm1PilotReady": False,
            "professionalReviewRequired": True,
        },
        "decision": "technical_pass_professional_review_required"
        if conservation_gate["status"] == "pass" and attribution_gate["status"] == "pass"
        else "block_downstream_contract_failure",
        "outputs": {
            "curatedPhysicalMatrixCsv": str(curated_matrix_path),
            "curatedPhysicalRegistryCsv": str(registry_path),
            "curatedGeneModuleProjectionCsv": str(projection_path),
            "canonicalGeneModuleStatusCsv": str(canonical_path),
            "clinvarVariantAggregateCsv": str(clinvar_aggregate_path),
            "clinvarSubmitterAssertionsCsv": str(clinvar_assertions_path),
            "clinpgxClinicalAnnotationsCsv": str(clinpgx_clinical_path),
            "clinpgxVariantAnnotationsCsv": str(clinpgx_annotations_path),
            "gwasVariantAssociationsCsv": str(gwas_path),
            "publicationEvidenceCsv": str(publications_path),
            "evidenceRefinementRawJsonlGz": str(raw_path),
            "evidenceRefinementRetryQueueJsonl": str(retry_path),
            "evidenceRefinementSummaryJson": str(summary_path),
        },
    }
    write_json(summary_path, summary)
    write_progress(
        output_dir,
        substage="complete",
        processed=100,
        total=100,
        unit="percent",
        message="Multisource evidence refinement completed; professional review remains required",
        metrics={"counts": summary["counts"], "gates": summary["gates"]},
    )
    cache.close()
    print(json.dumps(summary, ensure_ascii=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    process(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=True))
        raise
