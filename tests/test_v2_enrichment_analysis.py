"""Regression tests for the offline v2 enrichment analysis."""

from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "services" / "heal-enrichment-analysis" / "analyze_v2_enrichment.py"
SPEC = importlib.util.spec_from_file_location("heal_v2_enrichment_analysis_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {MODULE_PATH}")
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def triage_row(key: str, pos: str, ref: str, alt: str) -> dict[str, str]:
    return {
        "variant_key": key,
        "approved_symbol": "GENE1",
        "module_id": "T1.1",
        "module_name": "Test module",
        "system_within_module": "Test system",
        "tier": "Tier 1",
        "module_status": "Approved",
        "evidence_tier": "High",
        "is_draft": "false",
        "assembly_name": "GRCh38",
        "chrom_vcf": "chr1",
        "pos_vcf": pos,
        "variant_start": pos,
        "variant_end": pos,
        "ref_vcf": ref,
        "alt_vcf": alt,
        "local_region_class": "mane_cds_overlap",
        "local_feature_priority": "high",
        "annotation_needed": "true",
        "background_only": "false",
        "triage_decision": "include_ai",
    }


class V2EnrichmentAnalysisTests(unittest.TestCase):
    def test_process_preserves_statuses_and_accepts_vep_anchored_indel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache_path = root / "cache.sqlite"
            connection = sqlite3.connect(cache_path)
            connection.execute(
                """CREATE TABLE enrichment_cache (
                    assembly TEXT, variant_key TEXT, source TEXT,
                    request_fingerprint TEXT, response_json TEXT, status TEXT,
                    http_status INTEGER, fetched_at TEXT, expires_at TEXT,
                    pipeline_version TEXT,
                    PRIMARY KEY (assembly, variant_key, source)
                )"""
            )
            vep_snv = {
                "seq_region_name": "1", "start": 100, "end": 100, "assembly_name": "GRCh38",
                "allele_string": "A/G", "input": "1 100 snv A G . . .",
                "most_severe_consequence": "missense_variant",
                "colocated_variants": [{"id": "rs1", "allele_string": "A/G"}],
                "transcript_consequences": [{"gene_symbol": "GENE1", "hgvsc": "ENST:c.1A>G", "hgvsp": "ENSP:p.Ala1Gly", "cadd_phred": 22}],
            }
            vep_indel = {
                "seq_region_name": "1", "start": 101, "end": 100, "assembly_name": "GRCh38",
                "allele_string": "-/TA", "input": "1 100 indel G GTA . . .",
                "most_severe_consequence": "intron_variant",
                "colocated_variants": [{"id": "rs2", "allele_string": "-/TA"}],
                "transcript_consequences": [{"gene_symbol": "GENE1", "hgvsc": "ENST:c.2+1insTA"}],
            }
            rows = [
                ("GRCh38", "snv", "ensembl_vep_region", vep_snv, "success"),
                ("GRCh38", "indel", "ensembl_vep_region", vep_indel, "success"),
                ("GRCh38", "snv", "clinvar", {"data": {"count": "1", "clinical_significance": "Pathogenic"}, "error": ""}, "success"),
                ("GRCh38", "snv", "gwas", {"data": {}, "error": "429"}, "source_error"),
            ]
            for index, (assembly, key, source, payload, status) in enumerate(rows):
                connection.execute(
                    "INSERT INTO enrichment_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (assembly, key, source, f"fp{index}", json.dumps(payload), status, 429 if status == "source_error" else 200, f"2026-07-20T00:00:0{index}Z", "", "test"),
                )
            connection.commit()
            connection.close()

            triage_path = root / "triage.csv"
            with triage_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(triage_row("snv", "100", "A", "G")))
                writer.writeheader()
                writer.writerow(triage_row("snv", "100", "A", "G"))
                writer.writerow(triage_row("indel", "100", "G", "GTA"))

            result = analysis.process({
                "cachePath": str(cache_path),
                "triagePath": str(triage_path),
                "outputDir": str(root / "analysis"),
            })
            summary = result["summary"]
            with Path(summary["outputs"]["variant_evidence_matrix"]).open(encoding="utf-8") as matrix_handle:
                matrix = list(csv.DictReader(matrix_handle))
            by_key = {row["variant_key"]: row for row in matrix}

        self.assertEqual(summary["physical_variants"], 2)
        self.assertEqual(by_key["snv"]["evidence_category"], "clinical_evidence")
        self.assertEqual(by_key["snv"]["source_status_gwas"], "source_error")
        self.assertEqual(by_key["indel"]["vep_identity_class"], "normalized_indel_match")
        self.assertEqual(by_key["indel"]["source_status_clinvar"], "not_queried")


if __name__ == "__main__":
    unittest.main()
