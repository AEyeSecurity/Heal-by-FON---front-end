#!/usr/bin/env python3
"""Curate only the 93 unsigned Tier 1 groups for the HEAL prototype.

This lane deliberately separates prototype readiness from formal validation.
It reuses the byte-identical Semantic V2 prompts/schemas sealed by
verification-3, records the newer deterministic normalizer, carries the 12
signed groups without model calls, and never publishes the active registry.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_full_evidence_manifest_v2 as full_packets  # noqa: E402
import curation_v2 as cv2  # noqa: E402
import run_semantic_verification_v1 as base  # noqa: E402
import run_semantic_verification_v2 as v2  # noqa: E402


EXPECTED_REGISTRY_SHA256 = "73b94c09c31c184135bc16b2e756fa447f4c040a8bf38b49181168a8c62a967f"
HARD_CAP_USD = 75.0
SIGNED_PROTOTYPE_PROVENANCE = "signed_gold_prototype_replay"
SOURCE_TERMINAL_STATUS = "holdout_failed_requires_new_unseen_holdout"


def source_bundle(source_campaign: Path) -> tuple[dict, dict[str, str], dict]:
    terminal = cv2.read_json(source_campaign / "protocol_terminal_state.json")
    if terminal.get("status") != SOURCE_TERMINAL_STATUS or terminal.get("passed") is not False:
        raise ValueError("Source campaign is not the sealed failed verification-3 holdout")
    frozen_dir = source_campaign / "frozen-prompt"
    manifest = cv2.read_json(frozen_dir / "frozen_prompt_manifest.json")
    prompts = {
        role: (frozen_dir / "prompts" / f"prompt_{role}.md").read_text(encoding="utf-8")
        for role in ("curator", "critic", "arbiter")
    }
    hashes = {role: cv2.sha256_text(text) for role, text in prompts.items()}
    if hashes != manifest.get("prompt_sha256"):
        raise ValueError("Source prompt bytes no longer match verification-3 freeze")
    semantic_path = frozen_dir / v2.SEMANTIC_SCHEMA_PATH.name
    schema = cv2.read_json(semantic_path)
    if cv2.sha256_json(schema) != manifest.get("semantic_schema_sha256"):
        raise ValueError("Source Semantic V2 schema hash mismatch")
    final_path = frozen_dir / v2.FINAL_SCHEMA_PATH.name
    if cv2.sha256_json(cv2.read_json(final_path)) != manifest.get("final_schema_sha256"):
        raise ValueError("Source final schema hash mismatch")
    return manifest, prompts, schema


def create_prototype_freeze(*, source_campaign: Path, output: Path, evidence: dict,
                            prototype_snapshot: Path) -> dict:
    source_manifest, prompts, schema = source_bundle(source_campaign)
    if output.exists():
        existing = cv2.read_json(output / "prototype_freeze_manifest.json")
        if existing.get("normalizer_sha256") != cv2.sha256_file(Path(__file__).parent / "curation_v2.py"):
            raise ValueError("Prototype freeze normalizer changed")
        return existing
    output.mkdir(parents=True)
    prompt_dir = output / "prompts"
    prompt_dir.mkdir()
    for role, text in prompts.items():
        (prompt_dir / f"prompt_{role}.md").write_text(text, encoding="utf-8")
    shutil.copy2(source_campaign / "frozen-prompt" / v2.SEMANTIC_SCHEMA_PATH.name,
                 output / v2.SEMANTIC_SCHEMA_PATH.name)
    shutil.copy2(source_campaign / "frozen-prompt" / v2.FINAL_SCHEMA_PATH.name,
                 output / v2.FINAL_SCHEMA_PATH.name)
    body = {
        "schema_version": "tier1_prototype_freeze_v1",
        "created_at": cv2.utc_now(),
        "prototype_readiness": "prototype_candidate",
        "formal_validation_readiness": "pending_new_unseen_holdout",
        "source_holdout_status": SOURCE_TERMINAL_STATUS,
        "source_campaign": str(source_campaign),
        "source_frozen_manifest_sha256": cv2.sha256_json(source_manifest),
        "prompt_sha256": {role: cv2.sha256_text(text) for role, text in prompts.items()},
        "semantic_schema_sha256": cv2.sha256_json(schema),
        "final_schema_sha256": cv2.sha256_json(cv2.read_json(v2.FINAL_SCHEMA_PATH)),
        "normalizer_sha256": cv2.sha256_file(Path(__file__).parent / "curation_v2.py"),
        "normalizer_version": cv2.NORMALIZER_VERSION,
        "evidence_manifest_sha256": evidence.get("manifest_sha256"),
        "prototype_snapshot_sha256": cv2.sha256_file(prototype_snapshot),
        "observed_price_snapshot": base.PRICE_SNAPSHOT,
        "conservative_reserve_price_snapshot": base.RESERVE_PRICE_SNAPSHOT,
        "optimizer_enabled": False,
        "activation_blocked": True,
    }
    cv2.write_json(output / "prototype_freeze_manifest.json", body, immutable=True)
    return body


def prototype_manifest(evidence: dict, output: Path) -> dict:
    rows = []
    for row in evidence.get("packets") or []:
        provenance = (
            SIGNED_PROTOTYPE_PROVENANCE
            if row.get("group_id") in cv2.GOLD_GROUPS
            else "packet_selected"
        )
        rows.append({**row, "allowlist_provenance": provenance})
    body = {
        "schema_version": "tier1_prototype_mixed_manifest_v1",
        "created_at": cv2.utc_now(),
        "counts": {"total": 105, SIGNED_PROTOTYPE_PROVENANCE: 12, "packet_selected": 93},
        "packets": rows,
        "source_evidence_manifest_sha256": evidence.get("manifest_sha256"),
        "activation_blocked": True,
    }
    body["manifest_sha256"] = cv2.sha256_json(body)
    cv2.write_json(output, body, immutable=True)
    return body


def percentile95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])


def estimate_remaining(*, packets: dict[str, dict], prompts: dict[str, str],
                       verification_campaign: Path) -> dict:
    remaining = sorted(set(packets) - set(cv2.GOLD_GROUPS))
    if len(remaining) != 93:
        raise ValueError(f"Expected 93 unsigned packets, found {len(remaining)}")
    outputs, adjudications, observed_groups = v2._verification_usage(verification_campaign)
    p95 = {role: percentile95(values) for role, values in outputs.items()}
    fallback = max(p95["curator"], p95["critic"], 4000)
    if p95["arbiter"] <= 0:
        p95["arbiter"] = int(math.ceil(fallback * 1.15))
    rows = []
    base_cost = 0.0
    method = ""
    for group_id in remaining:
        for role in ("curator", "critic"):
            payload = {"evidence_packet": v2.selected_packet(packets[group_id], reverse=role == "critic")}
            tokens, method = v2.exact_input_tokens(prompts[role], payload)
            cost = (
                tokens * base.PRICE_SNAPSHOT["input_per_million"]
                + p95[role] * base.PRICE_SNAPSHOT["output_per_million"]
            ) / base.PRICE_SNAPSHOT["unit_tokens"]
            rows.append({"group_id": group_id, "role": role, "input_tokens": tokens,
                         "p95_output_tokens": p95[role], "expected_cost_usd": round(cost, 8)})
            base_cost += cost
    observed_rate = adjudications / observed_groups if observed_groups else 0.0
    rate = max(0.20, observed_rate)
    arbiters = int(math.ceil(93 * rate))
    avg_input = statistics.mean(row["input_tokens"] for row in rows)
    arbiter_input = int(math.ceil(avg_input + 2 * max(p95["curator"], p95["critic"])))
    arbiter_unit = (
        arbiter_input * base.PRICE_SNAPSHOT["input_per_million"]
        + p95["arbiter"] * base.PRICE_SNAPSHOT["output_per_million"]
    ) / base.PRICE_SNAPSHOT["unit_tokens"]
    pre_contingency = base_cost + arbiters * arbiter_unit
    total = pre_contingency * 1.25
    return {
        "schema_version": "tier1_prototype_remaining_cost_estimate_v1",
        "created_at": cv2.utc_now(), "groups_to_call": 93, "carried_signed_groups": 12,
        "base_calls": 186, "planned_arbiters": arbiters,
        "observed_verification_groups": observed_groups,
        "observed_verification_adjudications": adjudications,
        "observed_arbitration_rate": round(observed_rate, 6),
        "planning_arbitration_rate": round(rate, 6),
        "role_p95_output_tokens": p95, "input_token_method": method,
        "base_cost_usd": round(base_cost, 6), "arbiter_unit_cost_usd": round(arbiter_unit, 6),
        "pre_contingency_cost_usd": round(pre_contingency, 6), "contingency_percent": 25,
        "estimated_total_cost_usd": round(total, 6), "hard_cap_usd": HARD_CAP_USD,
        "within_cap": total <= HARD_CAP_USD, "price_snapshot": base.PRICE_SNAPSHOT,
        "conservative_reserve_price_snapshot": base.RESERVE_PRICE_SNAPSHOT,
        "call_estimates": rows,
    }


def structural_errors(record: dict) -> list[str]:
    return [
        str(error)
        for role in ("curator", "critic", "arbiter")
        for error in record.get(f"{role}_errors") or []
        if not str(error).startswith("scientific_disagreement:")
    ]


def seed_from_prior_campaign(*, prior: Path, output: Path) -> dict:
    """Carry completed results into a fresh campaign without reopening a terminal run."""
    terminal_path = prior / "protocol_terminal_state.json"
    if not terminal_path.exists():
        raise ValueError("Carry-forward campaign has no terminal state")
    terminal = cv2.read_json(terminal_path)
    if terminal.get("status") != "full_curation_incomplete" or terminal.get("passed") is not False:
        raise ValueError("Carry-forward source is not an incomplete terminal campaign")
    if terminal.get("active_registry_sha256") != EXPECTED_REGISTRY_SHA256:
        raise ValueError("Carry-forward source registry hash is not the protected baseline")

    valid_records: dict[str, Path] = {}
    for record_path in sorted((prior / "records").glob("*.json")):
        record = cv2.read_json(record_path)
        if record.get("final_decision"):
            valid_records[record["group_id"]] = record_path
    if not valid_records:
        raise ValueError("Carry-forward source has no completed unsigned decisions")

    for directory in ("records", "semantic", "decisions", "audit", "raw"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    for group_id, record_path in valid_records.items():
        stem = group_id.replace(":", "__")
        sources = [record_path]
        for directory in ("semantic", "decisions", "audit", "raw"):
            sources.extend(sorted((prior / directory).glob(f"{stem}*")))
        for source in sources:
            directory = "records" if source == record_path else source.parent.name
            target = output / directory / source.name
            shutil.copy2(source, target)
            copied.append({
                "path": str(target.relative_to(output)).replace("\\", "/"),
                "sha256": cv2.sha256_file(target),
            })

    prior_ledger = cv2.read_json(prior / "budget_ledger.json")
    valid_groups = set(valid_records)
    carried_entries = [
        row for row in prior_ledger.get("entries") or []
        if row.get("group_id") in valid_groups
    ]
    cv2.write_json(output / "budget_ledger.json", {
        "schema_version": prior_ledger.get("schema_version", "tier1_semantic_budget_ledger_v1"),
        "candidate_cap_usd": HARD_CAP_USD,
        "campaign_cap_usd": HARD_CAP_USD,
        "entries": carried_entries,
    }, immutable=True)
    incomplete_groups = sorted({
        ":".join(str(error).split(":")[:2])
        for error in terminal.get("critical_errors") or []
        if str(error).count(":") >= 2
    } - valid_groups)
    body = {
        "schema_version": "tier1_prototype_remaining_carry_forward_v1",
        "created_at": cv2.utc_now(),
        "source_campaign": str(prior),
        "source_terminal_sha256": cv2.sha256_file(terminal_path),
        "completed_groups_carried": sorted(valid_records),
        "completed_group_count": len(valid_records),
        "incomplete_groups_not_carried": incomplete_groups,
        "prior_accounted_cost_usd": round(sum(
            float(row.get("accounted_cost_usd") or 0) for row in carried_entries
        ), 8),
        "copied_artifacts": copied,
        "failed_role_artifacts_copied": False,
    }
    body["manifest_sha256"] = cv2.sha256_json(body)
    cv2.write_json(output / "carry_forward_manifest.json", body, immutable=True)
    return body


def run(args: argparse.Namespace) -> int:
    output = Path(args.output_dir).resolve()
    if args.resume and args.carry_forward_campaign:
        raise ValueError("--resume and --carry-forward-campaign are mutually exclusive")
    if output.exists() and not args.resume:
        raise FileExistsError(f"Prototype remaining campaign already exists: {output}")
    if args.resume and not output.exists():
        raise FileNotFoundError("Cannot resume a campaign that does not exist")
    output.mkdir(parents=True, exist_ok=True)
    terminal_path = output / "protocol_terminal_state.json"
    if terminal_path.exists():
        raise ValueError("A terminal prototype campaign cannot be resumed")
    if args.carry_forward_campaign:
        seed_from_prior_campaign(
            prior=Path(args.carry_forward_campaign).resolve(), output=output,
        )
    registry = Path(args.active_registry).resolve()
    if cv2.sha256_file(registry) != EXPECTED_REGISTRY_SHA256:
        raise ValueError("Active registry hash changed before prototype curation")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence = base.load_packets(evidence_path)
    v2.validate_full_packet_set(evidence_path, packets, evidence)
    source = Path(args.source_campaign).resolve()
    _, prompts, schema = source_bundle(source)
    snapshot_path = Path(args.prototype_snapshot).resolve()
    freeze = create_prototype_freeze(
        source_campaign=source, output=output / "prototype-freeze",
        evidence=evidence, prototype_snapshot=snapshot_path,
    )
    prototype_manifest(evidence, output / "prototype_evidence_manifest.json")
    estimate = estimate_remaining(packets=packets, prompts=prompts, verification_campaign=source)
    estimate_path = output / "cost_estimate.json"
    if estimate_path.exists():
        prior = cv2.read_json(estimate_path)
        left, right = dict(prior), dict(estimate)
        left.pop("created_at", None); right.pop("created_at", None)
        if left != right:
            raise ValueError("Cost estimate changed during resume")
        estimate = prior
    else:
        cv2.write_json(estimate_path, estimate, immutable=True)
    if not estimate["within_cap"]:
        terminal = {
            "schema_version": "tier1_prototype_remaining_terminal_v1",
            "status": "cost_estimate_above_75", "passed": False,
            "estimated_total_cost_usd": estimate["estimated_total_cost_usd"],
            "active_registry_sha256": cv2.sha256_file(registry), "created_at": cv2.utc_now(),
        }
        cv2.write_json(terminal_path, terminal, immutable=True)
        print(json.dumps(terminal, ensure_ascii=False))
        return 2
    config = {
        "schema_version": "tier1_prototype_remaining_configuration_v1",
        "created_at": cv2.utc_now(), "campaign_id": output.name,
        "model": cv2.MODEL, "groups_called": 93, "groups_carried": 12,
        "prompt_sha256": freeze["prompt_sha256"],
        "normalizer_sha256": freeze["normalizer_sha256"],
        "evidence_manifest_sha256": evidence.get("manifest_sha256"),
        "estimate_sha256": cv2.sha256_json(estimate), "hard_cap_usd": HARD_CAP_USD,
        "optimizer_enabled": False, "activation_blocked": True,
        "active_registry_sha256": cv2.sha256_file(registry), "seed": args.seed,
    }
    config_path = output / "run_manifest.json"
    if config_path.exists():
        existing = cv2.read_json(config_path)
        for value in (existing, config):
            value.pop("created_at", None)
        if existing != config:
            raise ValueError("Resume configuration differs from immutable run manifest")
    else:
        cv2.write_json(config_path, config, immutable=True)
    ledger = base.BudgetLedger(output / "budget_ledger.json", candidate_cap=HARD_CAP_USD, campaign_cap=HARD_CAP_USD)
    remaining = sorted(set(packets) - set(cv2.GOLD_GROUPS))
    random.Random(args.seed).shuffle(remaining)
    role_units = {
        role: statistics.mean(
            row["expected_cost_usd"] for row in estimate["call_estimates"] if row["role"] == role
        ) * 1.25
        for role in ("curator", "critic")
    }
    arbiter_unit = float(estimate["arbiter_unit_cost_usd"]) * 1.25
    planned_arbiters = int(estimate["planned_arbiters"])
    records: list[dict] = []
    arbiters = 0
    history: list[str] = []
    consecutive_family = ""
    consecutive_count = 0
    stop_reason = ""

    def remaining_reserve(exclude: tuple[str, str] | None = None) -> float:
        reserve = 0.0
        for pending_group in remaining:
            for role in ("curator", "critic"):
                if exclude == (pending_group, role) or v2._role_finished(output, pending_group, role):
                    continue
                reserve += role_units[role]
        reserve += max(0, planned_arbiters - arbiters) * arbiter_unit
        return round(reserve, 8)

    for index, group_id in enumerate(remaining, 1):
        record, arbiters = v2.run_group(
            phase="full", phase_groups=tuple(remaining), group_id=group_id,
            packet=packets[group_id], packets=packets, output_dir=output,
            prompts=prompts, schema=schema, api_key=api_key, timeout=args.timeout,
            max_output_tokens=args.max_output_tokens, ledger=ledger,
            arbiter_count=arbiters, prior_disagreements=0, strict_verification=False,
            remaining_reserve_fn=remaining_reserve,
        )
        records.append(record)
        errors = structural_errors(record)
        history.extend(errors)
        family = cv2.structural_error_family(errors[0]) if errors else ""
        if family and family == consecutive_family:
            consecutive_count += 1
        elif family:
            consecutive_family, consecutive_count = family, 1
        else:
            consecutive_family, consecutive_count = "", 0
        stop_reason = v2.full_structural_stop(
            current_errors=errors, history=history,
            consecutive_code=consecutive_family, consecutive_count=consecutive_count,
        )
        print(json.dumps({"progress": f"{index}/93", "group_id": group_id,
                          "complete": bool(record.get("final_decision")),
                          "adjudicated": record.get("adjudicated"),
                          "accounted_cost_usd": ledger.accounted,
                          "structural_stop": stop_reason}, ensure_ascii=False), flush=True)
        if stop_reason:
            break

    snapshot = cv2.read_json(snapshot_path)
    carried = [{
        "group_id": row["group_id"], "core_status": row["core_status"],
        "context_usable": row["context_usable"],
        "inference_ceiling": row["prototype_inference_ceiling"],
        "dominant_direction": row["dominant_direction"],
        "material_conflict": row["material_conflict"],
        "limitations": row["limitations"], "expert_review_basis": row["expert_review_basis"],
        "allowlist_provenance": SIGNED_PROTOTYPE_PROVENANCE,
        "packet_sha256": row["provenance"]["packet_sha256"], "adjudicated": False,
    } for row in snapshot.get("groups") or []]
    candidates = list(carried)
    critical = []
    for record in records:
        final = record.get("final_decision") or {}
        group_id = record["group_id"]
        if not final:
            critical.append(f"{group_id}:final_decision_missing")
            continue
        critical.extend(f"{group_id}:{error}" for error in cv2.validate_decision(final, packets[group_id]))
        candidates.append({
            "group_id": group_id, **final, "packet_sha256": record["packet_sha256"],
            "allowlist_provenance": "packet_selected", "adjudicated": record["adjudicated"],
            "arbitration_reasons": record["arbitration_reasons"],
        })
    with (output / "tier1_prototype_snapshot_candidate.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(candidates, key=lambda item: item["group_id"]):
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    complete = len(candidates) == 105 and not critical and not stop_reason
    summary = {
        "schema_version": "tier1_prototype_remaining_summary_v1", "created_at": cv2.utc_now(),
        "status": "tier1_prototype_snapshot_candidate" if complete else "full_curation_incomplete",
        "passed": complete, "signed_groups_carried": len(carried),
        "unsigned_groups_attempted": len(records),
        "total_groups_valid": len(candidates),
        "adjudications": sum(int(bool(row.get("adjudicated"))) for row in records),
        "status_counts": dict(Counter(row.get("core_status") for row in candidates)),
        "inference_ceiling_counts": dict(Counter(row.get("inference_ceiling") for row in candidates)),
        "critical_errors": critical, "structural_stop_reason": stop_reason,
        "estimated_cost_usd": estimate["estimated_total_cost_usd"],
        "accounted_cost_usd": ledger.accounted, "activation_blocked": True,
        "active_registry_sha256": cv2.sha256_file(registry),
        "next_gate": "human_review_before_any_publication",
    }
    cv2.write_json(output / "candidate_summary.json", summary, immutable=True)
    terminal = {**summary, "schema_version": "tier1_prototype_remaining_terminal_v1"}
    cv2.write_json(terminal_path, terminal, immutable=True)
    print(json.dumps(terminal, ensure_ascii=False))
    return 0 if complete else 2


def estimate(args: argparse.Namespace) -> int:
    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence = base.load_packets(evidence_path)
    v2.validate_full_packet_set(evidence_path, packets, evidence)
    source = Path(args.source_campaign).resolve()
    _, prompts, _ = source_bundle(source)
    result = estimate_remaining(packets=packets, prompts=prompts, verification_campaign=source)
    cv2.write_json(Path(args.output).resolve(), result, immutable=True)
    print(json.dumps({key: value for key, value in result.items() if key != "call_estimates"}, ensure_ascii=False))
    return 0 if result["within_cap"] else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)
    for command in ("estimate", "run"):
        item = subs.add_parser(command)
        item.add_argument("--source-campaign", required=True)
        item.add_argument("--evidence-manifest", required=True)
        if command == "estimate":
            item.add_argument("--output", required=True)
            item.set_defaults(func=estimate)
        else:
            item.add_argument("--prototype-snapshot", required=True)
            item.add_argument("--active-registry", required=True)
            item.add_argument("--output-dir", required=True)
            item.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
            item.add_argument("--timeout", type=int, default=600)
            item.add_argument("--max-output-tokens", type=int, default=16000)
            item.add_argument("--seed", type=int, default=20260822)
            item.add_argument("--resume", action="store_true")
            item.add_argument("--carry-forward-campaign")
            item.set_defaults(func=run)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
