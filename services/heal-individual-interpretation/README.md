# HEAL Individual Variant Interpretation

This service implements LLM1: one observed variant row in, one structured interpretation out.

Input:

- `heal_fon_interpretation_enrichment_plus.csv`

Deterministic preparation:

- filters the Enrichment Plus CSV to the fields needed by the LLM;
- renames `Confidence Level` to `preliminary_confidence_from_input`;
- excludes raw JSON fields by default;
- writes auditable payload files before calling the model.
- applies deterministic HEAL calibration after the LLM response, so `final_confidence_level`,
  `evidence_conflict_flag`, `requires_professional_review`, and `interpretation_scope` reflect
  deliverable value instead of raw technical match quality alone.

LLM execution:

- default model: `gpt-5-mini`;
- override with `HEAL_LLM1_MODEL`;
- default concurrency: `HEAL_LLM_MAX_WORKERS=3`;
- default row timeout: `HEAL_LLM_TIMEOUT_SECONDS=90`;
- default row attempts: `HEAL_LLM_ROW_ATTEMPTS=2`;
- API key is read from `HEAL_OPENAI_API_KEY` or `OPENAI_API_KEY`;
- output is constrained by `individual_variant_interpretation_schema.json`;
- one failed row does not stop the entire batch.
- rows are interpreted in controlled parallel batches and outputs are written incrementally.

Outputs:

- `variant_interpretation_payloads.jsonl`
- `variant_interpretation_payloads.csv`
- `individual_variant_interpretation_progress.json`
- `individual_variant_interpretations.jsonl`
- `individual_variant_interpretations.csv`
- `individual_variant_interpretation_errors.csv`
- `individual_variant_interpretation_summary.json`

Operational notes:

- This module does not perform deterministic grouping.
- This module does not generate the global interpretation report.
- `Low` means observed but low interpretive value; it does not mean low VCF quality.
- A lone `clinvar_conflict_flag=true` is not enough to make the final row `Conflicting`.
- Calibration is evidence-based and does not use gene-specific or rsID-specific overrides.
- `Conflicting` requires explicit conflict in the provided evidence, such as conflicting ClinVar pathogenicity/classification, pathogenicity/population-frequency contradiction, or material allele/locus ambiguity.
- Intronic/intergenic/GWAS-only markers can be `Low` when they are observed but have low deliverable weight.
- Raw external-source JSON remains in Enrichment Plus for audit, but is not passed to LLM1 by default.
- `--dry-run` exists only for local smoke tests and does not produce real interpretations.

Example:

```powershell
python C:\ServerCIT\services\heal-individual-interpretation\interpret_observed_variants.py `
  --input C:\ServerCIT\services\heal-variant-enrichment\runs\<run-id>\heal_fon_interpretation_enrichment_plus.csv `
  --output-dir C:\ServerCIT\services\heal-individual-interpretation\runs\<run-id>
```
