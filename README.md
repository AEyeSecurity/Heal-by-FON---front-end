# HEAL VCF Upload

Local React prototype for uploading a VCF file and running the HEAL by FON VCF integrity validator.

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
```

Default chunk size is 8 MiB. This keeps every request well below Cloudflare's common proxied request body limits while allowing multi-GB VCF files to land on the server by streaming each chunk to disk. The browser only receives an `uploadId`; local server paths stay on the backend.

When the upload is complete, the backend can notify n8n with:

```text
HEAL_N8N_UPLOAD_WEBHOOK_URL
```

When validation is complete, it can notify n8n with:

```text
HEAL_N8N_VALIDATION_WEBHOOK_URL
```

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
