#!/usr/bin/env python3
"""Compile an explicitly signed human CSV into the executable 12-card gold.

This command never edits the active mechanism registry. It requires an exact
confirmation string and refuses foundations with unresolved technical flags.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402
from build_human_review_foundation import identifiers, packet_map, read_csv, truthy  # noqa: E402


APPROVAL_VALUES = {"aprobado", "approved", "sí", "si", "yes", "true", "1"}


def split_values(value: str) -> list[str]:
    return sorted({item.strip() for item in str(value or "").split("|") if item.strip()})


def signed(value: str) -> bool:
    return str(value or "").strip().lower() in APPROVAL_VALUES


def iso_date(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError:
        parsed = dt.datetime.strptime(text, "%d/%m/%Y").date()
    if parsed > dt.date.today():
        raise ValueError("Signature date cannot be in the future")
    return parsed.isoformat()


def compile_gold(foundation: dict, packets: dict[str, dict], signed_rows: list[dict], evidence_manifest: dict | None = None) -> dict:
    groups = foundation.get("groups") or {}
    if set(groups) != set(cv2.GOLD_GROUPS):
        raise ValueError("Foundation must contain exactly the 12 frozen gold groups")
    unresolved = {
        group_id: [flag for flag in row.get("technical_flags") or [] if flag != "human_signature_pending"]
        for group_id, row in groups.items()
    }
    unresolved = {group_id: flags for group_id, flags in unresolved.items() if flags}
    if unresolved:
        raise ValueError(f"Foundation has unresolved technical flags: {unresolved}")
    by_group = {row.get("Grupo"): row for row in signed_rows}
    if set(by_group) != set(cv2.GOLD_GROUPS) or len(signed_rows) != 12:
        raise ValueError("Signed CSV must contain exactly the 12 frozen gold groups")
    manifest = cv2.gold_manifest_template(packets)
    manifest["approval_status"] = "approved"
    manifest["signature_source_sha256"] = foundation.get("signature_source_sha256") or {}
    manifest["evidence_manifest_sha256"] = (evidence_manifest or {}).get("manifest_sha256") or foundation.get("evidence_manifest_sha256", "")
    manifest["metadata_qa_sha256"] = (evidence_manifest or {}).get("metadata_qa_sha256", "")
    manifest["pubmed_snapshot_manifest_sha256"] = (evidence_manifest or {}).get("pubmed_snapshot_manifest_sha256", "")
    cards = []
    for group_id in cv2.GOLD_GROUPS:
        row = by_group[group_id]
        proposed = groups[group_id]
        packet = packets[group_id]
        if not signed(row.get("Aprobación final", "")):
            raise ValueError(f"Final approval is missing for {group_id}")
        reviewer = str(row.get("Revisor final") or "").strip()
        if not reviewer:
            raise ValueError(f"Final reviewer is missing for {group_id}")
        reviewed_at = iso_date(row.get("Fecha firma", ""))
        if row.get("Hash paquete reconciliado") != packet.get("packet_sha256"):
            raise ValueError(f"Reconciled packet hash changed for {group_id}")
        status = str(row.get("Estado propuesto") or "").strip()
        acceptable_statuses = split_values(row.get("Estados aceptables", ""))
        if status not in cv2.CORE_STATUSES or status not in acceptable_statuses:
            raise ValueError(f"Signed status is invalid for {group_id}: {status}")
        context_usable = truthy(row.get("Contexto utilizable", ""))
        ceiling = str(row.get("Techo de inferencia") or "").strip()
        if ceiling not in cv2.INFERENCE_CEILINGS:
            raise ValueError(f"Signed inference ceiling is invalid for {group_id}: {ceiling}")
        directions = split_values(row.get("Direcciones aceptables", ""))
        if not directions or set(directions) - {"supports_relation", "opposes_relation", "no_dominant_direction", "not_applicable"}:
            raise ValueError(f"Signed directions are invalid for {group_id}: {directions}")
        valid_ids = identifiers(row.get("Fuentes incluidas", ""))
        invalid_ids = identifiers(row.get("Fuentes excluidas", ""))
        packet_ids = {source["evidence_id"] for source in packet.get("source_ledger") or []}
        selected = set(packet.get("selected_evidence_ids") or [])
        if set(valid_ids) - packet_ids or set(valid_ids) - selected:
            raise ValueError(f"Signed valid evidence is not selected for {group_id}")
        if set(invalid_ids) & selected:
            raise ValueError(f"Signed invalid evidence remains selected for {group_id}")
        if status not in set(proposed.get("scientific_status_alternatives") or []):
            raise ValueError(f"Signed status is outside the reviewed alternatives for {group_id}")
        cards.append({
            "group_id": group_id,
            "split": proposed["split"],
            "packet_sha256": packet["packet_sha256"],
            "approval_status": "approved",
            "acceptable_core_statuses": acceptable_statuses,
            "context_usable": context_usable,
            "acceptable_inference_ceilings": [ceiling],
            "valid_evidence_ids": valid_ids,
            "invalid_evidence_ids": invalid_ids,
            "acceptable_directions": directions,
            "required_limitations": [str(row.get("Limitaciones") or "").strip()],
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "signature_reconciliation": str(row.get("Tipo reconciliación") or "original_signed"),
            "signature_source_csv_sha256": str(row.get("Hash firma fuente CSV") or (foundation.get("signature_source_sha256") or {}).get("csv") or ""),
            "signature_source_xlsx_sha256": str(row.get("Hash firma fuente XLSX") or (foundation.get("signature_source_sha256") or {}).get("xlsx") or ""),
            "notes": str(row.get("Correcciones") or "").strip(),
        })
    manifest["cards"] = cards
    manifest["manifest_sha256"] = cv2.sha256_json({key: value for key, value in manifest.items() if key != "manifest_sha256"})
    errors = cv2.validate_gold_manifest(manifest, packets, evidence_manifest=evidence_manifest)
    if errors:
        raise ValueError(f"Signed gold validation failed: {errors}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--foundation", required=True)
    parser.add_argument("--evidence-manifest", required=True)
    parser.add_argument("--signed-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args()
    if args.confirmation != "APPROVE_TIER1_GOLD_12":
        raise ValueError("Exact gold approval confirmation is required")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable signed gold: {output}")
    foundation = json.loads(Path(args.foundation).read_text(encoding="utf-8"))
    evidence_manifest = cv2.read_json(Path(args.evidence_manifest).resolve())
    evidence_errors = cv2.validate_evidence_manifest_artifacts(Path(args.evidence_manifest).resolve())
    if evidence_errors:
        raise ValueError(f"Evidence manifest validation failed: {evidence_errors}")
    packets = packet_map(Path(args.evidence_manifest).resolve())
    gold = compile_gold(foundation, packets, read_csv(Path(args.signed_csv).resolve()), evidence_manifest)
    cv2.write_json(output, gold, immutable=True)
    print(json.dumps({"status": "signed_gold_created", "output": str(output), "groups": 12}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
