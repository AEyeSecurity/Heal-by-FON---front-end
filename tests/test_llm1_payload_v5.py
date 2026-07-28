import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


v5 = load_module("heal_llm1_payload_v5_test", ROOT / "services" / "heal-llm1-payload-v5" / "build_llm1_payload_v5.py")
digest = load_module("heal_evidence_digest_test", ROOT / "services" / "heal-evidence-digest" / "digest_public_evidence.py")
interpreter = load_module(
    "heal_grouped_interpreter_v5_test",
    ROOT / "services" / "heal-grouped-individual-interpretation" / "interpret_gene_module_groups.py",
)


def variant_row(gene="GENE", key="v1", module="T1.1", position="100"):
    return {
        "variant_key": key,
        "approved_symbol": gene,
        "module_id": module,
        "module_name": "Example module",
        "system_within_module": "Example system",
        "tier": "Tier 1",
        "module_status": "Approved",
        "evidence_tier": "High",
        "assembly": "GRCh38",
        "chrom_vcf": "chr1",
        "pos_vcf": position,
        "ref_vcf": "A",
        "alt_vcf": "G",
        "gt_alleles": "0/1",
        "zygosity": "heterozygous",
        "resolved_rsid": f"rs{position}",
        "identity_match_class": "exact_coordinate_allele",
        "local_region_class": "mane_cds_overlap",
        "transcript_concordance": "concordant_or_contextual",
        "vep_status": "success",
        "vep_most_severe_consequence": "missense_variant",
        "vep_hgvsp": "p.Ala1Gly",
        "source_status_clinvar": "success",
        "source_status_ensembl_variation": "success",
        "source_status_myvariant": "not_found",
        "source_status_gwas": "not_found",
        "source_status_pharmgkb": "not_found",
        "focus_eligible": "true",
        "downstream_role": "focus_candidate",
        "attention_rank": "1",
        "attention_score": "120",
    }


def mechanism():
    return {
        "gene": "GENE",
        "module_id": "T1.1",
        "curation_status": "approved",
        "source_ids_or_urls": "PMID:1",
        "biological_function": "Curated function",
    }


class PayloadV5Tests(unittest.TestCase):
    def test_conflicting_clinvar_assertions_remain_separate_and_reconciled(self):
        assertions = [
            {
                "variant_key": "v1", "normalized_classification": "pathogenic_or_likely_pathogenic",
                "conditions": "Condition A", "review_status": "criteria provided", "scv_accession": "SCV1",
                "vcv_accession": "VCV1", "submitter": "Lab A", "identity_match": "true",
                "contributes_to_aggregate": "true", "description": "Reported pathogenic assertion.",
            },
            {
                "variant_key": "v1", "normalized_classification": "benign_or_likely_benign",
                "conditions": "Condition A", "review_status": "criteria provided", "scv_accession": "SCV2",
                "vcv_accession": "VCV1", "submitter": "Lab B", "identity_match": "true",
                "contributes_to_aggregate": "true", "description": "Reported benign assertion.",
            },
        ]
        payload, coverage, _ = v5.build_group_payload(
            [variant_row()], [], v5.mechanism_for_group([mechanism()], ("GENE", "T1.1")), assertions, [], [], "",
        )
        classes = {row["classification"] for row in payload["clinical_evidence_summary"]["assertion_groups"]}
        self.assertEqual(classes, {"pathogenic_or_likely_pathogenic", "benign_or_likely_benign"})
        self.assertEqual(len([row for row in coverage if row["record_id"].startswith("SCV")]), 2)
        self.assertTrue(payload["evidence_coverage"]["reconciled"])
        self.assertEqual(sum(payload["evidence_coverage"]["status_counts"].values()), len(coverage))

    def test_unknown_digest_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown evidence refs"):
            digest.validate_digest(
                {"items": [{"evidence_refs": ["PMID:invented"]}]},
                {"PMID:1"},
            )

    def test_v5_pilot_requires_explicit_mode_and_gates(self):
        payload, _, _ = v5.build_group_payload(
            [variant_row()], [], v5.mechanism_for_group([mechanism()], ("GENE", "T1.1")), [], [], [], "",
        )
        interpreter.validate_v5_payload(payload, dry_run=True)
        with self.assertRaisesRegex(ValueError, "execution_mode=pilot"):
            interpreter.validate_v5_payload(payload, dry_run=False)
        payload["execution_mode"] = "pilot"
        payload["gates"]["llm1_pilot_ready"] = True
        interpreter.validate_v5_payload(payload, dry_run=False)

    def test_v5_interpreter_dry_run_validates_references_without_model_call(self):
        payload, _, _ = v5.build_group_payload(
            [variant_row()], [], v5.mechanism_for_group([mechanism()], ("GENE", "T1.1")), [], [], [], "",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "payloads.jsonl"
            input_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            summary = interpreter.process({
                "inputPath": str(input_path), "outputDir": str(root / "out"), "dryRun": True, "maxGroups": 1,
            })
            self.assertEqual(summary["metadata"]["interpreted_groups"], 1)
            self.assertEqual(summary["metadata"]["error_groups"], 0)
            self.assertTrue(summary["metadata"]["dry_run"])

    def test_one_group_failure_does_not_stop_other_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            detail = root / "detail.csv"
            rows = [variant_row("BAD", "bad", "T1.1", "100"), variant_row("GENE", "ok", "T1.1", "101")]
            with detail.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            original = v5.build_group_payload

            def isolated_failure(group_rows, *args, **kwargs):
                if group_rows[0]["approved_symbol"] == "BAD":
                    raise ValueError("fixture group failure")
                return original(group_rows, *args, **kwargs)

            with mock.patch.object(v5, "build_group_payload", side_effect=isolated_failure):
                summary = v5.process({"detailPath": str(detail), "outputDir": str(root / "out")})
            self.assertEqual(summary["metadata"]["total_groups"], 1)
            with (root / "out" / "group_compression_errors.csv").open(encoding="utf-8-sig") as handle:
                errors = list(csv.DictReader(handle))
            self.assertEqual(errors[0]["group_id"], "BAD:T1.1")
            payloads = (root / "out" / "llm1_group_payloads_v5.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(payloads), 1)


if __name__ == "__main__":
    unittest.main()
