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
import run_semantic_verification_v2 as runner  # noqa: E402
import build_full_evidence_manifest_v2 as full_packets  # noqa: E402
from tests.test_tier1_curation_v2 import packet, quality  # noqa: E402


def semantic_v2(evidence_packet: dict | None = None) -> tuple[dict, dict]:
    evidence_packet = evidence_packet or packet()
    used = []
    for evidence_id in evidence_packet["selected_evidence_ids"]:
        if evidence_id == "NCBI_GENE:1":
            roles = ["canonical"]
            human, functional = False, False
        elif evidence_id == "PMID:1":
            roles = ["functional"]
            human, functional = False, True
        else:
            roles = ["human"]
            human, functional = True, False
        used.append({
            "evidence_id": evidence_id, "roles": roles, "supports_direction": "positive",
            "core_conflict_class": "none", "core_conflict_rationale": "",
            "human_applicability": human, "functional_compatibility": functional,
            "quality": quality(),
        })
    return {
        "schema_version": "mechanism_curation_semantic_v2", "core_status": "approved",
        "identity_exact": True, "module_relation_direct": True,
        "used_evidence": used, "excluded_evidence": [], "scientific_reason_codes": [],
        "limitations": ["Selected evidence has bounded context and transferability."],
        "confidence": 0.9,
    }, evidence_packet


class Tier1SemanticVerificationV2Tests(unittest.TestCase):
    def test_campaign_id_is_parameterized_and_previous_campaign_cannot_be_reopened(self):
        campaign_id = "semantic-v2-allowlist-conflict-verification-3"
        self.assertEqual(runner.validate_campaign_id(campaign_id), campaign_id)
        with self.assertRaises(ValueError):
            runner.validate_campaign_id("verification-2")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / campaign_id
            root.mkdir()
            cv2.write_json(root / "protocol_configuration.json", {"candidate": campaign_id}, immutable=True)
            cv2.write_json(root / "protocol_terminal_state.json", {"passed": False}, immutable=True)
            with self.assertRaisesRegex(ValueError, "cannot be resumed"):
                runner._existing_campaign_configuration(root, candidate_id=campaign_id, resume=True)

    def test_schema_contains_conflict_class_but_no_derived_fields(self):
        schema = cv2.read_json(SERVICE / "mechanism_curation_semantic_v2.schema.json")
        item = schema["properties"]["used_evidence"]["items"]
        self.assertIn("core_conflict_class", item["properties"])
        self.assertIn("core_conflict_rationale", item["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(item["additionalProperties"])
        forbidden = {"group_id", "context_usable", "inference_ceiling", "direction_assessment", "expert_review_basis"}
        self.assertFalse(forbidden & set(schema["properties"]))
        self.assertNotIn("uniqueItems", json.dumps(schema))

    def test_signed_allowlist_is_strict_for_all_roles(self):
        evidence_packet = packet()
        evidence_packet["human_review_policy"] = {
            "proposal_sha256": "a" * 64,
            "valid_evidence_ids": ["NCBI_GENE:1", "PMID:1"],
            "invalid_evidence_ids": [],
        }
        evidence_packet["packet_sha256"] = cv2.sha256_json({k: v for k, v in evidence_packet.items() if k != "packet_sha256"})
        semantic, _ = semantic_v2(evidence_packet)
        self.assertIn("semantic_used_evidence_outside_execution_allowlist", cv2.validate_semantic_assessment_v2(semantic, evidence_packet))
        semantic["used_evidence"] = [row for row in semantic["used_evidence"] if row["evidence_id"] != "PMID:2"]
        semantic["excluded_evidence"] = [{"evidence_id": "PMID:2", "reason_code": "outside_valid_evidence_allowlist"}]
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertEqual(cv2.validate_decision(final, evidence_packet), [])
        self.assertEqual(cv2.execution_evidence_allowlist(evidence_packet)["provenance"], "signed_gold")

    def test_unsigned_packet_uses_selected_window_with_distinct_provenance(self):
        evidence_packet = packet()
        resolved = cv2.execution_evidence_allowlist(evidence_packet)
        self.assertEqual(resolved["provenance"], "packet_selected")
        self.assertEqual(set(resolved["allowed_ids"]), set(evidence_packet["selected_evidence_ids"]))

    def test_contextual_heterogeneity_does_not_create_material_conflict(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "negative",
            "core_conflict_class": "contextual_heterogeneity",
            "core_conflict_rationale": "A population-specific endpoint differs without testing the central mechanism.",
        })
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertFalse(final["direction_assessment"]["material_conflict"])
        self.assertEqual(final["direction_assessment"]["opposition_score"], 0)
        self.assertEqual(final["expert_review_basis"]["conflict_evidence_ids"], [])

    def test_positive_contextual_evidence_keeps_support_without_opposition(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "positive",
            "core_conflict_class": "limited_generalizability",
            "core_conflict_rationale": "The finding supports the mechanism in a bounded population.",
        })
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertEqual(final["direction_assessment"]["dominant_direction"], "supports_relation")
        self.assertGreater(final["direction_assessment"]["support_score"], 0)
        self.assertEqual(final["direction_assessment"]["opposition_score"], 0)
        self.assertFalse(final["direction_assessment"]["material_conflict"])

    def test_downstream_null_does_not_create_material_conflict(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "null", "core_conflict_class": "downstream_null",
            "core_conflict_rationale": "The null clinical response is downstream of the molecular mechanism.",
        })
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertFalse(final["direction_assessment"]["material_conflict"])

    def test_positive_core_direction_can_coexist_with_downstream_null_limitation(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "positive", "core_conflict_class": "downstream_null",
            "core_conflict_rationale": "The core mechanism is supported although a later endpoint was null.",
        })
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertEqual(final["direction_assessment"]["dominant_direction"], "supports_relation")
        self.assertGreater(final["direction_assessment"]["support_score"], 0)
        self.assertEqual(final["direction_assessment"]["opposition_score"], 0)
        self.assertFalse(final["direction_assessment"]["material_conflict"])

    def test_direct_material_contradiction_creates_conflict_and_expert_ids(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "negative", "core_conflict_class": "direct_material_contradiction",
            "core_conflict_rationale": "The experiment directly tests and contradicts the same central mechanism.",
        })
        semantic["core_status"] = "approved_with_conflict"
        self.assertEqual(cv2.validate_semantic_assessment_v2(semantic, evidence_packet), [])
        final = cv2.normalize_semantic_assessment_v2(semantic, evidence_packet)
        self.assertTrue(final["direction_assessment"]["material_conflict"])
        self.assertEqual(final["expert_review_basis"]["conflict_evidence_ids"], ["PMID:2"])

    def test_null_cannot_be_direct_material_contradiction(self):
        semantic, evidence_packet = semantic_v2()
        human = next(row for row in semantic["used_evidence"] if row["evidence_id"] == "PMID:2")
        human.update({
            "supports_direction": "null", "core_conflict_class": "direct_material_contradiction",
            "core_conflict_rationale": "Incorrect classification for regression test.",
        })
        errors = cv2.validate_semantic_assessment_v2(semantic, evidence_packet)
        self.assertIn("direct_material_conflict_requires_negative_or_mixed:PMID:2", errors)

    def test_fads1_and_gclm_style_context_does_not_trigger_arbitration(self):
        first_semantic, evidence_packet = semantic_v2()
        second_semantic = json.loads(json.dumps(first_semantic))
        first_semantic["limitations"].append("Heterogeneous downstream endpoints are contextual.")
        second_semantic["limitations"].append("Generalizability is limited by assay and population.")
        first = cv2.normalize_semantic_assessment_v2(first_semantic, evidence_packet)
        second = cv2.normalize_semantic_assessment_v2(second_semantic, evidence_packet)
        self.assertEqual(cv2.semantic_arbitration_reasons(first_semantic, second_semantic, first, second), [])

    def test_role_runner_rejects_outside_allowlist_before_normalization(self):
        evidence_packet = packet()
        evidence_packet["human_review_policy"] = {
            "proposal_sha256": "a" * 64, "valid_evidence_ids": ["NCBI_GENE:1", "PMID:1"],
            "invalid_evidence_ids": [],
        }
        evidence_packet["packet_sha256"] = cv2.sha256_json({k: v for k, v in evidence_packet.items() if k != "packet_sha256"})
        semantic, _ = semantic_v2(evidence_packet)
        raw = {
            "id": "resp_test", "model": cv2.MODEL, "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": json.dumps(semantic)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
        with tempfile.TemporaryDirectory() as temporary:
            ledger = runner.base.BudgetLedger(Path(temporary) / "budget.json", candidate_cap=10, campaign_cap=15)
            with mock.patch.object(runner, "api_call", return_value=(raw, 0.1)):
                _, decision, errors, _ = runner.run_role(
                    phase="calibration", group_id=evidence_packet["group_id"], role="curator",
                    packet=evidence_packet, output_dir=Path(temporary) / "out", prompt="general",
                    schema=cv2.read_json(SERVICE / "mechanism_curation_semantic_v2.schema.json"),
                    api_key="secret", timeout=1, max_output_tokens=100, ledger=ledger,
                )
        self.assertFalse(decision)
        self.assertIn("semantic_used_evidence_outside_execution_allowlist", errors)

    def test_v2_runner_exposes_protocol_estimate_and_full_without_optimizer(self):
        commands = runner.parser()._subparsers._group_actions[0].choices
        self.assertEqual(set(commands), {"run-protocol", "estimate-full", "run-full"})
        source = (SERVICE / "run_semantic_verification_v2.py").read_text(encoding="utf-8")
        self.assertNotIn('add_parser("optimize")', source)
        self.assertNotIn('add_parser("publish")', source)

    def test_full_structural_stop_ignores_scientific_arbitration_and_stops_patterns(self):
        self.assertEqual(runner.full_structural_stop(
            current_errors=[], history=[], consecutive_code="", consecutive_count=0,
        ), "")
        self.assertEqual(runner.full_structural_stop(
            current_errors=["G:direction_score_mismatch"], history=["G:direction_score_mismatch"],
            consecutive_code="direction_score_mismatch", consecutive_count=1,
        ), "deterministic_failure:direction_score_mismatch")
        self.assertEqual(runner.full_structural_stop(
            current_errors=["schema_error"], history=["schema_error"] * 3,
            consecutive_code="schema_error", consecutive_count=3,
        ), "three_consecutive_structural_failures:schema_error")
        self.assertTrue(runner.full_structural_stop(
            current_errors=["same"], history=["same"] * 4 + ["other"],
            consecutive_code="same", consecutive_count=1,
        ).startswith("dominant_failure_80_percent:same"))
        self.assertEqual(
            cv2.structural_error_family(
                "MTHFR:T1.1:critic:semantic_used_evidence_outside_execution_allowlist:PMID:41417174"
            ),
            "semantic_used_evidence_outside_execution_allowlist",
        )

    def test_closed_mixed_manifest_requires_12_signed_and_93_packet_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            group_ids = list(cv2.GOLD_GROUPS) + [f"GENE{index}:T1.1" for index in range(93)]
            for index, group_id in enumerate(group_ids):
                evidence_packet = packet()
                evidence_packet["group_id"] = group_id
                provenance = "signed_gold" if group_id in cv2.GOLD_GROUPS else "packet_selected"
                if provenance == "signed_gold":
                    evidence_packet["human_review_policy"] = {
                        "proposal_sha256": "a" * 64,
                        "valid_evidence_ids": list(evidence_packet["selected_evidence_ids"]),
                        "invalid_evidence_ids": [],
                    }
                evidence_packet["packet_sha256"] = cv2.sha256_json(
                    {key: value for key, value in evidence_packet.items() if key != "packet_sha256"}
                )
                path = root / f"packet-{index}.json"
                cv2.write_json(path, evidence_packet, immutable=True)
                rows.append({
                    "group_id": group_id, "packet_path": str(path),
                    "packet_sha256": evidence_packet["packet_sha256"],
                    "file_sha256": cv2.sha256_file(path),
                    "allowlist_provenance": provenance,
                })
            manifest = {
                "schema_version": "tier1_mixed_evidence_packet_manifest_v2",
                "evidence_cutoff": cv2.CUTOFF.isoformat(),
                "counts": {"total": 105, "signed_gold": 12, "packet_selected": 93},
                "packets": rows,
            }
            manifest["manifest_sha256"] = cv2.sha256_json(manifest)
            path = root / "manifest.json"
            cv2.write_json(path, manifest, immutable=True)
            self.assertEqual(full_packets.validate_mixed_manifest(path), [])

    def test_full_cost_estimate_uses_210_base_calls_p95_and_25_percent_contingency(self):
        packets = {}
        for index in range(105):
            evidence_packet = packet()
            evidence_packet["group_id"] = f"GENE{index}:T1.1"
            packets[evidence_packet["group_id"]] = evidence_packet
        with mock.patch.object(runner, "_verification_usage", return_value=(
            {"curator": [4000, 5000], "critic": [4500, 5500], "arbiter": [6000]}, 1, 12,
        )), mock.patch.object(runner, "exact_input_tokens", return_value=(30000, "test")):
            estimate = runner.estimate_full_cost(
                packets=packets, prompts={"curator": "p", "critic": "p", "arbiter": "p"},
                verification_campaign=Path("unused"),
            )
        self.assertEqual(estimate["base_calls"], 210)
        self.assertEqual(estimate["planned_arbiters"], 21)
        self.assertEqual(estimate["contingency_percent"], 25)
        self.assertEqual(estimate["input_token_method"], "test")


if __name__ == "__main__":
    unittest.main()
