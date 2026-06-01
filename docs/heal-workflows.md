# HEAL Workflow Map

## Current Client Path

```text
Canon change
  -> Workflow 2: Canon Sheet Intake
  -> Workflow 3: rsID Coordinate Resolution

VCF upload
  -> Workflow 1: VCF Integrity Check
  -> Workflow 4: VCF-Canon Match
  -> Workflow 5: Match Preparation
  -> Downstream analysis placeholder
```

The browser only orchestrates uploads and polling. Heavy work is delegated to backend scripts through n8n workflows. n8n receives file references, not multi-GB binary payloads.

## Workflow 1 - VCF Integrity Check

- n8n name: `HEAL - VCF Integrity Check`
- Purpose: validate VCF file integrity.
- Runtime service: `C:\ServerCIT\services\heal-vcf-integrity`
- Key script: `validate_vcf_integrity.py`
- Status: kept inactive as the original standalone workflow; the API currently runs validation directly and still emits validation events to n8n.

Validation includes VCF/VCF.GZ detection, gzip readability, required headers, first variants, SHA-256 checksum, and optional full streaming metrics.

## Workflow 2 - Canon Sheet Intake

- n8n name: `HEAL - Canon Sheet Intake`
- Webhook env: `HEAL_N8N_CANON_WEBHOOK_URL`
- Runtime service: `C:\ServerCIT\services\heal-canon-intake`
- Key script: `process_heal_canon.py`
- Trigger: user uploads a new canon from the frontend.

Outputs include cleaned canon rows, rsID master, preview JSON, and processing summary. This runs only when the canon changes.

## Workflow 3 - rsID Coordinate Resolution

- n8n name: `HEAL - rsID Coordinate Resolution`
- Webhook env: `HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL`
- Runtime service: `C:\ServerCIT\services\heal-rsid-resolution`
- Key script: `resolve_rsid_coordinates.py`
- Trigger: immediately after Workflow 2 succeeds.

The output is the match-ready rsID table used by all later VCF uploads until the canon changes again.

## Workflow 4 - VCF-Canon Match

- n8n name: `HEAL - VCF Canon Match`
- Webhook env: `HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL`
- Runtime service: `C:\ServerCIT\services\heal-vcf-canon-match`
- Key script: `match_vcf_to_rsid_ready.py`
- Trigger: after a VCF passes integrity validation.

This workflow scans the VCF once and extracts candidate rows for the current canon targets. It creates `sheet_final_consolidated.csv` for QA and downstream preparation.

## Workflow 5 - Match Preparation

- n8n name: `HEAL - Match Preparation`
- Workflow ID: `HEALmatchPrep01`
- Webhook path: `heal-match-preparation-9b2f4a7c8d134b61`
- Webhook env: `HEAL_N8N_MATCH_PREPARATION_WEBHOOK_URL`
- Runtime service: `C:\ServerCIT\services\heal-match-preparation`
- Repository source copy: `services/heal-match-preparation`
- Key script: `prepare_match_deliverable.py`
- Trigger: immediately after Workflow 4 succeeds.

Inputs:

```text
sheet_final_consolidated.csv
```

Outputs:

```text
heal_fon_deliverable_presentation_min.csv
heal_fon_deliverable_presentation_audit.csv
match_preparation_summary.json
```

The frontend exposes download buttons for:

- the raw consolidated match CSV from Workflow 4
- the audit-ready match preparation CSV from Workflow 5
- the minimal deliverable-style CSV from Workflow 5

## Why It Is Fast

The Colab prototype used dataframe operations over a local notebook. The server version keeps the same logical stages but improves the execution shape:

- Large VCF uploads are chunked at the browser/API boundary, so the server never receives the full VCF as one request body.
- The VCF validator reads sequentially by streaming, not by loading the whole file into memory.
- Canon processing and rsID coordinate resolution happen only when the canon changes, not for every VCF.
- Workflow 4 precomputes the target chromosome/position keys from the canon and then scans the VCF once. Each VCF row is checked against indexed target keys instead of comparing every canon row against every VCF row.
- Workflow 5 does not re-read the VCF. It consumes the small consolidated CSV produced by Workflow 4, so preparation is effectively immediate.
- n8n only orchestrates paths and process execution. Heavy parsing stays in external scripts, which avoids large binary payloads and memory pressure inside n8n.

For the current test canon, Workflow 4 produces 149 consolidated rows. Workflow 5 works on those 149 rows, not on the full 1.5 GB VCF.

## Audit Downloads

After a VCF job completes:

```text
GET /api/vcf-canon-matches/:jobId/download
GET /api/vcf-canon-matches/:jobId/preparation-audit
GET /api/vcf-canon-matches/:jobId/preparation-minimal
```

The API verifies job ownership and allowed filesystem roots before serving any artifact. Local paths are not exposed in browser JSON.

## Operational Notes

- `HEAL - Match Preparation` is active and validated by webhook.
- The official n8n safe restart was used on 2026-06-01 to register the new production webhook.
- The workflow import did not preserve `parentFolderId`; no direct SQLite folder update was performed for Workflow 5.
- The latest verified n8n backup made during restart was `C:\n8n-backups\daily\20260601-094331`.
