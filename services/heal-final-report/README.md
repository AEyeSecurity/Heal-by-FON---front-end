# HEAL Final Report

This service renders the structured LLM2 `global_interpretation.json` into a
user-facing Word document.

It does not call an LLM and it does not add new interpretation. The script only
formats the already-approved global interpretation sections into a readable
`.docx` report with metadata, cautions, summaries, biological axes, review
priorities, and final recommendation.

When `global_interpretation.json` includes `structured_report`, the renderer
uses that deterministic structure instead of walking the raw JSON generically.
The current stable section order is:

1. case overview
2. primary biological axes
3. notable genetic patterns
4. findings for review
5. limitations
6. next review steps
7. technical audit

For each biological axis, the report keeps the interpretation and contextual
review guidance first, then places supporting genes, rsIDs, and source
categories after the explanatory text.

## Runtime

Repository source:

```text
services/heal-final-report
```

Server runtime copy:

```text
C:\ServerCIT\services\heal-final-report
```

## Script

```text
render_final_report.py
```

Input is passed as base64 JSON:

```json
{
  "inputPath": "C:\\ServerCIT\\services\\heal-global-interpretation\\runs\\...\\global_interpretation.json",
  "outputDir": "C:\\ServerCIT\\services\\heal-final-report\\runs\\...",
  "fileName": "sample.vcf",
  "languageMode": "es",
  "audienceMode": "family"
}
```

Output:

```text
*_final_report.docx
final_report_summary.json
```

## Audit Metadata

`final_report_summary.json` records:

- `report_renderer_version`
- `report_template_version`
- `structured_report_version`
- SHA-256 of the input `global_interpretation.json`
- SHA-256 of the generated DOCX
- upstream `audit_metadata` from the global interpretation stage

This lets QA distinguish changes caused by input data, LLM2 prompt/schema,
translation source, report template, or renderer changes.

The final report is served by the HEAL API through:

```text
POST /api/vcf-canon-matches/:jobId/final-report
GET  /api/vcf-canon-matches/:jobId/final-report
```
