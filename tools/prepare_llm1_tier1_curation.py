#!/usr/bin/env python3
"""Prepare, but never activate, the frozen Tier 1 internal curation candidate."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path


CUTOFF = "2026-07-28"
FINAL_MECHANISM_STATUSES = {"approved", "approved_with_conflict", "withheld", "rejected"}
FINAL_GWAS_STATUSES = {"approved", "valid_but_excluded", "rejected"}


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def integer(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def prepare_mechanisms(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    candidate, audit = [], []
    for source in rows:
        row = dict(source)
        group_id = f"{row.get('gene', '')}:{row.get('module_id', '')}"
        if not str(row.get("module_id", "")).startswith("T1."):
            candidate.append(row)
            continue
        previous = row.get("curation_status", "draft") or "draft"
        # A draft is an unevaluated state, not evidence of insufficiency. The old
        # draft->withheld fallback created a misleading 99% withheld snapshot.
        # Leave it untouched so the command fails closed below and direct users to
        # the evidence-backed v2 recuration workflow.
        audit.append({
            "group_id": group_id,
            "previous_status": previous,
            "candidate_status": row.get("curation_status", "draft"),
            "decision": "preserved_reviewed_decision" if previous in FINAL_MECHANISM_STATUSES else "blocked_draft_requires_mechanism_curation_v2",
            "evidence_cutoff": CUTOFF,
            "requires_internal_approval": "true",
        })
        candidate.append(row)
    return candidate, audit


def prepare_gwas(rows: list[dict], clusters: list[dict]) -> tuple[list[dict], list[dict]]:
    cluster_index = {
        (row.get("approved_symbol", ""), row.get("module_id", ""), row.get("trait_id", "")): row
        for row in clusters
    }
    candidate, audit = [], []
    for source in rows:
        row = dict(source)
        gene = row.get("gene") or row.get("approved_symbol", "")
        module_id = row.get("module_id", "")
        trait_id = row.get("trait_id", "")
        if not module_id.startswith("T1."):
            candidate.append(row)
            continue
        previous = row.get("relevance_status", "unreviewed") or "unreviewed"
        cluster = cluster_index.get((gene, module_id, trait_id), {})
        allele_count = integer(cluster.get("allele_confirmed_significant_count"))
        publications = integer(cluster.get("independent_publication_count"))
        direction = cluster.get("direction_status", "unknown") or "unknown"
        if previous in FINAL_GWAS_STATUSES:
            status = previous
            reason = row.get("relevance_reason", "")
            decision = "preserved_reviewed_decision"
        elif allele_count >= 2 and publications >= 2 and direction != "conflicting":
            status = "valid_but_excluded"
            reason = (
                "The frozen cluster passes exact-allele replication, but module relevance was not explicitly "
                "approved; it remains excluded from LLM1 pending internal adjudication."
            )
            decision = "replicated_but_module_relevance_not_approved"
        else:
            status = "rejected"
            reason = "The frozen exact-allele, multiple-publication threshold was not met."
            decision = "replication_threshold_not_met"
        row.update({
            "relevance_status": status,
            "relevance_reason": reason,
            "source_ids_or_urls": row.get("source_ids_or_urls") or cluster.get("publication_ids", ""),
            "reviewer": "Codex Tier 1 curation candidate",
            "reviewed_at": CUTOFF,
            "review_notes": (
                f"candidate_only; allele_confirmed={allele_count}; independent_publications={publications}; "
                f"direction={direction}; internal approval required"
            ),
        })
        audit.append({
            "gene": gene,
            "module_id": module_id,
            "trait_id": trait_id,
            "previous_status": previous,
            "candidate_status": status,
            "allele_confirmed": allele_count,
            "independent_publications": publications,
            "direction": direction,
            "decision": decision,
            "evidence_cutoff": CUTOFF,
            "requires_internal_approval": "true",
        })
        candidate.append(row)
    return candidate, audit


def registry_reconciliation(mechanisms: list[dict], payload_path: Path) -> list[dict]:
    registry = {f"{row.get('gene', '')}:{row.get('module_id', '')}" for row in mechanisms}
    payloads = {
        json.loads(line)["group_id"]
        for line in payload_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    rows = []
    for group_id in sorted(payloads - registry):
        rows.append({"group_id": group_id, "mismatch": "observed_payload_not_in_mechanism_registry", "decision_required": "add_map_or_exclude_canon_group"})
    for group_id in sorted(registry - payloads):
        rows.append({"group_id": group_id, "mismatch": "mechanism_registry_group_not_observed_in_payload", "decision_required": "retain_as_sparse_absence_or_reconcile_symbol"})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mechanisms", required=True)
    parser.add_argument("--gwas-registry", required=True)
    parser.add_argument("--gwas-clusters", required=True)
    parser.add_argument("--payload-v6", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Candidate output already exists: {output}")
    output.mkdir(parents=True)
    mechanisms_source = read_csv(args.mechanisms)
    gwas_source = read_csv(args.gwas_registry)
    mechanisms, mechanism_audit = prepare_mechanisms(mechanisms_source)
    gwas, gwas_audit = prepare_gwas(gwas_source, read_csv(args.gwas_clusters))
    reconciliation = registry_reconciliation(mechanisms_source, Path(args.payload_v6))
    tier1_mechanisms = [row for row in mechanisms if row.get("module_id", "").startswith("T1.")]
    if len(tier1_mechanisms) != 105:
        raise ValueError(f"Expected 105 Tier 1 groups, found {len(tier1_mechanisms)}")
    if any(row.get("curation_status") not in FINAL_MECHANISM_STATUSES for row in tier1_mechanisms):
        raise ValueError("Tier 1 mechanism candidate contains an unclassified group.")
    tier1_gwas = [row for row in gwas if row.get("module_id", "").startswith("T1.")]
    if any(row.get("relevance_status") not in FINAL_GWAS_STATUSES for row in tier1_gwas):
        raise ValueError("Tier 1 GWAS candidate contains an unclassified row.")
    write_csv(output / "mechanism_registry_v1_tier1_candidate.csv", mechanisms, list(mechanisms[0]))
    write_csv(output / "mechanism_tier1_decision_audit.csv", mechanism_audit, list(mechanism_audit[0]))
    write_csv(output / "gwas_module_relevance_registry_v1_tier1_candidate.csv", gwas, list(gwas[0]))
    write_csv(output / "gwas_tier1_decision_audit.csv", gwas_audit, list(gwas_audit[0]) if gwas_audit else ["gene", "module_id", "trait_id"])
    write_csv(output / "group_registry_reconciliation.csv", reconciliation, ["group_id", "mismatch", "decision_required"])
    summary = {
        "schema_version": "llm1_tier1_curation_candidate_v1",
        "candidate_only": True,
        "evidence_cutoff": CUTOFF,
        "new_studies_added": 0,
        "tier1_groups": len(tier1_mechanisms),
        "tier1_mechanism_status_counts": dict(Counter(row["curation_status"] for row in tier1_mechanisms)),
        "tier1_gwas_rows": len(tier1_gwas),
        "tier1_gwas_status_counts": dict(Counter(row["relevance_status"] for row in tier1_gwas)),
        "registry_mismatches": len(reconciliation),
        "activation_blocked": True,
        "activation_requirements": [
            "internal_ai_approval_of_complete_versioned_snapshot",
            "resolution_of_group_registry_reconciliation",
            "upload_through_authenticated_curation_flow",
        ],
    }
    (output / "curation_candidate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
