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

### Upload Reuse Retention

- Updated VCF reuse behavior so starting validation or VCF-canon match refreshes the upload manifest and filesystem timestamps.
- The configured 24-hour cleanup window now restarts from the latest actual use of a reused VCF.

### QA Mode, Optional pysam Parser, and Enrichment Parity

- Added frontend `Control de Calidad` mode.
- Added stage play controls for QA mode while keeping the normal one-button path.
- Added VCF parser selection:
  - `streaming` remains the stable default.
  - `pysam` is attempted when requested and falls back to streaming with an explicit warning if unavailable.
- Attempted `python -m pip install pysam` on the current Windows/Python 3.13 runtime. It failed because no wheel was available and source build required native tooling/htslib configuration. No production path depends on `pysam`; fallback behavior was verified.
- Added QA/debug match download endpoints for VCF candidates, strict matches, ALT-review matches, position-review matches, and no-position-match rows.
- Expanded observed variant enrichment to better mirror the Colab:
  - Ensembl mapping, phenotype, population, VEP transcript, and colocated-variant summaries
  - ClinVar `esearch` plus `esummary`
  - MyVariant CADD/dbSNP/dbNSFP-derived fields where available
  - allele and external-support summaries
  - compact raw JSON columns for source-level QA
- Updated `HEAL - VCF Canon Match` so n8n forwards `vcfParser` to the matcher script.
- Ran official safe n8n restart:
  - backup: `C:\n8n-backups\daily\20260601-194608`
  - workflows exported: `129`
  - credentials exported: `65`
  - final health: `ok`
- Restarted only HEAL API using `C:\ServerCIT\services\heal-vcf-api\start_heal_vcf_api.ps1` and verified all production env flags/webhooks were restored.

Validation:

- `python -m py_compile` passed for updated Python services.
- `node --check server\dev-api.js` passed.
- `npm run build` passed.
- Validator streaming smoke passed.
- Validator `pysam` fallback smoke passed.
- VCF-canon match `pysam` fallback smoke passed.
- Enrichment parity smoke with `rs429358` passed and produced the expanded columns.

## 2026-06-02

### Stage Artifact Downloads and Enrichment Resilience

- Added `artifactsReady` to VCF-canon match polling responses.
- Match, debug, and preparation CSV download endpoints now allow downloads as soon as the corresponding artifact exists, instead of requiring the entire job to be `complete`.
- The frontend now tracks stage artifact readiness separately from final job completion and shows progress-bar download icons as soon as each stage can be audited.
- Validation completion now records a `validation` object in the upload manifest and refreshes upload retention immediately after validation finishes.
- Added backend-level enrichment retry:
  - first normal attempt
  - up to two additional attempts
  - failure message includes all attempt errors
- Added a clean frontend popup for enrichment-stage failure after retries while preserving any earlier match/preparation downloads.

Validation:

- `node --check server\dev-api.js` passed.
- `npm run build` passed.
- Restarted only the HEAL API with `C:\ServerCIT\services\heal-vcf-api\start_heal_vcf_api.ps1`.
- HEAL API health returned `ok=true` with all production webhook flags configured.
- Local controlled fixture test using `valid_small.vcf` passed:
  - validation status: `valid`
  - upload manifest recorded `validation.status=valid`
  - match job completed
  - `artifactsReady.matches=true`
  - `artifactsReady.preparation=true`
  - `artifactsReady.enrichment=true`
  - match, preparation, and enrichment downloads returned HTTP `200`
- Local browser UI check passed:
  - page loads
  - five progress bars render
  - three analysis modes render
  - no download icons show before artifacts exist

### Colab Final Output Review

- Reviewed `TEST PROJECT_ Genomics x AI Interpretation Sprint.ipynb`.
- Reviewed `Resumen integral.docx`, `TEST Project & Prototype_English.docx`, and `Heal by FON — Genetics Product Specification.docx`.
- Confirmed the notebook does not generate a final `.docx` or PDF clinical report.
- Confirmed deterministic notebook outputs stop at CSV artifacts, especially:
  - `heal_fon_deliverable_presentation_min.csv`
  - `heal_fon_deliverable_presentation_audit.csv`
  - `heal_fon_interpretation_enriched_observed69.csv`
- Documented the next recommended workflow in `docs/final-interpretation-next-step.md`.

## 2026-06-03

### Colab-Style Interpretive Enrichment Output

- Re-reviewed the notebook cell that generates `heal_fon_interpretation_enriched_observed69.csv`.
- Kept `heal_observed_variant_enrichment.csv` as the technical QA enrichment output.
- Added a second enrichment artifact:
  - `heal_fon_interpretation_enriched_observed69.csv`
  - preserves the Colab column names and ordering
  - restores the narrative `external_support_summary`
  - includes `myvariant_best_id`, `myvariant_best_score`, and `myvariant_top_level_fields`
  - preserves `Notes` and `Interpretation (1 sentence)` from match preparation
- Aligned external source behavior more closely with the notebook:
  - MyVariant now queries by `scopes=dbsnp.rsid`, returns up to 3 hits, and requests the broader Colab field set.
  - ClinVar now tries a stricter Variant Name search before the broader rsID fallback.
  - Enrichment cache is schema-versioned so older cache entries are refreshed when output fields change.
- Added API endpoint:
  - `GET /api/vcf-canon-matches/:jobId/enrichment-interpretive`
- Updated the frontend:
  - the enrichment progress-bar download icon now downloads the interpretive CSV
  - the previous technical CSV is exposed as a separate QA download button

Validation:

- `python -m py_compile` passed for the deployed enrichment script.
- `node --check server\dev-api.js` passed.
- Full enrichment smoke with a fresh cache produced:
  - technical QA CSV: 69 rows, 58 columns
  - Colab-style interpretive CSV: 69 rows, 55 columns
  - unique rsIDs: 56
  - MyVariant best-id/best-score/top-level fields populated for 69 rows
- The fresh-cache smoke returned warnings for transient Ensembl sources on 4 rsIDs; the stage still produced both CSVs and recorded the source errors in summary metadata.

## 2026-06-06

### Enrichment Cache Robustness and Error Modal UX

- Reviewed the user-downloaded `heal-fon-interpretation-enriched-observed69.csv` against the Colab-style column contract.
- Confirmed the CSV had the expected 69 rows and 55 Colab-style columns.
- Found the practical mismatch: some VEP-derived interpretive fields were empty because a transient Ensembl VEP timeout had been stored in the enrichment cache.
- Updated enrichment cache behavior:
  - source-error payloads are no longer reused from cache
  - partial source-error payloads are not written to cache
  - cache schema version bumped to refresh older entries
  - Ensembl Variation `most_severe_consequence` is stored and used as fallback when VEP is temporarily unavailable
- Updated the frontend error dialog:
  - no raw backend error text in the popup
  - fixed retry-oriented text
  - added a top-right close button
  - enrichment inline error is now concise

Validation:

- Full fresh-cache enrichment smoke passed with status `valid`.
- Generated Colab-style interpretive CSV:
  - 69 rows
  - 55 columns
  - `vep_most_severe_consequence` populated for 69 rows
  - `external_support_summary` includes the narrative VEP/Ensembl/ClinVar/MyVariant summary again
- Copied a corrected comparison CSV to:
  - `C:\Users\Usuario\Downloads\heal-fon-interpretation-enriched-observed69-fixed.csv`
- `python -m py_compile` passed for the deployed enrichment script.
- `node --check server\dev-api.js` passed.
- `npm run build` passed.
- Local UI smoke loaded successfully at `http://127.0.0.1:5173/`.

### Canon Context and Colab Debug Parity

- Compared the Colab intermediate files against the app-generated artifacts:
  - `sheet_final_consolidated (master).csv`
  - `vcf_joined_chr_pos (1).csv`
  - `heal_fon_interpretation_enriched_observed69.csv`
- Found that the app kept the canon `effect` column in `sheet_final_consolidated.csv` but dropped it before enrichment.
- Added `Canon Effect` to match preparation outputs and the Colab-style interpretive enrichment CSV.
- Added `canon_effect` to the technical QA enrichment CSV.
- Expanded Ensembl VEP transcript summaries to include transcript ID, consequence, impact, biotype, SIFT, PolyPhen, amino-acid change, and protein positions when available.
- Added `vcf_joined_chr_pos.csv` as a match debug artifact that joins targeted VCF hits back to rsID, source rows, source tables, categories, genes, and resolved coordinate metadata.
- Exposed the new debug artifact through:
  - `GET /api/vcf-canon-matches/:jobId/debug/vcf_joined_chr_pos`
  - the frontend Control de Calidad download section.
- Bumped enrichment cache schema version to refresh older VEP summaries.
- Synced updated Python service scripts to `C:\ServerCIT\services`.

Validation:

- `python -m py_compile` passed for repository scripts and deployed runtime scripts.
- `node --check server\dev-api.js` passed.
- Real VCF-canon match smoke against the current sample VCF produced:
  - 149 consolidated rows
  - 56 raw VCF candidate rows
  - 56 joined VCF candidate rows
  - 4,902,011 scanned VCF variant rows
- Enrichment subset smoke produced:
  - interpretive CSV: 5 rows, 56 columns, including `Canon Effect`
  - technical QA CSV: 5 rows, 59 columns, including `canon_effect`
  - VEP transcript summary with SIFT/PolyPhen/amino-acid/protein-position detail
- A full fresh-cache enrichment smoke was started but exceeded the interactive timeout; the targeted subset smoke reused the new schema cache entries generated before timeout.

### Enrichment Plus Artifact

- Kept the existing technical QA enrichment CSV unchanged as the low-level audit artifact.
- Kept the Colab-style interpretive enrichment CSV as the notebook-parity artifact.
- Added a third output, `heal_fon_interpretation_enrichment_plus.csv`, for richer downstream AI/clinical interpretation.
- Expanded the enrichment source set with:
  - GWAS Catalog associations by rsID.
  - ClinPGx/PharmGKB variant, clinical annotation, and variant annotation lookups.
  - VEP advanced fields including HGVS, MANE/canonical transcript, domains, CADD, REVEL, AlphaMissense, SIFT, PolyPhen, and protein-level coordinates when available.
- Added normalized/derived interpretive helpers:
  - ClinVar normalized classification.
  - ClinVar evidence strength.
  - ClinVar conflict flag.
  - population max-frequency summary.
  - interpretation readiness summary.
- Added backend route:
  - `GET /api/vcf-canon-matches/:jobId/enrichment-plus`
- Added frontend download button:
  - `Descargar CSV Enrichment Plus`

### Enrichment Retry and Job Persistence

- Added server-side persistence for VCF-canon match jobs under:
  - `C:\ServerCIT\services\heal-vcf-canon-match\jobs`
- Persisted jobs are loaded at HEAL API startup so completed match/preparation/enrichment artifacts survive a process restart.
- Added backend endpoint:
  - `POST /api/vcf-canon-matches/:jobId/retry-enrichment`
- The retry endpoint reuses `heal_fon_deliverable_presentation_audit.csv` and reruns only the external variant enrichment stage.
- Added frontend pop-up action:
  - `Reintentar enriquecimiento`
- Confirmed the user-reported `Cannot GET /enrichment-plus` symptom means the running HEAL API process has not loaded the newer route yet; it is not evidence that the Plus CSV is missing.

### LLM1 Individual Variant Interpretation

- Added a new module under `services/heal-individual-interpretation`.
- Added deterministic payload preparation from `heal_fon_interpretation_enrichment_plus.csv`.
- Added prompt and strict JSON Schema files:
  - `prompt_llm1.md`
  - `individual_variant_interpretation_schema.json`
- Added Python runner:
  - `interpret_observed_variants.py`
- Outputs:
  - `variant_interpretation_payloads.jsonl`
  - `variant_interpretation_payloads.csv`
  - `individual_variant_interpretations.jsonl`
  - `individual_variant_interpretations.csv`
  - `individual_variant_interpretation_errors.csv`
  - `individual_variant_interpretation_summary.json`
- Added backend endpoint:
  - `POST /api/vcf-canon-matches/:jobId/individual-interpretation`
  - `GET /api/vcf-canon-matches/:jobId/individual-interpretations`
- Added frontend stage:
  - `Interpretacion individual`
- Added frontend download:
  - `Descargar CSV interpretacion individual`
- This module is intentionally separate from deterministic grouping and LLM2 global interpretation.
- Performance/progress correction:
  - changed the operational model to `gpt-5-mini` for lower latency on this structured row-level task;
  - added `HEAL_LLM_MAX_WORKERS`, `HEAL_LLM_TIMEOUT_SECONDS`, and `HEAL_LLM_ROW_ATTEMPTS`;
  - added incremental output writes and `individual_variant_interpretation_progress.json`;
  - backend now reads the progress file while the Python process is running, so the frontend no longer remains at the initial percentage during long LLM calls;
  - persisted `running` jobs are marked failed/retryable on API restart to avoid zombie jobs.
- Confidence calibration correction:
  - clarified in the prompt that `Low` means low interpretive weight, not poor VCF quality;
  - clarified that `clinvar_conflict_flag=true` alone does not justify `Conflicting`;
  - added deterministic HEAL calibration after each LLM row to recalculate `final_confidence_level`, `evidence_conflict_flag`, `requires_professional_review`, and `interpretation_scope`;
  - reduced the prior overuse of `requires_professional_review`;
  - preserved the curated prototype expectation that VDR/Fok1, SOD2, and TP53 are the primary `Conflicting` rows.
