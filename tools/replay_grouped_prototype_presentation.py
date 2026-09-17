#!/usr/bin/env python3
"""Replay grouped presentation artifacts from an immutable completed/failed run.

This tool never runs VEP, enrichment, LLM1, or LLM2.  It rebuilds the
deterministic client contracts from the persisted grouped execution state and,
when requested, performs only the explicit English report translation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "services" / "heal-grouped-prototype" / "run_grouped_prototype.py"
DATA_ROOT = Path(r"F:\Heal by FON\data")
JOBS_ROOT = DATA_ROOT / "jobs"


def load_runner():
    spec = importlib.util.spec_from_file_location("heal_grouped_presentation_replay", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def coverage_from_cards(cards: list[dict]) -> dict:
    covered = sum(row.get("coverage_status") == "covered_by_prototype_snapshot" for row in cards)
    return {
        "canonical_group_count": len(cards),
        "covered_count": covered,
        "not_covered_count": len(cards) - covered,
        "groups": [
            {
                "group_id": row.get("group_id"),
                "gene": row.get("gene"),
                "module_id": row.get("module_id"),
                "coverage_status": row.get("coverage_status"),
                "status": row.get("status"),
            }
            for row in cards
        ],
    }


def relative_data_path(path: Path) -> str:
    return str(path.resolve().relative_to(DATA_ROOT.resolve())).replace("/", "\\")


def create_preview_job(source_job: dict, preview_id: str, output_dir: Path, summary: dict, presentation: dict) -> Path:
    artifacts = dict(source_job.get("artifacts") or {})
    mapping = {
        "groupedPrototypeSummaryJson": output_dir / "grouped_prototype_run_summary.json",
        "groupedPrototypeDocx": output_dir / "HEAL_prototipo_cliente.docx",
        "groupedPrototypePdf": output_dir / "HEAL_prototipo_cliente.pdf",
        "groupedPrototypeCardsCsv": output_dir / "cards.csv",
        "groupedPrototypeCoverageCsv": output_dir / "coverage_client.csv",
        "groupedPrototypeCoverageAuditCsv": output_dir / "coverage.csv",
        "groupedPrototypeTechnicalAuditJson": output_dir / "replay_audit.json",
        "groupedPrototypeCardsJson": output_dir / "llm1_cards.json",
        "groupedPrototypeDownstreamJson": output_dir / "grouped_downstream_result_v1.json",
        "groupedPrototypeClientJson": output_dir / "grouped_client_result_v1.json",
        "groupedPrototypeReportViewJson": output_dir / "report_view_model_v3.json",
        "groupedPrototypePresentationStatusJson": output_dir / "presentation_status.json",
    }
    if presentation.get("english", {}).get("client_result"):
        mapping["groupedPrototypeClientEnJson"] = Path(presentation["english"]["client_result"])
    if presentation.get("english", {}).get("report_view_model"):
        mapping["groupedPrototypeReportViewEnJson"] = Path(presentation["english"]["report_view_model"])
    if presentation.get("english", {}).get("docx"):
        mapping["groupedPrototypeDocxEn"] = Path(presentation["english"]["docx"])
    if presentation.get("english", {}).get("pdf"):
        mapping["groupedPrototypePdfEn"] = Path(presentation["english"]["pdf"])
    for key, path in mapping.items():
        if path.exists():
            artifacts[key] = relative_data_path(path)
    preview = json.loads(json.dumps(source_job))
    preview["id"] = preview_id
    preview["status"] = "complete"
    preview["stage"] = "grouped_prototype"
    preview["progress"] = 100
    preview["stageProgress"] = 100
    preview["message"] = "Grouped prototype presentation replay completed"
    preview["error"] = None
    preview["updatedAt"] = summary.get("completed_at")
    preview["createdAt"] = summary.get("started_at")
    preview["presentation_language"] = presentation.get("requested_language", "en")
    preview["preview_of_job_id"] = source_job.get("id")
    preview["result"] = {**(preview.get("result") or {}), "groupedPrototype": summary}
    preview["artifacts"] = artifacts
    target = JOBS_ROOT / f"{preview_id}.json"
    write_json(target, preview)
    return target


def process(source_run: Path, output_dir: Path, *, translate: bool = True, preview_job_path: Path | None = None) -> dict:
    runner = load_runner()
    source_job_path = DATA_ROOT / "jobs" / f"{source_run.name}.json"
    source_job = read_json(source_job_path) if source_job_path.exists() else {"id": source_run.name, "fileName": source_run.name}
    grouped = source_run / "grouped-prototype"
    state_path = grouped / "grouped_prototype_execution_state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Missing immutable grouped execution state: {state_path}")
    state = read_json(state_path)
    cards = state.get("cards") or []
    llm2 = state.get("llm2_result") or {}
    if not cards or not llm2:
        raise ValueError("Grouped state is missing cards or the persisted LLM2 result")
    registry_before = runner.sha256_file(runner.ACTIVE_REGISTRY)
    if registry_before != runner.EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("Active registry hash changed before grouped presentation replay")
    output_dir.mkdir(parents=True, exist_ok=False)
    coverage = coverage_from_cards(cards)
    completeness = {"mode": "observed_variants_only", "absence_semantics": "not_observed_callability_unknown"}
    envelopes_path = grouped / "llm1_prototype_envelopes.json"
    if envelopes_path.exists():
        envelopes = read_json(envelopes_path)
        if envelopes:
            completeness = (envelopes[0].get("payload_v7") or {}).get("input_completeness") or completeness
    source_name = source_job.get("fileName") or source_run.name
    downstream, client, view = runner.build_client_ready_artifacts(
        llm2, cards, coverage, source_name,
        input_completeness=completeness,
        external_evidence_partial=True,
    )
    runner.write_json(output_dir / "grouped_downstream_result_v1.json", downstream)
    runner.write_json(output_dir / "grouped_client_result_v1.json", client)
    runner.write_json(output_dir / "report_view_model_v3.json", view)
    runner.write_json(output_dir / "llm1_cards.json", cards)
    runner.write_json(output_dir / "grouped_global_interpretation_v1.json", llm2)
    runner.write_json(output_dir / "quarantine.json", state.get("quarantines") or [])
    runner.write_docx(view, output_dir / "HEAL_prototipo_cliente.docx")
    runner.write_pdf(view, output_dir / "HEAL_prototipo_cliente.pdf")
    runner.write_csv(output_dir / "cards.csv", client["cards"])
    runner.write_csv(output_dir / "coverage_client.csv", runner.client_readiness.build_client_coverage_rows(client))
    runner.write_csv(output_dir / "coverage.csv", coverage["groups"])

    presentation = {"requested_language": "en" if translate else "es", "english": {"status": "not_requested"}}
    if translate:
        presentation_dir = output_dir / "presentation" / "en"
        english_client = runner.client_readiness.build_client_result(downstream, language="en")
        english_client_path = presentation_dir / "grouped_client_result_v2_en.json"
        runner.write_json(english_client_path, english_client)
        try:
            api_key = os.environ.get("HEAL_OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("presentation_translation_unavailable: missing_api_key")
            english_view, translation_result, translation_metadata = runner.create_english_presentation(
                view, api_key=api_key, timeout_seconds=600,
            )
            english_client["presentation_translation"] = {
                "status": "available", "source_view_sha256": runner.presentation_translation.sha256_json(view),
                "model": translation_metadata.get("effective_model"),
            }
            english_docx = presentation_dir / "HEAL_prototype_development_en.docx"
            english_pdf = presentation_dir / "HEAL_prototype_development_en.pdf"
            runner.write_json(presentation_dir / "translation_request.json", runner.presentation_translation.translation_payload(view))
            runner.write_json(presentation_dir / "translation_result.json", translation_result)
            runner.write_json(presentation_dir / "report_view_model_v3.en.json", english_view)
            runner.write_json(english_client_path, english_client)
            runner.write_json(presentation_dir / "audit" / "raw_translation_response.json", translation_metadata.get("raw_response") or {})
            runner.write_docx(english_view, english_docx, language="en")
            runner.write_pdf(english_view, english_pdf, language="en")
            presentation["english"] = {
                "status": "available", "cards_status": "available",
                "client_result": str(english_client_path),
                "report_view_model": str(presentation_dir / "report_view_model_v3.en.json"),
                "docx": str(english_docx), "pdf": str(english_pdf),
                "model": translation_metadata.get("effective_model"),
            }
        except Exception as error:  # noqa: BLE001
            english_client["presentation_translation"] = {
                "status": "unavailable", "fallback_language": "es",
                "error_code": str(error).split(":", 1)[0],
            }
            runner.write_json(english_client_path, english_client)
            presentation["english"] = {
                "status": "unavailable", "cards_status": "available",
                "client_result": str(english_client_path), "fallback_language": "es",
                "error_code": str(error).split(":", 1)[0],
            }
    runner.write_json(output_dir / "presentation_status.json", presentation)
    structural_quarantines = sum(row.get("quarantine_class") == "structural_contract" for row in state.get("quarantines") or [])
    summary = {
        "schema_version": "grouped_prototype_run_summary_v1",
        "status": "prototype_demo_incomplete" if structural_quarantines else "prototype_demo_ready_automatic",
        "prototype_readiness": "prototype_demo_incomplete" if structural_quarantines else "prototype_demo_ready_automatic",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "counts": {
            "canonical_groups": coverage["canonical_group_count"],
            "scientifically_covered": coverage["covered_count"],
            "valid_interpretation_count": downstream["coverage"]["valid_interpretation_count"],
            "covered_no_observed_variant": downstream["coverage"]["covered_no_observed_variant_count"],
            "quarantined": downstream["coverage"]["quarantined_count"],
            "prioritized_finding_count": downstream["coverage"]["prioritized_finding_count"],
            "not_covered": coverage["not_covered_count"],
        },
        "models": {"llm1": "reused_persisted_output", "llm2": "reused_persisted_output"},
        "telemetry": {"llm_calls": 0, "vep_runs": 0, "enrichment_runs": 0},
        "source_job_id": source_run.name,
        "source_state_sha256": sha256_file(state_path),
        "registry_sha256_before": registry_before,
        "registry_sha256_after": runner.sha256_file(runner.ACTIVE_REGISTRY),
        "outputs": {
            "docx": str(output_dir / "HEAL_prototipo_cliente.docx"),
            "pdf": str(output_dir / "HEAL_prototipo_cliente.pdf"),
            "client_result": str(output_dir / "grouped_client_result_v1.json"),
            "english": presentation.get("english"),
        },
    }
    runner.write_json(output_dir / "grouped_prototype_run_summary.json", summary)
    audit = {
        "schema_version": "grouped_prototype_presentation_replay_v1",
        "source_job_id": source_run.name, "source_state_sha256": summary["source_state_sha256"],
        "llm1_calls": 0, "llm2_calls": 0, "vep_runs": 0, "enrichment_runs": 0,
        "translation_requested": bool(translate),
        "translation_status": presentation.get("english", {}).get("status"),
        "registry_sha256": summary["registry_sha256_after"],
    }
    runner.write_json(output_dir / "replay_audit.json", audit)
    if preview_job_path is not None:
        preview_id = preview_job_path.stem
        create_preview_job(source_job, preview_id, output_dir, summary, presentation)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--preview-job-id")
    parser.add_argument("--no-translate", action="store_true")
    args = parser.parse_args()
    preview_path = JOBS_ROOT / f"{args.preview_job_id}.json" if args.preview_job_id else None
    summary = process(Path(args.source_run).resolve(), Path(args.output_dir).resolve(), translate=not args.no_translate, preview_job_path=preview_path)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
