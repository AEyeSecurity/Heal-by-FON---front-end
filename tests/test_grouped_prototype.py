from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-grouped-prototype" / "run_grouped_prototype.py"
SPEC = importlib.util.spec_from_file_location("grouped_prototype", SERVICE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GroupedPrototypeTests(unittest.TestCase):
    def test_responses_schema_projection_adds_type_without_mutating_source(self):
        original = {
            "type": "object",
            "properties": {
                "schema_version": {"const": "prototype_v1"},
                "enabled": {"const": True},
            },
        }
        projected = MODULE.responses_compatible_schema(original)
        self.assertEqual(projected["properties"]["schema_version"]["type"], "string")
        self.assertEqual(projected["properties"]["enabled"]["type"], "boolean")
        self.assertNotIn("type", original["properties"]["schema_version"])

    def test_responses_schema_projection_removes_unique_items_only_from_runtime_copy(self):
        original = {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
        projected = MODULE.responses_compatible_schema(original)
        self.assertNotIn("uniqueItems", projected)
        self.assertTrue(original["uniqueItems"])

    def test_envelope_is_closed_and_identity_bound(self):
        payload = {
            "group_id": "GENE:T1.1", "input_completeness": {"absence_semantics": "not_observed_callability_unknown"},
        }
        envelope = {
            "schema_version": "llm1_prototype_envelope_v1", "payload_v7": payload,
            "scientific_decision": {"group_id": "GENE:T1.1"},
            "coverage_status": "covered_by_prototype_snapshot", "readiness": {}, "allowlists": {}, "prototype_hashes": {},
        }
        MODULE.validate_envelope(envelope)
        envelope["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "open prototype envelope"):
            MODULE.validate_envelope(envelope)

    def test_context_ceiling_rejects_initial_guide(self):
        envelope = {
            "scientific_decision": {"prototype_inference_ceiling": "context_only"},
            "payload_v7": {"group_id": "GENE:T1.1", "gene": "GENE", "module_id": "T1.1"},
            "allowlists": {"evidence_ids": [], "variant_refs": [], "focus_variant_refs": []},
        }
        item = {
            "group_id": "GENE:T1.1", "gene": "GENE", "module_id": "T1.1",
            "inference_mode": "initial_guide", "review_priority": "none", "requires_professional_review": False,
            "interpretation_one_sentence_es": "Guía.", "interpretation_one_sentence_en": "Guide.",
            "focus_variant_refs": [], "evidence_used": [],
        }
        self.assertIn("llm1_inference_ceiling_exceeded", MODULE.validate_llm1_output(item, envelope))

    def test_prohibited_language_allows_safety_negation(self):
        self.assertFalse(MODULE.has_prohibited_language("Este prototipo no es un diagnóstico."))
        self.assertFalse(MODULE.has_prohibited_language("No iniciar ni suspender medicación por este resultado."))
        self.assertFalse(MODULE.has_prohibited_language("El alcance excluye el diagnóstico de toxinas."))
        self.assertTrue(MODULE.has_prohibited_language("Se recomienda iniciar medicación."))

    def test_invalid_key_is_a_campaign_wide_blocker_without_provider_body(self):
        error = RuntimeError('OpenAI API http_401: {"error":{"code":"invalid_api_key","message":"masked"}}')
        self.assertEqual(MODULE.global_technical_blocker_code(error), "openai_authentication_failed")
        self.assertIsNone(MODULE.global_technical_blocker_code(RuntimeError("single group schema mismatch")))

    def test_runtime_allowlist_uses_traceability_and_excludes_legacy_mechanism(self):
        payload = {
            "traceability_allowlist": {
                "allowed_evidence_ids": ["evv_1", "evm_1", "evk_1", "gwc_1"],
            },
        }
        self.assertEqual(MODULE.runtime_evidence_ids(payload), {"evv_1", "evk_1", "gwc_1"})

    def test_v2_envelope_uses_effective_runtime_ceiling(self):
        payload = {
            "payload_schema_version": "llm1_group_payload_v7",
            "group_id": "GENE:T1.1",
            "gene": "GENE",
            "module_id": "T1.1",
            "input_completeness": {"absence_semantics": "not_observed_callability_unknown"},
        }
        envelope = {
            "schema_version": "llm1_prototype_envelope_v2",
            "payload_v7": payload,
            "scientific_decision": {
                "group_id": "GENE:T1.1", "core_status": "approved",
                "scientific_inference_ceiling": "initial_guide_candidate",
                "effective_runtime_ceiling": "context_only", "dominant_direction": "supportive",
                "material_conflict": False, "limitations": [], "evidence_records": [],
            },
            "runtime_variant_gate": {
                "eligible": False, "reason_codes": ["direct_human_variant_evidence_missing"],
                "variant_refs": [], "evidence_ids": [],
            },
            "coverage_status": "covered_by_prototype_snapshot",
            "technical_gates": {
                "identity": True, "input_completeness": True, "evidence_allowlist": True,
                "variant_allowlist": True, "payload_schema": True,
            },
            "readiness": {
                "prototype_readiness": "approved_for_sandbox_smoke_test",
                "formal_validation_readiness": "pending_new_unseen_holdout",
            },
            "provenance": {
                "decision_signature": "signed", "allowlist": "signed_gold",
                "legacy_curation_override_scope": "curation_fields_only",
            },
            "allowlists": {
                "scientific_evidence_ids": [], "runtime_evidence_ids": [], "evidence_ids": [],
                "variant_refs": [], "focus_variant_refs": [],
            },
            "prototype_hashes": {
                "candidate_manifest": "a" * 64, "snapshot": "b" * 64,
                "payload": "c" * 64, "packet": "d" * 64,
            },
        }
        MODULE.validate_envelope(envelope)
        item = {
            "group_id": "GENE:T1.1", "gene": "GENE", "module_id": "T1.1",
            "inference_mode": "initial_guide", "review_priority": "none", "requires_professional_review": False,
            "interpretation_one_sentence_es": "Guía.", "interpretation_one_sentence_en": "Guide.",
            "focus_variant_refs": [], "evidence_used": [],
        }
        self.assertIn("llm1_inference_ceiling_exceeded", MODULE.validate_llm1_output(item, envelope))

    def test_negated_gwas_risk_is_not_a_critical_error(self):
        item = {"interpretation_long_es": "El GWAS no establece causalidad ni riesgo individual."}
        payload = {"gwas_evidence_summary": {"prioritized_clusters": [{"cluster_ref": "gwc_1"}]}}
        self.assertNotIn("gwas_causality_or_individual_risk", MODULE.prototype_critical_semantic_errors(item, payload))

    def test_llm2_cannot_add_group_variant_or_evidence(self):
        payload = {
            "allowlists": {"group_ids": ["GENE:T1.1"], "variant_refs": ["rs1"], "evidence_ids": ["PMID:1"]},
            "valid_llm1_cards": [{
                "group_id": "GENE:T1.1", "focus_variant_refs": ["rs1"],
                "evidence_used": [{"evidence_id": "PMID:1"}], "inference_mode": "context_only",
                "final_confidence_level": "Low",
            }],
        }
        result = {
            "schema_version": "grouped_global_interpretation_v1", "summary_es": "Resumen.",
            "coverage_statement_es": "Cobertura limitada a 12 grupos.", "limitations_es": ["Límite."],
            "key_findings": [{
                "group_id": "GENE:T1.1", "variant_refs": ["rs2"], "evidence_ids": ["PMID:2"],
                "inference_mode": "initial_guide", "confidence": "High",
            }],
        }
        errors = MODULE.validate_llm2_output(result, payload)
        self.assertIn("llm2_variant_outside_llm1", errors)
        self.assertIn("llm2_evidence_outside_llm1", errors)
        self.assertIn("llm2_inference_mode_changed", errors)
        self.assertIn("llm2_confidence_changed", errors)

    def test_dry_run_produces_both_reports_and_preserves_registry(self):
        payload_path = Path(
            r"F:\Heal by FON\data\runs\1304f27f-3b62-4cfb-be66-2d9246125cc3\llm1-preflight-v7-20260805-final\llm1_group_payloads_v7.jsonl"
        )
        if not payload_path.exists():
            self.skipTest("Operational fixture is not available")
        before = MODULE.sha256_file(MODULE.ACTIVE_REGISTRY)
        with tempfile.TemporaryDirectory() as temporary:
            result = MODULE.process({"payloadPath": str(payload_path), "outputDir": temporary, "fileName": "fixture.vcf", "dryRun": True})
            self.assertEqual(result["status"], "prototype_demo_ready_automatic")
            self.assertTrue((Path(temporary) / "HEAL_prototipo_desarrollo.docx").exists())
            self.assertTrue((Path(temporary) / "HEAL_prototipo_desarrollo.pdf").exists())
            cards = json.loads((Path(temporary) / "llm1_cards.json").read_text(encoding="utf-8"))
            self.assertEqual(len(cards), 180)
            self.assertEqual(sum(row["coverage_status"] == "covered_by_prototype_snapshot" for row in cards), 105)
            self.assertEqual(result["counts"]["not_covered"], 75)
        self.assertEqual(before, MODULE.sha256_file(MODULE.ACTIVE_REGISTRY))


if __name__ == "__main__":
    unittest.main()
