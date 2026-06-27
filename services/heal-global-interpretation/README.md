# HEAL Global Interpretation Service

This service implements LLM2: one normalized observed-variant interpretation table in, one structured global synthesis out.

It consumes:

- `individual_variant_interpretations_normalized.csv`

It produces:

- `global_interpretation_payload.json`
- `deterministic_summary.json`
- `global_interpretation.json`
- `global_interpretation_es_source.json` when an English report is produced from a Spanish canonical source
- `global_interpretation_sections.csv`
- `global_interpretation_summary.json`

LLM2 is synthesis-only. It must not reinterpret variants from scratch or change individual confidence labels.

## Axis Ontology

The service uses `axis_ontology.json` to map original canon categories into a
small set of canonical biological axes before the LLM receives the payload.
This keeps the report modular without allowing the model to invent arbitrary
major axes.

The deterministic summary includes:

- `axis_ontology.version`
- `axis_ontology.axes`
- `axis_ontology.category_axis_map`
- `genes_by_axis` grouped by canonical axis
- `category_groups` preserving the original canon category labels for audit

If a canon category does not match a configured pattern, it is assigned to
`uncategorized_context` so the mapping gap is visible instead of silently
merged into another axis.

## Audit Metadata

Each run records version and hash metadata in `audit_metadata` and in the
summary metadata:

- `pipeline_version`
- `axis_ontology_version`
- `axis_ontology_hash`
- `llm2_prompt_version`
- `translation_prompt_version`
- `global_interpretation_schema_version`
- input CSV SHA-256
- deterministic summary SHA-256
- LLM payload SHA-256
- prompt/schema SHA-256
- final global interpretation JSON SHA-256

For English output, the service first generates the canonical Spanish source
report, stores it as `global_interpretation_es_source.json`, and then translates
that same structured JSON to English. The Spanish source hash is recorded so the
English output can be audited against its source.

## Runtime

The API copies this service to:

`C:\ServerCIT\services\heal-global-interpretation`

The OpenAI key is read from `HEAL_OPENAI_API_KEY` or `OPENAI_API_KEY` in the HEAL API runtime environment.

## Model Selection

The backend selects the model dynamically:

- quick analysis: `gpt-5-mini`
- full analysis: `gpt-5.2`
- QA/debug: selected by the frontend from the allowed model list

## Manual Test

```powershell
python C:\ServerCIT\services\heal-global-interpretation\interpret_global_profile.py `
  --input-json-base64 <base64-json-payload>
```
