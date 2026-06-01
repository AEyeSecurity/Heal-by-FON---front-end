# Implementation Log

## 2026-05-25

### Frontend

- Created React + Vite frontend.
- Added Force of Nature visual identity using public logo asset.
- Added drag/select VCF upload UI.
- Added upload and validation progress bars.
- Added pipeline stepper:
  - VCF upload
  - integrity validation
  - downstream analysis
- Added Spanish/English UI language selector.
- Added quick and full analysis modes.
- Added Cloudflare Turnstile widget.
- Added duplicate upload reuse modal.
- Prepared Cloudflare Pages deployment.

### Backend

- Created Node/Express API in `server/dev-api.js`.
- Added chunked upload protocol for large VCF files.
- Added upload manifests under isolated `uploadId` directories.
- Added duplicate lookup endpoint.
- Added validation job polling endpoints.
- Delegated heavy VCF processing to external Python validator.
- Added public result sanitization.
- Added optional n8n webhook integration points.

### Validation

- Reused the existing streaming Python validator:
  - `validate_vcf_integrity.py`
  - VCF/VCF.GZ support
  - checksum support
  - full streaming stats support
- Tested with small VCF samples.
- Tested with `sample_nucleus_dna_download_vcf_NU-DRSQ-5692_copy.vcf`.
- Confirmed metrics match the earlier Colab baseline:
  - total rows: `4,902,011`
  - PASS rows: `4,887,587`
  - SNV: `3,947,988`
  - non-SNV: `954,023`
  - multiallelic: `89,363`
  - heterozygous GT: `2,941,757`
  - homozygous alternate GT: `1,852,141`
  - non-diploid/complex GT: `108,113`

### Cloudflare

- Connected frontend to Cloudflare Pages.
- Created dedicated Cloudflare Tunnel for HEAL API.
- Published:
  - `https://healbyfon.aeye.com.ar`
  - `https://heal-api.aeye.com.ar`
- Added Turnstile support.
- Configured API to require Turnstile for upload initiation.

### ServerCIT

- Created operational scripts:
  - `C:\ServerCIT\services\heal-vcf-api\start_heal_vcf_api.ps1`
  - `C:\ServerCIT\services\heal-vcf-api\install_heal_vcf_api_task.ps1`
  - `C:\ServerCIT\scripts\start_cloudflared_heal_api.ps1`
  - `C:\ServerCIT\scripts\install_cloudflared_heal_api_task.ps1`
- Created server-side environment template:
  - `C:\ProgramData\HealByFonApi\heal-vcf-api.env.example`
- Stored runtime secrets outside the repository.

### GitHub

- Repository:
  - `https://github.com/AEyeSecurity/Heal-by-FON---front-end`
- Main branch receives Cloudflare Pages deployments.

## Current State

The system is ready for the next stage: connecting validated VCF events to n8n.

Remaining before stable production:

- rotate tunnel token
- install API/tunnel persistence from elevated PowerShell
- configure n8n webhook URLs
- define retention policy for uploaded VCFs
- move from client fingerprint isolation to authenticated user ownership when multi-user requirements become concrete

### 2026-05-31 Operational Follow-up

- Added current-user Startup fallback launchers for HEAL API and HEAL Cloudflare Tunnel.
- Verified public API health and Turnstile enforcement.
- Confirmed n8n local REST API requires authentication (`401`) and the existing HEAL webhook is not registered while its workflow remains inactive.
- No direct SQLite writes were made.

## 2026-06-01

### Match Preparation Service

- Added runtime service:
  - `C:\ServerCIT\services\heal-match-preparation\prepare_match_deliverable.py`
  - `C:\ServerCIT\services\heal-match-preparation\run_heal_match_preparation.ps1`
- Initially added standalone n8n workflow, later superseded by the Workflow 4 internal stage:
  - `HEAL - Match Preparation`
  - workflow ID: `HEALmatchPrep01`
  - webhook path: `heal-match-preparation-9b2f4a7c8d134b61`
- Added backend env:
  - `HEAL_MATCH_PREPARATION_ROOT`
- Added download endpoints:
  - `GET /api/vcf-canon-matches/:jobId/preparation-audit`
  - `GET /api/vcf-canon-matches/:jobId/preparation-minimal`

### Validation

- Direct script test on the real VCF match output:
  - rows total: `149`
  - rows with observed genotype: `69`
  - strict matches inherited from Workflow 4: `36`
  - ALT-review matches inherited from Workflow 4: `33`
  - no VCF position match: `79`
  - no rsID detected: `1`
- n8n webhook test succeeded after safe n8n restart.
- Backend smoke test succeeded:
  - Workflow 4 completed.
  - Workflow 5 completed.
  - audit CSV download returned `200`.
  - minimal CSV download returned `200`.

### Operations

- Ran official safe n8n restart:
  - backup: `C:\n8n-backups\daily\20260601-094331`
  - workflows exported: `128`
  - credentials exported: `65`
  - final health: `ok`
- Restarted only the HEAL API process to load backend/env changes.

### Workflow Consolidation Follow-up

- Moved match preparation inside `HEAL - VCF Canon Match`.
- Left the standalone `HEAL - Match Preparation` workflow inactive.
- Restored the user-facing pipeline to four visible steps:
  - VCF upload
  - integrity validation
  - VCF-canon match
  - downstream analysis
- Kept internal progress bars for VCF-canon match and match preparation.
- Corrected `rsid_without_coordinates` classification in the VCF-canon match script.
- Ran official safe n8n restart:
  - backup: `C:\n8n-backups\daily\20260601-100652`
  - workflows exported: `128`
  - credentials exported: `65`
  - final health: `ok`

### External Variant Enrichment

- Added runtime service:
  - `C:\ServerCIT\services\heal-variant-enrichment\enrich_observed_variants.py`
  - `C:\ServerCIT\services\heal-variant-enrichment\run_heal_variant_enrichment.ps1`
- Added n8n workflow:
  - `HEAL - Variant Enrichment`
  - workflow ID: `HEALvariantEnrich01`
  - webhook path: `heal-variant-enrichment-3a9f6d2b4c1e48f0`
- Added backend env:
  - `HEAL_VARIANT_ENRICHMENT_ROOT`
  - `HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL`
- Added download endpoint:
  - `GET /api/vcf-canon-matches/:jobId/enrichment`
- Added frontend internal progress bar:
  - External enrichment / Enriquecimiento externo
- Added compact download icons next to completed progress bars for auditable CSV stages.

### External Enrichment Validation

- Direct script smoke test with `rs429358` succeeded:
  - status: `valid`
  - observed rows: `1`
  - unique rsIDs: `1`
  - sources: Ensembl Variation, Ensembl VEP, ClinVar E-utilities, MyVariant.info
- n8n webhook smoke test succeeded:
  - execution ID: `238671`
  - cache hit on repeated `rs429358`
- Backend smoke test with small controlled VCF succeeded:
  - job status: `complete`
  - final stage: `enriching`
  - enrichment status: `warning`
  - enrichment rows: `0`
  - enrichment CSV download returned a valid header-only CSV
- Ran official safe n8n restart:
  - backup: `C:\n8n-backups\daily\20260601-185238`
  - workflows exported: `129`
  - credentials exported: `65`
  - final health: `ok`
