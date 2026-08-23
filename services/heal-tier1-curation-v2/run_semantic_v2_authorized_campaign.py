#!/usr/bin/env python3
"""Restricted end-to-end orchestrator for the authorized Semantic V2 campaign.

The only reachable sequence is local tests, six-case calibration, immutable
freeze, single-use holdout, closed 105-packet preparation, cost gate, full
candidate curation, and audit packaging. It has no activation side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_v2 as cv2  # noqa: E402


SERVICE = Path(__file__).resolve().parent
VERIFIER = SERVICE / "run_semantic_verification_v2.py"
PACKET_BUILDER = SERVICE / "build_full_evidence_manifest_v2.py"


def run_command(command: list[str], *, cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        return int(process.wait())


def state(path: Path, **values) -> None:
    current = cv2.read_json(path) if path.exists() else {
        "schema_version": "tier1_semantic_v2_authorized_orchestration_v1",
        "created_at": cv2.utc_now(), "stages": [],
    }
    current.update(values)
    current["updated_at"] = cv2.utc_now()
    cv2.write_json(path, current)


def package(args: argparse.Namespace, phase: str) -> None:
    if not args.audit_packager:
        return
    command = [
        sys.executable, str(Path(args.audit_packager).resolve()),
        "--verification-campaign", str(Path(args.verification_output).resolve()),
        "--full-campaign", str(Path(args.full_output).resolve()),
        "--gold", str(Path(args.gold).resolve()),
        "--registry", str(Path(args.active_registry).resolve()),
        "--expected-registry-sha256", args.expected_registry_sha256,
        "--output-dir", str(Path(args.audit_output).resolve()),
        "--phase", phase,
    ]
    run_command(command, cwd=Path(args.repo_root).resolve(),
                log_path=Path(args.orchestration_root).resolve() / "audit_packaging.log")


def main() -> int:
    args = parser().parse_args()
    if not os.environ.get(args.api_key_env):
        raise ValueError(f"Missing {args.api_key_env}")
    root = Path(args.orchestration_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "orchestration_state.json"
    registry = Path(args.active_registry).resolve()
    if cv2.sha256_file(registry).lower() != args.expected_registry_sha256.lower():
        raise ValueError("Active registry differs before orchestration")

    test_log = root / "tests.log"
    if not (root / "tests_passed.json").exists():
        test_code = run_command(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=Path(args.repo_root).resolve(), log_path=test_log,
        )
        if test_code:
            state(state_path, status="tests_failed", stopped=True)
            package(args, "tests_failed")
            return test_code
        cv2.write_json(root / "tests_passed.json", {
            "passed": True, "test_log_sha256": cv2.sha256_file(test_log), "completed_at": cv2.utc_now(),
        }, immutable=True)
    state(state_path, status="tests_passed", stopped=False)

    verification = Path(args.verification_output).resolve()
    verification_terminal = verification / "protocol_terminal_state.json"
    if not verification_terminal.exists():
        command = [
            sys.executable, str(VERIFIER), "run-protocol",
            "--campaign-id", args.campaign_id,
            "--evidence-manifest", str(Path(args.signed_evidence_manifest).resolve()),
            "--gold", str(Path(args.gold).resolve()), "--run5", str(Path(args.run5).resolve()),
            "--run6", str(Path(args.run6).resolve()), "--active-registry", str(registry),
            "--expected-registry-sha256", args.expected_registry_sha256,
            "--holdout-lock", str(Path(args.holdout_lock).resolve()),
            "--output-root", str(verification), "--api-key-env", args.api_key_env,
            "--timeout", str(args.timeout), "--max-output-tokens", str(args.max_output_tokens),
            "--max-candidate-cost", "10", "--max-campaign-cost", "15",
        ]
        if verification.exists():
            if not args.resume:
                raise FileExistsError("Verification campaign exists; pass --resume for the same interrupted run")
            command.append("--resume")
        code = run_command(command, cwd=Path(args.repo_root).resolve(), log_path=root / "verification.log")
        if code not in {0, 2}:
            state(state_path, status="verification_interrupted", stopped=True)
            package(args, "verification_interrupted")
            return code
    terminal = cv2.read_json(verification_terminal)
    if not terminal.get("passed"):
        state(state_path, status=terminal.get("status", "verification_failed"), stopped=True)
        package(args, "verification_failed")
        return 2
    state(state_path, status="verification_passed_and_frozen", stopped=False)

    full_evidence = Path(args.full_evidence_output).resolve()
    full_manifest = full_evidence / "evidence_manifest.json"
    if not full_manifest.exists():
        code = run_command([
            sys.executable, str(PACKET_BUILDER),
            "--current-json", str(Path(args.current_json).resolve()),
            "--canon-root", str(Path(args.canon_root).resolve()),
            "--active-registry", str(registry),
            "--signed-gold-manifest", str(Path(args.signed_evidence_manifest).resolve()),
            "--output-dir", str(full_evidence),
            "--publication-limit", str(args.publication_limit),
            "--delay-seconds", str(args.delay_seconds),
        ], cwd=Path(args.repo_root).resolve(), log_path=root / "packet_build.log")
        if code:
            state(state_path, status="packet_manifest_incomplete", stopped=True)
            package(args, "packet_manifest_incomplete")
            return code
    state(state_path, status="packets_105_ready", stopped=False)

    estimate_path = root / "full_cost_estimate.json"
    if not estimate_path.exists():
        code = run_command([
            sys.executable, str(VERIFIER), "estimate-full",
            "--frozen-prompt-dir", str(verification / "frozen-prompt"),
            "--verification-report", str(verification / "final_evaluation_report.json"),
            "--evidence-manifest", str(full_manifest), "--output", str(estimate_path),
        ], cwd=Path(args.repo_root).resolve(), log_path=root / "cost_estimate.log")
        if code:
            state(state_path, status="cost_estimate_above_75", stopped=True)
            package(args, "cost_estimate_above_75")
            return code
    estimate = cv2.read_json(estimate_path)
    if not estimate.get("within_cap") or float(estimate.get("estimated_total_cost_usd") or 0) > 75:
        state(state_path, status="cost_estimate_above_75", stopped=True)
        package(args, "cost_estimate_above_75")
        return 2
    state(state_path, status="cost_estimate_authorized", estimated_cost_usd=estimate["estimated_total_cost_usd"], stopped=False)

    full_output = Path(args.full_output).resolve()
    full_terminal = full_output / "protocol_terminal_state.json"
    if not full_terminal.exists():
        command = [
            sys.executable, str(VERIFIER), "run-full",
            "--frozen-prompt-dir", str(verification / "frozen-prompt"),
            "--verification-report", str(verification / "final_evaluation_report.json"),
            "--evidence-manifest", str(full_manifest), "--gold", str(Path(args.gold).resolve()),
            "--active-registry", str(registry), "--expected-registry-sha256", args.expected_registry_sha256,
            "--output-dir", str(full_output), "--full-campaign-id", args.full_campaign_id,
            "--api-key-env", args.api_key_env, "--timeout", str(args.timeout),
            "--max-output-tokens", str(args.max_output_tokens), "--seed", str(args.seed),
        ]
        if full_output.exists():
            if not args.resume:
                raise FileExistsError("Full campaign exists; pass --resume for the same interrupted run")
            command.append("--resume")
        code = run_command(command, cwd=Path(args.repo_root).resolve(), log_path=root / "full_curation.log")
        if code not in {0, 2}:
            state(state_path, status="full_curation_interrupted", stopped=True)
            package(args, "full_curation_interrupted")
            return code
    full_terminal_body = cv2.read_json(full_terminal)
    state(state_path, status=full_terminal_body.get("status", "full_curation_complete"), stopped=True)
    package(args, "full_curation_complete" if full_terminal_body.get("passed") else "full_curation_incomplete")
    if cv2.sha256_file(registry).lower() != args.expected_registry_sha256.lower():
        raise RuntimeError("Active registry changed during orchestration")
    return 0 if full_terminal_body.get("passed") else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name in (
        "repo_root", "orchestration_root", "campaign_id", "verification_output",
        "signed_evidence_manifest", "gold", "run5", "run6", "active_registry",
        "expected_registry_sha256", "holdout_lock", "current_json", "canon_root",
        "full_evidence_output", "full_campaign_id", "full_output", "audit_output",
    ):
        result.add_argument("--" + name.replace("_", "-"), required=True)
    result.add_argument("--audit-packager")
    result.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY")
    result.add_argument("--timeout", type=int, default=600)
    result.add_argument("--max-output-tokens", type=int, default=16000)
    result.add_argument("--publication-limit", type=int, default=40)
    result.add_argument("--delay-seconds", type=float, default=0.34)
    result.add_argument("--seed", type=int, default=20260822)
    result.add_argument("--resume", action="store_true")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
