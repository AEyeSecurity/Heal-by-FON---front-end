import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "services" / "heal-grouped-prototype" / "client_readiness.py"
REPLAY_PATH = ROOT / "tools" / "replay_grouped_client_readiness.py"
RUNNER_PATH = ROOT / "services" / "heal-grouped-prototype" / "run_grouped_prototype.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLIENT = load("heal_client_readiness_test", MODULE_PATH)
REPLAY = load("heal_client_replay_test", REPLAY_PATH)
RUNNER = load("heal_grouped_runner_client_test", RUNNER_PATH)


def card(index, status, module="T1.1"):
    group_id = f"GENE{index}:{module}"
    return {
        "group_id": group_id, "gene": f"GENE{index}", "module_id": module, "module_name": "Módulo",
        "status": status,
        "coverage_status": "not_covered_by_prototype_snapshot" if status == "not_covered_by_prototype_snapshot" else "covered_by_prototype_snapshot",
        "eligible_for_llm2": status == "valid",
        "inference_mode": "context_only" if status == "valid" else "abstained_insufficient_evidence",
        "final_confidence_level": "Low" if status == "valid" else "Abstain",
        "review_priority": "none", "requires_professional_review": False,
        "scientific_inference_ceiling": "context_only", "effective_runtime_ceiling": "context_only",
        "runtime_variant_gate": {"eligible": False, "reason_codes": ["no_direct_evidence"]},
        "input_completeness_mode": "observed_variants_only",
        "focus_variant_refs": [f"rs{index}"] if status == "valid" else [],
        "evidence_used": [{"evidence_id": f"PMID:{index}", "source": "PubMed"}] if status == "valid" else [],
        "evidence_limitations": [
            "The mechanism registry status is draft and cannot support an individual conclusion.",
            "PharmGKB returned a provider error; this is not evidence of benignity.",
            "An identity-unresolved record was excluded.",
        ] if status == "valid" else [],
        "interpretation_one_sentence_es": "Interpretación contextual válida.",
        "interpretation_one_sentence_en": "Valid contextual interpretation.",
        "interpretation_long_es": "El registro del mecanismo está en borrador. El snapshot firmado permite contexto.",
        "interpretation_long_en": "The mechanism registry status is draft. The signed snapshot permits context.",
        "technical_interpretation_es": "Legacy payload. Evidencia válida del snapshot firmado.",
        "technical_interpretation_en": "Legacy mechanism. Valid signed-snapshot evidence.",
        "confidence_rationale_es": "Confianza baja.", "confidence_rationale_en": "Low confidence.",
        "family_notes_es": "Uso orientativo.", "family_notes_en": "For guidance only.",
    }


class GroupedClientReadinessTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HEAL_OPENAI_API_KEY", None)
        self.cards = (
            [card(index, "valid", f"T1.{index % 6 + 1}") for index in range(60)] +
            [card(index + 60, "covered_no_observed_variant", f"T1.{index % 6 + 1}") for index in range(45)] +
            [card(index + 105, "not_covered_by_prototype_snapshot", f"T1.{index % 6 + 1}") for index in range(75)]
        )
        self.coverage = {"canonical_group_count": 180, "covered_count": 105, "not_covered_count": 75}
        self.llm2 = {
            "report_title_es": "HEAL by FON - Prototipo de desarrollo",
            "key_findings": [
                {"group_id": self.cards[index]["group_id"], "headline_es": f"Hallazgo {index + 1}", "explanation_es": "Contexto priorizado."}
                for index in range(5)
            ],
            "disclaimer_es": "Inferencias generadas por una LLM; no constituyen diagnóstico.",
        }

    def test_closed_coverage_and_prioritization_counts_are_distinct(self):
        downstream = CLIENT.build_downstream_result(self.cards, self.coverage, self.llm2, external_evidence_partial=True)
        counts = downstream["coverage"]
        self.assertEqual(counts["canonical_group_count"], counts["scientifically_covered_count"] + counts["not_covered_count"])
        self.assertEqual(counts["scientifically_covered_count"], counts["valid_interpretation_count"] + counts["covered_no_observed_variant_count"])
        self.assertEqual(counts["valid_interpretation_count"], 60)
        self.assertEqual(counts["prioritized_finding_count"], 5)

    def test_legacy_authority_removed_but_audit_input_unchanged(self):
        original = json.dumps(self.cards, ensure_ascii=False)
        downstream = CLIENT.build_downstream_result(self.cards, self.coverage, self.llm2, external_evidence_partial=True)
        client = CLIENT.build_client_result(downstream)
        clean = json.dumps({"downstream": downstream, "client": client}, ensure_ascii=False)
        self.assertRegex(original.lower(), r"draft|legacy")
        self.assertNotRegex(clean.lower(), r"mechanism registry status is draft|legacy mechanism|legacy payload|registro del mecanismo está en borrador")

    def test_source_failure_and_unresolved_identity_become_safe_limitations(self):
        downstream = CLIENT.build_downstream_result(self.cards, self.coverage, self.llm2, external_evidence_partial=True)
        client = CLIENT.build_client_result(downstream)
        first = client["cards"][0]
        combined = " ".join(first["limitations_es"]).lower()
        self.assertIn("no se interpreta como benignidad", combined)
        self.assertIn("no pudieron resolverse", combined)
        self.assertNotIn("provider error", combined)
        self.assertNotIn("pharmgkb", combined)

    def test_internal_enums_are_humanized_for_client_text(self):
        value = "Clasificación benigna_o_probablemente_benigna; not_reported; risk_factor; context_only."
        cleaned = CLIENT.clean_authoritative_text(value).lower()
        self.assertIn("benigna o probablemente benigna", cleaned)
        self.assertIn("sin clasificación reportada", cleaned)
        self.assertIn("factor de riesgo", cleaned)
        self.assertIn("interpretación contextual", cleaned)
        self.assertNotIn("_", cleaned)

    def test_report_uses_prioritized_not_valid_finding_language(self):
        downstream = CLIENT.build_downstream_result(self.cards, self.coverage, self.llm2, external_evidence_partial=True)
        client = CLIENT.build_client_result(downstream)
        view = CLIENT.build_report_view_model(client, self.llm2, "fixture.vcf")
        text = json.dumps(view, ensure_ascii=False).lower()
        self.assertIn("hallazgo priorizado", text)
        self.assertNotIn("hallazgo(s)", text)
        self.assertNotIn("hallazgo(s) válido(s)", text)
        self.assertEqual(view["valid_interpretation_count"], 60)
        self.assertEqual(view["prioritized_finding_count"], 5)
        self.assertTrue(view["readiness"]["prototype_e2e_v1_closed"])
        self.assertEqual(view["readiness"]["formal_validation_readiness"], "pending_new_unseen_holdout")

    def test_client_coverage_rows_use_human_labels_only(self):
        downstream = CLIENT.build_downstream_result(self.cards, self.coverage, self.llm2)
        client = CLIENT.build_client_result(downstream)
        rows = CLIENT.build_client_coverage_rows(client)
        self.assertEqual(len(rows), 180)
        self.assertEqual(set(rows[0]), {"gen", "modulo", "cobertura_cientifica", "variante_foco", "resultado"})
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("not_covered_by_prototype_snapshot", serialized)
        self.assertNotIn("covered_no_observed_variant", serialized)

    def test_coverage_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "coverage_"):
            CLIENT.build_downstream_result(self.cards[:-1], self.coverage, self.llm2)

    def test_js_public_projection_removes_legacy_authority(self):
        script = """
          import { projectLegacyCardsForClient } from './server/grouped-client-contracts.js';
          const cards = projectLegacyCardsForClient([{group_id:'G:T1.1',gene:'G',module_id:'T1.1',status:'valid',coverage_status:'covered_by_prototype_snapshot',inference_mode:'context_only',final_confidence_level:'Low',interpretation_one_sentence_es:'Contexto válido.',interpretation_long_es:'El registro del mecanismo está en borrador. Contexto firmado.',focus_variant_refs:['rs1'],evidence_used:[{evidence_id:'PMID:1',source:'PubMed'}]}]);
          console.log(JSON.stringify(cards));
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertNotIn("borrador", result.stdout.lower())
        self.assertNotIn("PMID:1", result.stdout)
        self.assertIn("Contexto firmado", result.stdout)

    def test_api_audit_requires_both_tokens_and_client_is_default(self):
        source = (ROOT / "server" / "dev-api.js").read_text(encoding="utf-8")
        start = source.index('app.get("/api/vcf-canon-matches/:jobId/grouped-prototype/audit-summary"')
        section = source[start:start + 1600]
        self.assertIn("hasUploadAccessToken", section)
        self.assertIn("requireCurationAccess", section)
        self.assertIn("groupedClientSummary(job.result.groupedPrototype)", source)
        self.assertIn("Auditoría técnica sanitizada", (ROOT / "src" / "main.jsx").read_text(encoding="utf-8"))

    def test_operational_replay_is_read_only_and_makes_no_calls(self):
        run_dir = Path(r"F:\Heal by FON\data\runs\c70e0671-3753-432d-abb0-f7de033c6351")
        if not run_dir.exists():
            self.skipTest("Last real grouped run is unavailable")
        source_files = [
            run_dir / "grouped-prototype" / "llm1_cards.json",
            run_dir / "grouped-prototype" / "grouped_global_interpretation_v1.json",
        ]
        before = {path: REPLAY.sha256(path) for path in source_files}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "client-readiness-v1"
            manifest = REPLAY.process(run_dir, output)
            self.assertEqual(manifest["llm_calls"], 0)
            self.assertEqual(manifest["vep_runs"], 0)
            self.assertEqual(manifest["enrichment_runs"], 0)
            self.assertEqual(manifest["coverage"]["valid_interpretation_count"], 60)
            self.assertEqual(manifest["coverage"]["prioritized_finding_count"], 5)
            self.assertTrue((output / "HEAL_prototipo_cliente.docx").exists())
            self.assertTrue((output / "HEAL_prototipo_cliente.pdf").exists())
            self.assertTrue((output / "coverage_client.csv").exists())
            self.assertFalse((output / "raw_responses_audit.json").exists())
            with zipfile.ZipFile(output / "HEAL_prototipo_cliente.docx") as archive:
                docx_text = archive.read("word/document.xml").decode("utf-8").lower()
            pdf_text = " ".join(
                page.extract_text() or "" for page in PdfReader(output / "HEAL_prototipo_cliente.pdf").pages
            ).lower()
            for rendered_text in (docx_text, pdf_text):
                self.assertIn("procesamiento completado", rendered_text)
                self.assertIn("fuentes externas no estuvieron completamente disponibles", rendered_text)
                self.assertNotIn(run_dir.name.lower(), rendered_text)
                self.assertNotIn("grouped_client_result_v1", rendered_text)
                self.assertNotIn("pmid:", rendered_text)
                self.assertNotIn("not_reported", rendered_text)
        self.assertEqual(before, {path: REPLAY.sha256(path) for path in source_files})
        self.assertEqual(RUNNER.EXPECTED_REGISTRY_SHA256, RUNNER.sha256_file(RUNNER.ACTIVE_REGISTRY))


if __name__ == "__main__":
    unittest.main()
