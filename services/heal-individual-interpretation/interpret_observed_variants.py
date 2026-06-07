#!/usr/bin/env python3
"""Prepare and run HEAL individual observed-variant interpretations."""

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
import urllib.error
import urllib.request


DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_ROW_ATTEMPTS = 2
DEFAULT_MAX_WORKERS = 3
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
SCRIPT_DIR = Path(__file__).resolve().parent
PROMPT_PATH = SCRIPT_DIR / "prompt_llm1.md"
SCHEMA_PATH = SCRIPT_DIR / "individual_variant_interpretation_schema.json"

PAYLOAD_FIELDS = [
    "row_id",
    "Gene",
    "SNP (rsID)",
    "Category / Module",
    "Canon Effect",
    "Genotype",
    "gt_alleles",
    "patient_gt_alleles",
    "Zygosity",
    "Ref/Alt",
    "patient_ref",
    "patient_alt_catalog",
    "patient_observed_alt_alleles",
    "allele_match_summary",
    "source_group",
    "match_status",
    "Review Status",
    "Notes",
    "vep_most_severe_consequence",
    "vep_variant_class",
    "vep_picked_gene_symbol",
    "vep_picked_transcript",
    "vep_canonical",
    "vep_mane_select",
    "vep_hgvsc",
    "vep_hgvsp",
    "vep_amino_acids",
    "vep_protein_position",
    "vep_sift_prediction",
    "vep_sift_score",
    "vep_polyphen_prediction",
    "vep_polyphen_score",
    "vep_cadd_phred",
    "vep_revel_score",
    "vep_spliceai",
    "clinvar_normalized_classification",
    "clinvar_evidence_strength",
    "clinvar_conflict_flag",
    "clinvar_germline_classification",
    "clinvar_review_status",
    "population_frequency_summary",
    "population_max_frequency",
    "gwas_association_count",
    "gwas_top_traits",
    "gwas_min_pvalue",
    "gwas_top_associations",
    "pharmgkb_clinical_summary",
    "pharmgkb_clinical_evidence_levels",
    "pharmgkb_clinical_chemicals",
    "pharmgkb_allele_phenotypes",
    "interpretation_readiness_summary",
    "plus_source_error_sources",
]

OUTPUT_FIELDS = [
    "row_id",
    "gene",
    "rsID",
    "category",
    "canon_effect",
    "observed_genotype",
    "zygosity",
    "ref_alt",
    "variant_observed_in_vcf",
    "interpretation_scope",
    "interpretation_one_sentence_en",
    "interpretation_one_sentence_es",
    "interpretation_long_en",
    "interpretation_long_es",
    "technical_interpretation_en",
    "technical_interpretation_es",
    "llm_proposed_confidence_level",
    "preliminary_confidence_from_input",
    "final_confidence_level",
    "confidence_rationale_en",
    "confidence_rationale_es",
    "technical_notes_en",
    "technical_notes_es",
    "family_notes_en",
    "family_notes_es",
    "recommended_next_review_step_en",
    "recommended_next_review_step_es",
    "requires_professional_review",
    "gene_or_locus_ambiguity_flag",
    "evidence_conflict_flag",
    "evidence_used",
    "evidence_limitations",
]

NONCODING_WEAK_TERMS = {
    "intron_variant",
    "intergenic_variant",
    "upstream_gene_variant",
    "downstream_gene_variant",
    "5_prime_utr_variant",
    "3_prime_utr_variant",
    "regulatory_region_variant",
    "promoter",
}
CODING_OR_FUNCTIONAL_TERMS = {
    "missense_variant",
    "synonymous_variant",
    "start_lost",
    "stop_gained",
    "splice",
    "coding_sequence_variant",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def truthy(value) -> bool:
    return clean_str(value).lower() in {"1", "true", "yes", "y"}


def lower_blob(*values) -> str:
    return " ".join(clean_str(value).lower() for value in values if clean_str(value))


def contains_any(text: str, terms: set[str] | list[str] | tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
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


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def make_payload(row: dict) -> dict:
    payload = {field: clean_str(row.get(field)) for field in PAYLOAD_FIELDS}
    payload["preliminary_confidence_from_input"] = clean_str(row.get("Confidence Level"))
    return payload


def flatten_for_payload_csv(payload: dict) -> dict:
    return dict(payload)


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
                    "Interpret this single observed variant row. Return only JSON matching the schema.\n\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "heal_individual_variant_interpretation",
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
    row_attempts: int,
) -> dict:
    errors = []
    for attempt in range(1, row_attempts + 1):
        try:
            return call_openai_structured(
                payload,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                schema=schema,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001 - row-level retry keeps the batch moving.
            errors.append(str(error))
            if attempt < row_attempts:
                time.sleep(min(2.0 * attempt, 5.0))
    raise RuntimeError(" | ".join(errors))


def dry_run_interpretation(payload: dict) -> dict:
    conflict = truthy(payload.get("clinvar_conflict_flag")) or "conflict" in payload.get(
        "clinvar_normalized_classification", ""
    )
    final_confidence = "Conflicting" if conflict else payload.get("preliminary_confidence_from_input") or "Moderate"
    if final_confidence not in {"High", "Moderate", "Low", "Conflicting"}:
        final_confidence = "Moderate"
    scope = "conflicting_review_needed" if conflict else "benign_contextual"
    if clean_str(payload.get("pharmgkb_clinical_summary")):
        scope = "pharmacogenomic" if not conflict else scope
    return {
        "row_id": payload.get("row_id", ""),
        "gene": payload.get("Gene", ""),
        "rsID": payload.get("SNP (rsID)", ""),
        "category": payload.get("Category / Module", ""),
        "canon_effect": payload.get("Canon Effect", ""),
        "observed_genotype": payload.get("Genotype") or payload.get("patient_gt_alleles", ""),
        "zygosity": payload.get("Zygosity", ""),
        "ref_alt": payload.get("Ref/Alt", ""),
        "variant_observed_in_vcf": True,
        "interpretation_scope": scope,
        "interpretation_one_sentence_en": "Dry-run placeholder: observed variant interpretation requires OpenAI execution.",
        "interpretation_one_sentence_es": "Placeholder dry-run: la interpretacion de la variante observada requiere ejecucion con OpenAI.",
        "interpretation_long_en": "Dry-run output generated to validate file shape, schema, and frontend plumbing.",
        "interpretation_long_es": "Salida dry-run generada para validar formato, schema e integracion con frontend.",
        "technical_interpretation_en": "No model call was made.",
        "technical_interpretation_es": "No se realizo llamada al modelo.",
        "llm_proposed_confidence_level": final_confidence,
        "preliminary_confidence_from_input": payload.get("preliminary_confidence_from_input", ""),
        "final_confidence_level": final_confidence,
        "confidence_rationale_en": "Dry-run placeholder confidence mirrors input/conflict rules.",
        "confidence_rationale_es": "La confianza dry-run replica reglas basicas de entrada/conflicto.",
        "technical_notes_en": "Dry-run mode.",
        "technical_notes_es": "Modo dry-run.",
        "family_notes_en": "This row has not yet been interpreted by the LLM.",
        "family_notes_es": "Esta fila todavia no fue interpretada por la LLM.",
        "recommended_next_review_step_en": "Run the individual interpretation module with an OpenAI API key configured.",
        "recommended_next_review_step_es": "Ejecutar el modulo de interpretacion individual con una API key de OpenAI configurada.",
        "requires_professional_review": conflict,
        "gene_or_locus_ambiguity_flag": clean_str(payload.get("Gene")).upper()
        != clean_str(payload.get("vep_picked_gene_symbol")).upper()
        and bool(clean_str(payload.get("vep_picked_gene_symbol"))),
        "evidence_conflict_flag": conflict,
        "evidence_used": [
            {"source": "Canon", "field": "Canon Effect", "value": payload.get("Canon Effect", "")},
            {
                "source": "ClinVar",
                "field": "clinvar_normalized_classification",
                "value": payload.get("clinvar_normalized_classification", ""),
            },
        ],
        "evidence_limitations": ["Dry-run output is not a real model interpretation."],
    }


def real_clinvar_conflict(payload: dict) -> bool:
    classification = lower_blob(payload.get("clinvar_normalized_classification"))
    evidence_strength = lower_blob(payload.get("clinvar_evidence_strength"))
    germline = lower_blob(payload.get("clinvar_germline_classification"))
    review = lower_blob(payload.get("clinvar_review_status"))
    if "no conflicts" in review and "conflicting" not in classification and "conflicting" not in evidence_strength:
        return False
    conflict_blob = lower_blob(classification, evidence_strength, germline, review)
    return (
        "conflicting_pathogenicity" in conflict_blob
        or "conflicting classifications" in conflict_blob
        or "conflicting classification" in conflict_blob
        or evidence_strength == "conflicting"
    )


def pathogenic_or_likely_pathogenic(payload: dict) -> bool:
    blob = lower_blob(
        payload.get("clinvar_normalized_classification"),
        payload.get("clinvar_germline_classification"),
    )
    return ("pathogenic" in blob or "likely pathogenic" in blob) and "benign" not in blob


def pharmacogenomic_context(payload: dict) -> bool:
    blob = lower_blob(
        payload.get("pharmgkb_clinical_summary"),
        payload.get("pharmgkb_clinical_evidence_levels"),
        payload.get("pharmgkb_clinical_chemicals"),
        payload.get("pharmgkb_allele_phenotypes"),
        payload.get("clinvar_normalized_classification"),
    )
    return bool(blob) and (
        "pharmgkb" in blob
        or "drug_response" in blob
        or "drug response" in blob
        or "level" in blob
        or bool(clean_str(payload.get("pharmgkb_clinical_summary")))
    )


def gwas_only_context(payload: dict) -> bool:
    gwas_count = clean_str(payload.get("gwas_association_count"))
    return bool(gwas_count and gwas_count != "0") and not pharmacogenomic_context(payload)


def has_direct_clinvar_support(payload: dict) -> bool:
    blob = lower_blob(
        payload.get("clinvar_normalized_classification"),
        payload.get("clinvar_germline_classification"),
        payload.get("clinvar_review_status"),
    )
    return bool(blob) and "not_reported" not in blob and "not reported" not in blob


def noncoding_weak_marker(payload: dict) -> bool:
    consequence = lower_blob(payload.get("vep_most_severe_consequence"))
    if contains_any(consequence, CODING_OR_FUNCTIONAL_TERMS):
        return False
    if not contains_any(consequence, NONCODING_WEAK_TERMS):
        return False
    clinvar_blob = lower_blob(
        payload.get("clinvar_normalized_classification"),
        payload.get("clinvar_germline_classification"),
    )
    if pathogenic_or_likely_pathogenic(payload) or "drug_response" in clinvar_blob:
        return False
    return True


def low_deliverable_weight_marker(payload: dict) -> bool:
    if not noncoding_weak_marker(payload):
        return False
    if real_clinvar_conflict(payload) or pathogenic_or_likely_pathogenic(payload) or pharmacogenomic_context(payload):
        return False
    if has_direct_clinvar_support(payload) and "benign" not in lower_blob(payload.get("clinvar_normalized_classification")):
        return False
    return gwas_only_context(payload) or not has_direct_clinvar_support(payload)


def calibrated_scope(final_confidence: str, payload: dict, current_scope: str) -> str:
    if final_confidence == "Conflicting":
        return "conflicting_review_needed"
    if final_confidence == "Low":
        if noncoding_weak_marker(payload):
            return "association_only"
        return "limited_evidence"
    if pharmacogenomic_context(payload) and final_confidence in {"High", "Moderate"}:
        return "pharmacogenomic"
    return current_scope or "benign_contextual"


def calibrated_professional_review(final_confidence: str, payload: dict, gene_ambiguity: bool) -> bool:
    match_status = lower_blob(payload.get("match_status"))
    consequence = lower_blob(payload.get("vep_most_severe_consequence"))
    has_pharm_context = pharmacogenomic_context(payload) and final_confidence in {"High", "Moderate", "Conflicting"}
    alt_review_material = (
        "match_likely_needs_alt_review" in match_status
        and (
            contains_any(consequence, CODING_OR_FUNCTIONAL_TERMS)
            or real_clinvar_conflict(payload)
            or pathogenic_or_likely_pathogenic(payload)
            or has_pharm_context
        )
    )
    return (
        final_confidence == "Conflicting"
        or real_clinvar_conflict(payload)
        or pathogenic_or_likely_pathogenic(payload)
        or has_pharm_context
        or alt_review_material
        or (gene_ambiguity and final_confidence != "Low")
    )


def calibrate_confidence(item: dict, payload: dict) -> tuple[str, str]:
    current = clean_str(item.get("final_confidence_level")) or clean_str(item.get("llm_proposed_confidence_level")) or "Moderate"
    preliminary = clean_str(payload.get("preliminary_confidence_from_input"))
    match_status = lower_blob(payload.get("match_status"))
    consequence = lower_blob(payload.get("vep_most_severe_consequence"))

    if real_clinvar_conflict(payload) and (
        pathogenic_or_likely_pathogenic(payload)
        or contains_any(consequence, CODING_OR_FUNCTIONAL_TERMS)
        or "reviewed by expert panel" in lower_blob(payload.get("clinvar_review_status"))
    ):
        return "Conflicting", "Explicit ClinVar/pathogenicity conflict changes the interpretation."

    if current == "Conflicting" and not real_clinvar_conflict(payload):
        return "Moderate", "Model conflict downgraded because explicit ClinVar/pathogenicity conflict is not present."

    if low_deliverable_weight_marker(payload):
        return "Low", "Observed marker has low deliverable weight because support is mainly non-coding, indirect, or association-only."

    if noncoding_weak_marker(payload):
        if current == "High":
            return "Moderate", "Non-coding or indirect marker is capped at Moderate unless direct support is stronger."
        return current, "Non-coding marker confidence retained after evidence-based low/conflict checks."

    if current == "High" and "match_likely_needs_alt_review" in match_status:
        return "Moderate", "Non-strict ALT representation caps confidence at Moderate."

    if current == "High" and preliminary == "Moderate" and not contains_any(consequence, CODING_OR_FUNCTIONAL_TERMS):
        return "Moderate", "High model confidence capped because source confidence is moderate and evidence is indirect."

    if current not in {"High", "Moderate", "Low", "Conflicting"}:
        return "Moderate", "Invalid confidence normalized to Moderate."

    return current, "LLM confidence retained by calibration rules."


def normalize_output(item: dict, payload: dict, model: str, dry_run: bool) -> dict:
    out = {field: item.get(field, "") for field in OUTPUT_FIELDS}
    final_confidence, calibration_note = calibrate_confidence(item, payload)
    out["final_confidence_level"] = final_confidence
    gene_ambiguity = clean_str(payload.get("Gene")).upper() != clean_str(payload.get("vep_picked_gene_symbol")).upper() and bool(
        clean_str(payload.get("vep_picked_gene_symbol"))
    )
    out["interpretation_scope"] = calibrated_scope(final_confidence, payload, clean_str(out.get("interpretation_scope")))
    out["variant_observed_in_vcf"] = str(bool(item.get("variant_observed_in_vcf", True))).lower()
    out["requires_professional_review"] = str(calibrated_professional_review(final_confidence, payload, gene_ambiguity)).lower()
    out["gene_or_locus_ambiguity_flag"] = str(gene_ambiguity).lower()
    out["evidence_conflict_flag"] = str(real_clinvar_conflict(payload) or final_confidence == "Conflicting").lower()
    if calibration_note:
        suffix_en = f" Calibration note: {calibration_note}"
        suffix_es = " Nota de calibracion: las reglas HEAL separan calidad tecnica, evidencia externa y peso interpretativo final."
        out["confidence_rationale_en"] = (clean_str(out.get("confidence_rationale_en")) + suffix_en).strip()
        out["confidence_rationale_es"] = (clean_str(out.get("confidence_rationale_es")) + suffix_es).strip()
    for key in ["evidence_used", "evidence_limitations"]:
        out[key] = compact_json(item.get(key) or [])
    out["model"] = model
    out["dry_run"] = str(dry_run).lower()
    out["source_row_id"] = payload.get("row_id", "")
    return out


def process(payload: dict) -> dict:
    started_at = utc_now()
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model = clean_str(payload.get("model")) or os.environ.get("HEAL_LLM1_MODEL") or DEFAULT_MODEL
    timeout_seconds = int(payload.get("timeoutSeconds") or os.environ.get("HEAL_LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    row_attempts = int(payload.get("rowAttempts") or os.environ.get("HEAL_LLM_ROW_ATTEMPTS") or DEFAULT_ROW_ATTEMPTS)
    max_workers = max(
        1,
        min(6, int(payload.get("maxWorkers") or os.environ.get("HEAL_LLM_MAX_WORKERS") or DEFAULT_MAX_WORKERS)),
    )
    max_rows = int(payload.get("maxRows") or 0)
    dry_run = bool(payload.get("dryRun"))
    api_key = clean_str(payload.get("apiKey")) or os.environ.get("HEAL_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")
    if not dry_run and not api_key:
        raise RuntimeError("HEAL_OPENAI_API_KEY or OPENAI_API_KEY must be configured for individual interpretation.")

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    rows = read_csv(input_path)
    if max_rows > 0:
        rows = rows[:max_rows]
    payloads = [make_payload(row) for row in rows]

    payload_jsonl = output_dir / "variant_interpretation_payloads.jsonl"
    payload_csv = output_dir / "variant_interpretation_payloads.csv"
    progress_json = output_dir / "individual_variant_interpretation_progress.json"
    interpretations_jsonl = output_dir / "individual_variant_interpretations.jsonl"
    interpretations_csv = output_dir / "individual_variant_interpretations.csv"
    errors_csv = output_dir / "individual_variant_interpretation_errors.csv"
    summary_json = output_dir / "individual_variant_interpretation_summary.json"
    write_jsonl(payload_jsonl, payloads)
    payload_fieldnames = PAYLOAD_FIELDS + ["preliminary_confidence_from_input"]
    write_csv(payload_csv, [flatten_for_payload_csv(item) for item in payloads], payload_fieldnames)
    write_csv(interpretations_csv, [], OUTPUT_FIELDS + ["model", "dry_run", "source_row_id"])
    write_csv(errors_csv, [], ["row_id", "Gene", "SNP (rsID)", "error"])
    write_progress(
        progress_json,
        {
            "status": "running",
            "completedRows": 0,
            "totalRows": len(payloads),
            "interpretedRows": 0,
            "errorRows": 0,
            "currentRow": None,
            "model": model,
            "startedAt": started_at,
            "updatedAt": utc_now(),
        },
    )

    interpretation_results: dict[int, dict] = {}
    error_results: dict[int, dict] = {}

    def write_partial_outputs(completed_rows: int, current_item: dict | None = None) -> None:
        interpretations = [interpretation_results[index] for index in sorted(interpretation_results)]
        errors = [error_results[index] for index in sorted(error_results)]
        write_jsonl(interpretations_jsonl, interpretations)
        write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_row_id"])
        write_csv(errors_csv, errors, ["row_id", "Gene", "SNP (rsID)", "error"])
        write_progress(
            progress_json,
            {
                "status": "running",
                "completedRows": completed_rows,
                "totalRows": len(payloads),
                "interpretedRows": len(interpretations),
                "errorRows": len(errors),
                "currentRow": (
                    {
                        "row_id": current_item.get("row_id", ""),
                        "Gene": current_item.get("Gene", ""),
                        "SNP (rsID)": current_item.get("SNP (rsID)", ""),
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
        row_id = item.get("row_id") or str(index)
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
                    row_attempts=row_attempts,
                )
            return index, normalize_output(parsed, item, model, dry_run), None
        except Exception as error:  # noqa: BLE001 - row-level isolation is intentional.
            return index, None, (
                {
                    "row_id": row_id,
                    "Gene": item.get("Gene", ""),
                    "SNP (rsID)": item.get("SNP (rsID)", ""),
                    "error": str(error),
                }
            )

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
            future_to_item = {
                executor.submit(interpret_one, index, item): item for index, item in enumerate(payloads, start=1)
            }
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
    write_csv(interpretations_csv, interpretations, OUTPUT_FIELDS + ["model", "dry_run", "source_row_id"])
    write_csv(errors_csv, errors, ["row_id", "Gene", "SNP (rsID)", "error"])

    status = "valid" if interpretations and not errors else "warning" if interpretations else "invalid"
    summary = {
        "status": status,
        "errors": [] if interpretations else ["No rows were interpreted."],
        "warnings": [f"{len(errors)} row(s) failed individual interpretation."] if errors else [],
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            "source_rows": len(rows),
            "payload_rows": len(payloads),
            "interpreted_rows": len(interpretations),
            "error_rows": len(errors),
            "model": model,
            "dry_run": dry_run,
            "max_workers": max_workers,
            "schema": "heal_individual_variant_interpretation",
        },
        "outputs": {
            "variantInterpretationPayloadsJsonl": str(payload_jsonl),
            "variantInterpretationPayloadsCsv": str(payload_csv),
            "individualVariantInterpretationProgressJson": str(progress_json),
            "individualVariantInterpretationsJsonl": str(interpretations_jsonl),
            "individualVariantInterpretationsCsv": str(interpretations_csv),
            "individualVariantInterpretationErrorsCsv": str(errors_csv),
            "individualVariantInterpretationSummaryJson": str(summary_json),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(
        progress_json,
        {
            "status": status,
            "completedRows": len(payloads),
            "totalRows": len(payloads),
            "interpretedRows": len(interpretations),
            "errorRows": len(errors),
            "currentRow": None,
            "model": model,
            "maxWorkers": max_workers,
            "startedAt": started_at,
            "updatedAt": utc_now(),
            "completedAt": summary["timestamps"]["completedAt"],
        },
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", help="Base64-encoded JSON payload.")
    parser.add_argument("--input", help="Input Enrichment Plus CSV path.")
    parser.add_argument("--output-dir", help="Output directory.")
    parser.add_argument("--model", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-rows", type=int, default=0)
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
            if args.max_rows:
                payload["maxRows"] = args.max_rows
        result = process(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"valid", "warning"} else 1
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"status": "invalid", "errors": [str(error)], "warnings": []}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
