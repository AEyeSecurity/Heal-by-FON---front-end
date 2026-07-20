"""Focused regression tests for the coordinate-first v2 enrichment boundary."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER_PATH = ROOT / "services" / "heal-vcf-normalization" / "normalize_vcf_for_v2.py"
MATCHER_PATH = ROOT / "services" / "heal-vcf-canon-match" / "match_vcf_to_gene_module_ready.py"
CANON_INTAKE_PATH = ROOT / "services" / "heal-canon-intake" / "process_heal_canon.py"
ENRICHMENT_DIR = ROOT / "services" / "heal-variant-enrichment"
ENRICHMENT_PATH = ENRICHMENT_DIR / "enrich_gene_module_v2.py"
sys.path.insert(0, str(ENRICHMENT_DIR))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalizer = load_module("heal_vcf_normalizer_test", NORMALIZER_PATH)
matcher = load_module("heal_vcf_matcher_test", MATCHER_PATH)
canon_intake = load_module("heal_canon_intake_test", CANON_INTAKE_PATH)
enrichment = load_module("heal_v2_enrichment_test", ENRICHMENT_PATH)


class V2EnrichmentRemediationTests(unittest.TestCase):
    def test_detects_grch38_and_splits_observed_multiallelic_genotype(self):
        with tempfile.TemporaryDirectory() as temporary:
            vcf_path = Path(temporary) / "sample.vcf"
            vcf_path.write_text(
                "##fileformat=VCFv4.2\n"
                "##reference=GRCh38\n"
                "##contig=<ID=chr1,length=248956422>\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
                "chr1\t100\trs1\tA\tG,T\t.\tLowDepth\t.\tGT\t1/2\n",
                encoding="utf-8",
            )
            probe = normalizer.detect_assembly_from_header(vcf_path)
            sources, excluded, stats = normalizer.source_alleles(vcf_path)

        self.assertEqual(probe["assembly"], "GRCh38")
        self.assertEqual(len(excluded), 0)
        self.assertEqual(stats["multiallelic_records"], 1)
        self.assertEqual(stats["observed_source_alleles"], 2)
        rows = list(sources.values())
        self.assertEqual({row["source_alt_allele"] for row in rows}, {"G", "T"})
        self.assertEqual({row["source_allele_dosage"] for row in rows}, {"1"})
        self.assertEqual({row["source_zygosity"] for row in rows}, {"heterozygous"})

    def test_nonstandard_hla_contig_is_audited_before_reference_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            vcf_path = Path(temporary) / "hla.vcf"
            vcf_path.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
                "HLA-DRB1*03:01:01:01\t7566\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            sources, excluded, stats = normalizer.source_alleles(vcf_path)

        self.assertEqual(sources, {})
        self.assertEqual(stats["unsupported_contig"], 1)
        self.assertEqual(excluded[0]["exclusion_reason"], "unsupported_contig")

    def test_envelope_prefilter_keeps_only_possible_canon_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path = root / "envelopes.json"
            vcf_path = root / "sample.vcf"
            index_path.write_text(
                json.dumps({"chromosomes": {"chr1": [{"start": 1000, "end": 1100}]}}),
                encoding="utf-8",
            )
            vcf_path.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
                "chr1\t1050\trs-in\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
                "chr1\t5000\trs-out\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            regions = normalizer.load_target_regions(index_path, flank_bases=0)
            sources, excluded, stats = normalizer.source_alleles(vcf_path, regions)

        self.assertEqual(len(sources), 1)
        self.assertEqual(next(iter(sources.values()))["source_id_vcf"], "rs-in")
        self.assertEqual(excluded, [])
        self.assertEqual(stats["outside_canon_envelope_prefilter"], 1)

    def test_singleton_envelope_object_is_accepted_by_normalizer_and_matcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path = root / "envelopes.json"
            vcf_path = root / "sample.vcf"
            index = {
                "assembly": "GRCh38",
                "chromosomes": {
                    "chr14": {"gene_id": "GENE_MTHFD1_GRCH38", "symbol": "MTHFD1", "start": 1000, "end": 1100}
                },
            }
            index_path.write_text(json.dumps(index), encoding="utf-8")
            vcf_path.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
                "chr14\t1050\trs-in\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )

            regions = normalizer.load_target_regions(index_path, flank_bases=0)
            candidates, _, warnings = matcher.scan_vcf(vcf_path, index)

        self.assertEqual(regions, {"chr14": [(1000, 1100)]})
        self.assertEqual(warnings, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["approved_symbol"], "MTHFD1")

    def test_singleton_merged_feature_object_is_accepted_by_local_classifier(self):
        local = matcher.classify_local_region(
            {
                "transcript_body_union": {"start": 1000, "end": 1100, "strand": 1},
                "mane_cds_union": [{"start": 1040, "end": 1060}],
            },
            1050,
            1050,
        )

        self.assertEqual(local["local_region_class"], "mane_cds_overlap")
        self.assertEqual(local["overlap_feature_types"], "mane_cds_union")

    def test_canon_export_preserves_singleton_chromosome_as_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            index_path = Path(temporary) / "envelopes.json"
            canon_intake.export_gene_envelope_index(
                "test-canon",
                "GRCh38",
                [{
                    "gene_id": "GENE_MTHFD1_GRCH38",
                    "approved_symbol": "MTHFD1",
                    "start": 1000,
                    "end": 1100,
                    "strand": 1,
                    "module_ids": "T1.1",
                    "module_names": "Foundational Systems Resilience",
                    "biotype": "protein_coding",
                    "chrom": "chr14",
                }],
                index_path,
            )
            payload = json.loads(index_path.read_text(encoding="utf-8"))

        self.assertIsInstance(payload["chromosomes"]["chr14"], list)
        self.assertEqual(payload["chromosomes"]["chr14"][0]["symbol"], "MTHFD1")

    def test_target_files_normalize_raw_contig_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            regions_path, rename_path, count = normalizer.write_target_files(
                Path(temporary),
                {"chr1": [(100, 200)]},
                {"chr1": "1"},
            )
            regions = regions_path.read_text(encoding="utf-8")
            renames = rename_path.read_text(encoding="utf-8")

        self.assertEqual(count, 1)
        self.assertIn("1\t100\t200", regions)
        self.assertIn("1\tchr1", renames)

    def test_normalized_reader_rejects_rows_outside_target_and_writes_atomic_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            normalized_path = root / "normalized.vcf"
            normalized_path.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
                "chr1\t1050\t.\tA\tG\t.\tPASS\tORIG=chr1|1050|A|G|1\tGT\t0/1\n"
                "chr1\t5000\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n",
                encoding="utf-8",
            )
            source = {
                ("chr1", "1050", "A", "G", "1"): {
                    "source_chrom_vcf": "chr1",
                    "source_pos_vcf": "1050",
                    "source_id_vcf": ".",
                    "source_ref_vcf": "A",
                    "source_alt_vcf": "G",
                    "source_alt_allele": "G",
                    "source_alt_index": "1",
                    "source_gt_raw": "0/1",
                    "source_allele_dosage": "1",
                    "source_zygosity": "heterozygous",
                    "source_filter_vcf": "PASS",
                    "source_qual_vcf": ".",
                    "source_info_vcf": ".",
                }
            }
            excluded = []
            stats = normalizer.Counter()
            rows = normalizer.normalized_alleles(
                normalized_path,
                "GRCh38",
                source,
                excluded,
                stats,
                {"chr1": [(1000, 1100)]},
                root,
            )
            progress = json.loads((root / "normalization_progress.json").read_text(encoding="utf-8"))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_membership_basis"], "source_and_current")
        self.assertEqual(stats["normalized_source_matched"], 1)
        self.assertEqual(stats["normalized_outside_target"], 1)
        self.assertEqual(excluded[-1]["exclusion_reason"], "normalized_outside_target_regions")
        self.assertEqual(progress["substage"], "reading_normalized_vcf")

    def test_resolves_only_exact_vep_colocated_allele_rsid(self):
        variant = {"id_vcf": "rs999", "ref_vcf": "A", "alt_vcf": "G"}
        item = {
            "colocated_variants": [
                {"id": "rs999", "allele_string": "A/T"},
                {"id": "rs1139424", "allele_string": "A/G"},
            ]
        }
        rsid, reason = enrichment.exact_rsid(variant, item)
        self.assertEqual(rsid, "rs1139424")
        self.assertEqual(reason, "vep_colocated_exact_allele")

    def test_resolves_left_aligned_indel_from_normalized_allele_signature(self):
        variant = {
            "chrom_vcf": "chr1",
            "pos_vcf": "100",
            "ref_vcf": "CA",
            "alt_vcf": "CTA",
        }
        item = {
            "colocated_variants": [{
                "id": "rs1234",
                "seq_region_name": "1",
                "start": 101,
                "end": 101,
                "allele_string": "A/TA",
            }]
        }

        resolved = enrichment.resolve_rsid(variant, item)

        self.assertEqual(resolved["resolved_rsid"], "rs1234")
        self.assertEqual(resolved["status"], "vep_colocated_normalized_indel")
        self.assertEqual(resolved["reason"], "unique_vep_colocated_allele_match")

    def test_classifies_colocated_rsid_with_nonmatching_allele_as_audit_only(self):
        variant = {"chrom_vcf": "chr1", "pos_vcf": "100", "ref_vcf": "A", "alt_vcf": "G"}
        item = {
            "colocated_variants": [{
                "id": "rs123",
                "seq_region_name": "1",
                "start": 100,
                "end": 100,
                "allele_string": "A/T",
            }]
        }

        resolved = enrichment.resolve_rsid(variant, item)

        self.assertEqual(resolved["resolved_rsid"], "")
        self.assertEqual(resolved["status"], "vep_colocated_allele_mismatch")
        self.assertEqual(resolved["reason"], "colocated_rsid_requires_coordinate_allele_reconciliation")

    def test_secondary_sources_report_per_source_metrics_and_statuses(self):
        variant = {"variant_key": "GRCh38:chr1:100:A:G", "resolved_rsid": "rs1"}
        with tempfile.TemporaryDirectory() as temporary:
            cache = enrichment.EnrichmentCache(Path(temporary) / "cache.sqlite", ttl_days=1)
            with patch.object(enrichment.legacy, "fetch_ensembl_variation", return_value=({}, "")), \
                patch.object(enrichment.legacy, "fetch_clinvar", return_value=({"count": "0"}, "")), \
                patch.object(enrichment.legacy, "fetch_myvariant", return_value=({}, "")), \
                patch.object(enrichment.legacy, "fetch_gwas_catalog", return_value=({"association_count": "0"}, "")), \
                patch.object(enrichment.legacy, "fetch_clinpgx", return_value=({"clinical_annotation_count": "0"}, "")):
                _, metrics = enrichment.fetch_secondary_sources([variant], "GRCh38", cache, 1)
            cache.close()

        self.assertEqual(metrics["calls_total"], 5)
        self.assertEqual(metrics["calls_completed"], 5)
        self.assertEqual(metrics["source_stats"]["clinvar"]["not_found"], 1)
        self.assertEqual(metrics["source_stats"]["gwas"]["not_found"], 1)
        self.assertGreaterEqual(metrics["source_stats"]["pharmgkb"]["completed"], 1)

    def test_target_gene_parser_does_not_select_another_gene_transcript(self):
        item = {
            "most_severe_consequence": "missense_variant",
            "transcript_consequences": [
                {"gene_symbol": "OTHER", "canonical": 1, "hgvsp": "ENSP:p.Other"},
                {
                    "gene_symbol": "SDHA",
                    "mane_select": "ENST00000264932.10",
                    "transcript_id": "ENST00000264932",
                    "hgvsp": "ENSP00000264932:p.Arg31Gly",
                    "consequence_terms": ["missense_variant"],
                    "cadd_phred": 22.1,
                },
            ],
        }
        parsed = enrichment.parse_vep_for_gene(item, "SDHA")
        self.assertEqual(parsed["status"], "direct_target_transcript")
        self.assertEqual(parsed["picked_gene_symbol"], "SDHA")
        self.assertEqual(parsed["picked_hgvsp"], "ENSP00000264932:p.Arg31Gly")

    def test_sqlite_cache_key_excludes_patient_genotype(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "enrichment_cache.sqlite"
            cache = enrichment.EnrichmentCache(cache_path, ttl_days=1)
            cache.put("GRCh38", "v2_test", "vep", "fingerprint", {"public": "annotation"}, "success", 200)
            cached = cache.get("GRCh38", "v2_test", "vep", "fingerprint")
            text = cache_path.read_bytes().decode("latin1", errors="ignore")
            cache.close()

        self.assertEqual(cached["payload"], {"public": "annotation"})
        self.assertNotIn("0/1", text)


if __name__ == "__main__":
    unittest.main()
