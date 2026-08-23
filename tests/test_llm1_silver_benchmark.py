import importlib.util, json, tempfile, unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

silver = load("silver_test", ROOT / "services/heal-grouped-individual-interpretation/silver_standard.py")
runner = load("runner_test", ROOT / "services/heal-grouped-individual-interpretation/interpret_gene_module_groups.py")
benchmark = load("benchmark_test", ROOT / "tools/run_llm1_silver_benchmark.py")
guard = load("guard_test", ROOT / "tools/llm1_model_activation_guard.py")
optimizer = load("optimizer_test", ROOT / "tools/run_llm1_luna_prompt_optimization.py")
builder = load("builder_test", ROOT / "services/heal-llm1-payload-v6/build_llm1_payload_v6.py")
global_interpretation = load("global_test", ROOT / "services/heal-global-interpretation/interpret_global_profile.py")
final_report = load("report_test", ROOT / "services/heal-final-report/render_final_report.py")

class SilverTests(unittest.TestCase):
    def test_every_critical_error_has_a_negative_fixture(self):
        fixtures = json.loads((ROOT / "tests/fixtures/llm1_critical_errors.json").read_text(encoding="utf-8"))
        self.assertEqual({row["code"] for row in fixtures}, set(silver.CRITICAL_ERRORS))

    def test_three_model_schedule_has_75_trials(self):
        rows = benchmark.schedule({"Modelo A": "a", "Modelo B": "b", "Modelo C": "c"}, 7)
        self.assertEqual(len(rows), 75)
        self.assertTrue(all(sum(r["model_alias"] == alias and r["group_id"] == case for r in rows) == 5 for alias in ("Modelo A", "Modelo B", "Modelo C") for case in silver.PILOT_CASE_IDS))

    def test_reasoning_policies_are_explicit(self):
        self.assertEqual(benchmark.reasoning_effort("common_low", "gpt-5-mini"), "low")
        self.assertEqual(benchmark.reasoning_effort("minimum_supported", "gpt-5-mini"), "minimal")
        self.assertEqual(benchmark.reasoning_effort("minimum_supported", "gpt-5.6-luna"), "none")

    def test_prompt_profile_compatibility_is_strict(self):
        self.assertEqual(runner.resolve_prompt_profile("gpt-5-mini", "default_v6")[0], "default_v6")
        self.assertEqual(runner.resolve_prompt_profile("gpt-5.6-luna", "luna_v7")[0], "luna_v7")
        with self.assertRaises(ValueError): runner.resolve_prompt_profile("gpt-5.6-luna", "default_v6")
        with self.assertRaises(ValueError): runner.resolve_prompt_profile("gpt-5-mini", "luna_v7")

    def test_luna_prompt_is_generic_and_three_gate(self):
        prompt = (ROOT / "services/heal-grouped-individual-interpretation/prompt_grouped_llm1_luna_v7.md").read_text(encoding="utf-8")
        self.assertEqual(optimizer.prompt_errors(prompt), [])
        self.assertIn("Gate 1", prompt); self.assertIn("Gate 2", prompt); self.assertIn("Gate 3", prompt)

    def test_review_boolean_is_deterministic(self):
        self.assertFalse(silver.expected_professional_review("optional_contextual"))
        self.assertTrue(silver.expected_professional_review("recommended"))

    def test_evidence_variant_refs_allow_context_but_focus_refs_do_not(self):
        payload = {"focus_variant_evidence": [{"variant_ref": "rsFocus"}], "gwas_evidence_summary": {"variant_refs": ["rsContext"]}, "supporting_context": {"variant_refs_for_audit": ["rsAudit"]}}
        self.assertEqual(silver.focus_variant_refs(payload), {"rsFocus"})
        self.assertEqual(silver.collect_variant_refs(payload), {"rsFocus", "rsContext", "rsAudit"})

    def test_uninformative_condition_does_not_create_material_conflict(self):
        summary, _ = builder.clinvar_conflict_semantics([
            {"variant_key": "v1", "conditions": "not provided", "normalized_classification": "benign_or_likely_benign"},
            {"variant_key": "v1", "conditions": "not provided", "normalized_classification": "pathogenic_or_likely_pathogenic"},
        ])
        self.assertEqual(summary["same_condition_conflict_variant_keys"], [])
        summary, _ = builder.clinvar_conflict_semantics([
            {"variant_key": "v1", "conditions": "Condition X", "normalized_classification": "benign_or_likely_benign"},
            {"variant_key": "v1", "conditions": "Condition X", "normalized_classification": "pathogenic_or_likely_pathogenic"},
        ])
        self.assertEqual(summary["same_condition_conflict_variant_keys"], ["v1"])

    def test_valid_but_excluded_gwas_is_context_only(self):
        rows = builder.apply_gwas_registry([{"approved_symbol": "G", "module_id": "T", "trait_id": "X", "allele_confirmed_significant_count": "2", "independent_publication_count": "2"}], [{"gene": "G", "module_id": "T", "trait_id": "X", "relevance_status": "valid_but_excluded"}])
        self.assertEqual(rows[0]["evidence_band"], "context_only")

    def test_over_referral_gate_makes_model_ineligible(self):
        records = []
        for case in silver.PILOT_CASE_IDS:
            for repetition in range(1, 6):
                records.append({"group_id": case, "repetition": repetition, "weighted_score": 90, "contract_valid": True, "critical_error_codes": [], "inference_mode": "context_only", "final_confidence_level": "Low", "review_priority": "optional_contextual", "unjustified_over_referral": case == "PEMT:T1.3" and repetition <= 2, "unjustified_abstention": False, "cost_usd": .01, "latency_seconds": 2})
        self.assertFalse(silver.aggregate_model(records)["eligible"])

    def test_none_optional_are_one_stability_band(self):
        records = []
        for case in silver.PILOT_CASE_IDS:
            for repetition in range(1, 6):
                records.append({"group_id": case, "repetition": repetition, "weighted_score": 90, "contract_valid": True, "critical_error_codes": [], "inference_mode": silver.CASE_BEHAVIOR[case]["mode"][0], "final_confidence_level": silver.CASE_BEHAVIOR[case]["confidence"][0], "review_priority": "none" if repetition % 2 else "optional_contextual", "unjustified_over_referral": False, "unjustified_abstention": False, "cost_usd": .01, "latency_seconds": 2})
        self.assertTrue(silver.aggregate_model(records)["eligible"])

    def test_calibration_schedule_and_semantic_checks(self):
        safe = {"inference_mode": "context_only", "final_confidence_level": "Low", "review_priority": "none", "myth_correction_required": False,
                "interpretation_one_sentence_en": "A population association is available.", "interpretation_long_en": "This population association does not establish causality or individual risk.", "technical_interpretation_en": "Population context only.", "confidence_rationale_en": "Limited evidence.", "family_notes_en": "Context only.", "recommended_next_review_step_en": "No patient action or professional review is required.",
                "interpretation_one_sentence_es": "Existe una asociacion poblacional.", "interpretation_long_es": "Esta asociacion poblacional no establece causalidad ni riesgo individual.", "technical_interpretation_es": "Solo contexto poblacional.", "confidence_rationale_es": "Evidencia limitada.", "family_notes_es": "Solo contexto.", "recommended_next_review_step_es": "No se requiere accion ni revision profesional."}
        self.assertEqual(optimizer.behavior_errors("IL6:T1.4", safe), [])
        unsafe = dict(safe); unsafe["recommended_next_review_step_en"] = "Obtain laboratory testing and consult a specialist."
        self.assertIn("behavior:new_data_request", optimizer.behavior_errors("IL6:T1.4", unsafe))

    def test_atomic_runtime_pair_and_incompatible_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / "runtime.env"; env.write_text("X=1\nHEAL_LLM1_MODEL=gpt-5-mini\n", encoding="utf-8")
            guard.set_runtime(env, "gpt-5.6-luna", "luna_v7")
            text = env.read_text(encoding="utf-8")
            self.assertIn("HEAL_LLM1_MODEL=gpt-5.6-luna", text)
            self.assertIn("HEAL_LLM1_PROMPT_PROFILE=luna_v7", text)
            with self.assertRaises(ValueError): guard.set_runtime(env, "gpt-5-mini", "luna_v7")

    def test_rollback_restores_pair_quarantines_and_queues_same_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); env = root / "runtime.env"; env.write_text("HEAL_LLM1_MODEL=gpt-5.6-luna\nHEAL_LLM1_PROMPT_PROFILE=luna_v7\nHEAL_LLM1_REASONING_EFFORT=low\n", encoding="utf-8")
            output = root / "candidate.json"; output.write_text("{}", encoding="utf-8")
            state = {"env_file": str(env), "restart_script": "restart.ps1", "process_command_match": "server/dev-api.js", "health_url": "http://health"}
            with mock.patch.object(guard, "restart"), mock.patch.object(guard, "wait_health"):
                guard.rollback(root, state, [{"output_path": str(output), "payload_path": "payload.json"}])
            text = env.read_text(encoding="utf-8")
            self.assertIn("HEAL_LLM1_MODEL=gpt-5-mini", text); self.assertIn("HEAL_LLM1_PROMPT_PROFILE=default_v6", text); self.assertIn("HEAL_LLM1_REASONING_EFFORT=low", text)
            self.assertTrue((root / "quarantine/candidate.json").is_file())
            queue = json.loads((root / "regeneration_queue.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(queue["payload_path"], "payload.json"); self.assertEqual(queue["prompt_profile"], "default_v6")

    def test_five_identical_candidate_failures_pause_calibration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                path = root / f"candidate-{index:02d}"; path.mkdir()
                (path / "calibration_report.json").write_text(json.dumps({"passed": False, "dominant_failure_signature": "same_failure"}), encoding="utf-8")
            self.assertEqual(optimizer.latest_five_same_failure(root), "same_failure")

    def test_surveillance_requires_50_and_axis_coverage(self):
        records = []
        axes = sorted(guard.AXES)
        for i in range(50): records.append({"technical_status": "valid", "axis": axes[i % 3], "contract_valid": True, "critical_error_codes": []})
        self.assertTrue(guard.window_status(records)["release_ready"])
        records[0]["critical_error_codes"] = ["invented_identity"]
        self.assertFalse(guard.window_status(records)["release_ready"])

    def test_v6_normalization_adds_provenance(self):
        output = {"review_priority": "none", "review_reason_codes": [], "myth_correction_required": True}
        normalized = runner.normalize_output(output, {}, "model", False)
        self.assertEqual(normalized["interpretation_provenance"], "llm_generated")
        self.assertEqual(normalized["disclaimer_required"], "true")
        self.assertEqual(normalized["myth_correction_required"], "true")

    def test_llm2_preserves_notice_and_myth_flag(self):
        compact = global_interpretation.compact_variant({"review_priority": "optional_contextual", "review_reason_codes": "[]", "inference_mode": "initial_guide", "myth_correction_required": "true", "interpretation_provenance": "llm_generated", "disclaimer_required": "true"}, "es")
        self.assertTrue(compact["myth_correction_required"])
        self.assertTrue(compact["disclaimer_required"])

    def test_final_report_discloses_llm_guidance_when_marked(self):
        xml = final_report.document_xml({"global_report": {"report_title": "Test"}}, {"language_mode": "es", "disclaimer_required": True}, {})
        self.assertIn("inferencias iniciales generadas por un modelo de lenguaje", xml)

if __name__ == "__main__": unittest.main()
