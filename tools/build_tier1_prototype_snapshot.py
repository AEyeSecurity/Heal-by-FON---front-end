#!/usr/bin/env python3
"""Build the sealed 12-group sandbox snapshot used by the HEAL prototype.

This command is deliberately local-only: it replays already stored Semantic V2
assessments and never calls a model or mutates the production registry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = APP_ROOT / "services" / "heal-tier1-curation-v2"
sys.path.insert(0, str(SERVICE_ROOT))
import curation_v2 as cv2  # noqa: E402


DEFAULT_RECON = Path(r"F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\reconciliation-v4")
DEFAULT_CAMPAIGN = DEFAULT_RECON / "semantic-v2-allowlist-conflict-verification-3"
DEFAULT_REGISTRY = Path(r"F:\Heal by FON\data\canon\curation\mechanism_registry_v1.csv")
EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"
DEFAULT_OUTPUT = Path(r"F:\Heal by FON\data\prototype\tier1-12-snapshot-prototype-v2")
CEILING_RANK = {"none": 0, "context_only": 1, "initial_guide_candidate": 2}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compatible_with_signed_ceiling(prototype_ceiling: str, accepted: list[str]) -> bool:
    """A sandbox ceiling may be more conservative, but never more permissive."""
    return bool(accepted) and CEILING_RANK[prototype_ceiling] <= max(CEILING_RANK[item] for item in accepted)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconciliation-root", type=Path, default=DEFAULT_RECON)
    parser.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--active-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    registry_before = sha256_file(args.active_registry)
    if registry_before != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError(f"Active registry hash changed: {registry_before}")
    terminal = read_json(args.campaign / "protocol_terminal_state.json")
    if terminal.get("status") != "holdout_failed_requires_new_unseen_holdout":
        raise RuntimeError("verification-3 is not sealed in its expected terminal state")

    gold_path = args.reconciliation_root / "gold-v4" / "gold_frozen.json"
    gold = read_json(gold_path)
    cards = {row["group_id"]: row for row in gold["cards"]}
    replay_rows: list[dict] = []
    snapshot_groups: list[dict] = []
    all_errors: list[str] = []
    for group_id in cv2.GOLD_GROUPS:
        stem = group_id.replace(":", "__")
        packet_path = args.reconciliation_root / "evidence" / "packets" / f"{stem}.json"
        packet = read_json(packet_path)
        card = cards[group_id]
        if packet.get("packet_sha256") != card.get("packet_sha256"):
            all_errors.append(f"{group_id}:packet_hash_not_signed")
        phase = "calibration" if group_id in cv2.CALIBRATION_GROUPS else "holdout"
        normalized_by_role: dict[str, dict] = {}
        for role in ("curator", "critic"):
            semantic_path = args.campaign / phase / "semantic" / f"{stem}__{role}.json"
            semantic = read_json(semantic_path)
            semantic_errors = cv2.validate_semantic_assessment_v2(semantic, packet)
            decision = cv2.normalize_semantic_assessment_v2(semantic, packet)
            decision_errors = cv2.validate_decision(decision, packet)
            role_errors = sorted(set(semantic_errors + decision_errors))
            all_errors.extend(f"{group_id}:{role}:{error}" for error in role_errors)
            normalized_by_role[role] = decision
            replay_rows.append({
                "group_id": group_id, "phase": phase, "role": role,
                "core_status": decision["core_status"],
                "inference_ceiling": decision["inference_ceiling"],
                "dominant_direction": decision["direction_assessment"]["dominant_direction"],
                "material_conflict": decision["direction_assessment"]["material_conflict"],
                "semantic_errors": semantic_errors, "decision_errors": decision_errors,
                "semantic_sha256": sha256_file(semantic_path),
            })

        curator = normalized_by_role["curator"]
        critic = normalized_by_role["critic"]
        if curator["core_status"] != critic["core_status"]:
            all_errors.append(f"{group_id}:prototype_core_status_disagreement")
        if curator["direction_assessment"]["dominant_direction"] != critic["direction_assessment"]["dominant_direction"]:
            all_errors.append(f"{group_id}:prototype_direction_disagreement")
        prototype_ceiling = min(
            (curator["inference_ceiling"], critic["inference_ceiling"]),
            key=lambda value: CEILING_RANK[value],
        )
        if not compatible_with_signed_ceiling(prototype_ceiling, card["acceptable_inference_ceilings"]):
            all_errors.append(f"{group_id}:prototype_ceiling_exceeds_signed_gold")
        if curator["core_status"] not in card["acceptable_core_statuses"]:
            all_errors.append(f"{group_id}:prototype_status_outside_signed_gold")
        if curator["direction_assessment"]["dominant_direction"] not in card["acceptable_directions"]:
            all_errors.append(f"{group_id}:prototype_direction_outside_signed_gold")

        valid_ids = set(card["valid_evidence_ids"])
        ledger = {row["evidence_id"]: row for row in packet["source_ledger"]}
        evidence_records = [ledger[evidence_id] for evidence_id in card["valid_evidence_ids"] if evidence_id in ledger]
        if {row["evidence_id"] for row in evidence_records} != valid_ids:
            all_errors.append(f"{group_id}:signed_evidence_missing_from_packet")
        limitations = list(dict.fromkeys(
            list(card.get("required_limitations") or [])
            + list(curator.get("limitations") or [])
            + list(critic.get("limitations") or [])
        ))
        snapshot_groups.append({
            "group_id": group_id,
            "gene": packet["gene"]["symbol"],
            "module_id": packet["module"]["module_id"],
            "module": packet["module"],
            "core_status": curator["core_status"],
            "context_usable": bool(curator["context_usable"]),
            "signed_inference_ceilings": card["acceptable_inference_ceilings"],
            "prototype_inference_ceiling": prototype_ceiling,
            "dominant_direction": curator["direction_assessment"]["dominant_direction"],
            "material_conflict": bool(curator["direction_assessment"]["material_conflict"]),
            "confidence_floor": min(float(curator["confidence"]), float(critic["confidence"])),
            "valid_evidence_ids": card["valid_evidence_ids"],
            "invalid_evidence_ids": card["invalid_evidence_ids"],
            "evidence_records": evidence_records,
            "limitations": limitations,
            "expert_review_basis": curator["expert_review_basis"],
            "signature": {
                "reviewer": card["reviewer"], "reviewed_at": card["reviewed_at"],
                "reconciliation": card["signature_reconciliation"],
                "source_csv_sha256": card["signature_source_csv_sha256"],
                "source_xlsx_sha256": card["signature_source_xlsx_sha256"],
            },
            "provenance": {
                "allowlist": "signed_gold", "packet_sha256": packet["packet_sha256"],
                "curator_semantic_sha256": replay_rows[-2]["semantic_sha256"],
                "critic_semantic_sha256": replay_rows[-1]["semantic_sha256"],
            },
        })

    col14 = next(row for row in snapshot_groups if row["group_id"] == "COL14A1:T1.5")
    if col14["prototype_inference_ceiling"] != "context_only":
        all_errors.append("COL14A1:T1.5:prototype_ceiling_not_context_only")
    if all_errors:
        raise RuntimeError("Prototype replay failed:\n" + "\n".join(sorted(set(all_errors))))

    with args.active_registry.open("r", encoding="utf-8-sig", newline="") as handle:
        registry_rows = list(csv.DictReader(handle))
    canonical_groups = sorted({f"{row['gene']}:{row['module_id']}" for row in registry_rows})
    if len(canonical_groups) != 180:
        raise RuntimeError(f"Expected 180 canonical registry keys, found {len(canonical_groups)}")
    covered = set(cv2.GOLD_GROUPS)
    coverage = [{
        "group_id": group_id,
        "coverage_status": "covered_by_prototype_snapshot" if group_id in covered else "not_covered_by_prototype_snapshot",
        "llm_allowed": group_id in covered,
        "feeds_llm2": group_id in covered,
    } for group_id in canonical_groups]

    normalizer_path = SERVICE_ROOT / "curation_v2.py"
    prompt_files = sorted((args.campaign / "frozen-prompt" / "prompts").glob("*.md"))
    schema_files = sorted((args.campaign / "frozen-prompt").glob("*.schema.json"))
    snapshot = {
        "schema_version": "prototype_curation_snapshot_v1",
        "snapshot_id": "tier1-signed-12-prototype-20260822",
        "evidence_cutoff": "2026-07-28",
        "prototype_readiness": "prototype_candidate",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "groups": snapshot_groups,
    }
    snapshot["snapshot_sha256"] = cv2.sha256_json(snapshot)
    coverage_doc = {
        "schema_version": "prototype_coverage_manifest_v1", "canonical_group_count": 180,
        "covered_count": 12, "not_covered_count": 168, "groups": coverage,
    }
    coverage_doc["manifest_sha256"] = cv2.sha256_json(coverage_doc)
    manifest = {
        "schema_version": "prototype_candidate_manifest_v1",
        "prototype_readiness": "prototype_candidate",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "formal_holdout_reference": {
            "campaign": args.campaign.name, "status": terminal["status"],
            "campaign_tree_sha256": tree_sha256(args.campaign), "reopened": False,
        },
        "scientific_group_count": 12, "canonical_coverage_group_count": 180,
        "normalizer_version": cv2.NORMALIZER_VERSION,
        "hashes": {
            "gold": sha256_file(gold_path), "active_registry_before": registry_before,
            "normalizer": sha256_file(normalizer_path),
            "prompts": {path.name: sha256_file(path) for path in prompt_files},
            "schemas": {path.name: sha256_file(path) for path in schema_files},
            "packets": {row["group_id"]: row["provenance"]["packet_sha256"] for row in snapshot_groups},
        },
        "replay": {
            "roles_valid": len(replay_rows), "groups_valid": len(snapshot_groups),
            "errors": [], "col14a1_context_only": True,
            "ceiling_policy": "prototype_may_be_more_conservative_than_signed_gold_but_never_more_permissive",
        },
    }
    registry_after = sha256_file(args.active_registry)
    if registry_after != registry_before:
        raise RuntimeError("Active registry changed while building the prototype snapshot")
    manifest["hashes"]["active_registry_after"] = registry_after
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "prototype_curation_snapshot_v1.json": snapshot,
        "prototype_coverage_manifest_v1.json": coverage_doc,
        "prototype_candidate_manifest.json": manifest,
        "prototype_replay_rows.json": replay_rows,
    }
    for name, value in targets.items():
        target = args.output_dir / name
        if target.exists() and read_json(target) != value:
            raise FileExistsError(f"Immutable prototype artifact differs: {target}")
        if not target.exists():
            write_json(target, value)
    print(json.dumps({
        "status": "prototype_candidate", "output_dir": str(args.output_dir),
        "groups": 12, "coverage": 180, "active_registry_sha256": registry_after,
        "manifest_sha256": manifest["manifest_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
