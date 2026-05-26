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
