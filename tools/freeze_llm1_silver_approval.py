#!/usr/bin/env python3
"""Freeze the five manually approved LLM1 pilot payloads without invoking a model."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


PILOT_CASE_IDS = ("MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def traceability_allowlist(payload: dict) -> dict:
    evidence_ids: set[str] = set()
    variant_refs: set[str] = set()
    def visit(value: object) -> None:
        if isinstance(value, dict):
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id.strip(): evidence_ids.add(evidence_id.strip())
            variant_ref = value.get("variant_ref")
            if isinstance(variant_ref, str) and variant_ref.strip(): variant_refs.add(variant_ref.strip())
            for key in ("variant_refs", "variant_refs_for_audit", "primary_variant_refs"):
                refs = value.get(key)
                if isinstance(refs, list): variant_refs.update(str(ref).strip() for ref in refs if str(ref).strip())
            for key, child in value.items():
                if key != "traceability_allowlist": visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(payload)
    focus_refs = sorted(str(row.get("variant_ref", "")).strip() for row in payload.get("focus_variant_evidence") or [] if str(row.get("variant_ref", "")).strip())
    return {"allowed_evidence_ids": sorted(evidence_ids), "allowed_variant_refs": sorted(variant_refs), "allowed_focus_variant_refs": focus_refs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payloads", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--approval-owner", required=True)
    parser.add_argument("--approval-reference", required=True)
    args = parser.parse_args()

    source = Path(args.payloads).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Output directory must be empty.")

    selected: dict[str, dict] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            group_id = payload.get("group_id")
            if group_id in PILOT_CASE_IDS:
                if group_id in selected:
                    raise ValueError(f"Duplicate pilot payload: {group_id}")
                selected[group_id] = payload
    if set(selected) != set(PILOT_CASE_IDS):
        raise ValueError(f"Expected {PILOT_CASE_IDS}; found {sorted(selected)}")

    approved_at = utc_now()
    frozen = []
    for group_id in PILOT_CASE_IDS:
        payload = selected[group_id]
        gates = payload.get("gates") or {}
        if payload.get("execution_mode") != "dry_run":
            raise ValueError(f"{group_id}: source payload is not dry_run")
        if not gates.get("group_payload_ready"):
            raise ValueError(f"{group_id}: group_payload_ready is false")
        if gates.get("llm1_pilot_ready"):
            raise ValueError(f"{group_id}: source already has llm1_pilot_ready")
        payload["execution_mode"] = "pilot"
        payload["gates"] = {**gates, "llm1_pilot_ready": True}
        payload["traceability_allowlist"] = traceability_allowlist(payload)
        frozen.append(payload)

    payload_path = output_dir / "approved_pilot_payloads_v6.jsonl"
    write_jsonl(payload_path, frozen)
    write_json(
        output_dir / "approval_record.json",
        {
            "schema_version": "llm1_silver_manual_approval_v1",
            "approved_at": approved_at,
            "approval_owner": args.approval_owner,
            "approval_reference": args.approval_reference,
            "source_payloads": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "approved_payloads": str(payload_path),
            "approved_group_ids": list(PILOT_CASE_IDS),
            "payload_sha256_by_group": {row["group_id"]: sha256_json(row) for row in frozen},
            "changes_per_payload": {
                "execution_mode": "dry_run -> pilot",
                "gates.llm1_pilot_ready": "false -> true",
                "traceability_allowlist": "deterministically derived from the frozen payload",
            },
            "model_calls_performed": False,
        },
    )
    print(json.dumps({"status": "approved_payloads_frozen", "output": str(payload_path), "count": len(frozen)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
