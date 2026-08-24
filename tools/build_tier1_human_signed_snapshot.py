#!/usr/bin/env python3
"""Compile the human-signed 105-group Tier 1 sandbox snapshot.

This compiler is deliberately separate from registry publication.  It verifies
the signed review against the frozen Sol candidate and evidence packets, writes
immutable prototype artifacts, and proves that the active registry did not
change while it ran.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"
RESIGNED_GOLD = {
    "CYCS:T1.1", "CLOCK:T1.2", "IL6:T1.4", "COL14A1:T1.5", "ABCB1:T1.6",
}
PRKAA2 = "PRKAA2:T1.1"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json_immutable(path: Path, value: Any) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"Immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_csv_immutable(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty CSV: {path}")
    fields = list(rows[0])
    from io import StringIO
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    content = "\ufeff" + buffer.getvalue()
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"Immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_immutable(source: Path, target: Path) -> None:
    if target.exists():
        if sha256_file(source) != sha256_file(target):
            raise FileExistsError(f"Immutable signed source differs: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def parse_signature(rows: list[dict[str, str]]) -> dict[str, str]:
    signature = {str(row.get("campo") or "").strip(): str(row.get("valor") or "").strip() for row in rows}
    required = {
        "estado_revision": "APROBADO", "scope": "tier1_prototype_snapshot_candidate",
        "responsable": "Martina Liz Ceballos", "grupos_revisados": "105", "aprobados": "105",
        "initial_guide_candidate": "77", "context_only": "28", "arbitrajes_revisados": "12",
        "prkaa2_adjudicacion_humana": "Sí", "formal_validation_readiness": "pending_new_unseen_holdout",
        "production_ready": "No", "clinically_validated": "No",
    }
    failures = [f"signature:{key}" for key, expected in required.items() if signature.get(key) != expected]
    if failures:
        raise ValueError("Invalid signature controls: " + ", ".join(failures))
    if signature.get("fecha") != "23/08/2026":
        raise ValueError("Unexpected human review date")
    resigned = {item.strip() for item in signature.get("gold_resignados", "").split("|") if item.strip()}
    if resigned != RESIGNED_GOLD:
        raise ValueError("Re-signed Gold set is inconsistent")
    return signature


def evidence_ids_from_decision(decision: dict) -> list[str]:
    groups = decision.get("evidence_ids") or {}
    return sorted({str(item) for values in groups.values() for item in (values or []) if str(item).strip()})


def candidate_direction(candidate: dict) -> str:
    return str(candidate.get("dominant_direction") or (candidate.get("direction_assessment") or {}).get("dominant_direction") or "")


def candidate_material_conflict(candidate: dict) -> bool:
    if "material_conflict" in candidate:
        return bool(candidate["material_conflict"])
    return bool((candidate.get("direction_assessment") or {}).get("material_conflict"))


def validate_review(candidate: dict, review: dict) -> list[str]:
    errors: list[str] = []
    expected = {
        "estado": candidate.get("core_status"),
        "techo_inferencia": candidate.get("inference_ceiling"),
        "dirección": candidate_direction(candidate),
        "conflicto_material": "Sí" if candidate_material_conflict(candidate) else "No",
        "packet_sha256": candidate.get("packet_sha256"),
    }
    for field, value in expected.items():
        if str(review.get(field) or "").strip() != str(value or "").strip():
            errors.append(f"{candidate.get('group_id')}:{field}_mismatch")
    if review.get("decisión_revisor") != "APROBADO":
        errors.append(f"{candidate.get('group_id')}:not_human_approved")
    if review.get("responsable") != "Martina Liz Ceballos" or review.get("fecha") != "23/08/2026":
        errors.append(f"{candidate.get('group_id')}:signature_identity_or_date")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--gold-snapshot", type=Path, required=True)
    parser.add_argument("--active-registry", type=Path, required=True)
    parser.add_argument("--normalizer", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, action="append", default=[])
    parser.add_argument("--schema", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    inputs = [args.revision, args.signature, args.workbook, args.candidate, args.evidence_manifest,
              args.gold_snapshot, args.active_registry, args.normalizer, *args.prompt, *args.schema]
    missing = [str(path) for path in inputs if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing inputs: " + ", ".join(missing))
    registry_before = sha256_file(args.active_registry)
    if registry_before != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("Active registry hash is not the protected baseline")

    signature = parse_signature(read_csv(args.signature))
    reviews = read_csv(args.revision)
    if len(reviews) != 105:
        raise ValueError("Human review must contain exactly 105 rows")
    review_by_group = {row["grupo"]: row for row in reviews}
    if len(review_by_group) != 105:
        raise ValueError("Duplicate group in human review")

    candidates = read_jsonl(args.candidate)
    candidate_by_group = {row["group_id"]: row for row in candidates}
    if len(candidates) != 105 or len(candidate_by_group) != 105 or set(candidate_by_group) != set(review_by_group):
        raise ValueError("Candidate and signed review must contain the same 105 unique groups")
    errors = [error for group_id in sorted(candidate_by_group)
              for error in validate_review(candidate_by_group[group_id], review_by_group[group_id])]
    if errors:
        raise RuntimeError("Signed review reconciliation failed:\n" + "\n".join(errors))

    evidence_manifest = read_json(args.evidence_manifest)
    manifest_rows = {row["group_id"]: row for row in evidence_manifest.get("packets") or []}
    if set(manifest_rows) != set(candidate_by_group):
        raise ValueError("Evidence manifest is not closed over the 105 signed groups")
    gold_snapshot = read_json(args.gold_snapshot)
    gold_by_group = {row["group_id"]: row for row in gold_snapshot.get("groups") or []}
    if len(gold_by_group) != 12:
        raise ValueError("Gold prototype source must contain exactly 12 groups")

    registry_rows = read_csv(args.active_registry)
    canonical = sorted({f"{row['gene']}:{row['module_id']}" for row in registry_rows})
    if len(canonical) != 180 or not set(candidate_by_group).issubset(canonical):
        raise ValueError("Canonical registry does not contain the expected 180 groups")

    groups: list[dict] = []
    provenance_counts: Counter[str] = Counter()
    arbitration_count = 0
    for group_id in sorted(candidate_by_group):
        candidate = candidate_by_group[group_id]
        review = review_by_group[group_id]
        manifest_row = manifest_rows[group_id]
        packet_path = Path(manifest_row["packet_path"])
        if not packet_path.exists() or sha256_file(packet_path) != manifest_row["file_sha256"]:
            raise RuntimeError(f"Packet file hash mismatch: {group_id}")
        packet = read_json(packet_path)
        if packet.get("packet_sha256") != candidate.get("packet_sha256"):
            raise RuntimeError(f"Packet scientific hash mismatch: {group_id}")
        ledger = {row["evidence_id"]: row for row in packet.get("source_ledger") or []}

        if group_id in gold_by_group:
            valid_ids = sorted(set(gold_by_group[group_id]["valid_evidence_ids"]))
            invalid_ids = sorted(set(gold_by_group[group_id].get("invalid_evidence_ids") or []))
            decision_provenance = "human_resigned_after_prototype_replay" if group_id in RESIGNED_GOLD else "signed_gold_unchanged"
            allowlist_provenance = "signed_gold"
        else:
            valid_ids = evidence_ids_from_decision(candidate)
            invalid_ids = []
            decision_provenance = "human_adjudicated" if group_id == PRKAA2 else "packet_selected_human_reviewed"
            allowlist_provenance = "packet_selected_human_reviewed"
        if not valid_ids or set(valid_ids) - set(ledger) or set(valid_ids) & set(invalid_ids):
            raise RuntimeError(f"Invalid runtime evidence allowlist: {group_id}")

        evidence_records = [ledger[evidence_id] for evidence_id in valid_ids]
        arbitrated = str(review.get("arbitraje") or "").strip() == "Sí"
        arbitration_count += int(arbitrated)
        provenance_counts[decision_provenance] += 1
        groups.append({
            "group_id": group_id,
            "gene": packet["gene"]["symbol"],
            "module_id": packet["module"]["module_id"],
            "module": packet["module"],
            "core_status": candidate["core_status"],
            "context_usable": True,
            "prototype_inference_ceiling": candidate["inference_ceiling"],
            "dominant_direction": candidate_direction(candidate),
            "material_conflict": candidate_material_conflict(candidate),
            "valid_evidence_ids": valid_ids,
            "invalid_evidence_ids": invalid_ids,
            "evidence_records": evidence_records,
            "limitations": list(candidate.get("limitations") or []),
            "expert_review_basis": candidate.get("expert_review_basis") or {},
            "human_review": {
                "decision": "approved", "reviewer": review["responsable"],
                "reviewed_at": "2026-08-23", "comment": review.get("comentarios_revisor") or "",
                "arbitration_preserved": arbitrated,
            },
            "provenance": {
                "decision_signature": decision_provenance,
                "allowlist": allowlist_provenance,
                "packet_sha256": candidate["packet_sha256"],
                "packet_file_sha256": manifest_row["file_sha256"],
                "candidate_campaign": args.candidate.parent.name,
                "base_pair_consensus": False if group_id == PRKAA2 else not arbitrated,
            },
        })

    status_counts = Counter(row["core_status"] for row in groups)
    ceiling_counts = Counter(row["prototype_inference_ceiling"] for row in groups)
    expected_provenance = {
        "signed_gold_unchanged": 7,
        "human_resigned_after_prototype_replay": 5,
        "human_adjudicated": 1,
        "packet_selected_human_reviewed": 92,
    }
    if status_counts != Counter({"approved": 105}) or ceiling_counts != Counter({"initial_guide_candidate": 77, "context_only": 28}):
        raise RuntimeError("Signed snapshot counts do not match the approval")
    if dict(provenance_counts) != expected_provenance or arbitration_count != 12:
        raise RuntimeError("Provenance or arbitration counts are inconsistent")

    snapshot = {
        "schema_version": "tier1_prototype_snapshot_v1_human_signed",
        "snapshot_id": "tier1-105-human-signed-20260823",
        "created_at": "2026-08-23T00:00:00Z",
        "evidence_cutoff": "2026-07-28",
        "prototype_readiness": "approved_for_sandbox_smoke_test",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "human_signature": {
            "reviewer": signature["responsable"], "reviewed_at": "2026-08-23",
            "revision_csv_sha256": sha256_file(args.revision),
            "signature_csv_sha256": sha256_file(args.signature),
            "workbook_sha256": sha256_file(args.workbook),
        },
        "counts": {
            "groups": 105, "approved": 105, "initial_guide_candidate": 77,
            "context_only": 28, "arbitrations": 12, "provenance": expected_provenance,
        },
        "groups": groups,
    }
    # Exclude the wall-clock timestamp from the stable scientific identity.
    snapshot_identity = {key: value for key, value in snapshot.items() if key != "created_at"}
    snapshot["snapshot_sha256"] = sha256_json(snapshot_identity)

    covered = set(candidate_by_group)
    coverage_rows = [{
        "group_id": group_id,
        "coverage_status": "covered_by_prototype_snapshot" if group_id in covered else "not_covered_by_prototype_snapshot",
        "llm_allowed": group_id in covered,
        "feeds_llm2": group_id in covered,
    } for group_id in canonical]
    coverage = {
        "schema_version": "prototype_coverage_manifest_v1",
        "canonical_group_count": 180, "covered_count": 105, "not_covered_count": 75,
        "groups": coverage_rows,
    }
    coverage["manifest_sha256"] = sha256_json(coverage)

    manifest = {
        "schema_version": "tier1_prototype_candidate_manifest_v1_human_signed",
        "snapshot_id": snapshot["snapshot_id"],
        "prototype_readiness": "approved_for_sandbox_smoke_test",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "scientific_group_count": 105,
        "canonical_coverage_group_count": 180,
        "holdout_status": "failed_and_sealed_not_unseen",
        "hashes": {
            "snapshot": snapshot["snapshot_sha256"],
            "coverage": coverage["manifest_sha256"],
            "candidate_jsonl": sha256_file(args.candidate),
            "evidence_manifest": sha256_file(args.evidence_manifest),
            "revision_csv": sha256_file(args.revision),
            "signature_csv": sha256_file(args.signature),
            "signed_workbook": sha256_file(args.workbook),
            "normalizer": sha256_file(args.normalizer),
            "prompts": {path.name: sha256_file(path) for path in args.prompt},
            "schemas": {path.name: sha256_file(path) for path in args.schema},
            "packets": {row["group_id"]: row["provenance"]["packet_sha256"] for row in groups},
            "active_registry_before": registry_before,
        },
        "gates": {
            "groups_exact": True, "no_duplicates": True, "decisions_valid": True,
            "signature_reconciled": True, "prkaa2_human_adjudicated": True,
            "resigned_gold_preserved": True, "critical_errors": [], "scientific_quarantines": 0,
        },
    }
    registry_after = sha256_file(args.active_registry)
    if registry_after != registry_before:
        raise RuntimeError("Active registry changed while compiling the signed snapshot")
    manifest["hashes"]["active_registry_after"] = registry_after
    manifest["manifest_sha256"] = sha256_json(manifest)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json_immutable(args.output_dir / "tier1_prototype_snapshot_v1_human_signed.json", snapshot)
    write_json_immutable(args.output_dir / "prototype_coverage_manifest_v1.json", coverage)
    write_json_immutable(args.output_dir / "prototype_candidate_manifest.json", manifest)
    write_csv_immutable(args.output_dir / "tier1_prototype_snapshot_groups.csv", [{
        "group_id": row["group_id"], "gene": row["gene"], "module_id": row["module_id"],
        "core_status": row["core_status"], "inference_ceiling": row["prototype_inference_ceiling"],
        "dominant_direction": row["dominant_direction"], "material_conflict": row["material_conflict"],
        "arbitration_preserved": row["human_review"]["arbitration_preserved"],
        "decision_signature_provenance": row["provenance"]["decision_signature"],
        "allowlist_provenance": row["provenance"]["allowlist"],
        "valid_evidence_count": len(row["valid_evidence_ids"]),
        "packet_sha256": row["provenance"]["packet_sha256"],
        "reviewer": row["human_review"]["reviewer"], "reviewed_at": row["human_review"]["reviewed_at"],
    } for row in groups])
    write_csv_immutable(args.output_dir / "prototype_coverage_manifest_v1.csv", coverage_rows)
    signed_dir = args.output_dir / "signed_sources"
    copy_immutable(args.revision, signed_dir / args.revision.name)
    copy_immutable(args.signature, signed_dir / args.signature.name)
    copy_immutable(args.workbook, signed_dir / args.workbook.name)

    print(json.dumps({
        "status": "approved_for_sandbox_smoke_test", "groups": 105,
        "snapshot_sha256": snapshot["snapshot_sha256"], "manifest_sha256": manifest["manifest_sha256"],
        "active_registry_sha256": registry_after, "output_dir": str(args.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
