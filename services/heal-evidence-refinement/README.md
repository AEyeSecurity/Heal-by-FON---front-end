# HEAL Evidence Refinement v2

Second-stage curation for `gene_module_v2`. It runs after physical enrichment and before grouping.

## Contract

- `v2_curated_physical_variant_registry.csv` retains every normalized physical variant, including variants outside the canon match.
- `v2_curated_physical_variant_matrix.csv` contains one evidence row per enriched physical `variant_key`.
- `v2_curated_gene_module_projection.csv` retains every match row and references physical evidence without duplicating public payloads.
- `v2_canonical_gene_module_status.csv` retains every canon row. Sparse VCF absence is `not_observed`, never inferred `hom_ref`.
- Deep curation is selective, but conservation is total.

Independent axes prevent semantic collapse:

- `identity_status`
- `functional_annotation_status`
- `source_evidence_status`
- `curation_depth`
- `downstream_role`

`not_found`, `source_error`, `not_queried`, and benign classifications are never interchangeable.

## Source Policy

- ClinVar: VCV/SCV retrieval and exact GRCh allele validation. All assertions are retained; publication follow-up is limited to non-benign aggregate/assertion classes.
- ClinPGx: all clinical and variant annotations are retained without truncation. Allele/genotype compatibility and evidence level control publication follow-up. Artifacts include ClinPGx/PharmGKB attribution and CC BY-SA 4.0.
- GWAS Catalog: REST API v2 with complete pagination. Associations remain population evidence; focus requires genome-wide significance and exact observed ALT as effect allele.
- PubMed/PMC: PMIDs are deduplicated across sources. Abstracts come from PubMed; full text is stored only when PMC reports an open-access record.
- Ensembl, MyVariant, and VEP remain base-enrichment support sources and are not recursively expanded here.

## Modes

- `quick`: refines identities and source records already confirmed by base enrichment.
- `complete` / `qa`: also retries base source errors. Expensive coordinate/HGVS identity rescue remains owned by the base enrichment stage.

The stage always leaves `llm1PilotReady=false`. Professional review and explicit LLM enablement are separate gates.

## Invocation

```powershell
python refine_multisource_v2.py --input-json-base64 <payload>
```

Required production payload paths: normalized variants, physical matrix, full match, included and excluded triage audit, clean canon, and output directory. Historical/manual invocations may omit normalized or excluded-triage inputs, but the resulting fallback cannot claim the complete extraction contract.
