#!/usr/bin/env python3
"""Semantic V2 verification and candidate-only Tier 1 full curation.

The verification path is calibration -> immutable freeze -> single-use holdout.
The full path accepts only a passing frozen verification bundle and never
publishes, activates Luna, or processes VCFs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover - deterministic fallback is tested
    tiktoken = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402
import build_full_evidence_manifest_v2 as full_packets  # noqa: E402
import run_semantic_verification_v1 as base  # noqa: E402


def retryable_http_error(code: int, detail: str) -> bool:
    """Return whether an HTTP failure is unambiguously safe to retry."""
    normalized = detail.lower()
    if code == 429 and any(marker in normalized for marker in (
        "credit_balance_exhausted", "insufficient_quota", "no credits remaining",
    )):
        return False
    return code in {408, 409, 429, 500, 502, 503, 504}


ROOT = Path(__file__).resolve().parent
SEMANTIC_SCHEMA_PATH = ROOT / "mechanism_curation_semantic_v2.schema.json"
FINAL_SCHEMA_PATH = ROOT / "mechanism_curation_v2.schema.json"
PROMPT_PATHS = {
    role: ROOT / f"prompt_mechanism_{role}_semantic_v2.md"
    for role in ("curator", "critic", "arbiter")
}
MAX_ARBITERS_VERIFICATION = 1
FULL_COST_CAP_USD = 75.0
CAMPAIGN_ID_RE = re.compile(r"^semantic-v2-allowlist-conflict-verification-[1-9][0-9]*$")


def validate_campaign_id(value: str) -> str:
    candidate = str(value or "").strip()
    if not CAMPAIGN_ID_RE.fullmatch(candidate):
        raise ValueError("Invalid Semantic V2 campaign ID")
    return candidate


def selected_packet(packet: dict, *, reverse: bool = False) -> dict:
    result = base.selected_packet(packet, reverse=reverse)
    result["execution_allowlist"] = cv2.execution_evidence_allowlist(packet)
    return result


def prompts_and_schema() -> tuple[dict[str, str], dict]:
    prompts = {role: path.read_text(encoding="utf-8") for role, path in PROMPT_PATHS.items()}
    identifier_errors = [error for prompt in prompts.values() for error in cv2.prompt_identifier_errors(prompt)]
    if identifier_errors:
        raise ValueError(f"Semantic V2 prompt contains Gold identifiers: {identifier_errors}")
    return prompts, cv2.read_json(SEMANTIC_SCHEMA_PATH)


def _write_raw(root: Path, group_id: str, role: str, attempt: int, raw: dict) -> str:
    path = root / "raw" / f"{group_id.replace(':', '__')}__{role}__attempt-{attempt}.json"
    cv2.write_json(path, raw, immutable=True)
    return str(path)


def api_call(*, api_key: str, prompt: str, payload: dict, schema: dict,
             effort: str, timeout: int, max_output_tokens: int) -> tuple[dict, float]:
    body = {
        "model": cv2.MODEL, "store": False, "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "heal_mechanism_curation_semantic_v2",
                            "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        base.RESPONSES_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed endpoint
        raw = json.loads(response.read().decode("utf-8"))
    return raw, round(time.perf_counter() - started, 4)


def run_role(*, phase: str, group_id: str, role: str, packet: dict, output_dir: Path,
             prompt: str, schema: dict, api_key: str, timeout: int, max_output_tokens: int,
             ledger, extra: dict | None = None,
             mandatory_remaining_reserve: float = 0.0) -> tuple[dict, dict, list[str], dict]:
    stem = f"{group_id.replace(':', '__')}__{role}.json"
    semantic_path = output_dir / "semantic" / stem
    decision_path = output_dir / "decisions" / stem
    audit_path = output_dir / "audit" / stem
    if semantic_path.exists() and decision_path.exists() and audit_path.exists():
        semantic = cv2.read_json(semantic_path)
        decision = cv2.read_json(decision_path)
        errors = cv2.validate_semantic_assessment_v2(semantic, packet) + cv2.validate_decision(decision, packet)
        return semantic, decision, sorted(set(errors)), cv2.read_json(audit_path)
    if audit_path.exists():
        audit = cv2.read_json(audit_path)
        return {}, {}, list(audit.get("errors") or ["technical:no_valid_output"]), audit
    if semantic_path.exists() or decision_path.exists():
        raise ValueError(f"Incomplete immutable role artifacts for {group_id}:{role}")

    payload = {"evidence_packet": selected_packet(packet, reverse=role == "critic")}
    if extra:
        payload.update(extra)
    effort = "xhigh" if role == "arbiter" else "high"
    call_maximum = base.maximum_call_cost(prompt, payload, max_output_tokens)
    attempts: list[dict] = []
    failures: list[str] = []
    parsed: dict | None = None
    raw: dict | None = None
    latency = 0.0
    for attempt in (1, 2):
        ledger.ensure_call(phase, call_maximum, mandatory_remaining_reserve=mandatory_remaining_reserve)
        try:
            raw, latency = api_call(
                api_key=api_key, prompt=prompt, payload=payload, schema=schema,
                effort=effort, timeout=timeout, max_output_tokens=max_output_tokens,
            )
            raw_path = _write_raw(output_dir, group_id, role, attempt, raw)
            cost = base.usage_cost(raw.get("usage") or {})
            ledger.record(
                phase=phase, group_id=group_id, role=role, attempt=attempt,
                status=str(raw.get("status") or "response_received"),
                observed=cost["estimated_cost_usd"], maximum_cost=call_maximum,
            )
            attempts.append({
                "attempt": attempt, "http_response_received": True, "raw_path": raw_path,
                "response_id": raw.get("id", ""), "effective_model": raw.get("model", ""),
                "reasoning_effort": effort, "response_status": raw.get("status", ""),
                "latency_seconds": latency, **cost,
            })
            if raw.get("status") == "incomplete":
                reason = ((raw.get("incomplete_details") or {}).get("reason") or "unknown")
                failures.append(f"attempt_{attempt}:response_incomplete:{reason}")
                if attempt == 1 and reason == "max_output_tokens":
                    continue
                break
            try:
                parsed = json.loads(base.response_text(raw))
            except Exception as error:  # completed malformed output is semantic, not retryable
                failures.append(f"attempt_{attempt}:completed_response_parse_failed:{error}")
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1200]
            failures.append(f"attempt_{attempt}:http_{error.code}:{detail}")
            ledger.record(phase=phase, group_id=group_id, role=role, attempt=attempt,
                          status=f"http_{error.code}", maximum_cost=call_maximum)
            attempts.append({"attempt": attempt, "http_response_received": False, "http_status": error.code,
                             "reasoning_effort": effort, "estimated_cost_usd": None})
            if attempt == 1 and retryable_http_error(error.code, detail):
                continue
            break
        except (TimeoutError, socket.timeout) as error:
            failures.append(f"attempt_{attempt}:ambiguous_timeout_no_retry:{error}")
            ledger.record(phase=phase, group_id=group_id, role=role, attempt=attempt,
                          status="ambiguous_timeout_no_retry", unknown_reserve=call_maximum,
                          maximum_cost=call_maximum)
            attempts.append({"attempt": attempt, "http_response_received": False,
                             "reasoning_effort": effort, "estimated_cost_usd": None,
                             "cost_observability": "reserved_maximum_ambiguous_timeout"})
            break
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout)):
                failures.append(f"attempt_{attempt}:ambiguous_timeout_no_retry:{error}")
                ledger.record(phase=phase, group_id=group_id, role=role, attempt=attempt,
                              status="ambiguous_timeout_no_retry", unknown_reserve=call_maximum,
                              maximum_cost=call_maximum)
                attempts.append({"attempt": attempt, "http_response_received": False,
                                 "reasoning_effort": effort, "estimated_cost_usd": None,
                                 "cost_observability": "reserved_maximum_ambiguous_timeout"})
                break
            failures.append(f"attempt_{attempt}:transient_connection_error:{error}")
            ledger.record(phase=phase, group_id=group_id, role=role, attempt=attempt,
                          status="transient_connection_error", maximum_cost=call_maximum)
            attempts.append({"attempt": attempt, "http_response_received": False,
                             "reasoning_effort": effort, "estimated_cost_usd": None})
            if attempt == 1:
                continue
            break

    semantic_errors = ["technical:no_valid_output"] if parsed is None else cv2.validate_semantic_assessment_v2(parsed, packet)
    decision = cv2.normalize_semantic_assessment_v2(parsed, packet) if parsed is not None and not semantic_errors else {}
    final_errors = cv2.validate_decision(decision, packet) if decision else []
    errors = sorted(set(semantic_errors + final_errors))
    audit = {
        "schema_version": "tier1_semantic_role_audit_v2", "phase": phase,
        "group_id": group_id, "role": role, "model_id": cv2.MODEL,
        "effective_model": (raw or {}).get("model", ""), "reasoning_effort": effort,
        "prompt_sha256": cv2.sha256_text(prompt),
        "semantic_schema_sha256": cv2.sha256_json(schema),
        "final_schema_sha256": cv2.sha256_json(cv2.read_json(FINAL_SCHEMA_PATH)),
        "evidence_sha256": packet.get("packet_sha256"),
        "execution_allowlist": cv2.execution_evidence_allowlist(packet),
        "attempt_count": len(attempts), "attempts": attempts,
        "technical_failures": failures, "errors": errors,
        "latency_seconds": latency, "response_id": (raw or {}).get("id", ""),
        "created_at": cv2.utc_now(),
    }
    if parsed is not None:
        cv2.write_json(semantic_path, parsed, immutable=True)
    if decision:
        cv2.write_json(decision_path, decision, immutable=True)
    cv2.write_json(audit_path, audit, immutable=True)
    return parsed or {}, decision, errors, audit


def _role_finished(output_dir: Path, group_id: str, role: str) -> bool:
    stem = f"{group_id.replace(':', '__')}__{role}.json"
    semantic = output_dir / "semantic" / stem
    decision = output_dir / "decisions" / stem
    audit = output_dir / "audit" / stem
    return audit.exists() and ((semantic.exists() and decision.exists()) or not semantic.exists())


def pending_base_reserve(*, output_dir: Path, groups: tuple[str, ...], packets: dict[str, dict],
                         prompts: dict[str, str], max_output_tokens: int,
                         exclude: tuple[str, str] | None = None) -> float:
    reserve = 0.0
    for pending_group in groups:
        for pending_role in ("curator", "critic"):
            if exclude == (pending_group, pending_role) or _role_finished(output_dir, pending_group, pending_role):
                continue
            payload = {"evidence_packet": selected_packet(packets[pending_group], reverse=pending_role == "critic")}
            reserve += base.maximum_call_cost(prompts[pending_role], payload, max_output_tokens)
    return round(reserve, 8)


def run_group(*, phase: str, phase_groups: tuple[str, ...], group_id: str, packet: dict,
              packets: dict[str, dict], output_dir: Path, prompts: dict[str, str], schema: dict,
              api_key: str, timeout: int, max_output_tokens: int, ledger,
              arbiter_count: int, prior_disagreements: int, strict_verification: bool = True,
              remaining_reserve_fn=None) -> tuple[dict, int]:
    record_path = output_dir / "records" / f"{group_id.replace(':', '__')}.json"
    if record_path.exists():
        record = cv2.read_json(record_path)
        return record, arbiter_count + int(bool(record.get("adjudicated")))

    def reserve(exclude: tuple[str, str] | None = None) -> float:
        if remaining_reserve_fn:
            return float(remaining_reserve_fn(exclude))
        return pending_base_reserve(output_dir=output_dir, groups=phase_groups, packets=packets,
                                    prompts=prompts, max_output_tokens=max_output_tokens, exclude=exclude)

    curator_semantic, curator_decision, curator_errors, curator_audit = run_role(
        phase=phase, group_id=group_id, role="curator", packet=packet, output_dir=output_dir,
        prompt=prompts["curator"], schema=schema, api_key=api_key, timeout=timeout,
        max_output_tokens=max_output_tokens, ledger=ledger,
        mandatory_remaining_reserve=reserve((group_id, "curator")),
    )
    critic_semantic, critic_decision, critic_errors, critic_audit = run_role(
        phase=phase, group_id=group_id, role="critic", packet=packet, output_dir=output_dir,
        prompt=prompts["critic"], schema=schema, api_key=api_key, timeout=timeout,
        max_output_tokens=max_output_tokens, ledger=ledger,
        mandatory_remaining_reserve=reserve((group_id, "critic")),
    )
    reasons: list[str] = []
    if not curator_errors and not critic_errors:
        reasons = cv2.semantic_arbitration_reasons(
            curator_semantic, critic_semantic, curator_decision, critic_decision,
        )
    arbiter_semantic: dict = {}
    arbiter_decision: dict = {}
    arbiter_errors: list[str] = []
    arbiter_audit: dict | None = None
    if reasons:
        if strict_verification and prior_disagreements >= 1:
            arbiter_errors = ["structural:second_base_disagreement_stops_before_arbiter"]
        elif strict_verification and arbiter_count >= MAX_ARBITERS_VERIFICATION:
            arbiter_errors = ["structural:arbiter_limit_exceeded"]
        else:
            arbiter_semantic, arbiter_decision, arbiter_errors, arbiter_audit = run_role(
                phase=phase, group_id=group_id, role="arbiter", packet=packet, output_dir=output_dir,
                prompt=prompts["arbiter"], schema=schema, api_key=api_key, timeout=timeout,
                max_output_tokens=max_output_tokens, ledger=ledger,
                extra={
                    "curator_semantic": curator_semantic, "critic_semantic": critic_semantic,
                    "curator_normalized": curator_decision, "critic_normalized": critic_decision,
                    "arbitration_reasons": reasons,
                },
                mandatory_remaining_reserve=reserve(None),
            )
            arbiter_count += 1
    final_decision = arbiter_decision if arbiter_decision and not arbiter_errors else (curator_decision if not reasons else {})
    record = {
        "schema_version": "tier1_semantic_group_record_v2", "phase": phase,
        "group_id": group_id, "packet_sha256": packet.get("packet_sha256"),
        "allowlist_provenance": cv2.execution_evidence_allowlist(packet)["provenance"],
        "curator_semantic": curator_semantic, "critic_semantic": critic_semantic,
        "arbiter_semantic": arbiter_semantic or None,
        "curator_decision": curator_decision, "critic_decision": critic_decision,
        "arbiter_decision": arbiter_decision or None, "final_decision": final_decision,
        "curator_errors": curator_errors, "critic_errors": critic_errors,
        "arbiter_errors": arbiter_errors, "arbitration_reasons": reasons,
        "adjudicated": bool(arbiter_decision),
        "base_pair_agreement": bool(curator_decision and critic_decision and cv2.normalized_pair_agreement(curator_decision, critic_decision)),
        "call_audits": [row for row in (curator_audit, critic_audit, arbiter_audit) if row],
    }
    cv2.write_json(record_path, record, immutable=True)
    return record, arbiter_count


def run_phase(*, phase: str, groups: tuple[str, ...], output: Path, packets: dict[str, dict],
              prompts: dict[str, str], schema: dict, gold: dict, api_key: str,
              timeout: int, max_output_tokens: int, ledger,
              candidate_id: str) -> tuple[list[dict], dict]:
    completed_report = output / "phase_acceptance.json"
    completed_records = output / "records.json"
    if completed_report.exists() or completed_records.exists():
        if not (completed_report.exists() and completed_records.exists()):
            raise ValueError(f"Incomplete immutable phase artifacts: {phase}")
        records = cv2.read_json(completed_records)
        report = cv2.read_json(completed_report)
        if report.get("candidate") != candidate_id:
            raise ValueError(f"Campaign mismatch in completed {phase} phase")
        return records, report
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    arbiter_count = 0
    disagreements = 0
    for group_id in groups:
        record, arbiter_count = run_group(
            phase=phase, phase_groups=groups, group_id=group_id, packet=packets[group_id],
            packets=packets, output_dir=output, prompts=prompts, schema=schema,
            api_key=api_key, timeout=timeout, max_output_tokens=max_output_tokens,
            ledger=ledger, arbiter_count=arbiter_count, prior_disagreements=disagreements,
            strict_verification=True,
        )
        records.append(record)
        disagreements += int(not record.get("base_pair_agreement"))
        print(json.dumps({"phase": phase, "group": group_id, "complete": len(records),
                          "pair_agreement": record.get("base_pair_agreement"),
                          "adjudicated": record.get("adjudicated"),
                          "accounted_cost_usd": ledger.accounted}, ensure_ascii=False), flush=True)
        if disagreements > 1 or any(
            "technical:" in error or "ambiguous_timeout" in error or "second_base_disagreement" in error
            for role in ("curator", "critic", "arbiter")
            for error in record.get(f"{role}_errors") or []
        ):
            break
    report = base.phase_acceptance(records, gold, phase)
    report.update({"candidate": candidate_id, "semantic_schema_version": "v2",
                   "created_at": cv2.utc_now(), "accounted_phase_cost_usd": ledger.phase_accounted(phase)})
    cv2.write_json(output / "phase_acceptance.json", report, immutable=True)
    cv2.write_json(output / "records.json", records, immutable=True)
    return records, report


def phase_reserve(groups: tuple[str, ...], packets: dict[str, dict], prompts: dict[str, str], max_output_tokens: int) -> float:
    reserves = []
    for group_id in groups:
        for role in ("curator", "critic"):
            payload = {"evidence_packet": selected_packet(packets[group_id], reverse=role == "critic")}
            reserves.append(base.maximum_call_cost(prompts[role], payload, max_output_tokens))
    worst_base = max(reserves) if reserves else 0.0
    return round(sum(reserves) + MAX_ARBITERS_VERIFICATION * (worst_base + 0.10), 8)


def remaining_phase_reserve(*, phase_output: Path, groups: tuple[str, ...], packets: dict[str, dict],
                            prompts: dict[str, str], max_output_tokens: int) -> float:
    base_reserve = pending_base_reserve(
        output_dir=phase_output, groups=groups, packets=packets,
        prompts=prompts, max_output_tokens=max_output_tokens,
    )
    completed_arbiters = sum(
        int(_role_finished(phase_output, group_id, "arbiter")) for group_id in groups
    )
    pending_arbiter = max(0, MAX_ARBITERS_VERIFICATION - completed_arbiters)
    worst_base = max(
        (
            base.maximum_call_cost(
                prompts[role],
                {"evidence_packet": selected_packet(packets[group_id], reverse=role == "critic")},
                max_output_tokens,
            )
            for group_id in groups for role in ("curator", "critic")
        ),
        default=0.0,
    )
    return round(base_reserve + pending_arbiter * (worst_base + 0.10), 8)


def freeze_prompt(*, output: Path, prompts: dict[str, str], schema: dict, gold: dict,
                  evidence_manifest: dict, calibration_report: dict, config: dict,
                  campaign_output: Path, candidate_id: str) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    prompt_dir = output / "prompts"
    prompt_dir.mkdir()
    prompt_hashes = {}
    for role, text in prompts.items():
        path = prompt_dir / f"prompt_{role}.md"
        path.write_text(text, encoding="utf-8")
        prompt_hashes[role] = cv2.sha256_text(text)
    shutil.copy2(SEMANTIC_SCHEMA_PATH, output / SEMANTIC_SCHEMA_PATH.name)
    shutil.copy2(FINAL_SCHEMA_PATH, output / FINAL_SCHEMA_PATH.name)
    manifest = {
        "schema_version": "tier1_semantic_frozen_prompt_v2", "created_at": cv2.utc_now(),
        "candidate": candidate_id, "model": cv2.MODEL, "campaign_output": str(campaign_output),
        "prompt_sha256": prompt_hashes, "prompt_bundle_sha256": cv2.sha256_json(prompt_hashes),
        "semantic_schema_sha256": cv2.sha256_json(schema),
        "final_schema_sha256": cv2.sha256_json(cv2.read_json(FINAL_SCHEMA_PATH)),
        "normalizer_and_allowlist_resolver_sha256": cv2.sha256_file(ROOT / "curation_v2.py"),
        "gold_sha256": cv2.sha256_json(gold),
        "evidence_manifest_sha256": evidence_manifest.get("manifest_sha256"),
        "calibration_report_sha256": cv2.sha256_json(calibration_report),
        "configuration_sha256": cv2.sha256_json(config),
        "observed_price_snapshot_sha256": cv2.sha256_json(base.PRICE_SNAPSHOT),
        "reserve_price_snapshot_sha256": cv2.sha256_json(base.RESERVE_PRICE_SNAPSHOT),
        "normalization_architecture": "semantic_v2_then_deterministic_public_v2_decision",
    }
    cv2.write_json(output / "frozen_prompt_manifest.json", manifest, immutable=True)
    return manifest


def _terminal(output: Path, body: dict) -> int:
    terminal = {**body, "schema_version": "tier1_semantic_protocol_terminal_v2",
                "created_at": cv2.utc_now()}
    cv2.write_json(output / "protocol_terminal_state.json", terminal, immutable=True)
    print(json.dumps(terminal, ensure_ascii=False))
    return 0 if terminal.get("passed") else 2


def _existing_campaign_configuration(output: Path, *, candidate_id: str, resume: bool) -> dict | None:
    if not output.exists():
        if resume:
            raise FileNotFoundError("Cannot resume a campaign that does not exist")
        return None
    if not resume:
        raise FileExistsError(f"Semantic V2 campaign already exists: {output}")
    if (output / "protocol_terminal_state.json").exists():
        raise ValueError("A completed or failed formal campaign cannot be resumed")
    path = output / "protocol_configuration.json"
    if not path.exists():
        raise ValueError("Existing campaign has no immutable protocol configuration")
    config = cv2.read_json(path)
    if config.get("candidate") != candidate_id:
        raise ValueError("Resume campaign ID does not match existing configuration")
    return config


def cmd_protocol(args) -> int:
    output = Path(args.output_root).resolve()
    candidate_id = validate_campaign_id(args.campaign_id)
    if output.name != candidate_id:
        raise ValueError("Output directory basename must equal --campaign-id")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    config = _existing_campaign_configuration(
        output, candidate_id=candidate_id, resume=bool(args.resume),
    )
    if config is None:
        output.mkdir(parents=True)
        config = {
            "schema_version": "tier1_semantic_protocol_configuration_v2",
            "campaign_run_id": str(uuid.uuid4()), "candidate": candidate_id,
            "model": cv2.MODEL, "timeout_seconds": args.timeout,
            "max_output_tokens": args.max_output_tokens,
            "max_api_cost_usd_per_candidate": args.max_candidate_cost,
            "max_api_cost_usd_per_campaign": args.max_campaign_cost,
            "max_arbiters_per_verification_phase": MAX_ARBITERS_VERIFICATION,
            "optimizer_enabled": False, "activation_blocked": True,
            "evidence_manifest_path": str(Path(args.evidence_manifest).resolve()),
            "evidence_manifest_file_sha256": cv2.sha256_file(Path(args.evidence_manifest).resolve()),
            "gold_path": str(Path(args.gold).resolve()),
            "gold_file_sha256": cv2.sha256_file(Path(args.gold).resolve()),
            "active_registry_path": str(Path(args.active_registry).resolve()),
            "holdout_lock_path": str(Path(args.holdout_lock).resolve()),
        }
        cv2.write_json(output / "protocol_configuration.json", config, immutable=True)
    else:
        expected = {
            "model": cv2.MODEL, "timeout_seconds": args.timeout,
            "max_output_tokens": args.max_output_tokens,
            "max_api_cost_usd_per_candidate": args.max_candidate_cost,
            "max_api_cost_usd_per_campaign": args.max_campaign_cost,
            "evidence_manifest_path": str(Path(args.evidence_manifest).resolve()),
            "evidence_manifest_file_sha256": cv2.sha256_file(Path(args.evidence_manifest).resolve()),
            "gold_path": str(Path(args.gold).resolve()),
            "gold_file_sha256": cv2.sha256_file(Path(args.gold).resolve()),
            "active_registry_path": str(Path(args.active_registry).resolve()),
            "holdout_lock_path": str(Path(args.holdout_lock).resolve()),
        }
        if any(config.get(key) != value for key, value in expected.items()):
            raise ValueError("Resume configuration differs from the immutable campaign")
    run5 = Path(args.run5).resolve(); run6 = Path(args.run6).resolve()
    registry = Path(args.active_registry).resolve()
    baseline_path = output / "immutability_baseline.json"
    baseline = cv2.read_json(baseline_path) if baseline_path.exists() else base.immutable_baseline(run5, run6, registry)
    if baseline["active_registry"]["sha256"].lower() != args.expected_registry_sha256.lower():
        raise ValueError("Active registry hash does not match authorized baseline")
    if base.verify_baseline(baseline):
        raise ValueError(f"Immutable baseline changed: {base.verify_baseline(baseline)}")
    if not baseline_path.exists():
        cv2.write_json(baseline_path, baseline, immutable=True)

    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence_manifest = base.load_packets(evidence_path)
    gold = cv2.read_json(Path(args.gold).resolve())
    errors = cv2.validate_evidence_manifest_artifacts(evidence_path)
    errors.extend(cv2.validate_gold_manifest(gold, packets, evidence_manifest=evidence_manifest))
    if errors:
        raise ValueError(f"Gold/evidence gate failed: {sorted(set(errors))}")
    prompts, schema = prompts_and_schema()
    price_path = output / "price_snapshot.json"
    price_bundle = {
        "schema_version": "tier1_semantic_price_bundle_v2",
        "observed_cost_pricing": base.PRICE_SNAPSHOT,
        "conservative_budget_reserve_pricing": base.RESERVE_PRICE_SNAPSHOT,
    }
    if price_path.exists():
        if cv2.read_json(price_path) != price_bundle:
            raise ValueError("Price snapshot changed during resume")
    else:
        cv2.write_json(price_path, price_bundle, immutable=True)
    ledger = base.BudgetLedger(output / "budget_ledger.json",
                               candidate_cap=args.max_candidate_cost,
                               campaign_cap=args.max_campaign_cost)
    preflight = output / "preflight"; preflight.mkdir(exist_ok=True)
    auth_path = preflight / "model_authentication.json"
    authentication = cv2.read_json(auth_path) if auth_path.exists() else base.authenticate_model(api_key=api_key, timeout=min(args.timeout, 60))
    if not auth_path.exists():
        cv2.write_json(auth_path, authentication, immutable=True)
    if not authentication.get("authenticated"):
        raise RuntimeError("gpt-5.6-sol_authentication_failed")
    if not (preflight / "schema_probe_report.json").exists():
        base.run_schema_probe(api_key=api_key, output=output, timeout=args.timeout, ledger=ledger)

    ledger.ensure_phase_reserve("calibration", remaining_phase_reserve(
        phase_output=output / "calibration", groups=cv2.CALIBRATION_GROUPS,
        packets=packets, prompts=prompts, max_output_tokens=args.max_output_tokens,
    ))
    calibration_records, calibration_report = run_phase(
        phase="calibration", groups=cv2.CALIBRATION_GROUPS, output=output / "calibration",
        packets=packets, prompts=prompts, schema=schema, gold=gold, api_key=api_key,
        timeout=args.timeout, max_output_tokens=args.max_output_tokens, ledger=ledger,
        candidate_id=candidate_id,
    )
    if not calibration_report.get("passed"):
        leaks = base.secret_artifact_paths(output, api_key)
        if leaks:
            raise RuntimeError(f"secret_detected_in_artifacts:{leaks}")
        return _terminal(output, {
            "status": "calibration_failed_no_optimization", "passed": False,
            "candidate": candidate_id, "holdout_started": False,
            "calibration_report": str(output / "calibration" / "phase_acceptance.json"),
            "budget_ledger": str(ledger.path), "immutability_errors": base.verify_baseline(baseline),
            "secret_scan_passed": True,
        })

    frozen_dir = output / "frozen-prompt"
    if frozen_dir.exists():
        frozen = cv2.read_json(frozen_dir / "frozen_prompt_manifest.json")
        if frozen.get("candidate") != candidate_id:
            raise ValueError("Frozen prompt belongs to another campaign")
        if frozen.get("normalizer_and_allowlist_resolver_sha256") != cv2.sha256_file(ROOT / "curation_v2.py"):
            raise ValueError("Normalizer changed after freeze")
    else:
        frozen = freeze_prompt(output=frozen_dir, prompts=prompts, schema=schema, gold=gold,
                               evidence_manifest=evidence_manifest, calibration_report=calibration_report,
                               config=config, campaign_output=output, candidate_id=candidate_id)
    holdout_reserve = remaining_phase_reserve(
        phase_output=output / "holdout", groups=cv2.HOLDOUT_GROUPS,
        packets=packets, prompts=prompts, max_output_tokens=args.max_output_tokens,
    )
    try:
        ledger.ensure_phase_reserve("holdout", holdout_reserve)
    except RuntimeError as error:
        return _terminal(output, {
            "status": "holdout_paused_insufficient_budget", "passed": False,
            "candidate": candidate_id, "holdout_started": False, "reason": str(error),
            "required_holdout_reserve_usd": holdout_reserve, "frozen_prompt": str(frozen_dir),
            "budget_ledger": str(ledger.path), "immutability_errors": base.verify_baseline(baseline),
        })
    holdout_lock = Path(args.holdout_lock).resolve()
    lock_body = {
        "schema_version": "tier1_semantic_holdout_single_use_lock_v2",
        "campaign_run_id": config["campaign_run_id"], "campaign_output": str(output),
        "gold_sha256": cv2.sha256_json(gold), "frozen_prompt_sha256": cv2.sha256_json(frozen),
    }
    if holdout_lock.exists():
        existing_lock = cv2.read_json(holdout_lock)
        if any(existing_lock.get(key) != value for key, value in lock_body.items()):
            raise ValueError("Holdout single-use lock belongs to another execution")
        if not args.resume:
            raise ValueError("Holdout single-use lock already exists")
    else:
        cv2.write_json(holdout_lock, {**lock_body, "created_at": cv2.utc_now()}, immutable=True)
    holdout_records, holdout_report = run_phase(
        phase="holdout", groups=cv2.HOLDOUT_GROUPS, output=output / "holdout",
        packets=packets, prompts=prompts, schema=schema, gold=gold, api_key=api_key,
        timeout=args.timeout, max_output_tokens=args.max_output_tokens, ledger=ledger,
        candidate_id=candidate_id,
    )
    final_report = base.complete_acceptance(calibration_records, holdout_records, gold)
    final_report.update({
        "calibration_passed": True, "holdout_passed": holdout_report.get("passed"),
        "holdout_single_use_lock": str(holdout_lock), "accounted_campaign_cost_usd": ledger.accounted,
        "created_at": cv2.utc_now(),
    })
    if not holdout_report.get("passed") or not final_report.get("passed"):
        final_report["status"] = "holdout_failed_requires_new_unseen_holdout"
        final_report["passed"] = False
    else:
        final_report["status"] = "semantic_v2_verification_and_holdout_passed"
    cv2.write_json(output / "final_evaluation_report.json", final_report, immutable=True)
    immutability_errors = base.verify_baseline(baseline)
    leaks = base.secret_artifact_paths(output, api_key)
    if leaks:
        raise RuntimeError(f"secret_detected_in_artifacts:{leaks}")
    return _terminal(output, {
        "status": final_report["status"],
        "passed": bool(final_report.get("passed") and not immutability_errors),
        "candidate": candidate_id, "holdout_started": True,
        "calibration_report": str(output / "calibration" / "phase_acceptance.json"),
        "frozen_prompt": str(frozen_dir),
        "holdout_report": str(output / "holdout" / "phase_acceptance.json"),
        "final_evaluation_report": str(output / "final_evaluation_report.json"),
        "budget_ledger": str(ledger.path), "immutability_errors": immutability_errors,
        "secret_scan_passed": True,
    })


def _percentile_95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)])


def exact_input_tokens(prompt: str, payload: dict) -> tuple[int, str]:
    text = prompt + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if tiktoken is not None:
        encoding = tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(text)) + 24, "tiktoken:o200k_base_plus_24_message_overhead"
    return base.estimated_input_tokens(prompt, payload), "deterministic_utf8_character_fallback"


def _verification_usage(campaign: Path) -> tuple[dict[str, list[int]], int, int]:
    outputs: dict[str, list[int]] = {role: [] for role in ("curator", "critic", "arbiter")}
    adjudications = 0
    groups = 0
    for phase in ("calibration", "holdout"):
        records_path = campaign / phase / "records.json"
        if records_path.exists():
            records = cv2.read_json(records_path)
            groups += len(records)
            adjudications += sum(int(bool(row.get("adjudicated"))) for row in records)
        for audit_path in (campaign / phase / "audit").glob("*.json") if (campaign / phase / "audit").exists() else []:
            audit = cv2.read_json(audit_path)
            role = audit.get("role")
            for attempt in audit.get("attempts") or []:
                if role in outputs and attempt.get("response_status") == "completed":
                    outputs[role].append(int(attempt.get("output_tokens") or 0))
    return outputs, adjudications, groups


def load_frozen_bundle(frozen_dir: Path, verification_report: Path) -> tuple[dict, dict[str, str], dict]:
    report = cv2.read_json(verification_report)
    if not report.get("passed") or report.get("status") != "semantic_v2_verification_and_holdout_passed":
        raise ValueError("Full curation requires passing Semantic V2 calibration and holdout")
    manifest = cv2.read_json(frozen_dir / "frozen_prompt_manifest.json")
    if manifest.get("schema_version") != "tier1_semantic_frozen_prompt_v2":
        raise ValueError("Full curation requires a Semantic V2 frozen bundle")
    prompts = {
        role: (frozen_dir / "prompts" / f"prompt_{role}.md").read_text(encoding="utf-8")
        for role in ("curator", "critic", "arbiter")
    }
    prompt_hashes = {role: cv2.sha256_text(text) for role, text in prompts.items()}
    if prompt_hashes != manifest.get("prompt_sha256"):
        raise ValueError("Frozen prompt hash mismatch")
    schema = cv2.read_json(frozen_dir / SEMANTIC_SCHEMA_PATH.name)
    if cv2.sha256_json(schema) != manifest.get("semantic_schema_sha256"):
        raise ValueError("Frozen Semantic V2 schema hash mismatch")
    if cv2.sha256_file(frozen_dir / FINAL_SCHEMA_PATH.name) != cv2.sha256_file(FINAL_SCHEMA_PATH):
        raise ValueError("Frozen final schema changed or differs from the executable contract")
    if cv2.sha256_file(ROOT / "curation_v2.py") != manifest.get("normalizer_and_allowlist_resolver_sha256"):
        raise ValueError("Normalizer/allowlist resolver changed after freeze")
    if cv2.sha256_json(base.PRICE_SNAPSHOT) != manifest.get("observed_price_snapshot_sha256"):
        raise ValueError("Observed price snapshot changed after freeze")
    if cv2.sha256_json(base.RESERVE_PRICE_SNAPSHOT) != manifest.get("reserve_price_snapshot_sha256"):
        raise ValueError("Reserve price snapshot changed after freeze")
    return manifest, prompts, schema


def validate_full_packet_set(manifest_path: Path, packets: dict[str, dict], manifest: dict) -> None:
    errors = full_packets.validate_mixed_manifest(manifest_path)
    if len(packets) != 105 or set(packets) != {
        row.get("group_id") for row in manifest.get("packets") or []
    }:
        errors.append("loaded_packet_set_not_exactly_manifest_105")
    if errors:
        raise ValueError(f"Closed mixed evidence manifest failed: {sorted(set(errors))}")


def estimate_full_cost(*, packets: dict[str, dict], prompts: dict[str, str], verification_campaign: Path) -> dict:
    outputs, adjudications, observed_groups = _verification_usage(verification_campaign)
    role_p95 = {role: _percentile_95(values) for role, values in outputs.items()}
    fallback = max(role_p95["curator"], role_p95["critic"], 4000)
    if role_p95["arbiter"] <= 0:
        role_p95["arbiter"] = int(math.ceil(fallback * 1.15))
    rows = []
    base_cost = 0.0
    token_method = ""
    for group_id in sorted(packets):
        for role in ("curator", "critic"):
            payload = {"evidence_packet": selected_packet(packets[group_id], reverse=role == "critic")}
            tokens, token_method = exact_input_tokens(prompts[role], payload)
            expected = (
                tokens * base.PRICE_SNAPSHOT["input_per_million"]
                + role_p95[role] * base.PRICE_SNAPSHOT["output_per_million"]
            ) / base.PRICE_SNAPSHOT["unit_tokens"]
            rows.append({"group_id": group_id, "role": role, "input_tokens": tokens,
                         "p95_output_tokens": role_p95[role], "expected_cost_usd": round(expected, 8)})
            base_cost += expected
    observed_rate = adjudications / observed_groups if observed_groups else 0.0
    arbitration_rate = max(0.20, observed_rate)
    planned_arbiters = int(math.ceil(len(packets) * arbitration_rate))
    average_base_input = statistics.mean(row["input_tokens"] for row in rows) if rows else 0
    arbiter_input = int(math.ceil(average_base_input + 2 * max(role_p95["curator"], role_p95["critic"])))
    arbiter_unit = (
        arbiter_input * base.PRICE_SNAPSHOT["input_per_million"]
        + role_p95["arbiter"] * base.PRICE_SNAPSHOT["output_per_million"]
    ) / base.PRICE_SNAPSHOT["unit_tokens"]
    pre_contingency = base_cost + planned_arbiters * arbiter_unit
    total = pre_contingency * 1.25
    return {
        "schema_version": "tier1_full_cost_estimate_v2", "created_at": cv2.utc_now(),
        "groups": len(packets), "base_calls": len(rows),
        "observed_verification_groups": observed_groups,
        "observed_verification_adjudications": adjudications,
        "observed_arbitration_rate": round(observed_rate, 6),
        "planning_arbitration_rate": round(arbitration_rate, 6),
        "planned_arbiters": planned_arbiters, "role_p95_output_tokens": role_p95,
        "input_token_method": token_method, "base_cost_usd": round(base_cost, 6),
        "arbiter_unit_cost_usd": round(arbiter_unit, 6),
        "pre_contingency_cost_usd": round(pre_contingency, 6),
        "contingency_percent": 25, "estimated_total_cost_usd": round(total, 6),
        "hard_cap_usd": FULL_COST_CAP_USD, "within_cap": total <= FULL_COST_CAP_USD,
        "call_estimates": rows,
    }


def cmd_estimate_full(args) -> int:
    frozen_dir = Path(args.frozen_prompt_dir).resolve()
    manifest, prompts, _ = load_frozen_bundle(frozen_dir, Path(args.verification_report).resolve())
    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence = base.load_packets(evidence_path)
    validate_full_packet_set(evidence_path, packets, evidence)
    if len(packets) != 105:
        raise ValueError(f"Full estimate requires 105 packets, found {len(packets)}")
    estimate = estimate_full_cost(
        packets=packets, prompts=prompts,
        verification_campaign=Path(manifest["campaign_output"]),
    )
    estimate["evidence_manifest_sha256"] = evidence.get("manifest_sha256")
    output = Path(args.output).resolve()
    cv2.write_json(output, estimate, immutable=True)
    print(json.dumps({key: value for key, value in estimate.items() if key != "call_estimates"}, ensure_ascii=False))
    return 0 if estimate["within_cap"] else 2


def _structural_errors(record: dict) -> list[str]:
    errors = []
    for role in ("curator", "critic", "arbiter"):
        errors.extend(
            str(value) for value in record.get(f"{role}_errors") or []
            if not str(value).startswith("scientific_disagreement:")
        )
    return errors


def full_structural_stop(*, current_errors: list[str], history: list[str],
                         consecutive_code: str, consecutive_count: int) -> str:
    deterministic = cv2.structural_stop_reason(current_errors)
    if deterministic.startswith("deterministic_failure:"):
        return deterministic
    if consecutive_code and consecutive_count >= 3:
        return f"three_consecutive_structural_failures:{consecutive_code}"
    concentrated = cv2.structural_stop_reason(history)
    if concentrated.startswith("dominant_failure_80_percent:"):
        return concentrated
    return ""


def _full_manual_review(candidates: list[dict]) -> dict:
    approved = {row["group_id"] for row in candidates if row["core_status"] in {"approved", "approved_with_conflict"}}
    nonapproved = [row for row in candidates if row["core_status"] in {"withheld", "rejected"}]
    sample_size = math.ceil(len(nonapproved) * 0.20)
    sampled: set[str] = set()
    modules = sorted({row["group_id"].split(":", 1)[1] for row in nonapproved})
    for module in modules:
        rows = sorted((row for row in nonapproved if row["group_id"].endswith(module)),
                      key=lambda row: (row.get("evidence_selected_count", 0), row["group_id"]))
        if rows:
            sampled.add(rows[0]["group_id"])
    for row in sorted(nonapproved, key=lambda item: (not item.get("adjudicated"), item.get("evidence_selected_count", 0), item["group_id"])):
        if len(sampled) >= sample_size:
            break
        sampled.add(row["group_id"])
    review_ids = approved | sampled
    return {
        "schema_version": "tier1_curation_manual_review_v2", "created_at": cv2.utc_now(),
        "sampling": {"all_approved_and_conflict": len(approved),
                     "nonapproval_population": len(nonapproved),
                     "nonapproval_sample_required": sample_size,
                     "nonapproval_sample_selected": len(sampled)},
        "reviews": [{"group_id": group_id, "approved": None, "reviewer": "",
                     "reviewed_at": "", "notes": ""} for group_id in sorted(review_ids)],
    }


def cmd_run_full(args) -> int:
    output = Path(args.output_dir).resolve()
    full_campaign_id = str(args.full_campaign_id or "").strip()
    if not full_campaign_id or output.name != full_campaign_id:
        raise ValueError("Full output basename must equal --full-campaign-id")
    if output.exists():
        if not args.resume:
            raise FileExistsError(f"Full Semantic V2 candidate already exists: {output}")
        if (output / "protocol_terminal_state.json").exists():
            raise ValueError("A completed or failed full campaign cannot be resumed")
        if not (output / "run_manifest.json").exists():
            raise ValueError("Existing full campaign has no immutable run manifest")
    elif args.resume:
        raise FileNotFoundError("Cannot resume a full campaign that does not exist")
    registry = Path(args.active_registry).resolve()
    if cv2.sha256_file(registry).lower() != args.expected_registry_sha256.lower():
        raise ValueError("Active registry hash changed before full curation")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    frozen_dir = Path(args.frozen_prompt_dir).resolve()
    frozen, prompts, schema = load_frozen_bundle(frozen_dir, Path(args.verification_report).resolve())
    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence = base.load_packets(evidence_path)
    validate_full_packet_set(evidence_path, packets, evidence)
    if len(packets) != 105:
        raise ValueError(f"Full run requires 105 packets, found {len(packets)}")
    estimate = estimate_full_cost(packets=packets, prompts=prompts,
                                  verification_campaign=Path(frozen["campaign_output"]))
    output.mkdir(parents=True, exist_ok=True)
    estimate_path = output / "cost_estimate.json"
    if estimate_path.exists():
        existing_estimate = cv2.read_json(estimate_path)
        comparison = dict(estimate); comparison.pop("created_at", None)
        existing_comparison = dict(existing_estimate); existing_comparison.pop("created_at", None)
        if existing_comparison != comparison:
            raise ValueError("Full cost estimate changed during resume")
        estimate = existing_estimate
    else:
        cv2.write_json(estimate_path, estimate, immutable=True)
    if not estimate["within_cap"]:
        return _terminal(output, {
            "status": "full_curation_paused_estimate_above_75", "passed": False,
            "estimated_total_cost_usd": estimate["estimated_total_cost_usd"],
            "active_registry_sha256": cv2.sha256_file(registry),
        })
    config = {
        "schema_version": "tier1_semantic_full_configuration_v2", "created_at": cv2.utc_now(),
        "full_campaign_id": full_campaign_id,
        "model": cv2.MODEL, "groups": 105, "expected_base_calls": 210,
        "frozen_prompt_manifest_sha256": cv2.sha256_json(frozen),
        "semantic_schema_sha256": cv2.sha256_json(schema),
        "evidence_manifest_sha256": evidence.get("manifest_sha256"),
        "cost_estimate_sha256": cv2.sha256_json(estimate), "hard_cap_usd": FULL_COST_CAP_USD,
        "optimizer_enabled": False, "activation_blocked": True,
        "seed": args.seed,
        "active_registry_path": str(registry),
        "active_registry_sha256": cv2.sha256_file(registry),
    }
    run_manifest_path = output / "run_manifest.json"
    if run_manifest_path.exists():
        existing_config = cv2.read_json(run_manifest_path)
        comparable = dict(existing_config); comparable.pop("created_at", None)
        expected_comparable = dict(config); expected_comparable.pop("created_at", None)
        if comparable != expected_comparable:
            raise ValueError("Full resume configuration differs from immutable run manifest")
    else:
        cv2.write_json(run_manifest_path, config, immutable=True)
    ledger = base.BudgetLedger(output / "budget_ledger.json",
                               candidate_cap=FULL_COST_CAP_USD, campaign_cap=FULL_COST_CAP_USD)
    planned_arbiters = int(estimate["planned_arbiters"])
    role_unit = {
        role: statistics.mean(row["expected_cost_usd"] for row in estimate["call_estimates"] if row["role"] == role) * 1.25
        for role in ("curator", "critic")
    }
    arbiter_unit = float(estimate["arbiter_unit_cost_usd"]) * 1.25
    group_ids = sorted(packets)
    random.Random(args.seed).shuffle(group_ids)
    records: list[dict] = []
    arbiters = 0
    structural_history: list[str] = []
    consecutive_code = ""
    consecutive_count = 0
    stop_reason = ""

    def remaining_reserve(exclude: tuple[str, str] | None = None) -> float:
        reserve = 0.0
        for pending_group in group_ids:
            for pending_role in ("curator", "critic"):
                if exclude == (pending_group, pending_role) or _role_finished(output, pending_group, pending_role):
                    continue
                reserve += role_unit[pending_role]
        reserve += max(0, planned_arbiters - arbiters) * arbiter_unit
        return round(reserve, 8)

    for index, group_id in enumerate(group_ids, 1):
        record, arbiters = run_group(
            phase="full", phase_groups=tuple(group_ids), group_id=group_id, packet=packets[group_id],
            packets=packets, output_dir=output, prompts=prompts, schema=schema,
            api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens,
            ledger=ledger, arbiter_count=arbiters, prior_disagreements=0,
            strict_verification=False, remaining_reserve_fn=remaining_reserve,
        )
        records.append(record)
        errors = _structural_errors(record)
        structural_history.extend(errors)
        current_code = cv2.structural_error_family(errors[0]) if errors else ""
        if current_code and current_code == consecutive_code:
            consecutive_count += 1
        elif current_code:
            consecutive_code, consecutive_count = current_code, 1
        else:
            consecutive_code, consecutive_count = "", 0
        stop_reason = full_structural_stop(
            current_errors=errors, history=structural_history,
            consecutive_code=consecutive_code, consecutive_count=consecutive_count,
        )
        print(json.dumps({"progress": f"{index}/105", "group_id": group_id,
                          "complete": bool(record.get("final_decision")),
                          "adjudicated": record.get("adjudicated"),
                          "accounted_cost_usd": ledger.accounted,
                          "structural_stop": stop_reason}, ensure_ascii=False), flush=True)
        if stop_reason:
            break

    candidates: list[dict] = []
    critical: list[str] = []
    gold = cv2.read_json(Path(args.gold).resolve())
    gold_cards = {row["group_id"]: row for row in gold.get("cards") or []}
    for record in records:
        final = record.get("final_decision") or {}
        group_id = record["group_id"]
        if not final:
            critical.append(f"{group_id}:final_decision_missing")
            continue
        errors = cv2.validate_decision(final, packets[group_id])
        critical.extend(f"{group_id}:{error}" for error in errors)
        if group_id in gold_cards:
            critical.extend(f"{group_id}:{error}" for error in cv2.evaluate_against_gold(final, gold_cards[group_id]))
        candidates.append({
            "group_id": group_id, **final, "packet_sha256": record["packet_sha256"],
            "allowlist_provenance": record["allowlist_provenance"],
            "adjudicated": record["adjudicated"],
            "arbitration_reasons": record["arbitration_reasons"],
            "evidence_selected_count": packets[group_id]["selection_summary"]["selected_total"],
        })
    jsonl = output / "mechanism_curation_v2_candidate.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for row in sorted(candidates, key=lambda item: item["group_id"]):
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    cv2.write_json(output / "cross_group_audit_flags.json", cv2.cross_group_flags(candidates), immutable=True)
    cv2.write_json(output / "manual_review_template.json", _full_manual_review(candidates), immutable=True)
    complete = len(candidates) == 105 and not critical and not stop_reason
    summary = {
        "schema_version": "tier1_semantic_full_summary_v2", "created_at": cv2.utc_now(),
        "status": "full_curation_complete_candidate_only" if complete else "full_curation_incomplete",
        "passed": complete, "groups_attempted": len(records), "groups_valid": len(candidates),
        "adjudications": sum(int(bool(row.get("adjudicated"))) for row in records),
        "status_counts": dict(Counter(row["core_status"] for row in candidates)),
        "inference_ceiling_counts": dict(Counter(row["inference_ceiling"] for row in candidates)),
        "critical_errors": critical, "structural_stop_reason": stop_reason,
        "estimated_cost_usd": estimate["estimated_total_cost_usd"],
        "accounted_cost_usd": ledger.accounted, "activation_blocked": True,
        "active_registry_sha256": cv2.sha256_file(registry),
        "next_gate": "human_review_before_any_publication",
    }
    cv2.write_json(output / "candidate_summary.json", summary, immutable=True)
    return _terminal(output, {**summary, "secret_scan_passed": not base.secret_artifact_paths(output, api_key)})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    protocol = sub.add_parser("run-protocol")
    protocol.add_argument("--campaign-id", required=True)
    protocol.add_argument("--evidence-manifest", required=True)
    protocol.add_argument("--gold", required=True)
    protocol.add_argument("--run5", required=True)
    protocol.add_argument("--run6", required=True)
    protocol.add_argument("--active-registry", required=True)
    protocol.add_argument("--expected-registry-sha256", required=True)
    protocol.add_argument("--holdout-lock", required=True)
    protocol.add_argument("--output-root", required=True)
    protocol.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    protocol.add_argument("--timeout", type=int, default=600)
    protocol.add_argument("--max-output-tokens", type=int, default=16000)
    protocol.add_argument("--max-candidate-cost", type=float, default=10.0)
    protocol.add_argument("--max-campaign-cost", type=float, default=15.0)
    protocol.add_argument("--resume", action="store_true",
                          help="Resume only this same non-terminal interrupted campaign")
    protocol.set_defaults(func=cmd_protocol)
    estimate = sub.add_parser("estimate-full")
    estimate.add_argument("--frozen-prompt-dir", required=True)
    estimate.add_argument("--verification-report", required=True)
    estimate.add_argument("--evidence-manifest", required=True)
    estimate.add_argument("--output", required=True)
    estimate.set_defaults(func=cmd_estimate_full)
    full = sub.add_parser("run-full")
    full.add_argument("--frozen-prompt-dir", required=True)
    full.add_argument("--verification-report", required=True)
    full.add_argument("--evidence-manifest", required=True)
    full.add_argument("--gold", required=True)
    full.add_argument("--active-registry", required=True)
    full.add_argument("--expected-registry-sha256", required=True)
    full.add_argument("--output-dir", required=True)
    full.add_argument("--full-campaign-id", required=True)
    full.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    full.add_argument("--timeout", type=int, default=600)
    full.add_argument("--max-output-tokens", type=int, default=16000)
    full.add_argument("--seed", type=int, default=20260822)
    full.add_argument("--resume", action="store_true",
                      help="Resume only this same non-terminal interrupted full campaign")
    full.set_defaults(func=cmd_run_full)
    return result


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
