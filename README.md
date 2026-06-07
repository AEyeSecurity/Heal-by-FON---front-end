# HEAL VCF Upload

Local React prototype for uploading a VCF file, validating HEAL by FON VCF integrity, matching against the current canon, and enriching observed variants for audit.

## Documentation

- [Technical architecture](docs/technical-architecture.md)
- [HEAL workflow map](docs/heal-workflows.md)
- [Operations runbook](docs/operations-runbook.md)
- [Security notes](docs/security-notes.md)
- [Implementation log](docs/implementation-log.md)

## Local Run

```powershell
npm install
npm run start
```

Open:

```text
http://127.0.0.1:5173
```

The local API listens on:

```text
http://127.0.0.1:8787
```

and writes each upload to an isolated folder under:

```text
C:\ServerCIT\services\heal-vcf-integrity\incoming\<uploadId>
```

By default the development API keeps up to 12 upload workspaces and removes stale uploads older than 24 hours. Configure this with:

```text
HEAL_MAX_UPLOADS
HEAL_UPLOAD_TTL_HOURS
HEAL_UPLOAD_CHUNK_SIZE_BYTES
```

The API runs:

```text
C:\ServerCIT\services\heal-vcf-integrity\validate_vcf_integrity.py
```

## Analysis Modes

- Quick analysis: validates file accessibility, format, basic headers, first variant rows, gzip readability when applicable, and SHA-256 checksum.
- Full analysis: includes quick validation plus full streaming metrics for total variant rows, IDs/rsIDs, PASS rows, SNV/non-SNV, multiallelic rows, genotype distribution, malformed rows, and top chromosomes/contigs.

The UI supports selecting how many initial variant rows to validate in detail, from 1 to 100, defaulting to 20.

## Large File Upload Flow

The Pages frontend never receives or stores the VCF. It sends the file directly to the external HEAL API in chunks:

```text
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
POST /api/vcf-canon-matches/:jobId/individual-interpretation
GET  /api/vcf-canon-matches/:jobId/individual-interpretations
POST /api/vcf-canon-matches/:jobId/interpretation-normalization
GET  /api/vcf-canon-matches/:jobId/individual-interpretations-normalized
```

Default chunk size is 8 MiB. This keeps every request well below Cloudflare's common proxied request body limits while allowing multi-GB VCF files to land on the server by streaming each chunk to disk. The browser receives an `uploadId` plus an opaque per-upload access token for subsequent stage triggers and artifact downloads; local server paths stay on the backend.

If the same client reuses an already uploaded VCF, starting validation or match refreshes that upload workspace. The 24-hour cleanup window counts again from the latest use.

When the upload is complete, the backend can notify n8n with:

```text
HEAL_N8N_UPLOAD_WEBHOOK_URL
```

When validation is complete, it can notify n8n with:

```text
HEAL_N8N_VALIDATION_WEBHOOK_URL
```

After a valid or warning validation result, the frontend asks the backend to run the VCF-canon match. The backend uses the current cleaned canon and current resolved rsID master, then invokes:

```text
HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL
```

The UI currently shows four pipeline steps:

```text
VCF upload -> Integrity validation -> VCF-Canon match -> Downstream analysis
```

The VCF-canon match step internally includes the targeted VCF scan and match preparation. The fourth step is a placeholder for the next interpretation workflows.

As each stage finishes, the UI exposes CSV downloads for QA and review. The API serves the per-job consolidated match CSV and prepared audit/minimal CSVs as soon as those artifacts exist, even if later enrichment is still running. Once enrichment finishes, the current technical enrichment CSV remains available as a QA artifact and a second Colab-style interpretive CSV becomes available for user/AI review. Local server paths are not exposed.

The interpretive CSV preserves the Colab-style enrichment fields and also carries the curated canon context as `Canon Effect`. This keeps the downstream interpretation layer from having to infer gene-level meaning from memory or external search when the canon already supplied that context.

After match preparation, the backend invokes:

```text
HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL
```

That stage enriches only observed genotype rows with public Ensembl, ClinVar, and MyVariant.info data. It uses a schema-versioned local cache under `C:\ServerCIT\services\heal-variant-enrichment\cache` so repeated audits do not re-query the same rsIDs unless the enrichment schema changes.

The original Colab does not generate a final `.docx` or PDF report. Its deterministic final coded output is the Colab-style enriched observed-variant CSV plus deliverable-style CSV tables. The app now generates that Colab-style CSV as `heal_fon_interpretation_enriched_observed69.csv`; the next planned stage is a controlled final interpretation/report workflow; see `docs/final-interpretation-next-step.md`.

The first interpretation module is now separated as LLM1: individual observed-variant interpretation. It consumes `heal_fon_interpretation_enrichment_plus.csv`, prepares a filtered JSON payload per row, and writes `individual_variant_interpretations.csv`. It runs with controlled parallelism, writes progress incrementally, and keeps row-level errors isolated.

After LLM1, a separate deterministic QA normalization stage writes `individual_variant_interpretations_normalized.csv`. This stage normalizes duplicate confidence drift, applies generalized evidence-based confidence caps/raises, shortens overlong one-sentence outputs, and preserves audit columns explaining each adjustment. Deterministic grouping and global LLM2 reporting remain separate later modules.

## Canon Flow

The "Change canon" modal accepts `.csv` and `.xlsx` files. Canon upload is protected with the same Turnstile flow as VCF uploads.

When a canon changes, the backend runs:

```text
HEAL_N8N_CANON_WEBHOOK_URL
HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL
```

This keeps the cleaned canon and rsID match-ready table current, so they are not regenerated for every VCF upload.

If a webhook token is needed, set it outside the repository:

```text
HEAL_N8N_WEBHOOK_TOKEN
```

Do not commit secrets.

## Security Controls

The backend has server-side controls because CORS alone does not protect a public API from direct scripted traffic.

Configured controls:

- allowed file names: `.vcf`, `.vcf.gz`, `.gz`
- max file size: `HEAL_MAX_FILE_SIZE_BYTES`, default 6 GiB
- upload workspace retention: `HEAL_MAX_UPLOADS` and `HEAL_UPLOAD_TTL_HOURS`
- max active uploads per browser/client fingerprint: `HEAL_MAX_ACTIVE_UPLOADS_PER_CLIENT`
- init rate limit per IP/hour: `HEAL_INIT_RATE_LIMIT_PER_HOUR`
- optional Cloudflare Turnstile verification on upload init: `HEAL_TURNSTILE_SECRET`

For production, create a Cloudflare Turnstile widget for the Pages hostname and API hostname. Put the public site key in Pages as `VITE_TURNSTILE_SITE_KEY`, and put the secret key only on the backend host as `HEAL_TURNSTILE_SECRET`.

## Environment

Copy `.env.example` to `.env` when you need to override local settings.

For a deployed frontend, set:

```text
VITE_API_BASE=https://your-api.example.com
VITE_TURNSTILE_SITE_KEY=your-public-turnstile-site-key
```

This repository includes `.env.production` with the current public production values for Cloudflare Pages. These are not secrets: Vite exposes every `VITE_*` value to the browser bundle.

Also add the deployed Pages origin to:

```text
HEAL_ALLOWED_ORIGINS
```

## Cloudflare Pages

The frontend is a static Vite app and can be built with:

```powershell
npm run build
```

Cloudflare Pages settings:

```text
Build command: npm run build
Build output directory: dist
```

Optional Wrangler deploy:

```powershell
npm run deploy:pages
```

The local upload API is for development only. Large VCF uploads should not be handled only by static Pages. Production should use a separate API or a Cloudflare Worker/R2 signed upload flow, then trigger a validator service that streams the file from durable storage.

Recommended first production deployment:

1. Deploy this React app to Cloudflare Pages.
2. Run `npm run api:prod` on the HEAL server behind HTTPS.
3. Set `VITE_API_BASE` in Pages to the public API origin.
4. Set `VITE_TURNSTILE_SITE_KEY` in Pages if Turnstile is enabled.
5. Add the Pages origin to `HEAL_ALLOWED_ORIGINS` on the API server.
6. Set `HEAL_TURNSTILE_SECRET` and the n8n webhook variables on the API server, not in the frontend.

R2 remains a good later option if the file must land in object storage first. In that version, the API should issue presigned multipart upload instructions and n8n should receive the R2 object key instead of a local path.

References:

- Cloudflare Workers request body limits: https://developers.cloudflare.com/workers/platform/limits/
- Cloudflare R2 object uploads and multipart uploads: https://developers.cloudflare.com/r2/objects/upload-objects/
- Cloudflare R2 presigned URLs: https://developers.cloudflare.com/r2/api/s3/presigned-urls/
