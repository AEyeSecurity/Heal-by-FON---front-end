from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "heal-tier1-curation-v2"
sys.path.insert(0, str(SERVICE))
import curation_v2 as cv2  # noqa: E402


EVIDENCE_BUILDER_SPEC = importlib.util.spec_from_file_location(
    "tier1_evidence_builder", SERVICE / "build_evidence_packets.py"
)
EVIDENCE_BUILDER = importlib.util.module_from_spec(EVIDENCE_BUILDER_SPEC)
assert EVIDENCE_BUILDER_SPEC.loader is not None
EVIDENCE_BUILDER_SPEC.loader.exec_module(EVIDENCE_BUILDER)


def source(evidence_id, kind="primary_publication", roles=None, date="2025-01-01", cohort=None, derived=False):
    return {
        "evidence_id": evidence_id, "source_kind": kind,
        "citation_type": "database_record" if kind == "authoritative_database" else ("review" if kind == "review" else "journal_article"),
        "title": evidence_id, "publication_date": None if kind == "authoritative_database" else date,
        "pmid": evidence_id.split(":", 1)[1] if evidence_id.startswith("PMID:") else None,
        "doi": None, "url": "https://example.test/" + evidence_id, "cohort_key": cohort,
        "derived_publication": derived, "role_candidates": roles or [], "review_discovery_only": kind == "review",
        "identity_match": "exact", "module_relevance_candidate": True, "abstract_or_summary": "complete methods and results",
        "eligibility_status": "eligible", "exclusion_reasons": [],
        "metadata_complete": True, "retrieval_sha256": "a" * 64,
    }


def packet(extra_sources=None):
    ledger = [
        source("NCBI_GENE:1", "authoritative_database", ["canonical_source", "molecular_function"]),
        source("PMID:1", roles=["functional_study"]),
        source("PMID:2", roles=["human_study", "variant_specific"], cohort="cohort-2"),
    ] + list(extra_sources or [])
    selected, summary = cv2.select_sources(ledger)
    result = {
        "schema_version": "mechanism_evidence_packet_v2", "group_id": "GENE:T1.1",
        "gene": {"symbol": "GENE", "full_name": "Gene name"}, "approved_aliases": [],
        "module": {"module_id": "T1.1", "name": "Module", "purpose": "Purpose", "system_within_module": "System", "explicit_exclusions": "No diagnosis"},
        "evidence_cutoff": "2026-07-28", "retrieved_at": "2026-08-05T00:00:00Z",
        "database_snapshots": [], "source_ledger": ledger, "selected_evidence_ids": selected, "selection_summary": summary,
    }
    result["packet_sha256"] = cv2.sha256_json(result)
    return result


def quality(score=80):
    return {name: score for name in cv2.WEIGHTS}


def decision(status="approved", ceiling="initial_guide_candidate", context=True):
    assessments = [
        {"evidence_id": "PMID:1", "roles": ["functional"], "supports_direction": "positive", "human_applicability": True, "functional_compatibility": True, "quality": quality()},
        {"evidence_id": "PMID:2", "roles": ["human"], "supports_direction": "positive", "human_applicability": True, "functional_compatibility": False, "quality": quality()},
    ]
    approved = status in {"approved", "approved_with_conflict"}
    return {
        "schema_version": "mechanism_curation_v2_decision", "group_id": "GENE:T1.1",
        "core_status": status, "context_usable": context, "inference_ceiling": ceiling,
        "identity_exact": True, "module_relation_direct": True,
        "evidence_ids": {"functional": ["PMID:1"], "human": ["PMID:2"], "canonical": ["NCBI_GENE:1"], "conflict_or_null": []},
        "source_assessments": assessments, "direction_assessment": cv2.compute_direction(assessments),
        "decision_reason_codes": ["authoritative_plus_primary", "human_and_functional_applicable"],
        "limitations": ["Evidence quality and applicability remain bounded by the selected studies."], "confidence": 0.9,
        "expert_review_basis": {
            "strong_evidence": approved, "material_scientific_conflict": False,
            "conflict_evidence_ids": [], "runtime_variant_context_required": True,
        },
    }


class Tier1CurationV2Tests(unittest.TestCase):
    def test_ncbi_identity_requires_exact_official_symbol_not_alias(self):
        result = {
            "4582": {"name": "MUC1", "otheraliases": "PEMT"},
            "10400": {"name": "PEMT", "otheraliases": ""},
        }
        selected = EVIDENCE_BUILDER.select_exact_official_ncbi_record(result, ["4582", "10400"], "PEMT")
        self.assertEqual(selected[0], "10400")
        self.assertIsNone(EVIDENCE_BUILDER.select_exact_official_ncbi_record({"4582": result["4582"]}, ["4582"], "PEMT"))

    def test_publication_metadata_mismatch_is_not_silently_deduplicated(self):
        first = source("PMID:1")
        first["pmid"] = "123"; first["title"] = "First article"; first["doi"] = "10.1000/first"
        second = source("PMID:2")
        second["pmid"] = "123"; second["title"] = "Different article"; second["doi"] = "10.1000/second"
        errors = cv2.publication_metadata_errors([first, second])
        self.assertIn("pmid_title_mismatch:123", errors)
        self.assertIn("pmid_doi_mismatch:123", errors)

    def test_doi_only_primary_publication_is_traceable(self):
        row = source("DOI:10.1000/example")
        row["pmid"] = None
        row["title"] = "A DOI indexed primary article"
        row["doi"] = "10.1000/example"
        self.assertEqual(cv2.publication_metadata_errors([row]), [])

    def test_valid_context_and_initial_guide_candidate(self):
        self.assertEqual(cv2.validate_decision(decision(), packet()), [])

    def test_limitations_must_be_nonempty(self):
        d = decision()
        d["limitations"] = ["   "]
        self.assertIn("limitations_missing_or_empty", cv2.validate_decision(d, packet()))

    def test_array_uniqueness_is_enforced_deterministically(self):
        d = decision()
        d["evidence_ids"]["functional"] = ["PMID:1", "PMID:1"]
        d["source_assessments"][0]["roles"] = ["functional", "functional"]
        d["decision_reason_codes"] = ["authoritative_plus_primary", "authoritative_plus_primary"]
        errors = cv2.validate_decision(d, packet())
        self.assertIn("duplicate_evidence_id_within_role", errors)
        self.assertIn("duplicate_assessment_role", errors)
        self.assertIn("duplicate_decision_reason_code", errors)

    def test_gold_scoring_rejects_evidence_not_signed_valid(self):
        d = decision()
        gold = {
            "group_id": "GENE:T1.1", "acceptable_core_statuses": ["approved"],
            "context_usable": True, "acceptable_inference_ceilings": ["initial_guide_candidate"],
            "acceptable_directions": [d["direction_assessment"]["dominant_direction"]],
            "valid_evidence_ids": ["NCBI_GENE:1", "PMID:1"], "invalid_evidence_ids": [],
        }
        errors = cv2.evaluate_against_gold(d, gold)
        self.assertIn("gold:evidence_not_signed_valid", errors)

    def test_authoritative_database_alone_cannot_approve(self):
        p = packet()
        p["source_ledger"] = [p["source_ledger"][0]]
        p["selected_evidence_ids"], p["selection_summary"] = cv2.select_sources(p["source_ledger"])
        p["packet_sha256"] = cv2.sha256_json({k: v for k, v in p.items() if k != "packet_sha256"})
        d = decision(ceiling="context_only")
        d["evidence_ids"] = {"functional": [], "human": [], "canonical": ["NCBI_GENE:1"], "conflict_or_null": []}
        d["source_assessments"] = []
        d["direction_assessment"] = cv2.compute_direction([])
        errors = cv2.validate_decision(d, p)
        self.assertIn("unsupported_approval", errors)
        self.assertIn("authoritative_database_alone_cannot_approve", errors)

    def test_review_cannot_support_approval(self):
        p = packet([source("PMID:3", kind="review", roles=["functional_study", "human_study"])])
        d = decision()
        d["evidence_ids"] = {"functional": ["PMID:3"], "human": ["PMID:3"], "canonical": ["NCBI_GENE:1"], "conflict_or_null": []}
        d["source_assessments"] = [{"evidence_id": "PMID:3", "roles": ["functional", "human"], "supports_direction": "positive", "human_applicability": True, "functional_compatibility": True, "quality": quality()}]
        d["direction_assessment"] = cv2.compute_direction(d["source_assessments"])
        self.assertIn("unsupported_approval", cv2.validate_decision(d, p))

    def test_cutoff_is_enforced(self):
        p = packet([source("PMID:99", date="2026-07-29")])
        self.assertIn("publication_after_cutoff:PMID:99", cv2.validate_packet(p))

    def test_after_cutoff_source_can_be_retained_but_not_selected(self):
        excluded = source("PMID:99", date="2026-07-29")
        excluded["eligibility_status"] = "excluded"; excluded["exclusion_reasons"] = ["publication_after_cutoff"]
        p = packet([excluded])
        self.assertNotIn("PMID:99", p["selected_evidence_ids"])
        self.assertEqual(cv2.validate_packet(p), [])

    def test_absence_is_withheld_not_rejected(self):
        d = decision(status="rejected", ceiling="none", context=False)
        d["decision_reason_codes"] = ["insufficient_primary_evidence"]
        self.assertIn("rejected_without_positive_evidence", cv2.validate_decision(d, packet()))
        d["core_status"] = "withheld"
        d["evidence_ids"] = {"functional": [], "human": [], "canonical": [], "conflict_or_null": []}
        d["source_assessments"] = []
        d["direction_assessment"] = cv2.compute_direction([])
        self.assertNotIn("rejected_without_positive_evidence", cv2.validate_decision(d, packet()))

    def test_same_cohort_is_not_independent_replication(self):
        p = packet([source("PMID:3", roles=["human_study"], cohort="shared"), source("PMID:4", roles=["functional_study"], cohort="shared", derived=True)])
        d = decision(status="approved", ceiling="context_only")
        d["evidence_ids"] = {"functional": ["PMID:4"], "human": ["PMID:3"], "canonical": [], "conflict_or_null": []}
        d["source_assessments"] = [
            {"evidence_id": "PMID:3", "roles": ["human"], "supports_direction": "positive", "human_applicability": True, "functional_compatibility": False, "quality": quality()},
            {"evidence_id": "PMID:4", "roles": ["functional"], "supports_direction": "positive", "human_applicability": False, "functional_compatibility": True, "quality": quality()},
        ]
        d["direction_assessment"] = cv2.compute_direction(d["source_assessments"])
        self.assertIn("unsupported_approval", cv2.validate_decision(d, p))

    def test_conflict_below_margin_cannot_approve(self):
        d = decision(status="approved_with_conflict", ceiling="context_only")
        d["source_assessments"][0]["quality"] = quality(75)
        d["source_assessments"][1]["supports_direction"] = "negative"
        d["source_assessments"][1]["quality"] = quality(70)
        d["direction_assessment"] = cv2.compute_direction(d["source_assessments"])
        self.assertLess(d["direction_assessment"]["margin"], 15)
        self.assertIn("conflict_margin_below_15_cannot_approve", cv2.validate_decision(d, packet()))

    def test_initial_guide_requires_human_and_functional(self):
        d = decision()
        d["source_assessments"][1]["human_applicability"] = False
        d["direction_assessment"] = cv2.compute_direction(d["source_assessments"])
        self.assertIn("initial_guide_without_applicable_human_and_functional_evidence", cv2.validate_decision(d, packet()))

    def test_semantic_v2_contextual_or_animal_evidence_cannot_raise_ceiling(self):
        p = packet()
        semantic = {
            "schema_version": "mechanism_curation_semantic_v2",
            "core_status": "approved", "identity_exact": True,
            "module_relation_direct": True,
            "used_evidence": [
                {
                    "evidence_id": "NCBI_GENE:1", "roles": ["canonical"],
                    "supports_direction": "positive", "human_applicability": True,
                    "functional_compatibility": True, "quality": quality(),
                    "core_conflict_class": "none", "core_conflict_rationale": "Canonical context.",
                },
                {
                    "evidence_id": "PMID:1", "roles": ["functional"],
                    "supports_direction": "positive", "human_applicability": False,
                    "functional_compatibility": True, "quality": quality(),
                    "core_conflict_class": "none", "core_conflict_rationale": "Animal-only functional model.",
                },
                {
                    "evidence_id": "PMID:2", "roles": ["human"],
                    "supports_direction": "positive", "human_applicability": True,
                    "functional_compatibility": False, "quality": quality(),
                    "core_conflict_class": "limited_generalizability",
                    "core_conflict_rationale": "Compositional human endpoint without individual applicability.",
                },
            ],
            "excluded_evidence": [], "scientific_reason_codes": [],
            "limitations": ["Evidence supports context but not an individual guide."], "confidence": 0.9,
        }
        normalized = cv2.normalize_semantic_assessment_v2(semantic, p)
        self.assertEqual(normalized["inference_ceiling"], "context_only")
        self.assertIn("mechanism_context_without_direct_individual_support", normalized["decision_reason_codes"])

    def test_semantic_v2_direct_primary_human_and_human_functional_evidence_raise_ceiling(self):
        p = packet()
        semantic = {
            "schema_version": "mechanism_curation_semantic_v2",
            "core_status": "approved", "identity_exact": True,
            "module_relation_direct": True,
            "used_evidence": [
                {
                    "evidence_id": "NCBI_GENE:1", "roles": ["canonical"],
                    "supports_direction": "not_applicable", "human_applicability": False,
                    "functional_compatibility": False, "quality": quality(),
                    "core_conflict_class": "none", "core_conflict_rationale": "Identity context.",
                },
                {
                    "evidence_id": "PMID:1", "roles": ["functional"],
                    "supports_direction": "positive", "human_applicability": True,
                    "functional_compatibility": True, "quality": quality(),
                    "core_conflict_class": "none", "core_conflict_rationale": "Direct human-compatible functional evidence.",
                },
                {
                    "evidence_id": "PMID:2", "roles": ["human"],
                    "supports_direction": "positive", "human_applicability": True,
                    "functional_compatibility": False, "quality": quality(),
                    "core_conflict_class": "none", "core_conflict_rationale": "Direct human evidence.",
                },
            ],
            "excluded_evidence": [], "scientific_reason_codes": [],
            "limitations": ["Individual applicability remains bounded by the studied population."], "confidence": 0.9,
        }
        normalized = cv2.normalize_semantic_assessment_v2(semantic, p)
        self.assertEqual(normalized["inference_ceiling"], "initial_guide_candidate")

    def test_arbitration_triggers(self):
        first, second = decision(), decision(status="withheld", ceiling="none", context=False)
        second["confidence"] = 0.7
        reasons = cv2.arbitration_reasons(first, second)
        self.assertIn("disagreement:core_status", reasons)
        self.assertIn("confidence_below_0_80", reasons)

    def test_expert_review_basis_requires_real_selected_conflict(self):
        d = decision(status="approved_with_conflict", ceiling="context_only")
        d["source_assessments"][1]["supports_direction"] = "negative"
        d["evidence_ids"]["conflict_or_null"] = ["PMID:2"]
        d["direction_assessment"] = cv2.compute_direction(d["source_assessments"])
        d["expert_review_basis"] = {
            "strong_evidence": True, "material_scientific_conflict": True,
            "conflict_evidence_ids": ["PMID:2"], "runtime_variant_context_required": True,
        }
        errors = cv2.validate_decision(d, packet())
        self.assertNotIn("expert_review_material_conflict_mismatch", errors)
        d["expert_review_basis"]["conflict_evidence_ids"] = ["PMID:404"]
        self.assertIn("expert_review_conflict_evidence_not_selected", cv2.validate_decision(d, packet()))

    def test_prompt_cannot_include_gold_identifiers(self):
        self.assertTrue(cv2.prompt_identifier_errors("Special case MTHFR should pass"))
        self.assertEqual(cv2.prompt_identifier_errors("Use general exact-identity rules"), [])

    def test_selection_is_bounded_and_preserves_canonical(self):
        sources = [source(f"PMID:{index}", roles=["functional_study", "human_study"]) for index in range(10, 50)]
        p = packet(sources)
        self.assertLessEqual(len(p["selected_evidence_ids"]), 20)
        self.assertIn("NCBI_GENE:1", p["selected_evidence_ids"])
        self.assertGreater(p["selection_summary"]["overflow_total"], 0)

    def test_selector_prefers_direct_human_evidence_and_deduplicates_lineage(self):
        contextual = source("PMID:10", roles=[], date="2026-01-01")
        direct_human = source("PMID:11", roles=["human_study", "variant_specific"], date="2010-01-01")
        preprint = source("PMID:12", roles=["functional_study"], date="2024-01-01", derived=True)
        final = source("PMID:13", roles=["functional_study"], date="2025-01-01")
        preprint["title"] = final["title"] = "A sufficiently distinctive shared publication title"
        selected, _ = cv2.select_sources([contextual, direct_human, preprint, final])
        self.assertIn("PMID:11", selected)
        self.assertIn("PMID:13", selected)
        self.assertNotIn("PMID:12", selected)

    def test_human_review_policy_pins_valid_and_excludes_invalid_evidence(self):
        valid = source("PMID:20", roles=["human_study"], date="2001-01-01")
        invalid = source("PMID:21", roles=["functional_study", "human_study"], date="2026-01-01")
        selected, summary = cv2.select_sources(
            [valid, invalid], required_evidence_ids={"PMID:20"}, excluded_evidence_ids={"PMID:21"},
        )
        self.assertIn("PMID:20", selected)
        self.assertNotIn("PMID:21", selected)
        self.assertEqual(summary["review_required_selected"], 1)
        self.assertEqual(summary["review_excluded_total"], 1)

    def test_human_review_policy_cannot_pin_review_or_after_cutoff(self):
        review = source("PMID:30", kind="review", roles=["human_study"])
        with self.assertRaisesRegex(ValueError, "not selectable"):
            cv2.select_sources([review], required_evidence_ids={"PMID:30"})
        future = source("PMID:31", roles=["human_study"], date="2026-07-29")
        future["eligibility_status"] = "excluded"
        future["exclusion_reasons"] = ["publication_after_cutoff"]
        with self.assertRaisesRegex(ValueError, "not selectable"):
            cv2.select_sources([future], required_evidence_ids={"PMID:31"})

    def test_review_policy_parser_preserves_signature_as_separate_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "proposal.csv"
            path.write_text(
                "Grupo,Fuentes válidas,Fuentes inválidas,Revisor\n"
                "PEMT:T1.3,NCBI_GENE:10400; PMID:1,NCBI_GENE:4582,Pendiente de firma humana final\n",
                encoding="utf-8-sig",
            )
            policy = EVIDENCE_BUILDER.review_policies(path)["PEMT:T1.3"]
            self.assertEqual(policy["valid_evidence_ids"], ["NCBI_GENE:10400", "PMID:1"])
            self.assertEqual(policy["invalid_evidence_ids"], ["NCBI_GENE:4582"])
            self.assertNotIn("reviewer", policy)

    def test_draft_is_not_automatically_withheld(self):
        spec = importlib.util.spec_from_file_location("old_tier1", ROOT / "tools" / "prepare_llm1_tier1_curation.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        rows, audit = module.prepare_mechanisms([{"gene": "GENE", "module_id": "T1.1", "curation_status": "draft"}])
        self.assertEqual(rows[0]["curation_status"], "draft")
        self.assertEqual(audit[0]["decision"], "blocked_draft_requires_mechanism_curation_v2")

    def test_gold_requires_complete_human_approval(self):
        packets = {group: {**packet(), "group_id": group} for group in cv2.GOLD_GROUPS}
        for p in packets.values():
            p["packet_sha256"] = cv2.sha256_json({k: v for k, v in p.items() if k != "packet_sha256"})
        manifest = cv2.gold_manifest_template(packets)
        self.assertIn("gold_not_approved", cv2.validate_gold_manifest(manifest, packets))

    def test_three_repeated_failures_pause_optimization(self):
        reports = [{"passed": False, "dominant_failure_signature": "same"} for _ in range(3)]
        self.assertEqual(cv2.repeated_dominant_failure(reports), "same")

    def test_phase_acceptance_keeps_calibration_and_holdout_disjoint(self):
        self.assertEqual(set(cv2.CALIBRATION_GROUPS) & set(cv2.HOLDOUT_GROUPS), set())
        self.assertEqual(set(cv2.CALIBRATION_GROUPS) | set(cv2.HOLDOUT_GROUPS), set(cv2.GOLD_GROUPS))
        report = cv2.phase_acceptance([], {"cards": []}, "calibration")
        self.assertFalse(report["complete"])
        self.assertEqual(set(report["expected_groups"]), set(cv2.CALIBRATION_GROUPS))

    def test_cli_contains_separate_freeze_and_single_use_holdout_commands(self):
        source_text = (SERVICE / "run_curation_v2.py").read_text(encoding="utf-8")
        self.assertIn('sub.add_parser("freeze-prompt")', source_text)
        self.assertIn('sub.add_parser("evaluate-holdout")', source_text)
        self.assertIn('sub.add_parser("run-candidate1-protocol")', source_text)
        self.assertIn("Optimizer accepts calibration-only reports", source_text)
        self.assertIn("Holdout is single-use", source_text)

    def test_manual_review_requires_all_approvals_and_stratified_nonapprovals(self):
        candidates = [
            {"group_id": "A:T1.1", "core_status": "approved", "evidence_selected_count": 4, "adjudicated": False},
            {"group_id": "B:T1.1", "core_status": "withheld", "evidence_selected_count": 0, "adjudicated": True},
        ]
        errors = cv2.validate_manual_review(candidates, [])
        self.assertIn("approved_group_not_manually_reviewed:A:T1.1", errors)
        self.assertTrue(any(error.startswith("nonapproval_sample_below_20_percent") for error in errors))

    def test_schemas_are_closed(self):
        for name in ("mechanism_evidence_packet_v2.schema.json", "mechanism_curation_v2.schema.json"):
            schema = json.loads((SERVICE / name).read_text(encoding="utf-8"))
            self.assertFalse(schema["additionalProperties"])

    def test_decision_schema_uses_responses_supported_uniqueness_gate(self):
        schema_text = (SERVICE / "mechanism_curation_v2.schema.json").read_text(encoding="utf-8")
        runner_text = (SERVICE / "run_curation_v2.py").read_text(encoding="utf-8")
        self.assertNotIn('"uniqueItems"', schema_text)
        self.assertIn("run_decision_schema_probe", runner_text)

    def test_calibration_stops_after_nonrecoverable_technical_failure(self):
        runner_text = (SERVICE / "run_curation_v2.py").read_text(encoding="utf-8")
        self.assertIn("def has_nonrecoverable_technical_failure", runner_text)
        self.assertIn("if has_nonrecoverable_technical_failure(record):", runner_text)

    def test_protocol_freezes_limits_and_audits_optimizer_failure(self):
        runner_text = (SERVICE / "run_curation_v2.py").read_text(encoding="utf-8")
        self.assertIn('"optimizer_max_output_tokens": args.optimizer_max_output_tokens', runner_text)
        self.assertIn('"status": "optimizer_failed_technical_no_candidate"', runner_text)


if __name__ == "__main__":
    unittest.main()
