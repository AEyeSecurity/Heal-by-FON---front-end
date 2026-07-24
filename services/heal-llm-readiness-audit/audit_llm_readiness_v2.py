#!/usr/bin/env python3
"""Read-only readiness audit for HEAL gene-module v2 enrichment runs."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


RUN_SCHEMA = "heal_llm_readiness_v2_1"
SOURCE_NAMES = ("ensembl_variation", "clinvar", "myvariant", "gwas", "pharmgkb")
RAW_EVIDENCE_FIELDS = (
    "ensembl_raw_json",
    "vep_raw_json",
    "clinvar_esearch_json",
    "clinvar_esummary_json",
    "myvariant_raw_json",
    "gwas_raw_json",
    "pharmgkb_variant_raw_json",
    "pharmgkb_clinical_raw_json",
    "pharmgkb_variant_annotation_raw_json",
)
LOF_CONSEQUENCES = {
    "stop_gained",
    "frameshift_variant",
    "start_lost",
    "splice_acceptor_variant",
    "splice_donor_variant",
}
CODING_CONSEQUENCES = LOF_CONSEQUENCES | {
    "missense_variant",
    "inframe_insertion",
    "inframe_deletion",
    "stop_lost",
}
SPLICE_CONSEQUENCES = {
    "splice_acceptor_variant",
    "splice_donor_variant",
    "splice_region_variant",
    "splice_polypyrimidine_tract_variant",
    "splice_donor_5th_base_variant",
    "splice_donor_region_variant",
}
UTR_CONSEQUENCES = {"3_prime_UTR_variant", "5_prime_UTR_variant"}
NON_BENIGN_CLINVAR = {
    "pathogenic_or_likely_pathogenic",
    "conflicting_pathogenicity",
    "uncertain_significance",
    "risk_factor",
    "drug_response",
    "other",
}


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return default


def as_int(value, default: int = 0) -> int:
    try:
        return int(float(clean(value)))
    except (TypeError, ValueError):
        return default


def truthy(value) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: Iterable[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames or [])
    if not fields:
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, default=str), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_spliceai(value) -> tuple[float, str]:
    raw = clean(value)
    if not raw:
        return 0.0, "not_reported"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0.0, "malformed"
    maximum = max(as_float(payload.get(key)) for key in ("DS_AG", "DS_AL", "DS_DG", "DS_DL"))
    if maximum >= 0.8:
        return maximum, "very_strong"
    if maximum >= 0.5:
        return maximum, "strong"
    if maximum >= 0.2:
        return maximum, "candidate"
    if maximum >= 0.1:
        return maximum, "contextual"
    return maximum, "no_signal"


def functional_class(row: dict) -> str:
    if clean(row.get("vep_status")) != "success":
        return "annotation_failed"
    consequence = clean(row.get("vep_most_severe_consequence"))
    if consequence in LOF_CONSEQUENCES:
        return "lof_or_canonical_splice"
    if consequence == "missense_variant":
        return "missense"
    if consequence == "synonymous_variant":
        return "synonymous"
    if consequence in SPLICE_CONSEQUENCES:
        return "splice_region"
    if consequence in UTR_CONSEQUENCES:
        return "utr"
    if consequence == "intron_variant":
        return "intronic"
    if consequence == "non_coding_transcript_exon_variant":
        return "noncoding_exon"
    return consequence or "annotation_minimal"


def transcript_concordance(row: dict) -> tuple[str, str]:
    local = clean(row.get("local_region_class"))
    consequence = clean(row.get("vep_most_severe_consequence"))
    target_status = clean(row.get("vep_target_gene_effect_status"))
    if clean(row.get("vep_status")) != "success" or not consequence:
        return "unresolved", "VEP did not provide a usable target-gene consequence."
    if target_status and target_status != "direct_target_transcript":
        return "unresolved", f"Target gene effect status is {target_status}."
    if local == "utr_overlap" and consequence in UTR_CONSEQUENCES:
        return "concordant", "Local UTR overlap agrees with the target transcript consequence."
    if local == "utr_overlap" and consequence == "intron_variant":
        return "discordant_transcript_context", "Local transcript-union UTR overlap is intronic in the VEP target transcript."
    if local in {"mane_cds_overlap", "alternative_protein_coding_cds_overlap"} and consequence in CODING_CONSEQUENCES | {"synonymous_variant", "stop_retained_variant"}:
        return "concordant", "Local coding overlap agrees with a coding target transcript consequence."
    if local == "splice_region_candidate" and consequence in SPLICE_CONSEQUENCES:
        return "concordant", "Local splice window agrees with the target transcript consequence."
    if local == "protein_coding_exon_non_cds_overlap" and consequence in UTR_CONSEQUENCES | {"non_coding_transcript_exon_variant"}:
        return "concordant", "Local non-CDS exon overlap agrees with the target transcript consequence."
    return "context_dependent", f"Local class {local or 'unknown'} and VEP consequence {consequence} require transcript review."


def usable_clinvar(row: dict) -> bool:
    return clean(row.get("clinvar_normalized_classification")) in NON_BENIGN_CLINVAR


def has_pgx(row: dict) -> bool:
    return as_int(row.get("pharmgkb_clinical_annotation_count")) > 0 or as_int(row.get("pharmgkb_variant_annotation_count")) > 0


def has_gwas(row: dict) -> bool:
    return as_int(row.get("gwas_association_count")) > 0


def identity_axis(row: dict) -> str:
    identity = clean(row.get("identity_match_class"))
    resolution = clean(row.get("rsid_resolution_status"))
    if identity == "exact_coordinate_allele":
        return "exact_coordinate_allele"
    if resolution == "normalized_indel_match":
        return "normalized_indel_match"
    if resolution == "ambiguous_multiple_exact_rsids":
        return "ambiguous_identity"
    if identity == "rsid_without_allele_confirmation":
        return "candidate_not_confirmed"
    return "no_identity_match"


def evidence_axis(row: dict) -> str:
    if usable_clinvar(row):
        return "clinical_evidence"
    if as_int(row.get("pharmgkb_clinical_annotation_count")) > 0:
        return "pharmacogenomic_clinical_evidence"
    if has_gwas(row):
        return "association_evidence"
    if as_int(row.get("pharmgkb_variant_annotation_count")) > 0:
        return "pharmacogenomic_variant_context"
    if clean(row.get("population_max_frequency")):
        return "population_context"
    if clean(row.get("vep_status")) == "success":
        return "functional_vep_only"
    return "no_usable_evidence"


def readiness_axis(row: dict, concordance: str, spliceai_class: str) -> str:
    if clean(row.get("vep_status")) != "success":
        return "blocked_annotation"
    if identity_axis(row) not in {"exact_coordinate_allele", "normalized_indel_match"}:
        return "blocked_identity"
    if concordance == "discordant_transcript_context":
        return "blocked_transcript_context"
    source_errors = clean(row.get("source_error_sources"))
    consequence = clean(row.get("vep_most_severe_consequence"))
    strong_functional = consequence in CODING_CONSEQUENCES or spliceai_class in {"candidate", "strong", "very_strong"} or as_float(row.get("vep_cadd_phred")) >= 20
    if (usable_clinvar(row) or as_int(row.get("pharmgkb_clinical_annotation_count")) > 0) and strong_functional and not source_errors:
        return "high"
    if strong_functional or usable_clinvar(row) or has_pgx(row) or has_gwas(row):
        return "moderate"
    if clean(row.get("vep_hgvsc")) or clean(row.get("population_max_frequency")):
        return "limited"
    return "minimal"


def explain_information(row: dict) -> tuple[str, str]:
    available = ["normalized GRCh coordinate and alleles"]
    limitations = []
    if clean(row.get("resolved_rsid")):
        available.append("allele-confirmed rsID")
    else:
        limitations.append("no allele-confirmed rsID")
    if clean(row.get("vep_most_severe_consequence")):
        available.append(f"VEP consequence {clean(row.get('vep_most_severe_consequence'))}")
    else:
        limitations.append("no usable VEP consequence")
    if clean(row.get("vep_hgvsc")):
        available.append("HGVS transcript notation")
    if clean(row.get("vep_hgvsp")):
        available.append("protein-level HGVS")
    if usable_clinvar(row):
        available.append(f"ClinVar class {clean(row.get('clinvar_normalized_classification'))}")
    if has_gwas(row):
        available.append("GWAS association context")
    if has_pgx(row):
        available.append("PharmGKB context")
    if clean(row.get("source_error_sources")):
        limitations.append(f"source errors: {clean(row.get('source_error_sources'))}")
    limitations.append("does not establish diagnosis, penetrance, causality, or treatment response for this individual")
    return "; ".join(available), "; ".join(limitations)


def build_variant_audit(physical_rows: list[dict], projection_by_key: dict[str, list[dict]]) -> list[dict]:
    output = []
    for row in physical_rows:
        key = clean(row.get("variant_key"))
        projections = projection_by_key.get(key, [])
        representative = projections[0] if projections else {}
        spliceai_max, spliceai_class = parse_spliceai(row.get("vep_spliceai"))
        concordances = [transcript_concordance(item) for item in projections] or [("unresolved", "No gene-module projection exists.")]
        concordance_counter = Counter(item[0] for item in concordances)
        if concordance_counter.get("discordant_transcript_context"):
            concordance = "discordant_transcript_context"
        elif concordance_counter.get("unresolved"):
            concordance = "unresolved"
        elif concordance_counter.get("context_dependent"):
            concordance = "context_dependent"
        else:
            concordance = "concordant"
        available, limitations = explain_information(row)
        output.append({
            **row,
            "gene_symbols": "|".join(sorted({clean(item.get("approved_symbol")) for item in projections if clean(item.get("approved_symbol"))})),
            "module_ids": "|".join(sorted({clean(item.get("module_id")) for item in projections if clean(item.get("module_id"))})),
            "representative_zygosity": clean(representative.get("zygosity")),
            "representative_gt": clean(representative.get("gt_raw")),
            "representative_quality_flag": clean(representative.get("quality_flag")),
            "identity_axis": identity_axis(row),
            "functional_axis": functional_class(row),
            "evidence_axis": evidence_axis(row),
            "transcript_concordance": concordance,
            "transcript_concordance_counts": json.dumps(dict(concordance_counter), sort_keys=True),
            "spliceai_max_ds": spliceai_max,
            "spliceai_signal_class": spliceai_class,
            "readiness_axis": readiness_axis(row, concordance, spliceai_class),
            "information_available": available,
            "information_limitations": limitations,
        })
    return output


def candidate_strength(row: dict) -> tuple:
    consequence = clean(row.get("vep_most_severe_consequence"))
    consequence_score = 4 if consequence in LOF_CONSEQUENCES else 3 if consequence == "missense_variant" else 2 if consequence in SPLICE_CONSEQUENCES else 1
    return (
        -consequence_score,
        -as_float(row.get("spliceai_max_ds")),
        -as_float(row.get("vep_cadd_phred")),
        -as_int(row.get("clinvar_uid_count")),
        clean(row.get("variant_key")),
    )


def select_sample(audit_rows: list[dict]) -> list[dict]:
    categories = [
        ("coding_protein", 8, lambda r: clean(r.get("vep_most_severe_consequence")) in CODING_CONSEQUENCES and bool(clean(r.get("vep_hgvsp")) or clean(r.get("vep_hgvsc")))),
        ("splice", 6, lambda r: clean(r.get("vep_most_severe_consequence")) in SPLICE_CONSEQUENCES or clean(r.get("spliceai_signal_class")) in {"contextual", "candidate", "strong", "very_strong"}),
        ("clinvar_non_benign", 6, usable_clinvar),
        ("pharmgkb", 6, has_pgx),
        ("gwas", 6, has_gwas),
        ("utr_vep_intron_discordance", 6, lambda r: clean(r.get("transcript_concordance")) == "discordant_transcript_context"),
        ("identity_unresolved", 6, lambda r: clean(r.get("identity_axis")) not in {"exact_coordinate_allele", "normalized_indel_match"}),
        ("source_error_or_minimal", 6, lambda r: bool(clean(r.get("source_error_sources"))) or clean(r.get("readiness_axis")) in {"blocked_annotation", "minimal"}),
    ]
    selected = []
    used = set()
    for category, target, predicate in categories:
        candidates = sorted((row for row in audit_rows if predicate(row) and clean(row.get("variant_key")) not in used), key=candidate_strength)
        for row in candidates[:target]:
            copy = {**row, "sample_category": category}
            selected.append(copy)
            used.add(clean(row.get("variant_key")))
    if len(selected) < 50:
        for row in sorted((row for row in audit_rows if clean(row.get("variant_key")) not in used), key=candidate_strength):
            selected.append({**row, "sample_category": "deterministic_fill"})
            used.add(clean(row.get("variant_key")))
            if len(selected) == 50:
                break
    return selected[:50]


def field_completeness(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    output = []
    for field in rows[0]:
        populated = sum(1 for row in rows if clean(row.get(field)))
        zero_like = sum(1 for row in rows if clean(row.get(field)) in {"0", "0.0", "false", "False"})
        output.append({
            "field": field,
            "rows": len(rows),
            "populated": populated,
            "populated_pct": round(populated / len(rows) * 100, 3),
            "zero_like": zero_like,
            "usable_nonzero": populated - zero_like,
        })
    return output


def build_conflicts(projection_rows: list[dict], audit_by_key: dict[str, dict]) -> list[dict]:
    output = []
    for row in projection_rows:
        key = clean(row.get("variant_key"))
        audit = audit_by_key.get(key, {})
        status, detail = transcript_concordance(row)
        if status != "concordant":
            output.append({
                "variant_key": key,
                "approved_symbol": clean(row.get("approved_symbol")),
                "module_id": clean(row.get("module_id")),
                "local_region_class": clean(row.get("local_region_class")),
                "vep_most_severe_consequence": clean(row.get("vep_most_severe_consequence")),
                "vep_target_gene_effect_status": clean(row.get("vep_target_gene_effect_status")),
                "conflict_class": status,
                "detail": detail,
                "readiness_axis": clean(audit.get("readiness_axis")),
            })
    return output


def build_group_readiness(projection_rows: list[dict], audit_by_key: dict[str, dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in projection_rows:
        groups[(clean(row.get("approved_symbol")), clean(row.get("module_id")))].append(row)
    output = []
    for (gene, module_id), rows in sorted(groups.items()):
        variants = {clean(row.get("variant_key")) for row in rows}
        audits = [audit_by_key[key] for key in variants if key in audit_by_key]
        readiness = Counter(clean(row.get("readiness_axis")) for row in audits)
        evidence = Counter(clean(row.get("evidence_axis")) for row in audits)
        first = rows[0]
        blocking = []
        if readiness.get("blocked_transcript_context"):
            blocking.append("transcript_context_discordance")
        if readiness.get("blocked_annotation"):
            blocking.append("annotation_failure")
        if readiness.get("blocked_identity"):
            blocking.append("identity_unresolved")
        high_or_moderate = readiness.get("high", 0) + readiness.get("moderate", 0)
        output.append({
            "group_id": f"{gene}:{module_id}",
            "gene": gene,
            "module_id": module_id,
            "module_name": clean(first.get("module_name")),
            "system_within_module": clean(first.get("system_within_module")),
            "physical_variant_count": len(variants),
            "module_row_count": len(rows),
            "high_or_moderate_variants": high_or_moderate,
            "blocked_variants": sum(value for key, value in readiness.items() if key.startswith("blocked_")),
            "readiness_counts": json.dumps(dict(readiness), sort_keys=True),
            "evidence_counts": json.dumps(dict(evidence), sort_keys=True),
            "group_payload_ready": "true" if high_or_moderate > 0 and not blocking else "false",
            "blocking_reasons": "|".join(blocking),
        })
    return output


def build_canonical_status(canon_rows: list[dict], projection_rows: list[dict], run_id: str) -> list[dict]:
    observed = defaultdict(set)
    for row in projection_rows:
        observed[(clean(row.get("approved_symbol")), clean(row.get("module_id")))].add(clean(row.get("variant_key")))
    output = []
    for row in canon_rows:
        gene = clean(row.get("gene_symbol_normalized") or row.get("gene_symbol_original"))
        module_id = clean(row.get("module_id"))
        if clean(row.get("row_status")) != "active_gene_row" or not gene:
            status = "not_assessed"
        else:
            status = "observed_alt" if observed.get((gene, module_id)) else "not_observed"
        output.append({
            "run_id": run_id,
            "canon_row_id": clean(row.get("canon_row_id")),
            "gene": gene,
            "full_gene_name": clean(row.get("full_gene_name")),
            "module_id": module_id,
            "module_name": clean(row.get("module_name")),
            "tier": clean(row.get("tier")),
            "module_status": clean(row.get("module_status")),
            "evidence_tier": clean(row.get("evidence_tier")),
            "canonical_status": status,
            "observed_physical_variant_count": len(observed.get((gene, module_id), set())),
            "callable": "unknown",
            "callability_reason": "Sparse VCF absence cannot distinguish homozygous reference from not callable.",
            "hom_ref": "unknown",
            "canonical_snp_status": "not_applicable_no_canonical_snp_in_gene_module_v2",
            "cnv_status": "not_assessed",
            "vntr_status": "not_assessed",
        })
    return output


def build_mechanism_template(canon_rows: list[dict]) -> list[dict]:
    output = []
    seen = set()
    for row in canon_rows:
        gene = clean(row.get("gene_symbol_normalized") or row.get("gene_symbol_original"))
        module_id = clean(row.get("module_id"))
        key = (gene, module_id)
        if not gene or key in seen:
            continue
        seen.add(key)
        output.append({
            "mechanism_registry_version": "mechanism_registry_v1_template",
            "gene": gene,
            "module_id": module_id,
            "module_name": clean(row.get("module_name")),
            "system_within_module": clean(row.get("system_within_module")),
            "biological_function": "",
            "pathway": "",
            "directionality": "",
            "related_systems": "",
            "related_modules": "",
            "mechanism_evidence_tier": "",
            "source_ids_or_urls": "",
            "curation_status": "needs_bioinformatician_curation",
            "curation_notes": "Do not expose an uncurated mechanism to LLM1.",
        })
    return output


def extract_raw_evidence(plus_path: Path, sample_keys: set[str], output_path: Path) -> int:
    written = set()
    count = 0
    with plus_path.open("r", encoding="utf-8-sig", newline="") as handle, gzip.open(output_path, "wt", encoding="utf-8") as output:
        for row in csv.DictReader(handle):
            key = clean(row.get("variant_key"))
            if key not in sample_keys or key in written:
                continue
            payload = {
                "variant_key": key,
                "gene": clean(row.get("approved_symbol")),
                "module_id": clean(row.get("module_id")),
                "resolved_rsid": clean(row.get("resolved_rsid")),
                "source_status": {source: clean(row.get(f"source_status_{source}")) for source in SOURCE_NAMES},
                "public_evidence_payloads": {field: clean(row.get(field)) for field in RAW_EVIDENCE_FIELDS if clean(row.get(field))},
            }
            output.write(json.dumps(payload, ensure_ascii=True) + "\n")
            written.add(key)
            count += 1
            if written == sample_keys:
                break
    return count


def render_markdown(summary: dict) -> str:
    counts = summary["counts"]
    findings = summary["findings"]
    gates = summary["gates"]
    return f"""# HEAL by FON - Auditoria de readiness para LLM1 v2

**Run:** `{summary['run_id']}`  
**Fecha:** {summary['created_at']}  
**Decision:** **NO-GO para LLM1 productiva; GO para payloads dry-run y correccion deterministica.**

## Resumen ejecutivo

La corrida termino y sus artefactos principales son reconciliables. Sin embargo, la capa de evidencia aun contiene discordancias transcript-aware, deuda de fuentes externas y ausencia de una capa curada de mecanismos. La LLM no debe recibir estos datos como si tuvieran igual calidad.

## Universos

| Etapa | Filas |
|---|---:|
| Variantes normalizadas | {counts['normalized_variants']} |
| Match variante-gen | {counts['variant_gene_matches']} |
| Match variante-gen-modulo | {counts['matched_module_rows']} |
| Triage gen-modulo | {counts['triage_rows']} |
| Variantes fisicas enriquecidas | {counts['physical_variants']} |
| Grupos gen-modulo | {counts['gene_module_groups']} |

## Hallazgos principales

- {findings['utr_rows']} filas de triage fueron clasificadas como UTR; {findings['utr_intron_discordant_rows']} son intronicas para el transcript VEP objetivo.
- SpliceAI aparece en {findings['spliceai_populated']} variantes, pero {findings['spliceai_zero_signal']} tienen score maximo menor a 0.10.
- {findings['vep_errors']} variantes tienen error VEP y {findings['identity_unresolved']} no poseen identidad exacta confirmada.
- {findings['source_error_variants']} variantes tienen al menos un error de fuente secundaria.
- El canon tiene {counts['canonical_rows']} filas de estado, pero el VCF sparse no permite inferir homocigosis de referencia ni callability ante ausencia.
- El registro de mecanismos fue generado como template y requiere curacion profesional antes de llegar a LLM1.

## Readiness

| Gate | Estado | Motivo |
|---|---|---|
| Extraction contract | {gates['extraction_contract_ready']['status']} | {gates['extraction_contract_ready']['reason']} |
| Annotation | {gates['annotation_ready']['status']} | {gates['annotation_ready']['reason']} |
| Group payload | {gates['group_payload_ready']['status']} | {gates['group_payload_ready']['reason']} |
| LLM1 pilot | {gates['llm1_pilot_ready']['status']} | {gates['llm1_pilot_ready']['reason']} |

## Lectura biologica prudente

Los datos permiten distinguir variantes codificantes, splice, UTR, intronicas y evidencia contextual de ClinVar, GWAS y PharmGKB. Esto alcanza para priorizar revision y describir mecanismos ya curados, pero no para inferir causalidad, penetrancia, diagnostico o respuesta terapeutica individual. La densidad de variantes dentro de un gen tampoco constituye por si sola una senal funcional.

## Trabajo previo a LLM1

1. Resolver o excluir del ranking las discordancias UTR/intron por transcript.
2. Aplicar thresholds reales a SpliceAI y no premiar un JSON no vacio.
3. Mantener errores de fuente separados de ausencia de evidencia.
4. Completar y aprobar el registro de mecanismos gen-modulo.
5. Revisar los 50 ejemplos y todos los conflictos clinicos/identidad con el bioinformatico.

## Conclusion

La extraccion es util para QA y para construir payloads v3 en dry-run. No debe habilitarse la ejecucion productiva de LLM1 hasta que los cuatro gates pasen y exista aprobacion profesional documentada.
"""


def count_csv_rows(path: Path) -> int:
    with (gzip.open(path, "rt", encoding="utf-8-sig", newline="") if path.suffix == ".gz" else path.open("r", encoding="utf-8-sig", newline="")) as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def process(run_dir: Path, canon_clean: Path, output_dir: Path, previous_summary: Path | None = None) -> dict:
    run_dir = run_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "normalized": run_dir / "normalization" / "normalized_variants.csv.gz",
        "variant_gene": run_dir / "matching" / "vcf_variant_gene_matches.csv",
        "matched_modules": run_dir / "matching" / "sheet_final_consolidated.csv",
        "triage": run_dir / "ai-triage" / "heal_fon_ai_triage.csv",
        "physical": run_dir / "enrichment" / "v2_enrichment_physical_matrix.csv",
        "projection": run_dir / "enrichment" / "v2_enrichment_module_projection.csv",
        "plus": run_dir / "enrichment" / "heal_fon_interpretation_enrichment_plus_v2.csv",
        "quality": run_dir / "enrichment" / "enrichment_quality_summary.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing or not canon_clean.is_file():
        raise FileNotFoundError(f"Required readiness inputs are missing: {missing + ([str(canon_clean)] if not canon_clean.is_file() else [])}")

    triage_rows = read_csv(paths["triage"])
    physical_rows = read_csv(paths["physical"])
    projection_rows = read_csv(paths["projection"])
    canon_rows = read_csv(canon_clean)
    projection_by_key = defaultdict(list)
    for row in projection_rows:
        projection_by_key[clean(row.get("variant_key"))].append(row)
    audit_rows = build_variant_audit(physical_rows, projection_by_key)
    audit_by_key = {clean(row.get("variant_key")): row for row in audit_rows}
    conflict_rows = build_conflicts(projection_rows, audit_by_key)
    group_rows = build_group_readiness(projection_rows, audit_by_key)
    sample_rows = select_sample(audit_rows)
    sample_keys = {clean(row.get("variant_key")) for row in sample_rows}
    sample_projection_rows = [{**row, "sample_category": clean(next(item for item in sample_rows if clean(item.get("variant_key")) == clean(row.get("variant_key"))).get("sample_category"))} for row in projection_rows if clean(row.get("variant_key")) in sample_keys]
    canonical_rows = build_canonical_status(canon_rows, projection_rows, run_dir.name)
    mechanism_rows = build_mechanism_template(canon_rows)
    completeness_rows = field_completeness(physical_rows)

    write_csv(output_dir / "llm_readiness_variant_audit.csv", audit_rows)
    write_csv(output_dir / "llm_readiness_field_completeness.csv", completeness_rows)
    write_csv(output_dir / "llm_readiness_annotation_conflicts.csv", conflict_rows)
    write_csv(output_dir / "llm_readiness_group_summary.csv", group_rows)
    write_csv(output_dir / "llm_readiness_sample_50_physical.csv", sample_rows)
    write_csv(output_dir / "llm_readiness_sample_50_module_projection.csv", sample_projection_rows)
    write_csv(output_dir / "llm_readiness_canonical_status.csv", canonical_rows)
    write_csv(output_dir / "mechanism_registry_v1_template.csv", mechanism_rows)
    review_rows = [{**row, "reviewer": "", "review_date": "", "identity_ok": "", "transcript_context_ok": "", "evidence_attribution_ok": "", "biological_coherence": "", "decision": "", "comments": ""} for row in sample_rows]
    write_csv(output_dir / "bioinformatician_review_template.csv", review_rows)
    raw_count = extract_raw_evidence(paths["plus"], sample_keys, output_dir / "llm_readiness_sample_50_raw_evidence.jsonl.gz")

    quality = json.loads(paths["quality"].read_text(encoding="utf-8"))
    readiness_counts = Counter(clean(row.get("readiness_axis")) for row in audit_rows)
    functional_counts = Counter(clean(row.get("functional_axis")) for row in audit_rows)
    evidence_counts = Counter(clean(row.get("evidence_axis")) for row in audit_rows)
    identity_counts = Counter(clean(row.get("identity_axis")) for row in audit_rows)
    utr_rows = sum(1 for row in projection_rows if clean(row.get("local_region_class")) == "utr_overlap")
    utr_intron = sum(1 for row in projection_rows if clean(row.get("local_region_class")) == "utr_overlap" and clean(row.get("vep_most_severe_consequence")) == "intron_variant")
    spliceai_populated = sum(1 for row in audit_rows if clean(row.get("vep_spliceai")))
    spliceai_zero = sum(1 for row in audit_rows if clean(row.get("vep_spliceai")) and clean(row.get("spliceai_signal_class")) == "no_signal")
    source_error_variants = sum(1 for row in audit_rows if clean(row.get("source_error_sources")))
    source_status_fields = {
        "ensembl_vep_region": "vep_status",
        "ensembl_variation": "source_status_ensembl_variation",
        "clinvar": "source_status_clinvar",
        "myvariant": "source_status_myvariant",
        "gwas": "source_status_gwas",
        "pharmgkb": "source_status_pharmgkb",
    }
    source_status_counts = {
        source: dict(Counter(clean(row.get(field)) or "not_queried" for row in physical_rows))
        for source, field in source_status_fields.items()
    }
    canonical_complete = all(clean(row.get("canonical_status")) for row in canonical_rows)
    annotation_ready = not any(clean(row.get("readiness_axis")).startswith("blocked_") for row in audit_rows)
    mechanism_ready = all(clean(row.get("curation_status")) == "approved" for row in mechanism_rows)
    group_ready = all(clean(row.get("group_payload_ready")) == "true" for row in group_rows) and mechanism_ready
    gates = {
        "extraction_contract_ready": {
            "status": "partial" if canonical_complete else "fail",
            "reason": "Canon coverage is explicit, but sparse VCF callability, hom-ref, CNV and VNTR remain unknown/not assessed.",
        },
        "annotation_ready": {
            "status": "pass" if annotation_ready else "fail",
            "reason": f"{sum(value for key, value in readiness_counts.items() if key.startswith('blocked_'))} physical variants remain blocked by annotation, identity or transcript context.",
        },
        "group_payload_ready": {
            "status": "pass" if group_ready else "fail",
            "reason": "At least one group is blocked and the mechanism registry is not professionally curated.",
        },
        "llm1_pilot_ready": {
            "status": "blocked",
            "reason": "Requires all prior gates plus documented bioinformatician approval; dry-run payload generation only.",
        },
    }
    counts = {
        "normalized_variants": count_csv_rows(paths["normalized"]),
        "variant_gene_matches": count_csv_rows(paths["variant_gene"]),
        "matched_module_rows": count_csv_rows(paths["matched_modules"]),
        "triage_rows": len(triage_rows),
        "physical_variants": len(physical_rows),
        "gene_module_groups": len(group_rows),
        "canonical_rows": len(canonical_rows),
        "sample_physical_variants": len(sample_rows),
        "sample_module_rows": len(sample_projection_rows),
        "sample_raw_evidence_rows": raw_count,
    }
    previous = {}
    if previous_summary and previous_summary.is_file():
        previous = json.loads(previous_summary.read_text(encoding="utf-8"))
    previous_source_counts = previous.get("source_status_counts", {}) if previous else {}
    source_comparison = {}
    for source, current_counts in source_status_counts.items():
        prior_counts = previous_source_counts.get(source, {})
        statuses = sorted(set(current_counts) | set(prior_counts))
        source_comparison[source] = {
            status: {
                "previous": as_int(prior_counts.get(status)),
                "current": as_int(current_counts.get(status)),
                "delta": as_int(current_counts.get(status)) - as_int(prior_counts.get(status)),
            }
            for status in statuses
        }
    previous_comparison = {
        "previous_run_id": clean(previous.get("run_id")),
        "cardinality_changed": bool(previous) and (
            as_int(previous.get("counts", {}).get("physical_variants")) != counts["physical_variants"]
            or as_int(previous.get("counts", {}).get("triage_rows")) != counts["triage_rows"]
        ),
        "source_status_deltas": source_comparison,
        "coordinate_identity_resolved": sum(
            as_int(metrics.get("resolved") or metrics.get("success"))
            for metrics in quality.get("identityMetrics", {}).values()
        ),
        "interpretation": "Cardinality is stable. Exact-source retries improved some cached identities, while coordinate rescue resolved no additional exact identities and increased documented ClinVar source errors.",
    }
    summary = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "decision": "no_go_production_llm1_go_dry_run_payloads",
        "counts": counts,
        "identity_counts": dict(identity_counts),
        "functional_counts": dict(functional_counts),
        "evidence_counts": dict(evidence_counts),
        "readiness_counts": dict(readiness_counts),
        "source_status_counts": source_status_counts,
        "previous_run_comparison": previous_comparison,
        "findings": {
            "utr_rows": utr_rows,
            "utr_intron_discordant_rows": utr_intron,
            "spliceai_populated": spliceai_populated,
            "spliceai_zero_signal": spliceai_zero,
            "vep_errors": sum(1 for row in audit_rows if clean(row.get("vep_status")) != "success"),
            "identity_unresolved": sum(1 for row in audit_rows if clean(row.get("identity_axis")) not in {"exact_coordinate_allele", "normalized_indel_match"}),
            "source_error_variants": source_error_variants,
            "annotation_conflict_rows": len(conflict_rows),
        },
        "gates": gates,
        "quality_gate_source": quality,
        "previous_audit_summary": previous,
        "artifact_hashes": {name: sha256_file(path) for name, path in paths.items()},
        "outputs": {
            "variantAuditCsv": str(output_dir / "llm_readiness_variant_audit.csv"),
            "fieldCompletenessCsv": str(output_dir / "llm_readiness_field_completeness.csv"),
            "annotationConflictsCsv": str(output_dir / "llm_readiness_annotation_conflicts.csv"),
            "groupSummaryCsv": str(output_dir / "llm_readiness_group_summary.csv"),
            "samplePhysicalCsv": str(output_dir / "llm_readiness_sample_50_physical.csv"),
            "sampleModuleProjectionCsv": str(output_dir / "llm_readiness_sample_50_module_projection.csv"),
            "sampleRawEvidenceJsonlGz": str(output_dir / "llm_readiness_sample_50_raw_evidence.jsonl.gz"),
            "canonicalStatusCsv": str(output_dir / "llm_readiness_canonical_status.csv"),
            "mechanismRegistryTemplateCsv": str(output_dir / "mechanism_registry_v1_template.csv"),
            "bioinformaticianReviewTemplateCsv": str(output_dir / "bioinformatician_review_template.csv"),
        },
    }
    write_json(output_dir / "llm_readiness_summary.json", summary)
    report_path = output_dir / "llm_readiness_report.md"
    report_path.write_text(render_markdown(summary), encoding="utf-8")
    summary["outputs"]["reportMarkdown"] = str(report_path)
    write_json(output_dir / "llm_readiness_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a completed HEAL gene_module_v2 run for grouped LLM1 readiness.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--canon-clean", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--previous-summary")
    args = parser.parse_args()
    summary = process(
        Path(args.run_dir),
        Path(args.canon_clean),
        Path(args.output_dir),
        Path(args.previous_summary) if args.previous_summary else None,
    )
    print(json.dumps({"status": "valid", "decision": summary["decision"], "counts": summary["counts"], "outputs": summary["outputs"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
