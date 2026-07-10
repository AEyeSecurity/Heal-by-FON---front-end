#!/usr/bin/env python3
"""Run grouped HEAL gene+module interpretations for canon v2."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import unicodedata
import urllib.error
import urllib.request


DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_GROUP_ATTEMPTS = 2
DEFAULT_MAX_WORKERS = 3
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
SCRIPT_DIR = Path(__file__).resolve().parent
PROMPT_PATH = SCRIPT_DIR / "prompt_grouped_llm1.md"
SCHEMA_PATH = SCRIPT_DIR / "grouped_gene_module_interpretation_schema.json"

OUTPUT_FIELDS = [
    "group_id",
    "gene",
    "module_id",
    "module_name",
    "system_within_module",
    "group_size_total",
    "focus_variant_count",
    "interpretation_scope",
    "interpretation_one_sentence_en",
    "interpretation_one_sentence_es",
    "interpretation_long_en",
    "interpretation_long_es",
    "technical_interpretation_en",
    "technical_interpretation_es",
    "final_confidence_level",
    "confidence_rationale_en",
    "confidence_rationale_es",
    "family_notes_en",
    "family_notes_es",
    "recommended_next_review_step_en",
    "recommended_next_review_step_es",
    "requires_professional_review",
    "group_conflict_flag",
    "focus_variant_refs",
    "evidence_used",
    "evidence_limitations",
]

ASCII_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "\u00b5": "u",
    "\u03bc": "u",
    "\u03b2": "beta",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def ascii_text(value) -> str:
    text = clean_str(value)
    for source, replacement in ASCII_REPLACEMENTS.items():
        text = text.replace(source, replacement)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_progress(path: Path, progress: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_payloads(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            payload_json = clean_str(row.get("payload_json"))
            if payload_json:
                rows.append(json.loads(payload_json))
        return rows


def output_text_from_response(response: dict) -> str:
    texts = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(content["text"])
    if texts:
        return "\n".join(texts)
    if response.get("output_text"):
        return clean_str(response.get("output_text"))
    raise ValueError("OpenAI response did not contain output text.")


def call_openai_structured(
    payload: dict,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    schema: dict,
    timeout_seconds: int,
) -> dict:
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Interpret this grouped gene-module payload. Return only JSON matching the schema.\n\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "heal_grouped_gene_module_interpretation",
                "strict": True,
                "schema": schema,
            }
        },
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"OpenAI API http_{error.code}: {detail}") from error
    text = output_text_from_response(parsed)
    return json.loads(text)


def call_openai_with_retries(
    payload: dict,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    schema: dict,
    timeout_seconds: int,
    group_attempts: int,
) -> dict:
    errors = []
    for attempt in range(1, group_attempts + 1):
        try:
            return call_openai_structured(
                payload,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                schema=schema,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
            if attempt < group_attempts:
                time.sleep(min(2.0 * attempt, 5.0))
    raise RuntimeError(" | ".join(errors))


def dry_run_interpretation(payload: dict) -> dict:
    group_conflict = int(payload.get("group_counts", {}).get("clinvar_conflict_rows", 0) or 0) > 0
    confidence = "Conflicting" if group_conflict else "Moderate"
    scope = "conflicting_group_review_needed" if group_conflict else "mixed_signal_with_priority_variants"
    return {
        "group_id": payload.get("group_id", ""),
        "gene": payload.get("gene", ""),
        "module_id": payload.get("module_id", ""),
        "module_name": payload.get("module_name", ""),
        "system_within_module": payload.get("system_within_module", ""),
        "group_size_total": int(payload.get("group_size_total", 0) or 0),
        "focus_variant_count": int(payload.get("focus_variant_count", 0) or 0),
        "interpretation_scope": scope,
        "interpretation_one_sentence_en": "Dry-run placeholder: grouped interpretation requires OpenAI execution for this gene-module set.",
        "interpretation_one_sentence_es": "Placeholder dry-run: la interpretacion agrupada requiere ejecucion con OpenAI para este grupo gen-modulo.",
        "interpretation_long_en": "Dry-run output generated to validate grouped payload shape, schema, and frontend plumbing.",
        "interpretation_long_es": "Salida dry-run generada para validar formato agrupado, schema e integracion con frontend.",
        "technical_interpretation_en": "No model call was made; grouped payload was validated only.",
        "technical_interpretation_es": "No se realizo llamada al modelo; solo se valido el payload agrupado.",
        "final_confidence_level": confidence,
        "confidence_rationale_en": "Dry-run placeholder confidence reflects whether the grouped payload already contains explicit conflict counts.",
        "confidence_rationale_es": "La confianza dry-run refleja si el payload agrupado ya contiene conteos explicitos de conflicto.",
        "family_notes_en": "This grouped result has not yet been interpreted by the LLM.",
        "family_notes_es": "Este resultado agrupado todavia no fue interpretado por la LLM.",
        "recommended_next_review_step_en": "Run grouped interpretation with an OpenAI API key configured.",
        "recommended_next_review_step_es": "Ejecutar la interpretacion agrupada con una API key de OpenAI configurada.",
        "requires_professional_review": group_conflict,
        "group_conflict_flag": group_conflict,
        "focus_variant_refs": [item.get("variant_ref", "") for item in (payload.get("focus_variants") or [])[:12] if item.get("variant_ref")],
        "evidence_used": [
            {"source": "group", "field": "focus_variant_count", "value": str(payload.get("focus_variant_count", 0))},
            {
                "source": "group",
                "field": "clinvar_conflict_rows",
                "value": str(payload.get("group_counts", {}).get("clinvar_conflict_rows", 0)),
            },
        ],
        "evidence_limitations": ["Dry-run output is not a real grouped model interpretation."],
    }


def normalize_output(item: dict, payload: dict, model: str, dry_run: bool) -> dict:
    out = {field: item.get(field, "") for field in OUTPUT_FIELDS}
    out["group_size_total"] = int(item.get("group_size_total", payload.get("group_size_total", 0)) or 0)
    out["focus_variant_count"] = int(item.get("focus_variant_count", payload.get("focus_variant_count", 0)) or 0)
    out["focus_variant_refs"] = compact_json(item.get("focus_variant_refs") or [])
    out["evidence_used"] = compact_json(item.get("evidence_used") or [])
    out["evidence_limitations"] = compact_json(item.get("evidence_limitations") or [])
    out["requires_professional_review"] = str(bool(item.get("requires_professional_review"))).lower()
    out["group_conflict_flag"] = str(bool(item.get("group_conflict_flag"))).lower()
    out["model"] = model
    out["dry_run"] = str(dry_run).lower()
    out["source_group_id"] = payload.get("group_id", "")
    out["variant_detail_artifact"] = payload.get("variant_detail_artifact", "")
    return {key: ascii_text(value) if isinstance(value, str) else value for key, value in out.items()}


def process(payload: dict) -> dict:
    started_at = utc_now()
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model = clean_str(payload.get("model")) or os.environ.get("HEAL_LLM1_MODEL") or DEFAULT_MODEL
    timeout_seconds = int(payload.get("timeoutSeconds") or os.environ.get("HEAL_LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    group_attempts = int(payload.get("groupAttempts") or os.environ.get("HEAL_LLM_GROUP_ATTEMPTS") or DEFAULT_GROUP_ATTEMPTS)
    max_workers = max(1, min(6, int(payload.get("maxWorkers") or os.environ.get("HEAL_LLM_MAX_WORKERS") or DEFAULT_MAX_WORKERS)))
    max_groups = int(payload.get("maxGroups") or 0)
    dry_run = bool(payload.get("dryRun"))
    api_key = clean_str(payload.get("apiKey")) or os.environ.get("HEAL_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""

    if not input_path.exists():
        raise FileNotFoundError(f"Input grouped payload file not found: {input_path}")
    if not dry_run and not api_key:
        raise RuntimeError("HEAL_OPENAI_API_KEY or OPENAI_API_KEY must be configured for grouped interpretation.")

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payloads = read_payloads(input_path)
    if max_groups > 0:
        payloads = payloads[:max_groups]

    progress_json = output_dir / "gene_module_group_interpretation_progress.json"
    interpretations_jsonl = output_dir / "gene_module_group_interpretations.jsonl"
    interpretations_csv = output_dir / "gene_module_group_interpretations.csv"
    errors_csv = output_dir / "gene_module_group_interpretation_errors.csv"
    summary_json = output_dir / "gene_module_group_interpretation_summary.json"
    write_csv(
        interpretations_csv,
        [],
        OUTPUT_FIELDS + ["model", "dry_run", "source_group_id", "variant_detail_artifact"],
    )
    write_csv(errors_csv, [], ["group_id", "gene", "module_id", "error"])
    write_progress(
        progress_json,
        {
            "status": "running",
            "completedGroups": 0,
            "totalGroups": len(payloads),
            "interpretedGroups": 0,
            "errorGroups": 0,
            "currentGroup": None,
            "model": model,
            "startedAt": started_at,
            "updatedAt": utc_now(),
        },
    )

    interpretation_results: dict[int, dict] = {}
    error_results: dict[int, dict] = {}

    def write_partial_outputs(completed_groups: int, current_item: dict | None = None) -> None:
        interpretations = [interpretation_results[index] for index in sorted(interpretation_results)]
        errors = [error_results[index] for index in sorted(error_results)]
        write_jsonl(interpretations_jsonl, interpretations)
        write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_group_id", "variant_detail_artifact"])
        write_csv(errors_csv, errors, ["group_id", "gene", "module_id", "error"])
        write_progress(
            progress_json,
            {
                "status": "running",
                "completedGroups": completed_groups,
                "totalGroups": len(payloads),
                "interpretedGroups": len(interpretations),
                "errorGroups": len(errors),
                "currentGroup": (
                    {
                        "group_id": current_item.get("group_id", ""),
                        "gene": current_item.get("gene", ""),
                        "module_id": current_item.get("module_id", ""),
                    }
                    if current_item
                    else None
                ),
                "model": model,
                "maxWorkers": max_workers,
                "startedAt": started_at,
                "updatedAt": utc_now(),
            },
        )

    def interpret_one(index: int, item: dict) -> tuple[int, dict | None, dict | None]:
        try:
            if dry_run:
                parsed = dry_run_interpretation(item)
            else:
                parsed = call_openai_with_retries(
                    item,
                    api_key=api_key,
                    model=model,
                    system_prompt=system_prompt,
                    schema=schema,
                    timeout_seconds=timeout_seconds,
                    group_attempts=group_attempts,
                )
            return index, normalize_output(parsed, item, model, dry_run), None
        except Exception as error:  # noqa: BLE001
            return index, None, {
                "group_id": item.get("group_id", ""),
                "gene": item.get("gene", ""),
                "module_id": item.get("module_id", ""),
                "error": str(error),
            }

    completed_count = 0
    if dry_run or max_workers == 1:
        for index, item in enumerate(payloads, start=1):
            result_index, interpretation, error = interpret_one(index, item)
            if interpretation:
                interpretation_results[result_index] = interpretation
            if error:
                error_results[result_index] = error
            completed_count += 1
            write_partial_outputs(completed_count, item)
            if not dry_run:
                time.sleep(float(payload.get("delaySeconds") or os.environ.get("HEAL_LLM_DELAY_SECONDS") or 0.2))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {executor.submit(interpret_one, index, item): item for index, item in enumerate(payloads, start=1)}
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                result_index, interpretation, error = future.result()
                if interpretation:
                    interpretation_results[result_index] = interpretation
                if error:
                    error_results[result_index] = error
                completed_count += 1
                write_partial_outputs(completed_count, item)

    interpretations = [interpretation_results[index] for index in sorted(interpretation_results)]
    errors = [error_results[index] for index in sorted(error_results)]
    write_jsonl(interpretations_jsonl, interpretations)
    write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_group_id", "variant_detail_artifact"])
    write_csv(errors_csv, errors, ["group_id", "gene", "module_id", "error"])

    status = "valid" if interpretations and not errors else "warning" if interpretations else "invalid"
    summary = {
        "status": status,
        "errors": [] if interpretations else ["No groups were interpreted."],
        "warnings": [f"{len(errors)} group(s) failed grouped interpretation."] if errors else [],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            "source_groups": len(payloads),
            "interpreted_groups": len(interpretations),
            "error_groups": len(errors),
            "model": model,
            "dry_run": dry_run,
            "max_workers": max_workers,
            "groups_requires_review": sum(1 for row in interpretations if clean_str(row.get("requires_professional_review")) == "true"),
            "groups_with_conflict_flag": sum(1 for row in interpretations if clean_str(row.get("group_conflict_flag")) == "true"),
            "source_variants_total": sum(int(row.get("group_size_total", 0) or 0) for row in interpretations),
            "average_group_size": round(
                sum(int(row.get("group_size_total", 0) or 0) for row in interpretations) / len(interpretations),
                2,
            )
            if interpretations
            else 0,
            "schema": "heal_grouped_gene_module_interpretation",
        },
        "outputs": {
            "groupInterpretationsJsonl": str(interpretations_jsonl),
            "groupInterpretationsCsv": str(interpretations_csv),
            "groupInterpretationErrorsCsv": str(errors_csv),
            "groupInterpretationProgressJson": str(progress_json),
            "groupInterpretationSummaryJson": str(summary_json),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(
        progress_json,
        {
            "status": status,
            "completedGroups": len(payloads),
            "totalGroups": len(payloads),
            "interpretedGroups": len(interpretations),
            "errorGroups": len(errors),
            "currentGroup": None,
            "model": model,
            "maxWorkers": max_workers,
            "startedAt": started_at,
            "updatedAt": utc_now(),
            "completedAt": summary["timestamps"]["completedAt"],
        },
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", help="Base64-encoded JSON payload.")
    parser.add_argument("--input", help="Input grouped payload path.")
    parser.add_argument("--output-dir", help="Output directory.")
    parser.add_argument("--model", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-groups", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.input_json_base64:
            payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        else:
            if not args.input or not args.output_dir:
                raise ValueError("--input and --output-dir are required without --input-json-base64.")
            payload = {"inputPath": args.input, "outputDir": args.output_dir}
            if args.model:
                payload["model"] = args.model
            if args.dry_run:
                payload["dryRun"] = True
            if args.max_groups:
                payload["maxGroups"] = args.max_groups
        result = process(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"valid", "warning"} else 1
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"status": "invalid", "errors": [str(error)], "warnings": []}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
