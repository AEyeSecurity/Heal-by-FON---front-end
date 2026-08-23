#!/usr/bin/env python3
"""Restricted semantic-v1 verification campaign for Tier 1 mechanism curation.

This runner can only replay candidate-3 locally, execute the six calibration
groups, freeze a passing prompt, and execute the six holdout groups once.  It
contains no optimizer, publication, Luna, VCF, or 105-group entry point.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402


ROOT = Path(__file__).resolve().parent
SEMANTIC_SCHEMA_PATH = ROOT / "mechanism_curation_semantic_v1.schema.json"
FINAL_SCHEMA_PATH = ROOT / "mechanism_curation_v2.schema.json"
PROMPT_PATHS = {
    "curator": ROOT / "prompt_mechanism_curator_semantic_v1.md",
    "critic": ROOT / "prompt_mechanism_critic_semantic_v1.md",
    "arbiter": ROOT / "prompt_mechanism_arbiter_semantic_v1.md",
}
RESPONSES_URL = "https://api.openai.com/v1/responses"
MODEL_URL = f"https://api.openai.com/v1/models/{cv2.MODEL}"
PRICE_SNAPSHOT = {
    "schema_version": "openai_price_snapshot_v1",
    "model": "gpt-5.6-sol",
    "currency": "USD",
    "unit_tokens": 1_000_000,
    "input_per_million": 4.0,
    "cached_input_per_million": 0.4,
    "output_per_million": 20.0,
    "source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    "retrieved_on": "2026-08-22",
    "pricing_note": "Official promotional GPT-5.6 Sol pricing captured on the execution date; conservative reserves remain 5/0.5/30.",
}
RESERVE_PRICE_SNAPSHOT = {
    "schema_version": "openai_conservative_reserve_price_snapshot_v1",
    "model": "gpt-5.6-sol",
    "currency": "USD",
    "unit_tokens": 1_000_000,
    "input_per_million": 5.0,
    "cached_input_per_million": 0.5,
    "output_per_million": 30.0,
    "basis": "Conservative ceiling retained from the previously approved protocol.",
}
MAX_ARBITERS_PER_PHASE = 1


def usage_cost(usage: dict) -> dict:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    reasoning_tokens = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
    uncached_tokens = max(0, input_tokens - cached_tokens)
    estimated = (
        uncached_tokens * PRICE_SNAPSHOT["input_per_million"]
        + cached_tokens * PRICE_SNAPSHOT["cached_input_per_million"]
        + output_tokens * PRICE_SNAPSHOT["output_per_million"]
    ) / PRICE_SNAPSHOT["unit_tokens"]
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": uncached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "estimated_cost_usd": round(estimated, 8),
        "cost_observability": "observed_from_response_usage",
    }


def estimated_input_tokens(prompt: str, payload: dict) -> int:
    # Conservative for English prompts plus dense JSON; only used for budget reservation.
    characters = len(prompt) + len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return math.ceil(characters / 3) + 1024


def maximum_call_cost(prompt: str, payload: dict, max_output_tokens: int) -> float:
    input_cost = estimated_input_tokens(prompt, payload) * RESERVE_PRICE_SNAPSHOT["input_per_million"]
    output_cost = max_output_tokens * RESERVE_PRICE_SNAPSHOT["output_per_million"]
    return round((input_cost + output_cost) / RESERVE_PRICE_SNAPSHOT["unit_tokens"], 8)


def response_text(response: dict) -> str:
    values = [
        content.get("text", "")
        for output in response.get("output") or []
        for content in output.get("content") or []
        if content.get("type") in {"output_text", "text"} and content.get("text")
    ]
    if not values:
        raise ValueError("response_has_no_output_text")
    return "\n".join(values)


def selected_packet(packet: dict, *, reverse: bool = False) -> dict:
    selected = set(packet.get("selected_evidence_ids") or [])
    result = dict(packet)
    result["source_ledger"] = sorted(
        [row for row in packet.get("source_ledger") or [] if row.get("evidence_id") in selected],
        key=lambda row: row.get("evidence_id", ""), reverse=reverse,
    )
    return result


def load_packets(evidence_manifest_path: Path) -> tuple[dict[str, dict], dict]:
    manifest = cv2.read_json(evidence_manifest_path)
    unhashed = dict(manifest)
    declared_hash = unhashed.pop("manifest_sha256", "")
    if not declared_hash or cv2.sha256_json(unhashed) != declared_hash:
        raise ValueError("Evidence manifest hash mismatch")
    packets: dict[str, dict] = {}
    for row in manifest.get("packets") or []:
        packet = cv2.read_json(row["packet_path"])
        if packet.get("packet_sha256") != row.get("packet_sha256"):
            raise ValueError(f"Packet hash mismatch: {row.get('group_id')}")
        errors = cv2.validate_packet(packet)
        if errors:
            raise ValueError(f"Invalid packet {row.get('group_id')}: {errors}")
        packets[row["group_id"]] = packet
    return packets, manifest


def tree_manifest(path: Path) -> dict:
    rows = [
        {
            "path": str(file.relative_to(path)).replace("\\", "/"),
            "size": file.stat().st_size,
            "sha256": cv2.sha256_file(file),
        }
        for file in sorted(path.rglob("*")) if file.is_file()
    ]
    return {"root": str(path), "files": rows, "tree_sha256": cv2.sha256_json(rows)}


def immutable_baseline(run5: Path, run6: Path, registry: Path) -> dict:
    return {
        "schema_version": "tier1_semantic_immutability_baseline_v1",
        "run5": tree_manifest(run5),
        "run6": tree_manifest(run6),
        "active_registry": {"path": str(registry), "sha256": cv2.sha256_file(registry)},
    }


class BudgetLedger:
    def __init__(self, path: Path, *, candidate_cap: float, campaign_cap: float):
        self.path = path
        self.candidate_cap = float(candidate_cap)
        self.campaign_cap = float(campaign_cap)
        if path.exists():
            self.state = cv2.read_json(path)
            if self.state.get("candidate_cap_usd") != self.candidate_cap or self.state.get("campaign_cap_usd") != self.campaign_cap:
                raise ValueError("Cannot resume with changed budget caps")
        else:
            self.state = {
                "schema_version": "tier1_semantic_budget_ledger_v1",
                "candidate_cap_usd": self.candidate_cap,
                "campaign_cap_usd": self.campaign_cap,
                "entries": [],
            }
            self._write()

    def _write(self) -> None:
        body = dict(self.state)
        body["observed_cost_usd"] = self.observed
        body["unknown_reserved_cost_usd"] = self.unknown
        body["accounted_campaign_cost_usd"] = self.accounted
        body["phase_costs_usd"] = {
            phase: round(sum(float(row.get("accounted_cost_usd") or 0) for row in self.state["entries"] if row.get("phase") == phase), 8)
            for phase in ("probe", "calibration", "holdout")
        }
        cv2.write_json(self.path, body)
        self.state = body

    @property
    def observed(self) -> float:
        return round(sum(float(row.get("observed_cost_usd") or 0) for row in self.state["entries"]), 8)

    @property
    def unknown(self) -> float:
        return round(sum(float(row.get("unknown_reserved_cost_usd") or 0) for row in self.state["entries"]), 8)

    @property
    def accounted(self) -> float:
        return round(sum(float(row.get("accounted_cost_usd") or 0) for row in self.state["entries"]), 8)

    def phase_accounted(self, phase: str) -> float:
        return round(sum(float(row.get("accounted_cost_usd") or 0) for row in self.state["entries"] if row.get("phase") == phase), 8)

    def ensure_call(self, phase: str, maximum_cost: float, *, mandatory_remaining_reserve: float = 0.0) -> None:
        projected_campaign = self.accounted + maximum_cost + mandatory_remaining_reserve
        projected_phase = self.phase_accounted(phase) + maximum_cost + mandatory_remaining_reserve
        if projected_campaign > self.campaign_cap + 1e-9:
            raise RuntimeError("budget_gate_campaign_would_be_exceeded")
        if phase != "probe" and projected_phase > self.candidate_cap + 1e-9:
            raise RuntimeError(f"budget_gate_{phase}_would_be_exceeded")

    def ensure_phase_reserve(self, phase: str, reserve: float) -> None:
        if self.accounted + reserve > self.campaign_cap + 1e-9:
            raise RuntimeError("budget_gate_campaign_phase_reserve_insufficient")
        if phase != "probe" and self.phase_accounted(phase) + reserve > self.candidate_cap + 1e-9:
            raise RuntimeError(f"budget_gate_{phase}_phase_reserve_insufficient")

    def record(self, *, phase: str, group_id: str, role: str, attempt: int, status: str,
               observed: float = 0.0, unknown_reserve: float = 0.0, maximum_cost: float = 0.0) -> None:
        entry = {
            "phase": phase, "group_id": group_id, "role": role, "attempt": attempt,
            "status": status, "observed_cost_usd": round(observed, 8),
            "unknown_reserved_cost_usd": round(unknown_reserve, 8),
            "accounted_cost_usd": round(observed + unknown_reserve, 8),
            "maximum_call_cost_usd": round(maximum_cost, 8), "created_at": cv2.utc_now(),
        }
        key = (phase, group_id, role, attempt)
        existing = {(row["phase"], row["group_id"], row["role"], row["attempt"]) for row in self.state["entries"]}
        if key in existing:
            raise ValueError(f"Duplicate budget entry: {key}")
        self.state["entries"].append(entry)
        self._write()


def _write_raw(root: Path, group_id: str, role: str, attempt: int, raw: dict) -> str:
    path = root / "raw" / f"{group_id.replace(':', '__')}__{role}__attempt-{attempt}.json"
    cv2.write_json(path, raw, immutable=True)
    return str(path)


def api_call(*, api_key: str, prompt: str, payload: dict, schema: dict,
             effort: str, timeout: int, max_output_tokens: int) -> tuple[dict, float]:
    body = {
        "model": cv2.MODEL,
        "store": False,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "heal_mechanism_curation_semantic_v1", "strict": True, "schema": schema}},
    }
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed OpenAI endpoint
        raw = json.loads(response.read().decode("utf-8"))
    return raw, round(time.perf_counter() - started, 4)


def authenticate_model(*, api_key: str, timeout: int) -> dict:
    request = urllib.request.Request(
        MODEL_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed OpenAI endpoint
        raw = json.loads(response.read().decode("utf-8"))
    return {
        "schema_version": "tier1_semantic_model_authentication_v1",
        "requested_model": cv2.MODEL,
        "effective_model": raw.get("id", ""),
        "authenticated": raw.get("id") == cv2.MODEL,
        "latency_seconds": round(time.perf_counter() - started, 4),
        "created_at": cv2.utc_now(),
    }


def run_schema_probe(*, api_key: str, output: Path, timeout: int, ledger: "BudgetLedger") -> dict:
    probe_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_ok": {"type": "boolean"},
            "message": {"type": "string"},
        },
        "required": ["schema_ok", "message"],
    }
    prompt = "Return schema_ok=true and a short message confirming strict JSON Schema output."
    payload = {"probe": "synthetic_no_scientific_or_client_data"}
    maximum = maximum_call_cost(prompt, payload, 2000)
    ledger.ensure_call("probe", maximum)
    raw, latency = api_call(
        api_key=api_key, prompt=prompt, payload=payload, schema=probe_schema,
        effort="high", timeout=timeout, max_output_tokens=2000,
    )
    raw_path = output / "preflight" / "schema_probe_raw.json"
    cv2.write_json(raw_path, raw, immutable=True)
    cost = usage_cost(raw.get("usage") or {})
    ledger.record(
        phase="probe", group_id="synthetic", role="schema_probe", attempt=1,
        status=str(raw.get("status") or "response_received"),
        observed=cost["estimated_cost_usd"], maximum_cost=maximum,
    )
    parsed = json.loads(response_text(raw))
    if raw.get("status") != "completed" or parsed.get("schema_ok") is not True:
        raise RuntimeError("synthetic_schema_probe_failed")
    report = {
        "schema_version": "tier1_semantic_schema_probe_v1",
        "passed": True,
        "response_id": raw.get("id", ""),
        "effective_model": raw.get("model", ""),
        "reasoning_effort": "high",
        "latency_seconds": latency,
        "raw_path": str(raw_path),
        **cost,
        "created_at": cv2.utc_now(),
    }
    cv2.write_json(output / "preflight" / "schema_probe_report.json", report, immutable=True)
    return report


def run_role(*, phase: str, group_id: str, role: str, packet: dict, output_dir: Path,
             prompt: str, schema: dict, api_key: str, timeout: int, max_output_tokens: int,
             ledger: BudgetLedger, extra: dict | None = None,
             mandatory_remaining_reserve: float = 0.0) -> tuple[dict, dict, list[str], dict]:
    semantic_path = output_dir / "semantic" / f"{group_id.replace(':', '__')}__{role}.json"
    decision_path = output_dir / "decisions" / f"{group_id.replace(':', '__')}__{role}.json"
    audit_path = output_dir / "audit" / f"{group_id.replace(':', '__')}__{role}.json"
    if semantic_path.exists() and decision_path.exists() and audit_path.exists():
        semantic = cv2.read_json(semantic_path)
        decision = cv2.read_json(decision_path)
        errors = cv2.validate_semantic_assessment(semantic, packet) + cv2.validate_decision(decision, packet)
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
    call_maximum = maximum_call_cost(prompt, payload, max_output_tokens)
    attempts: list[dict] = []
    failures: list[str] = []
    parsed: dict | None = None
    raw: dict | None = None
    latency = 0.0
    for attempt in (1, 2):
        ledger.ensure_call(
            phase, call_maximum,
            mandatory_remaining_reserve=mandatory_remaining_reserve,
        )
        try:
            raw, latency = api_call(
                api_key=api_key, prompt=prompt, payload=payload, schema=schema,
                effort=effort, timeout=timeout, max_output_tokens=max_output_tokens,
            )
            raw_path = _write_raw(output_dir, group_id, role, attempt, raw)
            cost = usage_cost(raw.get("usage") or {})
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
                parsed = json.loads(response_text(raw))
            except Exception as error:  # completed malformed structured output is not a semantic retry
                failures.append(f"attempt_{attempt}:completed_response_parse_failed:{error}")
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1200]
            failures.append(f"attempt_{attempt}:http_{error.code}:{detail}")
            ledger.record(
                phase=phase, group_id=group_id, role=role, attempt=attempt,
                status=f"http_{error.code}", maximum_cost=call_maximum,
            )
            attempts.append({"attempt": attempt, "http_response_received": False, "http_status": error.code, "reasoning_effort": effort, "estimated_cost_usd": None})
            if attempt == 1 and error.code in {408, 409, 429, 500, 502, 503, 504}:
                continue
            break
        except (TimeoutError, socket.timeout) as error:
            failures.append(f"attempt_{attempt}:ambiguous_timeout_no_retry:{error}")
            ledger.record(
                phase=phase, group_id=group_id, role=role, attempt=attempt,
                status="ambiguous_timeout_no_retry", unknown_reserve=call_maximum, maximum_cost=call_maximum,
            )
            attempts.append({"attempt": attempt, "http_response_received": False, "reasoning_effort": effort, "estimated_cost_usd": None, "cost_observability": "reserved_maximum_ambiguous_timeout"})
            break
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout)):
                failures.append(f"attempt_{attempt}:ambiguous_timeout_no_retry:{error}")
                ledger.record(
                    phase=phase, group_id=group_id, role=role, attempt=attempt,
                    status="ambiguous_timeout_no_retry", unknown_reserve=call_maximum, maximum_cost=call_maximum,
                )
                attempts.append({"attempt": attempt, "http_response_received": False, "reasoning_effort": effort, "estimated_cost_usd": None, "cost_observability": "reserved_maximum_ambiguous_timeout"})
                break
            failures.append(f"attempt_{attempt}:transient_connection_error:{error}")
            ledger.record(
                phase=phase, group_id=group_id, role=role, attempt=attempt,
                status="transient_connection_error", maximum_cost=call_maximum,
            )
            attempts.append({"attempt": attempt, "http_response_received": False, "reasoning_effort": effort, "estimated_cost_usd": None})
            if attempt == 1:
                continue
            break

    semantic_errors = ["technical:no_valid_output"] if parsed is None else cv2.validate_semantic_assessment(parsed, packet)
    decision = cv2.normalize_semantic_assessment(parsed, packet) if parsed is not None and not semantic_errors else {}
    final_errors = cv2.validate_decision(decision, packet) if decision else []
    errors = sorted(set(semantic_errors + final_errors))
    audit = {
        "schema_version": "tier1_semantic_role_audit_v1",
        "phase": phase, "group_id": group_id, "role": role,
        "model_id": cv2.MODEL, "effective_model": (raw or {}).get("model", ""),
        "reasoning_effort": effort, "prompt_sha256": cv2.sha256_text(prompt),
        "semantic_schema_sha256": cv2.sha256_json(schema), "final_schema_sha256": cv2.sha256_json(cv2.read_json(FINAL_SCHEMA_PATH)),
        "evidence_sha256": packet.get("packet_sha256"), "attempt_count": len(attempts),
        "attempts": attempts, "technical_failures": failures, "errors": errors,
        "latency_seconds": latency, "response_id": (raw or {}).get("id", ""), "created_at": cv2.utc_now(),
    }
    if parsed is not None:
        cv2.write_json(semantic_path, parsed, immutable=True)
    if decision:
        cv2.write_json(decision_path, decision, immutable=True)
    cv2.write_json(audit_path, audit, immutable=True)
    return parsed or {}, decision, errors, audit


def _base_role_finished(output_dir: Path, group_id: str, role: str) -> bool:
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
            if exclude == (pending_group, pending_role):
                continue
            if _base_role_finished(output_dir, pending_group, pending_role):
                continue
            payload = {"evidence_packet": selected_packet(packets[pending_group], reverse=pending_role == "critic")}
            reserve += maximum_call_cost(prompts[pending_role], payload, max_output_tokens)
    return round(reserve, 8)


def run_group(*, phase: str, phase_groups: tuple[str, ...], group_id: str, packet: dict,
              packets: dict[str, dict], output_dir: Path, prompts: dict[str, str],
              schema: dict, api_key: str, timeout: int, max_output_tokens: int,
              ledger: BudgetLedger, arbiter_count: int,
              prior_disagreements: int) -> tuple[dict, int]:
    record_path = output_dir / "records" / f"{group_id.replace(':', '__')}.json"
    if record_path.exists():
        record = cv2.read_json(record_path)
        return record, arbiter_count + int(bool(record.get("adjudicated")))
    curator_semantic, curator_decision, curator_errors, curator_audit = run_role(
        phase=phase, group_id=group_id, role="curator", packet=packet, output_dir=output_dir,
        prompt=prompts["curator"], schema=schema, api_key=api_key, timeout=timeout,
        max_output_tokens=max_output_tokens, ledger=ledger,
        mandatory_remaining_reserve=pending_base_reserve(
            output_dir=output_dir, groups=phase_groups, packets=packets, prompts=prompts,
            max_output_tokens=max_output_tokens, exclude=(group_id, "curator"),
        ),
    )
    critic_semantic, critic_decision, critic_errors, critic_audit = run_role(
        phase=phase, group_id=group_id, role="critic", packet=packet, output_dir=output_dir,
        prompt=prompts["critic"], schema=schema, api_key=api_key, timeout=timeout,
        max_output_tokens=max_output_tokens, ledger=ledger,
        mandatory_remaining_reserve=pending_base_reserve(
            output_dir=output_dir, groups=phase_groups, packets=packets, prompts=prompts,
            max_output_tokens=max_output_tokens, exclude=(group_id, "critic"),
        ),
    )
    reasons = []
    arbiter_semantic: dict = {}
    arbiter_decision: dict = {}
    arbiter_errors: list[str] = []
    arbiter_audit: dict | None = None
    if not curator_errors and not critic_errors:
        reasons = cv2.semantic_arbitration_reasons(curator_semantic, critic_semantic, curator_decision, critic_decision)
    if reasons:
        if prior_disagreements >= 1:
            arbiter_errors = ["structural:second_base_disagreement_stops_before_arbiter"]
        elif arbiter_count >= MAX_ARBITERS_PER_PHASE:
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
                mandatory_remaining_reserve=pending_base_reserve(
                    output_dir=output_dir, groups=phase_groups, packets=packets,
                    prompts=prompts, max_output_tokens=max_output_tokens,
                ),
            )
            arbiter_count += 1
    final_decision = arbiter_decision if arbiter_decision and not arbiter_errors else (curator_decision if not reasons else {})
    record = {
        "schema_version": "tier1_semantic_group_record_v1",
        "phase": phase, "group_id": group_id, "packet_sha256": packet.get("packet_sha256"),
        "curator_semantic": curator_semantic, "critic_semantic": critic_semantic,
        "arbiter_semantic": arbiter_semantic or None,
        "curator_decision": curator_decision, "critic_decision": critic_decision,
        "arbiter_decision": arbiter_decision or None, "final_decision": final_decision,
        "curator_errors": curator_errors, "critic_errors": critic_errors, "arbiter_errors": arbiter_errors,
        "arbitration_reasons": reasons, "adjudicated": bool(arbiter_decision),
        "base_pair_agreement": bool(curator_decision and critic_decision and cv2.normalized_pair_agreement(curator_decision, critic_decision)),
        "call_audits": [row for row in (curator_audit, critic_audit, arbiter_audit) if row],
    }
    cv2.write_json(record_path, record, immutable=True)
    return record, arbiter_count


def phase_acceptance(records: list[dict], gold: dict, phase: str) -> dict:
    expected = set(cv2.CALIBRATION_GROUPS if phase == "calibration" else cv2.HOLDOUT_GROUPS)
    cards = {row["group_id"]: row for row in gold.get("cards") or []}
    errors: list[str] = []
    pair_agreement = 0
    arbiters = 0
    unsupported = 0
    for record in records:
        group_id = record.get("group_id")
        pair_agreement += int(bool(record.get("base_pair_agreement")))
        arbiters += int(bool(record.get("adjudicated")))
        for role in ("curator", "critic", "arbiter"):
            for error in record.get(f"{role}_errors") or []:
                errors.append(f"{group_id}:{role}:{error}")
                unsupported += int(error == "unsupported_approval")
        final = record.get("final_decision") or {}
        if not final:
            errors.append(f"{group_id}:final_decision_missing")
        else:
            errors.extend(f"{group_id}:{error}" for error in cv2.evaluate_against_gold(final, cards.get(group_id, {})))
    complete = len(records) == len(expected) and {row.get("group_id") for row in records} == expected
    report = {
        "schema_version": "tier1_semantic_phase_acceptance_v1", "phase": phase,
        "complete": complete, "expected_groups": sorted(expected), "pair_agreement": pair_agreement,
        "pair_agreement_required": 5, "arbiter_count": arbiters,
        "arbiter_limit": MAX_ARBITERS_PER_PHASE, "unsupported_approvals": unsupported,
        "error_count": len(errors), "errors": errors,
        "structural_stop_reason": cv2.structural_stop_reason(errors),
    }
    report["passed"] = bool(complete and pair_agreement >= 5 and arbiters <= MAX_ARBITERS_PER_PHASE and unsupported == 0 and not errors)
    return report


def complete_acceptance(calibration: list[dict], holdout: list[dict], gold: dict) -> dict:
    records = calibration + holdout
    cards = {row["group_id"]: row for row in gold.get("cards") or []}
    errors: list[str] = []
    agreement = 0
    for record in records:
        agreement += int(bool(record.get("base_pair_agreement")))
        group_id = record.get("group_id")
        for role in ("curator", "critic", "arbiter"):
            errors.extend(f"{group_id}:{role}:{error}" for error in record.get(f"{role}_errors") or [])
        final = record.get("final_decision") or {}
        if not final:
            errors.append(f"{group_id}:final_decision_missing")
        else:
            errors.extend(f"{group_id}:{error}" for error in cv2.evaluate_against_gold(final, cards.get(group_id, {})))
    complete = len(records) == 12 and {row.get("group_id") for row in records} == set(cv2.GOLD_GROUPS)
    return {
        "schema_version": "tier1_semantic_complete_acceptance_v1",
        "complete": complete, "pair_agreement": agreement, "pair_agreement_required": 11,
        "error_count": len(errors), "errors": errors,
        "passed": bool(complete and agreement >= 11 and not errors),
    }


def historical_regression(*, candidate3_dir: Path, packets: dict[str, dict], gold: dict, output: Path) -> dict:
    cards = {row["group_id"]: row for row in gold.get("cards") or []}
    rows = []
    errors: list[str] = []
    removed_arbitrations = 0
    for group_id in cv2.CALIBRATION_GROUPS:
        old_record = cv2.read_json(candidate3_dir / "records" / f"{group_id.replace(':', '__')}.json")
        role_rows = {}
        for role in ("curator", "critic"):
            semantic = cv2.legacy_decision_to_semantic(old_record[role], packets[group_id])
            semantic_errors = cv2.validate_semantic_assessment(semantic, packets[group_id])
            normalized = cv2.normalize_semantic_assessment(semantic, packets[group_id]) if not semantic_errors else {}
            normalized_errors = cv2.validate_decision(normalized, packets[group_id]) if normalized else []
            role_rows[role] = {"semantic": semantic, "normalized": normalized, "errors": semantic_errors + normalized_errors}
            errors.extend(f"{group_id}:{role}:{error}" for error in semantic_errors + normalized_errors)
        reasons = cv2.semantic_arbitration_reasons(
            role_rows["curator"]["semantic"], role_rows["critic"]["semantic"],
            role_rows["curator"]["normalized"], role_rows["critic"]["normalized"],
        ) if not role_rows["curator"]["errors"] and not role_rows["critic"]["errors"] else ["base_pass_invalid"]
        if old_record.get("adjudicated") and not reasons:
            removed_arbitrations += 1
        final = role_rows["curator"]["normalized"] if not reasons else {}
        gold_errors = cv2.evaluate_against_gold(final, cards[group_id]) if final else ["gold:final_missing"]
        errors.extend(f"{group_id}:{error}" for error in gold_errors)
        rows.append({
            "group_id": group_id, "curator": role_rows["curator"], "critic": role_rows["critic"],
            "new_arbitration_reasons": reasons, "old_adjudicated": bool(old_record.get("adjudicated")),
            "final_decision": final, "gold_errors": gold_errors,
        })
    report = {
        "schema_version": "tier1_candidate3_historical_regression_v1",
        "mode": "historical_regression_only", "source": str(candidate3_dir),
        "groups": rows, "groups_complete": len(rows), "false_arbitrations_removed": removed_arbitrations,
        "error_count": len(errors), "errors": errors,
        "passed": len(rows) == 6 and removed_arbitrations == 4 and not errors,
        "created_at": cv2.utc_now(),
    }
    cv2.write_json(output, report, immutable=True)
    return report


def prompts_and_schema() -> tuple[dict[str, str], dict]:
    prompts = {role: path.read_text(encoding="utf-8") for role, path in PROMPT_PATHS.items()}
    identifier_errors = [error for prompt in prompts.values() for error in cv2.prompt_identifier_errors(prompt)]
    if identifier_errors:
        raise ValueError(f"Semantic prompt contains Gold identifiers: {identifier_errors}")
    return prompts, cv2.read_json(SEMANTIC_SCHEMA_PATH)


def phase_reserve(groups: tuple[str, ...], packets: dict[str, dict], prompts: dict[str, str], max_output_tokens: int) -> float:
    reserves = []
    for group_id in groups:
        for role in ("curator", "critic"):
            payload = {"evidence_packet": selected_packet(packets[group_id], reverse=role == "critic")}
            reserves.append(maximum_call_cost(prompts[role], payload, max_output_tokens))
    worst_base = max(reserves) if reserves else 0
    return round(sum(reserves) + MAX_ARBITERS_PER_PHASE * (worst_base + 0.10), 8)


def freeze_prompt(*, output: Path, prompts: dict[str, str], schema: dict, gold: dict,
                  evidence_manifest: dict, calibration_report: dict, config: dict) -> dict:
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
        "schema_version": "tier1_semantic_frozen_prompt_v1", "created_at": cv2.utc_now(),
        "baseline": "candidate-3-semantic-policy", "candidate": "semantic-v1-verification-1",
        "model": cv2.MODEL, "prompt_sha256": prompt_hashes,
        "prompt_bundle_sha256": cv2.sha256_json(prompt_hashes),
        "semantic_schema_sha256": cv2.sha256_json(schema),
        "final_schema_sha256": cv2.sha256_json(cv2.read_json(FINAL_SCHEMA_PATH)),
        "normalizer_sha256": cv2.sha256_file(ROOT / "curation_v2.py"),
        "gold_sha256": cv2.sha256_json(gold),
        "evidence_manifest_sha256": evidence_manifest.get("manifest_sha256"),
        "calibration_report_sha256": cv2.sha256_json(calibration_report),
        "configuration_sha256": cv2.sha256_json(config),
        "observed_price_snapshot_sha256": cv2.sha256_json(PRICE_SNAPSHOT),
        "reserve_price_snapshot_sha256": cv2.sha256_json(RESERVE_PRICE_SNAPSHOT),
        "normalization_architecture": "semantic_assessment_then_deterministic_normalization",
    }
    cv2.write_json(output / "frozen_prompt_manifest.json", manifest, immutable=True)
    return manifest


def verify_baseline(expected: dict) -> list[str]:
    errors = []
    for key in ("run5", "run6"):
        current = tree_manifest(Path(expected[key]["root"]))
        if current["tree_sha256"] != expected[key]["tree_sha256"]:
            errors.append(f"immutable_tree_changed:{key}")
    registry = expected["active_registry"]
    if cv2.sha256_file(registry["path"]) != registry["sha256"]:
        errors.append("active_registry_changed")
    return errors


def secret_artifact_paths(root: Path, secret: str) -> list[str]:
    if not secret:
        return []
    needle = secret.encode("utf-8")
    leaks = []
    for file in root.rglob("*"):
        if file.is_file() and needle in file.read_bytes():
            leaks.append(str(file.relative_to(root)).replace("\\", "/"))
    return leaks


def run_phase(*, phase: str, groups: tuple[str, ...], output: Path, packets: dict[str, dict],
              prompts: dict[str, str], schema: dict, gold: dict, api_key: str,
              timeout: int, max_output_tokens: int, ledger: BudgetLedger) -> tuple[list[dict], dict]:
    output.mkdir(parents=True, exist_ok=True)
    records = []
    arbiter_count = 0
    disagreements = 0
    for group_id in groups:
        record, arbiter_count = run_group(
            phase=phase, phase_groups=groups, group_id=group_id, packet=packets[group_id],
            packets=packets, output_dir=output,
            prompts=prompts, schema=schema, api_key=api_key, timeout=timeout,
            max_output_tokens=max_output_tokens, ledger=ledger, arbiter_count=arbiter_count,
            prior_disagreements=disagreements,
        )
        records.append(record)
        disagreements += int(not record.get("base_pair_agreement"))
        print(json.dumps({
            "phase": phase, "group": group_id, "complete": len(records),
            "pair_agreement": record.get("base_pair_agreement"), "adjudicated": record.get("adjudicated"),
            "accounted_cost_usd": ledger.accounted,
        }, ensure_ascii=False), flush=True)
        if disagreements > 1 or any(
            "technical:" in error or "ambiguous_timeout" in error or "second_base_disagreement" in error
            for role in ("curator", "critic", "arbiter")
            for error in record.get(f"{role}_errors") or []
        ):
            break
    report = phase_acceptance(records, gold, phase)
    report.update({
        "candidate": "semantic-v1-verification-1", "created_at": cv2.utc_now(),
        "accounted_phase_cost_usd": ledger.phase_accounted(phase),
    })
    cv2.write_json(output / "phase_acceptance.json", report, immutable=True)
    cv2.write_json(output / "records.json", records, immutable=True)
    return records, report


def cmd_historical(args) -> int:
    packets, _ = load_packets(Path(args.evidence_manifest).resolve())
    gold = cv2.read_json(Path(args.gold).resolve())
    report = historical_regression(
        candidate3_dir=Path(args.candidate3_dir).resolve(), packets=packets, gold=gold,
        output=Path(args.output).resolve(),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


def cmd_protocol(args) -> int:
    output = Path(args.output_root).resolve()
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing {args.api_key_env}")
    terminal_path = output / "protocol_terminal_state.json"
    if terminal_path.exists():
        terminal = cv2.read_json(terminal_path)
        print(json.dumps(terminal, ensure_ascii=False))
        return 0 if terminal.get("passed") else 2
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "protocol_configuration.json"
    if config_path.exists():
        config = cv2.read_json(config_path)
    else:
        config = {
            "schema_version": "tier1_semantic_protocol_configuration_v1",
            "campaign_run_id": str(uuid.uuid4()), "candidate": "semantic-v1-verification-1",
            "model": cv2.MODEL, "timeout_seconds": args.timeout,
            "max_output_tokens": args.max_output_tokens,
            "max_api_cost_usd_per_candidate": args.max_candidate_cost,
            "max_api_cost_usd_per_campaign": args.max_campaign_cost,
            "max_arbiters_per_phase": MAX_ARBITERS_PER_PHASE,
            "optimizer_enabled": False, "activation_blocked": True,
        }
        cv2.write_json(config_path, config, immutable=True)
    expected_config = {
        "timeout_seconds": args.timeout, "max_output_tokens": args.max_output_tokens,
        "max_api_cost_usd_per_candidate": args.max_candidate_cost,
        "max_api_cost_usd_per_campaign": args.max_campaign_cost,
    }
    if any(config.get(key) != value for key, value in expected_config.items()):
        raise ValueError("Cannot resume protocol with changed configuration")

    holdout_lock = Path(args.holdout_lock).resolve()
    if holdout_lock.exists():
        existing_lock = cv2.read_json(holdout_lock)
        if existing_lock.get("campaign_run_id") != config["campaign_run_id"]:
            raise ValueError("Holdout is single-use and is already locked by another campaign")

    run5 = Path(args.run5).resolve(); run6 = Path(args.run6).resolve(); registry = Path(args.active_registry).resolve()
    baseline_path = output / "immutability_baseline.json"
    if baseline_path.exists():
        baseline = cv2.read_json(baseline_path)
    else:
        baseline = immutable_baseline(run5, run6, registry)
        if baseline["active_registry"]["sha256"].lower() != args.expected_registry_sha256.lower():
            raise ValueError("Active registry hash does not match the authorized baseline")
        cv2.write_json(baseline_path, baseline, immutable=True)
    initial_immutability_errors = verify_baseline(baseline)
    if initial_immutability_errors:
        raise ValueError(f"Immutable inputs changed before protocol: {initial_immutability_errors}")

    evidence_path = Path(args.evidence_manifest).resolve()
    packets, evidence_manifest = load_packets(evidence_path)
    gold = cv2.read_json(Path(args.gold).resolve())
    gold_errors = cv2.validate_evidence_manifest_artifacts(evidence_path)
    gold_errors.extend(cv2.validate_gold_manifest(gold, packets, evidence_manifest=evidence_manifest))
    if gold_errors:
        raise ValueError(f"Gold/evidence gate failed: {sorted(set(gold_errors))}")
    prompts, schema = prompts_and_schema()
    price_path = output / "price_snapshot.json"
    if not price_path.exists():
        cv2.write_json(
            price_path,
            {
                "schema_version": "tier1_semantic_price_bundle_v1",
                "observed_cost_pricing": PRICE_SNAPSHOT,
                "conservative_budget_reserve_pricing": RESERVE_PRICE_SNAPSHOT,
            },
            immutable=True,
        )

    historical_path = output / "historical_candidate3_regression.json"
    if historical_path.exists():
        historical = cv2.read_json(historical_path)
    else:
        historical = historical_regression(
            candidate3_dir=Path(args.candidate3_dir).resolve(), packets=packets, gold=gold, output=historical_path,
        )
    if not historical.get("passed"):
        terminal = {
            "schema_version": "tier1_semantic_protocol_terminal_v1", "status": "local_historical_regression_failed",
            "passed": False, "holdout_started": False, "historical_regression": str(historical_path),
            "immutability_errors": verify_baseline(baseline), "created_at": cv2.utc_now(),
        }
        cv2.write_json(terminal_path, terminal, immutable=True)
        print(json.dumps(terminal, ensure_ascii=False))
        return 2

    ledger = BudgetLedger(
        output / "budget_ledger.json", candidate_cap=args.max_candidate_cost,
        campaign_cap=args.max_campaign_cost,
    )

    preflight_dir = output / "preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    authentication_path = preflight_dir / "model_authentication.json"
    if authentication_path.exists():
        authentication = cv2.read_json(authentication_path)
    else:
        authentication = authenticate_model(api_key=api_key, timeout=min(args.timeout, 60))
        cv2.write_json(authentication_path, authentication, immutable=True)
    if not authentication.get("authenticated"):
        raise RuntimeError("gpt-5.6-sol_authentication_failed")
    probe_path = preflight_dir / "schema_probe_report.json"
    if not probe_path.exists():
        run_schema_probe(api_key=api_key, output=output, timeout=args.timeout, ledger=ledger)

    calibration_dir = output / "calibration"
    if not (calibration_dir / "phase_acceptance.json").exists() and holdout_lock.exists():
        raise ValueError("Holdout lock exists before calibration completed")

    calibration_reserve = phase_reserve(cv2.CALIBRATION_GROUPS, packets, prompts, args.max_output_tokens)
    ledger.ensure_phase_reserve("calibration", calibration_reserve)
    calibration_records, calibration_report = run_phase(
        phase="calibration", groups=cv2.CALIBRATION_GROUPS, output=calibration_dir,
        packets=packets, prompts=prompts, schema=schema, gold=gold, api_key=api_key,
        timeout=args.timeout, max_output_tokens=args.max_output_tokens, ledger=ledger,
    )
    if not calibration_report.get("passed"):
        secret_leaks = secret_artifact_paths(output, api_key)
        if secret_leaks:
            raise RuntimeError(f"secret_detected_in_artifacts:{secret_leaks}")
        terminal = {
            "schema_version": "tier1_semantic_protocol_terminal_v1", "status": "calibration_failed_no_optimization",
            "passed": False, "candidate": "semantic-v1-verification-1", "holdout_started": False,
            "calibration_report": str(calibration_dir / "phase_acceptance.json"),
            "budget_ledger": str(ledger.path), "immutability_errors": verify_baseline(baseline),
            "secret_scan_passed": True,
            "created_at": cv2.utc_now(),
        }
        cv2.write_json(terminal_path, terminal, immutable=True)
        print(json.dumps(terminal, ensure_ascii=False))
        return 2

    frozen_dir = output / "frozen-prompt"
    if (frozen_dir / "frozen_prompt_manifest.json").exists():
        frozen = cv2.read_json(frozen_dir / "frozen_prompt_manifest.json")
    else:
        frozen = freeze_prompt(
            output=frozen_dir, prompts=prompts, schema=schema, gold=gold,
            evidence_manifest=evidence_manifest, calibration_report=calibration_report, config=config,
        )

    holdout_reserve = phase_reserve(cv2.HOLDOUT_GROUPS, packets, prompts, args.max_output_tokens)
    try:
        ledger.ensure_phase_reserve("holdout", holdout_reserve)
    except RuntimeError as error:
        secret_leaks = secret_artifact_paths(output, api_key)
        if secret_leaks:
            raise RuntimeError(f"secret_detected_in_artifacts:{secret_leaks}") from error
        terminal = {
            "schema_version": "tier1_semantic_protocol_terminal_v1", "status": "holdout_paused_insufficient_budget",
            "passed": False, "candidate": "semantic-v1-verification-1", "holdout_started": False,
            "reason": str(error), "required_holdout_reserve_usd": holdout_reserve,
            "budget_ledger": str(ledger.path), "frozen_prompt": str(frozen_dir),
            "immutability_errors": verify_baseline(baseline), "created_at": cv2.utc_now(),
            "secret_scan_passed": True,
        }
        cv2.write_json(terminal_path, terminal, immutable=True)
        print(json.dumps(terminal, ensure_ascii=False))
        return 2

    lock_body = {
        "schema_version": "tier1_semantic_holdout_single_use_lock_v1",
        "campaign_run_id": config["campaign_run_id"], "campaign_output": str(output),
        "gold_sha256": cv2.sha256_json(gold), "frozen_prompt_sha256": cv2.sha256_json(frozen),
        "created_at": cv2.utc_now(),
    }
    if holdout_lock.exists():
        existing_lock = cv2.read_json(holdout_lock)
        if existing_lock.get("campaign_run_id") != config["campaign_run_id"]:
            raise ValueError("Holdout is single-use and is already locked by another campaign")
    else:
        cv2.write_json(holdout_lock, lock_body, immutable=True)

    holdout_dir = output / "holdout"
    holdout_records, holdout_report = run_phase(
        phase="holdout", groups=cv2.HOLDOUT_GROUPS, output=holdout_dir,
        packets=packets, prompts=prompts, schema=schema, gold=gold, api_key=api_key,
        timeout=args.timeout, max_output_tokens=args.max_output_tokens, ledger=ledger,
    )
    final_report = complete_acceptance(calibration_records, holdout_records, gold)
    final_report.update({
        "calibration_passed": calibration_report.get("passed"),
        "holdout_passed": holdout_report.get("passed"),
        "holdout_single_use_lock": str(holdout_lock),
        "accounted_campaign_cost_usd": ledger.accounted,
        "created_at": cv2.utc_now(),
    })
    if not holdout_report.get("passed") or not final_report.get("passed"):
        final_report["status"] = "holdout_failed_requires_new_unseen_holdout"
        final_report["passed"] = False
    else:
        final_report["status"] = "semantic_verification_and_holdout_passed"
    cv2.write_json(output / "final_evaluation_report.json", final_report, immutable=True)
    immutability_errors = verify_baseline(baseline)
    secret_leaks = secret_artifact_paths(output, api_key)
    if secret_leaks:
        raise RuntimeError(f"secret_detected_in_artifacts:{secret_leaks}")
    terminal = {
        "schema_version": "tier1_semantic_protocol_terminal_v1",
        "status": final_report["status"], "passed": bool(final_report.get("passed") and not immutability_errors),
        "candidate": "semantic-v1-verification-1", "holdout_started": True,
        "calibration_report": str(calibration_dir / "phase_acceptance.json"),
        "frozen_prompt": str(frozen_dir), "holdout_report": str(holdout_dir / "phase_acceptance.json"),
        "final_evaluation_report": str(output / "final_evaluation_report.json"),
        "budget_ledger": str(ledger.path), "immutability_errors": immutability_errors,
        "secret_scan_passed": True,
        "created_at": cv2.utc_now(),
    }
    cv2.write_json(terminal_path, terminal, immutable=True)
    print(json.dumps(terminal, ensure_ascii=False))
    return 0 if terminal["passed"] else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    historical = sub.add_parser("historical-regression")
    historical.add_argument("--candidate3-dir", required=True)
    historical.add_argument("--evidence-manifest", required=True)
    historical.add_argument("--gold", required=True)
    historical.add_argument("--output", required=True)
    historical.set_defaults(func=cmd_historical)

    protocol = sub.add_parser("run-protocol")
    protocol.add_argument("--candidate3-dir", required=True)
    protocol.add_argument("--run5", required=True)
    protocol.add_argument("--run6", required=True)
    protocol.add_argument("--evidence-manifest", required=True)
    protocol.add_argument("--gold", required=True)
    protocol.add_argument("--active-registry", required=True)
    protocol.add_argument("--expected-registry-sha256", required=True)
    protocol.add_argument("--holdout-lock", required=True)
    protocol.add_argument("--output-root", required=True)
    protocol.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    protocol.add_argument("--timeout", type=int, default=600)
    protocol.add_argument("--max-output-tokens", type=int, default=16000)
    protocol.add_argument("--max-candidate-cost", type=float, default=10.0)
    protocol.add_argument("--max-campaign-cost", type=float, default=15.0)
    protocol.set_defaults(func=cmd_protocol)
    return result


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
