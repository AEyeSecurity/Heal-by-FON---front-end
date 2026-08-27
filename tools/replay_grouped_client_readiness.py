#!/usr/bin/env python3
"""Regenerate client-ready grouped artifacts without executing upstream stages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-grouped-prototype" / "run_grouped_prototype.py"


def import_runner():
    spec = importlib.util.spec_from_file_location("heal_grouped_prototype_client_replay", SERVICE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contains_source_failure(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if "source_error" in str(key).lower() and child not in (None, 0, {}, []):
                return True
            if contains_source_failure(child):
                return True
    elif isinstance(value, list):
        return any(contains_source_failure(item) for item in value)
    return False


def process(run_dir: Path, output_dir: Path) -> dict:
    runner = import_runner()
    grouped = run_dir / "grouped-prototype"
    required = {
        "cards": grouped / "llm1_cards.json",
        "llm2": grouped / "grouped_global_interpretation_v1.json",
        "coverage": grouped / "coverage.csv",
        "summary": grouped / "grouped_prototype_run_summary.json",
    }
    missing = [str(path) for path in required.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing immutable replay inputs: " + ", ".join(missing))
    registry_before = sha256(runner.ACTIVE_REGISTRY)
    if registry_before != runner.EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("Active registry hash changed before client-readiness replay")
    cards = read_json(required["cards"])
    llm2 = read_json(required["llm2"])
    old_summary = read_json(required["summary"])
    with required["coverage"].open("r", encoding="utf-8-sig", newline="") as handle:
        coverage_rows = list(csv.DictReader(handle))
    covered = sum(row.get("coverage_status") == "covered_by_prototype_snapshot" for row in coverage_rows)
    coverage = {
        "canonical_group_count": len(coverage_rows),
        "covered_count": covered,
        "not_covered_count": len(coverage_rows) - covered,
    }
    completeness = {
        "mode": "observed_variants_only",
        "absence_semantics": "not_observed_callability_unknown",
    }
    envelopes_path = grouped / "llm1_prototype_envelopes.json"
    if envelopes_path.exists():
        envelopes = read_json(envelopes_path)
        if envelopes:
            completeness = (envelopes[0].get("payload_v7") or {}).get("input_completeness") or completeness
    enrichment_summary_path = run_dir / "enrichment" / "enrichment_quality_summary.json"
    external_partial = (
        (contains_source_failure(read_json(enrichment_summary_path)) if enrichment_summary_path.exists() else False)
        or any(runner.client_readiness.SOURCE_FAILURE.search(str(row)) for row in cards)
    )
    source_view = grouped / "report_view_model_v2.json"
    source_name = read_json(source_view).get("source_file_name", "VCF procesado") if source_view.exists() else "VCF procesado"
    downstream, client, view = runner.build_client_ready_artifacts(
        llm2, cards, coverage, source_name,
        input_completeness=completeness,
        external_evidence_partial=external_partial,
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "grouped_downstream_result_v1.json", downstream)
    write_json(output_dir / "grouped_client_result_v1.json", client)
    write_json(output_dir / "report_view_model_v3.json", view)
    runner.write_docx(view, output_dir / "HEAL_prototipo_cliente.docx")
    runner.write_pdf(view, output_dir / "HEAL_prototipo_cliente.pdf")
    runner.write_csv(output_dir / "cards_client.csv", client["cards"])
    runner.write_csv(output_dir / "coverage_client.csv", runner.client_readiness.build_client_coverage_rows(client))
    manifest = {
        "schema_version": "grouped_client_readiness_replay_v1",
        "source_run_id": run_dir.name,
        "source_artifacts_immutable": True,
        "llm_calls": 0, "vep_runs": 0, "enrichment_runs": 0,
        "source_hashes": {key: sha256(path) for key, path in required.items()},
        "registry_sha256_before": registry_before,
        "registry_sha256_after": sha256(runner.ACTIVE_REGISTRY),
        "snapshot_sha256": old_summary.get("snapshot_sha256"),
        "coverage": client["coverage"],
        "readiness": view["readiness"],
    }
    if manifest["registry_sha256_after"] != registry_before:
        raise RuntimeError("Active registry changed during client-readiness replay")
    write_json(output_dir / "client_readiness_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(process(Path(args.run_dir).resolve(), Path(args.output_dir).resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
