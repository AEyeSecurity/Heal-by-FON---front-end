#!/usr/bin/env python3
"""Run grouped HEAL gene+module interpretations for canon v2."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import datetime as dt
import json
import hashlib
import os
from pathlib import Path
import re
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
PROMPT_V4_PATH = SCRIPT_DIR / "prompt_grouped_llm1_v4.md"
PROMPT_V5_PATH = SCRIPT_DIR / "prompt_grouped_llm1_v5.md"
PROMPT_V6_PATH = SCRIPT_DIR / "prompt_grouped_llm1_v6.md"
PROMPT_LUNA_V7_PATH = SCRIPT_DIR / "prompt_grouped_llm1_luna_v7.md"
SCHEMA_PATH = SCRIPT_DIR / "grouped_gene_module_interpretation_schema.json"
SCHEMA_V5_PATH = SCRIPT_DIR / "grouped_gene_module_interpretation_v5_schema.json"
SCHEMA_V6_PATH = SCRIPT_DIR / "grouped_gene_module_interpretation_v6_schema.json"
SCHEMA_V7_PATH = SCRIPT_DIR / "grouped_gene_module_interpretation_v7_schema.json"

PROMPT_PROFILES = {
    "default_v6": PROMPT_V6_PATH,
    "luna_v7": PROMPT_LUNA_V7_PATH,
}


def resolve_prompt_profile(model: str, requested: str = "") -> tuple[str, Path]:
    profile = clean_str(requested) or clean_str(os.environ.get("HEAL_LLM1_PROMPT_PROFILE")) or "default_v6"
    if profile not in PROMPT_PROFILES:
        raise ValueError(f"Unknown HEAL_LLM1_PROMPT_PROFILE: {profile}")
    compatible = (model == "gpt-5.6-luna" and profile == "luna_v7") or (model != "gpt-5.6-luna" and profile == "default_v6")
    if not compatible:
        raise ValueError(f"Incompatible LLM1 model/prompt profile: {model}/{profile}")
    return profile, PROMPT_PROFILES[profile]

OUTPUT_FIELDS = [
    "group_id",
    "gene",
    "module_id",
    "module_name",
    "system_within_module",
    "group_size_total",
    "focus_variant_count",
    "interpretation_scope",
    "inference_mode",
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
    "review_priority",
    "review_reason_codes",
    "myth_correction_required",
    "interpretation_provenance",
    "disclaimer_required",
    "group_conflict_flag",
    "focus_variant_refs",
    "evidence_used",
    "evidence_limitations",
    "expert_review_conditions",
    "expert_review_candidate",
    "operational_status",
    "experimental_canary",
    "client_visible",
    "eligible_for_llm2",
    "input_completeness_mode",
    "age_band",
    "curation_snapshot_id",
    "evidence_cutoff",
]

CRITICAL_LANGUAGE_PATTERNS = {
    "diagnosis_claim": re.compile(r"\b(diagnos(?:is|ed|e)|diagn[oó]stic[oa]|tiene\s+(?:autismo|adhd|autoinmun)|has\s+(?:autism|adhd|autoimmune))\b", re.I),
    "treatment_or_dose": re.compile(r"\b(start|stop|increase|decrease|take|prescrib|iniciar|suspender|aumentar|reducir|tomar|prescrib)\w*\b.{0,45}\b(medication|drug|dose|mg|supplement|medicaci[oó]n|f[aá]rmaco|dosis|suplement)\w*\b", re.I),
    "individual_gwas_risk": re.compile(r"\b(GWAS|genome[- ]wide)\b.{0,100}\b(your|individual|personal|tu|su)\b.{0,35}\b(risk|riesgo|caus)\w*", re.I),
    "generic_referral": re.compile(r"\b(consult|see|refer|deriv|consulte|consultar)\w*\b.{0,50}\b(doctor|physician|specialist|professional|m[eé]dico|especialista|profesional)\b", re.I),
    "new_testing_request": re.compile(r"\b(obtain|order|request|repeat|realizar|solicitar|pedir|repetir)\w*\b.{0,45}\b(test|testing|laborator|study|panel|an[aá]lisis|estudio)\w*\b", re.I),
}

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


def v5_evidence_ids(payload: dict) -> set[str]:
    ids: set[str] = set()

    def visit(value) -> None:
        if isinstance(value, dict):
            if clean_str(value.get("evidence_id")):
                ids.add(clean_str(value["evidence_id"]))
            ids.update(clean_str(ref) for ref in value.get("evidence_refs") or [] if clean_str(ref))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return ids


def v5_variant_refs(payload: dict) -> set[str]:
    refs: set[str] = set()
    def visit(value) -> None:
        if isinstance(value, dict):
            if clean_str(value.get("variant_ref")):
                refs.add(clean_str(value["variant_ref"]))
            for key in ("variant_refs", "variant_refs_for_audit", "primary_variant_refs"):
                refs.update(clean_str(ref) for ref in value.get(key) or [] if clean_str(ref))
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(payload)
    return refs


def validate_v5_payload(payload: dict, *, dry_run: bool) -> None:
    version = payload.get("payload_schema_version")
    if version not in {"llm1_group_payload_v5", "llm1_group_payload_v6", "llm1_group_payload_v7"}:
        return
    expected_mode = "internal_auto" if version == "llm1_group_payload_v7" else "pilot"
    if not dry_run and payload.get("execution_mode") != expected_mode:
        raise ValueError(f"Bounded LLM1 execution requires execution_mode={expected_mode}.")
    if int(payload.get("compression_metadata", {}).get("estimated_tokens", 0) or 0) > 25_000:
        raise ValueError("V5 payload exceeds the 25,000 token hard limit.")
    gates = payload.get("gates") or {}
    if not gates.get("token_budget_ready"):
        raise ValueError("V5 payload did not pass its deterministic token budget gate.")
    if not dry_run and not gates.get("group_payload_ready"):
        raise ValueError("V5 payload did not pass its deterministic payload gates.")
    if version != "llm1_group_payload_v7" and not dry_run and not gates.get("llm1_pilot_ready"):
        raise ValueError("V5 payload was not approved for the controlled LLM1 pilot.")
    if not payload.get("evidence_coverage", {}).get("reconciled"):
        raise ValueError("V5 evidence coverage is not reconciled.")
    if version in {"llm1_group_payload_v6", "llm1_group_payload_v7"}:
        for focus in payload.get("focus_variant_evidence") or []:
            target_status = clean_str((focus.get("target_gene_annotation") or {}).get("status"))
            if target_status not in {"confirmed", "alternative_transcript"}:
                raise ValueError(f"V6 focus variant lacks target-gene confirmation: {focus.get('variant_ref', '')}")
            local_concordance = clean_str((focus.get("target_gene_annotation") or {}).get("local_consequence_concordance"))
            if local_concordance not in {"concordant", "splice_window_context"}:
                raise ValueError(f"V6 focus variant lacks local/transcript consequence concordance: {focus.get('variant_ref', '')}")
            frequency_relation = clean_str((focus.get("population") or {}).get("frequency_relation"))
            if frequency_relation not in {"observed_alt", "not_available_for_observed_alt"}:
                raise ValueError(f"V6 focus variant has ambiguous allele-frequency semantics: {focus.get('variant_ref', '')}")
        traceability = payload.get("traceability_allowlist") or {}
        expected_evidence = v5_evidence_ids({key: value for key, value in payload.items() if key != "traceability_allowlist"})
        expected_variants = v5_variant_refs({key: value for key, value in payload.items() if key != "traceability_allowlist"})
        expected_focus = {clean_str(row.get("variant_ref")) for row in payload.get("focus_variant_evidence") or [] if clean_str(row.get("variant_ref"))}
        if set(traceability.get("allowed_evidence_ids") or []) != expected_evidence:
            raise ValueError("V6 traceability evidence allowlist is missing or stale.")
        if set(traceability.get("allowed_variant_refs") or []) != expected_variants:
            raise ValueError("V6 traceability variant allowlist is missing or stale.")
        if set(traceability.get("allowed_focus_variant_refs") or []) != expected_focus:
            raise ValueError("V6 traceability focus allowlist is missing or stale.")
        if not dry_run and not gates.get("target_gene_annotation_ready"):
            raise ValueError("V6 target-gene annotation gate did not pass.")
        if not dry_run and not gates.get("allele_specific_frequency_ready"):
            raise ValueError("V6 allele-specific frequency gate did not pass.")
        if not dry_run and not gates.get("gwas_relevance_ready"):
            raise ValueError("V6 GWAS relevance gate did not pass.")
    if version == "llm1_group_payload_v7":
        completeness = payload.get("input_completeness") or {}
        if completeness.get("mode") == "observed_variants_only" and (
            completeness.get("can_assert_hom_ref") or completeness.get("can_assert_not_callable")
        ):
            raise ValueError("V7 sparse input cannot assert hom-ref or not-callable states.")
        if completeness.get("mode") == "observed_variants_only" and completeness.get("absence_semantics") != "not_observed_callability_unknown":
            raise ValueError("V7 sparse absence semantics are unsafe.")
        if not payload.get("age_context", {}).get("enabled"):
            raise ValueError("V7 age band is not enabled.")
        state = payload.get("operational_state") or {}
        if not dry_run and (state.get("status") not in {"eligible", "llm1_running"} or not state.get("llm1_eligible")):
            raise ValueError("V7 group is not eligible for internal LLM1 execution.")
        if not dry_run and not gates.get("llm1_internal_ready"):
            raise ValueError("V7 internal-auto gate did not pass.")


def validate_v5_interpretation(item: dict, payload: dict) -> None:
    allowed_evidence = v5_evidence_ids(payload)
    allowed_focus_variants = {
        clean_str(row.get("variant_ref"))
        for row in payload.get("focus_variant_evidence") or []
        if clean_str(row.get("variant_ref"))
    }
    allowed_variants = v5_variant_refs(payload)
    for evidence in item.get("evidence_used") or []:
        evidence_id = clean_str(evidence.get("evidence_id"))
        variant_ref = clean_str(evidence.get("variant_ref"))
        if not evidence_id or evidence_id not in allowed_evidence:
            raise ValueError(f"Interpretation cited an unknown evidence_id: {evidence_id or '<empty>'}")
        if variant_ref and variant_ref not in allowed_variants:
            raise ValueError(f"Interpretation cited an unknown payload variant_ref: {variant_ref}")
    unknown_focus = set(item.get("focus_variant_refs") or []) - allowed_focus_variants
    if unknown_focus:
        raise ValueError(f"Interpretation introduced unknown focus variant refs: {sorted(unknown_focus)}")
    if payload.get("payload_schema_version") in {"llm1_group_payload_v6", "llm1_group_payload_v7"}:
        priority = clean_str(item.get("review_priority"))
        if priority not in {"none", "optional_contextual", "recommended", "urgent"}:
            raise ValueError(f"Unknown review_priority: {priority or '<empty>'}")
        expected_review = priority in {"recommended", "urgent"}
        if item.get("requires_professional_review") is not expected_review:
            raise ValueError("requires_professional_review is inconsistent with review_priority.")
        mode = clean_str(item.get("inference_mode"))
        if mode not in {"initial_guide", "context_only", "abstained_insufficient_evidence"}:
            raise ValueError(f"Unknown inference_mode: {mode or '<empty>'}")
        if mode == "abstained_insufficient_evidence" and (
            item.get("final_confidence_level") != "Abstain"
            or item.get("interpretation_scope") != "abstained_insufficient_evidence"
        ):
            raise ValueError("Abstention requires matching confidence and interpretation scope.")
    if payload.get("payload_schema_version") == "llm1_group_payload_v7":
        gate = payload.get("expert_review_gate") or {}
        conditions = item.get("expert_review_conditions") or {}
        expected = {
            "strong_evidence": bool(gate.get("condition_1_strong_evidence")),
            "material_scientific_conflict": bool(gate.get("condition_2_material_scientific_conflict")),
            "observed_variant_directly_relevant": bool(gate.get("condition_3_observed_variant_directly_relevant")),
        }
        for name, value in expected.items():
            if conditions.get(name) is not value:
                raise ValueError(f"expert_review condition is inconsistent with deterministic input: {name}")
        condition_names = (
            "strong_evidence", "material_scientific_conflict", "observed_variant_directly_relevant",
            "individual_interpretation_materially_affected", "unresolved_from_available_evidence_context",
        )
        candidate = all(conditions.get(name) is True for name in condition_names)
        if item.get("expert_review_candidate") is not candidate:
            raise ValueError("expert_review_candidate must equal the AND of all five conditions.")
        reason = "unresolved_variant_specific_material_conflict"
        if candidate:
            focus = set(item.get("focus_variant_refs") or [])
            directly_relevant = set(gate.get("directly_relevant_focus_variant_refs") or [])
            limitation_text = " ".join(item.get("evidence_limitations") or [])
            if item.get("inference_mode") != "initial_guide" or item.get("final_confidence_level") != "Conflicting":
                raise ValueError("Expert conflict escalation requires initial_guide with Conflicting confidence.")
            if item.get("review_priority") != "recommended" or reason not in set(item.get("review_reason_codes") or []):
                raise ValueError("Expert conflict escalation requires recommended priority and its dedicated reason code.")
            if not focus or not focus.issubset(directly_relevant):
                raise ValueError("Expert conflict escalation requires an allowlisted directly relevant focus variant.")
            if not re.search(r"\b(conflict|conflicting|unresolved|discrepanc|conflicto|no resuelt|discrepancia)\b", limitation_text, re.I):
                raise ValueError("Expert conflict escalation requires an explicit unresolved-conflict limitation.")
        elif reason in set(item.get("review_reason_codes") or []):
            raise ValueError("Dedicated expert-conflict reason is forbidden unless all five conditions pass.")


def interpretation_text(item: dict) -> str:
    return "\n".join(
        clean_str(value)
        for key, value in item.items()
        if isinstance(value, str) and key.endswith(("_en", "_es"))
    )


def critical_semantic_errors(item: dict, payload: dict) -> list[str]:
    text = interpretation_text(item)
    errors = [code for code, pattern in CRITICAL_LANGUAGE_PATTERNS.items() if pattern.search(text)]
    if payload.get("professional_curation", {}).get("pgx_escalation_allowed") is not True:
        if re.search(r"\b(actionable|accionable|change medication|cambiar medicaci[oó]n|adjust dose|ajustar dosis)\b", text, re.I):
            errors.append("unconfirmed_pgx_actionability")
    if payload.get("gwas_evidence_summary", {}).get("prioritized_clusters") and re.search(
        r"\b(causes?|causa|predicts?|predice|individual risk|riesgo individual)\b", text, re.I
    ):
        errors.append("gwas_causality_or_individual_risk")
    return sorted(set(errors))


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


def default_reasoning_effort(model: str) -> str:
    return "minimal" if model == "gpt-5-mini" else "none"


def call_openai_structured(
    payload: dict,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    schema: dict,
    timeout_seconds: int,
    reasoning_effort: str,
) -> tuple[dict, dict]:
    body = {
        "model": model,
        "store": False,
        "reasoning": {"effort": reasoning_effort},
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
    return json.loads(text), {
        "response_id": clean_str(parsed.get("id")),
        "effective_model": clean_str(parsed.get("model")),
        "usage": parsed.get("usage") or {},
        "raw_output_text": text,
        "raw_response": parsed,
    }


def call_openai_with_retries(
    payload: dict,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    schema: dict,
    timeout_seconds: int,
    group_attempts: int,
    reasoning_effort: str,
) -> tuple[dict, dict]:
    errors = []
    started = time.perf_counter()
    for attempt in range(1, group_attempts + 1):
        try:
            result, metadata = call_openai_structured(
                payload,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                schema=schema,
                timeout_seconds=timeout_seconds,
                reasoning_effort=reasoning_effort,
            )
            metadata.update({"attempt_count": attempt, "elapsed_seconds": round(time.perf_counter() - started, 3)})
            return result, metadata
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
            if attempt < group_attempts:
                time.sleep(min(2.0 * attempt, 5.0))
    raise RuntimeError(" | ".join(errors))


def dry_run_interpretation(payload: dict) -> dict:
    if payload.get("payload_schema_version") in {"llm1_group_payload_v5", "llm1_group_payload_v6", "llm1_group_payload_v7"}:
        focus = payload.get("focus_variant_evidence") or []
        context = payload.get("group_context") or {}
        conflict = bool(payload.get("clinical_evidence_summary", {}).get("conflicting_variant_keys"))
        first_evidence = next((row for row in focus if row.get("evidence_id")), {})
        expert_gate = payload.get("expert_review_gate") or {}
        return {
            "group_id": payload.get("group_id", ""), "gene": payload.get("gene", ""),
            "module_id": payload.get("module_id", ""), "module_name": context.get("module_name", ""),
            "system_within_module": context.get("system_within_module", ""),
            "group_size_total": int(payload.get("deterministic_summary", {}).get("group_size_total", 0) or 0),
            "focus_variant_count": len(focus), "interpretation_scope": "abstained_insufficient_evidence",
            "inference_mode": "abstained_insufficient_evidence",
            "interpretation_one_sentence_en": "Dry-run placeholder; no model call was made.",
            "interpretation_one_sentence_es": "Placeholder dry-run; no se realizo una llamada al modelo.",
            "interpretation_long_en": "The bounded payload and its evidence references were validated only.",
            "interpretation_long_es": "Solo se validaron el payload acotado y sus referencias de evidencia.",
            "technical_interpretation_en": "No biological interpretation was generated.",
            "technical_interpretation_es": "No se genero interpretacion biologica.",
            "final_confidence_level": "Abstain", "confidence_rationale_en": "Dry-run mode.",
            "confidence_rationale_es": "Modo dry-run.", "family_notes_en": "Not for individual use.",
            "family_notes_es": "No apto para uso individual.", "recommended_next_review_step_en": "Professional payload review.",
            "recommended_next_review_step_es": "Revision profesional del payload.",
            "review_priority": "none", "review_reason_codes": [], "myth_correction_required": False,
            "expert_review_conditions": {
                "strong_evidence": bool(expert_gate.get("condition_1_strong_evidence")),
                "material_scientific_conflict": bool(expert_gate.get("condition_2_material_scientific_conflict")),
                "observed_variant_directly_relevant": bool(expert_gate.get("condition_3_observed_variant_directly_relevant")),
                "individual_interpretation_materially_affected": False,
                "unresolved_from_available_evidence_context": False,
            },
            "expert_review_candidate": False,
            "requires_professional_review": False, "group_conflict_flag": conflict,
            "focus_variant_refs": [row.get("variant_ref", "") for row in focus if row.get("variant_ref")],
            "evidence_used": ([{"evidence_id": first_evidence.get("evidence_id"), "variant_ref": first_evidence.get("variant_ref", ""), "source": "v5_payload", "field": "payload_validation", "value": "validated"}] if first_evidence else []),
            "evidence_limitations": ["Dry-run output is not a model interpretation."],
        }
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
    item["focus_variant_refs"] = list(dict.fromkeys(item.get("focus_variant_refs") or []))
    item["review_reason_codes"] = list(dict.fromkeys(item.get("review_reason_codes") or []))
    out = {field: item.get(field, "") for field in OUTPUT_FIELDS}
    out["group_size_total"] = int(item.get("group_size_total", payload.get("group_size_total", 0)) or 0)
    out["focus_variant_count"] = int(item.get("focus_variant_count", payload.get("focus_variant_count", 0)) or 0)
    out["focus_variant_refs"] = compact_json(item.get("focus_variant_refs") or [])
    out["evidence_used"] = compact_json(item.get("evidence_used") or [])
    out["evidence_limitations"] = compact_json(item.get("evidence_limitations") or [])
    out["expert_review_conditions"] = compact_json(item.get("expert_review_conditions") or {})
    out["expert_review_candidate"] = str(bool(item.get("expert_review_candidate"))).lower()
    out["review_reason_codes"] = compact_json(item.get("review_reason_codes") or [])
    priority = clean_str(item.get("review_priority"))
    out["requires_professional_review"] = str(priority in {"recommended", "urgent"}).lower()
    out["myth_correction_required"] = str(bool(item.get("myth_correction_required"))).lower()
    out["interpretation_provenance"] = "llm_generated"
    out["disclaimer_required"] = "true"
    out["group_conflict_flag"] = str(bool(item.get("group_conflict_flag"))).lower()
    out["model"] = model
    out["dry_run"] = str(dry_run).lower()
    out["source_group_id"] = payload.get("group_id", "")
    out["variant_detail_artifact"] = payload.get("variant_detail_artifact", "") or payload.get("provenance", {}).get("variant_detail_artifact", "")
    release = payload.get("release_context") or {}
    out["operational_status"] = "valid" if not dry_run else payload.get("operational_state", {}).get("status", "")
    out["experimental_canary"] = str(bool(release.get("experimental_canary"))).lower()
    out["client_visible"] = str(bool(release.get("client_visible"))).lower()
    out["eligible_for_llm2"] = str(bool(release.get("eligible_for_llm2"))).lower()
    out["input_completeness_mode"] = clean_str(payload.get("input_completeness", {}).get("mode"))
    out["age_band"] = clean_str(payload.get("age_context", {}).get("band"))
    out["curation_snapshot_id"] = clean_str(release.get("curation_snapshot_id"))
    out["evidence_cutoff"] = clean_str(release.get("evidence_cutoff"))
    return {key: ascii_text(value) if isinstance(value, str) else value for key, value in out.items()}


def process(payload: dict) -> dict:
    started_at = utc_now()
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model = clean_str(payload.get("model")) or os.environ.get("HEAL_LLM1_MODEL") or DEFAULT_MODEL
    prompt_profile = clean_str(payload.get("promptProfile")) or os.environ.get("HEAL_LLM1_PROMPT_PROFILE") or "default_v6"
    reasoning_effort = clean_str(payload.get("reasoningEffort")) or os.environ.get("HEAL_LLM1_REASONING_EFFORT") or default_reasoning_effort(model)
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

    payloads = read_payloads(input_path)
    versions = {item.get("payload_schema_version") for item in payloads}
    if len(versions) > 1:
        raise ValueError("Grouped interpretation input cannot mix payload schema versions.")
    version = next(iter(versions), "")
    if version in {"llm1_group_payload_v6", "llm1_group_payload_v7"}:
        schema_path = SCHEMA_V7_PATH if version == "llm1_group_payload_v7" else SCHEMA_V6_PATH
        prompt_profile, prompt_path = resolve_prompt_profile(model, prompt_profile)
        if not dry_run:
            max_workers = min(max_workers, 2)
    elif version == "llm1_group_payload_v5":
        schema_path = SCHEMA_V5_PATH
        prompt_path = PROMPT_V5_PATH
    elif version == "llm1_group_payload_v4":
        schema_path = SCHEMA_PATH
        prompt_path = PROMPT_V4_PATH
    else:
        schema_path = SCHEMA_PATH
        prompt_path = PROMPT_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    system_prompt = prompt_path.read_text(encoding="utf-8")
    prompt_sha256 = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    if max_groups > 0:
        payloads = payloads[:max_groups]

    progress_json = output_dir / "gene_module_group_interpretation_progress.json"
    interpretations_jsonl = output_dir / "gene_module_group_interpretations.jsonl"
    interpretations_csv = output_dir / "gene_module_group_interpretations.csv"
    errors_csv = output_dir / "gene_module_group_interpretation_errors.csv"
    quarantine_jsonl = output_dir / "gene_module_group_quarantine.jsonl"
    cards_json = output_dir / "llm1_group_cards.json"
    raw_responses_jsonl = output_dir / "gene_module_group_interpretation_raw_responses.jsonl"
    call_audit_csv = output_dir / "gene_module_group_interpretation_call_audit.csv"
    prompt_snapshot = output_dir / "llm1_pilot_prompt_snapshot.md"
    schema_snapshot = output_dir / "llm1_pilot_response_schema_snapshot.json"
    summary_json = output_dir / "gene_module_group_interpretation_summary.json"
    prompt_snapshot.write_text(system_prompt, encoding="utf-8")
    schema_snapshot.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
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
            "promptProfile": prompt_profile,
            "startedAt": started_at,
            "updatedAt": utc_now(),
        },
    )

    interpretation_results: dict[int, dict] = {}
    error_results: dict[int, dict] = {}
    call_results: dict[int, dict] = {}

    def write_partial_outputs(completed_groups: int, current_item: dict | None = None) -> None:
        interpretations = [interpretation_results[index] for index in sorted(interpretation_results)]
        errors = [error_results[index] for index in sorted(error_results)]
        calls = [call_results[index] for index in sorted(call_results)]
        write_jsonl(interpretations_jsonl, interpretations)
        write_jsonl(raw_responses_jsonl, calls)
        write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_group_id", "variant_detail_artifact"])
        write_csv(errors_csv, errors, ["group_id", "gene", "module_id", "error"])
        write_csv(
            call_audit_csv,
            calls,
            ["group_id", "model", "prompt_profile", "prompt_sha256", "effective_model", "reasoning_effort", "status", "response_id", "attempt_count", "elapsed_seconds", "input_tokens", "output_tokens", "total_tokens", "error"],
        )
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

    def interpret_one(index: int, item: dict) -> tuple[int, dict | None, dict | None, dict]:
        call_started_at = None
        call_metadata = {
            "group_id": item.get("group_id", ""), "model": model, "status": "dry_run" if dry_run else "running",
            "response_id": "", "attempt_count": 0, "elapsed_seconds": 0, "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "error": "", "raw_output_text": "",
            "raw_response": {}, "effective_model": "",
            "reasoning_effort": reasoning_effort,
            "prompt_profile": prompt_profile,
            "prompt_sha256": prompt_sha256,
        }
        try:
            validate_v5_payload(item, dry_run=dry_run)
            if dry_run:
                parsed = dry_run_interpretation(item)
            else:
                call_started_at = time.perf_counter()
                parsed, response_metadata = call_openai_with_retries(
                    item,
                    api_key=api_key,
                    model=model,
                    system_prompt=system_prompt,
                    schema=schema,
                    timeout_seconds=timeout_seconds,
                    group_attempts=group_attempts,
                    reasoning_effort=reasoning_effort,
                )
                usage = response_metadata.get("usage") or {}
                call_metadata.update({
                    "status": "success",
                    "response_id": response_metadata.get("response_id", ""),
                    "effective_model": response_metadata.get("effective_model", ""),
                    "attempt_count": response_metadata.get("attempt_count", 0),
                    "elapsed_seconds": response_metadata.get("elapsed_seconds", 0),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "raw_output_text": response_metadata.get("raw_output_text", ""),
                    "raw_response": response_metadata.get("raw_response") or {},
                })
            if item.get("payload_schema_version") in {"llm1_group_payload_v5", "llm1_group_payload_v6", "llm1_group_payload_v7"}:
                validate_v5_interpretation(parsed, item)
            semantic_errors = critical_semantic_errors(parsed, item) if not dry_run else []
            if semantic_errors:
                raise ValueError(f"critical_semantic_error:{','.join(semantic_errors)}")
            return index, normalize_output(parsed, item, model, dry_run), None, call_metadata
        except Exception as error:  # noqa: BLE001
            call_metadata.update({"status": "failed", "error": str(error)})
            if call_started_at is not None:
                call_metadata.update({
                    "attempt_count": group_attempts,
                    "elapsed_seconds": round(time.perf_counter() - call_started_at, 3),
                })
            return index, None, {
                "group_id": item.get("group_id", ""),
                "gene": item.get("gene", ""),
                "module_id": item.get("module_id", ""),
                "error": str(error),
            }, call_metadata

    completed_count = 0
    if dry_run or max_workers == 1:
        for index, item in enumerate(payloads, start=1):
            result_index, interpretation, error, call_metadata = interpret_one(index, item)
            if interpretation:
                interpretation_results[result_index] = interpretation
            if error:
                error_results[result_index] = error
            call_results[result_index] = call_metadata
            completed_count += 1
            write_partial_outputs(completed_count, item)
            if not dry_run:
                time.sleep(float(payload.get("delaySeconds") or os.environ.get("HEAL_LLM_DELAY_SECONDS") or 0.2))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {executor.submit(interpret_one, index, item): item for index, item in enumerate(payloads, start=1)}
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                result_index, interpretation, error, call_metadata = future.result()
                if interpretation:
                    interpretation_results[result_index] = interpretation
                if error:
                    error_results[result_index] = error
                call_results[result_index] = call_metadata
                completed_count += 1
                write_partial_outputs(completed_count, item)

    interpretations = [interpretation_results[index] for index in sorted(interpretation_results)]
    errors = [error_results[index] for index in sorted(error_results)]
    calls = [call_results[index] for index in sorted(call_results)]
    quarantine = [
        {**row, "operational_status": "quarantined", "quarantined_at": utc_now()}
        for row in errors
        if "critical_semantic_error:" in clean_str(row.get("error"))
    ]
    write_jsonl(interpretations_jsonl, interpretations)
    write_jsonl(raw_responses_jsonl, calls)
    write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_group_id", "variant_detail_artifact"])
    write_csv(errors_csv, errors, ["group_id", "gene", "module_id", "error"])
    write_jsonl(quarantine_jsonl, quarantine)
    cards = [
        {
            **row,
            "card_layer": "summary_and_traceability",
            "coverage_status": "interpretable",
        }
        for row in interpretations
    ] + [
        {
            "group_id": row.get("group_id", ""),
            "gene": row.get("gene", ""),
            "module_id": row.get("module_id", ""),
            "operational_status": "quarantined" if "critical_semantic_error:" in clean_str(row.get("error")) else "technical_failure",
            "coverage_status": "result_unavailable",
            "error": row.get("error", ""),
        }
        for row in errors
    ]
    cards_json.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(
        call_audit_csv,
        calls,
        ["group_id", "model", "prompt_profile", "prompt_sha256", "effective_model", "reasoning_effort", "status", "response_id", "attempt_count", "elapsed_seconds", "input_tokens", "output_tokens", "total_tokens", "error"],
    )

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
            "prompt_profile": prompt_profile,
            "prompt_sha256": prompt_sha256,
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
            "groupInterpretationRawResponsesJsonl": str(raw_responses_jsonl),
            "groupInterpretationCallAuditCsv": str(call_audit_csv),
            "groupQuarantineJsonl": str(quarantine_jsonl),
            "groupCardsJson": str(cards_json),
            "llm1PilotPromptSnapshotMd": str(prompt_snapshot),
            "llm1PilotResponseSchemaSnapshotJson": str(schema_snapshot),
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
            "promptProfile": prompt_profile,
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
