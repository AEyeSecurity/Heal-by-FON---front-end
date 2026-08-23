#!/usr/bin/env python3
"""Controlled LLM1 activation, retention, release and automatic rollback guard."""

from __future__ import annotations
import argparse, base64, datetime as dt, json, os, shutil, subprocess, time, urllib.request
from collections import Counter
from pathlib import Path

ALLOWED_CANDIDATES = {"gpt-5.6-luna", "gpt-5.6-terra"}
CONTROL = "gpt-5-mini"
CONTROL_PROFILE = "default_v6"
MODEL_PROFILES = {"gpt-5.6-luna": "luna_v7", "gpt-5.6-terra": "default_v6", CONTROL: CONTROL_PROFILE}
AXES = {"metabolism_nutrients_methylation", "immunity_inflammation", "xenobiotics_pharmacogenomics"}

def now(): return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def read_json(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(tmp, path)
def read_jsonl(path):
    if not Path(path).exists(): return []
    with Path(path).open("r", encoding="utf-8") as handle: return [json.loads(line) for line in handle if line.strip()]
def append_jsonl(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

def set_runtime(env_path: Path, model: str, prompt_profile: str, reasoning_effort: str = "low"):
    if MODEL_PROFILES.get(model) != prompt_profile: raise ValueError(f"Incompatible model/prompt profile: {model}/{prompt_profile}")
    if reasoning_effort != "low": raise ValueError("The frozen Luna v7 benchmark requires reasoning effort low.")
    lines = env_path.read_text(encoding="utf-8").splitlines(); replaced = set(); output = []
    for line in lines:
        if line.startswith("HEAL_LLM1_MODEL="):
            output.append(f"HEAL_LLM1_MODEL={model}"); replaced.add("model")
        elif line.startswith("HEAL_LLM1_PROMPT_PROFILE="):
            output.append(f"HEAL_LLM1_PROMPT_PROFILE={prompt_profile}"); replaced.add("profile")
        elif line.startswith("HEAL_LLM1_REASONING_EFFORT="):
            output.append(f"HEAL_LLM1_REASONING_EFFORT={reasoning_effort}"); replaced.add("effort")
        else: output.append(line)
    if "model" not in replaced: output.append(f"HEAL_LLM1_MODEL={model}")
    if "profile" not in replaced: output.append(f"HEAL_LLM1_PROMPT_PROFILE={prompt_profile}")
    if "effort" not in replaced: output.append(f"HEAL_LLM1_REASONING_EFFORT={reasoning_effort}")
    tmp = env_path.with_suffix(env_path.suffix + ".tmp"); tmp.write_text("\n".join(output) + "\n", encoding="utf-8"); os.replace(tmp, env_path)

def restart(script: str, process_command_match: str = ""):
    if not script: return
    path = Path(script).resolve()
    if not path.is_file() or path.suffix.lower() != ".ps1": raise ValueError("Restart target must be an existing .ps1 file.")
    if not process_command_match: raise ValueError("A narrow process command match is required for service restart.")
    stop_script = "$needle=$env:HEAL_RESTART_PROCESS_MATCH; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like ('*' + $needle + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    child_env = os.environ.copy(); child_env["HEAL_RESTART_PROCESS_MATCH"] = process_command_match
    subprocess.run(["powershell", "-NoProfile", "-Command", stop_script], check=True, env=child_env)
    encoded = base64.b64encode(f"& '{str(path).replace(chr(39), chr(39) * 2)}'".encode("utf-16le")).decode("ascii")
    launch_env = os.environ.copy(); launch_env["HEAL_RESTART_ENCODED"] = encoded; launch_env["HEAL_RESTART_WORKDIR"] = str(path.parent)
    launch_script = "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$env:HEAL_RESTART_ENCODED) -WindowStyle Hidden -WorkingDirectory $env:HEAL_RESTART_WORKDIR"
    subprocess.run(["powershell", "-NoProfile", "-Command", launch_script], check=True, env=launch_env)

def wait_health(url: str, model: str, prompt_profile: str, timeout_seconds: int = 20, reasoning_effort: str = "low"):
    deadline = time.monotonic() + timeout_seconds; last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response: payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") and payload.get("individualInterpretationModel") == model and payload.get("individualInterpretationPromptProfile") == prompt_profile and payload.get("individualInterpretationReasoningEffort") == reasoning_effort: return
            last_error = f"unexpected health contract: model={payload.get('individualInterpretationModel')} profile={payload.get('individualInterpretationPromptProfile')} effort={payload.get('individualInterpretationReasoningEffort')}"
        except Exception as error: last_error = str(error)
        time.sleep(1)
    raise RuntimeError(f"HEAL API did not become healthy with {model}/{prompt_profile}: {last_error}")

def window_status(records):
    valid = [r for r in records if r.get("technical_status") == "valid"]
    coverage = Counter(r.get("axis") for r in valid)
    critical = [r for r in valid if r.get("critical_error_codes")]
    contract_failures = [r for r in valid if not r.get("contract_valid")]
    return {"valid_outputs": len(valid), "axis_coverage": {axis: coverage[axis] for axis in sorted(AXES)}, "critical_count": len(critical), "contract_failure_count": len(contract_failures), "release_ready": len(valid) >= 50 and all(coverage[axis] >= 5 for axis in AXES) and not critical and not contract_failures}

def cmd_activate(args):
    if args.winner not in ALLOWED_CANDIDATES: raise ValueError("Only Luna or Terra can start the candidate surveillance window.")
    expected_profile = MODEL_PROFILES[args.winner]
    if args.prompt_profile != expected_profile: raise ValueError(f"Winner {args.winner} requires prompt profile {expected_profile}.")
    env_path, state_dir = Path(args.env_file).resolve(), Path(args.state_dir).resolve(); state_dir.mkdir(parents=True, exist_ok=False)
    state = {"status": "activating", "candidate": args.winner, "candidate_prompt_profile": args.prompt_profile, "reasoning_effort": "low", "rollback_model": CONTROL, "rollback_prompt_profile": CONTROL_PROFILE, "rollback_reasoning_effort": "low", "env_file": str(env_path), "started_at": now(), "restart_script": str(Path(args.restart_script).resolve()) if args.restart_script else "", "process_command_match": args.process_command_match, "health_url": args.health_url}
    write_json(state_dir / "state.json", state)
    try:
        set_runtime(env_path, args.winner, args.prompt_profile); restart(args.restart_script, args.process_command_match); wait_health(args.health_url, args.winner, args.prompt_profile)
    except Exception:
        set_runtime(env_path, CONTROL, CONTROL_PROFILE)
        restart(args.restart_script, args.process_command_match)
        wait_health(args.health_url, CONTROL, CONTROL_PROFILE)
        state.update({"status": "activation_failed_rolled_back", "failed_at": now()}); write_json(state_dir / "state.json", state)
        raise
    state.update({"status": "collecting", "activated_at": now()}); write_json(state_dir / "state.json", state)
    print(json.dumps({"status": "collecting", "candidate": args.winner, "prompt_profile": args.prompt_profile, "credentials_exposed": False})); return 0

def rollback(state_dir: Path, state: dict, records: list[dict]):
    set_runtime(Path(state["env_file"]), state.get("rollback_model", CONTROL), state.get("rollback_prompt_profile", CONTROL_PROFILE)); quarantine = state_dir / "quarantine"; quarantine.mkdir(exist_ok=True); queue = []
    for record in records:
        output_path = Path(record.get("output_path", ""))
        if output_path.is_file(): shutil.move(str(output_path), quarantine / output_path.name)
        if record.get("payload_path"): queue.append({"payload_path": record["payload_path"], "model": CONTROL, "prompt_profile": CONTROL_PROFILE, "reason": "candidate_critical_error"})
    for item in queue: append_jsonl(state_dir / "regeneration_queue.jsonl", item)
    restart(state.get("restart_script", ""), state.get("process_command_match", "")); wait_health(state.get("health_url", "http://127.0.0.1:8787/api/health"), CONTROL, CONTROL_PROFILE); state.update({"status": "rolled_back", "rolled_back_at": now(), "quarantined_outputs": len(records), "regeneration_queue_count": len(queue)}); write_json(state_dir / "state.json", state)

def cmd_record(args):
    state_dir = Path(args.state_dir).resolve(); state = read_json(state_dir / "state.json")
    if state["status"] != "collecting": raise ValueError(f"Window is not collecting: {state['status']}")
    record = read_json(args.record_json)
    if record.get("axis") not in AXES: raise ValueError("Record axis is not in the three-axis allowlist.")
    record.update({"recorded_at": now(), "candidate": state["candidate"]}); append_jsonl(state_dir / "retained_outputs.jsonl", record)
    records = read_jsonl(state_dir / "retained_outputs.jsonl"); status = window_status(records)
    if status["critical_count"]:
        rollback(state_dir, state, records); print(json.dumps({"status": "rolled_back", **status})); return 3
    if status["release_ready"]:
        state.update({"status": "ready_for_release", "ready_at": now(), "window": status}); write_json(state_dir / "state.json", state)
    print(json.dumps({"status": state["status"], **status})); return 0

def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("activate"); a.add_argument("--winner", required=True); a.add_argument("--prompt-profile", required=True); a.add_argument("--env-file", required=True); a.add_argument("--state-dir", required=True); a.add_argument("--restart-script", default=""); a.add_argument("--process-command-match", default=""); a.add_argument("--health-url", default="http://127.0.0.1:8787/api/health"); a.set_defaults(func=cmd_activate)
    r = sub.add_parser("record"); r.add_argument("--state-dir", required=True); r.add_argument("--record-json", required=True); r.set_defaults(func=cmd_record)
    args = p.parse_args(); return args.func(args)
if __name__ == "__main__": raise SystemExit(main())
