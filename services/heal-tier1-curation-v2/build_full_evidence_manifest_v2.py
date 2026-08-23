#!/usr/bin/env python3
"""Build the closed 12 signed + 93 packet-selected Tier 1 evidence manifest.

This command has no model, publication, registry activation, Luna, or VCF side
effects.  It is resumable at the individual unsigned packet level.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_evidence_packets as builder  # noqa: E402
import curation_v2 as cv2  # noqa: E402


def resolve_current_canon(current_path: Path, canon_root: Path) -> tuple[Path, Path, str]:
    current = cv2.read_json(current_path)
    run_id = str(current.get("runId") or "").strip()
    if not run_id:
        raise ValueError("current.json has no runId")
    run_dir = canon_root / "runs" / run_id
    clean = run_dir / "heal-canon-v2-clean-rows.csv"
    master = run_dir / "heal-canon-v2-gene-master.csv"
    if not clean.is_file() or not master.is_file():
        raise FileNotFoundError(f"Current canon artifacts are missing for run {run_id}")
    return clean, master, run_id


def cache_manifest(cache_dir: Path) -> dict:
    rows = [
        {"path": str(path.relative_to(cache_dir)).replace("\\", "/"),
         "size": path.stat().st_size, "sha256": cv2.sha256_file(path)}
        for path in sorted(cache_dir.rglob("*")) if path.is_file()
    ] if cache_dir.exists() else []
    body = {"schema_version": "tier1_retrieval_cache_snapshot_v1", "files": rows}
    body["snapshot_sha256"] = cv2.sha256_json(rows)
    return body


def validate_mixed_manifest(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = cv2.read_json(manifest_path)
    unhashed = dict(manifest)
    declared = unhashed.pop("manifest_sha256", "")
    if cv2.sha256_json(unhashed) != declared:
        errors.append("mixed_manifest_hash_mismatch")
    rows = manifest.get("packets") or []
    groups = [row.get("group_id") for row in rows]
    if len(rows) != 105 or len(set(groups)) != 105:
        errors.append("mixed_manifest_not_exactly_105_unique_groups")
    signed = [row for row in rows if row.get("allowlist_provenance") == "signed_gold"]
    automatic = [row for row in rows if row.get("allowlist_provenance") == "packet_selected"]
    if {row.get("group_id") for row in signed} != set(cv2.GOLD_GROUPS) or len(signed) != 12:
        errors.append("mixed_manifest_signed_gold_set_mismatch")
    if len(automatic) != 93:
        errors.append("mixed_manifest_packet_selected_count_mismatch")
    for row in rows:
        path = Path(str(row.get("packet_path") or ""))
        if not path.is_file():
            errors.append(f"mixed_packet_missing:{row.get('group_id')}")
            continue
        if cv2.sha256_file(path) != row.get("file_sha256"):
            errors.append(f"mixed_packet_file_hash_mismatch:{row.get('group_id')}")
            continue
        packet = cv2.read_json(path)
        if packet.get("group_id") != row.get("group_id") or packet.get("packet_sha256") != row.get("packet_sha256"):
            errors.append(f"mixed_packet_identity_or_hash_mismatch:{row.get('group_id')}")
        errors.extend(f"{row.get('group_id')}:{error}" for error in cv2.validate_packet(packet))
        allowlist = cv2.execution_evidence_allowlist(packet)
        if allowlist.get("provenance") != row.get("allowlist_provenance"):
            errors.append(f"mixed_packet_allowlist_provenance_mismatch:{row.get('group_id')}")
        if row.get("allowlist_provenance") == "packet_selected" and packet.get("human_review_policy"):
            errors.append(f"unsigned_packet_contains_human_review_policy:{row.get('group_id')}")
    if manifest.get("counts") != {"total": 105, "signed_gold": 12, "packet_selected": 93}:
        errors.append("mixed_manifest_counts_mismatch")
    if manifest.get("evidence_cutoff") != cv2.CUTOFF.isoformat():
        errors.append("mixed_manifest_cutoff_mismatch")
    return sorted(set(errors))


def build(args: argparse.Namespace) -> Path:
    output = Path(args.output_dir).resolve()
    packets_dir = output / "packets"
    cache_dir = output / "retrieval-cache"
    output.mkdir(parents=True, exist_ok=True)
    packets_dir.mkdir(exist_ok=True)
    current_path = Path(args.current_json).resolve()
    canon_root = Path(args.canon_root).resolve()
    registry = Path(args.active_registry).resolve()
    clean, master, current_run_id = resolve_current_canon(current_path, canon_root)
    groups = builder.tier1_groups(registry, clean, master)
    all_ids = {row["group_id"] for row in groups}
    if set(cv2.GOLD_GROUPS) - all_ids:
        raise ValueError("Gold groups are absent from the active Tier 1 canon")

    signed_manifest_path = Path(args.signed_gold_manifest).resolve()
    signed_manifest = cv2.read_json(signed_manifest_path)
    signed_rows = {row["group_id"]: row for row in signed_manifest.get("packets") or []}
    if set(signed_rows) != set(cv2.GOLD_GROUPS):
        raise ValueError("Signed Gold manifest is not the exact 12-group set")

    rows: list[dict] = []
    for group_id in sorted(cv2.GOLD_GROUPS):
        source = Path(signed_rows[group_id]["packet_path"]).resolve()
        target = packets_dir / source.name
        if target.exists():
            if cv2.sha256_file(target) != cv2.sha256_file(source):
                raise ValueError(f"Existing signed packet differs byte-for-byte: {group_id}")
        else:
            shutil.copyfile(source, target)
        packet = cv2.read_json(target)
        rows.append({
            "group_id": group_id, "packet_path": str(target),
            "packet_sha256": packet["packet_sha256"], "file_sha256": cv2.sha256_file(target),
            "source_file_sha256": cv2.sha256_file(source),
            "allowlist_provenance": "signed_gold",
            "allowlist_count": len((packet.get("human_review_policy") or {}).get("valid_evidence_ids") or []),
            "ledger_total": packet["selection_summary"]["ledger_total"],
            "selected_total": packet["selection_summary"]["selected_total"],
        })

    client = builder.HttpClient(cache_dir, delay=args.delay_seconds)
    for index, group in enumerate((row for row in groups if row["group_id"] not in cv2.GOLD_GROUPS), 1):
        group_id = group["group_id"]
        target = packets_dir / f"{group_id.replace(':', '__')}.json"
        if target.exists():
            packet = cv2.read_json(target)
            packet_errors = cv2.validate_packet(packet)
            if packet_errors or packet.get("human_review_policy"):
                raise ValueError(f"Existing unsigned packet is invalid: {group_id}: {packet_errors}")
        else:
            packet = builder.build_packet(group, client, publication_limit=args.publication_limit)
            if packet.get("human_review_policy"):
                raise ValueError(f"Unsigned packet unexpectedly contains human review policy: {group_id}")
            cv2.write_json(target, packet, immutable=True)
        rows.append({
            "group_id": group_id, "packet_path": str(target),
            "packet_sha256": packet["packet_sha256"], "file_sha256": cv2.sha256_file(target),
            "allowlist_provenance": "packet_selected",
            "allowlist_count": len(packet.get("selected_evidence_ids") or []),
            "ledger_total": packet["selection_summary"]["ledger_total"],
            "selected_total": packet["selection_summary"]["selected_total"],
        })
        print(json.dumps({"phase": "packets_93", "progress": f"{index}/93", "group_id": group_id}), flush=True)

    cache_body = cache_manifest(cache_dir)
    cv2.write_json(output / "retrieval_cache_manifest.json", cache_body)
    manifest = {
        "schema_version": "tier1_mixed_evidence_packet_manifest_v2",
        "candidate_only": True, "activation_blocked": True, "created_at": cv2.utc_now(),
        "evidence_cutoff": cv2.CUTOFF.isoformat(),
        "counts": {"total": 105, "signed_gold": 12, "packet_selected": 93},
        "packets": sorted(rows, key=lambda row: row["group_id"]),
        "provenance": {
            "signed_gold_manifest_path": str(signed_manifest_path),
            "signed_gold_manifest_file_sha256": cv2.sha256_file(signed_manifest_path),
            "current_json_path": str(current_path), "current_json_sha256": cv2.sha256_file(current_path),
            "current_canon_run_id": current_run_id,
            "active_registry_path": str(registry), "active_registry_sha256": cv2.sha256_file(registry),
            "clean_canon_rows_path": str(clean), "clean_canon_rows_sha256": cv2.sha256_file(clean),
            "gene_master_path": str(master), "gene_master_sha256": cv2.sha256_file(master),
            "retrieval_cache_manifest_sha256": cv2.sha256_json(cache_body),
            "builder_sha256": cv2.sha256_file(Path(__file__)),
        },
    }
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    manifest_path = output / "evidence_manifest.json"
    cv2.write_json(manifest_path, manifest)
    errors = validate_mixed_manifest(manifest_path)
    if errors:
        raise ValueError(f"Mixed evidence manifest failed: {errors}")
    return manifest_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--current-json", required=True)
    result.add_argument("--canon-root", required=True)
    result.add_argument("--active-registry", required=True)
    result.add_argument("--signed-gold-manifest", required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument("--publication-limit", type=int, default=40)
    result.add_argument("--delay-seconds", type=float, default=0.34)
    return result


def main() -> int:
    path = build(parser().parse_args())
    print(json.dumps({"status": "mixed_manifest_ready", "manifest": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
