#!/usr/bin/env python3
"""Replay quarantined LLM1 outputs through the current deterministic lane."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("heal_grouped_prototype_runner", HERE / "run_grouped_prototype.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_contracts():
    spec = importlib.util.spec_from_file_location("heal_grouped_prototype_contracts", HERE / "grouped_contracts.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_fix(old_errors: set[str], new_errors: set[str], audit: list[dict]) -> list[str]:
    fixes: list[str] = []
    removed = old_errors - new_errors
    if removed & {"diagnosis_claim", "llm1_prohibited_language"}:
        fixes.append("clause_polarity_diagnosis")
    if removed & {"individual_gwas_risk", "gwas_causality_or_individual_risk"}:
        fixes.append("clause_polarity_gwas")
    if audit or "llm1_duplicate_evidence" in removed:
        fixes.append("referential_evidence_coalescing")
    if new_errors & {"llm1_evidence_outside_allowlist"}:
        fixes.append("targeted_regeneration_required_with_envelope_v3")
    return fixes


def replay(run_dir: Path, output_dir: Path) -> dict:
    runner = load_runner()
    contracts = load_contracts()
    raw_rows = read_json(run_dir / "raw_responses_audit.json")
    envelopes = {row["scientific_decision"]["group_id"]: row for row in read_json(run_dir / "llm1_prototype_envelopes.json")}
    quarantines = {row["group_id"]: row for row in read_json(run_dir / "quarantine.json")}
    rows: list[dict] = []
    technical: list[dict] = []
    for raw in raw_rows:
        group_id = raw.get("group_id")
        if raw.get("stage") != "llm1" or group_id not in quarantines or not isinstance(raw.get("output"), dict):
            continue
        normalized, normalization_errors, audit = contracts.normalize_evidence_used(raw["output"])
        new_errors = sorted(set(normalization_errors + runner.validate_llm1_output(normalized, envelopes[group_id])))
        old_errors = sorted(set(raw.get("errors") or str(quarantines[group_id].get("error") or "").split(";")))
        fixes = classify_fix(set(old_errors), set(new_errors), audit)
        row = {
            "group_id": group_id,
            "old_errors": "|".join(old_errors),
            "new_errors": "|".join(new_errors),
            "old_status": "quarantined",
            "new_status": "valid" if not new_errors else "targeted_regeneration_required",
            "deterministic_fixes": "|".join(fixes),
            "coalesced_evidence_ids": "|".join(item["evidence_id"] for item in audit),
        }
        rows.append(row)
        technical.append({**row, "normalized_output": normalized, "normalization_audit": audit})
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["group_id", "old_errors", "new_errors", "old_status", "new_status", "deterministic_fixes", "coalesced_evidence_ids"]
    with (output_dir / "quarantine_replay_before_after.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "quarantine_replay_technical.json").write_text(json.dumps(technical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "status": "valid",
        "replayed": len(rows),
        "now_valid": sum(row["new_status"] == "valid" for row in rows),
        "targeted_regeneration_required": sum(row["new_status"] != "valid" for row in rows),
        "groups_requiring_regeneration": [row["group_id"] for row in rows if row["new_status"] != "valid"],
        "outputs": {
            "comparison_csv": str(output_dir / "quarantine_replay_before_after.csv"),
            "technical_json": str(output_dir / "quarantine_replay_technical.json"),
        },
    }
    (output_dir / "quarantine_replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def seed_targeted_regeneration(run_dir: Path, replay_dir: Path, output_dir: Path, payload_output: Path) -> dict:
    """Create a new immutable execution state that calls Luna only for unresolved groups."""
    runner = load_runner()
    original_state = read_json(run_dir / "grouped_prototype_execution_state.json")
    original_envelopes = read_json(run_dir / "llm1_prototype_envelopes.json")
    replay_rows = read_json(replay_dir / "quarantine_replay_technical.json")
    unresolved = {row["group_id"] for row in replay_rows if row["new_status"] != "valid"}
    replay_valid = {row["group_id"]: row for row in replay_rows if row["new_status"] == "valid"}

    snapshot = read_json(runner.DEFAULT_SNAPSHOT)
    candidate = read_json(runner.DEFAULT_CANDIDATE)
    coverage = read_json(runner.DEFAULT_COVERAGE)
    scientific = {row["group_id"]: row for row in snapshot["groups"]}
    payloads: dict[str, dict] = {}
    envelopes_v3: dict[str, dict] = {}
    for envelope in original_envelopes:
        payload = envelope.get("payload_v7") or {}
        group_id = str(payload.get("group_id") or envelope.get("scientific_decision", {}).get("group_id") or "")
        if not group_id or group_id not in scientific:
            continue
        payloads[group_id] = payload
        envelopes_v3[group_id] = runner.build_envelope(payload, scientific[group_id], candidate, snapshot)
        runner.validate_envelope(envelopes_v3[group_id])

    payload_output.parent.mkdir(parents=True, exist_ok=True)
    with payload_output.open("w", encoding="utf-8", newline="\n") as handle:
        for group_id in sorted(payloads):
            handle.write(json.dumps(payloads[group_id], ensure_ascii=False, separators=(",", ":")) + "\n")

    seeded_cards = [
        row for row in original_state.get("cards") or []
        if row.get("status") != "quarantined"
    ]
    for group_id, replay_row in sorted(replay_valid.items()):
        seeded_cards.append(runner.llm1_card(replay_row["normalized_output"], envelopes_v3[group_id]))

    raw_rows = [row for row in original_state.get("raw_rows") or [] if row.get("stage") == "llm1"]
    telemetry = [row for row in original_state.get("telemetry") or [] if row.get("stage") == "llm1"]
    source_binding = {
        "payload": sha256_file(payload_output),
        "snapshot": snapshot["snapshot_sha256"],
        "coverage": coverage["manifest_sha256"],
    }
    state = {
        "schema_version": "grouped_prototype_execution_state_v1",
        "source_binding": source_binding,
        "cards": sorted(seeded_cards, key=lambda row: row["group_id"]),
        "raw_rows": raw_rows,
        "telemetry": telemetry,
        "quarantines": [],
        "envelopes": [envelopes_v3[group_id] for group_id in sorted(envelopes_v3) if group_id not in unresolved],
        "replay_provenance": {
            "source_run": str(run_dir),
            "replay_dir": str(replay_dir),
            "reused_valid_quarantines": sorted(replay_valid),
            "targeted_regeneration_groups": sorted(unresolved),
            "historical_llm1_calls_reused": len(telemetry),
        },
        "updated_at": runner.now_iso(),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "grouped_prototype_execution_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = {
        "status": "targeted_regeneration_seed_ready",
        "seeded_cards": len(seeded_cards),
        "reused_llm1_calls": len(telemetry),
        "targeted_regeneration_groups": sorted(unresolved),
        "payload_path": str(payload_output),
        "output_dir": str(output_dir),
        "source_binding": source_binding,
    }
    (output_dir / "targeted_regeneration_seed_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed-output-dir")
    parser.add_argument("--payload-output-path")
    args = parser.parse_args()
    summary = replay(Path(args.run_dir), Path(args.output_dir))
    if args.seed_output_dir:
        if not args.payload_output_path:
            parser.error("--payload-output-path is required with --seed-output-dir")
        summary["targeted_seed"] = seed_targeted_regeneration(
            Path(args.run_dir), Path(args.output_dir), Path(args.seed_output_dir), Path(args.payload_output_path)
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
