#!/usr/bin/env python3
"""Prepare and run HEAL individual observed-variant interpretations."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_TIMEOUT_SECONDS = 90
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


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def truthy(value) -> bool:
    return clean_str(value).lower() in {"1", "true", "yes", "y"}


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


def normalize_output(item: dict, payload: dict, model: str, dry_run: bool) -> dict:
    out = {field: item.get(field, "") for field in OUTPUT_FIELDS}
    out["variant_observed_in_vcf"] = str(bool(item.get("variant_observed_in_vcf", True))).lower()
    for key in ["requires_professional_review", "gene_or_locus_ambiguity_flag", "evidence_conflict_flag"]:
        out[key] = str(bool(item.get(key, False))).lower()
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
    write_jsonl(payload_jsonl, payloads)
    payload_fieldnames = PAYLOAD_FIELDS + ["preliminary_confidence_from_input"]
    write_csv(payload_csv, [flatten_for_payload_csv(item) for item in payloads], payload_fieldnames)

    interpretations = []
    errors = []
    for index, item in enumerate(payloads, start=1):
        row_id = item.get("row_id") or str(index)
        try:
            if dry_run:
                parsed = dry_run_interpretation(item)
            else:
                parsed = call_openai_structured(
                    item,
                    api_key=api_key,
                    model=model,
                    system_prompt=system_prompt,
                    schema=schema,
                    timeout_seconds=timeout_seconds,
                )
            interpretations.append(normalize_output(parsed, item, model, dry_run))
        except Exception as error:  # noqa: BLE001 - row-level isolation is intentional.
            errors.append(
                {
                    "row_id": row_id,
                    "Gene": item.get("Gene", ""),
                    "SNP (rsID)": item.get("SNP (rsID)", ""),
                    "error": str(error),
                }
            )
        if not dry_run:
            time.sleep(float(payload.get("delaySeconds") or os.environ.get("HEAL_LLM_DELAY_SECONDS") or 0.2))

    interpretations_jsonl = output_dir / "individual_variant_interpretations.jsonl"
    interpretations_csv = output_dir / "individual_variant_interpretations.csv"
    errors_csv = output_dir / "individual_variant_interpretation_errors.csv"
    summary_json = output_dir / "individual_variant_interpretation_summary.json"
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
            "schema": "heal_individual_variant_interpretation",
        },
        "outputs": {
            "variantInterpretationPayloadsJsonl": str(payload_jsonl),
            "variantInterpretationPayloadsCsv": str(payload_csv),
            "individualVariantInterpretationsJsonl": str(interpretations_jsonl),
            "individualVariantInterpretationsCsv": str(interpretations_csv),
            "individualVariantInterpretationErrorsCsv": str(errors_csv),
            "individualVariantInterpretationSummaryJson": str(summary_json),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
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
