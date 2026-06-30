#!/usr/bin/env python3
"""Generate HEAL global synthesis from normalized individual variant interpretations."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
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
AXIS_ONTOLOGY_PATH = SCRIPT_DIR / "axis_ontology.json"
ALLOWED_LANGUAGE_MODES = {"en", "es", "both"}
ALLOWED_AUDIENCE_MODES = {"technical", "health_professional", "family", "all"}
PIPELINE_VERSION = "0.2.0"
AXIS_ONTOLOGY_VERSION = "0.1.0"
LLM2_PROMPT_VERSION = "0.3.0"
TRANSLATION_PROMPT_VERSION = "0.1.0"
GLOBAL_INTERPRETATION_SCHEMA_VERSION = "0.2.0"
CANONICAL_FRAME_VERSION = "0.1.0"
DETERMINISTIC_VALIDATOR_VERSION = "0.1.0"
STRUCTURED_REPORT_VERSION = "0.1.0"


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


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value) -> str:
    return sha256_text(canonical_json(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_hash_or_empty(path: Path) -> str:
    return sha256_file(path) if path.exists() else ""


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


def dominant_confidence(rows: list[dict]) -> str:
    counts = group_confidence(rows)
    if counts.get("Conflicting"):
        return "Conflicting"
    if counts.get("High", 0) >= 2 or (counts.get("High", 0) >= 1 and counts.get("Moderate", 0) >= 2):
        return "High"
    if counts.get("High") or counts.get("Moderate", 0) >= 2:
        return "Moderate"
    return "Low"


def axis_strength(rows: list[dict]) -> str:
    unique_rsids = len({ascii_text(row.get("rsID")).lower() for row in rows if ascii_text(row.get("rsID"))})
    unique_genes = len({ascii_text(row.get("gene")).upper() for row in rows if ascii_text(row.get("gene"))})
    counts = group_confidence(rows)
    if counts.get("Conflicting"):
        return "review_with_caution"
    if unique_rsids >= 5 and unique_genes >= 4 and counts.get("Low", 0) < len(rows):
        return "strong"
    if unique_rsids >= 2 and unique_genes >= 2:
        return "moderate"
    return "limited"


def evidence_mix(rows: list[dict]) -> dict:
    buckets = {
        "functional_or_coding": 0,
        "regulatory_or_intronic": 0,
        "pharmacogenomic": 0,
        "gwas_or_associative": 0,
        "clinvar_or_clinical_database": 0,
        "technical_review_flags": 0,
    }
    for row in rows:
        evidence_text = " ".join(
            [
                ascii_text(row.get("technical_interpretation_en")),
                ascii_text(row.get("technical_interpretation_es")),
                ascii_text(row.get("confidence_rationale_en")),
                ascii_text(row.get("confidence_rationale_es")),
                ascii_text(row.get("technical_notes_en")),
                ascii_text(row.get("technical_notes_es")),
                ascii_text(row.get("recommended_next_review_step_en")),
                ascii_text(row.get("recommended_next_review_step_es")),
                ascii_text(row.get("evidence_used")),
            ]
        ).lower()
        if any(term in evidence_text for term in ["missense", "coding", "protein", "synonymous", "hgvsp"]):
            buckets["functional_or_coding"] += 1
        if any(term in evidence_text for term in ["intron", "upstream", "downstream", "regulatory", "modifier"]):
            buckets["regulatory_or_intronic"] += 1
        if any(term in evidence_text for term in ["pharmgkb", "pharmacogen", "drug", "medication", "pgx"]):
            buckets["pharmacogenomic"] += 1
        if any(term in evidence_text for term in ["gwas", "association", "associative", "risk factor"]):
            buckets["gwas_or_associative"] += 1
        if any(term in evidence_text for term in ["clinvar", "clinical", "benign", "pathogenic", "conflicting"]):
            buckets["clinvar_or_clinical_database"] += 1
        if truthy(row.get("requires_professional_review")) or truthy(row.get("gene_or_locus_ambiguity_flag")):
            buckets["technical_review_flags"] += 1
    return buckets


def normalize_match_text(value) -> str:
    normalized = ascii_text(value).lower()
    return " ".join(normalized.replace("_", " ").replace("/", " ").replace("&", " ").split())


def load_axis_ontology() -> dict:
    ontology = read_json(AXIS_ONTOLOGY_PATH)
    axes = ontology.get("axes") or []
    if not isinstance(axes, list) or not axes:
        raise ValueError("Axis ontology must include a non-empty axes list.")
    axis_ids = [axis.get("axis_id") for axis in axes]
    unknown_axis_id = ontology.get("unknown_axis_id")
    if unknown_axis_id not in axis_ids:
        raise ValueError("Axis ontology unknown_axis_id must match one configured axis_id.")
    if ontology.get("version") != AXIS_ONTOLOGY_VERSION:
        raise ValueError(
            f"Axis ontology version mismatch: file={ontology.get('version')} code={AXIS_ONTOLOGY_VERSION}"
        )
    return ontology


def resolve_axis_for_category(category: str, ontology: dict) -> dict:
    normalized_category = normalize_match_text(category)
    axes = ontology.get("axes") or []
    unknown_axis_id = ontology.get("unknown_axis_id")
    unknown_axis = next(axis for axis in axes if axis.get("axis_id") == unknown_axis_id)
    for axis in axes:
        if axis.get("axis_id") == unknown_axis_id:
            continue
        for pattern in axis.get("allowed_category_patterns") or []:
            if normalize_match_text(pattern) in normalized_category:
                return axis
    return unknown_axis


def compact_axis(axis: dict) -> dict:
    return {
        "axis_id": ascii_text(axis.get("axis_id")),
        "display_name": ascii_text(axis.get("display_name")),
        "description": ascii_text(axis.get("description")),
        "review_domain": ascii_text(axis.get("review_domain")),
    }


def build_deterministic_summary(rows: list[dict], ontology: dict) -> dict:
    rsid_groups = defaultdict(list)
    gene_groups = defaultdict(list)
    category_groups = defaultdict(list)
    axis_groups = defaultdict(list)
    axis_lookup = {axis.get("axis_id"): axis for axis in ontology.get("axes") or []}
    category_axis_map = {}
    for row in rows:
        rsid = ascii_text(row.get("rsID")).lower()
        gene = ascii_text(row.get("gene")).upper()
        category = ascii_text(row.get("category"))
        axis = resolve_axis_for_category(category, ontology) if category else axis_lookup[ontology["unknown_axis_id"]]
        axis_id = axis["axis_id"]
        row["_heal_axis_id"] = axis_id
        row["_heal_axis_name"] = axis["display_name"]
        if rsid:
            rsid_groups[rsid].append(row)
        if gene:
            gene_groups[gene].append(row)
        if category:
            category_groups[category].append(row)
            category_axis_map[category] = {
                "category": category,
                "axis_id": ascii_text(axis_id),
                "axis_name": ascii_text(axis["display_name"]),
            }
        axis_groups[axis_id].append(row)

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
    for axis_id, group in sorted(axis_groups.items()):
        axis = axis_lookup.get(axis_id) or axis_lookup[ontology["unknown_axis_id"]]
        genes_by_axis.append(
            {
                "axis_id": ascii_text(axis_id),
                "axis_name": ascii_text(axis.get("display_name")),
                "axis_description": ascii_text(axis.get("description")),
                "review_domain": ascii_text(axis.get("review_domain")),
                "source_categories": unique_sorted(row.get("category") for row in group),
                "genes": unique_sorted(row.get("gene") for row in group),
                "rsids": unique_sorted(row.get("rsID") for row in group),
                "row_ids": unique_sorted(row.get("row_id") for row in group),
                "confidence_distribution": group_confidence(group),
                "axis_strength": axis_strength(group),
                "suggested_axis_confidence": dominant_confidence(group),
                "evidence_mix": evidence_mix(group),
            }
        )

    category_groups_by_original_label = []
    for category, group in sorted(category_groups.items()):
        resolved = category_axis_map.get(category) or {}
        category_groups_by_original_label.append(
            {
                "category": category,
                "axis_id": resolved.get("axis_id", ""),
                "axis_name": resolved.get("axis_name", ""),
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
        "axis_ontology": {
            "version": ascii_text(ontology.get("version")),
            "unknown_axis_id": ascii_text(ontology.get("unknown_axis_id")),
            "axes": [compact_axis(axis) for axis in ontology.get("axes") or []],
            "category_axis_map": list(category_axis_map.values()),
        },
        "repeated_rsids": repeated_rsids,
        "multiple_variants_same_gene": multiple_variants_same_gene,
        "genes_by_axis": genes_by_axis,
        "category_groups": category_groups_by_original_label,
        "conflicting_variants": conflicting_variants,
        "professional_review_variants": professional_review_variants,
        "gene_locus_ambiguities": gene_locus_ambiguities,
    }


def confidence_rank(label: str) -> int:
    return {"High": 4, "Moderate": 3, "Conflicting": 2, "Low": 1}.get(ascii_text(label), 0)


def review_priority_for_row(row: dict) -> str:
    confidence = ascii_text(row.get("final_confidence_level"))
    if confidence == "Conflicting" or truthy(row.get("evidence_conflict_flag")):
        return "high"
    if truthy(row.get("requires_professional_review")) or truthy(row.get("gene_or_locus_ambiguity_flag")):
        return "medium"
    return "low"


def build_canonical_analysis_frame(rows: list[dict], deterministic_summary: dict) -> dict:
    axes = []
    for axis in deterministic_summary.get("genes_by_axis") or []:
        axes.append(
            {
                "axis_id": axis.get("axis_id", ""),
                "axis_name": axis.get("axis_name", ""),
                "axis_description": axis.get("axis_description", ""),
                "review_domain": axis.get("review_domain", ""),
                "source_categories": axis.get("source_categories", []),
                "supporting_genes": axis.get("genes", []),
                "supporting_rsids": axis.get("rsids", []),
                "row_ids": axis.get("row_ids", []),
                "confidence_distribution": axis.get("confidence_distribution", {}),
                "suggested_axis_confidence": axis.get("suggested_axis_confidence", "Low"),
                "axis_strength": axis.get("axis_strength", "limited"),
                "evidence_mix": axis.get("evidence_mix", {}),
                "interpretation_rule": (
                    "Interpret the axis only as a non-diagnostic contextual pattern. "
                    "Explain possible review domains when symptoms, family history, medications, or labs make them relevant."
                ),
            }
        )
    top_findings = []
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            review_priority_for_row(row) == "high",
            review_priority_for_row(row) == "medium",
            confidence_rank(row.get("final_confidence_level")),
            ascii_text(row.get("gene")),
            ascii_text(row.get("rsID")),
        ),
        reverse=True,
    )
    for row in sorted_rows[:20]:
        priority = review_priority_for_row(row)
        if priority == "low" and ascii_text(row.get("final_confidence_level")) == "Low":
            continue
        top_findings.append(
            {
                "gene": ascii_text(row.get("gene")),
                "rsID": ascii_text(row.get("rsID")),
                "row_id": ascii_text(row.get("row_id")),
                "confidence_level": ascii_text(row.get("final_confidence_level")),
                "review_priority": priority,
                "reason": ascii_text(row.get("recommended_next_review_step_en") or row.get("confidence_rationale_en") or row.get("technical_notes_en")),
            }
        )
    readiness_blockers = []
    if deterministic_summary.get("conflicting_variants"):
        readiness_blockers.append("conflicting_variant_evidence_present")
    unknown_axis = [
        axis for axis in axes
        if axis.get("axis_id") == (deterministic_summary.get("axis_ontology") or {}).get("unknown_axis_id")
    ]
    if unknown_axis and any(axis.get("row_ids") for axis in unknown_axis):
        readiness_blockers.append("unmapped_canon_categories_present")
    if deterministic_summary.get("gene_locus_ambiguities"):
        readiness_blockers.append("gene_or_locus_ambiguities_present")
    informational_readiness = "ready" if not unknown_axis else "ready_with_minor_review"
    clinical_readiness = "needs_professional_review" if readiness_blockers else "contextual_review_only"
    return {
        "version": CANONICAL_FRAME_VERSION,
        "purpose": "Deterministic frame that constrains LLM2 synthesis and final report rendering.",
        "allowed_axes": axes,
        "top_findings_for_review_seed": top_findings,
        "readiness": {
            "informational_report_readiness": informational_readiness,
            "clinical_use_readiness": clinical_readiness,
            "blockers_or_review_flags": readiness_blockers,
        },
        "report_order": [
            "overview",
            "primary_biological_axes",
            "notable_gene_patterns",
            "findings_for_review",
            "limitations",
            "next_steps",
            "technical_audit",
        ],
        "non_diagnostic_constraints": [
            "Do not diagnose disease or predict disease deterministically.",
            "Do not recommend treatment, medication, supplements, or lifestyle changes.",
            "Do not infer symptoms from genotype alone.",
            "Do provide contextual review implications when compatible symptoms, family history, labs, or medication context exist.",
        ],
    }


def default_contextual_guidance(axis: dict) -> dict:
    review_domain = ascii_text(axis.get("review_domain") or axis.get("axis_name"))
    return {
        "possible_review_domain": review_domain,
        "when_it_may_be_relevant": (
            "If compatible symptoms, family history, medication questions, or laboratory findings are present, "
            "this axis may help decide what to review with a qualified professional."
        ),
        "who_could_review_it": (
            "A qualified clinician or relevant specialist, selected according to the symptoms, medications, or labs under review."
        ),
        "what_it_should_clarify": (
            "Whether the clinical context supports formal evaluation, technical confirmation, or more focused evidence review."
        ),
        "what_not_to_infer": (
            "Do not infer a diagnosis, treatment need, medication response, or symptoms from these variants alone."
        ),
    }


def validate_and_repair_report(report: dict, frame: dict, deterministic_summary: dict, language_mode: str) -> tuple[dict, list[str]]:
    warnings = []
    report.setdefault("metadata", {})
    report["metadata"].update(
        {
            "language_mode": language_mode,
            "variant_count_observed": deterministic_summary["variant_count_observed"],
            "unique_rsid_count": deterministic_summary["unique_rsid_count"],
            "unique_gene_count": deterministic_summary["unique_gene_count"],
            "confidence_distribution": deterministic_summary["confidence_distribution"],
        }
    )
    allowed_by_id = {axis["axis_id"]: axis for axis in frame.get("allowed_axes") or []}
    allowed_by_name = {ascii_text(axis["axis_name"]).lower(): axis for axis in frame.get("allowed_axes") or []}
    repaired_by_axis_id = {}
    for item in report.get("biological_axes") or []:
        item_axis_id = ascii_text(item.get("axis_id"))
        frame_axis = allowed_by_id.get(item_axis_id)
        if not frame_axis:
            frame_axis = allowed_by_name.get(ascii_text(item.get("axis_name")).lower())
        if not frame_axis:
            warnings.append(f"Dropped biological axis outside canonical frame: {ascii_text(item.get('axis_name'))}")
            continue
        axis_id = frame_axis["axis_id"]
        if axis_id in repaired_by_axis_id:
            warnings.append(f"Merged duplicate biological axis from LLM output: {axis_id}")
            continue
        item["axis_id"] = axis_id
        item["axis_name"] = frame_axis["axis_name"]
        item["source_categories"] = frame_axis.get("source_categories", [])
        item["supporting_genes"] = frame_axis.get("supporting_genes", [])
        item["supporting_rsids"] = frame_axis.get("supporting_rsids", [])
        if item.get("confidence_of_axis") not in {"High", "Moderate", "Low", "Conflicting"}:
            item["confidence_of_axis"] = frame_axis.get("suggested_axis_confidence") or "Low"
        if not isinstance(item.get("contextual_review_guidance"), dict):
            item["contextual_review_guidance"] = default_contextual_guidance(frame_axis)
        else:
            defaults = default_contextual_guidance(frame_axis)
            for key, value in defaults.items():
                if not ascii_text(item["contextual_review_guidance"].get(key)):
                    item["contextual_review_guidance"][key] = value
        repaired_by_axis_id[axis_id] = item
    repaired_axes = [
        repaired_by_axis_id[frame_axis["axis_id"]]
        for frame_axis in frame.get("allowed_axes") or []
        if frame_axis["axis_id"] in repaired_by_axis_id
    ]
    if not repaired_axes:
        warnings.append("LLM returned no valid canonical biological axes; deterministic axis placeholders were inserted.")
        for frame_axis in frame.get("allowed_axes") or []:
            if not frame_axis.get("supporting_rsids"):
                continue
            repaired_axes.append(
                {
                    "axis_id": frame_axis["axis_id"],
                    "axis_name": frame_axis["axis_name"],
                    "source_categories": frame_axis.get("source_categories", []),
                    "summary": "This axis has observed variants and should be interpreted from the individual variant table.",
                    "supporting_genes": frame_axis.get("supporting_genes", []),
                    "supporting_rsids": frame_axis.get("supporting_rsids", []),
                    "confidence_of_axis": frame_axis.get("suggested_axis_confidence") or "Low",
                    "why_it_matters": "It groups related observed variants into a deterministic biological review domain.",
                    "cautions": "This deterministic placeholder is non-diagnostic and should be reviewed before release.",
                    "contextual_review_guidance": default_contextual_guidance(frame_axis),
                }
            )
    report["biological_axes"] = repaired_axes
    final_recommendation = report.setdefault("final_recommendation", {})
    if frame.get("readiness", {}).get("blockers_or_review_flags"):
        final_recommendation["ready_for_report_generation"] = bool(final_recommendation.get("ready_for_report_generation", True))
        final_recommendation["overall_readiness"] = final_recommendation.get("overall_readiness") or "ready_with_minor_review"
    return report, warnings


def build_structured_report(report: dict, frame: dict) -> dict:
    global_report = report.get("global_report") or {}
    language = (report.get("metadata") or {}).get("language_mode", "es")
    titles = {
        "es": {
            "overview": "Panorama general del caso",
            "primary_biological_axes": "Ejes biologicos principales",
            "notable_gene_patterns": "Patrones geneticos destacables",
            "findings_for_review": "Hallazgos para revision",
            "limitations": "Limitaciones",
            "next_steps": "Proximos pasos de revision",
            "technical_audit": "Auditoria tecnica",
        },
        "en": {
            "overview": "Case overview",
            "primary_biological_axes": "Primary biological axes",
            "notable_gene_patterns": "Notable genetic patterns",
            "findings_for_review": "Findings for review",
            "limitations": "Limitations",
            "next_steps": "Next review steps",
            "technical_audit": "Technical audit",
        },
    }
    section_titles = titles.get(language, titles["es"])
    sections = [
        {
            "section_id": "overview",
            "title": section_titles["overview"],
            "blocks": [
                {"type": "paragraph", "text": ascii_text(global_report.get("executive_summary"))},
                {"type": "paragraph", "text": ascii_text(global_report.get("main_interpretation"))},
            ],
        },
        {
            "section_id": "primary_biological_axes",
            "title": section_titles["primary_biological_axes"],
            "blocks": [
                {
                    "type": "axis",
                    "axis_id": ascii_text(axis.get("axis_id")),
                    "title": ascii_text(axis.get("axis_name")),
                    "confidence": ascii_text(axis.get("confidence_of_axis")),
                    "interpretation": ascii_text(axis.get("summary")),
                    "why_it_matters": ascii_text(axis.get("why_it_matters")),
                    "contextual_review_guidance": axis.get("contextual_review_guidance") or {},
                    "cautions": ascii_text(axis.get("cautions")),
                    "supporting_genes": axis.get("supporting_genes") or [],
                    "supporting_rsids": axis.get("supporting_rsids") or [],
                    "source_categories": axis.get("source_categories") or [],
                }
                for axis in report.get("biological_axes") or []
            ],
        },
        {
            "section_id": "notable_gene_patterns",
            "title": section_titles["notable_gene_patterns"],
            "blocks": report.get("notable_gene_patterns") or [],
        },
        {
            "section_id": "findings_for_review",
            "title": section_titles["findings_for_review"],
            "blocks": report.get("top_findings_for_review") or [],
        },
        {
            "section_id": "limitations",
            "title": section_titles["limitations"],
            "blocks": [
                {"type": "paragraph", "text": ascii_text(global_report.get("important_caution"))},
                {"type": "paragraph", "text": ascii_text(global_report.get("limitations"))},
                report.get("low_confidence_findings_summary") or {},
            ],
        },
        {
            "section_id": "next_steps",
            "title": section_titles["next_steps"],
            "blocks": [
                {"type": "paragraph", "text": ascii_text(global_report.get("next_review_steps"))},
                report.get("final_recommendation") or {},
            ],
        },
        {
            "section_id": "technical_audit",
            "title": section_titles["technical_audit"],
            "blocks": [
                report.get("technical_summary") or {},
                {
                    "canonical_frame_version": frame.get("version"),
                    "allowed_axis_count": len(frame.get("allowed_axes") or []),
                    "readiness": frame.get("readiness") or {},
                },
            ],
        },
    ]
    return {
        "version": STRUCTURED_REPORT_VERSION,
        "language_mode": (report.get("metadata") or {}).get("language_mode", "es"),
        "source": "llm2_global_interpretation_plus_deterministic_frame",
        "sections": sections,
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
    axis_ontology = load_axis_ontology()
    base_language_mode = "es" if language_mode == "en" else language_mode
    variant_interpretations = [compact_variant(row, base_language_mode) for row in rows]
    deterministic_summary = build_deterministic_summary(rows, axis_ontology)
    canonical_analysis_frame = build_canonical_analysis_frame(rows, deterministic_summary)
    llm_payload = {
        "language_mode": base_language_mode,
        "audience_mode": audience_mode,
        "project_context": project_context(),
        "deterministic_summary": deterministic_summary,
        "canonical_analysis_frame": canonical_analysis_frame,
        "variant_interpretations": variant_interpretations,
    }
    prompt_text = PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    audit_metadata = {
        "pipeline_version": PIPELINE_VERSION,
        "axis_ontology_version": AXIS_ONTOLOGY_VERSION,
        "llm2_prompt_version": LLM2_PROMPT_VERSION,
        "translation_prompt_version": TRANSLATION_PROMPT_VERSION,
        "global_interpretation_schema_version": GLOBAL_INTERPRETATION_SCHEMA_VERSION,
        "canonical_frame_version": CANONICAL_FRAME_VERSION,
        "deterministic_validator_version": DETERMINISTIC_VALIDATOR_VERSION,
        "structured_report_version": STRUCTURED_REPORT_VERSION,
        "model_name": model,
        "model_temperature": "default",
        "requested_language_mode": language_mode,
        "base_language_mode": base_language_mode,
        "audience_mode": audience_mode,
        "input_hash": file_hash_or_empty(input_path),
        "deterministic_summary_hash": sha256_json(deterministic_summary),
        "canonical_analysis_frame_hash": sha256_json(canonical_analysis_frame),
        "llm_payload_hash": sha256_json(llm_payload),
        "llm2_prompt_hash": sha256_text(prompt_text),
        "global_interpretation_schema_hash": sha256_json(schema),
        "axis_ontology_hash": sha256_json(axis_ontology),
        "generated_at": utc_now(),
    }
    llm_payload["audit_metadata"] = audit_metadata
    audit_metadata["llm_payload_with_audit_hash"] = sha256_json(llm_payload)

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
            "audit_metadata": audit_metadata,
            "canonical_analysis_frame": canonical_analysis_frame,
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
        report["deterministic_validation"] = {
            "validator_version": DETERMINISTIC_VALIDATOR_VERSION,
            "warnings": [],
            "canonical_analysis_frame_hash": sha256_json(canonical_analysis_frame),
        }
    else:
        report = call_openai_structured(llm_payload, api_key=api_key, model=model, timeout_seconds=timeout_seconds)
        report = sanitize_report(report)
        report["audit_metadata"] = dict(audit_metadata)
        report, validation_warnings = validate_and_repair_report(report, canonical_analysis_frame, deterministic_summary, base_language_mode)
        report["deterministic_validation"] = {
            "validator_version": DETERMINISTIC_VALIDATOR_VERSION,
            "warnings": validation_warnings,
            "canonical_analysis_frame_hash": sha256_json(canonical_analysis_frame),
        }
        report["canonical_analysis_frame"] = canonical_analysis_frame
        report["structured_report"] = build_structured_report(report, canonical_analysis_frame)
        if language_mode == "en":
            write_json(source_es_report_json, report)
            source_es_report_hash = sha256_file(source_es_report_json)
            report = translate_report(
                report,
                target_language_mode="en",
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            report = sanitize_report(report)
            report, translation_validation_warnings = validate_and_repair_report(
                report,
                canonical_analysis_frame,
                deterministic_summary,
                language_mode,
            )
            report["audit_metadata"] = {
                **audit_metadata,
                "translation_source_language_mode": base_language_mode,
                "translation_target_language_mode": language_mode,
                "translation_source_report_hash": source_es_report_hash,
                "translation_model_name": model,
            }
            report["deterministic_validation"] = {
                "validator_version": DETERMINISTIC_VALIDATOR_VERSION,
                "warnings": translation_validation_warnings,
                "canonical_analysis_frame_hash": sha256_json(canonical_analysis_frame),
            }
            report["canonical_analysis_frame"] = canonical_analysis_frame
            report["structured_report"] = build_structured_report(report, canonical_analysis_frame)

    report["audit_metadata"] = {
        **audit_metadata,
        **(report.get("audit_metadata") or {}),
    }
    if "structured_report" not in report:
        report["structured_report"] = build_structured_report(report, canonical_analysis_frame)
    if "canonical_analysis_frame" not in report:
        report["canonical_analysis_frame"] = canonical_analysis_frame
    write_json(report_json, report)
    global_interpretation_file_hash = sha256_file(report_json)
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
        "canonical_axis_count": len(canonical_analysis_frame["allowed_axes"]),
        "informational_report_readiness": canonical_analysis_frame["readiness"]["informational_report_readiness"],
        "clinical_use_readiness": canonical_analysis_frame["readiness"]["clinical_use_readiness"],
        "model": model,
        "dry_run": dry_run,
        "language_mode": language_mode,
        "base_language_mode": base_language_mode,
        "audience_mode": audience_mode,
        "overall_readiness": report.get("final_recommendation", {}).get("overall_readiness", ""),
        "audit_metadata": {
            **(report.get("audit_metadata") or audit_metadata),
            "global_interpretation_json_hash": global_interpretation_file_hash,
        },
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
            "globalInterpretationEsSourceJson": str(source_es_report_json) if source_es_report_json.exists() else "",
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
