"""Bounded English presentation sidecar for grouped prototype reports.

The canonical Spanish report is never replaced.  This module translates only a
deterministic list of client-visible prose fields and rebuilds an English view
with the original genes, variants, counts, modules, and safety constraints.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
MODULE_LABELS_EN = {
    "T1.1": "Core system resilience",
    "T1.2": "Sleep and circadian rhythms",
    "T1.3": "Essential nutrients and cofactors",
    "T1.4": "Immunity and inflammation",
    "T1.5": "Connective tissue and physical resilience",
    "T1.6": "Detoxification and oxidative stress management",
}


def sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _add(items: list[dict], path: str, value: object) -> None:
    text = str(value or "").strip()
    if text:
        items.append({"path": path, "text_es": text})


def translatable_fields(view: dict) -> list[dict]:
    """Return a stable, explicit translation projection; never include facts."""
    items: list[dict] = []
    for key in ("title", "prototype_label", "summary", "coverage_statement", "disclaimer"):
        _add(items, key, view.get(key))
    processing = view.get("processing_status") or {}
    external = view.get("external_evidence_availability") or {}
    _add(items, "processing_status.label_es", processing.get("label_es"))
    _add(items, "external_evidence_availability.label_es", external.get("label_es"))
    for index, row in enumerate(view.get("modules") or []):
        _add(items, f"modules[{index}].title", row.get("title"))
        _add(items, f"modules[{index}].summary", row.get("summary"))
    for section in ("primary_findings", "secondary_findings"):
        for index, row in enumerate(view.get(section) or []):
            _add(items, f"{section}[{index}].headline_es", row.get("headline_es"))
            _add(items, f"{section}[{index}].explanation_es", row.get("explanation_es"))
            _add(items, f"{section}[{index}].client_interpretation_type", row.get("client_interpretation_type"))
            _add(items, f"{section}[{index}].individual_applicability", row.get("individual_applicability"))
            _add(items, f"{section}[{index}].scientific_relationship_confidence", row.get("scientific_relationship_confidence"))
    for section in ("limitations", "cannot_infer"):
        for index, value in enumerate(view.get(section) or []):
            _add(items, f"{section}[{index}]", value)
    return items


def translation_payload(view: dict) -> dict:
    source_sha256 = sha256_json(view)
    fields = translatable_fields(view)
    return {
        "schema_version": "grouped_presentation_translation_request_v1",
        "source_view_sha256": source_sha256,
        "target_language": "en",
        "fields": fields,
    }


def translation_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "source_view_sha256", "target_language", "translations"],
        "properties": {
            "schema_version": {"type": "string", "const": "grouped_presentation_translation_v1"},
            "source_view_sha256": {"type": "string"},
            "target_language": {"type": "string", "const": "en"},
            "translations": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["path", "text_en"],
                    "properties": {"path": {"type": "string"}, "text_en": {"type": "string"}},
                },
            },
        },
    }


SYSTEM_PROMPT = """You are a constrained English translator for a genomic prototype report.
Translate only the supplied Spanish client-visible prose into clear English.
Do not add, omit, reinterpret, soften, strengthen, diagnose, recommend treatment, or change any factual content.
Do not translate identifiers, genes, variants, module IDs, counts, safety limits, or validation status. Return only strict JSON."""


def validate_translation(result: dict, request: dict) -> None:
    if result.get("schema_version") != "grouped_presentation_translation_v1":
        raise ValueError("presentation_translation_schema_version_invalid")
    if result.get("source_view_sha256") != request.get("source_view_sha256") or result.get("target_language") != "en":
        raise ValueError("presentation_translation_source_mismatch")
    expected = [item["path"] for item in request["fields"]]
    actual = [item.get("path") for item in result.get("translations") or []]
    if actual != expected or len(set(actual)) != len(actual):
        raise ValueError("presentation_translation_field_paths_mismatch")
    for row in result["translations"]:
        if not str(row.get("text_en") or "").strip():
            raise ValueError("presentation_translation_empty_text")


def _set_path(target: dict, path: str, value: str) -> None:
    current: Any = target
    for token in path.split(".")[:-1]:
        if "[" in token:
            key, index = token[:-1].split("[", 1)
            current = current[key][int(index)]
        else:
            current = current[token]
    last = path.split(".")[-1]
    if "[" in last:
        key, index = last[:-1].split("[", 1)
        current[key][int(index)] = value
    else:
        current[last] = value


def apply_translation(view: dict, result: dict, request: dict) -> dict:
    validate_translation(result, request)
    translated = copy.deepcopy(view)
    for row in result["translations"]:
        _set_path(translated, row["path"], row["text_en"])
    for section in ("primary_findings", "secondary_findings"):
        for finding in translated.get(section) or []:
            module_id = str(finding.get("module_id") or "")
            if module_id in MODULE_LABELS_EN:
                finding["module_name"] = MODULE_LABELS_EN[module_id]
    translated["schema_version"] = "report_view_model_v3_en"
    translated["presentation_language"] = "en"
    translated["translation_source_sha256"] = request["source_view_sha256"]
    return translated


def translate_with_retry(
    view: dict,
    *,
    call: Callable[[dict, str, dict], tuple[dict, dict]],
) -> tuple[dict, dict, dict]:
    """One technical retry only; semantic/schema failure is not retried."""
    request = translation_payload(view)
    errors: list[str] = []
    for attempt in (1, 2):
        try:
            result, metadata = call(request, SYSTEM_PROMPT, translation_schema())
            validate_translation(result, request)
            metadata = {**metadata, "attempt_count": attempt, "prior_attempt_errors": errors}
            return apply_translation(view, result, request), result, metadata
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
            transient = any(token in str(error).lower() for token in ("http_408", "http_429", "http_500", "http_502", "http_503", "http_504", "timeout", "timed out", "connection reset", "truncated"))
            if attempt == 2 or not transient:
                raise RuntimeError("presentation_translation_unavailable: " + " | ".join(errors)) from error
    raise AssertionError("unreachable")
