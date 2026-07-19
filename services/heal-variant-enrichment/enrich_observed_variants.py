#!/usr/bin/env python3
"""Enrich observed HEAL variants with public external variant sources."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 18
DEFAULT_CACHE_TTL_DAYS = 14
CACHE_SCHEMA_VERSION = 5
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


def is_v2_row(row: dict) -> bool:
    return "module_id" in row and "approved_symbol" in row and "local_region_class" in row


def schema_version_for_rows(rows: list[dict]) -> str:
    if rows and is_v2_row(rows[0]):
        return "gene_module_v2"
    return "legacy_rsid_canon"


def row_rsid(row: dict) -> str:
    return normalize_rsid(first_present(row.get("SNP (rsID)"), row.get("id_vcf")))


def row_id_value(row: dict) -> str:
    return first_present(row.get("row_id"), row.get("variant_gene_module_id"), row.get("canon_row_id"))


def row_gene(row: dict) -> str:
    return first_present(row.get("Gene"), row.get("approved_symbol"), row.get("gene_symbol_original"))


def row_category(row: dict) -> str:
    return first_present(row.get("Category / Module"), row.get("module_name"), row.get("module_id"))


def row_canon_effect(row: dict) -> str:
    return first_present(row.get("Canon Effect"), row.get("effect"), row.get("local_region_class"))


def row_ref_alt(row: dict) -> str:
    ref_alt = clean_str(row.get("Ref/Alt"))
    if ref_alt:
        return ref_alt
    ref = clean_str(row.get("ref_vcf") or row.get("ref"))
    alt = clean_str(row.get("alt_vcf") or row.get("alt"))
    if ref or alt:
        return f"{ref}/{alt}".strip("/")
    return ""


def row_confidence_level(row: dict) -> str:
    confidence = clean_str(row.get("Confidence Level"))
    if confidence:
        return confidence
    if as_bool(row.get("background_only")):
        return "Low"
    annotation_needed = clean_str(row.get("annotation_needed"))
    if annotation_needed == "true":
        return "High"
    if annotation_needed == "optional":
        return "Moderate"
    return "Moderate"


def row_review_status(row: dict) -> str:
    review = clean_str(row.get("Review Status"))
    if review:
        return review
    annotation_needed = clean_str(row.get("annotation_needed"))
    if annotation_needed == "true":
        return "ready_for_annotation"
    if annotation_needed == "optional":
        return "optional_annotation"
    if as_bool(row.get("background_only")):
        return "background_only"
    return ""


def row_source_group(row: dict) -> str:
    return first_present(row.get("source_group"), row.get("module_status"))


def row_notes(row: dict) -> str:
    return first_present(row.get("Notes"), row.get("triage_reason"), row.get("notes"))


def row_found_in_vcf_by_chr_pos(row: dict) -> str:
    value = clean_str(row.get("found_in_vcf_by_chr_pos"))
    if value:
        return value
    return "true" if as_bool(row.get("has_genotype")) else "false"


def fieldnames_from_rows(rows: list[dict]) -> list[str]:
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    return fieldnames


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


def get_nested(obj, *keys):
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current


def compact_number(value) -> str:
    text = clean_str(value)
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number == 0:
        return "0"
    if abs(number) < 0.001 or abs(number) >= 100000:
        return f"{number:.3e}"
    return f"{number:.6g}"


def is_http_not_found(error: str) -> bool:
    return clean_str(error).startswith("http_404")


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
    params = urllib.parse.urlencode(
        {
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
        }
    )
    url = f"https://rest.ensembl.org/vep/human/id/{urllib.parse.quote(rsid)}?{params}"
    payload, error = json_get(url, timeout_seconds)
    if not isinstance(payload, list) or not payload:
        return {}, error or "empty_response"
    item = payload[0]
    transcript_consequences = item.get("transcript_consequences") or []
    colocated_variants = item.get("colocated_variants") or []
    canonical_entry = next((entry for entry in transcript_consequences if clean_str(entry.get("canonical")) == "1"), {})
    mane_entry = next((entry for entry in transcript_consequences if clean_str(entry.get("mane_select"))), {})
    picked_entry = mane_entry or canonical_entry or (transcript_consequences[0] if transcript_consequences else {})
    gene_symbols = [entry.get("gene_symbol") for entry in transcript_consequences]
    impacts = [entry.get("impact") for entry in transcript_consequences]
    consequence_terms = []
    transcript_summary = []
    domains_summary = []
    for entry in transcript_consequences:
        consequence_terms.extend(entry.get("consequence_terms") or [])
        domains = entry.get("domains") or []
        domains_text = unique_join(
            [
                f"{clean_str(domain.get('db'))}:{clean_str(domain.get('name'))}"
                for domain in domains
                if isinstance(domain, dict)
            ],
            limit=8,
        )
        if domains_text:
            domains_summary.append(domains_text)
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
                    f"hgvsc={clean_str(entry.get('hgvsc'))}" if clean_str(entry.get("hgvsc")) else "",
                    f"hgvsp={clean_str(entry.get('hgvsp'))}" if clean_str(entry.get("hgvsp")) else "",
                    f"cadd_phred={clean_str(entry.get('cadd_phred'))}" if clean_str(entry.get("cadd_phred")) else "",
                    f"revel={clean_str(entry.get('revel_score'))}" if clean_str(entry.get("revel_score")) else "",
                    f"alphamissense={clean_str(entry.get('alphamissense_score'))}"
                    if clean_str(entry.get("alphamissense_score"))
                    else "",
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
        "picked_gene_symbol": clean_str(picked_entry.get("gene_symbol")),
        "picked_transcript_id": clean_str(picked_entry.get("transcript_id")),
        "picked_canonical": clean_str(picked_entry.get("canonical")),
        "picked_mane_select": clean_str(picked_entry.get("mane_select")),
        "picked_hgvsc": clean_str(picked_entry.get("hgvsc")),
        "picked_hgvsp": clean_str(picked_entry.get("hgvsp")),
        "picked_protein_id": clean_str(picked_entry.get("protein_id")),
        "picked_exon": clean_str(picked_entry.get("exon")),
        "picked_intron": clean_str(picked_entry.get("intron")),
        "picked_cdna": "-".join(
            item
            for item in [clean_str(picked_entry.get("cdna_start")), clean_str(picked_entry.get("cdna_end"))]
            if item
        ),
        "picked_cds": "-".join(
            item
            for item in [clean_str(picked_entry.get("cds_start")), clean_str(picked_entry.get("cds_end"))]
            if item
        ),
        "picked_amino_acids": clean_str(picked_entry.get("amino_acids")),
        "picked_protein_position": "-".join(
            item
            for item in [clean_str(picked_entry.get("protein_start")), clean_str(picked_entry.get("protein_end"))]
            if item
        ),
        "picked_sift_prediction": clean_str(picked_entry.get("sift_prediction") or picked_entry.get("sift_pred")),
        "picked_sift_score": clean_str(picked_entry.get("sift_score")),
        "picked_polyphen_prediction": clean_str(
            picked_entry.get("polyphen_prediction") or picked_entry.get("polyphen2_hdiv_pred")
        ),
        "picked_polyphen_score": clean_str(picked_entry.get("polyphen_score")),
        "picked_cadd_phred": clean_str(picked_entry.get("cadd_phred")),
        "picked_revel_score": clean_str(picked_entry.get("revel_score") or picked_entry.get("revel")),
        "picked_alphamissense_score": clean_str(picked_entry.get("alphamissense_score") or picked_entry.get("alphamissense")),
        "picked_alphamissense_pred": clean_str(picked_entry.get("alphamissense_pred")),
        "picked_mutationtaster_pred": clean_str(picked_entry.get("mutationtaster_pred")),
        "picked_metasvm_pred": clean_str(picked_entry.get("metasvm_pred")),
        "picked_spliceai": clean_str(picked_entry.get("spliceai")),
        "picked_uniprot": unique_join(
            [
                picked_entry.get("swissprot"),
                picked_entry.get("trembl"),
                picked_entry.get("uniparc"),
                picked_entry.get("uniprot_isoform"),
            ],
            limit=8,
        ),
        "domains_summary": unique_join(domains_summary, limit=8),
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


def fetch_gwas_catalog(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    params = urllib.parse.urlencode({"projection": "associationBySnp", "size": "20"})
    url = (
        "https://www.ebi.ac.uk/gwas/rest/api/singleNucleotidePolymorphisms/"
        f"{urllib.parse.quote(rsid)}/associations?{params}"
    )
    payload, error = json_get(url, timeout_seconds)
    if not isinstance(payload, dict):
        if is_http_not_found(error):
            return {
                "association_count": "0",
                "top_traits": "",
                "reported_genes": "",
                "min_pvalue": "",
                "top_associations": "",
                "raw_json": "",
            }, ""
        return {}, error or "empty_response"
    associations = ((payload.get("_embedded") or {}).get("associations") or [])
    traits = []
    genes = []
    association_parts = []
    min_pvalue = ""
    for association in associations[:20]:
        if not isinstance(association, dict):
            continue
        pvalue = association.get("pvalue")
        if pvalue is None and association.get("pvalueMantissa") is not None and association.get("pvalueExponent") is not None:
            try:
                pvalue = float(association.get("pvalueMantissa")) * (10 ** int(association.get("pvalueExponent")))
            except Exception:
                pvalue = None
        if pvalue is not None:
            pvalue_text = compact_number(pvalue)
            if not min_pvalue:
                min_pvalue = pvalue_text
            else:
                try:
                    if float(pvalue) < float(min_pvalue):
                        min_pvalue = pvalue_text
                except Exception:
                    pass
        else:
            pvalue_text = ""
        association_traits = [item.get("trait") for item in association.get("efoTraits") or [] if isinstance(item, dict)]
        traits.extend(association_traits)
        locus_genes = []
        for locus in association.get("loci") or []:
            if not isinstance(locus, dict):
                continue
            for gene in locus.get("authorReportedGenes") or []:
                if isinstance(gene, dict):
                    locus_genes.append(gene.get("geneName"))
        genes.extend(locus_genes)
        effect = first_present(
            f"beta={compact_number(association.get('betaNum'))} {clean_str(association.get('betaDirection'))}".strip()
            if association.get("betaNum") is not None
            else "",
            f"OR={compact_number(association.get('orPerCopyNum'))}" if association.get("orPerCopyNum") is not None else "",
        )
        association_parts.append(
            "; ".join(
                part
                for part in [
                    f"trait={unique_join(association_traits, limit=3, sep=',')}" if association_traits else "",
                    f"p={pvalue_text}" if pvalue_text else "",
                    effect,
                    f"genes={unique_join(locus_genes, limit=4, sep=',')}" if locus_genes else "",
                ]
                if part
            )
        )
    return {
        "association_count": clean_str(len(associations)),
        "top_traits": unique_join(traits, limit=12, sep=" | "),
        "reported_genes": unique_join(genes, limit=12, sep=" | "),
        "min_pvalue": min_pvalue,
        "top_associations": unique_join(association_parts, limit=8, sep=" || "),
        "raw_json": compact_json(payload),
    }, ""


def fetch_clinpgx(rsid: str, timeout_seconds: int) -> tuple[dict, str]:
    base_url = "https://api.pharmgkb.org/v1"
    variant_payload, variant_error = json_get(
        f"{base_url}/data/variant/?{urllib.parse.urlencode({'symbol': rsid, 'view': 'max'})}",
        timeout_seconds,
    )
    if variant_error and not is_http_not_found(variant_error):
        return {}, f"variant_lookup: {variant_error}"
    if is_http_not_found(variant_error):
        variant_payload = {"data": []}
    time.sleep(0.35)
    clinical_payload, clinical_error = json_get(
        f"{base_url}/data/clinicalAnnotation?{urllib.parse.urlencode({'location.fingerprint': rsid, 'view': 'max'})}",
        timeout_seconds,
    )
    time.sleep(0.35)
    variant_annotation_payload, variant_annotation_error = json_get(
        f"{base_url}/data/variantAnnotation?{urllib.parse.urlencode({'location.fingerprint': rsid, 'view': 'max'})}",
        timeout_seconds,
    )
    errors = []
    if is_http_not_found(clinical_error):
        clinical_payload = {"data": []}
    elif clinical_error:
        errors.append(f"clinicalAnnotation: {clinical_error}")
    if is_http_not_found(variant_annotation_error):
        variant_annotation_payload = {"data": []}
    elif variant_annotation_error:
        errors.append(f"variantAnnotation: {variant_annotation_error}")
    variant_rows = variant_payload.get("data") if isinstance(variant_payload, dict) else []
    clinical_rows = clinical_payload.get("data") if isinstance(clinical_payload, dict) else []
    annotation_rows = variant_annotation_payload.get("data") if isinstance(variant_annotation_payload, dict) else []
    variant = variant_rows[0] if variant_rows else {}

    clinical_parts = []
    clinical_chemicals = []
    evidence_levels = []
    allele_phenotypes = []
    for item in clinical_rows[:12]:
        if not isinstance(item, dict):
            continue
        level = clean_str(get_nested(item, "levelOfEvidence", "term"))
        if level:
            evidence_levels.append(level)
        chemicals = [chem.get("name") for chem in item.get("relatedChemicals") or [] if isinstance(chem, dict)]
        clinical_chemicals.extend(chemicals)
        allele_text = unique_join(
            [
                f"{clean_str(ap.get('allele'))}: {clean_str(ap.get('phenotype'))[:220]}"
                for ap in item.get("allelePhenotypes") or []
                if isinstance(ap, dict)
            ],
            limit=3,
            sep=" || ",
        )
        if allele_text:
            allele_phenotypes.append(allele_text)
        clinical_parts.append(
            "; ".join(
                part
                for part in [
                    f"name={clean_str(item.get('name'))}" if clean_str(item.get("name")) else "",
                    f"level={level}" if level else "",
                    f"chemicals={unique_join(chemicals, limit=4, sep=',')}" if chemicals else "",
                ]
                if part
            )
        )

    variant_annotation_parts = []
    annotation_chemicals = []
    pmids = []
    for item in annotation_rows[:15]:
        if not isinstance(item, dict):
            continue
        chemicals = [chem.get("name") for chem in item.get("relatedChemicals") or [] if isinstance(chem, dict)]
        annotation_chemicals.extend(chemicals)
        for cross_ref in get_nested(item, "literature", "crossReferences") or []:
            if isinstance(cross_ref, dict) and cross_ref.get("resource") == "PubMed":
                pmids.append(cross_ref.get("resourceId"))
        variant_annotation_parts.append(
            "; ".join(
                part
                for part in [
                    f"genotype={clean_str(item.get('alleleGenotype'))}" if clean_str(item.get("alleleGenotype")) else "",
                    f"chemicals={unique_join(chemicals, limit=3, sep=',')}" if chemicals else "",
                    f"sentence={clean_str(item.get('sentence'))[:240]}" if clean_str(item.get("sentence")) else "",
                    f"score={compact_number(item.get('score'))}" if item.get("score") is not None else "",
                ]
                if part
            )
        )

    result = {
        "variant_id": clean_str(variant.get("id")),
        "variant_symbol": clean_str(variant.get("symbol")),
        "variant_name": clean_str(variant.get("name")),
        "variant_type": clean_str(variant.get("type")),
        "variant_change_classification": clean_str(variant.get("changeClassification")),
        "variant_clinical_significance": clean_str(variant.get("clinicalSignificance")),
        "variant_rare": clean_str(variant.get("rare")),
        "variant_rarity_source": clean_str(variant.get("raritySource")),
        "clinical_annotation_count": clean_str(len(clinical_rows)),
        "clinical_evidence_levels": unique_join(evidence_levels, limit=8),
        "clinical_chemicals": unique_join(clinical_chemicals, limit=15, sep=" | "),
        "clinical_summary": unique_join(clinical_parts, limit=8, sep=" || "),
        "clinical_allele_phenotypes": unique_join(allele_phenotypes, limit=8, sep=" || "),
        "variant_annotation_count": clean_str(len(annotation_rows)),
        "variant_annotation_chemicals": unique_join(annotation_chemicals, limit=15, sep=" | "),
        "variant_annotation_summary": unique_join(variant_annotation_parts, limit=10, sep=" || "),
        "pmids": unique_join(pmids, limit=12, sep=" | "),
        "variant_raw_json": compact_json(variant_payload),
        "clinical_raw_json": compact_json(clinical_payload),
        "variant_annotation_raw_json": compact_json(variant_annotation_payload),
    }
    return result, " | ".join(errors)


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
    time.sleep(delay_seconds)
    gwas_catalog, error = fetch_gwas_catalog(rsid, timeout_seconds)
    if error:
        errors["gwas_catalog"] = error
    time.sleep(max(delay_seconds, 0.35))
    clinpgx, error = fetch_clinpgx(rsid, timeout_seconds)
    if error:
        errors["clinpgx"] = error

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
        "gwasCatalog": gwas_catalog,
        "clinPgx": clinpgx,
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


def normalize_clinvar_classification(value: str) -> str:
    text = clean_str(value).lower()
    if not text:
        return "not_reported"
    if "pathogenic" in text and "conflicting" in text:
        return "conflicting_pathogenicity"
    if "pathogenic" in text:
        return "pathogenic_or_likely_pathogenic"
    if "uncertain" in text or "vus" in text:
        return "uncertain_significance"
    if "drug response" in text:
        return "drug_response"
    if "risk factor" in text:
        return "risk_factor"
    if "benign" in text:
        return "benign_or_likely_benign"
    return "other"


def clinvar_evidence_strength(review_status: str) -> str:
    text = clean_str(review_status).lower()
    if not text:
        return "not_reported"
    if "practice guideline" in text:
        return "practice_guideline"
    if "expert panel" in text:
        return "expert_panel"
    if "multiple submitters" in text and "no conflicts" in text:
        return "multi_submitter_no_conflict"
    if "conflicting" in text:
        return "conflicting"
    if "single submitter" in text:
        return "single_submitter"
    return "limited_or_unclear"


def population_frequency_summary(populations: str) -> dict:
    max_frequency = -1.0
    max_population = ""
    max_allele = ""
    count = 0
    for chunk in clean_str(populations).split("|"):
        values = {}
        for part in chunk.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
        frequency = values.get("freq")
        if frequency is None:
            continue
        try:
            number = float(frequency)
        except ValueError:
            continue
        count += 1
        if number > max_frequency:
            max_frequency = number
            max_population = values.get("pop", "")
            max_allele = values.get("allele", "")
    if max_frequency < 0:
        return {"max_frequency": "", "max_population": "", "max_allele": "", "count": "0", "summary": ""}
    frequency_text = compact_number(max_frequency)
    return {
        "max_frequency": frequency_text,
        "max_population": max_population,
        "max_allele": max_allele,
        "count": clean_str(count),
        "summary": f"max_freq={frequency_text}; population={max_population}; allele={max_allele}",
    }


def interpretation_readiness(row: dict, plus_row: dict) -> str:
    flags = []
    if plus_row.get("Canon Effect"):
        flags.append("canon_context")
    if plus_row.get("vep_hgvsp") or plus_row.get("vep_hgvsc"):
        flags.append("hgvs")
    if plus_row.get("vep_cadd_phred") or plus_row.get("vep_revel_score") or plus_row.get("vep_alphamissense_score"):
        flags.append("deleteriousness_scores")
    if plus_row.get("clinvar_normalized_classification") not in {"", "not_reported"}:
        flags.append("clinvar")
    if plus_row.get("gwas_association_count") not in {"", "0"}:
        flags.append("gwas")
    if plus_row.get("pharmgkb_clinical_annotation_count") not in {"", "0"} or plus_row.get("pharmgkb_variant_annotation_count") not in {"", "0"}:
        flags.append("pharmacogenomics")
    if plus_row.get("population_frequency_summary"):
        flags.append("population_frequency")
    confidence = clean_str(row.get("Confidence Level"))
    if confidence == "High" and len(flags) >= 4:
        return f"high_interpretability: {', '.join(flags)}"
    if len(flags) >= 3:
        return f"moderate_interpretability: {', '.join(flags)}"
    return f"limited_interpretability: {', '.join(flags) if flags else 'minimal_external_context'}"


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


def build_output_row_v2(row: dict, enrichment: dict) -> dict:
    base = {
        **row,
        "row_id": row_id_value(row),
        "Gene": row_gene(row),
        "SNP (rsID)": row_rsid(row),
        "Genotype": clean_str(row.get("Genotype") or row.get("gt_alleles")),
        "Zygosity": clean_str(row.get("Zygosity") or row.get("zygosity")),
        "Ref/Alt": row_ref_alt(row),
        "canon_effect": row_canon_effect(row),
        "patient_ref": first_present(row.get("ref_vcf"), row.get("ref")),
        "patient_alt_catalog": first_present(row.get("alt_vcf"), row.get("alt")),
        "patient_observed_alt_alleles": observed_alt_alleles(row),
        "Confidence Level": row_confidence_level(row),
        "Category / Module": row_category(row),
        "Review Status": row_review_status(row),
        "source_group": row_source_group(row),
        "Notes": row_notes(row),
    }
    legacy_view = build_output_row(base, enrichment)
    return {**base, **legacy_view}


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


def build_colab_output_row_v2(row: dict, enrichment: dict) -> dict:
    base = {
        **row,
        "row_id": row_id_value(row),
        "Gene": row_gene(row),
        "SNP (rsID)": row_rsid(row),
        "Category / Module": row_category(row),
        "Canon Effect": row_canon_effect(row),
        "Genotype": clean_str(row.get("Genotype") or row.get("gt_alleles")),
        "gt_alleles": clean_str(row.get("gt_alleles") or row.get("Genotype")),
        "patient_gt_alleles": clean_str(row.get("gt_alleles") or row.get("Genotype")),
        "Zygosity": clean_str(row.get("Zygosity") or row.get("zygosity")),
        "Ref/Alt": row_ref_alt(row),
        "patient_ref": first_present(row.get("ref_vcf"), row.get("ref")),
        "patient_alt_catalog": first_present(row.get("alt_vcf"), row.get("alt")),
        "patient_observed_alt_alleles": observed_alt_alleles(row),
        "source_group": row_source_group(row),
        "match_status": clean_str(row.get("match_status") or row.get("local_region_class")),
        "Confidence Level": row_confidence_level(row),
        "Review Status": row_review_status(row),
        "Notes": row_notes(row),
        "gt_raw": clean_str(row.get("gt_raw")),
        "ref": clean_str(row.get("ref")),
        "alt": clean_str(row.get("alt")),
        "ref_vcf": clean_str(row.get("ref_vcf")),
        "alt_vcf": clean_str(row.get("alt_vcf")),
        "has_genotype": clean_str(row.get("has_genotype")),
        "found_in_vcf_by_chr_pos": row_found_in_vcf_by_chr_pos(row),
    }
    legacy_view = build_colab_output_row(base, enrichment)
    return {**base, **legacy_view}


def build_plus_output_row(row: dict, enrichment: dict) -> dict:
    base = build_colab_output_row(row, enrichment)
    ensembl_variation = enrichment.get("ensemblVariation") or {}
    ensembl_vep = enrichment.get("ensemblVep") or {}
    clinvar = enrichment.get("clinVar") or {}
    myvariant = enrichment.get("myVariant") or {}
    gwas = enrichment.get("gwasCatalog") or {}
    clinpgx = enrichment.get("clinPgx") or {}
    population = population_frequency_summary(ensembl_variation.get("populations"))
    clinvar_conflict_text = (
        f"{clean_str(clinvar.get('clinical_significance'))} {clean_str(clinvar.get('review_status'))}".lower()
    )

    plus = {
        **base,
        "clinvar_normalized_classification": normalize_clinvar_classification(clinvar.get("clinical_significance")),
        "clinvar_evidence_strength": clinvar_evidence_strength(clinvar.get("review_status")),
        "clinvar_conflict_flag": "true" if "conflict" in clinvar_conflict_text else "false",
        "clinvar_trait_names": clean_str(clinvar.get("trait_names")),
        "population_frequency_summary": population["summary"],
        "population_max_frequency": population["max_frequency"],
        "population_max_frequency_population": population["max_population"],
        "population_max_frequency_allele": population["max_allele"],
        "population_frequency_observations": population["count"],
        "vep_picked_gene_symbol": clean_str(ensembl_vep.get("picked_gene_symbol")),
        "vep_picked_transcript": clean_str(ensembl_vep.get("picked_transcript_id")),
        "vep_canonical": clean_str(ensembl_vep.get("picked_canonical")),
        "vep_mane_select": clean_str(ensembl_vep.get("picked_mane_select")),
        "vep_hgvsc": clean_str(ensembl_vep.get("picked_hgvsc")),
        "vep_hgvsp": clean_str(ensembl_vep.get("picked_hgvsp")),
        "vep_protein_id": clean_str(ensembl_vep.get("picked_protein_id")),
        "vep_exon": clean_str(ensembl_vep.get("picked_exon")),
        "vep_intron": clean_str(ensembl_vep.get("picked_intron")),
        "vep_cdna_position": clean_str(ensembl_vep.get("picked_cdna")),
        "vep_cds_position": clean_str(ensembl_vep.get("picked_cds")),
        "vep_amino_acids": clean_str(ensembl_vep.get("picked_amino_acids")),
        "vep_protein_position": clean_str(ensembl_vep.get("picked_protein_position")),
        "vep_sift_prediction": clean_str(ensembl_vep.get("picked_sift_prediction")),
        "vep_sift_score": clean_str(ensembl_vep.get("picked_sift_score")),
        "vep_polyphen_prediction": clean_str(ensembl_vep.get("picked_polyphen_prediction")),
        "vep_polyphen_score": clean_str(ensembl_vep.get("picked_polyphen_score")),
        "vep_cadd_phred": clean_str(ensembl_vep.get("picked_cadd_phred") or myvariant.get("cadd_phred")),
        "vep_revel_score": clean_str(ensembl_vep.get("picked_revel_score")),
        "vep_alphamissense_score": clean_str(ensembl_vep.get("picked_alphamissense_score")),
        "vep_alphamissense_pred": clean_str(ensembl_vep.get("picked_alphamissense_pred")),
        "vep_mutationtaster_pred": clean_str(ensembl_vep.get("picked_mutationtaster_pred")),
        "vep_metasvm_pred": clean_str(ensembl_vep.get("picked_metasvm_pred")),
        "vep_spliceai": clean_str(ensembl_vep.get("picked_spliceai")),
        "vep_uniprot": clean_str(ensembl_vep.get("picked_uniprot")),
        "vep_domains": clean_str(ensembl_vep.get("domains_summary")),
        "gwas_association_count": clean_str(gwas.get("association_count")),
        "gwas_top_traits": clean_str(gwas.get("top_traits")),
        "gwas_reported_genes": clean_str(gwas.get("reported_genes")),
        "gwas_min_pvalue": clean_str(gwas.get("min_pvalue")),
        "gwas_top_associations": clean_str(gwas.get("top_associations")),
        "pharmgkb_variant_id": clean_str(clinpgx.get("variant_id")),
        "pharmgkb_change_classification": clean_str(clinpgx.get("variant_change_classification")),
        "pharmgkb_clinical_significance": clean_str(clinpgx.get("variant_clinical_significance")),
        "pharmgkb_rare": clean_str(clinpgx.get("variant_rare")),
        "pharmgkb_rarity_source": clean_str(clinpgx.get("variant_rarity_source")),
        "pharmgkb_clinical_annotation_count": clean_str(clinpgx.get("clinical_annotation_count")),
        "pharmgkb_clinical_evidence_levels": clean_str(clinpgx.get("clinical_evidence_levels")),
        "pharmgkb_clinical_chemicals": clean_str(clinpgx.get("clinical_chemicals")),
        "pharmgkb_clinical_summary": clean_str(clinpgx.get("clinical_summary")),
        "pharmgkb_allele_phenotypes": clean_str(clinpgx.get("clinical_allele_phenotypes")),
        "pharmgkb_variant_annotation_count": clean_str(clinpgx.get("variant_annotation_count")),
        "pharmgkb_variant_annotation_chemicals": clean_str(clinpgx.get("variant_annotation_chemicals")),
        "pharmgkb_variant_annotation_summary": clean_str(clinpgx.get("variant_annotation_summary")),
        "pharmgkb_pmids": clean_str(clinpgx.get("pmids")),
        "plus_source_error_sources": "|".join(sorted((enrichment.get("errors") or {}).keys())),
        "gwas_raw_json": clean_str(gwas.get("raw_json")),
        "pharmgkb_variant_raw_json": clean_str(clinpgx.get("variant_raw_json")),
        "pharmgkb_clinical_raw_json": clean_str(clinpgx.get("clinical_raw_json")),
        "pharmgkb_variant_annotation_raw_json": clean_str(clinpgx.get("variant_annotation_raw_json")),
    }
    plus["interpretation_readiness_summary"] = interpretation_readiness(row, plus)
    return plus


def build_plus_output_row_v2(row: dict, enrichment: dict) -> dict:
    base = build_colab_output_row_v2(row, enrichment)
    plus = build_plus_output_row(base, enrichment)
    return {**base, **plus}


def process(input_path: Path, output_dir: Path, cache_dir: Path, timeout_seconds: int, ttl_days: int) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    schema_version = schema_version_for_rows(rows)
    observed_rows = [row for row in rows if as_bool(row.get("has_genotype"))]
    rows_with_rsid = [row for row in observed_rows if row_rsid(row)]
    unique_rsids = sorted({row_rsid(row) for row in rows_with_rsid})

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

    if schema_version == "gene_module_v2":
        output_rows = [build_output_row_v2(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
        colab_output_rows = [build_colab_output_row_v2(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
        plus_output_rows = [build_plus_output_row_v2(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
        qa_fieldnames = fieldnames_from_rows(output_rows)
        colab_fieldnames = fieldnames_from_rows(colab_output_rows)
    else:
        output_rows = [build_output_row(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
        colab_output_rows = [build_colab_output_row(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
        plus_output_rows = [build_plus_output_row(row, enrichments.get(row_rsid(row), {})) for row in observed_rows]
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
    plus_output_csv = output_dir / "heal_fon_interpretation_enrichment_plus.csv"
    plus_fieldnames = fieldnames_from_rows(plus_output_rows) if schema_version == "gene_module_v2" else colab_fieldnames + [
        "clinvar_normalized_classification",
        "clinvar_evidence_strength",
        "clinvar_conflict_flag",
        "clinvar_trait_names",
        "population_frequency_summary",
        "population_max_frequency",
        "population_max_frequency_population",
        "population_max_frequency_allele",
        "population_frequency_observations",
        "vep_picked_gene_symbol",
        "vep_picked_transcript",
        "vep_canonical",
        "vep_mane_select",
        "vep_hgvsc",
        "vep_hgvsp",
        "vep_protein_id",
        "vep_exon",
        "vep_intron",
        "vep_cdna_position",
        "vep_cds_position",
        "vep_amino_acids",
        "vep_protein_position",
        "vep_sift_prediction",
        "vep_sift_score",
        "vep_polyphen_prediction",
        "vep_polyphen_score",
        "vep_cadd_phred",
        "vep_revel_score",
        "vep_alphamissense_score",
        "vep_alphamissense_pred",
        "vep_mutationtaster_pred",
        "vep_metasvm_pred",
        "vep_spliceai",
        "vep_uniprot",
        "vep_domains",
        "gwas_association_count",
        "gwas_top_traits",
        "gwas_reported_genes",
        "gwas_min_pvalue",
        "gwas_top_associations",
        "pharmgkb_variant_id",
        "pharmgkb_change_classification",
        "pharmgkb_clinical_significance",
        "pharmgkb_rare",
        "pharmgkb_rarity_source",
        "pharmgkb_clinical_annotation_count",
        "pharmgkb_clinical_evidence_levels",
        "pharmgkb_clinical_chemicals",
        "pharmgkb_clinical_summary",
        "pharmgkb_allele_phenotypes",
        "pharmgkb_variant_annotation_count",
        "pharmgkb_variant_annotation_chemicals",
        "pharmgkb_variant_annotation_summary",
        "pharmgkb_pmids",
        "interpretation_readiness_summary",
        "plus_source_error_sources",
        "gwas_raw_json",
        "pharmgkb_variant_raw_json",
        "pharmgkb_clinical_raw_json",
        "pharmgkb_variant_annotation_raw_json",
    ]
    write_csv(output_csv, output_rows, qa_fieldnames)
    write_csv(colab_output_csv, colab_output_rows, colab_fieldnames)
    write_csv(plus_output_csv, plus_output_rows, plus_fieldnames)

    source_errors = {}
    for enrichment in enrichments.values():
        for source in (enrichment.get("errors") or {}).keys():
            source_errors[source] = source_errors.get(source, 0) + 1

    status = "valid" if not source_errors else "warning"
    if not observed_rows:
        status = "warning"
        warnings.append("No observed genotype rows were available for external enrichment.")
    elif schema_version == "gene_module_v2" and not rows_with_rsid:
        warnings.append("Gene-module v2 rows do not include rsIDs; external enrichment fields remain sparse but rows were preserved.")

    summary = {
        "status": status,
        "errors": [],
        "warnings": warnings[:30],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "cacheDir": str(cache_dir),
        "metadata": {
            "schema_version": schema_version,
            "source_rows": len(rows),
            "observed_rows": len(observed_rows),
            "rows_with_rsid": len(rows_with_rsid),
            "unique_rsids": len(unique_rsids),
            "output_rows": len(output_rows),
            "cache_hits": sum(1 for item in enrichments.values() if item.get("cacheHit")),
            "source_error_counts": source_errors,
            "plus_rows": len(plus_output_rows),
            "sources": [
                "Ensembl variation",
                "Ensembl VEP",
                "ClinVar E-utilities",
                "MyVariant.info",
                "GWAS Catalog",
                "ClinPGx/PharmGKB",
            ],
        },
        "outputs": {
            "observedVariantEnrichmentCsv": str(output_csv),
            "observedVariantInterpretiveCsv": str(colab_output_csv),
            "observedVariantEnrichmentPlusCsv": str(plus_output_csv),
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
        args.cache_dir = (
            os.environ.get("HEAL_LEGACY_ENRICHMENT_CACHE_ROOT")
            or os.environ.get("HEAL_ENRICHMENT_CACHE_ROOT")
            or str(Path(__file__).resolve().parent / "cache")
        )
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
