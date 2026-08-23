#!/usr/bin/env python3
"""Build VCF-independent, resumable evidence packets for Tier 1 curation v2."""

from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402


USER_AGENT = "HEAL-internal-scientific-curation/2.0"
MODULE_TERMS = {
    "T1.1": ["mitochond", "energy", "redox", "inflamm", "one-carbon", "methyl", "metabolic"],
    "T1.2": ["sleep", "circadian", "melatonin", "chronotype"],
    "T1.3": ["nutrient", "vitamin", "mineral", "cofactor", "lipid", "fatty acid", "choline", "folate"],
    "T1.4": ["inflamm", "immune", "cytokine", "infection"],
    "T1.5": ["connective", "collagen", "bone", "cartilage", "tendon", "physical resilience"],
    "T1.6": ["xenobiotic", "detox", "oxidative stress", "drug transport", "glutathione"],
}
FUNCTIONAL_TERMS = re.compile(r"\b(function|functional|mechanis|pathway|enzyme|protein|expression|knockout|knockdown|cell|in vitro|in vivo|mouse|mice|murine|activity|metaboli[sz])", re.I)
HUMAN_TERMS = re.compile(r"\b(human|patient|participant|cohort|case.control|clinical|children|child|infant|adult|population|volunteer)", re.I)
NULL_TERMS = re.compile(r"\b(no association|not associated|no significant|null result|no effect|did not differ|failed to replicate|contradict)", re.I)
VARIANT_TERMS = re.compile(r"\b(rs\d+|variant|polymorphism|genotype|allele|mutation)", re.I)
REVIEW_EVIDENCE_ID = re.compile(r"(?:PMID:\d+|UNIPROT:[A-Z0-9]+|REACTOME:[A-Z0-9]+|NCBI_GENE:\d+)")


class HttpClient:
    def __init__(self, cache_dir: Path, *, delay: float = 0.34, retries: int = 1, timeout: int = 30):
        self.cache_dir = cache_dir
        self.delay = delay
        self.retries = retries
        self.timeout = timeout

    def get(self, url: str, *, suffix: str = ".json") -> tuple[bytes, str]:
        key = cv2.sha256_text(url)
        target = self.cache_dir / f"{key}{suffix}"
        if target.exists():
            return target.read_bytes(), cv2.sha256_text(target.read_text(encoding="utf-8", errors="replace"))
        target.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml"})
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed scientific APIs
                    content = response.read()
                temporary = target.with_suffix(target.suffix + ".tmp")
                temporary.write_bytes(content)
                temporary.replace(target)
                time.sleep(self.delay)
                return content, cv2.sha256_text(content.decode("utf-8", errors="replace"))
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
                last_error = error
                time.sleep(min(4, 0.5 * (2**attempt)))
        raise RuntimeError(f"GET failed after retries: {url}: {last_error}")


def json_get(client: HttpClient, url: str) -> tuple[dict, str]:
    raw, digest = client.get(url)
    return json.loads(raw.decode("utf-8")), digest


def canonical_rows(clean_rows_path: Path) -> dict[str, dict]:
    rows = cv2.read_csv(clean_rows_path)
    mapped: dict[str, dict] = {}
    for row in rows:
        module_id = row.get("module_id", "")
        gene = row.get("gene_symbol_normalized") or row.get("gene_symbol_original") or ""
        if module_id.startswith("T1.") and gene:
            mapped.setdefault(f"{gene}:{module_id}", row)
    return mapped


def review_policies(path: Path | None) -> dict[str, dict]:
    """Load the human evidence allow/deny lists without treating them as a signature."""
    if path is None:
        return {}
    proposal_sha256 = cv2.sha256_text(path.read_text(encoding="utf-8-sig"))
    policies: dict[str, dict] = {}
    for row in cv2.read_csv(path):
        group_id = str(row.get("Grupo") or "").strip()
        if not group_id:
            continue
        valid = sorted(set(REVIEW_EVIDENCE_ID.findall(str(row.get("Fuentes válidas") or ""))))
        invalid = sorted(set(REVIEW_EVIDENCE_ID.findall(str(row.get("Fuentes inválidas") or ""))))
        overlap = set(valid) & set(invalid)
        if overlap:
            raise ValueError(f"Review policy overlaps valid and invalid evidence for {group_id}: {sorted(overlap)}")
        policies[group_id] = {
            "proposal_sha256": proposal_sha256,
            "valid_evidence_ids": valid,
            "invalid_evidence_ids": invalid,
        }
    unknown = set(policies) - set(cv2.GOLD_GROUPS)
    if unknown:
        raise ValueError(f"Review proposal contains groups outside the frozen gold: {sorted(unknown)}")
    return policies


def tier1_groups(registry_path: Path, clean_rows_path: Path, gene_master_path: Path | None = None) -> list[dict]:
    canon = canonical_rows(clean_rows_path)
    master_aliases: dict[tuple[str, str], str] = {}
    if gene_master_path:
        for master in cv2.read_csv(gene_master_path):
            for module_id in str(master.get("module_ids") or "").split("|"):
                approved = master.get("approved_symbol") or ""
                original = master.get("gene_symbol_original") or ""
                if approved and module_id:
                    master_aliases[(approved, module_id)] = original
    groups = []
    for row in cv2.read_csv(registry_path):
        module_id = row.get("module_id", "")
        if not module_id.startswith("T1."):
            continue
        group_id = f"{row.get('gene', '')}:{module_id}"
        source = canon.get(group_id)
        if not source:
            original = master_aliases.get((row.get("gene", ""), module_id), "")
            source = canon.get(f"{original}:{module_id}")
        if not source:
            raise ValueError(f"Tier 1 group is absent from clean canon rows: {group_id}")
        groups.append({"group_id": group_id, "gene": row["gene"], "module_id": module_id, "canon": source})
    groups.sort(key=lambda group: (group["module_id"], group["gene"]))
    if len(groups) != 105:
        raise ValueError(f"Expected exactly 105 Tier 1 groups, found {len(groups)}")
    return groups


def select_exact_official_ncbi_record(result: dict, identifiers: list[str], requested_symbol: str) -> tuple[str, dict] | None:
    """Return only a record whose NCBI official symbol exactly matches the request."""
    expected = requested_symbol.strip().upper()
    for identifier in identifiers:
        candidate = result.get(identifier, {})
        if str(candidate.get("name") or "").strip().upper() == expected:
            return identifier, candidate
    return None


def ncbi_gene(client: HttpClient, gene: str) -> tuple[dict, dict]:
    query = urllib.parse.urlencode({"db": "gene", "term": f"{gene}[sym] AND 9606[Taxonomy ID]", "retmode": "json"})
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}"
    search, search_hash = json_get(client, search_url)
    ids = search.get("esearchresult", {}).get("idlist") or []
    if not ids:
        return {"symbol": gene, "name": "", "aliases": [], "summary": "", "gene_id": ""}, {
            "database": "NCBI_Gene", "version_or_release": "live_snapshot", "retrieved_at": cv2.utc_now(),
            "url": search_url, "response_sha256": search_hash, "status": "not_found",
        }
    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({"db": "gene", "id": ",".join(ids), "retmode": "json"})
    summary, summary_hash = json_get(client, summary_url)
    result = summary.get("result", {})
    # NCBI's symbol search may return a historical alias before the requested
    # official symbol (PEMT previously resolved to MUC1/GeneID 4582). An alias
    # is useful for discovery, never for final gene identity.
    exact = select_exact_official_ncbi_record(result, ids, gene)
    if exact is None:
        observed = [str(result.get(identifier, {}).get("name") or "") for identifier in ids]
        return {"symbol": "", "name": "", "aliases": [], "summary": "", "gene_id": ""}, {
            "database": "NCBI_Gene", "version_or_release": "exact_official_symbol_not_found",
            "retrieved_at": cv2.utc_now(), "url": summary_url, "response_sha256": summary_hash,
            "status": "technical_failure", "identity_error": f"requested={gene}; returned={','.join(observed)}",
        }
    selected_id, row = exact
    aliases = [alias.strip() for alias in str(row.get("otheraliases") or "").split(",") if alias.strip()]
    record = {
        "symbol": row.get("name") or gene,
        "name": row.get("description") or "",
        "aliases": aliases,
        "summary": row.get("summary") or "",
        "gene_id": str(row.get("uid") or selected_id),
    }
    return record, {
        "database": "NCBI_Gene", "version_or_release": f"GeneID:{selected_id}", "retrieved_at": cv2.utc_now(),
        "url": summary_url, "response_sha256": summary_hash, "status": "available",
    }


def uniprot(client: HttpClient, gene: str) -> tuple[dict, dict]:
    params = urllib.parse.urlencode({"query": f"(gene_exact:{gene}) AND (organism_id:9606) AND (reviewed:true)", "format": "json", "size": 1})
    url = f"https://rest.uniprot.org/uniprotkb/search?{params}"
    try:
        payload, digest = json_get(client, url)
    except (RuntimeError, json.JSONDecodeError):
        return {}, {
            "database": "UniProt", "version_or_release": "live_snapshot", "retrieved_at": cv2.utc_now(),
            "url": url, "response_sha256": cv2.sha256_text(url), "status": "technical_failure",
        }
    results = payload.get("results") or []
    if not results:
        return {}, {"database": "UniProt", "version_or_release": "live_snapshot", "retrieved_at": cv2.utc_now(), "url": url, "response_sha256": digest, "status": "not_found"}
    row = results[0]
    comments = []
    for comment in row.get("comments") or []:
        for text in comment.get("texts") or []:
            if text.get("value"):
                comments.append(text["value"])
    return {
        "accession": row.get("primaryAccession") or "", "entry_type": row.get("entryType") or "",
        "protein": row.get("proteinDescription") or {}, "function_summary": " ".join(comments)[:12000],
    }, {
        "database": "UniProt", "version_or_release": str(row.get("entryAudit", {}).get("sequenceVersion") or "reviewed_current"),
        "retrieved_at": cv2.utc_now(), "url": url, "response_sha256": digest, "status": "available",
    }


def reactome(client: HttpClient, accession: str) -> tuple[list[dict], dict]:
    if not accession:
        empty_hash = cv2.sha256_text("")
        return [], {"database": "Reactome", "version_or_release": "live_snapshot", "retrieved_at": cv2.utc_now(), "url": "", "response_sha256": empty_hash, "status": "not_found"}
    url = f"https://reactome.org/ContentService/data/mapping/UniProt/{urllib.parse.quote(accession)}/pathways?species=9606"
    try:
        payload, digest = json_get(client, url)
    except (RuntimeError, json.JSONDecodeError):
        return [], {"database": "Reactome", "version_or_release": "live_snapshot", "retrieved_at": cv2.utc_now(), "url": url, "response_sha256": cv2.sha256_text(url), "status": "technical_failure"}
    rows = payload if isinstance(payload, list) else []
    return rows, {"database": "Reactome", "version_or_release": "ContentService_current", "retrieved_at": cv2.utc_now(), "url": url, "response_sha256": digest, "status": "available" if rows else "not_found"}


def pubmed_ids(client: HttpClient, gene: str, module_id: str, limit: int) -> list[str]:
    module_query = " OR ".join(f'"{term}"[Title/Abstract]' for term in MODULE_TERMS[module_id])
    term = f'({gene}[Title/Abstract]) AND ({module_query}) AND ("1900/01/01"[Date - Publication] : "2026/07/28"[Date - Publication])'
    query = urllib.parse.urlencode({"db": "pubmed", "term": term, "retmode": "json", "retmax": limit, "sort": "relevance"})
    payload, _ = json_get(client, f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}")
    return payload.get("esearchresult", {}).get("idlist") or []


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape("".join(element.itertext()))).strip()


def _normalize_doi(value: str | None) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)
    return text.rstrip(" .;,)")


def _article_date(article: ET.Element) -> str | None:
    pub_date = article.find(".//JournalIssue/PubDate")
    if pub_date is None:
        return None
    year = _text(pub_date.find("Year")) or re.search(r"\b(19|20)\d{2}\b", _text(pub_date.find("MedlineDate")) or "")
    year = year if isinstance(year, str) else (year.group(0) if year else "")
    month_text = _text(pub_date.find("Month"))
    day_text = _text(pub_date.find("Day"))
    months = {name.lower(): index for index, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
    month = months.get(month_text[:3].lower(), int(month_text) if month_text.isdigit() else 1)
    day = int(day_text) if day_text.isdigit() else 1
    return f"{year}-{month:02d}-{day:02d}" if year else None


def pubmed_records(client: HttpClient, ids: list[str], gene: str, aliases: list[str], module_id: str) -> list[dict]:
    if not ids:
        return []
    query = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{query}"
    raw, digest = client.get(url, suffix=".xml")
    root = ET.fromstring(raw)
    rows = []
    identity_terms = [gene] + aliases
    for article in root.findall("./PubmedArticle"):
        pmid = _text(article.find("./MedlineCitation/PMID"))
        title = _text(article.find("./MedlineCitation/Article/ArticleTitle"))
        abstract = " ".join(_text(node) for node in article.findall("./MedlineCitation/Article/Abstract/AbstractText"))
        combined = f"{title} {abstract}"
        publication_types = {_text(node).lower() for node in article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType")}
        mesh = " ".join(_text(node) for node in article.findall("./MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName"))
        doi = ""
        # ArticleIdList belongs to the article itself. A descendant search also
        # traverses PubmedData/ReferenceList and can capture a cited paper DOI.
        for node in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            if node.attrib.get("IdType") == "doi":
                doi = _normalize_doi(_text(node))
        is_review = any("review" in item for item in publication_types) or "meta-analysis" in publication_types
        roles = []
        if FUNCTIONAL_TERMS.search(combined): roles.append("functional_study")
        if HUMAN_TERMS.search(combined + " " + mesh): roles.append("human_study")
        if VARIANT_TERMS.search(combined): roles.append("variant_specific")
        if NULL_TERMS.search(combined): roles.append("conflict_or_null")
        if not roles: roles.append("insufficient_metadata")
        exact = bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(gene)}(?![A-Za-z0-9])", combined, re.I))
        alias = not exact and any(re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", combined, re.I) for item in aliases if len(item) >= 3)
        nct = re.search(r"\bNCT\d{8}\b", combined, re.I)
        module_relevant = any(term.lower() in combined.lower() for term in MODULE_TERMS[module_id])
        publication_date = _article_date(article)
        citation_type = "review" if is_review else ("clinical_trial" if any("clinical trial" in item for item in publication_types) else "journal_article")
        row = {
            "evidence_id": f"PMID:{pmid}", "source_kind": "review" if is_review else "primary_publication",
            "citation_type": citation_type, "title": title, "publication_date": publication_date,
            "pmid": pmid or None, "doi": doi or None, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "cohort_key": nct.group(0).upper() if nct else None, "derived_publication": False,
            "role_candidates": sorted(set(roles)), "review_discovery_only": is_review,
            "identity_match": "exact" if exact else ("alias" if alias else "ambiguous"),
            "eligibility_status": "eligible", "exclusion_reasons": [],
            "module_relevance_candidate": module_relevant, "abstract_or_summary": abstract[:16000],
            "metadata_complete": bool(title and publication_date and abstract),
            "retrieval_sha256": cv2.sha256_text(f"{digest}:{pmid}:{title}:{abstract}"),
        }
        rows.append(row)
    return rows


def europe_pmc_records(client: HttpClient, gene: str, aliases: list[str], module_id: str, limit: int) -> list[dict]:
    module_query = " OR ".join(f'"{term}"' for term in MODULE_TERMS[module_id])
    query = f'TITLE_ABS:{gene} AND ({module_query}) AND FIRST_PDATE:[1900-01-01 TO 2026-07-28]'
    params = urllib.parse.urlencode({"query": query, "format": "json", "pageSize": limit, "resultType": "core", "sort": "CITED desc"})
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"
    try:
        payload, digest = json_get(client, url)
    except (RuntimeError, json.JSONDecodeError):
        # Europe PMC supplements PubMed and is never the only primary-literature
        # source. Preserve resilience without silently inventing metadata.
        return []
    rows = []
    for result in payload.get("resultList", {}).get("result") or []:
        title = str(result.get("title") or "")
        abstract = str(result.get("abstractText") or "")
        combined = f"{title} {abstract}"
        publication_types = " ".join(result.get("pubTypeList", {}).get("pubType") or []).lower()
        is_review = "review" in publication_types or "meta-analysis" in publication_types
        roles = []
        if FUNCTIONAL_TERMS.search(combined): roles.append("functional_study")
        if HUMAN_TERMS.search(combined): roles.append("human_study")
        if VARIANT_TERMS.search(combined): roles.append("variant_specific")
        if NULL_TERMS.search(combined): roles.append("conflict_or_null")
        if not roles: roles.append("insufficient_metadata")
        exact = bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(gene)}(?![A-Za-z0-9])", combined, re.I))
        alias = not exact and any(re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", combined, re.I) for item in aliases if len(item) >= 3)
        pmid = str(result.get("pmid") or "")
        doi = str(result.get("doi") or "")
        source_id = f"PMID:{pmid}" if pmid else (f"DOI:{doi.lower()}" if doi else f"EPMC:{result.get('id')}")
        nct = re.search(r"\bNCT\d{8}\b", combined, re.I)
        rows.append({
            "evidence_id": source_id, "source_kind": "review" if is_review else "primary_publication",
            "citation_type": "review" if is_review else ("clinical_trial" if "clinical trial" in publication_types else "journal_article"),
            "title": title, "publication_date": result.get("firstPublicationDate") or result.get("journalInfo", {}).get("printPublicationDate") or None,
            "pmid": pmid or None, "doi": doi or None,
            "url": f"https://europepmc.org/article/MED/{pmid}" if pmid else f"https://europepmc.org/article/{result.get('source', '')}/{result.get('id', '')}",
            "cohort_key": nct.group(0).upper() if nct else None, "derived_publication": False,
            "role_candidates": sorted(set(roles)), "review_discovery_only": is_review,
            "identity_match": "exact" if exact else ("alias" if alias else "ambiguous"),
            "eligibility_status": "eligible", "exclusion_reasons": [],
            "module_relevance_candidate": any(term.lower() in combined.lower() for term in MODULE_TERMS[module_id]),
            "abstract_or_summary": abstract[:16000], "metadata_complete": bool(title and abstract and (result.get("firstPublicationDate") or result.get("pubYear"))),
            "retrieval_sha256": cv2.sha256_text(f"{digest}:{source_id}:{title}:{abstract}"),
        })
    return rows


def database_source(evidence_id: str, database: str, title: str, url: str, summary: str, digest: str, module_id: str) -> dict:
    return {
        "evidence_id": evidence_id, "source_kind": "authoritative_database", "citation_type": "database_record",
        "title": title, "publication_date": None, "pmid": None, "doi": None, "url": url,
        "cohort_key": None, "derived_publication": False, "role_candidates": ["canonical_source", "molecular_function"],
        "review_discovery_only": False, "identity_match": "exact",
        "eligibility_status": "eligible", "exclusion_reasons": [],
        "module_relevance_candidate": any(term in summary.lower() for term in MODULE_TERMS[module_id]),
        "abstract_or_summary": summary[:16000], "metadata_complete": bool(title and summary), "retrieval_sha256": digest,
    }


def build_packet(group: dict, client: HttpClient, *, publication_limit: int, review_policy: dict | None = None) -> dict:
    gene, module_id, canon = group["gene"], group["module_id"], group["canon"]
    ncbi, ncbi_snapshot = ncbi_gene(client, gene)
    if ncbi.get("symbol") != gene:
        raise ValueError(
            f"NCBI Gene exact official-symbol validation failed for {group['group_id']}: "
            f"requested={gene}; returned={ncbi.get('symbol') or 'none'}"
        )
    protein, uniprot_snapshot = uniprot(client, gene)
    pathways, reactome_snapshot = reactome(client, protein.get("accession", ""))
    aliases = sorted(set(ncbi.get("aliases") or []))
    review_policy = review_policy or {}
    required_ids = set(review_policy.get("valid_evidence_ids") or [])
    invalid_ids = set(review_policy.get("invalid_evidence_ids") or [])
    reviewed_pmids = {
        evidence_id.split(":", 1)[1]
        for evidence_id in required_ids | invalid_ids
        if evidence_id.startswith("PMID:")
    }
    ids = sorted(set(pubmed_ids(client, gene, module_id, publication_limit)) | reviewed_pmids, key=int)
    publications = pubmed_records(client, ids, gene, aliases, module_id)
    # PubMed is authoritative for PMID-linked metadata. Europe PMC is used to
    # supplement records that PubMed did not return, not to create competing
    # title/DOI claims for the same PMID.
    pubmed_authoritative_ids = {row.get("pmid") for row in publications if row.get("pmid")}
    publications += [
        row for row in europe_pmc_records(client, gene, aliases, module_id, publication_limit)
        if not row.get("pmid") or row.get("pmid") not in pubmed_authoritative_ids
    ]
    for publication in publications:
        published = cv2.parse_date(publication.get("publication_date"))
        if published and published > cv2.CUTOFF:
            publication["eligibility_status"] = "excluded"
            publication["exclusion_reasons"] = ["publication_after_cutoff"]
    database_rows = [
        database_source(f"NCBI_GENE:{ncbi.get('gene_id') or gene}", "NCBI_Gene", f"NCBI Gene: {gene}", ncbi_snapshot["url"], f"{ncbi.get('name', '')}. {ncbi.get('summary', '')}", ncbi_snapshot["response_sha256"], module_id),
    ]
    if protein:
        database_rows.append(database_source(f"UNIPROT:{protein.get('accession')}", "UniProt", f"UniProt: {gene}", uniprot_snapshot["url"], protein.get("function_summary", "") or json.dumps(protein.get("protein") or {}), uniprot_snapshot["response_sha256"], module_id))
    if pathways:
        pathway_text = "; ".join(str(row.get("displayName") or row.get("name") or "") for row in pathways)
        database_rows.append(database_source(f"REACTOME:{protein.get('accession')}", "Reactome", f"Reactome pathways: {gene}", reactome_snapshot["url"], pathway_text, reactome_snapshot["response_sha256"], module_id))
    metadata_errors = cv2.publication_metadata_errors(publications)
    if metadata_errors:
        raise ValueError(f"Publication metadata validation failed for {group['group_id']}: {metadata_errors}")
    ledger = cv2.deduplicate_sources(database_rows + publications)
    for source in ledger:
        evidence_id = source.get("evidence_id")
        if evidence_id in required_ids:
            source["review_disposition"] = "valid"
        elif evidence_id in invalid_ids:
            source["review_disposition"] = "invalid"
            source["eligibility_status"] = "excluded"
            source["exclusion_reasons"] = sorted(set(source.get("exclusion_reasons") or []) | {"human_review_invalid"})
        else:
            source["review_disposition"] = "unreviewed"
    ledger_ids = {source.get("evidence_id") for source in ledger}
    missing_required = required_ids - ledger_ids
    if missing_required:
        raise ValueError(f"Human-reviewed valid evidence is missing for {group['group_id']}: {sorted(missing_required)}")
    selected, selection_summary = cv2.select_sources(
        ledger, required_evidence_ids=required_ids, excluded_evidence_ids=invalid_ids,
    )
    packet = {
        "schema_version": "mechanism_evidence_packet_v2", "group_id": group["group_id"],
        "gene": {"symbol": gene, "full_name": canon.get("full_gene_name") or ncbi.get("name") or ""},
        "approved_aliases": aliases,
        "module": {
            "module_id": module_id, "name": canon.get("module_name") or "", "purpose": canon.get("module_purpose") or "",
            "system_within_module": canon.get("system_within_module") or "", "explicit_exclusions": canon.get("explicit_exclusions") or "",
        },
        "evidence_cutoff": cv2.CUTOFF.isoformat(), "retrieved_at": cv2.utc_now(),
        "database_snapshots": [ncbi_snapshot, uniprot_snapshot, reactome_snapshot],
        "source_ledger": ledger, "selected_evidence_ids": selected, "selection_summary": selection_summary,
    }
    if review_policy:
        packet["human_review_policy"] = review_policy
    packet["packet_sha256"] = cv2.sha256_json(packet)
    errors = cv2.validate_packet(packet)
    if errors:
        raise ValueError(f"Invalid packet {group['group_id']}: {errors}")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mechanism-registry", required=True)
    parser.add_argument("--clean-canon-rows", required=True)
    parser.add_argument("--gene-master")
    parser.add_argument("--review-proposal-csv")
    parser.add_argument("--cache-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scope", choices=("all", "gold"), default="all")
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--publication-limit", type=int, default=40)
    parser.add_argument("--delay-seconds", type=float, default=0.34)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    client = HttpClient(Path(args.cache_dir).resolve() if args.cache_dir else output / "retrieval-cache", delay=args.delay_seconds)
    policies = review_policies(Path(args.review_proposal_csv).resolve() if args.review_proposal_csv else None)
    groups = tier1_groups(
        Path(args.mechanism_registry), Path(args.clean_canon_rows),
        Path(args.gene_master) if args.gene_master else None,
    )
    requested = set(args.group or (cv2.GOLD_GROUPS if args.scope == "gold" else [group["group_id"] for group in groups]))
    unknown = requested - {group["group_id"] for group in groups}
    if unknown:
        raise ValueError(f"Unknown groups requested: {sorted(unknown)}")
    manifest_rows, failures = [], []
    for index, group in enumerate([group for group in groups if group["group_id"] in requested], 1):
        target = output / "packets" / f"{group['group_id'].replace(':', '__')}.json"
        failure_path = output / "failures" / f"{group['group_id'].replace(':', '__')}.json"
        if target.exists():
            packet = cv2.read_json(target)
            errors = cv2.validate_packet(packet)
            if errors:
                raise ValueError(f"Existing packet cannot be resumed: {target}: {errors}")
        else:
            try:
                packet = build_packet(
                    group, client, publication_limit=args.publication_limit,
                    review_policy=policies.get(group["group_id"]),
                )
                cv2.write_json(target, packet, immutable=True)
            except Exception as error:  # noqa: BLE001 - preserve resumable per-group failure
                failures.append({"group_id": group["group_id"], "error": str(error), "at": cv2.utc_now()})
                cv2.write_json(failure_path, failures[-1])
                continue
        failure_path.unlink(missing_ok=True)
        manifest_rows.append({
            "group_id": group["group_id"], "packet_path": str(target), "packet_sha256": packet["packet_sha256"],
            "ledger_total": packet["selection_summary"]["ledger_total"], "selected_total": packet["selection_summary"]["selected_total"],
        })
        print(json.dumps({"progress": f"{index}/{len(requested)}", "group_id": group["group_id"], "selected": packet["selection_summary"]["selected_total"]}), flush=True)
    manifest = {
        "schema_version": "tier1_evidence_packet_manifest_v2", "candidate_only": True, "activation_blocked": True,
        "created_at": cv2.utc_now(), "evidence_cutoff": cv2.CUTOFF.isoformat(), "requested_groups": len(requested),
        "completed_groups": len(manifest_rows), "failed_groups": failures, "packets": sorted(manifest_rows, key=lambda row: row["group_id"]),
    }
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    cv2.write_json(output / "evidence_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if not failures and len(manifest_rows) == len(requested) else 2


if __name__ == "__main__":
    raise SystemExit(main())
