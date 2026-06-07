# HEAL by FON VCF Intake - Technical Architecture

## Scope

This repository contains the first production-facing slice of HEAL by FON: a web interface and backend API for receiving large VCF files, validating their integrity, maintaining the current interpretation canon, matching VCF rows against the canon, preparing match outputs for audit, and enriching observed variants with public external sources.

Clinical/genomic interpretation, report generation, and multi-user accounts are not implemented here yet. The current downstream implementation stops after observed variant enrichment; later interpretation is still represented as a placeholder.

## Public Components

### Frontend

- Framework: React + Vite
- Hosting: Cloudflare Pages
- Public domain: `https://healbyfon.aeye.com.ar`
- Source entrypoint: `src/main.jsx`
- Styles: `src/styles.css`
- Public production config: `.env.production`

The frontend is a static application. It does not process, store, or validate VCF files itself. It sends files directly to the backend API in chunks.

### Backend API

- Runtime: Node.js + Express
- Source: `server/dev-api.js`
- Public API hostname: `https://heal-api.aeye.com.ar`
- Local bind: `http://127.0.0.1:8787`
- Tunnel: Cloudflare Tunnel, dedicated to HEAL API
- Upload storage root: configured by `HEAL_UPLOAD_ROOT`

The backend receives VCF chunks, assembles files on disk, starts streaming validation, orchestrates canon processing, starts the VCF-canon match, prepares audit/download artifacts, and starts observed variant enrichment.

### VCF Validator

- Validator script: `C:\ServerCIT\services\heal-vcf-integrity\validate_vcf_integrity.py`
- Execution mode: child process spawned by the API
- Processing style: streaming reads, avoiding full VCF loading into memory

The validator supports:

- VCF and `.vcf.gz` detection
- basic VCF headers
- first variant row checks
- gzip readability
- streaming checksum
- optional full metrics
- optional `pysam` parser mode with explicit fallback to the default streaming parser when `pysam` is unavailable

## Request Flow

```text
Browser
  -> Cloudflare Pages frontend
  -> Cloudflare Turnstile challenge
  -> https://heal-api.aeye.com.ar
  -> Cloudflare Tunnel
  -> http://127.0.0.1:8787
  -> C:\ServerCIT\services\heal-vcf-integrity\incoming\<uploadId>
  -> validate_vcf_integrity.py
  -> JSON validation result
```

## Upload Flow

The browser uploads large VCF files in chunks.

```text
POST /api/uploads/lookup
POST /api/uploads/init
PUT  /api/uploads/:uploadId/chunks/:chunkIndex
POST /api/uploads/:uploadId/complete
POST /api/validations
GET  /api/validations/:jobId
POST /api/vcf-canon-matches
GET  /api/vcf-canon-matches/:jobId
GET  /api/vcf-canon-matches/:jobId/download
GET  /api/vcf-canon-matches/:jobId/preparation-audit
GET  /api/vcf-canon-matches/:jobId/preparation-minimal
GET  /api/vcf-canon-matches/:jobId/enrichment
GET  /api/vcf-canon-matches/:jobId/enrichment-interpretive
GET  /api/vcf-canon-matches/:jobId/enrichment-plus
POST /api/vcf-canon-matches/:jobId/retry-enrichment
```

Default chunk size:

```text
8 MiB
```

The chunked protocol is used because multi-GB VCF files should not be uploaded through the static Pages application or buffered as a single request body.

## Duplicate Upload Reuse

Before uploading, the frontend calls:

```text
POST /api/uploads/lookup
```

The backend offers reuse only when all of these match:

- same sanitized file name
- same file size
- status is `complete`
- same client fingerprint

This avoids re-uploading the same multi-GB VCF during repeated tests while preventing one user from seeing or reusing another user's upload.

When an existing VCF is reused for validation or match, the backend refreshes the upload manifest and filesystem timestamps. The 24-hour retention window therefore restarts from the latest real use, so repeated work on the same VCF does not require re-uploading it every day.

## Analysis Modes

### Quick Analysis

The quick mode runs integrity validation only:

- file exists and is accessible
- size greater than zero
- VCF / VCF.GZ detection
- `##fileformat=VCF`
- `#CHROM` line
- first variant rows
- gzip readability if applicable
- checksum

### Full Analysis

The full mode includes quick validation plus whole-file streaming metrics:

- total variant rows
- non-empty ID rows
- rsID rows
- PASS rows
- multiallelic rows
- SNV / non-SNV
- genotype distribution
- malformed rows
- top chromosomes/contigs

## Public Result Shape

The API returns validation results without exposing local filesystem paths. Public metadata includes:

- upload ID
- file name
- size
- detected format
- sample IDs
- checked variant count
- checksum
- validation status
- warnings/errors
- optional full metrics

Local paths remain internal to the backend and n8n integration.

The VCF-canon match download endpoint serves the per-job `sheet_final_consolidated.csv` artifact for QA as soon as that artifact exists. Match preparation download endpoints similarly become available as soon as the preparation CSVs exist, even while downstream enrichment is still running. The technical enrichment endpoint serves `heal_observed_variant_enrichment.csv` for source-level QA. The interpretive enrichment endpoint serves `heal_fon_interpretation_enriched_observed69.csv`, matching the deterministic Colab output shape for user/AI review while adding `Canon Effect` from the curated canon. The Enrichment Plus endpoint serves `heal_fon_interpretation_enrichment_plus.csv`, which keeps the Colab-style fields and appends normalized clinical/evidence fields from VEP, ClinVar, population frequencies, GWAS Catalog, and ClinPGx/PharmGKB. The browser receives CSV attachments; JSON results and download responses do not expose internal filesystem paths.

The match polling response includes an `artifactsReady` object:

```json
{
  "matches": true,
  "debug": true,
  "preparation": true,
  "enrichment": false,
  "enrichmentInterpretive": false,
  "enrichmentPlus": false
}
```

This lets the frontend show the compact download icon next to each progress bar as each stage becomes auditable, instead of waiting for the full downstream chain to finish.

VCF-canon jobs are persisted under `C:\ServerCIT\services\heal-vcf-canon-match\jobs` after match/preparation/enrichment starts. This lets download and retry endpoints recover completed job artifacts after a HEAL API process restart. The persisted object is server-side only; `publicJob()` still strips internal artifact paths from browser JSON.

Control de Calidad mode exposes additional debug downloads through whitelisted endpoints:

```text
GET /api/vcf-canon-matches/:jobId/debug/vcf_candidates
GET /api/vcf-canon-matches/:jobId/debug/vcf_joined_chr_pos
GET /api/vcf-canon-matches/:jobId/debug/match_strict
GET /api/vcf-canon-matches/:jobId/debug/alt_review
GET /api/vcf-canon-matches/:jobId/debug/position_review
GET /api/vcf-canon-matches/:jobId/debug/no_vcf_match
GET /api/canon/current/debug/:artifact
```

The backend serves these only from known run directories and verifies job ownership before download.

## n8n Integration Points

The backend has webhook points for the current HEAL prototype:

```text
HEAL_N8N_UPLOAD_WEBHOOK_URL
HEAL_N8N_VALIDATION_WEBHOOK_URL
HEAL_N8N_CANON_WEBHOOK_URL
HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL
HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL
HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL
```

Current status:

```text
n8nUploadWebhookConfigured: true
n8nValidationWebhookConfigured: true
n8nCanonWebhookConfigured: true
n8nRsidResolutionWebhookConfigured: true
n8nVcfCanonMatchWebhookConfigured: true
n8nVariantEnrichmentWebhookConfigured: true
```

Workflow responsibilities:

- `HEAL - VCF Integrity Check`: original integrity workflow, kept inactive.
- `HEAL - Canon Sheet Intake`: cleans the uploaded canon and creates `rsid_master.csv`.
- `HEAL - rsID Coordinate Resolution`: runs only after canon changes and creates the match-ready rsID table.
- `HEAL - VCF Canon Match`: runs after a VCF passes validation, performs the targeted VCF scan, matches against the current canon, and creates audit/minimal CSV outputs for QA and downstream review.
- `HEAL - Variant Enrichment`: runs after match preparation and enriches observed genotype rows with Ensembl, ClinVar, and MyVariant.info data.
- `HEAL - Match Preparation`: superseded as a standalone workflow and left inactive; its script now runs inside `HEAL - VCF Canon Match`.

Natural usage path:

```text
Canon change -> clean canon -> resolve rsID coordinates
VCF upload -> integrity validation -> VCF-canon match, targeted scan, preparation, and observed enrichment -> downstream analysis placeholder
```

Webhook secrets, if added later, must stay outside GitHub and outside Cloudflare Pages.

## Final Interpretation Gap

The original Colab did not generate a final `.docx` or PDF clinical report. Its coded outputs stop at structured CSV files:

- `sheet_final_consolidated.csv`
- `heal_fon_deliverable_presentation_min.csv`
- `heal_fon_deliverable_presentation_audit.csv`
- `heal_fon_interpretation_enriched_observed69.csv`

The downstream interpretation described in the project documents was performed manually/with AI assistance outside the notebook. The next implementation stage should therefore create a controlled interpretation/report workflow that consumes the enriched observed-variant CSV plus the 149-row deliverable/audit table and writes a final presentable table/report under HEAL language constraints.

## Production Domains

```text
Frontend: https://healbyfon.aeye.com.ar
API:      https://heal-api.aeye.com.ar
```

## Current Known Gaps

- API and tunnel are running, but persistence after server reboot still depends on the installed local scheduled tasks/service setup.
- Tunnel token should still be rotated later because it was temporarily shared during setup.
- Workflow 3, workflow 4, and workflow 5 are active but not visually assigned to the `Heal by FON` folder in n8n until specific `parentFolderId` maintenance updates are authorized.
- Upload retention is simple TTL/cap based storage, not a formal user/file lifecycle system.
- There is no user login or account isolation beyond the current browser/client fingerprint and Turnstile controls.
