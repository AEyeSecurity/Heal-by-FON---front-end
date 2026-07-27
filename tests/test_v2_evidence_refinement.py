"""Contract tests for multisource evidence refinement."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "services" / "heal-evidence-refinement" / "refine_multisource_v2.py"


def load_module():
    spec = importlib.util.spec_from_file_location("heal_evidence_refinement_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refinement = load_module()


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_gzip_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["variant_key"]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class V2EvidenceRefinementTests(unittest.TestCase):
    def test_open_provider_circuit_still_serves_valid_cache_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = refinement.RefinementCache(Path(temporary) / "cache.sqlite")
            url = "https://example.test/public-record"
            cache.put("gwas", "fixture", url, '{"data": 1}', "success", "fixture", 200)
            client = refinement.PublicHttpClient(cache)
            client.open_circuits.add("gwas")

            result = client.request("gwas", "fixture", url)

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["cache_hit"])
            cache.close()

    def test_clinpgx_empty_responses_are_not_reported_as_success(self):
        class EmptyClient:
            @staticmethod
            def get_json(_source, _query_mode, _url):
                return {"data": []}, {"status": "success", "status_reason": "provider_response"}

        bundle, status = refinement.fetch_clinpgx_bundle("rs1", EmptyClient())

        self.assertEqual(status["status"], "not_found")
        self.assertEqual(status["record_count"], 0)
        self.assertEqual(sum(len(refinement.extract_data_rows(value)) for value in bundle.values()), 0)

    def test_clinvar_parser_keeps_submitter_assertion_and_validates_allele(self):
        root = refinement.ET.fromstring(
            """
            <ClinVarResult-Set>
              <VariationArchive VariationID="1" Accession="VCV000000001" Version="2" NumberOfSubmissions="1" NumberOfSubmitters="1">
                <InterpretedRecord>
                  <SimpleAllele>
                    <Location><SequenceLocation Assembly="GRCh38" Chr="1" positionVCF="100" referenceAlleleVCF="A" alternateAlleleVCF="G"/></Location>
                  </SimpleAllele>
                  <Classifications><GermlineClassification><ReviewStatus>criteria provided, single submitter</ReviewStatus><Description>Benign</Description></GermlineClassification></Classifications>
                  <ClinicalAssertionList>
                    <ClinicalAssertion SubmissionDate="2025-01-01" ContributesToAggregateClassification="true">
                      <ClinVarAccession Accession="SCV000000001" Version="3" SubmitterName="Example Lab"/>
                      <Classification DateLastEvaluated="2024-12-01"><ReviewStatus>criteria provided</ReviewStatus><GermlineClassification>Benign</GermlineClassification></Classification>
                      <Assertion>variation to disease</Assertion>
                      <ObservedInList><ObservedIn><ObservedData><Attribute Type="Description">Observed evidence.</Attribute><Citation><ID Source="PubMed">123</ID></Citation></ObservedData></ObservedIn></ObservedInList>
                    </ClinicalAssertion>
                  </ClinicalAssertionList>
                </InterpretedRecord>
              </VariationArchive>
            </ClinVarResult-Set>
            """
        )
        parsed = refinement.parse_clinvar_vcv(
            root,
            {"variant_key": "v1", "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "100", "ref_vcf": "A", "alt_vcf": "G"},
        )

        self.assertTrue(parsed["identity_match"])
        self.assertEqual(parsed["normalized_classification"], "benign_or_likely_benign")
        self.assertEqual(parsed["assertions"][0]["scv_accession"], "SCV000000001")
        self.assertEqual(parsed["assertions"][0]["submitter"], "Example Lab")
        self.assertEqual(parsed["assertions"][0]["pmids"], "123")

    def test_clinpgx_uses_observed_genotype_and_does_not_drop_mismatch(self):
        bundle = {
            "clinical": {
                "data": [{
                    "id": "CA1",
                    "name": "Example",
                    "levelOfEvidence": {"term": "2A"},
                    "allelePhenotypes": [
                        {"allele": "G", "phenotype": "matching"},
                        {"allele": "T", "phenotype": "context only"},
                    ],
                    "literature": {"crossReferences": [{"resource": "PubMed", "resourceId": "123"}]},
                }]
            },
            "variant_annotation": {"data": []},
        }
        clinical, _ = refinement.parse_clinpgx_bundle(
            bundle,
            {"variant_key": "v1", "resolved_rsid": "rs1", "ref_vcf": "A", "alt_vcf": "G", "gt_alleles": "A/G"},
        )

        self.assertEqual(len(clinical), 2)
        self.assertEqual(clinical[0]["allele_match_status"], "compatible_observed_genotype")
        self.assertEqual(clinical[0]["publication_followup_eligible"], "true")
        self.assertEqual(clinical[1]["allele_match_status"], "incompatible_observed_genotype")
        self.assertEqual(clinical[1]["publication_followup_eligible"], "false")

    def test_gwas_focus_requires_significance_and_observed_alt(self):
        base = {
            "association_id": 1,
            "accession_id": "GCST1",
            "p_value": 1e-10,
            "snp_allele": [{"rs_id": "rs1", "effect_allele": "G"}],
        }
        row = {"variant_key": "v1", "resolved_rsid": "rs1", "ref_vcf": "A", "alt_vcf": "G"}

        matched = refinement.parse_gwas_association(base, row)
        mismatched = refinement.parse_gwas_association(
            {**base, "association_id": 2, "snp_allele": [{"rs_id": "rs1", "effect_allele": "T"}]},
            row,
        )

        self.assertEqual(matched["focus_eligible"], "true")
        self.assertEqual(mismatched["focus_eligible"], "false")
        self.assertEqual(mismatched["evidence_scope"], "population_association_not_individual_causality")

    def test_process_preserves_all_contracts_and_deduplicates_publications(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            physical_path = root / "physical.csv"
            match_path = root / "match.csv"
            triage_path = root / "triage.csv"
            triage_excluded_path = root / "triage_excluded.csv"
            canon_path = root / "canon.csv"
            normalized_path = root / "normalized.csv.gz"
            output_dir = root / "output"
            physical = [
                {
                    "variant_key": "v_benign", "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "100", "ref_vcf": "A", "alt_vcf": "G",
                    "resolved_rsid": "rs1", "identity_match_class": "exact_coordinate_allele", "vep_status": "success", "vep_most_severe_consequence": "missense_variant",
                    "source_status_clinvar": "success", "clinvar_normalized_classification": "benign_or_likely_benign", "source_status_pharmgkb": "not_found", "source_status_gwas": "not_found",
                },
                {
                    "variant_key": "v_pgx", "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "200", "ref_vcf": "A", "alt_vcf": "G",
                    "resolved_rsid": "rs2", "identity_match_class": "exact_coordinate_allele", "vep_status": "success", "vep_most_severe_consequence": "missense_variant",
                    "source_status_clinvar": "not_found", "source_status_pharmgkb": "success", "pharmgkb_clinical_annotation_count": "1", "source_status_gwas": "not_found",
                },
                {
                    "variant_key": "v_pathogenic", "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "300", "ref_vcf": "C", "alt_vcf": "T",
                    "resolved_rsid": "rs3", "identity_match_class": "exact_coordinate_allele", "vep_status": "success", "vep_most_severe_consequence": "missense_variant",
                    "source_status_clinvar": "success", "clinvar_normalized_classification": "pathogenic_or_likely_pathogenic", "source_status_pharmgkb": "not_found", "source_status_gwas": "not_found",
                },
                {
                    "variant_key": "v_unresolved", "assembly": "GRCh38", "chrom_vcf": "chr1", "pos_vcf": "400", "ref_vcf": "G", "alt_vcf": "A",
                    "resolved_rsid": "", "identity_match_class": "no_identity_match", "vep_status": "success", "vep_most_severe_consequence": "5_prime_UTR_variant",
                    "source_status_clinvar": "not_queried", "source_status_pharmgkb": "not_queried", "source_status_gwas": "not_queried",
                },
            ]
            write_csv(physical_path, physical)
            match = []
            for index, variant in enumerate(physical + [{"variant_key": "v_background", "chrom_vcf": "chr1", "pos_vcf": "500", "ref_vcf": "T", "alt_vcf": "C"}], start=1):
                match.append({
                    **variant,
                    "variant_gene_module_id": f"vgm{index}",
                    "approved_symbol": f"GENE{index}",
                    "module_id": f"T1.{index}",
                    "module_name": f"Module {index}",
                    "gt_alleles": "A/G" if variant["variant_key"] == "v_pgx" else "",
                    "local_region_class": "mane_cds_overlap",
                })
            write_csv(match_path, match)
            write_csv(triage_path, match[:4])
            write_csv(
                triage_excluded_path,
                [{**match[4], "triage_decision": "exclude_background", "triage_reason": "Background-only local region"}],
            )
            write_csv(
                canon_path,
                [{"canon_row_id": f"c{index}", "module_id": row["module_id"], "gene_symbol_normalized": row["approved_symbol"], "row_status": "active_gene_row"} for index, row in enumerate(match, start=1)],
            )
            write_gzip_csv(
                normalized_path,
                [{"variant_key": row["variant_key"]} for row in match]
                + [{"variant_key": "v_outside_canon", "chrom_vcf": "chr2", "pos_vcf": "600", "ref_vcf": "C", "alt_vcf": "A"}],
            )

            def fake_clinvar(row, _client):
                classification = "Benign" if row["variant_key"] == "v_benign" else "Pathogenic"
                normalized = refinement.normalized_clinvar_class(classification)
                return [{
                    "vcv_accession": "VCV1",
                    "aggregate_classification": classification,
                    "normalized_classification": normalized,
                    "review_status": "criteria provided",
                    "conditions": "Example condition",
                    "identity_match": True,
                    "pmids": ["123"] if normalized != "benign_or_likely_benign" else [],
                    "assertions": [{
                        "variant_key": row["variant_key"], "submitter": "Lab", "normalized_classification": normalized,
                        "pmids": "123" if normalized != "benign_or_likely_benign" else "", "identity_match": "true",
                    }],
                }], {"status": "success", "status_reason": "fixture"}

            def fake_clinpgx(_rsid, _client):
                return {
                    "clinical": {"data": [{
                        "id": "CA1", "levelOfEvidence": {"term": "2A"},
                        "allelePhenotypes": [{"allele": "G", "phenotype": "fixture"}],
                        "literature": {"crossReferences": [{"resource": "PubMed", "resourceId": "123"}]},
                    }]},
                    "variant_annotation": {"data": []},
                    "variant": {"data": []},
                }, {"status": "success", "status_reason": "fixture"}

            publication_calls: list[str] = []

            def fake_publication(pmid, _client, _directory):
                publication_calls.append(pmid)
                return {"pmid": pmid, "title": "Fixture", "pmc_open_access": "false"}, {"status": "success", "status_reason": "fixture"}

            with patch.object(refinement, "fetch_clinvar_records", side_effect=fake_clinvar), \
                patch.object(refinement, "fetch_clinpgx_bundle", side_effect=fake_clinpgx), \
                patch.object(refinement, "fetch_publication", side_effect=fake_publication):
                summary = refinement.process({
                    "physicalMatrixPath": str(physical_path),
                    "matchPath": str(match_path),
                    "triagePath": str(triage_path),
                    "triageExcludedPath": str(triage_excluded_path),
                    "canonCleanPath": str(canon_path),
                    "normalizedVariantsPath": str(normalized_path),
                    "outputDir": str(output_dir),
                    "cachePath": str(root / "cache.sqlite"),
                    "analysisMode": "quick",
                    "assembly": "GRCh38",
                })

            self.assertEqual(summary["counts"]["enrichedPhysicalVariants"], 4)
            self.assertEqual(summary["counts"]["matchedPhysicalRegistryRows"], 6)
            self.assertEqual(summary["counts"]["normalizedPhysicalRows"], 6)
            self.assertEqual(summary["counts"]["variantGeneModuleRows"], 5)
            self.assertEqual(summary["counts"]["benignContextVariants"], 1)
            self.assertEqual(summary["gates"]["conservationGate"]["status"], "pass")
            self.assertEqual(publication_calls, ["123"])
            self.assertEqual(len(refinement.read_csv(output_dir / "v2_curated_physical_variant_matrix.csv")), 4)
            registry = refinement.read_csv(output_dir / "v2_curated_physical_variant_registry.csv")
            self.assertEqual(len(registry), 6)
            self.assertEqual(next(row for row in registry if row["variant_key"] == "v_background")["downstream_role"], "background_observed")
            self.assertEqual(next(row for row in registry if row["variant_key"] == "v_outside_canon")["downstream_role"], "background_observed")
            projection = refinement.read_csv(output_dir / "v2_curated_gene_module_projection.csv")
            background_projection = next(row for row in projection if row["variant_key"] == "v_background")
            self.assertEqual(background_projection["triage_decision"], "exclude_background")
            self.assertEqual(background_projection["triage_reason"], "Background-only local region")


if __name__ == "__main__":
    unittest.main()
