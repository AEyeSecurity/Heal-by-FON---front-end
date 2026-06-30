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

## Canonical Analysis Frame

After building the deterministic summary, the service creates
`canonical_analysis_frame`. This is the contract LLM2 must follow:

- allowed biological axes
- source categories behind each axis
- supporting genes and rsIDs
- axis strength and suggested axis confidence
- evidence mix per axis
- top findings for review
- informational vs clinical readiness
- non-diagnostic constraints

The frame is written into `global_interpretation_payload.json` and into the
final `global_interpretation.json` for audit.

## Deterministic Validation

The LLM2 result is validated and repaired before it is saved:

- axes outside the canonical frame are dropped;
- duplicate axes are merged;
- axes are reordered to the canonical frame order;
- missing support genes/rsIDs are replaced from the deterministic frame;
- missing `contextual_review_guidance` fields are filled from deterministic
  defaults;
- metadata counts are restored from the deterministic summary.

The report stores a `deterministic_validation` block with warnings and the
frame hash used for validation.

## Structured Report

The service adds a deterministic `structured_report` block to
`global_interpretation.json`. This is the report-rendering source used by the
DOCX renderer. The structure is stable across languages:

1. overview
2. primary biological axes
3. notable gene patterns
4. findings for review
5. limitations
6. next steps
7. technical audit

For English output, LLM2 first generates the canonical Spanish report, the
translation stage translates the structured JSON, and the backend rebuilds the
same `structured_report` shape from the translated content.

## Audit Metadata

Each run records version and hash metadata in `audit_metadata` and in the
summary metadata:

- `pipeline_version`
- `axis_ontology_version`
- `axis_ontology_hash`
- `llm2_prompt_version`
- `translation_prompt_version`
- `global_interpretation_schema_version`
- `canonical_frame_version`
- `deterministic_validator_version`
- `structured_report_version`
- input CSV SHA-256
- deterministic summary SHA-256
- canonical analysis frame SHA-256
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
