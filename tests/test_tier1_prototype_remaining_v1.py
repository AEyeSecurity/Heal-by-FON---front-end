from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-tier1-curation-v2" / "run_prototype_remaining_v1.py"
SPEC = importlib.util.spec_from_file_location("tier1_prototype_remaining", SERVICE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Tier1PrototypeRemainingTests(unittest.TestCase):
    def test_prototype_manifest_separates_signed_replay_and_packet_selected(self):
        rows = []
        for group_id in MODULE.cv2.GOLD_GROUPS:
            rows.append({"group_id": group_id, "allowlist_provenance": "signed_gold"})
        for index in range(93):
            rows.append({"group_id": f"GENE{index}:T1.1", "allowlist_provenance": "packet_selected"})
        evidence = {"manifest_sha256": "abc", "packets": rows}
        with tempfile.TemporaryDirectory() as temporary:
            manifest = MODULE.prototype_manifest(evidence, Path(temporary) / "manifest.json")
        counts = manifest["counts"]
        self.assertEqual(counts[MODULE.SIGNED_PROTOTYPE_PROVENANCE], 12)
        self.assertEqual(counts["packet_selected"], 93)
        signed = [row for row in manifest["packets"] if row["allowlist_provenance"] == MODULE.SIGNED_PROTOTYPE_PROVENANCE]
        self.assertEqual({row["group_id"] for row in signed}, set(MODULE.cv2.GOLD_GROUPS))

    def test_official_and_conservative_price_snapshots_are_separate(self):
        self.assertEqual(MODULE.base.PRICE_SNAPSHOT["input_per_million"], 4.0)
        self.assertEqual(MODULE.base.PRICE_SNAPSHOT["output_per_million"], 20.0)
        self.assertEqual(MODULE.base.RESERVE_PRICE_SNAPSHOT["input_per_million"], 5.0)
        self.assertEqual(MODULE.base.RESERVE_PRICE_SNAPSHOT["output_per_million"], 30.0)

    def test_hard_cap_is_fixed(self):
        self.assertEqual(MODULE.HARD_CAP_USD, 75.0)

    def test_credit_exhaustion_is_not_retryable(self):
        self.assertFalse(MODULE.v2.retryable_http_error(
            429, '{"code":"credit_balance_exhausted","type":"insufficient_quota"}',
        ))
        self.assertTrue(MODULE.v2.retryable_http_error(429, "rate_limit_exceeded"))
        self.assertTrue(MODULE.v2.retryable_http_error(503, "temporary"))

    def test_carry_forward_copies_only_completed_groups_and_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior, output = root / "prior", root / "continuation"
            for directory in ("records", "semantic", "decisions", "audit", "raw"):
                (prior / directory).mkdir(parents=True, exist_ok=True)
            terminal = {
                "status": "full_curation_incomplete", "passed": False,
                "active_registry_sha256": MODULE.EXPECTED_REGISTRY_SHA256,
                "critical_errors": ["BAD:T1.4:final_decision_missing"],
            }
            (prior / "protocol_terminal_state.json").write_text(json.dumps(terminal), encoding="utf-8")
            valid_record = {"group_id": "GOOD:T1.1", "final_decision": {"core_status": "approved"}}
            failed_record = {"group_id": "BAD:T1.4", "final_decision": None}
            (prior / "records" / "GOOD__T1.1.json").write_text(json.dumps(valid_record), encoding="utf-8")
            (prior / "records" / "BAD__T1.4.json").write_text(json.dumps(failed_record), encoding="utf-8")
            for directory in ("semantic", "decisions", "audit", "raw"):
                (prior / directory / "GOOD__T1.1__curator.json").write_text("{}", encoding="utf-8")
                (prior / directory / "BAD__T1.4__curator.json").write_text("{}", encoding="utf-8")
            ledger = {
                "schema_version": "tier1_semantic_budget_ledger_v1",
                "entries": [
                    {"phase": "full", "group_id": "GOOD:T1.1", "role": "curator", "attempt": 1, "accounted_cost_usd": 0.4},
                    {"phase": "full", "group_id": "BAD:T1.4", "role": "curator", "attempt": 1, "accounted_cost_usd": 0.0},
                ],
            }
            (prior / "budget_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
            output.mkdir()
            manifest = MODULE.seed_from_prior_campaign(prior=prior, output=output)
            self.assertEqual(manifest["completed_groups_carried"], ["GOOD:T1.1"])
            self.assertEqual(manifest["incomplete_groups_not_carried"], ["BAD:T1.4"])
            self.assertTrue((output / "records" / "GOOD__T1.1.json").exists())
            self.assertFalse((output / "records" / "BAD__T1.4.json").exists())
            seeded = json.loads((output / "budget_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(len(seeded["entries"]), 1)
            self.assertEqual(seeded["entries"][0]["group_id"], "GOOD:T1.1")


if __name__ == "__main__":
    unittest.main()
