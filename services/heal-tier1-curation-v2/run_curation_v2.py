#!/usr/bin/env python3
"""Orchestrate gold calibration and double-pass GPT-5.6 Sol curation.

All commands create candidate artifacts. `publish` is the only command allowed to
write an active snapshot and requires a separately reviewed approval manifest.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "mechanism_curation_v2.schema.json"
PROMPTS = {
    "curator": ROOT / "prompt_mechanism_curator_v2.md",
    "critic": ROOT / "prompt_mechanism_critic_v2.md",
    "arbiter": ROOT / "prompt_mechanism_arbiter_v2.md",
    "optimizer": ROOT / "prompt_optimizer_v2.md",
}
RESPONSES_URL = "https://api.openai.com/v1/responses"
PROMPT_ROLES = ("curator", "critic", "arbiter")
PRICE_SNAPSHOT = {
    "schema_version": "openai_price_snapshot_v1",
    "model": "gpt-5.6-sol",
    "currency": "USD",
    "unit_tokens": 1_000_000,
    "input_per_million": 5.0,
    "cached_input_per_million": 0.5,
    "output_per_million": 30.0,
    "source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    "retrieved_on": "2026-08-16",
}


def usage_cost(usage: dict) -> dict:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    input_details = usage.get("input_tokens_details") or {}
    cached_tokens = int(input_details.get("cached_tokens") or 0)
    uncached_tokens = max(0, input_tokens - cached_tokens)
    price = PRICE_SNAPSHOT
    estimated = (
        uncached_tokens * price["input_per_million"]
        + cached_tokens * price["cached_input_per_million"]
        + output_tokens * price["output_per_million"]
    ) / price["unit_tokens"]
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": uncached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
        "estimated_cost_usd": round(estimated, 8),
        "cost_observability": "observed_from_response_usage",
    }


def prompt_bundle_hash(prompts: dict[str, str]) -> str:
    return cv2.sha256_json({role: cv2.sha256_text(prompts[role]) for role in PROMPT_ROLES})


def response_text(response: dict) -> str:
    texts = [
        content["text"]
        for output in response.get("output") or []
        for content in output.get("content") or []
        if content.get("type") in {"output_text", "text"} and content.get("text")
    ]
    if not texts:
        raise ValueError("Responses API result contains no output text")
    return "\n".join(texts)


def call_responses(*, api_key: str, prompt: str, user_payload: dict, schema: dict, schema_name: str,
                   effort: str, timeout: int, max_output_tokens: int) -> tuple[dict, float]:
    body = {
        "model": cv2.MODEL, "store": False, "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        RESPONSES_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed OpenAI API
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1600]
        raise RuntimeError(f"Responses API http_{error.code}: {detail}") from error
    return raw, round(time.perf_counter() - started, 4)


def technical_retry(*, raw_writer=None, **kwargs) -> tuple[dict | None, dict | None, float, list[str], list[dict]]:
    failures: list[str] = []
    attempts: list[dict] = []
    api_key = kwargs.get("api_key", "")
    for attempt in (1, 2):
        try:
            raw, latency = call_responses(**kwargs)
            raw_path = raw_writer(attempt, raw) if raw_writer else ""
            cost = usage_cost(raw.get("usage") or {})
            attempts.append({
                "attempt": attempt, "http_response_received": True, "raw_path": raw_path,
                "response_id": raw.get("id", ""), "effective_model": raw.get("model", ""),
                "latency_seconds": latency, **cost,
            })
            parsed = json.loads(response_text(raw))
            return parsed, raw, latency, failures, attempts
        except Exception as error:  # noqa: BLE001 - standardized technical retry only
            safe_error = str(error).replace(api_key, "[REDACTED]") if api_key else str(error)
            failures.append(f"attempt_{attempt}:{safe_error}")
            if len(attempts) < attempt:
                attempts.append({
                    "attempt": attempt, "http_response_received": False, "raw_path": "",
                    "response_id": "", "effective_model": "", "latency_seconds": 0.0,
                    "input_tokens": 0, "cached_input_tokens": 0, "uncached_input_tokens": 0,
                    "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": None,
                    "cost_observability": "unobservable_no_response_usage",
                })
            else:
                attempts[-1]["parse_or_validation_error"] = safe_error
    return None, None, 0.0, failures, attempts


def packet_map(evidence_manifest_path: Path) -> dict[str, dict]:
    manifest = cv2.read_json(evidence_manifest_path)
    unhashed = dict(manifest)
    declared_hash = unhashed.pop("manifest_sha256", "")
    if not declared_hash or cv2.sha256_json(unhashed) != declared_hash:
        raise ValueError("Evidence manifest hash mismatch")
    packets = {}
    for row in manifest.get("packets") or []:
        path = Path(row["packet_path"])
        packet = cv2.read_json(path)
        if packet.get("packet_sha256") != row.get("packet_sha256"):
            raise ValueError(f"Evidence manifest hash mismatch: {row.get('group_id')}")
        errors = cv2.validate_packet(packet)
        if errors:
            raise ValueError(f"Invalid packet {row.get('group_id')}: {errors}")
        packets[row["group_id"]] = packet
    return packets


def selected_packet(packet: dict, *, reverse: bool = False) -> dict:
    allowed = set(packet.get("selected_evidence_ids") or [])
    result = dict(packet)
    result["source_ledger"] = [source for source in packet.get("source_ledger") or [] if source.get("evidence_id") in allowed]
    result["source_ledger"] = sorted(result["source_ledger"], key=lambda row: row["evidence_id"], reverse=reverse)
    return result


def _write_raw(root: Path, group_id: str, role: str, attempt: int, raw: dict) -> str:
    path = root / "raw" / f"{group_id.replace(':', '__')}__{role}__attempt-{attempt}.json"
    cv2.write_json(path, raw, immutable=True)
    return str(path)


def run_role(*, group_id: str, role: str, packet: dict, output_dir: Path, prompt: str, schema: dict,
             api_key: str, effort: str, timeout: int, max_output_tokens: int, extra: dict | None = None) -> tuple[dict, list[str], dict]:
    decision_path = output_dir / "decisions" / f"{group_id.replace(':', '__')}__{role}.json"
    audit_path = output_dir / "audit" / f"{group_id.replace(':', '__')}__{role}.json"
    if decision_path.exists() and audit_path.exists():
        decision = cv2.read_json(decision_path)
        return decision, cv2.validate_decision(decision, packet), cv2.read_json(audit_path)
    if audit_path.exists() and not decision_path.exists():
        audit = cv2.read_json(audit_path)
        return {}, list(audit.get("errors") or ["technical:no_valid_output"]), audit
    if decision_path.exists() and not audit_path.exists():
        raise ValueError(f"Incomplete immutable role artifacts: {decision_path}")
    user_payload = {"evidence_packet": selected_packet(packet, reverse=role == "critic")}
    if extra:
        user_payload.update(extra)
    parsed, raw, latency, failures, attempts = technical_retry(
        api_key=api_key, prompt=prompt, user_payload=user_payload, schema=schema,
        schema_name="heal_mechanism_curation_v2", effort=effort, timeout=timeout,
        max_output_tokens=max_output_tokens,
        raw_writer=lambda attempt, value: _write_raw(output_dir, group_id, role, attempt, value),
    )
    errors = ["technical:no_valid_output"] if parsed is None else cv2.validate_decision(parsed, packet)
    observed_attempts = [item for item in attempts if item.get("estimated_cost_usd") is not None]
    audit = {
        "group_id": group_id, "role": role, "model_id": cv2.MODEL,
        "effective_model": (raw or {}).get("model", ""), "reasoning_effort": effort,
        "prompt_sha256": cv2.sha256_text(prompt), "schema_sha256": cv2.sha256_json(schema),
        "evidence_sha256": packet["packet_sha256"], "attempt_count": len(attempts),
        "technical_failures": failures, "latency_seconds": latency,
        "input_tokens": sum(item.get("input_tokens", 0) for item in observed_attempts),
        "cached_input_tokens": sum(item.get("cached_input_tokens", 0) for item in observed_attempts),
        "output_tokens": sum(item.get("output_tokens", 0) for item in observed_attempts),
        "total_tokens": sum(item.get("total_tokens", 0) for item in observed_attempts),
        "estimated_cost_usd": round(sum(item.get("estimated_cost_usd", 0) for item in observed_attempts), 8),
        "cost_observability": "complete" if all(item.get("cost_observability") == "observed_from_response_usage" for item in attempts) else "partial",
        "attempts": attempts,
        "response_id": (raw or {}).get("id", ""), "errors": errors, "created_at": cv2.utc_now(),
    }
    if parsed is not None:
        cv2.write_json(decision_path, parsed, immutable=True)
    cv2.write_json(audit_path, audit, immutable=True)
    return parsed or {}, errors, audit


def run_group(*, group_id: str, packet: dict, output_dir: Path, prompts: dict[str, str], schema: dict,
              api_key: str, timeout: int, max_output_tokens: int) -> dict:
    curator, curator_errors, curator_audit = run_role(
        group_id=group_id, role="curator", packet=packet, output_dir=output_dir, prompt=prompts["curator"],
        schema=schema, api_key=api_key, effort="high", timeout=timeout, max_output_tokens=max_output_tokens,
    )
    critic, critic_errors, critic_audit = run_role(
        group_id=group_id, role="critic", packet=packet, output_dir=output_dir, prompt=prompts["critic"],
        schema=schema, api_key=api_key, effort="high", timeout=timeout, max_output_tokens=max_output_tokens,
    )
    reasons = cv2.arbitration_reasons(curator, critic) if curator and critic else ["base_pass_invalid"]
    arbiter, arbiter_errors, arbiter_audit = {}, [], None
    if reasons and curator and critic:
        arbiter, arbiter_errors, arbiter_audit = run_role(
            group_id=group_id, role="arbiter", packet=packet, output_dir=output_dir, prompt=prompts["arbiter"],
            schema=schema, api_key=api_key, effort="xhigh", timeout=timeout, max_output_tokens=max_output_tokens,
            extra={"curator_decision": curator, "critic_decision": critic, "arbitration_reasons": reasons},
        )
    final = arbiter if arbiter else curator
    record = {
        "group_id": group_id, "packet_sha256": packet["packet_sha256"], "packet_errors": cv2.validate_packet(packet),
        "curator": curator, "critic": critic, "arbiter": arbiter or None, "final_decision": final,
        "curator_errors": curator_errors, "critic_errors": critic_errors, "arbiter_errors": arbiter_errors,
        "arbitration_reasons": reasons, "adjudicated": bool(arbiter),
        "call_audits": [item for item in (curator_audit, critic_audit, arbiter_audit) if item],
    }
    cv2.write_json(output_dir / "records" / f"{group_id.replace(':', '__')}.json", record)
    return record


def has_nonrecoverable_technical_failure(record: dict) -> bool:
    """Stop a phase once its fixed retry budget can no longer produce a complete result."""
    role_errors = [
        error
        for role in ("curator", "critic", "arbiter")
        for error in (record.get(f"{role}_errors") or [])
    ]
    return any("technical:" in error or "no_valid_output" in error for error in role_errors)


def load_prompts(prompt_source: Path) -> dict[str, str]:
    prompts = {name: path.read_text(encoding="utf-8") for name, path in PROMPTS.items()}
    if prompt_source.is_dir():
        manifest_path = prompt_source / "prompt_bundle_manifest.json"
        manifest = cv2.read_json(manifest_path)
        for role in PROMPT_ROLES:
            path = prompt_source / manifest["files"][role]
            text = path.read_text(encoding="utf-8")
            if cv2.sha256_text(text) != manifest["prompt_sha256"][role]:
                raise ValueError(f"Prompt bundle hash mismatch: {role}")
            prompts[role] = text
        if prompt_bundle_hash(prompts) != manifest.get("bundle_sha256"):
            raise ValueError("Prompt bundle manifest hash mismatch")
    else:
        prompts["curator"] = prompt_source.read_text(encoding="utf-8")
    errors = [error for role in PROMPT_ROLES for error in cv2.prompt_identifier_errors(prompts[role])]
    if errors:
        raise ValueError(f"Candidate prompt bundle contains gold identifiers: {sorted(set(errors))}")
    return prompts


def write_prompt_bundle(output: Path, prompts: dict[str, str], *, provenance: dict, immutable: bool = True) -> dict:
    if output.exists() and immutable:
        raise FileExistsError(f"Prompt bundle already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    files, hashes = {}, {}
    for role in PROMPT_ROLES:
        name = f"prompt_{role}.md"
        path = output / name
        path.write_text(prompts[role].rstrip() + "\n", encoding="utf-8")
        files[role] = name
        hashes[role] = cv2.sha256_text(path.read_text(encoding="utf-8"))
    normalized = {role: (output / files[role]).read_text(encoding="utf-8") for role in PROMPT_ROLES}
    manifest = {
        "schema_version": "tier1_curation_prompt_bundle_v1", "created_at": cv2.utc_now(),
        "files": files, "prompt_sha256": hashes, "bundle_sha256": prompt_bundle_hash(normalized),
        "provenance": provenance,
    }
    cv2.write_json(output / "prompt_bundle_manifest.json", manifest, immutable=True)
    return manifest


def candidate_reports(output_root: Path) -> list[dict]:
    reports = []
    for path in sorted(output_root.glob("candidate-*/acceptance_report.json"), key=lambda item: item.parent.name):
        reports.append(cv2.read_json(path))
    return reports


def candidate_guard(output_root: Path, candidate: str, *, resume: bool = False) -> Path:
    if not re_fullmatch(r"candidate-[1-5]", candidate):
        raise ValueError("Candidate must be candidate-1 through candidate-5")
    reports = candidate_reports(output_root)
    if len(reports) >= 5:
        raise ValueError("Five prompt candidates already exist; optimization is paused")
    repeated = cv2.repeated_dominant_failure(reports, threshold=3)
    if repeated:
        raise ValueError(f"Optimization paused after three candidates shared dominant failure: {repeated}")
    target = output_root / candidate
    if target.exists():
        if resume:
            return target
        raise FileExistsError(f"Candidate output already exists: {target}")
    return target


def re_fullmatch(pattern: str, value: str) -> bool:
    import re
    return re.fullmatch(pattern, value) is not None


def aggregate_failure(report: dict) -> str:
    errors = report.get("errors") or []
    normalized = [error.split(":", 2)[-1] for error in errors]
    return Counter(normalized).most_common(1)[0][0] if normalized else ""


def cmd_prepare_gold(args) -> int:
    packets = packet_map(Path(args.evidence_manifest))
    missing = set(cv2.GOLD_GROUPS) - set(packets)
    if missing:
        raise ValueError(f"Missing gold evidence packets: {sorted(missing)}")
    manifest = cv2.gold_manifest_template(packets)
    cv2.write_json(args.output, manifest, immutable=True)
    cards_output = Path(args.output).resolve().with_name("gold_review_cards.json")
    review_cards = [{
        "group_id": group_id,
        "split": "calibration" if group_id in cv2.CALIBRATION_GROUPS else "holdout",
        "gene": packets[group_id]["gene"], "module": packets[group_id]["module"],
        "packet_sha256": packets[group_id]["packet_sha256"],
        "selected_sources": [
            source for source in packets[group_id]["source_ledger"]
            if source["evidence_id"] in set(packets[group_id]["selected_evidence_ids"])
        ],
        "selection_summary": packets[group_id]["selection_summary"],
    } for group_id in cv2.GOLD_GROUPS]
    cv2.write_json(cards_output, {"schema_version": "tier1_curation_gold_review_cards_v2", "cards": review_cards}, immutable=True)
    print(json.dumps({"status": "pending_human_gold_approval", "output": str(Path(args.output).resolve()), "review_cards": str(cards_output), "groups": 12}))
    return 0


def cmd_validate_gold(args) -> int:
    evidence_manifest_path = Path(args.evidence_manifest)
    packets = packet_map(evidence_manifest_path)
    evidence_manifest = cv2.read_json(evidence_manifest_path)
    manifest = cv2.read_json(args.gold)
    if args.seal_output:
        manifest.pop("manifest_sha256", None)
        manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    errors = cv2.validate_evidence_manifest_artifacts(evidence_manifest_path)
    errors.extend(cv2.validate_gold_manifest(manifest, packets, evidence_manifest=evidence_manifest))
    errors = sorted(set(errors))
    if not errors and args.seal_output:
        cv2.write_json(args.seal_output, manifest, immutable=True)
    print(json.dumps({"status": "approved" if not errors else "blocked", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


def cmd_calibrate(args) -> int:
    output_root = Path(args.output_root).resolve()
    resume = bool(getattr(args, "resume", False))
    target = candidate_guard(output_root, args.candidate, resume=resume)
    completed_report = target / "acceptance_report.json"
    if resume and completed_report.exists():
        report = cv2.read_json(completed_report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("passed") else 2
    evidence_manifest_path = Path(args.evidence_manifest)
    packets = packet_map(evidence_manifest_path)
    evidence_manifest = cv2.read_json(evidence_manifest_path)
    gold = cv2.read_json(args.gold)
    gold_errors = cv2.validate_evidence_manifest_artifacts(evidence_manifest_path)
    gold_errors.extend(cv2.validate_gold_manifest(gold, packets, evidence_manifest=evidence_manifest))
    if gold_errors:
        raise ValueError(f"Gold is not executable: {gold_errors}")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    prompt_path = Path(args.prompt).resolve()
    prompts = load_prompts(prompt_path)
    schema = cv2.read_json(SCHEMA_PATH)
    target.mkdir(parents=True, exist_ok=resume)
    snapshot_dir = target / "prompt_bundle_snapshot"
    if not snapshot_dir.exists():
        write_prompt_bundle(snapshot_dir, prompts, provenance={"source": str(prompt_path), "candidate": args.candidate})
    schema_snapshot = target / "decision_schema_snapshot.json"
    if not schema_snapshot.exists():
        shutil.copy2(SCHEMA_PATH, schema_snapshot)
    manifest_body = {
        "schema_version": "tier1_curation_prompt_candidate_v2", "candidate": args.candidate,
        "created_at": cv2.utc_now(), "model": cv2.MODEL, "base_effort": "high", "arbiter_effort": "xhigh",
        "prompt_sha256": {role: cv2.sha256_text(prompts[role]) for role in PROMPT_ROLES},
        "prompt_bundle_sha256": prompt_bundle_hash(prompts), "schema_sha256": cv2.sha256_json(schema),
        "gold_sha256": cv2.sha256_json(gold), "evidence_manifest_sha256": cv2.sha256_json(cv2.read_json(args.evidence_manifest)),
        "calibration_groups": list(cv2.CALIBRATION_GROUPS), "holdout_groups": list(cv2.HOLDOUT_GROUPS),
        "max_output_tokens": args.max_output_tokens,
        "technical_retry_limit": 1, "semantic_retry": False, "activation_blocked": True,
    }
    run_manifest_path = target / "run_manifest.json"
    if run_manifest_path.exists():
        existing = cv2.read_json(run_manifest_path)
        for field in ("model", "prompt_bundle_sha256", "schema_sha256", "gold_sha256", "evidence_manifest_sha256", "max_output_tokens"):
            if existing.get(field) != manifest_body.get(field):
                raise ValueError(f"Cannot resume candidate with changed {field}")
    else:
        cv2.write_json(run_manifest_path, manifest_body, immutable=True)
    calibration_records_path = target / "calibration_records.json"
    if resume and calibration_records_path.exists():
        records = cv2.read_json(calibration_records_path)
    else:
        records = []
        for group_id in cv2.CALIBRATION_GROUPS:
            record = run_group(
                group_id=group_id, packet=packets[group_id], output_dir=target, prompts=prompts, schema=schema,
                api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens,
            )
            records.append(record)
            if has_nonrecoverable_technical_failure(record):
                break
        cv2.write_json(calibration_records_path, records, immutable=True)
    report = cv2.phase_acceptance(records, gold, "calibration")
    report.update({
        "schema_version": "tier1_curation_prompt_acceptance_v2", "candidate": args.candidate,
        "created_at": cv2.utc_now(), "phase": "calibration", "holdout_executed": False,
        "prompt_sha256": {role: cv2.sha256_text(prompts[role]) for role in PROMPT_ROLES},
        "prompt_bundle_sha256": prompt_bundle_hash(prompts),
        "dominant_failure_signature": aggregate_failure(report),
    })
    cv2.write_json(target / "acceptance_report.json", report, immutable=True)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


OPTIMIZER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["replacement_prompts", "general_changes", "error_codes_addressed"],
    "properties": {
        "replacement_prompts": {
            "type": "object", "additionalProperties": False, "required": list(PROMPT_ROLES),
            "properties": {role: {"type": "string"} for role in PROMPT_ROLES},
        },
        "general_changes": {"type": "array", "items": {"type": "string"}},
        "error_codes_addressed": {"type": "array", "items": {"type": "string"}},
    },
}


def cmd_optimize(args) -> int:
    output_root = Path(args.output_root).resolve()
    reports = candidate_reports(output_root)
    if not reports:
        raise ValueError("At least one failed candidate report is required")
    if any(report.get("phase") != "calibration" or report.get("holdout_executed") for report in reports):
        raise ValueError("Optimizer accepts calibration-only reports; holdout feedback is forbidden")
    if reports[-1].get("passed"):
        raise ValueError("The latest candidate passed; no optimization is needed")
    if len(reports) >= 5 or cv2.repeated_dominant_failure(reports, 3):
        raise ValueError("Optimization is paused by the configured stopping rule")
    current_source = Path(args.current_prompt).resolve()
    current_prompts = load_prompts(current_source)
    code_counts = Counter(error.split(":", 2)[-1] for report in reports for error in report.get("errors") or [])
    aggregate_codes = [{"code": code, "count": count} for code, count in sorted(code_counts.items())]
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    output = Path(args.output).resolve()
    raw_root = output.parent / f"{output.name}-optimizer-raw"
    parsed, raw, latency, failures, attempts = technical_retry(
        api_key=api_key, prompt=PROMPTS["optimizer"].read_text(encoding="utf-8"),
        user_payload={"current_prompts": current_prompts, "aggregate_error_codes": aggregate_codes},
        schema=OPTIMIZER_SCHEMA, schema_name="heal_curation_prompt_optimizer_v2", effort="xhigh",
        timeout=args.timeout, max_output_tokens=args.max_output_tokens,
        raw_writer=lambda attempt, value: _write_raw(raw_root, "PROMPT", "optimizer", attempt, value),
    )
    if parsed is None:
        raise RuntimeError(f"Prompt optimizer failed: {failures}")
    replacement_prompts = {role: parsed["replacement_prompts"][role].rstrip() + "\n" for role in PROMPT_ROLES}
    errors = [error for role in PROMPT_ROLES for error in cv2.prompt_identifier_errors(replacement_prompts[role])]
    if errors:
        raise ValueError(f"Optimizer produced case-specific prompt: {errors}")
    if output.exists():
        raise FileExistsError(f"Prompt candidate already exists: {output}")
    bundle_manifest = write_prompt_bundle(output, replacement_prompts, provenance={
        "source_prompt_bundle_sha256": prompt_bundle_hash(current_prompts),
        "general_changes": parsed["general_changes"], "error_codes_addressed": parsed["error_codes_addressed"],
    })
    observed_attempts = [item for item in attempts if item.get("estimated_cost_usd") is not None]
    cv2.write_json(output / "optimizer_audit.json", {
        "created_at": cv2.utc_now(), "model": cv2.MODEL, "effective_model": raw.get("model"),
        "reasoning_effort": "xhigh", "latency_seconds": latency, "technical_failures": failures,
        "source_prompt_bundle_sha256": prompt_bundle_hash(current_prompts),
        "candidate_prompt_bundle_sha256": bundle_manifest["bundle_sha256"],
        "general_changes": parsed["general_changes"], "error_codes_addressed": parsed["error_codes_addressed"],
        "aggregate_error_codes": aggregate_codes, "attempts": attempts,
        "input_tokens": sum(item.get("input_tokens", 0) for item in observed_attempts),
        "output_tokens": sum(item.get("output_tokens", 0) for item in observed_attempts),
        "total_tokens": sum(item.get("total_tokens", 0) for item in observed_attempts),
        "estimated_cost_usd": round(sum(item.get("estimated_cost_usd", 0) for item in observed_attempts), 8),
    }, immutable=True)
    print(json.dumps({"status": "candidate_prompt_bundle_created", "output": str(output)}, ensure_ascii=False))
    return 0


def cmd_freeze_prompt(args) -> int:
    acceptance_path = Path(args.calibration_report).resolve()
    acceptance = cv2.read_json(acceptance_path)
    if acceptance.get("phase") != "calibration" or not acceptance.get("passed") or acceptance.get("holdout_executed"):
        raise ValueError("freeze-prompt requires a passing calibration-only report")
    prompt_source = Path(args.prompt).resolve()
    prompts = load_prompts(prompt_source)
    evidence_path, gold_path = Path(args.evidence_manifest).resolve(), Path(args.gold).resolve()
    packets = packet_map(evidence_path)
    evidence_manifest = cv2.read_json(evidence_path)
    gold = cv2.read_json(gold_path)
    gold_errors = cv2.validate_evidence_manifest_artifacts(evidence_path)
    gold_errors.extend(cv2.validate_gold_manifest(gold, packets, evidence_manifest=evidence_manifest))
    if gold_errors:
        raise ValueError(f"Cannot freeze against invalid Gold/evidence: {sorted(set(gold_errors))}")
    output = Path(args.output_dir).resolve()
    if output.exists():
        if bool(getattr(args, "resume", False)) and (output / "frozen_prompt_manifest.json").exists():
            existing = cv2.read_json(output / "frozen_prompt_manifest.json")
            checks = {
                "prompt_bundle_sha256": prompt_bundle_hash(prompts),
                "schema_sha256": cv2.sha256_json(cv2.read_json(SCHEMA_PATH)),
                "gold_sha256": cv2.sha256_json(gold),
                "evidence_manifest_sha256": evidence_manifest["manifest_sha256"],
                "calibration_report_sha256": cv2.sha256_json(acceptance),
            }
            for field, expected in checks.items():
                if existing.get(field) != expected:
                    raise ValueError(f"Cannot resume frozen prompt with changed {field}")
            print(json.dumps({"status": "prompt_already_frozen", "output": str(output), "prompt_bundle_sha256": existing.get("prompt_bundle_sha256")}))
            return 0
        raise FileExistsError(f"Frozen prompt directory already exists: {output}")
    output.mkdir(parents=True)
    prompt_dir = output / "prompts"
    bundle_manifest = write_prompt_bundle(prompt_dir, prompts, provenance={
        "source": str(prompt_source), "calibration_report": str(acceptance_path),
    })
    shutil.copy2(SCHEMA_PATH, output / "decision_schema_frozen.json")
    manifest = {
        "schema_version": "tier1_curation_frozen_prompt_v1", "created_at": cv2.utc_now(),
        "phase": "frozen_after_calibration", "model": cv2.MODEL,
        "prompt_sha256": bundle_manifest["prompt_sha256"], "prompt_bundle_sha256": bundle_manifest["bundle_sha256"],
        "prompt_bundle_path": str(prompt_dir), "schema_sha256": cv2.sha256_json(cv2.read_json(SCHEMA_PATH)),
        "price_snapshot": PRICE_SNAPSHOT, "price_snapshot_sha256": cv2.sha256_json(PRICE_SNAPSHOT),
        "gold_sha256": cv2.sha256_json(gold), "gold_path": str(gold_path),
        "evidence_manifest_sha256": evidence_manifest["manifest_sha256"], "evidence_manifest_path": str(evidence_path),
        "calibration_report_sha256": cv2.sha256_json(acceptance), "calibration_report_path": str(acceptance_path),
        "calibration_records_path": str(acceptance_path.parent / "calibration_records.json"),
        "calibration_groups": list(cv2.CALIBRATION_GROUPS), "holdout_groups": list(cv2.HOLDOUT_GROUPS),
        "holdout_execution_allowed": True, "holdout_executed": False,
    }
    manifest["manifest_sha256"] = cv2.sha256_json(manifest)
    cv2.write_json(output / "frozen_prompt_manifest.json", manifest, immutable=True)
    print(json.dumps({"status": "prompt_frozen", "output": str(output), "prompt_bundle_sha256": manifest["prompt_bundle_sha256"]}))
    return 0


def cmd_evaluate_holdout(args) -> int:
    frozen_dir = Path(args.frozen_prompt_dir).resolve()
    frozen_path = frozen_dir / "frozen_prompt_manifest.json"
    frozen = cv2.read_json(frozen_path)
    unhashed = dict(frozen)
    declared = unhashed.pop("manifest_sha256", "")
    if not declared or cv2.sha256_json(unhashed) != declared:
        raise ValueError("Frozen prompt manifest hash mismatch")
    prompt_dir = Path(frozen["prompt_bundle_path"])
    schema_path = frozen_dir / "decision_schema_frozen.json"
    prompts = load_prompts(prompt_dir)
    if prompt_bundle_hash(prompts) != frozen.get("prompt_bundle_sha256"):
        raise ValueError("Frozen prompt bundle was modified")
    if cv2.sha256_json(cv2.read_json(schema_path)) != frozen.get("schema_sha256"):
        raise ValueError("Frozen schema was modified")
    evidence_path, gold_path = Path(frozen["evidence_manifest_path"]), Path(frozen["gold_path"])
    if cv2.read_json(evidence_path).get("manifest_sha256") != frozen.get("evidence_manifest_sha256"):
        raise ValueError("Frozen evidence manifest changed")
    gold = cv2.read_json(gold_path)
    if cv2.sha256_json(gold) != frozen.get("gold_sha256"):
        raise ValueError("Frozen Gold changed")
    output = Path(args.output_dir).resolve()
    marker_path = frozen_dir / "holdout_executed.json"
    resume = bool(getattr(args, "resume", False))
    if (output.exists() or marker_path.exists()) and not resume:
        raise FileExistsError("Holdout is single-use and has already been started or completed")
    if resume and marker_path.exists():
        marker = cv2.read_json(marker_path)
        if marker.get("output_dir") != str(output) or marker.get("frozen_manifest_sha256") != declared:
            raise ValueError("Holdout resume marker does not match this frozen run")
        final_path = output / "final_evaluation_report.json"
        if final_path.exists():
            final_report = cv2.read_json(final_path)
            print(json.dumps(final_report, ensure_ascii=False))
            return 0 if final_report.get("passed") else 2
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    output.mkdir(parents=True, exist_ok=resume)
    if not marker_path.exists():
        cv2.write_json(marker_path, {
            "started_at": cv2.utc_now(), "output_dir": str(output), "frozen_manifest_sha256": declared,
            "state": "started",
        }, immutable=True)
    packets = packet_map(evidence_path)
    schema = cv2.read_json(schema_path)
    holdout_records_path = output / "holdout_records.json"
    if resume and holdout_records_path.exists():
        records = cv2.read_json(holdout_records_path)
    else:
        records = [run_group(
            group_id=group_id, packet=packets[group_id], output_dir=output, prompts=prompts, schema=schema,
            api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens,
        ) for group_id in cv2.HOLDOUT_GROUPS]
        cv2.write_json(holdout_records_path, records, immutable=True)
    holdout_report = cv2.phase_acceptance(records, gold, "holdout")
    calibration_records = cv2.read_json(frozen["calibration_records_path"])
    final_report = cv2.phase_acceptance(calibration_records + records, gold, "complete")
    final_report.update({
        "schema_version": "tier1_curation_final_evaluation_v1", "created_at": cv2.utc_now(),
        "calibration": cv2.phase_acceptance(calibration_records, gold, "calibration"),
        "holdout": holdout_report, "frozen_prompt_manifest_sha256": declared,
        "holdout_single_use": True,
    })
    if not holdout_report["passed"]:
        final_report["status"] = "holdout_failed_requires_new_unseen_holdout"
        final_report["passed"] = False
    else:
        final_report["status"] = "passed" if final_report["passed"] else "failed"
    cv2.write_json(output / "final_evaluation_report.json", final_report, immutable=True)
    marker = cv2.read_json(marker_path)
    marker.update({"completed_at": cv2.utc_now(), "state": "passed" if final_report["passed"] else "failed", "final_report": str(output / "final_evaluation_report.json")})
    cv2.write_json(output / "holdout_terminal_state.json", marker, immutable=True)
    print(json.dumps(final_report, ensure_ascii=False))
    return 0 if final_report["passed"] else 2


PROBE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ok"],
    "properties": {"ok": {"type": "boolean", "const": True}},
}


def run_protocol_probe(*, api_key: str, output_root: Path, timeout: int) -> dict:
    audit_path = output_root / "preflight" / "probe_audit.json"
    if audit_path.exists():
        audit = cv2.read_json(audit_path)
        if not audit.get("passed"):
            raise RuntimeError(f"Previous Sol preflight exhausted its permitted attempts: {audit.get('technical_failures') or []}")
        return audit
    parsed, raw, latency, failures, attempts = technical_retry(
        api_key=api_key,
        prompt="Return JSON with ok=true. Do not add any other content.",
        user_payload={"purpose": "HEAL Tier 1 Sol quota and Responses API preflight"},
        schema=PROBE_SCHEMA, schema_name="heal_tier1_sol_preflight_v1", effort="none",
        timeout=timeout, max_output_tokens=32,
        raw_writer=lambda attempt, value: _write_raw(output_root / "preflight", "PREFLIGHT", "probe", attempt, value),
    )
    audit = {
        "schema_version": "tier1_sol_preflight_v1", "created_at": cv2.utc_now(),
        "passed": bool(parsed and parsed.get("ok") is True), "model": cv2.MODEL,
        "effective_model": (raw or {}).get("model", ""), "latency_seconds": latency,
        "technical_failures": failures, "attempts": attempts,
    }
    cv2.write_json(audit_path, audit, immutable=True)
    if not audit["passed"]:
        raise RuntimeError(f"Sol preflight failed: {failures}")
    return audit


def run_decision_schema_probe(*, api_key: str, output_root: Path, timeout: int) -> dict:
    audit_path = output_root / "preflight" / "decision_schema_probe_audit.json"
    if audit_path.exists():
        audit = cv2.read_json(audit_path)
        if not audit.get("passed"):
            raise RuntimeError(f"Previous decision-schema probe exhausted its permitted attempts: {audit.get('technical_failures') or []}")
        return audit
    schema = cv2.read_json(SCHEMA_PATH)
    prompt = (
        "Return one synthetic object that conforms exactly to the supplied JSON Schema. "
        "Use group_id='SCHEMA_PROBE:T0.0', core_status='withheld', context_usable=false, "
        "inference_ceiling='none', empty evidence arrays and source_assessments, "
        "dominant_direction='not_applicable', zero scores, one non-empty limitation, "
        "confidence=0, exact identity=false, direct module relation=false, and no conflict."
    )
    parsed, raw, latency, failures, attempts = technical_retry(
        api_key=api_key, prompt=prompt, user_payload={"purpose": "HEAL decision JSON Schema compatibility probe"},
        schema=schema, schema_name="heal_mechanism_curation_v2_probe", effort="none",
        timeout=timeout, max_output_tokens=1200,
        raw_writer=lambda attempt, value: _write_raw(output_root / "preflight", "SCHEMA", "decision", attempt, value),
    )
    audit = {
        "schema_version": "tier1_sol_decision_schema_probe_v1", "created_at": cv2.utc_now(),
        "passed": parsed is not None, "model": cv2.MODEL, "effective_model": (raw or {}).get("model", ""),
        "decision_schema_sha256": cv2.sha256_json(schema), "latency_seconds": latency,
        "technical_failures": failures, "attempts": attempts,
    }
    cv2.write_json(audit_path, audit, immutable=True)
    if not audit["passed"]:
        raise RuntimeError(f"Sol decision-schema preflight failed: {failures}")
    return audit


def cmd_run_protocol(args) -> int:
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=bool(args.resume))
    terminal_path = output_root / "protocol_terminal_state.json"
    if terminal_path.exists():
        terminal = cv2.read_json(terminal_path)
        print(json.dumps(terminal, ensure_ascii=False))
        return 0 if terminal.get("passed") else 2
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    registry_path = Path(args.active_registry).resolve()
    registry_sha_before = cv2.sha256_file(registry_path)
    expected_registry = args.expected_registry_sha256.lower()
    if registry_sha_before != expected_registry:
        raise ValueError(f"Active registry hash changed before protocol: {registry_sha_before}")
    price_path = output_root / "price_snapshot.json"
    if not price_path.exists():
        cv2.write_json(price_path, PRICE_SNAPSHOT, immutable=True)
    configuration_path = output_root / "protocol_configuration.json"
    configuration = {
        "schema_version": "tier1_candidate1_protocol_configuration_v1",
        "model": cv2.MODEL,
        "max_output_tokens": args.max_output_tokens,
        "optimizer_max_output_tokens": args.optimizer_max_output_tokens,
        "timeout_seconds": args.timeout,
        "technical_retry_limit": 1,
    }
    if configuration_path.exists():
        if cv2.read_json(configuration_path) != configuration:
            raise ValueError("Cannot resume protocol with changed execution limits")
    else:
        cv2.write_json(configuration_path, configuration, immutable=True)
    try:
        run_protocol_probe(api_key=api_key, output_root=output_root, timeout=args.timeout)
        run_decision_schema_probe(api_key=api_key, output_root=output_root, timeout=args.timeout)
    except Exception as error:  # noqa: BLE001 - terminal audit for a bounded preflight
        registry_sha_after = cv2.sha256_file(registry_path)
        terminal = {
            "schema_version": "tier1_candidate1_protocol_terminal_v1", "created_at": cv2.utc_now(),
            "status": "preflight_failed_technical_no_calibration", "passed": False,
            "error": str(error), "calibration_started": False, "holdout_started": False,
            "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
            "forbidden_stages_executed": [], "stopped_after_preflight": True,
        }
        cv2.write_json(terminal_path, terminal, immutable=True)
        print(json.dumps(terminal, ensure_ascii=False))
        return 2

    calibration_root = output_root / "calibration"
    prompt_root = output_root / "prompt-candidates"
    current_prompt = Path(args.initial_prompt).resolve()
    selected_candidate = ""
    selected_report = None
    for index in range(1, 6):
        candidate = f"candidate-{index}"
        cmd_calibrate(argparse.Namespace(
            evidence_manifest=args.evidence_manifest, gold=args.gold, output_root=str(calibration_root),
            candidate=candidate, prompt=str(current_prompt), api_key_env=args.api_key_env,
            timeout=args.timeout, max_output_tokens=args.max_output_tokens, resume=args.resume,
        ))
        report = cv2.read_json(calibration_root / candidate / "acceptance_report.json")
        if report.get("passed"):
            selected_candidate, selected_report = candidate, report
            break
        if any("technical:" in error or "no_valid_output" in error for error in report.get("errors") or []):
            summary = {"status": "calibration_failed_technical_no_optimization", "candidate": candidate, "report": report}
            cv2.write_json(terminal_path, summary, immutable=True)
            return 2
        reports = candidate_reports(calibration_root)
        repeated = cv2.repeated_dominant_failure(reports, 3)
        if index >= 5 or repeated:
            summary = {"status": "calibration_failed_stopping_rule", "candidate": candidate, "repeated_failure": repeated, "report": report}
            cv2.write_json(terminal_path, summary, immutable=True)
            return 2
        next_prompt = prompt_root / f"candidate-{index + 1}"
        try:
            cmd_optimize(argparse.Namespace(
                output_root=str(calibration_root), current_prompt=str(current_prompt), output=str(next_prompt),
                api_key_env=args.api_key_env, timeout=args.timeout, max_output_tokens=args.optimizer_max_output_tokens,
            ))
        except Exception as error:  # noqa: BLE001 - preserve bounded protocol terminal state
            registry_sha_after = cv2.sha256_file(registry_path)
            if registry_sha_after != registry_sha_before:
                raise RuntimeError(
                    f"Active registry changed during protocol: {registry_sha_before} -> {registry_sha_after}"
                ) from error
            terminal = {
                "schema_version": "tier1_candidate1_protocol_terminal_v1", "created_at": cv2.utc_now(),
                "status": "optimizer_failed_technical_no_candidate", "passed": False,
                "candidate": candidate, "error": str(error),
                "calibration_report": str(calibration_root / candidate / "acceptance_report.json"),
                "holdout_started": False,
                "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
                "forbidden_stages_executed": [], "stopped_after_optimizer": True,
            }
            cv2.write_json(terminal_path, terminal, immutable=True)
            print(json.dumps(terminal, ensure_ascii=False))
            return 2
        current_prompt = next_prompt

    if not selected_candidate or selected_report is None:
        raise RuntimeError("Calibration ended without a selected prompt")
    frozen_dir = output_root / "frozen-prompt"
    cmd_freeze_prompt(argparse.Namespace(
        calibration_report=str(calibration_root / selected_candidate / "acceptance_report.json"),
        prompt=str(current_prompt), evidence_manifest=args.evidence_manifest, gold=args.gold,
        output_dir=str(frozen_dir), resume=args.resume,
    ))
    holdout_dir = output_root / "holdout"
    holdout_rc = cmd_evaluate_holdout(argparse.Namespace(
        frozen_prompt_dir=str(frozen_dir), output_dir=str(holdout_dir), api_key_env=args.api_key_env,
        timeout=args.timeout, max_output_tokens=args.max_output_tokens, resume=args.resume,
    ))
    final_report = cv2.read_json(holdout_dir / "final_evaluation_report.json")
    registry_sha_after = cv2.sha256_file(registry_path)
    if registry_sha_after != registry_sha_before:
        raise RuntimeError(f"Active registry changed during protocol: {registry_sha_before} -> {registry_sha_after}")
    terminal = {
        "schema_version": "tier1_candidate1_protocol_terminal_v1", "created_at": cv2.utc_now(),
        "status": final_report.get("status"), "passed": bool(final_report.get("passed")),
        "selected_candidate": selected_candidate, "selected_prompt_source": str(current_prompt),
        "calibration_report": str(calibration_root / selected_candidate / "acceptance_report.json"),
        "frozen_prompt_dir": str(frozen_dir), "holdout_report": str(holdout_dir / "final_evaluation_report.json"),
        "registry_sha256_before": registry_sha_before, "registry_sha256_after": registry_sha_after,
        "forbidden_stages_executed": [], "stopped_after_holdout": True,
    }
    cv2.write_json(terminal_path, terminal, immutable=True)
    print(json.dumps(terminal, ensure_ascii=False))
    return holdout_rc


def _load_frozen_acceptance(path: Path, prompt: Path) -> dict:
    report = cv2.read_json(path)
    if not report.get("passed"):
        raise ValueError("Full run requires a passing 12/12 prompt acceptance report")
    manifest = cv2.read_json(path.parent / "run_manifest.json")
    prompts = load_prompts(prompt)
    if manifest.get("prompt_bundle_sha256") != prompt_bundle_hash(prompts):
        raise ValueError("Prompt differs from the accepted candidate snapshot")
    return manifest


def cmd_run_full(args) -> int:
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Full candidate output already exists: {output}")
    packets = packet_map(Path(args.evidence_manifest))
    if len(packets) != 105:
        raise ValueError(f"Full run requires 105 complete packets, found {len(packets)}")
    prompt_path = Path(args.prompt).resolve()
    accepted = _load_frozen_acceptance(Path(args.acceptance_report).resolve(), prompt_path)
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    output.mkdir(parents=True)
    prompts = load_prompts(prompt_path)
    schema = cv2.read_json(SCHEMA_PATH)
    shutil.copy2(prompt_path, output / "curator_prompt_snapshot.md")
    shutil.copy2(SCHEMA_PATH, output / "decision_schema_snapshot.json")
    cv2.write_json(output / "run_manifest.json", {
        "schema_version": "tier1_curation_full_candidate_v2", "candidate_only": True, "activation_blocked": True,
        "created_at": cv2.utc_now(), "model": cv2.MODEL, "expected_base_calls": 210,
        "prompt_sha256": accepted["prompt_sha256"], "schema_sha256": cv2.sha256_json(schema),
        "evidence_manifest_sha256": cv2.sha256_json(cv2.read_json(args.evidence_manifest)),
        "technical_retry_limit": 1, "semantic_retry": False,
    }, immutable=True)
    records = []
    group_ids = sorted(packets)
    random.Random(args.seed).shuffle(group_ids)
    for index, group_id in enumerate(group_ids, 1):
        record = run_group(
            group_id=group_id, packet=packets[group_id], output_dir=output, prompts=prompts, schema=schema,
            api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens,
        )
        records.append(record)
        print(json.dumps({"progress": f"{index}/105", "group_id": group_id, "status": record["final_decision"].get("core_status"), "adjudicated": record["adjudicated"]}), flush=True)
    candidates, critical = [], []
    for record in records:
        final = record["final_decision"]
        errors = cv2.validate_decision(final, packets[record["group_id"]])
        if errors:
            critical.extend(f"{record['group_id']}:{error}" for error in errors)
        candidates.append({
            "group_id": record["group_id"], **final, "packet_sha256": record["packet_sha256"],
            "adjudicated": record["adjudicated"], "arbitration_reasons": record["arbitration_reasons"],
            "evidence_selected_count": packets[record["group_id"]]["selection_summary"]["selected_total"],
        })
    with (output / "mechanism_curation_v2_candidate.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(candidates, key=lambda item: item["group_id"]):
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    flags = cv2.cross_group_flags(candidates)
    cv2.write_json(output / "cross_group_audit_flags.json", flags, immutable=True)
    summary = {
        "schema_version": "tier1_curation_full_summary_v2", "created_at": cv2.utc_now(),
        "groups": len(candidates), "base_calls_expected": 210, "adjudications": sum(row["adjudicated"] for row in records),
        "status_counts": dict(Counter(row["core_status"] for row in candidates)),
        "context_usable_count": sum(row["context_usable"] for row in candidates),
        "inference_ceiling_counts": dict(Counter(row["inference_ceiling"] for row in candidates)),
        "critical_errors": critical, "activation_blocked": True,
        "next_gate": "manual_review_100_percent_approved_and_conflict_plus_20_percent_stratified_nonapprovals",
    }
    cv2.write_json(output / "candidate_summary.json", summary, immutable=True)
    approved_groups = {row["group_id"] for row in candidates if row["core_status"] in {"approved", "approved_with_conflict"}}
    nonapproved = [row for row in candidates if row["core_status"] in {"withheld", "rejected"}]
    sample_size = (len(nonapproved) + 4) // 5
    sampled = set()
    for module_id in sorted({row["group_id"].split(":", 1)[1] for row in nonapproved}):
        module_rows = [row for row in nonapproved if row["group_id"].endswith(module_id)]
        module_rows.sort(key=lambda row: (row["evidence_selected_count"], row["group_id"]))
        if module_rows: sampled.add(module_rows[0]["group_id"])
    low = sorted(nonapproved, key=lambda row: (row["evidence_selected_count"], row["group_id"]))
    disagreements = sorted((row for row in nonapproved if row["adjudicated"]), key=lambda row: row["group_id"])
    for row in disagreements + low:
        if len(sampled) >= sample_size: break
        sampled.add(row["group_id"])
    review_ids = approved_groups | sampled
    cv2.write_json(output / "manual_review_template.json", {
        "schema_version": "tier1_curation_manual_review_v2", "created_at": cv2.utc_now(),
        "sampling": {"all_approved_and_conflict": len(approved_groups), "nonapproval_population": len(nonapproved), "nonapproval_sample_required": sample_size, "nonapproval_sample_selected": len(sampled)},
        "reviews": [{"group_id": group_id, "approved": None, "reviewer": "", "reviewed_at": "", "notes": ""} for group_id in sorted(review_ids)],
    }, immutable=True)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not critical and len(candidates) == 105 else 2


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cmd_approve_snapshot(args) -> int:
    candidate_dir = Path(args.candidate_dir).resolve()
    candidates = read_jsonl(candidate_dir / "mechanism_curation_v2_candidate.jsonl")
    summary = cv2.read_json(candidate_dir / "candidate_summary.json")
    reviews = cv2.read_json(args.review_manifest)
    errors = list(summary.get("critical_errors") or [])
    errors.extend(cv2.validate_manual_review(candidates, reviews.get("reviews") or []))
    if not args.owner.strip() or not args.approval_reference.strip():
        errors.append("approval_owner_and_reference_required")
    approval = {
        "schema_version": "tier1_curation_snapshot_approval_v2", "status": "blocked" if errors else "approved_for_publication",
        "created_at": cv2.utc_now(), "owner": args.owner, "approval_reference": args.approval_reference,
        "candidate_summary_sha256": cv2.sha256_json(summary), "candidate_registry_sha256": cv2.sha256_text((candidate_dir / "mechanism_curation_v2_candidate.jsonl").read_text(encoding="utf-8")),
        "review_manifest_sha256": cv2.sha256_json(reviews), "errors": errors, "active_registry_modified": False,
    }
    cv2.write_json(args.output, approval, immutable=True)
    print(json.dumps(approval, ensure_ascii=False))
    return 0 if not errors else 2


def cmd_publish(args) -> int:
    approval = cv2.read_json(args.approval)
    if approval.get("status") != "approved_for_publication" or approval.get("errors"):
        raise ValueError("Snapshot approval is not publishable")
    if args.confirmation != "PUBLISH_MECHANISM_CURATION_V2":
        raise ValueError("Exact publication confirmation is required")
    candidate_dir = Path(args.candidate_dir).resolve()
    registry = candidate_dir / "mechanism_curation_v2_candidate.jsonl"
    if cv2.sha256_text(registry.read_text(encoding="utf-8")) != approval.get("candidate_registry_sha256"):
        raise ValueError("Candidate registry changed after approval")
    destination = Path(args.destination).resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite active snapshot: {destination}")
    destination.mkdir(parents=True)
    shutil.copy2(registry, destination / "mechanism_curation_v2.jsonl")
    shutil.copy2(args.approval, destination / "approval.json")
    cv2.write_json(destination / "snapshot_manifest.json", {
        "schema_version": "mechanism_curation_v2_snapshot", "published_at": cv2.utc_now(),
        "registry_sha256": approval["candidate_registry_sha256"], "approval_sha256": cv2.sha256_json(approval),
        "source_candidate": str(candidate_dir),
    }, immutable=True)
    print(json.dumps({"status": "published", "destination": str(destination)}))
    return 0


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gold = sub.add_parser("prepare-gold")
    gold.add_argument("--evidence-manifest", required=True); gold.add_argument("--output", required=True); gold.set_defaults(func=cmd_prepare_gold)
    check = sub.add_parser("validate-gold")
    check.add_argument("--evidence-manifest", required=True); check.add_argument("--gold", required=True); check.add_argument("--seal-output"); check.set_defaults(func=cmd_validate_gold)
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--evidence-manifest", required=True); calibrate.add_argument("--gold", required=True); calibrate.add_argument("--output-root", required=True); calibrate.add_argument("--candidate", required=True)
    calibrate.add_argument("--prompt", default=str(PROMPTS["curator"])); calibrate.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    calibrate.add_argument("--timeout", type=int, default=180); calibrate.add_argument("--max-output-tokens", type=int, default=7000); calibrate.add_argument("--resume", action="store_true"); calibrate.set_defaults(func=cmd_calibrate)
    optimize = sub.add_parser("optimize")
    optimize.add_argument("--output-root", required=True); optimize.add_argument("--current-prompt", required=True); optimize.add_argument("--output", required=True)
    optimize.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); optimize.add_argument("--timeout", type=int, default=180); optimize.add_argument("--max-output-tokens", type=int, default=5000); optimize.set_defaults(func=cmd_optimize)
    freeze = sub.add_parser("freeze-prompt")
    freeze.add_argument("--calibration-report", required=True); freeze.add_argument("--prompt", required=True)
    freeze.add_argument("--evidence-manifest", required=True); freeze.add_argument("--gold", required=True); freeze.add_argument("--output-dir", required=True)
    freeze.add_argument("--resume", action="store_true"); freeze.set_defaults(func=cmd_freeze_prompt)
    holdout = sub.add_parser("evaluate-holdout")
    holdout.add_argument("--frozen-prompt-dir", required=True); holdout.add_argument("--output-dir", required=True)
    holdout.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); holdout.add_argument("--timeout", type=int, default=180); holdout.add_argument("--max-output-tokens", type=int, default=7000)
    holdout.add_argument("--resume", action="store_true"); holdout.set_defaults(func=cmd_evaluate_holdout)
    protocol = sub.add_parser("run-candidate1-protocol")
    protocol.add_argument("--evidence-manifest", required=True); protocol.add_argument("--gold", required=True)
    protocol.add_argument("--output-root", required=True); protocol.add_argument("--active-registry", required=True)
    protocol.add_argument("--expected-registry-sha256", required=True)
    protocol.add_argument("--initial-prompt", default=str(PROMPTS["curator"])); protocol.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    protocol.add_argument("--timeout", type=int, default=300); protocol.add_argument("--max-output-tokens", type=int, default=7000)
    protocol.add_argument("--optimizer-max-output-tokens", type=int, default=7000); protocol.add_argument("--resume", action="store_true")
    protocol.set_defaults(func=cmd_run_protocol)
    full = sub.add_parser("run-full")
    full.add_argument("--evidence-manifest", required=True); full.add_argument("--acceptance-report", required=True); full.add_argument("--prompt", required=True); full.add_argument("--output-dir", required=True)
    full.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); full.add_argument("--timeout", type=int, default=180); full.add_argument("--max-output-tokens", type=int, default=7000); full.add_argument("--seed", type=int, default=20260805); full.set_defaults(func=cmd_run_full)
    approve = sub.add_parser("approve-snapshot")
    approve.add_argument("--candidate-dir", required=True); approve.add_argument("--review-manifest", required=True); approve.add_argument("--owner", required=True); approve.add_argument("--approval-reference", required=True); approve.add_argument("--output", required=True); approve.set_defaults(func=cmd_approve_snapshot)
    publish = sub.add_parser("publish")
    publish.add_argument("--candidate-dir", required=True); publish.add_argument("--approval", required=True); publish.add_argument("--destination", required=True); publish.add_argument("--confirmation", required=True); publish.set_defaults(func=cmd_publish)
    return parser


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
