#!/usr/bin/env python3
"""Compile the human Tier 1 review package into an immutable, non-active foundation.

The artefacts created here are deliberately not accepted by the LLM1 runtime.
They preserve the review evidence, expose every unresolved reconciliation issue,
and provide a single source of truth for the later signed-gold/import workflow.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402


CUTOFF = dt.date(2026, 7, 28)
GOLD_GROUPS = (
    "MTHFR:T1.1", "CYCS:T1.1", "CLOCK:T1.2", "ASMT:T1.2",
    "PEMT:T1.3", "FADS1:T1.3", "IL6:T1.4", "IL10:T1.4",
    "RUNX2:T1.5", "COL14A1:T1.5", "ABCB1:T1.6", "GCLM:T1.6",
)
ID_PATTERN = re.compile(r"(?:PMID:\d+|UNIPROT:[A-Z0-9]+|REACTOME:[A-Z0-9]+|NCBI_GENE:\d+)")


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"sí", "si", "yes", "true", "1"}


def identifiers(value: str) -> list[str]:
    return sorted(set(ID_PATTERN.findall(value or "")))


def date_after_cutoff(value: str) -> bool:
    try:
        return dt.date.fromisoformat((value or "")[:10]) > CUTOFF
    except ValueError:
        return False


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def first_acceptable_status(value: str) -> str:
    status = (value or "").split("|")[0].strip()
    if status not in {"approved", "approved_with_conflict", "withheld", "rejected"}:
        raise ValueError(f"Unsupported proposed status: {status or '<empty>'}")
    return status


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def group_review(proposal: dict, ledger_rows: list[dict], selected_rows: list[dict], base_rows: list[dict]) -> dict:
    group_id = proposal["Grupo"]
    gene = proposal["Gen"]
    ledger_by_id = {row["ID de evidencia"]: row for row in ledger_rows}
    current_selected = {row["ID de evidencia"] for row in selected_rows}
    valid_ids = identifiers(proposal.get("Fuentes válidas", ""))
    invalid_ids = identifiers(proposal.get("Fuentes inválidas", ""))
    technical_flags: list[str] = []
    missing_valid = sorted(set(valid_ids) - set(ledger_by_id))
    invalid_still_selected = sorted(set(invalid_ids) & current_selected)
    after_cutoff_selected = sorted(
        row["ID de evidencia"] for row in selected_rows if date_after_cutoff(row.get("Fecha publicación", ""))
    )
    titles: dict[str, list[str]] = defaultdict(list)
    for row in selected_rows:
        title = normalize_title(row.get("Título", ""))
        if title:
            titles[title].append(row["ID de evidencia"])
    duplicate_lineages = sorted(sorted(ids) for ids in titles.values() if len(ids) > 1)
    expected_ncbi = next((identifier for identifier in valid_ids if identifier.startswith("NCBI_GENE:")), "")
    selected_ncbi = next((identifier for identifier in current_selected if identifier.startswith("NCBI_GENE:")), "")
    if missing_valid:
        technical_flags.append("manual_valid_evidence_missing_from_ledger")
    if invalid_still_selected:
        technical_flags.append("manual_invalid_evidence_still_selected")
    if after_cutoff_selected:
        technical_flags.append("selected_evidence_after_cutoff")
    if duplicate_lineages:
        technical_flags.append("selected_publication_lineage_not_deduplicated")
    if expected_ncbi and selected_ncbi and expected_ncbi != selected_ncbi:
        technical_flags.append("authoritative_identity_mismatch")
    if len(selected_rows) != 20:
        technical_flags.append("selected_evidence_count_not_20")
    if len(ledger_rows) != int(proposal["Fuentes en ledger"]):
        technical_flags.append("ledger_count_mismatch")
    if not any(row.get("Base de datos") == "NCBI_Gene" and row.get("Estado") == "available" for row in base_rows):
        technical_flags.append("authoritative_ncbi_snapshot_unavailable")
    review_state = proposal.get("Revisor", "").strip()
    if "firma humana final" in review_state.lower() or not review_state:
        technical_flags.append("human_signature_pending")
    proposed_ids = [identifier for identifier in valid_ids if identifier in ledger_by_id]
    return {
        "group_id": group_id,
        "gene": gene,
        "module_id": group_id.split(":", 1)[1],
        "split": proposal["Segmento"],
        "scientific_status_proposed": first_acceptable_status(proposal["Estados aceptables"]),
        "scientific_status_alternatives": [item.strip() for item in proposal["Estados aceptables"].split("|") if item.strip()],
        "context_usable_proposed": truthy(proposal["¿Contexto utilizable?"]),
        "inference_ceiling_proposed": proposal["Techo de inferencia"].strip(),
        "acceptable_directions_proposed": ["supports_relation"],
        "required_limitations": proposal["Limitaciones requeridas"].strip(),
        "reviewer_state": review_state,
        "review_date": proposal["Fecha revisión"].strip(),
        "review_notes": proposal["Notas del revisor"].strip(),
        "original_packet_sha256": proposal["Hash del paquete"].strip(),
        "allowed_evidence_ids_proposed": valid_ids,
        "invalid_evidence_ids_proposed": invalid_ids,
        "reconciled_existing_evidence_ids": proposed_ids,
        "technical_flags": sorted(set(technical_flags)),
        "reconciliation": {
            "missing_valid_evidence_ids": missing_valid,
            "invalid_evidence_ids_currently_selected": invalid_still_selected,
            "selected_after_cutoff": after_cutoff_selected,
            "duplicate_selected_title_lineages": duplicate_lineages,
            "expected_ncbi_evidence_id": expected_ncbi,
            "currently_selected_ncbi_evidence_id": selected_ncbi,
            "selected_count": len(selected_rows),
            "ledger_count": len(ledger_rows),
        },
        # A proposal is never released by this compiler. It must be reconciled
        # against refreshed primary records and receive final human sign-off.
        "release_ready": False,
    }


def packet_map(evidence_manifest_path: Path) -> dict[str, dict]:
    manifest = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    unhashed = dict(manifest)
    declared_hash = unhashed.pop("manifest_sha256", "")
    if not declared_hash or cv2.sha256_json(unhashed) != declared_hash:
        raise ValueError("Evidence manifest hash mismatch")
    packets: dict[str, dict] = {}
    for row in manifest.get("packets") or []:
        path = Path(row["packet_path"])
        packet = json.loads(path.read_text(encoding="utf-8"))
        if row.get("packet_sha256") != packet.get("packet_sha256"):
            raise ValueError(f"Evidence manifest packet hash mismatch: {row.get('group_id')}")
        packets[row["group_id"]] = packet
    return packets


def reconciled_group_review(proposal: dict, packet: dict, previous: dict | None = None) -> dict:
    group_id = proposal["Grupo"]
    valid_ids = identifiers(proposal.get("Fuentes válidas", ""))
    invalid_ids = identifiers(proposal.get("Fuentes inválidas", ""))
    ledger = {row["evidence_id"]: row for row in packet.get("source_ledger") or []}
    selected = set(packet.get("selected_evidence_ids") or [])
    policy = packet.get("human_review_policy") or {}
    technical_flags = [f"packet_invalid:{error}" for error in cv2.validate_packet(packet)]
    if packet.get("group_id") != group_id or packet.get("gene", {}).get("symbol") != proposal["Gen"]:
        technical_flags.append("packet_identity_mismatch")
    if set(policy.get("valid_evidence_ids") or []) != set(valid_ids):
        technical_flags.append("review_valid_policy_mismatch")
    if set(policy.get("invalid_evidence_ids") or []) != set(invalid_ids):
        technical_flags.append("review_invalid_policy_mismatch")
    if set(valid_ids) - set(ledger):
        technical_flags.append("manual_valid_evidence_missing_from_ledger")
    if set(valid_ids) - selected:
        technical_flags.append("manual_valid_evidence_not_selected")
    if set(invalid_ids) & selected:
        technical_flags.append("manual_invalid_evidence_still_selected")
    expected_ncbi = next((item for item in valid_ids if item.startswith("NCBI_GENE:")), "")
    selected_ncbi = next((item for item in selected if item.startswith("NCBI_GENE:")), "")
    if expected_ncbi and expected_ncbi != selected_ncbi:
        technical_flags.append("authoritative_identity_mismatch")
    lineages: dict[str, list[str]] = defaultdict(list)
    for evidence_id in selected:
        source = ledger.get(evidence_id) or {}
        if source.get("source_kind") == "primary_publication":
            lineages[cv2.selection_lineage_key(source)].append(evidence_id)
    duplicate_lineages = sorted(sorted(items) for items in lineages.values() if len(items) > 1)
    if duplicate_lineages:
        technical_flags.append("selected_publication_lineage_not_deduplicated")
    review_state = proposal.get("Revisor", "").strip()
    if "firma humana final" in review_state.lower() or not review_state:
        technical_flags.append("human_signature_pending")
    previous = previous or {}
    previous_valid = set(previous.get("allowed_evidence_ids_proposed") or [])
    previous_invalid = set(previous.get("invalid_evidence_ids_proposed") or [])
    return {
        "group_id": group_id,
        "gene": proposal["Gen"],
        "module_id": group_id.split(":", 1)[1],
        "split": proposal["Segmento"],
        "scientific_status_proposed": first_acceptable_status(proposal["Estados aceptables"]),
        "scientific_status_alternatives": [item.strip() for item in proposal["Estados aceptables"].split("|") if item.strip()],
        "context_usable_proposed": truthy(proposal["¿Contexto utilizable?"]),
        "inference_ceiling_proposed": proposal["Techo de inferencia"].strip(),
        "required_limitations": proposal["Limitaciones requeridas"].strip(),
        "reviewer_state": review_state,
        "review_date": proposal["Fecha revisión"].strip(),
        "review_notes": proposal["Notas del revisor"].strip(),
        "original_packet_sha256": proposal["Hash del paquete"].strip(),
        "reconciled_packet_sha256": packet.get("packet_sha256", ""),
        "allowed_evidence_ids_proposed": valid_ids,
        "invalid_evidence_ids_proposed": invalid_ids,
        "reconciled_existing_evidence_ids": sorted(set(valid_ids) & set(ledger)),
        "technical_flags": sorted(set(technical_flags)),
        "reconciliation": {
            "missing_valid_evidence_ids": sorted(set(valid_ids) - set(ledger)),
            "valid_evidence_ids_not_selected": sorted(set(valid_ids) - selected),
            "invalid_evidence_ids_currently_selected": sorted(set(invalid_ids) & selected),
            "duplicate_selected_title_lineages": duplicate_lineages,
            "expected_ncbi_evidence_id": expected_ncbi,
            "currently_selected_ncbi_evidence_id": selected_ncbi,
            "selected_count": len(selected),
            "ledger_count": len(ledger),
            "added_valid_ids_since_foundation_v1": sorted(set(valid_ids) - previous_valid),
            "removed_valid_ids_since_foundation_v1": sorted(previous_valid - set(valid_ids)),
            "added_invalid_ids_since_foundation_v1": sorted(set(invalid_ids) - previous_invalid),
        },
        "release_ready": False,
    }


def build_reconciled_foundation(
    proposal_csv: Path, evidence_manifest: Path, previous_foundation: Path | None = None,
) -> tuple[dict, list[dict], dict]:
    proposals = read_csv(proposal_csv)
    if {row["Grupo"] for row in proposals} != set(GOLD_GROUPS) or len(proposals) != len(GOLD_GROUPS):
        raise ValueError("Completed proposal must contain exactly the 12 frozen gold groups.")
    packets = packet_map(evidence_manifest)
    if set(packets) != set(GOLD_GROUPS):
        raise ValueError("Reconciled evidence manifest must contain exactly the 12 frozen gold groups.")
    previous_groups = {}
    if previous_foundation:
        previous_groups = json.loads(previous_foundation.read_text(encoding="utf-8")).get("groups") or {}
    groups = {
        proposal["Grupo"]: reconciled_group_review(
            proposal, packets[proposal["Grupo"]], previous_groups.get(proposal["Grupo"]),
        )
        for proposal in proposals
    }
    source_manifest = {
        "schema_version": "tier1_human_review_source_manifest_v2",
        "evidence_cutoff": CUTOFF.isoformat(),
        "files": [
            {"name": proposal_csv.name, "sha256": sha256_file(proposal_csv)},
            {"name": evidence_manifest.name, "sha256": sha256_file(evidence_manifest)},
        ],
        "packet_hashes": {group_id: packets[group_id]["packet_sha256"] for group_id in GOLD_GROUPS},
    }
    if previous_foundation:
        source_manifest["previous_foundation"] = {
            "path": str(previous_foundation), "sha256": sha256_file(previous_foundation),
        }
    source_manifest["manifest_sha256"] = sha256_json(source_manifest)
    rows = [groups[group_id] for group_id in GOLD_GROUPS]
    non_signature_flags = {
        group_id: [flag for flag in row["technical_flags"] if flag != "human_signature_pending"]
        for group_id, row in groups.items()
    }
    technical_blocking = sorted(group_id for group_id, flags in non_signature_flags.items() if flags)
    signature_pending = sorted(
        group_id for group_id, row in groups.items() if "human_signature_pending" in row["technical_flags"]
    )
    snapshot = {
        "schema_version": "tier1_human_review_foundation_v2",
        "created_at": now(), "evidence_cutoff": CUTOFF.isoformat(),
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "gold_status": "proposal_reconciled_pending_human_signature" if not technical_blocking else "proposal_reconciliation_blocked",
        "active_registry_modified": False, "release_ready": False, "groups": groups,
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    report = {
        "schema_version": "tier1_human_review_validation_v2", "created_at": now(),
        "gold_group_count": len(groups), "release_ready": False,
        "ready_for_human_signature": not technical_blocking,
        "technical_blocking_groups": technical_blocking,
        "signature_pending_groups": signature_pending,
        "technical_flag_counts": dict(Counter(flag for row in rows for flag in row["technical_flags"])),
        "next_required_action": (
            "Obtain explicit final human signature for all 12 groups; do not calibrate before a signed gold is compiled."
            if not technical_blocking else
            "Resolve the remaining packet reconciliation flags before requesting a human signature."
        ),
    }
    return snapshot, rows, {"source_manifest": source_manifest, "report": report}


def build_foundation(package_dir: Path, proposal_csv: Path) -> tuple[dict, list[dict], dict]:
    required = {
        "fichas_gold_humanas.csv", "fuentes_seleccionadas.csv", "ledger_completo.csv",
        "control_evidencia.csv", "bases_autoritativas.csv",
    }
    missing = sorted(name for name in required if not (package_dir / name).exists())
    if missing:
        raise FileNotFoundError(f"Human review package is missing: {', '.join(missing)}")
    proposals = read_csv(proposal_csv)
    if {row["Grupo"] for row in proposals} != set(GOLD_GROUPS) or len(proposals) != len(GOLD_GROUPS):
        raise ValueError("Completed proposal must contain exactly the 12 frozen gold groups.")
    base_cards = read_csv(package_dir / "fichas_gold_humanas.csv")
    if any(row.get("Estado del gold") != "pending" for row in base_cards):
        raise ValueError("Base human package must remain pending; it is evidence, not a signed gold release.")
    controls = {row["Grupo"]: row for row in read_csv(package_dir / "control_evidencia.csv")}
    selected_by_group: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(package_dir / "fuentes_seleccionadas.csv"):
        selected_by_group[row["Grupo"]].append(row)
    ledger_by_group: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(package_dir / "ledger_completo.csv"):
        ledger_by_group[row["Grupo"]].append(row)
    bases_by_group: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(package_dir / "bases_autoritativas.csv"):
        bases_by_group[row["Grupo"]].append(row)
    groups = {}
    for proposal in sorted(proposals, key=lambda row: GOLD_GROUPS.index(row["Grupo"])):
        group_id = proposal["Grupo"]
        control = controls.get(group_id)
        if not control or not truthy(control.get("Paquete completo")):
            raise ValueError(f"Control evidence packet is not complete for {group_id}")
        if int(control["Fuentes seleccionadas"]) != len(selected_by_group[group_id]):
            raise ValueError(f"Selected-source control mismatch for {group_id}")
        if int(control["Fuentes en ledger"]) != len(ledger_by_group[group_id]):
            raise ValueError(f"Ledger control mismatch for {group_id}")
        groups[group_id] = group_review(proposal, ledger_by_group[group_id], selected_by_group[group_id], bases_by_group[group_id])
    source_files = sorted(required | {proposal_csv.name})
    source_manifest = {
        "schema_version": "tier1_human_review_source_manifest_v1",
        "evidence_cutoff": CUTOFF.isoformat(),
        "files": [
            {"name": name, "sha256": sha256_file(proposal_csv if name == proposal_csv.name else package_dir / name)}
            for name in source_files
        ],
    }
    source_manifest["manifest_sha256"] = sha256_json(source_manifest)
    snapshot = {
        "schema_version": "tier1_human_review_foundation_v1",
        "created_at": now(),
        "evidence_cutoff": CUTOFF.isoformat(),
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "gold_status": "proposal_pending_human_signature_and_packet_reconciliation",
        "active_registry_modified": False,
        "release_ready": False,
        "groups": groups,
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    report = {
        "schema_version": "tier1_human_review_validation_v1",
        "created_at": now(),
        "gold_group_count": len(groups),
        "release_ready": False,
        "blocking_groups": sorted(group_id for group_id, row in groups.items() if row["technical_flags"]),
        "technical_flag_counts": dict(Counter(flag for row in groups.values() for flag in row["technical_flags"])),
        "next_required_action": "Refresh/reconcile source records, then obtain final human signature before any runtime import or LLM calibration.",
    }
    return snapshot, [groups[group_id] for group_id in GOLD_GROUPS], {"source_manifest": source_manifest, "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable, non-active Tier 1 human-review foundation artifacts.")
    parser.add_argument("--package-dir")
    parser.add_argument("--evidence-manifest")
    parser.add_argument("--previous-foundation")
    parser.add_argument("--proposal-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite immutable foundation directory: {output}")
    if bool(args.package_dir) == bool(args.evidence_manifest):
        raise ValueError("Choose exactly one source: --package-dir or --evidence-manifest")
    if args.evidence_manifest:
        snapshot, rows, metadata = build_reconciled_foundation(
            Path(args.proposal_csv).resolve(), Path(args.evidence_manifest).resolve(),
            Path(args.previous_foundation).resolve() if args.previous_foundation else None,
        )
    else:
        snapshot, rows, metadata = build_foundation(Path(args.package_dir).resolve(), Path(args.proposal_csv).resolve())
    write_json(output / "source_manifest.json", metadata["source_manifest"])
    write_json(output / "tier1_human_review_foundation.json", snapshot)
    write_json(output / "validation_report.json", metadata["report"])
    write_csv(output / "runtime_import_preview.csv", [
        {
            "mechanism_registry_version": "mechanism_registry_human_review_preview_v1",
            "gene": row["gene"], "module_id": row["module_id"],
            "curation_status": row["scientific_status_proposed"],
            "source_ids_or_urls": " | ".join(row["allowed_evidence_ids_proposed"]),
            "reviewer": row["reviewer_state"], "reviewed_at": row["review_date"],
            "review_notes": row["review_notes"],
            "technical_flags": " | ".join(row["technical_flags"]),
            "release_ready": "false",
        }
        for row in rows
    ], [
        "mechanism_registry_version", "gene", "module_id", "curation_status", "source_ids_or_urls",
        "reviewer", "reviewed_at", "review_notes", "technical_flags", "release_ready",
    ])
    write_csv(output / "human_signoff_template.csv", [
        {
            "Grupo": row["group_id"], "Gen": row["gene"], "Segmento": row["split"],
            "Estado propuesto": row["scientific_status_proposed"],
            "Estados aceptables": " | ".join(row["scientific_status_alternatives"]),
            "Contexto utilizable": "Sí" if row["context_usable_proposed"] else "No",
            "Techo de inferencia": row["inference_ceiling_proposed"],
            "Direcciones aceptables": " | ".join(row.get("acceptable_directions_proposed") or ["supports_relation"]),
            "Dirección y conflictos": row["review_notes"],
            "Limitaciones": row["required_limitations"],
            "Fuentes incluidas": " | ".join(row["allowed_evidence_ids_proposed"]),
            "Fuentes excluidas": " | ".join(row["invalid_evidence_ids_proposed"]),
            "Bloqueos técnicos": " | ".join(flag for flag in row["technical_flags"] if flag != "human_signature_pending"),
            "Aprobación final": "PENDIENTE", "Revisor final": "", "Fecha firma": "", "Correcciones": "",
            "Hash paquete reconciliado": row.get("reconciled_packet_sha256") or row.get("original_packet_sha256"),
        }
        for row in rows
    ], [
        "Grupo", "Gen", "Segmento", "Estado propuesto", "Estados aceptables", "Contexto utilizable",
        "Techo de inferencia", "Direcciones aceptables", "Dirección y conflictos", "Limitaciones", "Fuentes incluidas", "Fuentes excluidas",
        "Bloqueos técnicos", "Aprobación final", "Revisor final", "Fecha firma", "Correcciones",
        "Hash paquete reconciliado",
    ])
    print(json.dumps(metadata["report"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
