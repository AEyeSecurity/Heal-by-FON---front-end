# HEAL Individual Variant Interpretation

This service implements LLM1: one observed variant row in, one structured interpretation out.

Input:

- `heal_fon_interpretation_enrichment_plus.csv`

Deterministic preparation:

- filters the Enrichment Plus CSV to the fields needed by the LLM;
- renames `Confidence Level` to `preliminary_confidence_from_input`;
- excludes raw JSON fields by default;
- writes auditable payload files before calling the model.

LLM execution:

- default model: `gpt-5.5`;
- override with `HEAL_LLM1_MODEL`;
- API key is read from `HEAL_OPENAI_API_KEY` or `OPENAI_API_KEY`;
- output is constrained by `individual_variant_interpretation_schema.json`;
- one failed row does not stop the entire batch.

Outputs:

- `variant_interpretation_payloads.jsonl`
- `variant_interpretation_payloads.csv`
- `individual_variant_interpretations.jsonl`
- `individual_variant_interpretations.csv`
- `individual_variant_interpretation_errors.csv`
- `individual_variant_interpretation_summary.json`

Operational notes:

- This module does not perform deterministic grouping.
- This module does not generate the global interpretation report.
- Raw external-source JSON remains in Enrichment Plus for audit, but is not passed to LLM1 by default.
- `--dry-run` exists only for local smoke tests and does not produce real interpretations.

Example:

```powershell
python C:\ServerCIT\services\heal-individual-interpretation\interpret_observed_variants.py `
  --input C:\ServerCIT\services\heal-variant-enrichment\runs\<run-id>\heal_fon_interpretation_enrichment_plus.csv `
  --output-dir C:\ServerCIT\services\heal-individual-interpretation\runs\<run-id>
```
