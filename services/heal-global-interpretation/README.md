# HEAL Global Interpretation Service

This service implements LLM2: one normalized observed-variant interpretation table in, one structured global synthesis out.

It consumes:

- `individual_variant_interpretations_normalized.csv`

It produces:

- `global_interpretation_payload.json`
- `deterministic_summary.json`
- `global_interpretation.json`
- `global_interpretation_sections.csv`
- `global_interpretation_summary.json`

LLM2 is synthesis-only. It must not reinterpret variants from scratch or change individual confidence labels.

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
