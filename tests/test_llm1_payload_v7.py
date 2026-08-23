import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


v7 = load("heal_llm1_payload_v7_test", ROOT / "services/heal-llm1-payload-v7/build_llm1_payload_v7.py")
v6 = load("heal_llm1_payload_v6_test", ROOT / "services/heal-llm1-payload-v6/build_llm1_payload_v6.py")
runner = load(
    "heal_grouped_interpreter_v7_test",
    ROOT / "services/heal-grouped-individual-interpretation/interpret_gene_module_groups.py",
)
curation = load("heal_tier1_curation_test", ROOT / "tools/prepare_llm1_tier1_curation.py")


def patient_context():
    return {
        "schema_version": "llm1_patient_context_v1",
        "axes": {
            "metabolism_nutrients_methylation": {"items": [], "default_state": "not_provided", "allowed_codes": []},
            "immunity_inflammation": {"items": [], "default_state": "not_provided", "allowed_codes": []},
            "xenobiotics_pharmacogenomics": {"items": [], "default_state": "not_provided", "allowed_codes": []},
        },
        "structured_medications": [],
        "free_note": "",
        "free_note_authoritative": False,
        "interpretation_constraints": {},
    }


class PayloadV7Tests(unittest.TestCase):
    def test_internal_auto_requires_hash_valid_released_human_foundation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "foundation.json"
            snapshot = {
                "schema_version": "tier1_human_review_foundation_v1",
                "groups": {f"GENE{index}:T1.1": {"release_ready": True, "technical_flags": []} for index in range(12)},
                "release_ready": True,
            }
            snapshot["snapshot_sha256"] = __import__("hashlib").sha256(v7.canonical_json(snapshot)).hexdigest()
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            self.assertTrue(v7.human_review_foundation_state(str(path))["ready"])
            snapshot["groups"]["GENE0:T1.1"]["technical_flags"] = ["unresolved"]
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            blocked = v7.human_review_foundation_state(str(path))
            self.assertFalse(blocked["ready"])
            self.assertEqual(blocked["reason"], "human_review_foundation_hash_invalid")

    def test_sparse_input_cannot_claim_hom_ref(self):
        with self.assertRaisesRegex(ValueError, "cannot assert hom-ref"):
            v7.validate_input_completeness({
                "mode": "observed_variants_only", "source_type": "vcf", "reference_build": "GRCh38",
                "reference_version": "test", "sample_count": 1, "callability_method": "not_available",
                "can_assert_hom_ref": True, "can_assert_not_callable": False,
                "absence_semantics": "not_observed_callability_unknown",
            })

    def test_callability_aware_requires_gvcf_or_all_sites(self):
        with self.assertRaisesRegex(ValueError, "requires gVCF"):
            v7.validate_input_completeness({
                "mode": "callability_aware", "source_type": "vcf", "reference_build": "GRCh38",
                "reference_version": "test", "sample_count": 1, "callability_method": "depth_and_gq",
                "can_assert_hom_ref": True, "can_assert_not_callable": True,
                "absence_semantics": "explicit_hom_ref_or_not_callable",
            })

    def test_age_8_to_17_is_defined_but_disabled(self):
        source = v7.empty_group_payload(
            {"gene": "GENE", "module_id": "T1.1", "curation_status": "withheld"},
            [{"module_name": "Module", "tier": "Tier 1"}],
            patient_context(),
        )
        converted = v7.convert_payload(source, {
            "ageBand": "age_8_17", "activeAgeBands": "age_0_7", "activeTiers": "T1",
            "experimentalCanaryGroups": "IFNG:T3.5", "assembly": "GRCh38",
        })
        self.assertFalse(converted["age_context"]["enabled"])
        self.assertEqual(converted["operational_state"]["status"], "preflight_blocked")
        self.assertIn("age_band_not_enabled", converted["operational_state"]["blocker_codes"])

    def test_empty_sparse_group_is_coverage_only_not_hom_ref(self):
        source = v7.empty_group_payload(
            {"gene": "GENE", "module_id": "T1.1", "curation_status": "withheld"},
            [{"module_name": "Module", "tier": "Tier 1"}],
            patient_context(),
        )
        converted = v7.convert_payload(source, {"activeTiers": "T1", "activeAgeBands": "age_0_7", "ageBand": "age_0_7"})
        card = v7.card_from_payload(converted)
        self.assertEqual(card["coverage_status"], "not_interpreted")
        self.assertIn("callability is unknown", card["deterministic_summary"]["statement"])
        self.assertEqual(converted["input_completeness"]["absence_semantics"], "not_observed_callability_unknown")

    def test_pgx_is_general_and_never_actionable_without_all_inputs(self):
        payload = {
            "clinical_evidence_summary": {"condition_conflict_semantics": {"drug_response_context_variant_keys": ["v1"]}},
            "focus_variant_evidence": [], "patient_context": {"structured_medications": []},
            "professional_curation": {},
        }
        v7.generalize_pgx(payload)
        self.assertEqual(payload["professional_curation"]["pgx_observed_genotype_applicability"], "not_confirmed")
        self.assertEqual(payload["professional_curation"]["pgx_evidence_strength"], "weak_context_only")
        self.assertFalse(payload["professional_curation"]["pgx_escalation_allowed"])

    def test_critical_language_is_quarantinable(self):
        item = {"interpretation_long_en": "Start medication at a dose of 10 mg."}
        errors = runner.critical_semantic_errors(item, {"professional_curation": {}, "gwas_evidence_summary": {}})
        self.assertIn("treatment_or_dose", errors)

    def test_expert_review_candidate_is_true_only_for_five_of_five(self):
        payload = {
            "payload_schema_version": "llm1_group_payload_v7",
            "focus_variant_evidence": [{"variant_ref": "v1", "evidence_id": "e1"}],
            "traceability_allowlist": {"allowed_evidence_ids": ["e1"], "allowed_variant_refs": ["v1"], "allowed_focus_variant_refs": ["v1"]},
            "expert_review_gate": {
                "condition_1_strong_evidence": True, "condition_2_material_scientific_conflict": True,
                "condition_3_observed_variant_directly_relevant": True,
                "directly_relevant_focus_variant_refs": ["v1"], "conflict_evidence_ids": ["e1"],
            },
        }
        conditions = {
            "strong_evidence": True, "material_scientific_conflict": True,
            "observed_variant_directly_relevant": True, "individual_interpretation_materially_affected": True,
            "unresolved_from_available_evidence_context": True,
        }
        item = {
            "evidence_used": [{"evidence_id": "e1", "variant_ref": "v1"}], "focus_variant_refs": ["v1"],
            "review_priority": "recommended", "requires_professional_review": True,
            "inference_mode": "initial_guide", "final_confidence_level": "Conflicting",
            "interpretation_scope": "conflicting_group_review_needed",
            "review_reason_codes": ["unresolved_variant_specific_material_conflict"],
            "evidence_limitations": ["Unresolved material conflict remains."],
            "expert_review_conditions": conditions, "expert_review_candidate": True,
        }
        runner.validate_v5_interpretation(item, payload)
        for condition in conditions:
            changed = json.loads(json.dumps(item))
            changed["expert_review_conditions"][condition] = False
            changed["expert_review_candidate"] = False
            if condition in {"strong_evidence", "material_scientific_conflict", "observed_variant_directly_relevant"}:
                with self.assertRaisesRegex(ValueError, "inconsistent"):
                    runner.validate_v5_interpretation(changed, payload)
            else:
                changed["review_priority"] = "none"
                changed["requires_professional_review"] = False
                changed["review_reason_codes"] = []
                runner.validate_v5_interpretation(changed, payload)

    def test_tier1_candidate_does_not_classify_unreviewed_mechanism(self):
        rows, audit = curation.prepare_mechanisms([
            {"gene": "GENE", "module_id": "T1.1", "curation_status": "draft"},
            {"gene": "OTHER", "module_id": "T2.1", "curation_status": "draft"},
        ])
        self.assertEqual(rows[0]["curation_status"], "draft")
        self.assertEqual(rows[1]["curation_status"], "draft")
        self.assertEqual(audit[0]["decision"], "blocked_draft_requires_mechanism_curation_v2")
        self.assertEqual(audit[0]["requires_internal_approval"], "true")

    def test_schema_root_is_closed(self):
        schema = json.loads((ROOT / "services/heal-llm1-payload-v7/llm1_group_payload_v7.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])

    def test_v6_post_extension_compaction_trims_only_optional_excerpts(self):
        payload = {
            "clinical_evidence_summary": {
                "assertion_groups": [{
                    "representative_assertion": {
                        "evidence_id": "e1", "variant_ref": "v1", "description_excerpt": "d" * 600,
                    },
                }],
            },
            "publication_evidence_digest": {
                "selected_publications": [{"evidence_id": "e2", "abstract_excerpt": "a" * 900}],
            },
            "compression_metadata": {"strategy": "deterministic", "compression_level": 0},
        }
        self.assertTrue(v6.compact_v6_optional_text(payload))
        assertion = payload["clinical_evidence_summary"]["assertion_groups"][0]["representative_assertion"]
        publication = payload["publication_evidence_digest"]["selected_publications"][0]
        self.assertEqual(len(assertion["description_excerpt"]), 300)
        self.assertEqual(len(publication["abstract_excerpt"]), 450)
        self.assertEqual(assertion["evidence_id"], "e1")
        self.assertEqual(assertion["variant_ref"], "v1")
        self.assertEqual(payload["compression_metadata"]["compression_level"], 2)

    def test_server_exposes_v7_cards_and_internal_review(self):
        source = (ROOT / "server/dev-api.js").read_text(encoding="utf-8")
        self.assertIn("/llm1-group-cards", source)
        self.assertIn("/llm1-internal-review", source)
        self.assertIn("runInternalAutoLlm1", source)


if __name__ == "__main__":
    unittest.main()
