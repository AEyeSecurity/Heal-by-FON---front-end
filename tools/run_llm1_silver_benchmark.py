#!/usr/bin/env python3
"""Freeze, probe, run, blind-score and reveal the three-model LLM1 benchmark."""

from __future__ import annotations

import argparse, csv, datetime as dt, importlib.util, json, os, random, shutil, statistics, time, urllib.error, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-grouped-individual-interpretation"
DEFAULT_PROMPT = SERVICE / "prompt_grouped_llm1_v6.md"
DEFAULT_SCHEMA = SERVICE / "grouped_gene_module_interpretation_v6_schema.json"
SPEC = importlib.util.spec_from_file_location("silver", SERVICE / "silver_standard.py")
silver = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(silver)
RESPONSES_URL = "https://api.openai.com/v1/responses"

def utc_now(): return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def read_json(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
def write_jsonl(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows: handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
def read_jsonl(path): return silver.read_jsonl(Path(path))
def write_csv(path, rows, fields):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle: return list(csv.DictReader(handle))

def response_text(response):
    texts = [content["text"] for output in response.get("output") or [] for content in output.get("content") or [] if content.get("type") in {"output_text", "text"} and content.get("text")]
    if not texts: raise ValueError("Responses API result contains no output text.")
    return "\n".join(texts)

def reasoning_effort(policy, model):
    if policy == "planned_none": return "none"
    if policy == "common_low": return "low"
    if policy == "minimum_supported": return "minimal" if model == "gpt-5-mini" else "none"
    raise ValueError(f"Unknown reasoning policy: {policy}")

def call_model(api_key, model, prompt, user_text, schema, timeout, max_output_tokens, policy="planned_none"):
    body = {"model": model, "store": False, "reasoning": {"effort": reasoning_effort(policy, model)}, "max_output_tokens": max_output_tokens,
            "input": [{"role": "system", "content": prompt}, {"role": "user", "content": user_text}],
            "text": {"format": {"type": "json_schema", "name": "heal_llm1_v6", "strict": True, "schema": schema}}}
    request = urllib.request.Request(RESPONSES_URL, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response: raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Responses API http_{error.code}: {error.read().decode('utf-8', errors='replace')[:1500]}") from error
    return json.loads(response_text(raw)), raw, round(time.perf_counter() - started, 4)

def schedule(models_by_alias, seed):
    rows = [{"model_alias": alias, "group_id": case, "repetition": repetition} for alias in sorted(models_by_alias) for case in silver.PILOT_CASE_IDS for repetition in range(1, 6)]
    random.Random(seed).shuffle(rows)
    for index, row in enumerate(rows, 1): row["trial_id"] = f"T{index:03d}"
    return rows

def load_bundle(path):
    path = Path(path); manifest = read_json(path / "manifest.json")
    payloads = {row["group_id"]: read_json(path / row["payload_path"]) for row in manifest["cases"]}
    gold = {row["group_id"]: read_json(path / row["gold_path"]) for row in manifest["cases"]}
    return manifest, payloads, gold

def bundle_errors(path, approved):
    path = Path(path); manifest, payloads, gold = load_bundle(path); errors = []
    if silver.sha256_file(path / manifest["prompt_path"]) != manifest["prompt_sha256"]: errors.append("prompt_hash_mismatch")
    if silver.sha256_file(path / manifest["schema_path"]) != manifest["schema_sha256"]: errors.append("schema_hash_mismatch")
    if approved and manifest.get("approved_reasoning_policy") not in manifest.get("reasoning_policy_options", []): errors.append("reasoning_policy_not_approved")
    for case in silver.PILOT_CASE_IDS:
        errors += [f"{case}:{x}" for x in silver.payload_preflight(payloads[case])]
        errors += [f"{case}:{x}" for x in silver.validate_gold_case(gold[case], payloads[case], approved)]
    return errors

def cmd_prepare(args):
    payloads = {row["group_id"]: row for row in read_jsonl(args.payloads) if row.get("group_id") in silver.PILOT_CASE_IDS}
    if set(payloads) != set(silver.PILOT_CASE_IDS): raise ValueError(f"Expected exactly {silver.PILOT_CASE_IDS}; found {sorted(payloads)}")
    out = Path(args.output_dir).resolve()
    if out.exists() and any(out.iterdir()): raise ValueError("Bundle directory must be empty.")
    (out / "frozen/payloads").mkdir(parents=True); (out / "gold").mkdir()
    shutil.copy2(args.prompt, out / "frozen/prompt.md"); shutil.copy2(args.schema, out / "frozen/schema.json")
    cases = []
    for case, payload in payloads.items():
        name = case.replace(":", "__"); write_json(out / f"frozen/payloads/{name}.json", payload); write_json(out / f"gold/{name}.json", silver.build_gold_case(payload))
        cases.append({"group_id": case, "payload_path": f"frozen/payloads/{name}.json", "payload_sha256": silver.sha256_json(payload), "gold_path": f"gold/{name}.json"})
    manifest = {"schema_version": "llm1_silver_benchmark_v2", "evidence_cutoff": "2026-07-28", "created_at": utc_now(),
                "prompt_path": "frozen/prompt.md", "schema_path": "frozen/schema.json", "cases": cases,
                "models": list(silver.MODELS), "reasoning_policy_status": "pending_manual_choice",
                "reasoning_policy_options": ["common_low", "minimum_supported"], "approved_reasoning_policy": "", "repetitions": 5, "expected_outputs": 75}
    manifest["prompt_sha256"] = silver.sha256_file(out / manifest["prompt_path"]); manifest["schema_sha256"] = silver.sha256_file(out / manifest["schema_path"])
    write_json(out / "rubric.json", {"weights": silver.RUBRIC_WEIGHTS, "scale": [0, 1, 2, 3], "critical_errors": silver.CRITICAL_ERRORS})
    write_json(out / "manifest.json", manifest)
    errors = bundle_errors(out, False); print(json.dumps({"status": "awaiting_behavior_approval" if not errors else "blocked", "errors": errors, "bundle": str(out)})); return 0 if not errors else 2

def cmd_probe(args):
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key: raise ValueError(f"Missing {args.api_key_env}")
    schema = read_json(args.schema); results = []
    probe_text = "Return a schema-valid synthetic contract probe. Use group_id=PROBE:T0, gene=PROBE, module_id=T0, zero counts, inference_mode=abstained_insufficient_evidence, interpretation_scope=abstained_insufficient_evidence, final_confidence_level=Abstain, review_priority=none, empty arrays, myth_correction_required=false, requires_professional_review=false, group_conflict_flag=false, and short paired English/Spanish text. This contains no real patient data."
    for model in silver.MODELS:
        try:
            parsed, raw, latency = call_model(api_key, model, "Return only JSON matching the supplied schema.", probe_text, schema, args.timeout, 2000, args.reasoning_policy)
            valid = set(parsed) == set(schema.get("required") or []) and parsed.get("group_id") == "PROBE:T0"
            results.append({"model_alias": model, "effective_model": raw.get("model"), "reasoning_effort": reasoning_effort(args.reasoning_policy, model), "same_strict_schema": True, "valid": valid, "usage": raw.get("usage"), "latency_seconds": latency})
        except Exception as error: results.append({"model_alias": model, "valid": False, "error": str(error)})
    write_json(args.output, {"created_at": utc_now(), "reasoning_policy": args.reasoning_policy, "results": results}); print(json.dumps(results)); return 0 if all(row["valid"] and row.get("usage") for row in results) else 2

def cmd_approve(args):
    bundle = Path(args.bundle).resolve()
    manifest, payloads, gold = load_bundle(bundle)
    errors = bundle_errors(bundle, False)
    if errors: raise ValueError(f"Cannot approve invalid bundle: {errors}")
    approved_at = utc_now()
    for group_id, case in gold.items():
        case["approval_status"] = "approved"
        case["approval_owner"] = args.approval_owner
        case["approval_timestamp"] = approved_at
        write_json(bundle / next(row["gold_path"] for row in manifest["cases"] if row["group_id"] == group_id), case)
    manifest["reasoning_policy_status"] = "manually_approved"
    manifest["approved_reasoning_policy"] = args.reasoning_policy
    manifest["approval_owner"] = args.approval_owner
    manifest["approval_timestamp"] = approved_at
    manifest["approval_reference"] = args.approval_reference
    write_json(bundle / "manifest.json", manifest)
    approved_errors = bundle_errors(bundle, True)
    print(json.dumps({"status": "approved" if not approved_errors else "blocked", "errors": approved_errors, "bundle": str(bundle)}))
    return 0 if not approved_errors else 2

def calculate_cost(usage, price):
    return round(int(usage.get("input_tokens", 0)) * float(price["input_per_million_usd"]) / 1e6 + int(usage.get("output_tokens", 0)) * float(price["output_per_million_usd"]) / 1e6, 8)

def normalize_contract_lists(output):
    if not isinstance(output, dict): return output, []
    changes = []
    for field in ("focus_variant_refs", "review_reason_codes"):
        values = output.get(field)
        if isinstance(values, list):
            deduplicated = list(dict.fromkeys(values))
            if deduplicated != values:
                output[field] = deduplicated; changes.append(f"deduplicated:{field}")
    return output, changes

def cmd_run(args):
    bundle = Path(args.bundle); errors = bundle_errors(bundle, True)
    if errors: raise ValueError(f"Blocked bundle: {errors}")
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key: raise ValueError(f"Missing {args.api_key_env}")
    pricing = read_json(args.pricing); manifest, payloads, gold = load_bundle(bundle)
    if args.reasoning_policy != manifest.get("approved_reasoning_policy"): raise ValueError("Requested reasoning policy differs from the approved frozen bundle.")
    prompt = (bundle / manifest["prompt_path"]).read_text(encoding="utf-8"); schema = read_json(bundle / manifest["schema_path"])
    aliases = ["Modelo A", "Modelo B", "Modelo C"]; random.SystemRandom().shuffle(aliases); key = dict(zip(aliases, silver.MODELS)); trials = schedule(key, args.seed)
    prompt_hash, schema_hash = silver.sha256_file(bundle / manifest["prompt_path"]), silver.sha256_file(bundle / manifest["schema_path"])
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=False); write_json(out / "internal/model_key.json", key); write_json(out / "internal/schedule.json", trials); write_json(out / "internal/pricing_snapshot.json", pricing)
    blind, audit = [], []
    for trial in trials:
        model, parsed, raw, latency, failures = key[trial["model_alias"]], None, None, 0, []
        for attempt in (1, 2):
            try:
                parsed, raw, latency = call_model(api_key, model, prompt, "Interpret this payload. Return only schema-valid JSON.\n\n" + json.dumps(payloads[trial["group_id"]], ensure_ascii=False), schema, args.timeout, args.max_output_tokens, args.reasoning_policy); break
            except Exception as error: failures.append(f"attempt_{attempt}:{error}")
        parsed, normalizations = normalize_contract_lists(parsed)
        errors = ["technical:no_valid_output"] if parsed is None else silver.validate_output(parsed, payloads[trial["group_id"]], gold[trial["group_id"]])
        if parsed is not None:
            parsed["requires_professional_review"] = silver.expected_professional_review(parsed["review_priority"]); parsed["interpretation_provenance"] = "llm_generated"; parsed["disclaimer_required"] = True
            write_json(out / f"internal/raw/{trial['trial_id']}.json", raw)
        usage = (raw or {}).get("usage") or {}; price = pricing["models"][model]
        blind.append({**trial, "output": parsed, "contract_valid": not errors, "automatic_errors": errors, "deterministic_normalizations": normalizations})
        audit.append({**trial, "model_id": model, "effective_model": (raw or {}).get("model", ""), "attempt_count": len(failures) + (1 if raw else 0), "contract_valid": not errors,
                      "prompt_sha256": prompt_hash, "schema_sha256": schema_hash, "payload_sha256": silver.sha256_json(payloads[trial["group_id"]]),
                      "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0), "latency_seconds": latency,
                      "cost_usd": calculate_cost(usage, price) if raw else 0, "error": " | ".join(failures + errors)})
    write_jsonl(out / "blind/outputs.jsonl", blind); write_csv(out / "internal/call_audit.csv", audit, list(audit[0]))
    fields = ["trial_id", "group_id", "model_alias", *silver.RUBRIC_WEIGHTS, "critical_error_codes", "unjustified_over_referral", "unjustified_abstention", "reviewer_notes"]
    for pass_no, seed in ((1, args.seed + 101), (2, args.seed + 202)):
        rows = [{"trial_id": row["trial_id"], "group_id": row["group_id"], "model_alias": row["model_alias"]} for row in blind]; random.Random(seed).shuffle(rows); write_csv(out / f"blind/score_pass_{pass_no}.csv", rows, fields)
    write_json(out / "run_manifest.json", {"run_id": out.name, "created_at": utc_now(), "expected_outputs": 75, "valid_outputs": sum(row["contract_valid"] for row in blind), "reasoning_policy": args.reasoning_policy, "reasoning_efforts": {model: reasoning_effort(args.reasoning_policy, model) for model in silver.MODELS}, "seed": args.seed, "bundle_manifest_sha256": silver.sha256_file(bundle / "manifest.json"), "prompt_sha256": prompt_hash, "schema_sha256": schema_hash, "pricing_sha256": silver.sha256_file(Path(args.pricing))}); return 0

def parse_bool(value): return str(value).strip().lower() in {"true", "1", "yes"}
def scored_rows(path):
    rows = {}
    for row in read_csv(path):
        scores = {name: float(row[name]) for name in silver.RUBRIC_WEIGHTS}
        rows[row["trial_id"]] = {**row, "scores": scores, "weighted_score": silver.weighted_score(scores)}
    return rows

def cmd_score(args):
    root = Path(args.run_dir); first, second = scored_rows(root / "blind/score_pass_1.csv"), scored_rows(root / "blind/score_pass_2.csv"); needs = []
    for trial in first:
        if abs(first[trial]["weighted_score"] - second[trial]["weighted_score"]) > 5 or any(abs(first[trial]["scores"][d] - second[trial]["scores"][d]) > 1 for d in silver.RUBRIC_WEIGHTS): needs.append(trial)
    adjudication_path = root / "blind/score_adjudication.csv"; fields = ["trial_id", "group_id", "model_alias", *silver.RUBRIC_WEIGHTS, "critical_error_codes", "unjustified_over_referral", "unjustified_abstention", "reviewer_notes"]
    if needs and not adjudication_path.exists(): write_csv(adjudication_path, [{"trial_id": t, "group_id": first[t]["group_id"], "model_alias": first[t]["model_alias"]} for t in needs], fields); print(json.dumps({"status": "adjudication_required", "trials": needs})); return 2
    adjudicated = scored_rows(adjudication_path) if needs else {}; blind = {row["trial_id"]: row for row in read_jsonl(root / "blind/outputs.jsonl")}; audit = {row["trial_id"]: row for row in read_csv(root / "internal/call_audit.csv")}; final = []
    for trial in first:
        row = adjudicated.get(trial)
        if not row:
            row = dict(first[trial]); row["weighted_score"] = round(statistics.mean([first[trial]["weighted_score"], second[trial]["weighted_score"]]), 4)
        output = blind[trial].get("output") or {}; call = audit[trial]
        final.append({"trial_id": trial, "group_id": row["group_id"], "model_alias": row["model_alias"], "repetition": int(blind[trial]["repetition"]), "weighted_score": row["weighted_score"],
                      "contract_valid": blind[trial]["contract_valid"], "critical_error_codes": [x for x in str(row.get("critical_error_codes", "")).split("|") if x],
                      "unjustified_over_referral": parse_bool(row.get("unjustified_over_referral")), "unjustified_abstention": parse_bool(row.get("unjustified_abstention")),
                      "inference_mode": output.get("inference_mode"), "final_confidence_level": output.get("final_confidence_level"), "review_priority": output.get("review_priority"),
                      "cost_usd": float(call["cost_usd"]), "latency_seconds": float(call["latency_seconds"])})
    write_jsonl(root / "blind/final_scores.jsonl", final); write_json(root / "blind/scoring_lock.json", {"locked_at": utc_now(), "adjudicated_trials": needs, "sha256": silver.sha256_file(root / "blind/final_scores.jsonl")}); return 0

def cmd_revalidate(args):
    root, bundle = Path(args.run_dir).resolve(), Path(args.bundle).resolve()
    manifest, payloads, gold = load_bundle(bundle)
    output_path = root / "blind/outputs.jsonl"
    rows = read_jsonl(output_path)
    if len(rows) != 75:
        raise ValueError(f"Expected 75 stored outputs; found {len(rows)}")
    backup = root / "blind/outputs.pre-validator-amendment.jsonl"
    if backup.exists() and not args.supersede:
        raise ValueError("Validator amendment was already applied; use --supersede only for a documented validator correction.")
    before_hash = silver.sha256_file(output_path)
    if not backup.exists():
        shutil.copy2(output_path, backup)
    elif (root / "validator_amendment.json").exists():
        shutil.copy2(root / "validator_amendment.json", root / "validator_amendment.previous.json")
    before_valid = sum(bool(row.get("contract_valid")) for row in rows)
    normalization_count = 0
    for row in rows:
        output, normalizations = normalize_contract_lists(row.get("output"))
        row["output"] = output
        row["deterministic_normalizations"] = sorted(set((row.get("deterministic_normalizations") or []) + normalizations))
        normalization_count += len(normalizations)
        errors = ["technical:no_valid_output"] if output is None else silver.validate_output(output, payloads[row["group_id"]], gold[row["group_id"]])
        row["automatic_errors"] = errors
        row["contract_valid"] = not errors
    write_jsonl(output_path, rows)
    validation_by_trial = {row["trial_id"]: list(row["automatic_errors"]) for row in rows}
    audit_path = root / "internal/call_audit.csv"
    audit_rows = read_csv(audit_path)
    for audit_row in audit_rows:
        validation_errors = validation_by_trial[audit_row["trial_id"]]
        technical_errors = [part.strip() for part in str(audit_row.get("error", "")).split(" | ") if part.strip().startswith("attempt_")]
        audit_row["contract_valid"] = str(not validation_errors).lower()
        audit_row["error"] = " | ".join(technical_errors + validation_errors)
    write_csv(audit_path, audit_rows, list(audit_rows[0]))
    after_valid = sum(bool(row.get("contract_valid")) for row in rows)
    write_json(root / "validator_amendment.json", {
        "schema_version": "llm1_silver_validator_amendment_v1", "applied_at": utc_now(),
        "reason": "Evidence citations were incorrectly checked against focus-only variant references instead of every exact variant reference present in the payload.",
        "scientific_inputs_changed": False, "raw_model_outputs_changed": False,
        "validated_outputs_deterministically_normalized": normalization_count > 0,
        "supersedes_previous_amendment": bool(args.supersede),
        "before_outputs_sha256": before_hash, "backup_path": str(backup),
        "after_outputs_sha256": silver.sha256_file(output_path),
        "valid_outputs_before": before_valid, "valid_outputs_after": after_valid,
        "code_rule": "evidence_used.variant_ref -> all payload variant refs; focus_variant_refs -> focus-only refs",
        "deterministic_list_normalizations": normalization_count,
        "bundle_manifest_sha256": silver.sha256_file(bundle / "manifest.json"),
    })
    run_manifest = read_json(root / "run_manifest.json")
    run_manifest["valid_outputs"] = after_valid
    run_manifest["validator_amendment"] = "validator_amendment.json"
    write_json(root / "run_manifest.json", run_manifest)
    print(json.dumps({"status": "revalidated", "valid_before": before_valid, "valid_after": after_valid}))
    return 0

def cmd_reveal(args):
    root = Path(args.run_dir)
    if not (root / "blind/scoring_lock.json").exists(): raise ValueError("Scoring must be locked before reveal.")
    key = read_json(root / "internal/model_key.json"); rows = read_jsonl(root / "blind/final_scores.jsonl"); by_model = defaultdict(list)
    for row in rows: by_model[key[row["model_alias"]]].append(row)
    aggregates = {model: silver.aggregate_model(records) for model, records in by_model.items()}; decision = silver.provisional_decision(aggregates, by_model)
    write_json(root / "provisional_report.json", {"title": "silver benchmark provisional autoevaluado", "models": aggregates, "decision": decision, "revealed_at": utc_now(), "limitations": ["Five cases do not validate all LLM1 behavior.", "No positive PGx case is included; it is required in round two."]}); print(json.dumps(decision)); return 0

def parser():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="command", required=True)
    x = sub.add_parser("prepare"); x.add_argument("--payloads", required=True); x.add_argument("--output-dir", required=True); x.add_argument("--prompt", default=str(DEFAULT_PROMPT)); x.add_argument("--schema", default=str(DEFAULT_SCHEMA)); x.set_defaults(func=cmd_prepare)
    x = sub.add_parser("probe"); x.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); x.add_argument("--timeout", type=int, default=90); x.add_argument("--output", required=True); x.add_argument("--schema", default=str(DEFAULT_SCHEMA)); x.add_argument("--reasoning-policy", choices=["planned_none", "common_low", "minimum_supported"], default="planned_none"); x.set_defaults(func=cmd_probe)
    x = sub.add_parser("approve"); x.add_argument("--bundle", required=True); x.add_argument("--approval-owner", required=True); x.add_argument("--approval-reference", required=True); x.add_argument("--reasoning-policy", choices=["common_low", "minimum_supported"], required=True); x.set_defaults(func=cmd_approve)
    x = sub.add_parser("run"); x.add_argument("--bundle", required=True); x.add_argument("--pricing", required=True); x.add_argument("--output-dir", required=True); x.add_argument("--api-key-env", default="HEAL_OPENAI_API_KEY"); x.add_argument("--timeout", type=int, default=120); x.add_argument("--max-output-tokens", type=int, default=6000); x.add_argument("--seed", type=int, default=20260728); x.add_argument("--reasoning-policy", choices=["planned_none", "common_low", "minimum_supported"], default="planned_none"); x.set_defaults(func=cmd_run)
    x = sub.add_parser("score"); x.add_argument("--run-dir", required=True); x.set_defaults(func=cmd_score)
    x = sub.add_parser("revalidate"); x.add_argument("--run-dir", required=True); x.add_argument("--bundle", required=True); x.add_argument("--supersede", action="store_true"); x.set_defaults(func=cmd_revalidate)
    x = sub.add_parser("reveal"); x.add_argument("--run-dir", required=True); x.set_defaults(func=cmd_reveal); return p
if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
