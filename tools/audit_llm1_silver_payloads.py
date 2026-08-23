#!/usr/bin/env python3
"""Audit the five regenerated silver payloads before manual behavior approval."""

import argparse, json
from pathlib import Path

PILOTS = {"MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5"}

def main():
    p = argparse.ArgumentParser(); p.add_argument("--payloads", required=True); p.add_argument("--output", required=True); args = p.parse_args()
    rows = [json.loads(line) for line in Path(args.payloads).read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = {row["group_id"]: row for row in rows if row.get("group_id") in PILOTS}
    if set(selected) != PILOTS: raise ValueError(f"Expected five pilots, found {sorted(selected)}")
    cases = []
    for group_id in sorted(PILOTS):
        row = selected[group_id]; focus = {item.get("variant_ref") for item in row.get("focus_variant_evidence") or []}
        errors = (row.get("source_failures") or {}).get("error_refs_for_audit") or []
        material = [error for error in errors if error.get("variant_ref") in focus]
        axes = (row.get("patient_context") or {}).get("axes") or {}
        cases.append({
            "group_id": group_id,
            "mechanism_status": (row.get("curated_mechanism") or {}).get("curation_status"),
            "focus_variant_count": len(focus),
            "same_condition_material_conflicts": (row.get("clinical_evidence_summary") or {}).get("condition_conflict_semantics", {}).get("same_condition_conflict_variant_keys", []),
            "source_error_count": len(errors), "material_source_error_count": len(material),
            "material_source_errors": material,
            "all_context_not_provided": bool(axes) and all(axis.get("default_state") == "not_provided" and not axis.get("items") for axis in axes.values()),
            "structured_medication_count": len((row.get("patient_context") or {}).get("structured_medications") or []),
            "payload_ready": bool((row.get("gates") or {}).get("group_payload_ready")),
        })
    result = {"status": "valid" if all(c["payload_ready"] and c["material_source_error_count"] == 0 and c["all_context_not_provided"] for c in cases) else "blocked", "cases": cases}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(result, ensure_ascii=False)); return 0 if result["status"] == "valid" else 2

if __name__ == "__main__": raise SystemExit(main())
