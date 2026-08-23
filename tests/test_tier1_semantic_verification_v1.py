from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-tier1-curation-v2"
sys.path.insert(0, str(SERVICE))
import curation_v2 as cv2  # noqa: E402
import run_semantic_verification_v1 as runner  # noqa: E402
from tests.test_tier1_curation_v2 import decision, packet  # noqa: E402


def semantic_fixture() -> tuple[dict, dict]:
    evidence_packet = packet()
    semantic = cv2.legacy_decision_to_semantic(decision(), evidence_packet)
    return semantic, evidence_packet


class Tier1SemanticVerificationV1Tests(unittest.TestCase):
    def test_model_schema_contains_no_derived_decision_fields(self):
        schema = json.loads((SERVICE / "mechanism_curation_semantic_v1.schema.json").read_text(encoding="utf-8"))
        forbidden = {
            "group_id", "context_usable", "inference_ceiling", "evidence_ids",
            "direction_assessment", "expert_review_basis",
        }
        self.assertEqual(forbidden & set(schema["properties"]), set())
        self.assertFalse(schema["additionalProperties"])

    def test_semantic_evidence_coverage_is_exact(self):
        semantic, evidence_packet = semantic_fixture()
        self.assertEqual(cv2.validate_semantic_assessment(semantic, evidence_packet), [])
        semantic["used_evidence"].pop()
        self.assertIn("semantic_evidence_coverage_mismatch", cv2.validate_semantic_assessment(semantic, evidence_packet))

    def test_normalizer_derives_mathematics_and_stable_final_contract(self):
        semantic, evidence_packet = semantic_fixture()
        final = cv2.normalize_semantic_assessment(semantic, evidence_packet)
        self.assertEqual(final["schema_version"], "mechanism_curation_v2_decision")
        self.assertEqual(final["direction_assessment"], cv2.compute_direction(final["source_assessments"]))
        self.assertEqual(final["context_usable"], True)
        self.assertEqual(final["inference_ceiling"], "initial_guide_candidate")
        self.assertEqual(cv2.validate_decision(final, evidence_packet), [])

    def test_scores_never_trigger_semantic_arbitration(self):
        semantic, evidence_packet = semantic_fixture()
        first = cv2.normalize_semantic_assessment(semantic, evidence_packet)
        second_semantic = json.loads(json.dumps(semantic))
        second_semantic["used_evidence"][0]["quality"]["methodology"] -= 1
        second = cv2.normalize_semantic_assessment(second_semantic, evidence_packet)
        self.assertEqual(cv2.semantic_arbitration_reasons(semantic, second_semantic, first, second), [])

    def test_material_scientific_decision_triggers_arbitration(self):
        semantic, evidence_packet = semantic_fixture()
        first = cv2.normalize_semantic_assessment(semantic, evidence_packet)
        second_semantic = json.loads(json.dumps(semantic))
        second_semantic["core_status"] = "withheld"
        second = cv2.normalize_semantic_assessment(second_semantic, evidence_packet)
        reasons = cv2.semantic_arbitration_reasons(semantic, second_semantic, first, second)
        self.assertIn("scientific_disagreement:core_status", reasons)

    def test_structural_stop_is_immediate_for_deterministic_or_concentrated_failure(self):
        self.assertEqual(
            cv2.structural_stop_reason(["G:critic:direction_score_mismatch"]),
            "deterministic_failure:direction_score_mismatch",
        )
        reason = cv2.structural_stop_reason(["G:critic:same"] * 4 + ["G:critic:other"])
        self.assertEqual(reason, "dominant_failure_80_percent:same:4/5")

    def test_budget_gate_blocks_projected_candidate_and_campaign_overrun(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = runner.BudgetLedger(Path(temporary) / "budget.json", candidate_cap=1.0, campaign_cap=1.5)
            ledger.ensure_call("calibration", 0.9)
            ledger.record(phase="calibration", group_id="G:T1", role="curator", attempt=1, status="completed", observed=0.9, maximum_cost=0.9)
            with self.assertRaisesRegex(RuntimeError, "calibration"):
                ledger.ensure_call("calibration", 0.2)
            with self.assertRaisesRegex(RuntimeError, "campaign"):
                ledger.ensure_phase_reserve("holdout", 0.7)

    def test_current_pricing_reasoning_tokens_and_conservative_reserve(self):
        self.assertEqual(runner.PRICE_SNAPSHOT["input_per_million"], 4.0)
        self.assertEqual(runner.PRICE_SNAPSHOT["cached_input_per_million"], 0.4)
        self.assertEqual(runner.PRICE_SNAPSHOT["output_per_million"], 20.0)
        self.assertEqual(runner.RESERVE_PRICE_SNAPSHOT["input_per_million"], 5.0)
        self.assertEqual(runner.RESERVE_PRICE_SNAPSHOT["output_per_million"], 30.0)
        usage = runner.usage_cost({
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 400},
            "output_tokens": 500,
            "output_tokens_details": {"reasoning_tokens": 300},
        })
        self.assertEqual(usage["reasoning_tokens"], 300)
        self.assertAlmostEqual(usage["estimated_cost_usd"], 0.01256)

    def test_optional_call_must_leave_mandatory_base_reserve(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = runner.BudgetLedger(Path(temporary) / "budget.json", candidate_cap=1.0, campaign_cap=1.5)
            ledger.record(
                phase="calibration", group_id="G:T1", role="curator", attempt=1,
                status="completed", observed=0.4, maximum_cost=0.4,
            )
            with self.assertRaisesRegex(RuntimeError, "calibration"):
                ledger.ensure_call("calibration", 0.3, mandatory_remaining_reserve=0.4)

    def test_second_base_disagreement_stops_before_arbiter(self):
        curator_semantic, evidence_packet = semantic_fixture()
        critic_semantic = json.loads(json.dumps(curator_semantic))
        critic_semantic["core_status"] = "withheld"
        curator_decision = cv2.normalize_semantic_assessment(curator_semantic, evidence_packet)
        critic_decision = cv2.normalize_semantic_assessment(critic_semantic, evidence_packet)
        responses = [
            (curator_semantic, curator_decision, [], {"role": "curator"}),
            (critic_semantic, critic_decision, [], {"role": "critic"}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = runner.BudgetLedger(root / "budget.json", candidate_cap=10, campaign_cap=15)
            with mock.patch.object(runner, "run_role", side_effect=responses) as mocked:
                record, arbiter_count = runner.run_group(
                    phase="calibration", phase_groups=(evidence_packet["group_id"],),
                    group_id=evidence_packet["group_id"], packet=evidence_packet,
                    packets={evidence_packet["group_id"]: evidence_packet}, output_dir=root / "phase",
                    prompts={"curator": "p", "critic": "p", "arbiter": "p"}, schema={},
                    api_key="secret", timeout=1, max_output_tokens=100, ledger=ledger,
                    arbiter_count=1, prior_disagreements=1,
                )
            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(arbiter_count, 1)
            self.assertIn("structural:second_base_disagreement_stops_before_arbiter", record["arbiter_errors"])

    def test_secret_scan_reports_path_without_emitting_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "safe.json").write_text('{"ok":true}', encoding="utf-8")
            self.assertEqual(runner.secret_artifact_paths(root, "sk-test-secret"), [])
            (root / "bad.json").write_text('{"value":"sk-test-secret"}', encoding="utf-8")
            self.assertEqual(runner.secret_artifact_paths(root, "sk-test-secret"), ["bad.json"])

    def test_ambiguous_timeout_is_not_retried_and_reserves_maximum(self):
        semantic, evidence_packet = semantic_fixture()
        del semantic
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = runner.BudgetLedger(root / "budget.json", candidate_cap=10, campaign_cap=15)
            with mock.patch.object(runner, "api_call", side_effect=TimeoutError("timed out")) as mocked:
                _, _, errors, audit = runner.run_role(
                    phase="calibration", group_id=evidence_packet["group_id"], role="curator",
                    packet=evidence_packet, output_dir=root / "role", prompt="general prompt",
                    schema=cv2.read_json(SERVICE / "mechanism_curation_semantic_v1.schema.json"),
                    api_key="secret", timeout=1, max_output_tokens=100,
                    ledger=ledger,
                )
            self.assertEqual(mocked.call_count, 1)
            self.assertIn("technical:no_valid_output", errors)
            self.assertGreater(ledger.unknown, 0)
            self.assertIn("ambiguous_timeout_no_retry", " ".join(audit["technical_failures"]))

    def test_restricted_runner_has_no_optimizer_or_expansion_commands(self):
        commands = runner.parser()._subparsers._group_actions[0].choices
        self.assertEqual(set(commands), {"historical-regression", "run-protocol"})
        source = (SERVICE / "run_semantic_verification_v1.py").read_text(encoding="utf-8")
        self.assertNotIn('add_parser("optimize")', source)
        self.assertNotIn('add_parser("publish")', source)
        self.assertNotIn('add_parser("run-full")', source)


if __name__ == "__main__":
    unittest.main()
