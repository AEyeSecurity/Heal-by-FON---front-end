#!/usr/bin/env python3
"""Build transcript-aware, allele-specific HEAL LLM1 payloads v6."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
V5_PATH = SCRIPT_DIR.parent / "heal-llm1-payload-v5" / "build_llm1_payload_v5.py"
PAYLOAD_VERSION = "llm1_group_payload_v6"
PILOT_GROUPS = {
    "MTHFR:T1.1": "clinical_conflict_and_compression",
    "PEMT:T1.3": "functional_and_clinical",
    "IL6:T1.4": "gwas_relevance",
    "NQO1:T1.6": "pharmacogenomic_context",
    "IFNG:T3.5": "abstention",
}
TARGET_READY = {"confirmed", "alternative_transcript"}
CODING_CONSEQUENCES = {
    "transcript_ablation", "splice_acceptor_variant", "splice_donor_variant", "stop_gained", "frameshift_variant",
    "stop_lost", "start_lost", "transcript_amplification", "inframe_insertion", "inframe_deletion", "missense_variant",
    "protein_altering_variant", "splice_region_variant", "incomplete_terminal_codon_variant", "start_retained_variant",
    "stop_retained_variant", "synonymous_variant", "coding_sequence_variant",
}
CONSEQUENCE_RANK = {
    "transcript_ablation": 0,
    "splice_acceptor_variant": 1,
    "splice_donor_variant": 1,
    "stop_gained": 2,
    "frameshift_variant": 3,
    "stop_lost": 4,
    "start_lost": 4,
    "transcript_amplification": 5,
    "inframe_insertion": 6,
    "inframe_deletion": 6,
    "missense_variant": 7,
    "protein_altering_variant": 8,
    "splice_region_variant": 9,
    "incomplete_terminal_codon_variant": 10,
    "start_retained_variant": 11,
    "stop_retained_variant": 11,
    "synonymous_variant": 12,
    "coding_sequence_variant": 13,
    "5_prime_UTR_variant": 14,
    "3_prime_UTR_variant": 14,
    "non_coding_transcript_exon_variant": 15,
    "intron_variant": 16,
    "upstream_gene_variant": 17,
    "downstream_gene_variant": 17,
    "intergenic_variant": 18,
}


def load_v5_module():
    spec = importlib.util.spec_from_file_location("heal_llm1_payload_v5_for_v6", V5_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load v5 payload builder: {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v5 = load_v5_module()


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_key_values(raw: str) -> dict:
    result = {}
    for part in raw.split("; "):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def parse_transcripts(row: dict) -> list[dict]:
    transcripts = []
    for raw in v5.clean(row.get("vep_transcript_summary")).split("|"):
        raw = raw.strip()
        if not raw:
            continue
        parsed = parse_key_values(raw)
        if parsed.get("tx") or parsed.get("gene"):
            transcripts.append(parsed)
    return transcripts


def consequence_rank(value: str) -> int:
    terms = re.split(r"[,&]", v5.clean(value))
    return min((CONSEQUENCE_RANK.get(term.strip(), 99) for term in terms if term.strip()), default=99)


def local_consequence_concordance(row: dict, transcript: dict) -> tuple[str, str]:
    local_region = v5.clean(row.get("local_region_class"))
    consequences = {item.strip() for item in re.split(r"[,&]", v5.clean(transcript.get("consequence"))) if item.strip()}
    if not transcript:
        return "unavailable", "No target-gene transcript consequence is available."
    if local_region == "utr_overlap":
        matched = bool(consequences & {"5_prime_UTR_variant", "3_prime_UTR_variant"})
        return ("concordant", "Target-gene transcript confirms a UTR consequence.") if matched else (
            "discordant", "Local UTR overlap is not confirmed as UTR on the selected target-gene transcript."
        )
    if local_region in {"mane_cds_overlap", "alternative_protein_coding_cds_overlap"}:
        matched = bool(consequences & CODING_CONSEQUENCES)
        return ("concordant", "Target-gene transcript confirms a coding or splice consequence.") if matched else (
            "discordant", "Local coding overlap is not confirmed as coding on the selected target-gene transcript."
        )
    if local_region == "protein_coding_exon_non_cds_overlap":
        excluded = {"intron_variant", "upstream_gene_variant", "downstream_gene_variant", "intergenic_variant"}
        matched = bool(consequences) and not consequences.issubset(excluded)
        return ("concordant", "Target-gene transcript confirms an exonic consequence.") if matched else (
            "discordant", "Local exon overlap is intronic or external on the selected target-gene transcript."
        )
    if local_region == "splice_region_candidate":
        return (
            "concordant" if any("splice" in item for item in consequences) else "splice_window_context",
            "Target-gene transcript consequence is evaluated together with the deterministic splice window.",
        )
    return "concordant", "Target-gene transcript is compatible with the local region classification."


def choose_target_transcript(row: dict) -> tuple[str, dict, str]:
    target_gene = v5.clean(row.get("approved_symbol"))
    picked_gene = v5.clean(row.get("vep_picked_gene_symbol"))
    picked_tx = v5.clean(row.get("vep_picked_transcript"))
    transcripts = [item for item in parse_transcripts(row) if v5.clean(item.get("gene")) == target_gene]
    if not transcripts:
        status = "discordant_gene" if picked_gene and picked_gene != target_gene else "no_target_gene_consequence"
        reason = (
            f"VEP picked {picked_gene}, and no transcript consequence was found for {target_gene}."
            if picked_gene and picked_gene != target_gene
            else f"No VEP transcript consequence was found for target gene {target_gene}."
        )
        return status, {}, reason

    def rank(item: dict) -> tuple:
        tx = v5.clean(item.get("tx"))
        biotype = v5.clean(item.get("biotype"))
        is_global_pick = tx and tx == picked_tx and picked_gene == target_gene
        mane_select = v5.clean(item.get("mane_select")) or (v5.clean(row.get("vep_mane_select")) if is_global_pick else "")
        mane_plus_clinical = v5.clean(item.get("mane_plus_clinical")) or (
            v5.clean(row.get("vep_mane_plus_clinical")) if is_global_pick else ""
        )
        canonical = v5.clean(item.get("canonical")) or (v5.clean(row.get("vep_canonical")) if is_global_pick else "")
        return (
            0 if mane_select else 1,
            0 if mane_plus_clinical else 1,
            0 if canonical else 1,
            0 if is_global_pick else 1,
            0 if biotype == "protein_coding" else 1,
            consequence_rank(item.get("consequence", "")),
            0 if item.get("hgvsp") else 1,
            0 if item.get("hgvsc") else 1,
            tx,
        )

    selected = sorted(transcripts, key=rank)[0]
    if picked_gene == target_gene and v5.clean(selected.get("tx")) == picked_tx:
        status = "confirmed"
        reason = "VEP picked transcript belongs to the target gene."
    else:
        status = "alternative_transcript"
        reason = "A target-gene transcript consequence was selected instead of the VEP global picked transcript."
    return status, selected, reason


def parse_population_observations(row: dict) -> list[dict]:
    observations = []
    for raw in v5.clean(row.get("ensembl_populations")).split("|"):
        parsed = parse_key_values(raw.strip())
        if not parsed.get("allele") or not parsed.get("freq"):
            continue
        try:
            frequency = float(parsed["freq"])
        except ValueError:
            continue
        observations.append({
            "population": v5.clean(parsed.get("pop")),
            "allele": v5.clean(parsed.get("allele")),
            "frequency": frequency,
        })
    return observations


def allele_specific_frequency(row: dict) -> dict:
    alt = v5.clean(row.get("alt_vcf"))
    ref = v5.clean(row.get("ref_vcf"))
    observations = parse_population_observations(row)
    matches = [item for item in observations if item["allele"] == alt]
    best = max(matches, key=lambda item: item["frequency"], default=None)
    other = [item for item in observations if item["allele"] != alt]
    return {
        "observed_alt": alt,
        "reference_allele": ref,
        "observed_alt_frequency": best["frequency"] if best else None,
        "observed_alt_frequency_population": best["population"] if best else "",
        "frequency_relation": "observed_alt" if best else "not_available_for_observed_alt",
        "matching_observation_count": len(matches),
        "other_allele_observation_count": len(other),
        "other_allele_context": sorted(other, key=lambda item: (-item["frequency"], item["population"]))[:2],
        "source": "ensembl_variation_populations",
    }


def enrich_detail_row(row: dict) -> tuple[dict, dict, dict]:
    output = dict(row)
    status, transcript, reason = choose_target_transcript(row)
    output["vep_target_gene_effect_status"] = status
    output["vep_target_gene_effect_reason"] = reason
    output["vep_target_gene_symbol"] = v5.clean(row.get("approved_symbol"))
    output["vep_target_transcript"] = v5.clean(transcript.get("tx"))
    local_status, local_reason = local_consequence_concordance(row, transcript)
    output["vep_target_local_consequence_concordance"] = local_status
    output["vep_target_local_consequence_reason"] = local_reason
    if transcript:
        output["vep_most_severe_consequence"] = v5.clean(transcript.get("consequence"))
        output["vep_picked_transcript"] = v5.clean(transcript.get("tx"))
        output["vep_hgvsc"] = v5.clean(transcript.get("hgvsc"))
        output["vep_hgvsp"] = v5.clean(transcript.get("hgvsp"))
        output["vep_cadd_phred"] = v5.clean(transcript.get("cadd_phred")) or v5.clean(row.get("vep_cadd_phred"))
        output["vep_revel_score"] = v5.clean(transcript.get("revel")) or v5.clean(row.get("vep_revel_score"))
        output["vep_spliceai"] = v5.clean(transcript.get("spliceai")) or v5.clean(row.get("vep_spliceai"))
    if status not in TARGET_READY or local_status not in {"concordant", "splice_window_context"}:
        output["focus_eligible"] = "false"
    frequency = allele_specific_frequency(row)
    target_audit = {
        "group_id": f"{v5.clean(row.get('approved_symbol'))}:{v5.clean(row.get('module_id'))}",
        "variant_key": v5.clean(row.get("variant_key")),
        "variant_ref": v5.variant_ref(row),
        "target_gene": v5.clean(row.get("approved_symbol")),
        "vep_picked_gene": v5.clean(row.get("vep_picked_gene_symbol")),
        "vep_global_picked_transcript": v5.clean(row.get("vep_picked_transcript")),
        "target_gene_annotation_status": status,
        "selected_target_transcript": v5.clean(transcript.get("tx")),
        "selected_consequence": v5.clean(transcript.get("consequence")),
        "selected_hgvsc": v5.clean(transcript.get("hgvsc")),
        "selected_hgvsp": v5.clean(transcript.get("hgvsp")),
        "selected_mane_select": v5.clean(transcript.get("mane_select")) or (
            v5.clean(row.get("vep_mane_select"))
            if v5.clean(transcript.get("tx")) == v5.clean(row.get("vep_picked_transcript"))
            else ""
        ),
        "selected_mane_plus_clinical": v5.clean(transcript.get("mane_plus_clinical")) or (
            v5.clean(row.get("vep_mane_plus_clinical"))
            if v5.clean(transcript.get("tx")) == v5.clean(row.get("vep_picked_transcript"))
            else ""
        ),
        "selected_canonical": v5.clean(transcript.get("canonical")) or (
            v5.clean(row.get("vep_canonical"))
            if v5.clean(transcript.get("tx")) == v5.clean(row.get("vep_picked_transcript"))
            else ""
        ),
        "local_consequence_concordance": local_status,
        "local_consequence_reason": local_reason,
        "focus_eligible_v5": str(v5.as_bool(row.get("focus_eligible"))).lower(),
        "focus_eligible_v6": str(
            v5.as_bool(output.get("focus_eligible"))
            and status in TARGET_READY
            and local_status in {"concordant", "splice_window_context"}
        ).lower(),
        "reason": reason,
    }
    frequency_audit = {
        "group_id": target_audit["group_id"],
        "variant_key": target_audit["variant_key"],
        "variant_ref": target_audit["variant_ref"],
        **frequency,
        "legacy_max_frequency": v5.clean(row.get("population_max_frequency")),
        "legacy_max_frequency_population": v5.clean(row.get("population_max_frequency_population")),
        "legacy_max_frequency_allele": v5.clean(row.get("population_max_frequency_allele")),
    }
    return output, target_audit, frequency_audit


def condition_key(value: str) -> str:
    return re.sub(r"\s+", " ", v5.clean(value).lower()) or "not_reported"


def clinvar_conflict_semantics(assertions: list[dict]) -> tuple[dict, list[dict]]:
    by_variant_condition: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_variant: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in assertions:
        key = (v5.clean(row.get("variant_key")), condition_key(row.get("conditions")))
        classification = v5.clean(row.get("normalized_classification")) or "not_reported"
        by_variant_condition[key].add(classification)
    for (variant_key, condition), classes in by_variant_condition.items():
        for classification in classes:
            by_variant[variant_key].append((condition, classification))

    same_condition = []
    cross_condition = []
    drug_response = []
    audit = []
    for variant_key, pairs in sorted(by_variant.items()):
        condition_classes = defaultdict(set)
        for condition, classification in pairs:
            condition_classes[condition].add(classification)
            if classification == "drug_response":
                drug_response.append(variant_key)
        same = {
            condition: sorted(classes)
            for condition, classes in condition_classes.items()
            if len(classes) > 1 or "conflicting_pathogenicity" in classes
        }
        distinct_by_condition = {condition: tuple(sorted(classes)) for condition, classes in condition_classes.items()}
        cross = len(set(distinct_by_condition.values())) > 1 and not same
        if same:
            same_condition.append(variant_key)
        elif cross:
            cross_condition.append(variant_key)
        audit.append({
            "variant_key": variant_key,
            "same_condition_conflict": str(bool(same)).lower(),
            "cross_condition_heterogeneity": str(cross).lower(),
            "drug_response_context": str(any(classification == "drug_response" for _, classification in pairs)).lower(),
            "condition_count": len(condition_classes),
            "condition_classifications_json": json.dumps(
                {condition: sorted(classes) for condition, classes in condition_classes.items()},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        })
    summary = {
        "same_condition_conflict_variant_keys": sorted(set(same_condition)),
        "cross_condition_heterogeneity_variant_keys": sorted(set(cross_condition)),
        "drug_response_context_variant_keys": sorted(set(drug_response)),
        "aggregate_without_conflict_count": sum(
            1 for row in audit if row["same_condition_conflict"] == "false" and row["cross_condition_heterogeneity"] == "false"
        ),
    }
    return summary, audit


def ensure_persistent_registry(path: Path | None, rows: list[dict], fieldnames: list[str]) -> list[dict]:
    if not path:
        return rows
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return read_csv(path)
    write_csv(path, rows, fieldnames)
    return rows


def build_gwas_registry_template(clusters: list[dict]) -> list[dict]:
    seen = set()
    rows = []
    for cluster in sorted(clusters, key=lambda row: (
        v5.clean(row.get("approved_symbol")), v5.clean(row.get("module_id")), v5.clean(row.get("trait_id"))
    )):
        key = (
            v5.clean(cluster.get("approved_symbol")),
            v5.clean(cluster.get("module_id")),
            v5.clean(cluster.get("trait_id")),
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        rows.append({
            "registry_version": "gwas_module_relevance_registry_v1",
            "gene": key[0],
            "approved_symbol": key[0],
            "module_id": key[1],
            "trait_id": key[2],
            "trait_labels": v5.clean(cluster.get("trait_labels")),
            "relevance_status": "unreviewed",
            "relevance_reason": "",
            "source_ids_or_urls": "",
            "reviewer": "",
            "reviewed_at": "",
            "review_notes": "",
        })
    return rows


def apply_gwas_registry(clusters: list[dict], registry: list[dict]) -> list[dict]:
    index = {
        (v5.clean(row.get("gene") or row.get("approved_symbol")), v5.clean(row.get("module_id")), v5.clean(row.get("trait_id"))): row
        for row in registry
    }
    output = []
    for source in clusters:
        row = dict(source)
        key = (v5.clean(row.get("approved_symbol")), v5.clean(row.get("module_id")), v5.clean(row.get("trait_id")))
        review = index.get(key, {})
        status = v5.clean(review.get("relevance_status")) or "unreviewed"
        row["module_relevance_status"] = status
        row["module_relevance_reason"] = v5.clean(review.get("relevance_reason"))
        if status == "approved" and v5.as_int(row.get("allele_confirmed_significant_count")) > 0:
            if v5.as_int(row.get("independent_publication_count")) >= 2 and v5.clean(row.get("direction_status")) != "conflicting":
                row["evidence_band"] = "high_confidence_replicated"
            else:
                row["evidence_band"] = "moderate_contextual"
        elif status == "rejected":
            row["evidence_band"] = "context_only"
        output.append(row)
    return output


def v6_focus_frequency(payload: dict, enriched_rows: list[dict]) -> None:
    row_index = {v5.clean(row.get("variant_key")): row for row in enriched_rows}
    for item in payload.get("focus_variant_evidence") or []:
        row = row_index.get(v5.clean(item.get("variant_key")), {})
        status = v5.clean(row.get("vep_target_gene_effect_status"))
        item["target_gene_annotation"] = {
            "status": status,
            "target_gene": v5.clean(row.get("approved_symbol")),
            "selected_transcript": v5.clean(row.get("vep_target_transcript")),
            "reason": v5.clean(row.get("vep_target_gene_effect_reason")),
            "local_consequence_concordance": v5.clean(row.get("vep_target_local_consequence_concordance")),
            "local_consequence_reason": v5.clean(row.get("vep_target_local_consequence_reason")),
        }
        item["functional_evidence"]["target_gene_effect_status"] = status
        item["population"] = allele_specific_frequency(row)


def build_manifest(payloads: list[dict]) -> list[dict]:
    by_id = {row["group_id"]: row for row in payloads}
    output = []
    for group_id, category in PILOT_GROUPS.items():
        payload = by_id.get(group_id)
        if not payload:
            output.append({
                "group_id": group_id, "gene": group_id.split(":", 1)[0], "module_id": group_id.split(":", 1)[1],
                "selection_category": category, "estimated_tokens": 0, "focus_variant_count": 0,
                "target_concordant_focus_count": 0, "observed_alt_frequency_count": 0, "source_error_count": 0,
                "mechanism_status": "missing", "gwas_relevance_status": "not_applicable",
                "blockers": "group_not_available", "approved_for_pilot": "false", "approval_reviewer": "",
                "approval_timestamp": "", "review_notes": "",
            })
            continue
        blockers = [key for key, ready in payload["gates"].items() if key != "llm1_pilot_ready" and not ready]
        output.append({
            "group_id": group_id,
            "gene": payload["gene"],
            "module_id": payload["module_id"],
            "selection_category": category,
            "estimated_tokens": payload["compression_metadata"]["estimated_tokens"],
            "focus_variant_count": len(payload["focus_variant_evidence"]),
            "target_concordant_focus_count": sum(
                item.get("target_gene_annotation", {}).get("status") in TARGET_READY
                and item.get("target_gene_annotation", {}).get("local_consequence_concordance")
                in {"concordant", "splice_window_context"}
                for item in payload["focus_variant_evidence"]
            ),
            "observed_alt_frequency_count": sum(
                item.get("population", {}).get("frequency_relation") == "observed_alt"
                for item in payload["focus_variant_evidence"]
            ),
            "source_error_count": v5.as_int(payload.get("source_failures", {}).get("count")),
            "mechanism_status": payload["curated_mechanism"]["curation_status"],
            "gwas_relevance_status": payload["professional_curation"]["gwas_relevance_status"],
            "blockers": " | ".join(blockers),
            "approved_for_pilot": "false",
            "approval_reviewer": "",
            "approval_timestamp": "",
            "review_notes": "",
        })
    return output


def process(payload: dict) -> dict:
    output_dir = Path(payload["outputDir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_rows = read_csv(Path(payload["detailPath"]))
    if not detail_rows:
        raise ValueError("V6 payload builder input detail is empty.")
    canonical_rows = read_csv(Path(payload["canonicalStatusPath"])) if payload.get("canonicalStatusPath") else []
    run_mechanisms = read_csv(Path(payload["mechanismRegistryPath"])) if payload.get("mechanismRegistryPath") else []
    assertions = read_csv(Path(payload["clinvarAssertionsPath"])) if payload.get("clinvarAssertionsPath") else []
    raw_clusters = read_csv(Path(payload["gwasClustersPath"])) if payload.get("gwasClustersPath") else []
    publications = read_csv(Path(payload["publicationsPath"])) if payload.get("publicationsPath") else []
    mechanism_path = Path(payload["persistentMechanismRegistryPath"]).resolve() if payload.get("persistentMechanismRegistryPath") else None
    gwas_path = Path(payload["persistentGwasRegistryPath"]).resolve() if payload.get("persistentGwasRegistryPath") else None
    mechanism_fields = list(run_mechanisms[0]) if run_mechanisms else [
        "mechanism_registry_version", "gene", "module_id", "curation_status", "biological_function", "pathway",
        "directionality", "related_systems", "related_modules", "mechanism_evidence_tier", "source_ids_or_urls",
        "reviewer", "reviewed_at", "review_notes",
    ]
    mechanisms = ensure_persistent_registry(mechanism_path, run_mechanisms, mechanism_fields)
    gwas_fields = [
        "registry_version", "gene", "approved_symbol", "module_id", "trait_id", "trait_labels", "relevance_status", "relevance_reason",
        "source_ids_or_urls", "reviewer", "reviewed_at", "review_notes",
    ]
    gwas_registry = ensure_persistent_registry(gwas_path, build_gwas_registry_template(raw_clusters), gwas_fields)
    clusters = apply_gwas_registry(raw_clusters, gwas_registry)

    enriched_rows = []
    target_audit = []
    frequency_audit = []
    for row in detail_rows:
        enriched, target, frequency = enrich_detail_row(row)
        enriched_rows.append(enriched)
        target_audit.append(target)
        frequency_audit.append(frequency)

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in enriched_rows:
        groups[(v5.clean(row.get("approved_symbol")), v5.clean(row.get("module_id")))].append(row)
    canonical_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in canonical_rows:
        canonical_index[(v5.clean(row.get("approved_symbol") or row.get("gene")), v5.clean(row.get("module_id")))].append(row)
    assertions_by_variant: dict[str, list[dict]] = defaultdict(list)
    for row in assertions:
        assertions_by_variant[v5.clean(row.get("variant_key"))].append(row)

    conflict_summary, conflict_audit = clinvar_conflict_semantics(assertions)
    conflict_by_variant = {row["variant_key"]: row for row in conflict_audit}
    payloads = []
    errors = []
    token_audit = []
    for key, rows in sorted(groups.items()):
        group_id = f"{key[0]}:{key[1]}"
        try:
            rows.sort(key=lambda row: (v5.as_int(row.get("attention_rank"), 10**9), -v5.as_int(row.get("attention_score"))))
            variant_keys = {v5.clean(row.get("variant_key")) for row in rows}
            group_assertions = [item for variant_key in variant_keys for item in assertions_by_variant.get(variant_key, [])]
            group_clusters = [row for row in clusters if v5.clean(row.get("approved_symbol")) == key[0] and v5.clean(row.get("module_id")) == key[1]]
            publication_ids = {pmid for row in group_assertions for pmid in re.findall(r"\d+", v5.clean(row.get("pmids")))}
            publication_ids.update(pmid for row in group_clusters for pmid in v5.split_values(row.get("publication_ids")))
            group_publications = [row for row in publications if v5.clean(row.get("pmid")) in publication_ids]
            mechanism = v5.mechanism_for_group(mechanisms, key)
            built, coverage, audit = v5.build_group_payload(
                rows, canonical_index.get(key, []), mechanism, group_assertions, group_clusters, group_publications,
                v5.clean(payload.get("tokenizerModel")),
            )
            built["payload_schema_version"] = PAYLOAD_VERSION
            v6_focus_frequency(built, rows)
            group_conflicts = [conflict_by_variant[item] for item in variant_keys if item in conflict_by_variant]
            built["clinical_evidence_summary"]["condition_conflict_semantics"] = {
                "same_condition_conflict_variant_keys": sorted(
                    row["variant_key"] for row in group_conflicts if row["same_condition_conflict"] == "true"
                ),
                "cross_condition_heterogeneity_variant_keys": sorted(
                    row["variant_key"] for row in group_conflicts if row["cross_condition_heterogeneity"] == "true"
                ),
                "drug_response_context_variant_keys": sorted(
                    row["variant_key"] for row in group_conflicts if row["drug_response_context"] == "true"
                ),
            }
            discordant = [row for row in rows if v5.clean(row.get("vep_target_gene_effect_status")) not in TARGET_READY]
            built["target_gene_discordant_context"] = v5.compact_context(discordant)
            frequencies = [allele_specific_frequency(row) for row in rows]
            built["allele_specific_frequency_summary"] = {
                "variants_with_observed_alt_frequency": sum(item["observed_alt_frequency"] is not None for item in frequencies),
                "variants_without_observed_alt_frequency": sum(item["observed_alt_frequency"] is None for item in frequencies),
                "other_allele_context_present": sum(bool(item["other_allele_context"]) for item in frequencies),
                "audit_artifact": "allele_specific_frequency_audit.csv",
            }
            approved_gwas = [row for row in group_clusters if v5.clean(row.get("module_relevance_status")) == "approved"]
            unreviewed_gwas = [row for row in group_clusters if v5.clean(row.get("module_relevance_status")) == "unreviewed"]
            built["professional_curation"] = {
                "mechanism_registry_version": mechanism.get("registry_version", "mechanism_registry_v1"),
                "mechanism_status": mechanism.get("curation_status", "missing"),
                "gwas_registry_version": "gwas_module_relevance_registry_v1",
                "gwas_relevance_status": "approved" if approved_gwas else "unreviewed" if unreviewed_gwas else "not_applicable",
                "approved_gwas_cluster_count": len(approved_gwas),
                "unreviewed_gwas_cluster_count": len(unreviewed_gwas),
            }
            target_ready = all(
                item.get("target_gene_annotation", {}).get("status") in TARGET_READY
                and item.get("target_gene_annotation", {}).get("local_consequence_concordance") in {"concordant", "splice_window_context"}
                for item in built.get("focus_variant_evidence") or []
            )
            category = PILOT_GROUPS.get(group_id, "")
            gwas_ready = category != "gwas_relevance" or bool(built["gwas_evidence_summary"].get("prioritized_clusters"))
            built["gates"].update({
                "target_gene_annotation_ready": target_ready,
                "allele_specific_frequency_ready": all(
                    item.get("population", {}).get("frequency_relation") in {"observed_alt", "not_available_for_observed_alt"}
                    for item in built.get("focus_variant_evidence") or []
                ),
                "gwas_relevance_ready": gwas_ready,
            })
            built["gates"]["group_payload_ready"] = all([
                built["gates"].get("token_budget_ready"),
                built["gates"].get("mechanism_registry_ready"),
                built["gates"].get("source_errors_acceptable_for_pilot"),
                target_ready,
                gwas_ready,
            ])
            built["gates"]["llm1_pilot_ready"] = False
            tokens, method = v5.estimate_tokens(built, v5.clean(payload.get("tokenizerModel")))
            built["compression_metadata"].update({
                "estimated_tokens": tokens,
                "estimation_method": method,
                "budget_status": v5.token_status(tokens) if tokens <= v5.HARD_TOKENS else "compression_review_required",
            })
            built["gates"]["token_budget_ready"] = tokens <= v5.HARD_TOKENS
            built["gates"]["group_payload_ready"] = built["gates"]["group_payload_ready"] and tokens <= v5.HARD_TOKENS
            built["provenance"].update({
                "payload_builder": PAYLOAD_VERSION,
                "target_gene_audit_artifact": "target_gene_consequence_audit.csv",
                "frequency_audit_artifact": "allele_specific_frequency_audit.csv",
                "clinvar_conflict_audit_artifact": "clinvar_condition_conflict_audit.csv",
                "mechanism_registry_artifact": str(mechanism_path) if mechanism_path else "",
                "gwas_relevance_registry_artifact": str(gwas_path) if gwas_path else "",
            })
            payloads.append(built)
            token_audit.append({
                **audit,
                "estimated_tokens": tokens,
                "estimation_method": method,
                "budget_status": built["compression_metadata"]["budget_status"],
                "focus_variants": len(built["focus_variant_evidence"]),
                "target_gene_discordant": len(discordant),
                "group_payload_ready": str(built["gates"]["group_payload_ready"]).lower(),
            })
        except Exception as error:  # noqa: BLE001
            errors.append({"group_id": group_id, "error": str(error)})

    payload_path = output_dir / "llm1_group_payloads_v6.jsonl"
    payload_csv = output_dir / "llm1_group_payloads_v6.csv"
    schema_path = output_dir / "llm1_group_payload_v6.schema.json"
    target_path = output_dir / "target_gene_consequence_audit.csv"
    frequency_path = output_dir / "allele_specific_frequency_audit.csv"
    conflict_path = output_dir / "clinvar_condition_conflict_audit.csv"
    token_path = output_dir / "group_token_budget_audit_v6.csv"
    manifest_path = output_dir / "llm1_pilot_candidate_manifest_v3.csv"
    error_path = output_dir / "group_payload_v6_errors.csv"
    summary_path = output_dir / "llm1_group_payload_v6_summary.json"
    mechanism_snapshot_path = output_dir / "mechanism_registry_v1_snapshot.csv"
    gwas_snapshot_path = output_dir / "gwas_module_relevance_registry_v1_snapshot.csv"
    write_jsonl(payload_path, payloads)
    write_csv(payload_csv, [
        {"group_id": row["group_id"], "payload_json": json.dumps(row, ensure_ascii=False, separators=(",", ":"))}
        for row in payloads
    ], ["group_id", "payload_json"])
    write_csv(target_path, target_audit, list(target_audit[0]) if target_audit else ["group_id", "variant_key"])
    write_csv(frequency_path, frequency_audit, list(frequency_audit[0]) if frequency_audit else ["group_id", "variant_key"])
    write_csv(conflict_path, conflict_audit, list(conflict_audit[0]) if conflict_audit else ["variant_key"])
    write_csv(token_path, token_audit, list(token_audit[0]) if token_audit else ["group_id", "estimated_tokens"])
    manifest = build_manifest(payloads)
    write_csv(manifest_path, manifest, list(manifest[0]) if manifest else ["group_id", "approved_for_pilot"])
    write_csv(error_path, errors, ["group_id", "error"])
    write_csv(mechanism_snapshot_path, mechanisms, mechanism_fields)
    write_csv(gwas_snapshot_path, gwas_registry, gwas_fields)
    schema_path.write_text((SCRIPT_DIR / "llm1_group_payload_v6.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    target_counts = dict(Counter(row["target_gene_annotation_status"] for row in target_audit))
    focus_rows_v5 = [row for row in target_audit if row["focus_eligible_v5"] == "true"]
    focus_rows_v6 = [row for row in target_audit if row["focus_eligible_v6"] == "true"]
    legacy_frequency_mismatches = sum(
        bool(row["legacy_max_frequency_allele"])
        and row["legacy_max_frequency_allele"] != row["observed_alt"]
        for row in frequency_audit
    )
    summary = {
        "status": "valid" if not errors else "warning",
        "schemaVersion": "gene_module_v2",
        "payloadSchemaVersion": PAYLOAD_VERSION,
        "executionMode": "dry_run",
        "metadata": {
            "total_groups": len(payloads),
            "source_rows": len(detail_rows),
            "source_variants_total": len({v5.clean(row.get("variant_key")) for row in detail_rows}),
            "target_gene_status_counts": target_counts,
            "v5_focus_eligible_rows": len(focus_rows_v5),
            "v6_focus_eligible_rows": len(focus_rows_v6),
            "focus_rows_reclassified": len(focus_rows_v5) - len(focus_rows_v6),
            "legacy_frequency_allele_mismatches": legacy_frequency_mismatches,
            "groups_with_approved_mechanism": sum(row["gates"]["mechanism_registry_ready"] for row in payloads),
            "groups_payload_ready": sum(row["gates"]["group_payload_ready"] for row in payloads),
            "groups_within_hard_limit": sum(row["gates"]["token_budget_ready"] for row in payloads),
            "max_estimated_tokens": max((row["compression_metadata"]["estimated_tokens"] for row in payloads), default=0),
            "focus_variants_total": sum(len(row.get("focus_variant_evidence") or []) for row in payloads),
            "source_failure_groups": sum(v5.as_int(row.get("source_failures", {}).get("count")) > 0 for row in payloads),
            "source_failure_records": sum(v5.as_int(row.get("source_failures", {}).get("count")) for row in payloads),
            "frequency_relation_counts": dict(Counter(row["frequency_relation"] for row in frequency_audit)),
            "gwas_relevance_status_counts": dict(
                Counter(v5.clean(row.get("relevance_status")) or "unreviewed" for row in gwas_registry)
            ),
            "groups_with_approved_gwas_relevance": sum(
                bool(row.get("gwas_evidence_summary", {}).get("prioritized_clusters")) for row in payloads
            ),
            "pilot_candidates": len(manifest),
            "pilot_manifest": manifest,
            "llm_calls": 0,
            "clinvar_conflict_summary": conflict_summary,
        },
        "outputs": {
            "groupPayloadsJsonlV6": str(payload_path),
            "groupPayloadsCsvV6": str(payload_csv),
            "groupPayloadSchemaV6Json": str(schema_path),
            "targetGeneConsequenceAuditCsv": str(target_path),
            "alleleSpecificFrequencyAuditCsv": str(frequency_path),
            "clinvarConditionConflictAuditCsv": str(conflict_path),
            "groupTokenBudgetAuditV6Csv": str(token_path),
            "llm1PilotCandidateManifestV3Csv": str(manifest_path),
            "groupPayloadV6ErrorsCsv": str(error_path),
            "groupPayloadV6SummaryJson": str(summary_path),
            "persistentMechanismRegistryCsv": str(mechanism_snapshot_path),
            "persistentGwasRegistryCsv": str(gwas_snapshot_path),
        },
        "gates": {
            "targetGeneAnnotationReady": "pass" if all(
                item.get("target_gene_annotation", {}).get("status") in TARGET_READY
                and item.get("target_gene_annotation", {}).get("local_consequence_concordance") in {"concordant", "splice_window_context"}
                for row in payloads for item in row.get("focus_variant_evidence") or []
            ) else "fail",
            "alleleSpecificFrequencyReady": "pass",
            "tokenBudgetReady": "pass" if all(row["gates"]["token_budget_ready"] for row in payloads) else "review_required",
            "llm1PilotReady": "blocked",
        },
        "timestamps": {"completedAt": utc_now()},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build transcript-aware HEAL LLM1 group payloads v6.")
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
    process(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
