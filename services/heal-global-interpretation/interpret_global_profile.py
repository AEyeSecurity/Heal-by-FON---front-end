#!/usr/bin/env python3
"""Generate HEAL global synthesis from normalized individual variant interpretations."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
from pathlib import Path
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict


DEFAULT_MODEL = "gpt-5.2"
DEFAULT_TIMEOUT_SECONDS = 240
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
SCRIPT_DIR = Path(__file__).resolve().parent
PROMPT_PATH = SCRIPT_DIR / "prompt_llm2.md"
SCHEMA_PATH = SCRIPT_DIR / "global_interpretation_schema.json"
ALLOWED_LANGUAGE_MODES = {"en", "es", "both"}
ALLOWED_AUDIENCE_MODES = {"technical", "health_professional", "family", "all"}


ASCII_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "\u2248": "approximately",
    "\u00b5": "u",
    "\u03bc": "u",
    "\u03b2": "beta",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u00a0", " ").strip()


def ascii_text(value) -> str:
    text = clean(value)
    for source, replacement in ASCII_REPLACEMENTS.items():
        text = text.replace(source, replacement)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def truthy(value) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_json_list(value) -> list:
    raw = clean(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def choose_text(row: dict, base: str, language_mode: str):
    if language_mode == "both":
        return {
            "en": ascii_text(row.get(f"{base}_en")),
            "es": ascii_text(row.get(f"{base}_es")),
        }
    suffix = "es" if language_mode == "es" else "en"
    return ascii_text(row.get(f"{base}_{suffix}"))


def compact_variant(row: dict, language_mode: str) -> dict:
    return {
        "row_id": ascii_text(row.get("row_id")),
        "gene": ascii_text(row.get("gene")),
        "rsID": ascii_text(row.get("rsID")),
        "category": ascii_text(row.get("category")),
        "canon_effect": ascii_text(row.get("canon_effect")),
        "observed_genotype": ascii_text(row.get("observed_genotype")),
        "zygosity": ascii_text(row.get("zygosity")),
        "ref_alt": ascii_text(row.get("ref_alt")),
        "variant_observed_in_vcf": truthy(row.get("variant_observed_in_vcf")),
        "interpretation_one_sentence": choose_text(row, "interpretation_one_sentence", language_mode),
        "interpretation_long": choose_text(row, "interpretation_long", language_mode),
        "technical_interpretation": choose_text(row, "technical_interpretation", language_mode),
        "final_confidence_level": ascii_text(row.get("final_confidence_level")),
        "confidence_rationale": choose_text(row, "confidence_rationale", language_mode),
        "notes": choose_text(row, "technical_notes", language_mode),
        "family_notes": choose_text(row, "family_notes", language_mode),
        "recommended_next_review_step": choose_text(row, "recommended_next_review_step", language_mode),
        "requires_professional_review": truthy(row.get("requires_professional_review")),
        "gene_or_locus_ambiguity_flag": truthy(row.get("gene_or_locus_ambiguity_flag")),
        "evidence_conflict_flag": truthy(row.get("evidence_conflict_flag")),
        "evidence_used": parse_json_list(row.get("evidence_used")),
        "evidence_limitations": parse_json_list(row.get("evidence_limitations")),
    }


def unique_sorted(values) -> list[str]:
    return sorted({ascii_text(value) for value in values if ascii_text(value)})


def confidence_distribution(rows: list[dict]) -> dict:
    counts = Counter(ascii_text(row.get("final_confidence_level")) for row in rows)
    return {label: int(counts.get(label, 0)) for label in ["High", "Moderate", "Low", "Conflicting"]}


def group_confidence(rows: list[dict]) -> dict:
    counts = Counter(ascii_text(row.get("final_confidence_level")) for row in rows)
    return {label: int(counts.get(label, 0)) for label in ["High", "Moderate", "Low", "Conflicting"] if counts.get(label)}


def build_deterministic_summary(rows: list[dict]) -> dict:
    rsid_groups = defaultdict(list)
    gene_groups = defaultdict(list)
    category_groups = defaultdict(list)
    for row in rows:
        rsid = ascii_text(row.get("rsID")).lower()
        gene = ascii_text(row.get("gene")).upper()
        category = ascii_text(row.get("category"))
        if rsid:
            rsid_groups[rsid].append(row)
        if gene:
            gene_groups[gene].append(row)
        if category:
            category_groups[category].append(row)

    repeated_rsids = []
    for rsid, group in sorted(rsid_groups.items()):
        if len(group) <= 1:
            continue
        genotypes = unique_sorted(row.get("observed_genotype") for row in group)
        repeated_rsids.append(
            {
                "rsID": group[0].get("rsID", rsid),
                "gene_values": unique_sorted(row.get("gene") for row in group),
                "categories": unique_sorted(row.get("category") for row in group),
                "row_ids": unique_sorted(row.get("row_id") for row in group),
                "same_genotype": len(genotypes) <= 1,
                "confidence_distribution": group_confidence(group),
                "note": "Same rsID appears in multiple curated contexts; do not count as independent variants.",
            }
        )

    multiple_variants_same_gene = []
    for gene, group in sorted(gene_groups.items()):
        rsids = unique_sorted(row.get("rsID") for row in group)
        if len(rsids) <= 1:
            continue
        multiple_variants_same_gene.append(
            {
                "gene": gene,
                "rsids": rsids,
                "row_ids": unique_sorted(row.get("row_id") for row in group),
                "categories": unique_sorted(row.get("category") for row in group),
                "confidence_distribution": group_confidence(group),
                "note": "Multiple distinct rsIDs observed in the same gene.",
            }
        )

    genes_by_axis = []
    for category, group in sorted(category_groups.items()):
        genes_by_axis.append(
            {
                "axis_name": category,
                "genes": unique_sorted(row.get("gene") for row in group),
                "rsids": unique_sorted(row.get("rsID") for row in group),
                "row_ids": unique_sorted(row.get("row_id") for row in group),
                "confidence_distribution": group_confidence(group),
            }
        )

    conflicting_variants = [
        {
            "gene": ascii_text(row.get("gene")),
            "rsID": ascii_text(row.get("rsID")),
            "row_id": ascii_text(row.get("row_id")),
            "reason": ascii_text(row.get("confidence_rationale_en") or row.get("technical_notes_en")),
        }
        for row in rows
        if ascii_text(row.get("final_confidence_level")) == "Conflicting" or truthy(row.get("evidence_conflict_flag"))
    ]

    professional_review_variants = [
        {
            "gene": ascii_text(row.get("gene")),
            "rsID": ascii_text(row.get("rsID")),
            "row_id": ascii_text(row.get("row_id")),
            "reason": ascii_text(row.get("recommended_next_review_step_en") or row.get("technical_notes_en")),
        }
        for row in rows
        if truthy(row.get("requires_professional_review"))
    ]

    gene_locus_ambiguities = [
        {
            "gene": ascii_text(row.get("gene")),
            "rsID": ascii_text(row.get("rsID")),
            "row_id": ascii_text(row.get("row_id")),
            "notes": ascii_text(row.get("technical_notes_en")),
        }
        for row in rows
        if truthy(row.get("gene_or_locus_ambiguity_flag"))
    ]

    return {
        "variant_count_observed": len(rows),
        "unique_rsid_count": len({ascii_text(row.get("rsID")).lower() for row in rows if ascii_text(row.get("rsID"))}),
        "unique_gene_count": len({ascii_text(row.get("gene")).upper() for row in rows if ascii_text(row.get("gene"))}),
        "confidence_distribution": confidence_distribution(rows),
        "repeated_rsids": repeated_rsids,
        "multiple_variants_same_gene": multiple_variants_same_gene,
        "genes_by_axis": genes_by_axis,
        "conflicting_variants": conflicting_variants,
        "professional_review_variants": professional_review_variants,
        "gene_locus_ambiguities": gene_locus_ambiguities,
    }


def project_context() -> dict:
    return {
        "vcf_type_note": (
            "The VCF appears to report observed variants rather than all genomic positions. "
            "Non-observed loci should not be interpreted as confirmed reference genotypes."
        ),
        "scope_note": "This is a non-diagnostic interpretation report for informational and review purposes.",
        "observed_variants_only": True,
        "non_observed_variants_policy": (
            "Non-observed variants are handled outside this LLM as no variant observed in current VCF."
        ),
    }


def output_text_from_response(response: dict) -> str:
    texts = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(content["text"])
    if texts:
        return "\n".join(texts)
    if response.get("output_text"):
        return clean(response.get("output_text"))
    raise ValueError("OpenAI response did not contain output text.")


def translation_prompt(target_language: str) -> str:
    return f"""You are a strict medical-report translation assistant.

Translate the provided HEAL report JSON into {target_language}.

Critical requirements:
- Preserve the exact JSON structure and all keys required by the schema.
- Preserve the number, order, and grouping of all biological axes.
- Preserve the number, order, and grouping of notable gene patterns, findings for review, conflicting findings, summaries, limitations, and next steps.
- Do not add, remove, merge, split, reprioritize, or reinterpret sections.
- Do not change confidence labels, booleans, counts, gene symbols, rsIDs, review priorities, or readiness values.
- Translate human-readable prose only.
- Keep gene symbols and rsIDs exactly unchanged.
- Keep technical labels such as High, Moderate, Low, Conflicting, high, medium, low, ready, ready_with_minor_review, and needs_review exactly unchanged.
- Keep the same clinical caution level and the same contextual review strength.
- The translated report must read naturally, but it must remain a translation of the source report, not a new analysis.
"""


def call_openai_structured(
    payload: dict,
    *,
    api_key: str,
    model: str,
    timeout_seconds: int,
    system_prompt: str | None = None,
    user_instruction: str | None = None,
) -> dict:
    system_prompt = system_prompt if system_prompt is not None else PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    (user_instruction or "Create the HEAL global synthesis from this payload. ")
                    + "Return JSON matching the schema only.\n\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "heal_global_interpretation",
                "strict": True,
                "schema": schema,
            }
        },
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1600]
        raise RuntimeError(f"OpenAI API http_{error.code}: {detail}") from error
    return json.loads(output_text_from_response(parsed))


def translate_report(
    report: dict,
    *,
    target_language_mode: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
) -> dict:
    translated = call_openai_structured(
        {
            "target_language_mode": target_language_mode,
            "source_report": report,
        },
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        system_prompt=translation_prompt("English" if target_language_mode == "en" else "Spanish"),
        user_instruction="Translate this HEAL report JSON while preserving its exact structure. ",
    )
    translated["metadata"]["language_mode"] = target_language_mode
    return translated


def flatten_global_json(report: dict) -> list[dict]:
    rows = []
    global_report = report.get("global_report") or {}
    for key, value in global_report.items():
        rows.append({"section": "global_report", "item": key, "value": ascii_text(value)})
    for item in report.get("biological_axes") or []:
        rows.append(
            {
                "section": "biological_axes",
                "item": ascii_text(item.get("axis_name")),
                "value": ascii_text(item.get("summary")),
                "confidence": ascii_text(item.get("confidence_of_axis")),
                "genes": "|".join(item.get("supporting_genes") or []),
                "rsids": "|".join(item.get("supporting_rsids") or []),
            }
        )
    for item in report.get("notable_gene_patterns") or []:
        rows.append(
            {
                "section": "notable_gene_patterns",
                "item": ascii_text(item.get("gene_or_locus")),
                "value": ascii_text(item.get("summary")),
                "confidence": ascii_text(item.get("confidence")),
                "rsids": "|".join(item.get("rsids") or []),
            }
        )
    for item in report.get("top_findings_for_review") or []:
        rows.append(
            {
                "section": "top_findings_for_review",
                "item": f"{ascii_text(item.get('gene'))} {ascii_text(item.get('rsID'))}".strip(),
                "value": ascii_text(item.get("reason_for_review")),
                "confidence": ascii_text(item.get("confidence_level")),
                "priority": ascii_text(item.get("review_priority")),
            }
        )
    for item in report.get("conflicting_or_sensitive_findings") or []:
        rows.append(
            {
                "section": "conflicting_or_sensitive_findings",
                "item": f"{ascii_text(item.get('gene'))} {ascii_text(item.get('rsID'))}".strip(),
                "value": ascii_text(item.get("why_sensitive_or_conflicting")),
            }
        )
    return rows


def sanitize_report(value):
    if isinstance(value, dict):
        return {key: sanitize_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_report(item) for item in value]
    if isinstance(value, str):
        return ascii_text(value)
    return value


def process(payload: dict) -> dict:
    started_at = utc_now()
    input_path = Path(payload["inputPath"]).resolve()
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    language_mode = clean(payload.get("languageMode") or "es").lower()
    audience_mode = clean(payload.get("audienceMode") or "all").lower()
    if language_mode not in ALLOWED_LANGUAGE_MODES:
        language_mode = "es"
    if audience_mode not in ALLOWED_AUDIENCE_MODES:
        audience_mode = "all"
    model = clean(payload.get("model")) or os.environ.get("HEAL_LLM2_MODEL") or DEFAULT_MODEL
    timeout_seconds = int(payload.get("timeoutSeconds") or os.environ.get("HEAL_LLM2_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    dry_run = bool(payload.get("dryRun"))
    api_key = clean(payload.get("apiKey")) or os.environ.get("HEAL_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")
    if not dry_run and not api_key:
        raise RuntimeError("HEAL_OPENAI_API_KEY or OPENAI_API_KEY must be configured for global interpretation.")

    rows = read_csv(input_path)
    base_language_mode = "es" if language_mode == "en" else language_mode
    variant_interpretations = [compact_variant(row, base_language_mode) for row in rows]
    deterministic_summary = build_deterministic_summary(rows)
    llm_payload = {
        "language_mode": base_language_mode,
        "audience_mode": audience_mode,
        "project_context": project_context(),
        "deterministic_summary": deterministic_summary,
        "variant_interpretations": variant_interpretations,
    }

    payload_json = output_dir / "global_interpretation_payload.json"
    deterministic_json = output_dir / "deterministic_summary.json"
    report_json = output_dir / "global_interpretation.json"
    source_es_report_json = output_dir / "global_interpretation_es_source.json"
    sections_csv = output_dir / "global_interpretation_sections.csv"
    summary_json = output_dir / "global_interpretation_summary.json"
    write_json(payload_json, llm_payload)
    write_json(deterministic_json, deterministic_summary)

    if dry_run:
        report = {
            "metadata": {
                "language_mode": language_mode,
                "audience_mode": audience_mode,
                "variant_count_observed": deterministic_summary["variant_count_observed"],
                "unique_rsid_count": deterministic_summary["unique_rsid_count"],
                "unique_gene_count": deterministic_summary["unique_gene_count"],
                "confidence_distribution": deterministic_summary["confidence_distribution"],
            },
            "global_report": {
                "report_title": "HEAL global interpretation dry run",
                "executive_summary": "Dry run placeholder for global interpretation.",
                "main_interpretation": "Dry run did not call the LLM.",
                "important_caution": "This is not a diagnostic report.",
                "limitations": project_context()["vcf_type_note"],
                "next_review_steps": "Run without dryRun to generate the full synthesis.",
            },
            "biological_axes": [],
            "notable_gene_patterns": [],
            "top_findings_for_review": [],
            "conflicting_or_sensitive_findings": [],
            "low_confidence_findings_summary": {"summary": "", "how_to_use": ""},
            "family_friendly_summary": {"short_summary": "", "what_this_does_not_mean": "", "what_may_be_worth_reviewing": ""},
            "technical_summary": {
                "methodological_summary": "Dry run created deterministic summary only.",
                "data_quality_comment": "",
                "interpretation_constraints": "",
            },
            "final_recommendation": {
                "ready_for_report_generation": False,
                "what_should_be_reviewed_before_release": ["Run full LLM2 synthesis."],
                "overall_readiness": "needs_review",
            },
        }
    else:
        report = call_openai_structured(llm_payload, api_key=api_key, model=model, timeout_seconds=timeout_seconds)
        report = sanitize_report(report)
        if language_mode == "en":
            write_json(source_es_report_json, report)
            report = translate_report(
                report,
                target_language_mode="en",
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            report = sanitize_report(report)

    write_json(report_json, report)
    section_rows = flatten_global_json(report)
    write_csv(
        sections_csv,
        section_rows,
        ["section", "item", "value", "confidence", "priority", "genes", "rsids"],
    )

    metadata = {
        "source_rows": len(rows),
        "variant_count_observed": deterministic_summary["variant_count_observed"],
        "unique_rsid_count": deterministic_summary["unique_rsid_count"],
        "unique_gene_count": deterministic_summary["unique_gene_count"],
        "confidence_distribution": deterministic_summary["confidence_distribution"],
        "repeated_rsid_count": len(deterministic_summary["repeated_rsids"]),
        "multiple_variant_gene_count": len(deterministic_summary["multiple_variants_same_gene"]),
        "conflicting_variant_count": len(deterministic_summary["conflicting_variants"]),
        "professional_review_variant_count": len(deterministic_summary["professional_review_variants"]),
        "gene_locus_ambiguity_count": len(deterministic_summary["gene_locus_ambiguities"]),
        "model": model,
        "dry_run": dry_run,
        "language_mode": language_mode,
        "audience_mode": audience_mode,
        "overall_readiness": report.get("final_recommendation", {}).get("overall_readiness", ""),
    }
    summary = {
        "status": "valid",
        "errors": [],
        "warnings": [],
        "metadata": metadata,
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "outputs": {
            "globalInterpretationPayloadJson": str(payload_json),
            "deterministicSummaryJson": str(deterministic_json),
            "globalInterpretationJson": str(report_json),
            "globalInterpretationSectionsCsv": str(sections_csv),
            "globalInterpretationSummaryJson": str(summary_json),
        },
    }
    write_json(summary_json, summary)
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        print(json.dumps(process(payload), ensure_ascii=False))
        return 0
    except Exception as error:
        result = {
            "status": "invalid",
            "errors": [str(error)],
            "warnings": [],
            "metadata": {},
            "timestamps": {"completedAt": utc_now()},
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
