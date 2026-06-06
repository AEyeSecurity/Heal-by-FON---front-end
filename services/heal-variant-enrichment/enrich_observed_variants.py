#!/usr/bin/env python3
"""Enrich observed HEAL variants with public external variant sources."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 18
DEFAULT_CACHE_TTL_DAYS = 14
CACHE_SCHEMA_VERSION = 4
USER_AGENT = "HEAL-by-FON-prototype/0.1"


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


def clean_str(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def normalize_rsid(value) -> str:
    text = clean_str(value).lower()
    return text if text.startswith("rs") and text[2:].isdigit() else ""


def json_get(url: str, timeout_seconds: int) -> tuple[dict | list | None, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload), ""
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        return None, f"http_{error.code}: {detail}"
    except Exception as error:  # noqa: BLE001 - external APIs must not stop the pipeline.
        return None, str(error)


def unique_join(values, limit: int = 8, sep: str = "|") -> str:
    out = []
    for value in values:
        text = clean_str(value)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return sep.join(out)


def compact_json(obj, max_len: int = 12000) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(obj)
    if len(text) > max_len:
        return f"{text[:max_len]} ...[TRUNCATED]"
    return text


def split_alleles(value: str) -> list[str]:
    text = clean_str(value)
    if not text:
        return []
    parts = []
    for separator in ["/", ",", "|"]:
        text = text.replace(separator, "/")
    for part in text.split("/"):
        part = part.strip()
        if part:
            parts.append(part)
    return parts


def first_present(*values) -> str:
    for value in values:
        text = clean_str(value)
        if text:
            return text
    return ""


def observed_alt_alleles(row: dict) -> str:
    patient_ref = first_present(row.get("ref_vcf"), row.get("ref"))
    gt_alleles = first_present(row.get("gt_alleles"), row.get("Genotype"))
    alleles = split_alleles(gt_alleles)
    if not alleles:
        return ""
    if not patient_ref:
        return ",".join(sorted(set(alleles)))
    alts = sorted({allele for allele in alleles if allele != patient_ref})
    return ",".join(alts)


def cache_path(cache_dir: Path, rsid: str) -> Path:
    return cache_dir / f"{rsid}.json"


def load_cache(cache_dir: Path, rsid: str, ttl_days: int) -> dict | None:
    path = cache_path(cache_dir, rsid)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = payload.get("fetchedAt")
    if not fetched_at:
        return None
    if int(payload.get("schemaVersion") or 1) != CACHE_SCHEMA_VERSION:
        return None
    if payload.get("errors"):
        return None
    try:
        fetched = dt.datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.datetime.now(dt.UTC) - fetched > dt.timedelta(days=ttl_days):
        return None
    payload["cacheHit"] = True
    return payload


def save_cache(cache_dir: Path, rsid: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, rsid).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_ensembl_variation(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    params = urllib.parse.urlencode({"content-type": "application/json", "phenotypes": "1", "pops": "1"})
    url = f"https://rest.ensembl.org/variation/human/{urllib.parse.quote(rsid)}?{params}"
    payload, error = json_get(url, timeout_seconds)
    if not isinstance(payload, dict):
        return {}, error or "empty_response"
    mappings = payload.get("mappings") or []
    first_mapping = mappings[0] if mappings else {}
    phenotypes = payload.get("phenotypes") or []
    phenotype_parts = []
    for phenotype in phenotypes[:8]:
        pieces = []
        for key in ["trait", "description", "source", "study"]:
            if clean_str(phenotype.get(key)):
                pieces.append(f"{key}={phenotype.get(key)}")
        if pieces:
            phenotype_parts.append("; ".join(pieces))
    populations = payload.get("populations") or []
    population_parts = []
    for population in populations[:12]:
        name = clean_str(population.get("population") or population.get("name"))
        allele = clean_str(population.get("allele"))
        frequency = clean_str(population.get("frequency"))
        if name or allele or frequency:
            population_parts.append(f"pop={name}; allele={allele}; freq={frequency}")
    return {
        "var_class": clean_str(payload.get("var_class")),
        "most_severe_consequence": clean_str(payload.get("most_severe_consequence")),
        "clinical_significance": unique_join(payload.get("clinical_significance") or []),
        "evidence": unique_join(payload.get("evidence") or []),
        "phenotypes": unique_join(phenotype_parts, limit=8),
        "populations": unique_join(population_parts, limit=12),
        "minor_allele": clean_str(payload.get("minor_allele")),
        "maf": clean_str(payload.get("MAF") or payload.get("minor_allele_freq")),
        "source": clean_str(payload.get("source")),
        "mapping_assembly": clean_str(first_mapping.get("assembly_name")),
        "mapping_location": clean_str(first_mapping.get("location")),
        "mapping_allele_string": clean_str(first_mapping.get("allele_string")),
        "mappings_summary": unique_join(
            [
                f"{clean_str(item.get('assembly_name'))}:{clean_str(item.get('seq_region_name'))}:{clean_str(item.get('start'))}-{clean_str(item.get('end'))}:{clean_str(item.get('allele_string'))}"
                for item in mappings[:8]
            ],
            limit=8,
        ),
        "raw_json": compact_json(payload),
    }, ""


def fetch_ensembl_vep(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    url = f"https://rest.ensembl.org/vep/human/id/{urllib.parse.quote(rsid)}?content-type=application/json"
    payload, error = json_get(url, timeout_seconds)
    if not isinstance(payload, list) or not payload:
        return {}, error or "empty_response"
    item = payload[0]
    transcript_consequences = item.get("transcript_consequences") or []
    colocated_variants = item.get("colocated_variants") or []
    gene_symbols = [entry.get("gene_symbol") for entry in transcript_consequences]
    impacts = [entry.get("impact") for entry in transcript_consequences]
    consequence_terms = []
    transcript_summary = []
    for entry in transcript_consequences:
        consequence_terms.extend(entry.get("consequence_terms") or [])
        transcript_summary.append(
            "; ".join(
                part
                for part in [
                    f"gene={clean_str(entry.get('gene_symbol'))}" if clean_str(entry.get("gene_symbol")) else "",
                    f"tx={clean_str(entry.get('transcript_id'))}" if clean_str(entry.get("transcript_id")) else "",
                    f"consequence={unique_join(entry.get('consequence_terms') or [], limit=6)}"
                    if entry.get("consequence_terms")
                    else "",
                    f"impact={clean_str(entry.get('impact'))}" if clean_str(entry.get("impact")) else "",
                    f"biotype={clean_str(entry.get('biotype'))}" if clean_str(entry.get("biotype")) else "",
                    f"sift={clean_str(entry.get('sift_prediction'))}" if clean_str(entry.get("sift_prediction")) else "",
                    f"sift_score={clean_str(entry.get('sift_score'))}" if clean_str(entry.get("sift_score")) else "",
                    f"polyphen={clean_str(entry.get('polyphen_prediction'))}"
                    if clean_str(entry.get("polyphen_prediction"))
                    else "",
                    f"polyphen_score={clean_str(entry.get('polyphen_score'))}" if clean_str(entry.get("polyphen_score")) else "",
                    f"amino_acids={clean_str(entry.get('amino_acids'))}" if clean_str(entry.get("amino_acids")) else "",
                    f"protein_start={clean_str(entry.get('protein_start'))}" if clean_str(entry.get("protein_start")) else "",
                    f"protein_end={clean_str(entry.get('protein_end'))}" if clean_str(entry.get("protein_end")) else "",
                ]
                if part
            )
        )
    colocated_summary = []
    for entry in colocated_variants[:10]:
        colocated_summary.append(
            "; ".join(
                part
                for part in [
                    f"id={clean_str(entry.get('id'))}" if clean_str(entry.get("id")) else "",
                    f"alleles={clean_str(entry.get('allele_string'))}" if clean_str(entry.get("allele_string")) else "",
                    f"minor={clean_str(entry.get('minor_allele'))}" if clean_str(entry.get("minor_allele")) else "",
                    f"maf={clean_str(entry.get('minor_allele_freq'))}" if clean_str(entry.get("minor_allele_freq")) else "",
                ]
                if part
            )
        )
    return {
        "most_severe_consequence": clean_str(item.get("most_severe_consequence")),
        "variant_class": clean_str(item.get("variant_class")),
        "gene_symbols": unique_join(gene_symbols),
        "impacts": unique_join(impacts),
        "consequence_terms": unique_join(consequence_terms, limit=12),
        "transcript_summary": unique_join(transcript_summary, limit=10),
        "colocated_variants": unique_join(colocated_summary, limit=10),
        "raw_json": compact_json(payload),
    }, ""


def fetch_myvariant(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    params = urllib.parse.urlencode(
        {
            "q": rsid,
            "scopes": "dbsnp.rsid",
            "fields": "dbsnp,clinvar,cadd,gnomad,dbnsfp,snpeff,_id,_score",
            "size": "3",
        }
    )
    payload, error = json_get(f"https://myvariant.info/v1/query?{params}", timeout_seconds)
    if not isinstance(payload, dict):
        return {}, error or "empty_response"
    hits = payload.get("hits") or []
    first = hits[0] if hits else {}
    clinvar = first.get("clinvar") or {}
    cadd = first.get("cadd") or {}
    dbsnp = first.get("dbsnp") or {}
    dbnsfp = first.get("dbnsfp") or {}
    if isinstance(clinvar.get("rcv"), list):
        rcv = clinvar.get("rcv")[0] if clinvar.get("rcv") else {}
    else:
        rcv = clinvar.get("rcv") or {}
    return {
        "hits": clean_str(len(hits)),
        "total_hits": clean_str(payload.get("total")),
        "best_id": clean_str(first.get("_id")) if isinstance(first, dict) else "",
        "best_score": clean_str(first.get("_score")) if isinstance(first, dict) else "",
        "top_level_fields": unique_join(list(first.keys()) if isinstance(first, dict) else [], limit=40, sep=" | "),
        "clinvar_significance": clean_str(rcv.get("clinical_significance") or clinvar.get("clinical_significance")),
        "clinvar_review_status": clean_str(rcv.get("review_status") or clinvar.get("review_status")),
        "cadd_phred": clean_str(cadd.get("phred")),
        "dbsnp_gene": clean_str(dbsnp.get("gene") or dbsnp.get("genes")),
        "dbsnp_alleles": clean_str(dbsnp.get("alleles")),
        "dbnsfp_gene_name": clean_str(dbnsfp.get("genename")),
        "raw_json": compact_json(payload),
    }, ""


def fetch_clinvar(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    payload = {}
    ids = []
    error = ""
    for term_text in [f'"{rsid}"[Variant Name]', rsid]:
        term = urllib.parse.quote(term_text)
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&retmode=json&retmax=5&tool=heal_fon_service&term={term}"
        payload, error = json_get(url, timeout_seconds)
        if not isinstance(payload, dict):
            return {}, error or "empty_response"
        result = payload.get("esearchresult") or {}
        ids = result.get("idlist") or []
        if ids:
            break
        time.sleep(0.36)
    summary_payload = {}
    if ids:
        ids_param = urllib.parse.quote(",".join(ids[:3]))
        summary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=clinvar&retmode=json&tool=heal_fon_service&id={ids_param}"
        )
        summary_payload, summary_error = json_get(summary_url, timeout_seconds)
        if summary_error:
            return {
                "count": clean_str(len(ids)),
                "ids": unique_join(ids, limit=5, sep=","),
                "summary_error": summary_error,
                "esearch_raw_json": compact_json(payload),
            }, ""
    summary_items = []
    if isinstance(summary_payload, dict):
        summary_result = summary_payload.get("result") or {}
        for clinvar_id in ids[:5]:
            item = summary_result.get(str(clinvar_id)) or {}
            if not isinstance(item, dict):
                continue
            summary_items.append(item)
    accessions = []
    record_types = []
    for item in summary_items:
        accession = item.get("accession")
        if isinstance(accession, dict):
            accession = accession.get("accession")
        accessions.append(accession)
        record_types.append(item.get("record_type") or item.get("recordtype"))
    return {
        "count": clean_str(len(ids)),
        "ids": unique_join(ids, limit=5, sep=","),
        "titles": unique_join([item.get("title") for item in summary_items], limit=3, sep=" || "),
        "accessions": unique_join(accessions, limit=3, sep=" | "),
        "clinical_significance": unique_join(
            [clinvar_classification(item) for item in summary_items],
            limit=8,
            sep=" | ",
        ),
        "review_status": unique_join(
            [clinvar_review_status(item) for item in summary_items],
            limit=8,
            sep=" | ",
        ),
        "record_types": unique_join(record_types, limit=5, sep=" | "),
        "trait_names": unique_join(
            [clinvar_trait_name(item) for item in summary_items],
            limit=8,
        ),
        "esearch_raw_json": compact_json(payload),
        "esummary_raw_json": compact_json(summary_payload),
    }, ""


def fetch_external(rsid: str, cache_dir: Path, timeout_seconds: int, ttl_days: int, delay_seconds: float) -> dict:
    cached = load_cache(cache_dir, rsid, ttl_days)
    if cached:
        return cached

    errors = {}
    ensembl_variation, error = fetch_ensembl_variation(rsid, timeout_seconds)
    if error:
        errors["ensembl_variation"] = error
    time.sleep(delay_seconds)
    ensembl_vep, error = fetch_ensembl_vep(rsid, timeout_seconds)
    if error:
        errors["ensembl_vep"] = error
    time.sleep(delay_seconds)
    myvariant, error = fetch_myvariant(rsid, timeout_seconds)
    if error:
        errors["myvariant"] = error
    time.sleep(delay_seconds)
    clinvar, error = fetch_clinvar(rsid, timeout_seconds)
    if error:
        errors["clinvar"] = error

    payload = {
        "rsid": rsid,
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "fetchedAt": utc_now(),
        "cacheHit": False,
        "errors": errors,
        "ensemblVariation": ensembl_variation,
        "ensemblVep": ensembl_vep,
        "myVariant": myvariant,
        "clinVar": clinvar,
    }
    if not errors:
        save_cache(cache_dir, rsid, payload)
    return payload


def allele_match_summary(row: dict, enrichment: dict) -> str:
    observed = set(split_alleles(observed_alt_alleles(row)))
    catalog = set(split_alleles(first_present(row.get("alt_vcf"), row.get("alt"))))
    ensembl_alleles = set(split_alleles((enrichment.get("ensemblVariation") or {}).get("mapping_allele_string")))
    if not observed:
        return "no_observed_alt_allele"
    if catalog and observed.intersection(catalog):
        return "observed_alt_overlaps_vcf_alt_catalog"
    if ensembl_alleles and observed.intersection(ensembl_alleles):
        return "observed_alt_overlaps_ensembl_mapping"
    return "observed_alt_not_confirmed_against_catalogs"


def external_support_summary(enrichment: dict) -> str:
    support = []
    errors = enrichment.get("errors") or {}
    ensembl_variation = enrichment.get("ensemblVariation") or {}
    ensembl_vep = enrichment.get("ensemblVep") or {}
    myvariant = enrichment.get("myVariant") or {}
    clinvar = enrichment.get("clinVar") or {}
    if ensembl_variation.get("clinical_significance") or ensembl_variation.get("phenotypes"):
        support.append("Ensembl variation clinical/phenotype signals")
    if ensembl_vep.get("most_severe_consequence") or ensembl_vep.get("gene_symbols"):
        support.append("Ensembl VEP consequence signals")
    if clinvar.get("count") and clinvar.get("count") != "0":
        support.append("ClinVar records found")
    if myvariant.get("hits") and myvariant.get("hits") != "0":
        support.append("MyVariant records found")
    if errors:
        support.append(f"Source errors: {','.join(sorted(errors.keys()))}")
    return " | ".join(support)


def colab_allele_match_summary(row: dict) -> str:
    observed = split_alleles(observed_alt_alleles(row))
    if not observed:
        return "no_observed_alt_allele_detected"

    known_alt = split_alleles(first_present(row.get("alt_vcf"), row.get("alt")))
    if known_alt:
        observed_set = set(observed)
        known_set = set(known_alt)
        if observed_set.issubset(known_set):
            return "observed_patient_alt_within_internal_alt_catalog"
        if observed_set.intersection(known_set):
            return "partial_overlap_with_internal_alt_catalog"
        return "observed_patient_alt_not_in_internal_alt_catalog"

    return "internal_alt_catalog_missing"


def colab_external_support_summary(row: dict) -> str:
    parts = []
    checks = [
        ("vep_most_severe_consequence", "VEP most severe consequence"),
        ("ensembl_var_class", "Ensembl variant class"),
        ("ensembl_clin_sig", "Ensembl clinical significance"),
        ("clinvar_germline_classification", "ClinVar classification"),
        ("clinvar_review_status", "ClinVar review status"),
        ("ensembl_phenotypes", "Ensembl phenotypes"),
        ("vep_transcript_summary", "VEP transcript summary"),
        ("myvariant_top_level_fields", "MyVariant fields present"),
    ]
    for key, label in checks:
        value = clean_str(row.get(key))
        if value:
            parts.append(f"{label}: {value}")
    return " || ".join(parts)


def display_pipe_list(value: str) -> str:
    text = clean_str(value)
    if not text:
        return ""
    return " | ".join(part.strip() for part in text.split("|") if part.strip())


def clinvar_classification(item: dict) -> str:
    direct = clean_str(item.get("clinical_significance"))
    if direct:
        return direct
    germline = item.get("germline_classification")
    if isinstance(germline, dict):
        return clean_str(germline.get("description"))
    return ""


def clinvar_review_status(item: dict) -> str:
    direct = clean_str(item.get("review_status"))
    if direct:
        return direct
    germline = item.get("germline_classification")
    if isinstance(germline, dict):
        return clean_str(germline.get("review_status"))
    return ""


def clinvar_trait_name(item: dict) -> str:
    trait_set = item.get("trait_set")
    if isinstance(trait_set, list) and trait_set:
        first = trait_set[0]
        if isinstance(first, dict):
            return clean_str(first.get("trait_name"))
    return ""


def build_output_row(row: dict, enrichment: dict) -> dict:
    ensembl_variation = enrichment.get("ensemblVariation") or {}
    ensembl_vep = enrichment.get("ensemblVep") or {}
    myvariant = enrichment.get("myVariant") or {}
    clinvar = enrichment.get("clinVar") or {}
    errors = enrichment.get("errors") or {}
    return {
        "Gene": clean_str(row.get("Gene")),
        "SNP (rsID)": normalize_rsid(row.get("SNP (rsID)")),
        "Genotype": clean_str(row.get("Genotype")),
        "Zygosity": clean_str(row.get("Zygosity")),
        "Ref/Alt": clean_str(row.get("Ref/Alt")),
        "canon_effect": first_present(row.get("Canon Effect"), row.get("effect")),
        "patient_ref": first_present(row.get("ref_vcf"), row.get("ref")),
        "patient_alt_catalog": first_present(row.get("alt_vcf"), row.get("alt")),
        "patient_observed_alt_alleles": observed_alt_alleles(row),
        "allele_match_summary": allele_match_summary(row, enrichment),
        "external_support_summary": external_support_summary(enrichment),
        "Confidence Level": clean_str(row.get("Confidence Level")),
        "Category / Module": clean_str(row.get("Category / Module")),
        "Review Status": clean_str(row.get("Review Status")),
        "match_status": clean_str(row.get("match_status")),
        "source_group": clean_str(row.get("source_group")),
        "row_id": clean_str(row.get("row_id")),
        "ref_vcf": clean_str(row.get("ref_vcf")),
        "alt_vcf": clean_str(row.get("alt_vcf")),
        "gt_raw": clean_str(row.get("gt_raw")),
        "ensembl_most_severe_consequence": first_present(
            ensembl_vep.get("most_severe_consequence"),
            ensembl_variation.get("most_severe_consequence"),
        ),
        "ensembl_variant_class": clean_str(ensembl_vep.get("variant_class") or ensembl_variation.get("var_class")),
        "ensembl_gene_symbols": clean_str(ensembl_vep.get("gene_symbols")),
        "ensembl_consequence_terms": clean_str(ensembl_vep.get("consequence_terms")),
        "ensembl_impacts": clean_str(ensembl_vep.get("impacts")),
        "ensembl_clinical_significance": clean_str(ensembl_variation.get("clinical_significance")),
        "ensembl_evidence": clean_str(ensembl_variation.get("evidence")),
        "ensembl_phenotypes": clean_str(ensembl_variation.get("phenotypes")),
        "ensembl_populations": clean_str(ensembl_variation.get("populations")),
        "ensembl_minor_allele": clean_str(ensembl_variation.get("minor_allele")),
        "ensembl_maf": clean_str(ensembl_variation.get("maf")),
        "ensembl_mapping_location": clean_str(ensembl_variation.get("mapping_location")),
        "ensembl_mapping_allele_string": clean_str(ensembl_variation.get("mapping_allele_string")),
        "ensembl_mappings_summary": clean_str(ensembl_variation.get("mappings_summary")),
        "ensembl_transcript_summary": clean_str(ensembl_vep.get("transcript_summary")),
        "ensembl_colocated_variants": clean_str(ensembl_vep.get("colocated_variants")),
        "clinvar_count": clean_str(clinvar.get("count")),
        "clinvar_ids": clean_str(clinvar.get("ids")),
        "clinvar_titles": clean_str(clinvar.get("titles")),
        "clinvar_clinical_significance": clean_str(clinvar.get("clinical_significance")),
        "clinvar_review_status": clean_str(clinvar.get("review_status")),
        "clinvar_trait_names": clean_str(clinvar.get("trait_names")),
        "myvariant_hits": clean_str(myvariant.get("hits")),
        "myvariant_best_id": clean_str(myvariant.get("best_id")),
        "myvariant_best_score": clean_str(myvariant.get("best_score")),
        "myvariant_top_level_fields": clean_str(myvariant.get("top_level_fields")),
        "myvariant_clinvar_significance": clean_str(myvariant.get("clinvar_significance")),
        "myvariant_clinvar_review_status": clean_str(myvariant.get("clinvar_review_status")),
        "myvariant_cadd_phred": clean_str(myvariant.get("cadd_phred")),
        "myvariant_dbsnp_gene": clean_str(myvariant.get("dbsnp_gene")),
        "myvariant_dbsnp_alleles": clean_str(myvariant.get("dbsnp_alleles")),
        "myvariant_dbnsfp_gene_name": clean_str(myvariant.get("dbnsfp_gene_name")),
        "external_cache_hit": "true" if enrichment.get("cacheHit") else "false",
        "external_error_sources": "|".join(sorted(errors.keys())),
        "ensembl_variation_raw_json": clean_str(ensembl_variation.get("raw_json")),
        "ensembl_vep_raw_json": clean_str(ensembl_vep.get("raw_json")),
        "clinvar_esearch_raw_json": clean_str(clinvar.get("esearch_raw_json")),
        "clinvar_esummary_raw_json": clean_str(clinvar.get("esummary_raw_json")),
        "myvariant_raw_json": clean_str(myvariant.get("raw_json")),
    }


def build_colab_output_row(row: dict, enrichment: dict) -> dict:
    ensembl_variation = enrichment.get("ensemblVariation") or {}
    ensembl_vep = enrichment.get("ensemblVep") or {}
    myvariant = enrichment.get("myVariant") or {}
    clinvar = enrichment.get("clinVar") or {}

    base = {
        "row_id": clean_str(row.get("row_id")),
        "Gene": clean_str(row.get("Gene")),
        "SNP (rsID)": normalize_rsid(row.get("SNP (rsID)")),
        "Category / Module": clean_str(row.get("Category / Module")),
        "Canon Effect": first_present(row.get("Canon Effect"), row.get("effect")),
        "Genotype": clean_str(row.get("Genotype")),
        "gt_alleles": clean_str(row.get("gt_alleles") or row.get("Genotype")),
        "patient_gt_alleles": clean_str(row.get("gt_alleles") or row.get("Genotype")),
        "Zygosity": clean_str(row.get("Zygosity")),
        "Ref/Alt": clean_str(row.get("Ref/Alt")),
        "patient_ref": first_present(row.get("ref_vcf"), row.get("ref")),
        "patient_alt_catalog": first_present(row.get("alt_vcf"), row.get("alt")),
        "patient_observed_alt_alleles": observed_alt_alleles(row),
        "allele_match_summary": colab_allele_match_summary(row),
        "source_group": clean_str(row.get("source_group")),
        "match_status": clean_str(row.get("match_status")),
        "Confidence Level": clean_str(row.get("Confidence Level")),
        "Review Status": clean_str(row.get("Review Status")),
        "Notes": clean_str(row.get("Notes")),
        "ensembl_var_class": clean_str(ensembl_variation.get("var_class") or ensembl_vep.get("variant_class")),
        "ensembl_minor_allele": clean_str(ensembl_variation.get("minor_allele")),
        "ensembl_minor_allele_freq": clean_str(ensembl_variation.get("maf")),
        "ensembl_clin_sig": display_pipe_list(ensembl_variation.get("clinical_significance")),
        "ensembl_evidence": display_pipe_list(ensembl_variation.get("evidence")),
        "vep_most_severe_consequence": first_present(
            ensembl_vep.get("most_severe_consequence"),
            ensembl_variation.get("most_severe_consequence"),
        ),
        "vep_variant_class": clean_str(ensembl_vep.get("variant_class")),
        "clinvar_uid_count": clean_str(clinvar.get("count")),
        "clinvar_germline_classification": display_pipe_list(clinvar.get("clinical_significance")),
        "clinvar_review_status": display_pipe_list(clinvar.get("review_status")),
        "clinvar_titles": clean_str(clinvar.get("titles")),
        "myvariant_hit_count": clean_str(myvariant.get("hits")),
        "myvariant_best_id": clean_str(myvariant.get("best_id")),
        "myvariant_best_score": clean_str(myvariant.get("best_score")),
        "external_support_summary": "",
        "Interpretation (1 sentence)": clean_str(row.get("Interpretation (1 sentence)")),
        "gt_raw": clean_str(row.get("gt_raw")),
        "ref": clean_str(row.get("ref")),
        "alt": clean_str(row.get("alt")),
        "ref_vcf": clean_str(row.get("ref_vcf")),
        "alt_vcf": clean_str(row.get("alt_vcf")),
        "has_genotype": clean_str(row.get("has_genotype")),
        "found_in_vcf_by_chr_pos": clean_str(row.get("found_in_vcf_by_chr_pos")),
        "ensembl_phenotypes": clean_str(ensembl_variation.get("phenotypes")),
        "ensembl_populations": clean_str(ensembl_variation.get("populations")),
        "ensembl_mappings": clean_str(ensembl_variation.get("mappings_summary")),
        "ensembl_raw_json": clean_str(ensembl_variation.get("raw_json")),
        "vep_transcript_summary": clean_str(ensembl_vep.get("transcript_summary")),
        "vep_colocated_variants": clean_str(ensembl_vep.get("colocated_variants")),
        "vep_raw_json": clean_str(ensembl_vep.get("raw_json")),
        "clinvar_uids": clean_str(clinvar.get("ids")),
        "clinvar_accessions": clean_str(clinvar.get("accessions")),
        "clinvar_record_types": clean_str(clinvar.get("record_types")),
        "clinvar_esearch_json": clean_str(clinvar.get("esearch_raw_json")),
        "clinvar_esummary_json": clean_str(clinvar.get("esummary_raw_json")),
        "myvariant_top_level_fields": clean_str(myvariant.get("top_level_fields")),
        "myvariant_raw_json": clean_str(myvariant.get("raw_json")),
    }
    base["external_support_summary"] = colab_external_support_summary(base)
    return base


def process(input_path: Path, output_dir: Path, cache_dir: Path, timeout_seconds: int, ttl_days: int) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    observed_rows = [row for row in rows if as_bool(row.get("has_genotype")) and normalize_rsid(row.get("SNP (rsID)"))]
    unique_rsids = sorted({normalize_rsid(row.get("SNP (rsID)")) for row in observed_rows})

    enrichments = {}
    warnings = []
    for rsid in unique_rsids:
        enrichments[rsid] = fetch_external(
            rsid=rsid,
            cache_dir=cache_dir,
            timeout_seconds=timeout_seconds,
            ttl_days=ttl_days,
            delay_seconds=0.12,
        )
        if enrichments[rsid].get("errors"):
            warnings.append(f"{rsid}: {','.join(sorted(enrichments[rsid]['errors'].keys()))}")

    output_rows = [build_output_row(row, enrichments[normalize_rsid(row.get("SNP (rsID)"))]) for row in observed_rows]
    colab_output_rows = [
        build_colab_output_row(row, enrichments[normalize_rsid(row.get("SNP (rsID)"))]) for row in observed_rows
    ]
    qa_fieldnames = [
        "Gene",
        "SNP (rsID)",
        "Genotype",
        "Zygosity",
        "Ref/Alt",
        "patient_ref",
        "patient_alt_catalog",
        "patient_observed_alt_alleles",
        "allele_match_summary",
        "external_support_summary",
        "Confidence Level",
        "Category / Module",
        "canon_effect",
        "Review Status",
        "match_status",
        "source_group",
        "row_id",
        "ref_vcf",
        "alt_vcf",
        "gt_raw",
        "ensembl_most_severe_consequence",
        "ensembl_variant_class",
        "ensembl_gene_symbols",
        "ensembl_consequence_terms",
        "ensembl_impacts",
        "ensembl_clinical_significance",
        "ensembl_evidence",
        "ensembl_phenotypes",
        "ensembl_populations",
        "ensembl_minor_allele",
        "ensembl_maf",
        "ensembl_mapping_location",
        "ensembl_mapping_allele_string",
        "ensembl_mappings_summary",
        "ensembl_transcript_summary",
        "ensembl_colocated_variants",
        "clinvar_count",
        "clinvar_ids",
        "clinvar_titles",
        "clinvar_clinical_significance",
        "clinvar_review_status",
        "clinvar_trait_names",
        "myvariant_hits",
        "myvariant_best_id",
        "myvariant_best_score",
        "myvariant_top_level_fields",
        "myvariant_clinvar_significance",
        "myvariant_clinvar_review_status",
        "myvariant_cadd_phred",
        "myvariant_dbsnp_gene",
        "myvariant_dbsnp_alleles",
        "myvariant_dbnsfp_gene_name",
        "external_cache_hit",
        "external_error_sources",
        "ensembl_variation_raw_json",
        "ensembl_vep_raw_json",
        "clinvar_esearch_raw_json",
        "clinvar_esummary_raw_json",
        "myvariant_raw_json",
    ]
    colab_fieldnames = [
        "row_id",
        "Gene",
        "SNP (rsID)",
        "Category / Module",
        "Canon Effect",
        "Genotype",
        "gt_alleles",
        "patient_gt_alleles",
        "Zygosity",
        "Ref/Alt",
        "patient_ref",
        "patient_alt_catalog",
        "patient_observed_alt_alleles",
        "allele_match_summary",
        "source_group",
        "match_status",
        "Confidence Level",
        "Review Status",
        "Notes",
        "ensembl_var_class",
        "ensembl_minor_allele",
        "ensembl_minor_allele_freq",
        "ensembl_clin_sig",
        "ensembl_evidence",
        "vep_most_severe_consequence",
        "vep_variant_class",
        "clinvar_uid_count",
        "clinvar_germline_classification",
        "clinvar_review_status",
        "clinvar_titles",
        "myvariant_hit_count",
        "myvariant_best_id",
        "myvariant_best_score",
        "external_support_summary",
        "Interpretation (1 sentence)",
        "gt_raw",
        "ref",
        "alt",
        "ref_vcf",
        "alt_vcf",
        "has_genotype",
        "found_in_vcf_by_chr_pos",
        "ensembl_phenotypes",
        "ensembl_populations",
        "ensembl_mappings",
        "ensembl_raw_json",
        "vep_transcript_summary",
        "vep_colocated_variants",
        "vep_raw_json",
        "clinvar_uids",
        "clinvar_accessions",
        "clinvar_record_types",
        "clinvar_esearch_json",
        "clinvar_esummary_json",
        "myvariant_top_level_fields",
        "myvariant_raw_json",
    ]
    output_csv = output_dir / "heal_observed_variant_enrichment.csv"
    colab_output_csv = output_dir / "heal_fon_interpretation_enriched_observed69.csv"
    write_csv(output_csv, output_rows, qa_fieldnames)
    write_csv(colab_output_csv, colab_output_rows, colab_fieldnames)

    source_errors = {}
    for enrichment in enrichments.values():
        for source in (enrichment.get("errors") or {}).keys():
            source_errors[source] = source_errors.get(source, 0) + 1

    status = "valid" if not source_errors else "warning"
    if not observed_rows:
        status = "warning"
        warnings.append("No observed genotype rows were available for external enrichment.")

    summary = {
        "status": status,
        "errors": [],
        "warnings": warnings[:30],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "cacheDir": str(cache_dir),
        "metadata": {
            "source_rows": len(rows),
            "observed_rows": len(observed_rows),
            "unique_rsids": len(unique_rsids),
            "output_rows": len(output_rows),
            "cache_hits": sum(1 for item in enrichments.values() if item.get("cacheHit")),
            "source_error_counts": source_errors,
            "sources": ["Ensembl variation", "Ensembl VEP", "ClinVar E-utilities", "MyVariant.info"],
        },
        "outputs": {
            "observedVariantEnrichmentCsv": str(output_csv),
            "observedVariantInterpretiveCsv": str(colab_output_csv),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "observed_variant_enrichment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich observed HEAL variants with public external sources.")
    parser.add_argument("--input")
    parser.add_argument("--output-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--cache-ttl-days", type=int, default=DEFAULT_CACHE_TTL_DAYS)
    parser.add_argument("--input-json-base64", default="")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("deliverableAuditCsv")
        args.output_dir = payload.get("outputDir")
        args.cache_dir = payload.get("cacheDir") or args.cache_dir
    if not args.input or not args.output_dir:
        parser.error("--input and --output-dir are required.")
    if not args.cache_dir:
        args.cache_dir = r"C:\ServerCIT\services\heal-variant-enrichment\cache"
    return args


def main() -> int:
    args = parse_args()
    process(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        cache_dir=Path(args.cache_dir),
        timeout_seconds=max(3, args.timeout_seconds),
        ttl_days=max(1, args.cache_ttl_days),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
