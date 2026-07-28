import csv
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


grouping = load_module(
    "heal_grouping_v3_test",
    ROOT / "services" / "heal-grouped-interpretation-prep" / "prepare_gene_module_group_payloads.py",
)
readiness = load_module(
    "heal_llm_readiness_test",
    ROOT / "services" / "heal-llm-readiness-audit" / "audit_llm_readiness_v2.py",
)


def base_row(key="v1", pos="100", local="mane_cds_overlap"):
    return {
        "variant_key": key,
        "variant_gene_module_id": f"{key}:GENE:T1.1",
        "approved_symbol": "GENE",
        "full_gene_name": "Example gene",
        "module_id": "T1.1",
        "module_name": "Example module",
        "system_within_module": "Example system",
        "tier": "Tier 1",
        "module_status": "Approved",
        "evidence_tier": "High",
        "module_purpose": "Purpose",
        "explicit_exclusions": "No diagnosis",
        "canonical_version": "v1",
        "assembly": "GRCh38",
        "chrom_vcf": "chr1",
        "pos_vcf": pos,
        "variant_start": pos,
        "ref_vcf": "A",
        "alt_vcf": "G",
        "resolved_rsid": f"rs{pos}",
        "candidate_rsid": f"rs{pos}",
        "identity_match_class": "exact_coordinate_allele",
        "vep_status": "success",
        "vep_target_gene_effect_status": "direct_target_transcript",
        "vep_most_severe_consequence": "missense_variant",
        "local_region_class": local,
        "clinvar_normalized_classification": "not_reported",
        "source_status_clinvar": "not_found",
        "source_status_ensembl_variation": "success",
        "source_status_myvariant": "success",
        "source_status_gwas": "not_found",
        "source_status_pharmgkb": "not_found",
        "vep_spliceai": "",
        "gwas_association_count": "0",
        "pharmgkb_clinical_annotation_count": "0",
        "pharmgkb_variant_annotation_count": "0",
    }


class GroupPayloadV3Tests(unittest.TestCase):
    def test_spliceai_zero_does_not_increase_attention(self):
        row = base_row()
        base_score, _, _ = grouping.attention_score(row)
        row["vep_spliceai"] = json.dumps({"DS_AG": 0, "DS_AL": 0, "DS_DG": 0, "DS_DL": 0})
        score, reasons, eligible = grouping.attention_score(row)
        self.assertTrue(eligible)
        self.assertEqual(score, base_score)
        self.assertIn("spliceai_no_signal=+0", reasons)

    def test_utr_intron_discordance_is_not_focus_eligible(self):
        row = base_row(local="utr_overlap")
        row["vep_most_severe_consequence"] = "intron_variant"
        _, reasons, eligible = grouping.attention_score(row)
        self.assertFalse(eligible)
        self.assertEqual(grouping.transcript_concordance(row), "discordant_utr_vs_intron")
        self.assertTrue(any("excluded_from_focus" in reason for reason in reasons))

    def test_process_builds_v3_dry_run_without_llm_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "projection.csv"
            rows = [base_row("v1", "100"), base_row("v2", "200", "utr_overlap")]
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            canonical_path = root / "canonical.csv"
            with canonical_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["gene", "module_id", "canonical_status", "callable", "hom_ref"])
                writer.writeheader()
                writer.writerow({"gene": "GENE", "module_id": "T1.1", "canonical_status": "observed_alt", "callable": "unknown", "hom_ref": "unknown"})
            mechanism_path = root / "mechanism.csv"
            with mechanism_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["gene", "module_id", "curation_status", "source_ids_or_urls"])
                writer.writeheader()
                writer.writerow({"gene": "GENE", "module_id": "T1.1", "curation_status": "needs_bioinformatician_curation", "source_ids_or_urls": ""})
            summary = grouping.process(input_path, root / "out", canonical_path, mechanism_path)
            payload = json.loads((root / "out" / "gene_module_group_payloads_v3.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["payload_schema_version"], "llm1_group_payload_v3")
            self.assertTrue(payload["dry_run_only"])
            self.assertEqual(summary["metadata"]["llm_calls"], 0)
            self.assertEqual(summary["gates"]["llm1PilotReady"], "blocked")
            self.assertEqual(payload["canonical_status"][0]["hom_ref"], "unknown")
            self.assertFalse(payload["curated_mechanisms"]["usable_by_llm"])
            payload_v4 = json.loads((root / "out" / "gene_module_group_payloads_v4.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload_v4["payload_schema_version"], "llm1_group_payload_v4")
            self.assertFalse(payload_v4["gates"]["llm1_pilot_ready"])
            self.assertEqual(
                payload_v4["deterministic_summary"]["group_size_total"],
                len(payload_v4["focus_variants"])
                + payload_v4["context_variants"]["count"]
                + payload_v4["unresolved_and_failed"]["count"],
            )
            self.assertTrue((root / "out" / "mechanism_registry_v1.csv").is_file())
            self.assertTrue((root / "out" / "llm1_pilot_manifest_v1.csv").is_file())

    def test_v4_focus_has_absolute_twenty_and_six_gwas_only_caps(self):
        rows = []
        for index in range(30):
            row = base_row(f"v{index}", str(100 + index), "protein_coding_exon_non_cds_overlap")
            row.update(
                {
                    "attention_score": 100,
                    "attention_reasons": ["fixture"],
                    "focus_eligible": True,
                    "downstream_role": "focus_candidate",
                    "curated_gwas_high_confidence_cluster_count": "1",
                }
            )
            rows.append(row)

        focus = grouping.v4_focus_rows(rows)

        self.assertEqual(len(focus), 6)
        self.assertLessEqual(len(focus), 20)


class ReadinessAuditTests(unittest.TestCase):
    def test_sparse_canon_absence_is_not_hom_ref(self):
        canon = [{
            "gene_symbol_normalized": "GENE",
            "module_id": "T1.1",
            "canon_row_id": "r1",
            "row_status": "active_gene_row",
        }]
        status = readiness.build_canonical_status(canon, [], "run")
        self.assertEqual(status[0]["canonical_status"], "not_observed")
        self.assertEqual(status[0]["callable"], "unknown")
        self.assertEqual(status[0]["hom_ref"], "unknown")


if __name__ == "__main__":
    unittest.main()
