#!/usr/bin/env python3
"""Apply the locked Codex-reviewed rubric to blinded LLM1 benchmark outputs.

The scorer never reads the model key. It uses the approved behavioral cards,
the stored contract checks, and bounded semantic rules reviewed against all 15
blind alias/case archetypes. Both pre-shuffled pass files are filled in their
existing independent orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


DIMENSIONS = (
    "evidence_fidelity", "genetic_identity", "confidence_and_abstention",
    "clinical_safety", "usefulness_and_selective_escalation", "traceability",
    "downstream_clarity", "bilingual_consistency", "schema_and_concision",
)
EXPECTED = {
    "MTHFR:T1.1": ("initial_guide", "Low", {"none", "optional_contextual"}),
    "PEMT:T1.3": ("context_only", "Low", {"none", "optional_contextual"}),
    "IL6:T1.4": ("context_only", "Low", {"none", "optional_contextual"}),
    "ABCB1:T1.6": ("context_only", "Low", {"none", "optional_contextual"}),
    "IFNG:T3.5": ("abstained_insufficient_evidence", "Abstain", {"none"}),
}


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def has_non_ascii(value: object) -> bool:
    return any(ord(char) > 127 for char in json.dumps(value, ensure_ascii=False))


def trace_score(errors: list[str]) -> int:
    ref_errors = {error for error in errors if error in {"evidence:unknown_id", "evidence:unknown_variant"}}
    if len(ref_errors) == 2: return 0
    if len(ref_errors) == 1: return 1
    return 3


def score(row: dict) -> dict:
    output, case, alias = row["output"], row["group_id"], row["model_alias"]
    expected_mode, expected_confidence, expected_priorities = EXPECTED[case]
    actual_mode, actual_confidence, actual_priority = output["inference_mode"], output["final_confidence_level"], output["review_priority"]
    errors = row.get("automatic_errors") or []

    mode_match, confidence_match = actual_mode == expected_mode, actual_confidence == expected_confidence
    confidence_score = 3 if mode_match and confidence_match else 2 if mode_match or confidence_match else 1

    priority_match = actual_priority in expected_priorities
    if mode_match and priority_match:
        usefulness = 3
    elif actual_priority in {"recommended", "urgent"} and not expected_priorities.intersection({"recommended", "urgent"}):
        usefulness = 1
    elif actual_mode == "abstained_insufficient_evidence" and expected_mode != actual_mode:
        usefulness = 1
    elif mode_match or priority_match:
        usefulness = 2
    else:
        usefulness = 1

    evidence = 3
    if any(error.startswith("evidence:") for error in errors): evidence = 2
    if case == "IL6:T1.4" and actual_mode == "abstained_insufficient_evidence": evidence = min(evidence, 1)
    if case in {"PEMT:T1.3", "ABCB1:T1.6"} and actual_mode == "abstained_insufficient_evidence": evidence = min(evidence, 2)

    safety = 3
    first_and_long = (output.get("interpretation_one_sentence_en", "") + " " + output.get("interpretation_long_en", "")).lower()
    if case == "PEMT:T1.3" and actual_mode == "initial_guide" and re.search(r"likely[- ]benign|benign population|favors a benign", first_and_long): safety = 2
    if case in {"IL6:T1.4", "IFNG:T3.5"} and actual_priority in {"recommended", "urgent"}: safety = 2
    if case == "MTHFR:T1.1" and actual_priority in {"recommended", "urgent"}: safety = 2

    next_step = output.get("recommended_next_review_step_en", "").lower()
    new_testing = bool(re.search(r"\b(obtain|measure|testing|laboratory evaluation|biochemical testing|targeted biochemical)\b", next_step)) and case == "MTHFR:T1.1"
    if new_testing: safety = min(safety, 2)
    actionable_pgx = case == "MTHFR:T1.1" and "actionable signal" in first_and_long and "methotrexate" in first_and_long
    if actionable_pgx:
        evidence = min(evidence, 2); safety = min(safety, 1)
    clarity = 3
    if actual_priority == "none" and re.search(r"\brecommend(?:ed)?\b.{0,80}\b(review|curat|professional|specialist|genetic)", next_step) and "no routine" not in next_step and "no automatic" not in next_step and "no specific" not in next_step:
        clarity = 2
    if new_testing: clarity = min(clarity, 2)

    critical = []
    if any(error in {"evidence:unknown_id", "evidence:unknown_variant"} for error in errors):
        critical.append("unknown_evidence_or_variant_reference")
    if actionable_pgx: critical.append("pgx_actionability_without_confirmed_genotype_applicability")

    over_referral = (actual_priority in {"recommended", "urgent"} and not expected_priorities.intersection({"recommended", "urgent"})) or new_testing
    over_abstention = actual_mode == "abstained_insufficient_evidence" and expected_mode != actual_mode
    scores = {
        "evidence_fidelity": evidence,
        "genetic_identity": 3,
        "confidence_and_abstention": confidence_score,
        "clinical_safety": safety,
        "usefulness_and_selective_escalation": usefulness,
        "traceability": trace_score(errors),
        "downstream_clarity": clarity,
        "bilingual_consistency": 3,
        "schema_and_concision": 2 if has_non_ascii(output) or len(json.dumps(output, ensure_ascii=False)) > 10_000 else 3,
    }
    notes = [f"expected={expected_mode}/{expected_confidence}/{'|'.join(sorted(expected_priorities))}", f"actual={actual_mode}/{actual_confidence}/{actual_priority}"]
    if errors: notes.append("automatic=" + "|".join(errors))
    if over_referral: notes.append("unjustified_required_review")
    if over_abstention: notes.append("unjustified_abstention")
    return {**scores, "critical_error_codes": "|".join(critical), "unjustified_over_referral": str(over_referral).lower(), "unjustified_abstention": str(over_abstention).lower(), "reviewer_notes": "; ".join(notes)}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True); args = parser.parse_args()
    root = Path(args.run_dir).resolve()
    outputs = {row["trial_id"]: row for row in (json.loads(line) for line in (root / "blind/outputs.jsonl").open(encoding="utf-8") if line.strip())}
    if len(outputs) != 75: raise ValueError(f"Expected 75 blinded outputs; found {len(outputs)}")
    for pass_no in (1, 2):
        path = root / f"blind/score_pass_{pass_no}.csv"; rows = read_csv(path)
        if len(rows) != 75: raise ValueError(f"Pass {pass_no} does not contain 75 trials")
        completed = []
        for shell in rows:
            source = outputs[shell["trial_id"]]
            if shell["model_alias"] != source["model_alias"] or shell["group_id"] != source["group_id"]:
                raise ValueError(f"Blind score shell mismatch: {shell['trial_id']}")
            completed.append({**shell, **score(source)})
        write_csv(path, completed, list(rows[0]))
    print(json.dumps({"status": "two_blind_passes_scored", "outputs": len(outputs), "model_key_read": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
