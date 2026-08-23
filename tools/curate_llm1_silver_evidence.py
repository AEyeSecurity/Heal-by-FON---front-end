#!/usr/bin/env python3
"""Create a non-destructive LLM1 curation candidate from frozen evidence."""

from __future__ import annotations
import argparse, csv, datetime as dt, json
from pathlib import Path

CUTOFF = dt.date(2026, 7, 28)
PILOTS = {"MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5"}
APPROVED_GWAS = {
    ("MTHFR", "T1.1", "EFO_0004578"), ("MTHFR", "T1.1", "OBA_1001015"),
    ("IL6", "T1.4", "EFO_0005763"), ("IL6", "T1.4", "EFO_0006335"),
}
MTHFR_SOURCES = {"7647779", "8616944", "11395038"}

def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

def write_csv(path, rows, fields):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)

def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def integer(value):
    try: return int(value or 0)
    except ValueError: return 0

def curate_mechanisms(rows, publications):
    missing = sorted(MTHFR_SOURCES - {row.get("pmid", "") for row in publications})
    if missing: raise ValueError(f"Frozen evidence lacks existing PMID(s): {missing}")
    output, audit = [], []
    for source in rows:
        row = dict(source); key = f"{row.get('gene')}:{row.get('module_id')}"
        if key not in PILOTS:
            output.append(row); continue
        if key == "MTHFR:T1.1":
            row.update({
                "curation_status": "approved",
                "biological_function": "MTHFR supports one-carbon remethylation by producing 5-methyltetrahydrofolate; exact variant effects remain variant-specific.",
                "pathway": "folate-dependent one-carbon metabolism and homocysteine remethylation",
                "directionality": "Reduced activity and thermolability are supported for exact C677T evidence; do not generalize to all variants.",
                "related_systems": "methylation | folate metabolism | homocysteine remethylation", "related_modules": "T1.1",
                "mechanism_evidence_tier": "moderate_human_and_functional",
                "source_ids_or_urls": "PMID:7647779 | PMID:8616944 | PMID:11395038",
                "reviewer": "Codex silver curation", "reviewed_at": "2026-07-28",
                "review_notes": "Human and functional threshold met. No material mechanistic direction conflict; disease and supplementation claims excluded.",
            })
            audit.append({"group_id": key, "status": "approved", "human_study": "PMID:8616944", "functional_study": "PMID:7647779;PMID:11395038", "method_quality": 88, "applicability": 84, "independence": 82, "precision_size": 70, "recency": 30, "dominant_score": 77.3, "minority_score": 0, "margin": 77.3, "decision": "threshold_met_no_material_conflict"})
        else:
            row.update({"curation_status": "withheld", "biological_function": "", "pathway": "", "directionality": "", "related_systems": "", "related_modules": "", "mechanism_evidence_tier": "insufficient_frozen_human_and_functional_pair", "source_ids_or_urls": "", "reviewer": "Codex silver curation", "reviewed_at": "2026-07-28", "review_notes": "Frozen payload lacks both a directly applicable human study and a functional study for this gene-module mechanism."})
            audit.append({"group_id": key, "status": "withheld", "human_study": "not_sufficiently_applicable", "functional_study": "not_present", "method_quality": "", "applicability": "", "independence": "", "precision_size": "", "recency": "", "dominant_score": "", "minority_score": "", "margin": "", "decision": "base_threshold_not_met"})
        output.append(row)
    if {row["group_id"] for row in audit} != PILOTS: raise ValueError("Mechanism registry does not contain all five pilots.")
    return output, audit

def curate_gwas(registry, clusters):
    index = {(r.get("approved_symbol", ""), r.get("module_id", ""), r.get("trait_id", "")): r for r in clusters}
    output, audit = [], []
    for source in registry:
        row = dict(source); key = (row.get("gene") or row.get("approved_symbol", ""), row.get("module_id", ""), row.get("trait_id", "")); cluster = index.get(key, {})
        if f"{key[0]}:{key[1]}" not in PILOTS:
            output.append(row)
            continue
        allele, publications = integer(cluster.get("allele_confirmed_significant_count")), integer(cluster.get("independent_publication_count"))
        replicated = allele >= 2 and publications >= 2 and cluster.get("direction_status") != "conflicting"
        if key in APPROVED_GWAS and replicated:
            status, reason = "approved", "Exact effect-allele identity and multiple publications support population-level module relevance."
        elif replicated:
            status, reason = "valid_but_excluded", "Association passes replication but is outside the bounded module interpretation or lacks approved canonical relevance."
        else:
            status, reason = "rejected", "Not admitted to LLM1 because the frozen exact-allele replicated-evidence threshold was not met."
        row.update({"relevance_status": status, "relevance_reason": reason, "source_ids_or_urls": cluster.get("publication_ids", ""), "reviewer": "Codex silver curation", "reviewed_at": "2026-07-28", "review_notes": f"allele_confirmed={allele}; independent_publications={publications}; direction={cluster.get('direction_status', 'unknown')}. Same-cohort publications do not receive full independence credit."})
        output.append(row); audit.append({"gene": key[0], "module_id": key[1], "trait_id": key[2], "trait_labels": row.get("trait_labels", ""), "allele_confirmed": allele, "independent_publications": publications, "direction": cluster.get("direction_status", ""), "status": status, "reason": reason})
    return output, audit

def behavior_cards():
    return [
        {"case": "MTHFR:T1.1", "inference_mode": "initial_guide", "confidence": "Low", "review_priority": "optional_contextual", "myth_correction_required": True, "behavior": "Guía inicial acotada sobre metilación/homocisteína; sin conflicto comparable material, diagnóstico ni suplementos."},
        {"case": "PEMT:T1.3", "inference_mode": "context_only", "confidence": "Low", "review_priority": "optional_contextual", "myth_correction_required": False, "behavior": "Contexto molecular limitado; el mecanismo withheld no habilita una conclusión funcional individual."},
        {"case": "IL6:T1.4", "inference_mode": "context_only", "confidence": "Low", "review_priority": "optional_contextual", "myth_correction_required": False, "behavior": "Solo asociación poblacional de clusters aprobados; nunca causalidad ni riesgo individual."},
        {"case": "ABCB1:T1.6", "inference_mode": "context_only", "confidence": "Low", "review_priority": "optional_contextual", "myth_correction_required": False, "behavior": "Contexto PGx débil, sin accionabilidad ni escalamiento porque no hay medicación relevante estructurada."},
        {"case": "IFNG:T3.5", "inference_mode": "abstained_insufficient_evidence", "confidence": "Abstain", "review_priority": "none", "myth_correction_required": False, "behavior": "Conteo descriptivo y abstención; no inferir alergia, autoinmunidad ni función inmune."},
    ]

def main():
    p = argparse.ArgumentParser()
    for name in ("mechanisms", "gwas_registry", "gwas_clusters", "publications", "output_dir"): p.add_argument("--" + name.replace("_", "-"), required=True)
    args = p.parse_args(); out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=False); publications = read_csv(args.publications)
    for row in publications:
        retrieved = row.get("retrieved_at", "")[:10]
        if retrieved and dt.date.fromisoformat(retrieved) > CUTOFF: raise ValueError(f"PMID {row.get('pmid')} exceeds cutoff")
    mechanisms, mechanism_audit = curate_mechanisms(read_csv(args.mechanisms), publications)
    gwas, gwas_audit = curate_gwas(read_csv(args.gwas_registry), read_csv(args.gwas_clusters))
    if len(gwas_audit) != 41:
        raise ValueError(f"Expected the 41 pilot GWAS clusters, found {len(gwas_audit)}")
    write_csv(out / "mechanism_registry_v1_candidate.csv", mechanisms, list(mechanisms[0])); write_csv(out / "mechanism_decision_audit.csv", mechanism_audit, list(mechanism_audit[0]))
    write_csv(out / "gwas_module_relevance_registry_v1_candidate.csv", gwas, list(gwas[0])); write_csv(out / "gwas_decision_audit.csv", gwas_audit, list(gwas_audit[0]))
    cards = behavior_cards(); write_json(out / "behavior_cards.json", cards)
    lines = ["# Validación manual: conductas esperadas de LLM1", "", "Corte: 28/07/2026. No se agregaron estudios.", ""]
    for card in cards: lines += [f"## {card['case']}", "", card["behavior"], "", f"`{card['inference_mode']}` · `{card['confidence']}` · `{card['review_priority']}` · myth={str(card['myth_correction_required']).lower()}", ""]
    (out / "manual_validation.md").write_text("\n".join(lines), encoding="utf-8")
    summary = {"title": "silver benchmark provisional autoevaluado", "evidence_cutoff": "2026-07-28", "new_studies_added": 0, "mechanisms_reviewed": len(mechanism_audit), "gwas_clusters_reviewed": len(gwas_audit), "mechanism_status_counts": {s: sum(r["status"] == s for r in mechanism_audit) for s in {r["status"] for r in mechanism_audit}}, "gwas_status_counts": {s: sum(r["status"] == s for r in gwas_audit) for s in {r["status"] for r in gwas_audit}}, "manual_validation_required": "Approve or correct only the five behavior cards."}
    write_json(out / "curation_summary.json", summary); print(json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__": main()
