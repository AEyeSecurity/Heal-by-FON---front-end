#!/usr/bin/env python3
"""Explicit, protected English-presentation generation for an existing run."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = load_module("heal_grouped_prototype_runner", SCRIPT_DIR / "run_grouped_prototype.py")
client_readiness = load_module("heal_grouped_client_readiness_translation", SCRIPT_DIR / "client_readiness.py")
translation = load_module("heal_grouped_presentation_translation_script", SCRIPT_DIR / "grouped_presentation_translation.py")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process(payload: dict) -> dict:
    output_dir = Path(payload["outputDir"]).resolve()
    report_path = Path(payload["reportViewPath"]).resolve()
    downstream_path = Path(payload["downstreamPath"]).resolve()
    api_key = str(payload.get("apiKey") or os.environ.get("HEAL_OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("HEAL_OPENAI_API_KEY is required for English presentation generation")
    view = json.loads(report_path.read_text(encoding="utf-8"))
    downstream = json.loads(downstream_path.read_text(encoding="utf-8"))
    translated, result, metadata = runner.create_english_presentation(
        view, api_key=api_key, timeout_seconds=int(payload.get("timeoutSeconds") or 180),
    )
    english_client = client_readiness.build_client_result(downstream, language="en")
    english_client["presentation_translation"] = {
        "status": "available", "source_view_sha256": translation.sha256_json(view),
        "model": metadata.get("effective_model"),
    }
    presentation_dir = output_dir / "presentation" / "en"
    docx = presentation_dir / "HEAL_prototype_development_en.docx"
    pdf = presentation_dir / "HEAL_prototype_development_en.pdf"
    write_json(presentation_dir / "translation_request.json", translation.translation_payload(view))
    write_json(presentation_dir / "translation_result.json", result)
    write_json(presentation_dir / "report_view_model_v3.en.json", translated)
    write_json(presentation_dir / "grouped_client_result_v2_en.json", english_client)
    write_json(presentation_dir / "audit" / "raw_translation_response.json", metadata.get("raw_response") or {})
    runner.write_docx(translated, docx, language="en")
    runner.write_pdf(translated, pdf, language="en")
    summary = {
        "status": "available", "source_view_sha256": translation.sha256_json(view),
        "client_result": str(presentation_dir / "grouped_client_result_v2_en.json"),
        "report_view_model": str(presentation_dir / "report_view_model_v3.en.json"),
        "docx": str(docx), "pdf": str(pdf),
        "telemetry": runner.usage_row(metadata, stage="presentation_translation", group_id="global", role="english_report_translator", elapsed=0.0,
                                        model=os.environ.get("HEAL_PROTOTYPE_TRANSLATION_MODEL", "gpt-5.6-luna")),
    }
    write_json(output_dir / "presentation_status.json", {"requested_language": "en", "english": summary})
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    try:
        process(json.loads(base64.b64decode(args.input_json_base64).decode("utf-8")))
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"status": "unavailable", "error": str(error).split(":", 1)[0]}, ensure_ascii=False))
        raise SystemExit(1)
