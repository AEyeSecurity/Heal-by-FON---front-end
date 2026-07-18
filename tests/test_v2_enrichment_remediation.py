"""Focused regression tests for the coordinate-first v2 enrichment boundary."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER_PATH = ROOT / "services" / "heal-vcf-normalization" / "normalize_vcf_for_v2.py"
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

        self.assertEqual(cached["payload"], {"public": "annotation"})
        self.assertNotIn("0/1", text)


if __name__ == "__main__":
    unittest.main()
