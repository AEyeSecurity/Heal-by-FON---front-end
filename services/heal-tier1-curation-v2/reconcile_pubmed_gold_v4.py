#!/usr/bin/env python3
"""Technical PMID/title/DOI reconciliation for the signed 12-group Gold."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402
from build_evidence_packets import USER_AGENT, _normalize_doi, _text  # noqa: E402
from compile_signed_gold import iso_date  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_pubmed(pmids: list[str], output: Path, batch_size: int = 200) -> tuple[dict[str, dict], dict]:
    response_dir = output / "responses"
    response_dir.mkdir(parents=True, exist_ok=False)
    records: dict[str, dict] = {}
    batches: list[dict] = []
    retrieved_at = cv2.utc_now()
    for offset in range(0, len(pmids), batch_size):
        requested = pmids[offset:offset + batch_size]
        query = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(requested), "retmode": "xml"})
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"})
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - fixed NCBI endpoint
                    raw = response.read()
                ET.fromstring(raw)
                break
            except Exception as error:  # noqa: BLE001 - bounded technical retry
                last_error = error
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"PubMed batch failed after 3 attempts: {offset // batch_size + 1}: {last_error}")
        response_hash = hashlib.sha256(raw).hexdigest()
        response_path = response_dir / f"batch-{offset // batch_size + 1:02d}.xml"
        response_path.write_bytes(raw)
        returned = []
        for article in ET.fromstring(raw).findall("./PubmedArticle"):
            pmid = _text(article.find("./MedlineCitation/PMID"))
            title = cv2.normalize_title(_text(article.find("./MedlineCitation/Article/ArticleTitle")))
            doi = ""
            for node in article.findall("./PubmedData/ArticleIdList/ArticleId"):
                if node.attrib.get("IdType") == "doi":
                    doi = _normalize_doi(_text(node))
                    break
            record = {"pmid": pmid, "title": title, "doi": doi or None}
            record["authoritative_record_sha256"] = cv2.sha256_json(record)
            record["metadata_provenance"] = {
                "database": "PubMed", "retrieved_at": retrieved_at, "url": url,
                "response_sha256": response_hash,
            }
            records[pmid] = record
            returned.append(pmid)
        batches.append({
            "url": url, "retrieved_at": retrieved_at, "requested_pmids": requested,
            "returned_pmids": returned, "response_path": str(response_path),
            "response_sha256": response_hash,
        })
    missing = sorted(set(pmids) - set(records), key=int)
    manifest = {
        "schema_version": "pubmed_metadata_snapshot_v1", "database": "PubMed",
        "retrieved_at": retrieved_at, "record_count": len(records), "requested_count": len(pmids),
        "missing_pmids": missing, "batches": batches,
    }
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    cv2.write_json(output / "snapshot_manifest.json", manifest, immutable=True)
    if missing:
        raise ValueError(f"PubMed omitted {len(missing)} PMIDs: {missing[:20]}")
    return records, manifest


def reconcile(args: argparse.Namespace) -> dict:
    source_manifest = cv2.read_json(Path(args.evidence_manifest).resolve())
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable reconciliation: {output}")
    output.mkdir(parents=True)
    signed_csv = Path(args.signed_csv).resolve()
    signed_xlsx = Path(args.signed_xlsx).resolve()
    signed_rows = cv2.read_csv(signed_csv)
    signed_by_group = {row["Grupo"]: row for row in signed_rows}
    if len(signed_rows) != 12 or set(signed_by_group) != set(cv2.GOLD_GROUPS):
        raise ValueError("Signed source must contain exactly the frozen 12 groups")

    packets: dict[str, dict] = {}
    all_pmids: set[str] = set()
    for row in source_manifest.get("packets") or []:
        packet = cv2.read_json(row["packet_path"])
        if packet.get("packet_sha256") != row.get("packet_sha256"):
            raise ValueError(f"Source packet hash mismatch: {row['group_id']}")
        packets[row["group_id"]] = packet
        all_pmids.update(str(source["pmid"]) for source in packet.get("source_ledger") or [] if source.get("pmid"))
    if set(packets) != set(cv2.GOLD_GROUPS):
        raise ValueError("Evidence manifest must contain exactly the frozen 12 groups")

    records, snapshot = fetch_pubmed(sorted(all_pmids, key=int), output / "pubmed-snapshot")
    reconciled: dict[str, dict] = {}
    qa_rows: list[dict] = []
    scientific_blockers: dict[str, list[str]] = {}
    for group_id in cv2.GOLD_GROUPS:
        old = packets[group_id]
        packet = json.loads(json.dumps(old))
        blockers = []
        selected = set(packet.get("selected_evidence_ids") or [])
        for source in packet.get("source_ledger") or []:
            pmid = str(source.get("pmid") or "")
            if not pmid:
                source.update({
                    "metadata_provenance": None, "authoritative_record_sha256": None,
                    "metadata_consistency_status": "not_applicable",
                })
                continue
            authoritative = records[pmid]
            old_title = cv2.normalize_title(source.get("title"))
            old_doi = cv2.normalize_doi(source.get("doi"))
            title_match = cv2.normalize_title_identity(old_title) == cv2.normalize_title_identity(authoritative["title"])
            doi_match = old_doi == cv2.normalize_doi(authoritative["doi"])
            if not title_match:
                blockers.append(f"title_identity_changed:PMID:{pmid}")
            source.update({
                "title": authoritative["title"], "doi": authoritative["doi"],
                "metadata_provenance": authoritative["metadata_provenance"],
                "authoritative_record_sha256": authoritative["authoritative_record_sha256"],
                "metadata_consistency_status": "consistent",
            })
            qa_rows.append({
                "group_id": group_id, "evidence_id": source.get("evidence_id"),
                "selected": str(source.get("evidence_id") in selected).lower(), "pmid": pmid,
                "packet_title_before": old_title, "pubmed_title": authoritative["title"],
                "title_consistent": str(title_match).lower(), "packet_doi_before": old_doi,
                "pubmed_doi": authoritative["doi"] or "", "doi_consistent_before": str(doi_match).lower(),
                "doi_after": authoritative["doi"] or "", "status_after": "consistent",
            })
        if blockers:
            scientific_blockers[group_id] = blockers
        reconciled[group_id] = packet

    qa_fields = [
        "group_id", "evidence_id", "selected", "pmid", "packet_title_before", "pubmed_title",
        "title_consistent", "packet_doi_before", "pubmed_doi", "doi_consistent_before", "doi_after", "status_after",
    ]
    qa_path = output / "qa_pmid_title_doi.csv"
    write_csv(qa_path, qa_rows, qa_fields)
    if scientific_blockers:
        cv2.write_json(output / "scientific_change_blockers.json", scientific_blockers, immutable=True)
        raise ValueError(f"Publication identity changed; re-sign required: {sorted(scientific_blockers)}")
    qa_sha = sha256_file(qa_path)

    packet_dir = output / "evidence" / "packets"
    packet_dir.mkdir(parents=True)
    packet_rows = []
    for group_id in cv2.GOLD_GROUPS:
        packet = reconciled[group_id]
        packet["retrieved_at"] = cv2.utc_now()
        packet["publication_metadata_snapshot"] = {
            "database": "PubMed", "retrieved_at": snapshot["retrieved_at"],
            "manifest_sha256": snapshot["manifest_sha256"], "qa_sha256": qa_sha,
            "consistency_status": "consistent",
        }
        packet.pop("packet_sha256", None)
        packet["packet_sha256"] = cv2.sha256_json(packet)
        errors = cv2.validate_packet(packet)
        if errors:
            raise ValueError(f"Reconciled packet invalid for {group_id}: {errors}")
        target = packet_dir / f"{group_id.replace(':', '__')}.json"
        cv2.write_json(target, packet, immutable=True)
        packet_rows.append({
            "group_id": group_id, "packet_path": str(target), "packet_sha256": packet["packet_sha256"],
            "ledger_total": packet["selection_summary"]["ledger_total"],
            "selected_total": packet["selection_summary"]["selected_total"],
        })

    source_csv_sha, source_xlsx_sha = sha256_file(signed_csv), sha256_file(signed_xlsx)
    manifest = {
        "schema_version": "tier1_evidence_packet_manifest_v4", "candidate_only": True,
        "activation_blocked": True, "created_at": cv2.utc_now(), "evidence_cutoff": cv2.CUTOFF.isoformat(),
        "requested_groups": 12, "completed_groups": 12, "failed_groups": [], "packets": packet_rows,
        "pubmed_snapshot_manifest_sha256": snapshot["manifest_sha256"], "metadata_qa_sha256": qa_sha,
        "pubmed_snapshot_manifest_path": str(output / "pubmed-snapshot" / "snapshot_manifest.json"),
        "metadata_qa_path": str(qa_path),
        "signature_source_csv_sha256": source_csv_sha, "signature_source_xlsx_sha256": source_xlsx_sha,
    }
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    evidence_manifest_path = output / "evidence" / "evidence_manifest.json"
    cv2.write_json(evidence_manifest_path, manifest, immutable=True)

    reconciled_rows, groups = [], {}
    for group_id in cv2.GOLD_GROUPS:
        source_row = dict(signed_by_group[group_id])
        old, packet = packets[group_id], reconciled[group_id]
        old_ids = {row["evidence_id"] for row in old.get("source_ledger") or []}
        new_ids = {row["evidence_id"] for row in packet.get("source_ledger") or []}
        if old_ids != new_ids or old.get("selected_evidence_ids") != packet.get("selected_evidence_ids"):
            raise ValueError(f"Evidence set changed for {group_id}; re-sign required")
        source_row["Hash paquete reconciliado"] = packet["packet_sha256"]
        source_row["Fecha firma"] = iso_date(source_row["Fecha firma"])
        source_row["Tipo reconciliación"] = "technical_metadata_only"
        source_row["Hash firma fuente CSV"] = source_csv_sha
        source_row["Hash firma fuente XLSX"] = source_xlsx_sha
        reconciled_rows.append(source_row)
        alternatives = [value.strip() for value in source_row["Estados aceptables"].split("|") if value.strip()]
        groups[group_id] = {
            "group_id": group_id, "gene": source_row["Gen"], "module_id": group_id.split(":", 1)[1],
            "split": source_row["Segmento"], "scientific_status_proposed": source_row["Estado propuesto"],
            "scientific_status_alternatives": alternatives,
            "context_usable_proposed": source_row["Contexto utilizable"].strip().lower() in {"sí", "si", "true", "1", "yes"},
            "inference_ceiling_proposed": source_row["Techo de inferencia"],
            "required_limitations": source_row["Limitaciones"], "reviewer_state": source_row["Revisor final"],
            "review_date": source_row["Fecha firma"], "review_notes_original": source_row.get("Correcciones", ""),
            "work_completed": "PMID-title-DOI reconciliation, packet regeneration and signature hash reconciliation completed.",
            "current_scientific_limitations": source_row["Limitaciones"],
            "remaining_pre_sol_gates": "validate-gold=0 and frozen calibration prompt required.",
            "original_packet_sha256": old["packet_sha256"], "reconciled_packet_sha256": packet["packet_sha256"],
            "allowed_evidence_ids_proposed": [value.strip() for value in source_row["Fuentes incluidas"].split("|") if value.strip()],
            "invalid_evidence_ids_proposed": [value.strip() for value in source_row["Fuentes excluidas"].split("|") if value.strip()],
            "technical_flags": [], "signature_reconciliation": "technical_metadata_only", "release_ready": False,
        }

    foundation_dir = output / "foundation-v4"
    signed_out = foundation_dir / "LLM1_Tier1_Firma_12_Grupos_2026-08-14_RECONCILIADO.csv"
    write_csv(signed_out, reconciled_rows, list(signed_rows[0]) + ["Tipo reconciliación", "Hash firma fuente CSV", "Hash firma fuente XLSX"])
    foundation = {
        "schema_version": "tier1_human_review_foundation_v4", "created_at": cv2.utc_now(),
        "evidence_cutoff": cv2.CUTOFF.isoformat(), "gold_status": "signed_reconciled_ready_for_gold_validation",
        "active_registry_modified": False, "release_ready": False,
        "signature_source_sha256": {"csv": source_csv_sha, "xlsx": source_xlsx_sha},
        "evidence_manifest_sha256": manifest["manifest_sha256"], "groups": groups,
    }
    foundation["snapshot_sha256"] = cv2.sha256_json(foundation)
    foundation_path = foundation_dir / "tier1_human_review_foundation.json"
    cv2.write_json(foundation_path, foundation, immutable=True)
    source_dir = foundation_dir / "signed-sources"
    source_dir.mkdir()
    shutil.copy2(signed_csv, source_dir / signed_csv.name)
    shutil.copy2(signed_xlsx, source_dir / signed_xlsx.name)
    result = {
        "status": "technical_metadata_only_reconciled", "groups": 12, "unique_pmids": len(all_pmids),
        "qa_rows": len(qa_rows), "qa_errors_after": 0, "scientific_changes": 0,
        "evidence_manifest": str(evidence_manifest_path), "foundation": str(foundation_path),
        "signed_csv": str(signed_out), "qa_csv": str(qa_path),
    }
    cv2.write_json(output / "reconciliation_summary.json", result, immutable=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-manifest", required=True)
    parser.add_argument("--signed-csv", required=True)
    parser.add_argument("--signed-xlsx", required=True)
    parser.add_argument("--output-dir", required=True)
    print(json.dumps(reconcile(parser.parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
