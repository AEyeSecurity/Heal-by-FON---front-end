from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "services" / "heal-tier1-curation-v2" / "build_human_review_foundation.py"
SPEC = importlib.util.spec_from_file_location("human_review_foundation", SCRIPT)
FOUNDATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(FOUNDATION)

SIGNED_SPEC = importlib.util.spec_from_file_location(
    "compile_signed_gold", ROOT / "services" / "heal-tier1-curation-v2" / "compile_signed_gold.py"
)
SIGNED = importlib.util.module_from_spec(SIGNED_SPEC)
assert SIGNED_SPEC.loader is not None
SIGNED_SPEC.loader.exec_module(SIGNED)


def proposal() -> dict:
    return {
        "Grupo": "PEMT:T1.3", "Gen": "PEMT", "Segmento": "holdout",
        "Estados aceptables": "approved", "¿Contexto utilizable?": "Sí",
        "Techo de inferencia": "initial_guide_candidate", "Limitaciones requeridas": "No diagnosis.",
        "Revisor": "Pendiente de firma humana final", "Fecha revisión": "2026-08-10",
        "Notas del revisor": "Correct identity before release.", "Hash del paquete": "a" * 64,
        "Fuentes válidas": "NCBI_GENE:10400; PMID:1", "Fuentes inválidas": "NCBI_GENE:4582",
        "Fuentes en ledger": "2",
    }


def evidence(evidence_id: str, title: str = "A study") -> dict:
    return {
        "ID de evidencia": evidence_id, "Fecha publicación": "2025-01-01", "Título": title,
    }


def packet_source(evidence_id: str, kind: str) -> dict:
    return {
        "evidence_id": evidence_id, "source_kind": kind,
        "citation_type": "database_record" if kind == "authoritative_database" else "journal_article",
        "title": evidence_id, "publication_date": None if kind == "authoritative_database" else "2025-01-01",
        "pmid": evidence_id.split(":", 1)[1] if evidence_id.startswith("PMID:") else None,
        "doi": None, "url": "https://example.test", "cohort_key": None, "derived_publication": False,
        "role_candidates": ["canonical_source"] if kind == "authoritative_database" else ["human_study"],
        "review_discovery_only": False, "identity_match": "exact", "eligibility_status": "eligible",
        "exclusion_reasons": [], "module_relevance_candidate": True, "abstract_or_summary": "Evidence",
        "metadata_complete": True, "retrieval_sha256": "a" * 64, "review_disposition": "valid",
    }


class HumanReviewFoundationTests(unittest.TestCase):
    def test_pending_signature_and_packet_mismatch_cannot_release(self):
        review = FOUNDATION.group_review(
            proposal(), [evidence("NCBI_GENE:4582"), evidence("PMID:1")],
            [evidence("NCBI_GENE:4582"), evidence("PMID:1")],
            [{"Base de datos": "NCBI_Gene", "Estado": "available"}],
        )
        self.assertFalse(review["release_ready"])
        self.assertIn("human_signature_pending", review["technical_flags"])
        self.assertIn("authoritative_identity_mismatch", review["technical_flags"])
        self.assertIn("manual_valid_evidence_missing_from_ledger", review["technical_flags"])
        self.assertIn("manual_invalid_evidence_still_selected", review["technical_flags"])

    def test_duplicate_selected_title_is_a_lineage_blocker(self):
        card = proposal()
        card["Fuentes válidas"] = "PMID:1; PMID:2"
        card["Fuentes inválidas"] = ""
        review = FOUNDATION.group_review(
            card, [evidence("PMID:1", "Same title"), evidence("PMID:2", "Same title")],
            [evidence("PMID:1", "Same title"), evidence("PMID:2", "Same title")],
            [{"Base de datos": "NCBI_Gene", "Estado": "available"}],
        )
        self.assertIn("selected_publication_lineage_not_deduplicated", review["technical_flags"])

    def test_reconciled_packet_leaves_only_human_signature_pending(self):
        card = proposal()
        sources = [packet_source("NCBI_GENE:10400", "authoritative_database"), packet_source("PMID:1", "primary_publication")]
        selection_summary = {
            "ledger_total": 2, "selected_total": 2, "functional_selected": 0, "human_selected": 1,
            "conflict_or_null_selected": 0, "overflow_total": 0,
            "review_required_selected": 2, "review_excluded_total": 1,
        }
        packet = {
            "schema_version": "mechanism_evidence_packet_v2", "group_id": "PEMT:T1.3",
            "gene": {"symbol": "PEMT", "full_name": "PEMT"}, "approved_aliases": [],
            "module": {"module_id": "T1.3", "name": "Nutrients", "purpose": "Purpose", "system_within_module": "System", "explicit_exclusions": "No diagnosis"},
            "evidence_cutoff": "2026-07-28", "retrieved_at": "2026-08-14T00:00:00Z", "database_snapshots": [],
            "source_ledger": sources, "selected_evidence_ids": ["NCBI_GENE:10400", "PMID:1"],
            "selection_summary": selection_summary,
            "human_review_policy": {"proposal_sha256": "b" * 64, "valid_evidence_ids": ["NCBI_GENE:10400", "PMID:1"], "invalid_evidence_ids": ["NCBI_GENE:4582"]},
        }
        packet["packet_sha256"] = FOUNDATION.cv2.sha256_json(packet)
        review = FOUNDATION.reconciled_group_review(card, packet)
        self.assertEqual(review["technical_flags"], ["human_signature_pending"])
        self.assertEqual(review["reconciliation"]["currently_selected_ncbi_evidence_id"], "NCBI_GENE:10400")

    def test_signed_gold_requires_explicit_complete_approval(self):
        packets = {}
        groups = {}
        signed_rows = []
        for group_id in FOUNDATION.GOLD_GROUPS:
            gene, module = group_id.split(":", 1)
            sources = [packet_source("NCBI_GENE:1", "authoritative_database"), packet_source("PMID:1", "primary_publication")]
            item = {
                "schema_version": "mechanism_evidence_packet_v2", "group_id": group_id,
                "gene": {"symbol": gene, "full_name": gene}, "approved_aliases": [],
                "module": {"module_id": module, "name": "Module", "purpose": "Purpose", "system_within_module": "System", "explicit_exclusions": "No diagnosis"},
                "evidence_cutoff": "2026-07-28", "retrieved_at": "2026-08-14T00:00:00Z", "database_snapshots": [],
                "source_ledger": sources, "selected_evidence_ids": ["NCBI_GENE:1", "PMID:1"],
                "selection_summary": {"ledger_total": 2, "selected_total": 2, "functional_selected": 0, "human_selected": 1, "conflict_or_null_selected": 0, "overflow_total": 0, "review_required_selected": 2, "review_excluded_total": 0},
            }
            item["packet_sha256"] = FOUNDATION.cv2.sha256_json(item)
            packets[group_id] = item
            groups[group_id] = {
                "technical_flags": ["human_signature_pending"],
                "split": "calibration" if group_id in FOUNDATION.cv2.CALIBRATION_GROUPS else "holdout",
                "scientific_status_alternatives": ["approved"],
            }
            signed_rows.append({
                "Grupo": group_id, "Aprobación final": "APROBADO", "Revisor final": "Reviewer",
                "Fecha firma": "2026-08-14", "Hash paquete reconciliado": item["packet_sha256"],
                "Estado propuesto": "approved", "Estados aceptables": "approved", "Contexto utilizable": "Sí",
                "Techo de inferencia": "context_only", "Direcciones aceptables": "supports_relation",
                "Fuentes incluidas": "NCBI_GENE:1 | PMID:1", "Fuentes excluidas": "",
                "Limitaciones": "No diagnosis", "Correcciones": "",
            })
        gold = SIGNED.compile_gold({"groups": groups}, packets, signed_rows)
        self.assertEqual(gold["approval_status"], "approved")
        self.assertEqual(len(gold["cards"]), 12)
        signed_rows[0]["Aprobación final"] = "PENDIENTE"
        with self.assertRaisesRegex(ValueError, "Final approval is missing"):
            SIGNED.compile_gold({"groups": groups}, packets, signed_rows)


if __name__ == "__main__":
    unittest.main()
