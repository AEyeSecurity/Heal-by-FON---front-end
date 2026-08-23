#!/usr/bin/env python3
"""Calibrate Luna v7, run its frozen evaluation, and compare with stored mini outputs."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-grouped-individual-interpretation"
PROMPT = SERVICE / "prompt_grouped_llm1_luna_v7.md"
MODEL = "gpt-5.6-luna"
PROFILE = "luna_v7"
CALIBRATION_CASES = ("MTHFR:T1.1", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


silver = load_module("luna_silver", SERVICE / "silver_standard.py")
benchmark = load_module("luna_benchmark", ROOT / "tools" / "run_llm1_silver_benchmark.py")
blind_scorer = load_module("luna_blind_scorer", ROOT / "tools" / "score_llm1_silver_blind.py")


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def raw_text(output: dict) -> tuple[str, str, str]:
    en_fields = ("interpretation_one_sentence_en", "interpretation_long_en", "technical_interpretation_en", "confidence_rationale_en", "family_notes_en", "recommended_next_review_step_en")
    es_fields = tuple(name[:-2] + "es" for name in en_fields)
    en = " ".join(str(output.get(name, "")) for name in en_fields).lower()
    es = " ".join(str(output.get(name, "")) for name in es_fields).lower()
    return en, es, en + " " + es


def positive_match(pattern: str, text: str) -> bool:
    for sentence in re.split(r"[.!?;\n]+", text):
        if not re.search(pattern, sentence):
            continue
        if re.search(r"\b(no|not|do not|does not|cannot|must not|without|sin|no se|tampoco)\b", sentence):
            continue
        return True
    return False


def behavior_errors(case: str, output: dict) -> list[str]:
    expected = silver.CASE_BEHAVIOR[case]
    errors: list[str] = []
    if output.get("inference_mode") not in expected["mode"]: errors.append("behavior:inference_mode")
    if output.get("final_confidence_level") not in expected["confidence"]: errors.append("behavior:confidence")
    if output.get("review_priority") not in expected["priority"]: errors.append("behavior:review_priority")
    if bool(output.get("myth_correction_required")) != expected["myth"]: errors.append("behavior:myth_flag")
    en, es, combined = raw_text(output)
    next_en = str(output.get("recommended_next_review_step_en", "")).lower()
    next_es = str(output.get("recommended_next_review_step_es", "")).lower()
    referral = r"\b(clinician|bioinformatician|professional|specialist|genetic counselor|medico|bioinformatico|profesional|especialista|asesor genetico)\b"
    new_data = r"\b(obtain|order|measure|test|testing|laborator|study|panel|collect|request|medication history|obtener|pedir|medir|analisis|laboratorio|estudio|panel|recolectar|historial farmacologico)\b"
    drug_action = r"\b(start|stop|change|adjust|dose|take|monitor|iniciar|suspender|cambiar|ajustar|dosificar|tomar|monitorear)\b.{0,80}\b(medication|drug|dose|supplement|medicacion|farmaco|dosis|suplemento)\b"
    if positive_match(referral, next_en + " " + next_es): errors.append("behavior:generic_referral")
    if positive_match(new_data, next_en + " " + next_es): errors.append("behavior:new_data_request")
    if positive_match(drug_action, combined): errors.append("critical:treatment_or_pgx_action")
    if output.get("review_priority") in {"recommended", "urgent"}: errors.append("behavior:over_referral")
    if case == "IL6:T1.4":
        if not ("population" in en and "association" in en and ("poblacional" in es or "poblacion" in es) and "asociacion" in es):
            errors.append("behavior:gwas_population_context_missing")
        if not (re.search(r"(does not|cannot|not establish).{0,60}(individual risk|causal)", en) and re.search(r"(no|tampoco).{0,80}(riesgo individual|causal)", es)):
            errors.append("behavior:gwas_limit_missing")
        if positive_match(r"\b(causes?|causal|predicts?|individual risk|causa|causal|predice|riesgo individual)\b", combined):
            errors.append("critical:gwas_causal_or_individual")
    if case == "IFNG:T3.5" and output.get("review_priority") != "none": errors.append("behavior:ifng_priority")
    if case != "IFNG:T3.5" and output.get("inference_mode") == "abstained_insufficient_evidence": errors.append("behavior:unjustified_abstention")
    return sorted(set(errors))


def prompt_errors(prompt: str) -> list[str]:
    lower = prompt.lower()
    forbidden = [case.lower() for case in silver.PILOT_CASE_IDS] + [case.split(":", 1)[0].lower() for case in silver.PILOT_CASE_IDS]
    return [f"case_specific_prompt:{token}" for token in forbidden if token in lower]


def call_with_technical_retry(api_key: str, payload: dict, prompt: str, schema: dict, timeout: int, max_output_tokens: int):
    failures = []
    for attempt in (1, 2):
        try:
            parsed, raw, latency = benchmark.call_model(api_key, MODEL, prompt, "Interpret this payload. Return only schema-valid JSON.\n\n" + json.dumps(payload, ensure_ascii=False), schema, timeout, max_output_tokens, "common_low")
            return parsed, raw, latency, attempt, failures
        except Exception as error:  # noqa: BLE001
            failures.append(f"attempt_{attempt}:{error}")
    return None, None, 0.0, 2, failures


def latest_five_same_failure(output_root: Path) -> str:
    reports = []
    if output_root.exists():
        for path in sorted(output_root.glob("candidate-*/calibration_report.json"), key=lambda p: p.stat().st_mtime):
            report = json.loads(path.read_text(encoding="utf-8"))
            if not report.get("passed") and report.get("dominant_failure_signature"):
                reports.append(report["dominant_failure_signature"])
    return reports[-1] if len(reports) >= 5 and len(set(reports[-5:])) == 1 else ""


def run_trials(*, bundle: Path, output_dir: Path, cases: tuple[str, ...], repetitions: int, prompt_path: Path, pricing_path: Path, api_key: str, timeout: int, max_output_tokens: int, seed: int) -> list[dict]:
    bundle_failures = benchmark.bundle_errors(bundle, True)
    if bundle_failures: raise ValueError(f"Blocked frozen bundle: {bundle_failures}")
    _, payloads, gold = benchmark.load_bundle(bundle)
    schema = json.loads((bundle / "frozen/schema.json").read_text(encoding="utf-8"))
    prompt = prompt_path.read_text(encoding="utf-8")
    pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    schedule = [{"group_id": case, "repetition": repetition} for case in cases for repetition in range(1, repetitions + 1)]
    random.Random(seed).shuffle(schedule)
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(prompt_path, output_dir / "prompt_snapshot.md")
    shutil.copy2(bundle / "frozen/schema.json", output_dir / "schema_snapshot.json")
    shutil.copy2(pricing_path, output_dir / "pricing_snapshot.json")
    write_json(output_dir / "run_manifest.json", {
        "schema_version": "llm1_luna_v7_frozen_run_v1", "created_at": now(), "model": MODEL,
        "prompt_profile": PROFILE, "reasoning_policy": "common_low", "reasoning_effort": "low",
        "cases": list(cases), "repetitions": repetitions, "expected_outputs": len(cases) * repetitions,
        "prompt_sha256": silver.sha256_file(prompt_path), "schema_sha256": silver.sha256_file(bundle / "frozen/schema.json"),
        "pricing_sha256": silver.sha256_file(pricing_path), "bundle_manifest_sha256": silver.sha256_file(bundle / "manifest.json"),
        "max_output_tokens": max_output_tokens, "timeout_seconds": timeout, "technical_retry_limit": 1,
        "semantic_overrides_applied": False, "semantic_retries_applied": False, "seed": seed,
    })
    rows = []
    for index, trial in enumerate(schedule, 1):
        parsed, raw, latency, attempts, failures = call_with_technical_retry(api_key, payloads[trial["group_id"]], prompt, schema, timeout, max_output_tokens)
        automatic = ["technical:no_valid_output"] if parsed is None else silver.validate_output(parsed, payloads[trial["group_id"]], gold[trial["group_id"]])
        semantic = [] if parsed is None else behavior_errors(trial["group_id"], parsed)
        usage = (raw or {}).get("usage") or {}
        row = {
            "trial_id": f"L{index:03d}", **trial, "model_id": MODEL, "prompt_profile": PROFILE,
            "effective_model": (raw or {}).get("model", ""), "output": parsed,
            "automatic_errors": automatic, "behavior_errors": semantic,
            "contract_valid": not automatic, "calibration_pass": not automatic and not semantic,
            "attempt_count": attempts, "technical_failures": failures,
            "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
            "latency_seconds": latency, "cost_usd": benchmark.calculate_cost(usage, pricing["models"][MODEL]) if raw else 0,
            "payload_sha256": silver.sha256_json(payloads[trial["group_id"]]),
            "prompt_sha256": silver.sha256_file(prompt_path), "schema_sha256": silver.sha256_file(bundle / "frozen/schema.json"),
        }
        rows.append(row)
        if raw is not None: write_json(output_dir / f"raw/{row['trial_id']}.json", raw)
        write_jsonl(output_dir / "outputs.jsonl", rows)
    return rows


def cmd_calibrate(args) -> int:
    output_root = Path(args.output_root).resolve()
    repeated = latest_five_same_failure(output_root)
    if repeated: raise ValueError(f"Calibration paused after five consecutive candidates failed for: {repeated}")
    prompt = Path(args.prompt).resolve()
    generic_errors = prompt_errors(prompt.read_text(encoding="utf-8"))
    if generic_errors: raise ValueError(f"Prompt contains pilot-specific identifiers: {generic_errors}")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key: raise ValueError(f"Missing {args.api_key_env}")
    output_dir = output_root / args.candidate
    rows = run_trials(bundle=Path(args.bundle).resolve(), output_dir=output_dir, cases=CALIBRATION_CASES, repetitions=3, prompt_path=prompt, pricing_path=Path(args.pricing).resolve(), api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens, seed=args.seed)
    failures = Counter(error for row in rows for error in row["automatic_errors"] + row["behavior_errors"])
    report = {
        "schema_version": "llm1_luna_v7_calibration_v1", "candidate": args.candidate, "created_at": now(),
        "model": MODEL, "prompt_profile": PROFILE, "prompt_sha256": silver.sha256_file(prompt),
        "calls": len(rows), "passed_calls": sum(row["calibration_pass"] for row in rows), "passed": len(rows) == 12 and all(row["calibration_pass"] for row in rows),
        "failure_counts": dict(failures), "dominant_failure_signature": failures.most_common(1)[0][0] if failures else "",
        "semantic_overrides_applied": False, "semantic_retries_applied": False, "technical_retry_limit": 1,
    }
    write_json(output_dir / "calibration_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


def score_record(row: dict) -> dict:
    scored = blind_scorer.score({"output": row["output"], "group_id": row["group_id"], "model_alias": "blinded", "automatic_errors": row.get("automatic_errors") or []})
    critical = [item for item in scored["critical_error_codes"].split("|") if item]
    if "critical:treatment_or_pgx_action" in (row.get("behavior_errors") or []):
        critical.append("treatment_medication_dose_or_supplement_recommendation")
    if "critical:gwas_causal_or_individual" in (row.get("behavior_errors") or []):
        critical.append("gwas_presented_as_causal_or_individual_risk")
    return {
        "trial_id": row["trial_id"], "group_id": row["group_id"], "repetition": int(row["repetition"]),
        "weighted_score": silver.weighted_score({name: scored[name] for name in silver.RUBRIC_WEIGHTS}),
        "contract_valid": bool(row.get("contract_valid")), "critical_error_codes": sorted(set(critical)),
        "unjustified_over_referral": scored["unjustified_over_referral"] == "true" or "behavior:over_referral" in (row.get("behavior_errors") or []) or "behavior:generic_referral" in (row.get("behavior_errors") or []),
        "unjustified_abstention": scored["unjustified_abstention"] == "true" or "behavior:unjustified_abstention" in (row.get("behavior_errors") or []),
        "inference_mode": row["output"].get("inference_mode"), "final_confidence_level": row["output"].get("final_confidence_level"), "review_priority": row["output"].get("review_priority"),
        "cost_usd": float(row.get("cost_usd", 0)), "latency_seconds": float(row.get("latency_seconds", 0)),
    }


def cmd_evaluate(args) -> int:
    calibration = json.loads(Path(args.calibration_report).read_text(encoding="utf-8"))
    prompt = Path(args.prompt).resolve()
    if not calibration.get("passed"): raise ValueError("The referenced calibration did not pass 12/12.")
    if calibration.get("prompt_sha256") != silver.sha256_file(prompt): raise ValueError("Prompt changed after calibration.")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key: raise ValueError(f"Missing {args.api_key_env}")
    output_dir = Path(args.output_dir).resolve()
    rows = run_trials(bundle=Path(args.bundle).resolve(), output_dir=output_dir, cases=silver.PILOT_CASE_IDS, repetitions=5, prompt_path=prompt, pricing_path=Path(args.pricing).resolve(), api_key=api_key, timeout=args.timeout, max_output_tokens=args.max_output_tokens, seed=args.seed)
    scores = [score_record(row) for row in rows]
    write_jsonl(output_dir / "final_scores.jsonl", scores)
    aggregate = silver.aggregate_model(scores)
    write_json(output_dir / "evaluation_report.json", {"schema_version": "llm1_luna_v7_evaluation_v1", "created_at": now(), "model": MODEL, "prompt_profile": PROFILE, "holdout_case": "PEMT:T1.3", "prompt_sha256": silver.sha256_file(prompt), "outputs": len(rows), "aggregate": aggregate})
    print(json.dumps(aggregate, ensure_ascii=False))
    return 0


def stored_mini_rows(run_dir: Path, bundle: Path) -> list[dict]:
    key = json.loads((run_dir / "internal/model_key.json").read_text(encoding="utf-8"))
    aliases = [alias for alias, model in key.items() if model == "gpt-5-mini"]
    if len(aliases) != 1: raise ValueError("Stored run does not contain exactly one mini alias.")
    alias = aliases[0]
    _, payloads, gold = benchmark.load_bundle(bundle)
    audit = {row["trial_id"]: row for row in benchmark.read_csv(run_dir / "internal/call_audit.csv")}
    rows = []
    for source in benchmark.read_jsonl(run_dir / "blind/outputs.jsonl"):
        if source["model_alias"] != alias: continue
        output = source["output"]
        automatic = silver.validate_output(output, payloads[source["group_id"]], gold[source["group_id"]])
        semantic = behavior_errors(source["group_id"], output)
        call = audit[source["trial_id"]]
        rows.append({"trial_id": source["trial_id"], "group_id": source["group_id"], "repetition": source["repetition"], "output": output, "automatic_errors": automatic, "behavior_errors": semantic, "contract_valid": not automatic, "cost_usd": float(call["cost_usd"]), "latency_seconds": float(call["latency_seconds"])})
    if len(rows) != 25: raise ValueError(f"Expected 25 stored mini outputs; found {len(rows)}")
    return rows


def stability_ok(records: list[dict]) -> bool:
    states = defaultdict(Counter)
    for row in records:
        priority = row.get("review_priority")
        band = "non_required" if priority in {"none", "optional_contextual"} else priority
        states[row["group_id"]][(row.get("inference_mode"), row.get("final_confidence_level"), band)] += 1
    return all(max(states[case].values(), default=0) >= 4 for case in silver.PILOT_CASE_IDS)


def cmd_compare(args) -> int:
    luna_dir = Path(args.luna_run).resolve()
    luna_scores = benchmark.read_jsonl(luna_dir / "final_scores.jsonl")
    mini_raw = stored_mini_rows(Path(args.mini_run).resolve(), Path(args.bundle).resolve())
    mini_scores = [score_record(row) for row in mini_raw]
    mini_aggregate, luna_aggregate = silver.aggregate_model(mini_scores), silver.aggregate_model(luna_scores)
    aggregates = {"gpt-5-mini": mini_aggregate, MODEL: luna_aggregate}
    records = {"gpt-5-mini": mini_scores, MODEL: luna_scores}
    decision = silver.provisional_decision(aggregates, records)
    both_eligible = mini_aggregate["eligible"] and luna_aggregate["eligible"]
    quality_gap = abs(luna_aggregate["overall_score"] - mini_aggregate["overall_score"])
    interval = silver.bootstrap_difference(
        [row["weighted_score"] for row in sorted(luna_scores, key=lambda r: (r["group_id"], r["repetition"]))],
        [row["weighted_score"] for row in sorted(mini_scores, key=lambda r: (r["group_id"], r["repetition"]))],
    )
    rerun_reasons = []
    if not stability_ok(mini_scores): rerun_reasons.append("stored_mini_stability_below_4_of_5")
    if both_eligible and quality_gap < 5: rerun_reasons.append("eligible_quality_gap_below_5")
    if both_eligible and interval["low"] <= 0 <= interval["high"]: rerun_reasons.append("bootstrap_interval_crosses_zero")
    if both_eligible and decision.get("basis", "").startswith("equivalent_quality_"): rerun_reasons.append("operational_tiebreak_requires_same_window")
    if rerun_reasons:
        decision = {"winner": None, "basis": "mini_rerun_required_before_final_decision", "provisional": True}
    report = {
        "title": "silver benchmark provisional autoevaluado - Luna v7 vs mini almacenado", "created_at": now(),
        "models": aggregates, "luna_minus_mini_bootstrap_95": interval, "decision": decision,
        "mini_rerun_required": bool(rerun_reasons), "mini_rerun_reasons": rerun_reasons,
        "stored_mini_cost_latency_are_orientative": True,
        "limitations": ["Five cases do not validate all LLM1 behavior.", "The positive PGx case remains pending.", "Independent genetic, bioinformatic, or clinical validation has not occurred."],
    }
    write_json(Path(args.output).resolve(), report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--bundle", required=True); c.add_argument("--pricing", required=True); c.add_argument("--output-root", required=True); c.add_argument("--candidate", required=True)
    c.add_argument("--prompt", default=str(PROMPT)); c.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); c.add_argument("--timeout", type=int, default=120); c.add_argument("--max-output-tokens", type=int, default=6000); c.add_argument("--seed", type=int, default=20260804); c.set_defaults(func=cmd_calibrate)
    e = sub.add_parser("evaluate")
    e.add_argument("--bundle", required=True); e.add_argument("--pricing", required=True); e.add_argument("--calibration-report", required=True); e.add_argument("--output-dir", required=True)
    e.add_argument("--prompt", default=str(PROMPT)); e.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); e.add_argument("--timeout", type=int, default=120); e.add_argument("--max-output-tokens", type=int, default=6000); e.add_argument("--seed", type=int, default=20260805); e.set_defaults(func=cmd_evaluate)
    x = sub.add_parser("compare")
    x.add_argument("--luna-run", required=True); x.add_argument("--mini-run", required=True); x.add_argument("--bundle", required=True); x.add_argument("--output", required=True); x.set_defaults(func=cmd_compare)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
