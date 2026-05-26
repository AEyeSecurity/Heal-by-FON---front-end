# HEAL by FON VCF Intake - Technical Architecture

## Scope

This repository contains the first production-facing slice of HEAL by FON: a web interface and backend API for receiving large VCF files and validating their integrity before downstream genomic interpretation workflows.

The current scope is intentionally limited to VCF intake and integrity validation. Clinical/genomic interpretation, report generation, multi-user accounts, and the n8n downstream pipeline are not implemented here yet.

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

The backend receives VCF chunks, assembles files on disk, starts streaming validation, and returns structured JSON results.

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
- optional full streaming metrics

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

## n8n Integration Points

The backend has two optional webhook points:

```text
HEAL_N8N_UPLOAD_WEBHOOK_URL
HEAL_N8N_VALIDATION_WEBHOOK_URL
```

Current status:

```text
n8nUploadWebhookConfigured: false
n8nValidationWebhookConfigured: false
```

Expected next step:

1. Create or update the n8n workflow that receives a completed upload or completed validation event.
2. Set webhook URLs in the backend environment file.
3. Keep webhook secrets outside GitHub and outside Cloudflare Pages.

## Production Domains

```text
Frontend: https://healbyfon.aeye.com.ar
API:      https://heal-api.aeye.com.ar
```

## Current Known Gaps

- API and tunnel are running, but persistence after server reboot still needs installation from an elevated PowerShell session.
- Tunnel token should be rotated because it was temporarily shared during setup.
- n8n webhooks are not configured yet.
- Upload retention is simple TTL/cap based storage, not a formal user/file lifecycle system.
- There is no user login or account isolation beyond the current browser/client fingerprint and Turnstile controls.
