import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


v6 = load_module("heal_llm1_payload_v6_test", ROOT / "services" / "heal-llm1-payload-v6" / "build_llm1_payload_v6.py")
interpreter = load_module(
    "heal_grouped_interpreter_v6_test",
    ROOT / "services" / "heal-grouped-individual-interpretation" / "interpret_gene_module_groups.py",
)


def row(transcript_summary: str, *, picked_gene="GENE", picked_tx="ENST1", alt="G"):
    return {
        "variant_key": "v1", "approved_symbol": "GENE", "module_id": "T1.1", "module_name": "Module",
        "system_within_module": "System", "tier": "Tier 1", "module_status": "Approved", "evidence_tier": "High",
        "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "100", "ref_vcf": "A", "alt_vcf": alt,
        "gt_alleles": "0/1", "zygosity": "heterozygous", "resolved_rsid": "rs100",
        "identity_match_class": "exact_coordinate_allele", "local_region_class": "mane_cds_overlap",
        "vep_status": "success", "vep_picked_gene_symbol": picked_gene, "vep_picked_transcript": picked_tx,
        "vep_transcript_summary": transcript_summary,
        "ensembl_populations": "pop=gnomAD:ALL; allele=A; freq=0.8|pop=gnomAD:ALL; allele=G; freq=0.2",
        "source_status_clinvar": "success", "source_status_ensembl_variation": "success",
        "source_status_myvariant": "not_found", "source_status_gwas": "not_found", "source_status_pharmgkb": "not_found",
        "focus_eligible": "true", "downstream_role": "focus_candidate", "attention_rank": "1", "attention_score": "120",
    }


class PayloadV6Tests(unittest.TestCase):
    def test_target_gene_transcript_replaces_global_picked_gene(self):
        source = row(
            "gene=OTHER; tx=ENST0; consequence=intron_variant; biotype=protein_coding|"
            "gene=GENE; tx=ENST2; consequence=missense_variant; biotype=protein_coding; "
            "hgvsc=ENST2:c.1A>G; hgvsp=ENSP2:p.Ala1Gly; cadd_phred=20",
            picked_gene="OTHER",
            picked_tx="ENST0",
        )
        enriched, audit, _ = v6.enrich_detail_row(source)
        self.assertEqual(audit["target_gene_annotation_status"], "alternative_transcript")
        self.assertEqual(enriched["vep_picked_transcript"], "ENST2")
        self.assertEqual(enriched["vep_hgvsp"], "ENSP2:p.Ala1Gly")
        self.assertEqual(enriched["focus_eligible"], "true")

    def test_missing_target_gene_is_removed_from_focus(self):
        source = row("gene=OTHER; tx=ENST0; consequence=missense_variant; biotype=protein_coding", picked_gene="OTHER")
        enriched, audit, _ = v6.enrich_detail_row(source)
        self.assertEqual(audit["target_gene_annotation_status"], "discordant_gene")
        self.assertEqual(enriched["focus_eligible"], "false")

    def test_mane_select_is_prioritized_within_target_gene(self):
        source = row(
            "gene=GENE; tx=ENST1; consequence=missense_variant; biotype=protein_coding|"
            "gene=GENE; tx=ENST2; consequence=synonymous_variant; biotype=protein_coding; mane_select=NM_1",
        )
        enriched, audit, _ = v6.enrich_detail_row(source)
        self.assertEqual(audit["selected_target_transcript"], "ENST2")
        self.assertEqual(audit["selected_mane_select"], "NM_1")
        self.assertEqual(enriched["vep_picked_transcript"], "ENST2")

    def test_local_utr_requires_target_transcript_utr_consequence(self):
        source = row("gene=GENE; tx=ENST1; consequence=intron_variant; biotype=protein_coding")
        source["local_region_class"] = "utr_overlap"
        enriched, audit, _ = v6.enrich_detail_row(source)
        self.assertEqual(audit["local_consequence_concordance"], "discordant")
        self.assertEqual(enriched["focus_eligible"], "false")

    def test_population_frequency_is_specific_to_observed_alt(self):
        frequency = v6.allele_specific_frequency(row("gene=GENE; tx=ENST1; consequence=missense_variant"))
        self.assertEqual(frequency["frequency_relation"], "observed_alt")
        self.assertEqual(frequency["observed_alt_frequency"], 0.2)
        self.assertEqual(frequency["other_allele_context"][0]["allele"], "A")

    def test_clinvar_same_condition_conflict_differs_from_cross_condition(self):
        assertions = [
            {"variant_key": "same", "conditions": "Condition A", "normalized_classification": "pathogenic_or_likely_pathogenic"},
            {"variant_key": "same", "conditions": "Condition A", "normalized_classification": "benign_or_likely_benign"},
            {"variant_key": "cross", "conditions": "Condition A", "normalized_classification": "benign_or_likely_benign"},
            {"variant_key": "cross", "conditions": "Condition B", "normalized_classification": "drug_response"},
        ]
        summary, audit = v6.clinvar_conflict_semantics(assertions)
        self.assertIn("same", summary["same_condition_conflict_variant_keys"])
        self.assertIn("cross", summary["cross_condition_heterogeneity_variant_keys"])
        by_key = {item["variant_key"]: item for item in audit}
        self.assertEqual(by_key["same"]["same_condition_conflict"], "true")
        self.assertEqual(by_key["cross"]["cross_condition_heterogeneity"], "true")

    def test_unreviewed_gwas_cannot_be_promoted(self):
        clusters = [{
            "approved_symbol": "GENE", "module_id": "T1.1", "trait_id": "EFO_1",
            "allele_confirmed_significant_count": "2", "independent_publication_count": "2",
            "direction_status": "consistent", "evidence_band": "moderate_contextual",
        }]
        unreviewed = v6.apply_gwas_registry(clusters, [])
        self.assertEqual(unreviewed[0]["module_relevance_status"], "unreviewed")
        self.assertNotEqual(unreviewed[0]["evidence_band"], "high_confidence_replicated")
        approved = v6.apply_gwas_registry(clusters, [{
            "gene": "GENE", "module_id": "T1.1", "trait_id": "EFO_1", "relevance_status": "approved",
        }])
        self.assertEqual(approved[0]["evidence_band"], "high_confidence_replicated")

    def test_v6_interpreter_rejects_ambiguous_focus(self):
        payload = {
            "payload_schema_version": "llm1_group_payload_v6", "execution_mode": "dry_run",
            "compression_metadata": {"estimated_tokens": 100},
            "gates": {"token_budget_ready": True}, "evidence_coverage": {"reconciled": True},
            "focus_variant_evidence": [{
                "variant_ref": "rs1", "target_gene_annotation": {"status": "discordant_gene"},
                "population": {"frequency_relation": "observed_alt"},
            }],
        }
        with self.assertRaisesRegex(ValueError, "target-gene confirmation"):
            interpreter.validate_v5_payload(payload, dry_run=True)

    def test_manifest_is_fixed_to_five_canaries(self):
        manifest = v6.build_manifest([])
        self.assertEqual(len(manifest), 5)
        self.assertEqual({item["group_id"] for item in manifest}, set(v6.PILOT_GROUPS))


if __name__ == "__main__":
    unittest.main()
