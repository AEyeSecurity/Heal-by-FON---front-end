# HEAL Workflow Map

## Current Client Path

```text
Canon change
  -> Workflow 2: Canon Sheet Intake
  -> Workflow 3: rsID Coordinate Resolution

VCF upload
  -> Workflow 1: VCF Integrity Check
  -> Workflow 4: VCF-Canon Match, targeted scan, and match preparation
  -> Workflow 5: External Variant Enrichment
  -> LLM1: Individual Variant Interpretation
  -> Deterministic QA Normalization
  -> LLM2: Global Interpretation
  -> Downstream analysis placeholder
```

The browser only orchestrates uploads and polling. Heavy work is delegated to backend scripts through n8n workflows. n8n receives file references, not multi-GB binary payloads.

## Workflow 1 - VCF Integrity Check

- n8n name: `HEAL - VCF Integrity Check`
- Purpose: validate VCF file integrity.
- Runtime service: `C:\ServerCIT\services\heal-vcf-integrity`
- Key script: `validate_vcf_integrity.py`
- Status: kept inactive as the original standalone workflow; the API currently runs validation directly and still emits validation events to n8n.

Validation includes VCF/VCF.GZ detection, gzip readability, required headers, first variants, SHA-256 checksum, and optional full metrics.

The validator accepts a `vcfParser` setting:

- `streaming`: default production parser, no native dependency.
- `pysam`: optional parser closer to the original Colab implementation. If `pysam` is unavailable or cannot open the file, the validator records a warning and falls back to `streaming`.

On the current Windows/Python 3.13 runtime, `pysam` does not install from a prebuilt wheel and source compilation fails without a native htslib toolchain. The option is implemented safely, but currently falls back to streaming until the runtime is moved to a compatible Python/Linux/conda environment.

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

This workflow includes the Colab "targeted VCF scan" stage. It scans the VCF once and extracts candidate rows for the current canon targets by `CHROM:POS`, then joins those candidates with the current canon and rsID match-ready table. It creates `sheet_final_consolidated.csv` for QA.

Workflow 4 also accepts `vcfParser` from the HEAL API and forwards it to the matcher script. The same parser policy applies here: `streaming` is stable; `pysam` is attempted when requested and falls back to streaming with an explicit warning if unavailable.

Workflow 4 also runs the match preparation script internally. This keeps the user-facing pipeline as one match step while still producing separate audit/download artifacts.

Internal preparation service:

- Runtime service: `C:\ServerCIT\services\heal-match-preparation`
- Repository source copy: `services/heal-match-preparation`
- Key script: `prepare_match_deliverable.py`
- Trigger: inside Workflow 4, immediately after `sheet_final_consolidated.csv` is created.

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
- the audit-ready match preparation CSV from Workflow 4's internal preparation stage
- the minimal deliverable-style CSV from Workflow 4's internal preparation stage
- QA/debug match CSVs: VCF position candidates, VCF candidates joined back to canon/rsID metadata, strict matches, ALT-review matches, position-review matches, and no-position-match rows

## Workflow 5 - External Variant Enrichment

- n8n name: `HEAL - Variant Enrichment`
- Webhook env: `HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL`
- Runtime service: `C:\ServerCIT\services\heal-variant-enrichment`
- Repository source copy: `services/heal-variant-enrichment`
- Key script: `enrich_observed_variants.py`
- Trigger: automatically after Workflow 4 completes match preparation.

This corresponds to the Colab external enrichment stage for observed variants.

Input:

```text
heal_fon_deliverable_presentation_audit.csv
```

Scope:

- keep only rows with `has_genotype=true`
- enrich observed rsIDs with Ensembl Variation
- enrich with Ensembl VEP consequences
- enrich with ClinVar E-utilities `esearch` and `esummary`
- enrich with MyVariant.info summaries
- use a local rsID cache so repeated audits do not re-query public APIs
- produce a technical enriched audit CSV for QA
- produce a Colab-style interpretive enriched CSV for user/AI review

The technical QA CSV keeps the broad source-level detail needed for debugging: patient allele context, curated `canon_effect`, `allele_match_summary`, generic `external_support_summary`, Ensembl mapping/phenotype/population/VEP transcript summaries, colocated variants, ClinVar details, MyVariant CADD/dbSNP/dbNSFP-derived fields, and compact raw JSON columns.

The interpretive CSV mirrors the deterministic Colab output shape and adds `Canon Effect` from the curated canon. It writes `heal_fon_interpretation_enriched_observed69.csv` with the Colab column names and ordering, including the more narrative `external_support_summary`, `myvariant_best_id`, `myvariant_best_score`, `myvariant_top_level_fields`, `Notes`, and `Interpretation (1 sentence)`. `Canon Effect` is intentionally carried forward so downstream AI/user-facing interpretation can use the canon's own biological context instead of inventing it.

The VEP transcript summary now keeps transcript-level details when Ensembl returns them: transcript ID, consequence, impact, biotype, SIFT, PolyPhen, amino-acid change, and protein positions.

Outputs:

```text
heal_observed_variant_enrichment.csv
heal_fon_interpretation_enriched_observed69.csv
observed_variant_enrichment_summary.json
```

## Why It Is Fast

The Colab prototype used dataframe operations over a local notebook. The server version keeps the same logical stages but improves the execution shape:

- Large VCF uploads are chunked at the browser/API boundary, so the server never receives the full VCF as one request body.
- The VCF validator reads sequentially by streaming, not by loading the whole file into memory.
- Canon processing and rsID coordinate resolution happen only when the canon changes, not for every VCF.
- Workflow 4 precomputes the target chromosome/position keys from the canon and then performs the targeted VCF scan once. Each VCF row is checked against indexed target keys instead of comparing every canon row against every VCF row.
- The internal preparation stage does not re-read the VCF. It consumes the small consolidated CSV produced by the match stage, so preparation is effectively immediate.
- External enrichment does not re-read the VCF either. It consumes only the observed genotype rows from the audit CSV and deduplicates rsIDs before calling public APIs.
- Repeated enrichment runs use the local rsID cache under `C:\ServerCIT\services\heal-variant-enrichment\cache`.
- n8n only orchestrates paths and process execution. Heavy parsing stays in external scripts, which avoids large binary payloads and memory pressure inside n8n.

For the current test canon, Workflow 4 produces 149 consolidated rows. The internal preparation stage works on those 149 rows, not on the full 1.5 GB VCF.

## Audit Downloads

As each stage creates an artifact:

```text
GET /api/vcf-canon-matches/:jobId/download
GET /api/vcf-canon-matches/:jobId/preparation-audit
GET /api/vcf-canon-matches/:jobId/preparation-minimal
GET /api/vcf-canon-matches/:jobId/enrichment
GET /api/vcf-canon-matches/:jobId/enrichment-interpretive
GET /api/vcf-canon-matches/:jobId/enrichment-plus
POST /api/vcf-canon-matches/:jobId/retry-enrichment
POST /api/vcf-canon-matches/:jobId/individual-interpretation
GET /api/vcf-canon-matches/:jobId/individual-interpretations
POST /api/vcf-canon-matches/:jobId/interpretation-normalization
GET /api/vcf-canon-matches/:jobId/individual-interpretations-normalized
POST /api/vcf-canon-matches/:jobId/global-interpretation
GET /api/vcf-canon-matches/:jobId/global-interpretation
GET /api/vcf-canon-matches/:jobId/global-interpretation-sections
GET /api/vcf-canon-matches/:jobId/global-interpretation-payload
GET /api/vcf-canon-matches/:jobId/global-interpretation-deterministic-summary
GET /api/vcf-canon-matches/:jobId/debug/:artifact
```

The `debug/:artifact` whitelist includes `vcf_candidates` and `vcf_joined_chr_pos`. `vcf_candidates` is the raw targeted VCF extraction; `vcf_joined_chr_pos` is the Colab-style debug table that joins those VCF hits back to rsID, source rows, categories, genes, and resolved coordinate metadata.

The match and preparation CSVs are downloadable as soon as their files exist, even if external enrichment is still running or later fails. The technical QA enrichment, Colab-style interpretive enrichment, and Enrichment Plus CSVs remain downloadable only after their own files exist. The API verifies job ownership and allowed filesystem roots before serving any artifact. Local paths are not exposed in browser JSON.

The polling response exposes `artifactsReady.matches`, `artifactsReady.preparation`, `artifactsReady.enrichment`, `artifactsReady.enrichmentInterpretive`, `artifactsReady.enrichmentPlus`, `artifactsReady.individualInterpretation`, `artifactsReady.interpretationNormalization`, and `artifactsReady.globalInterpretation` so the frontend can show each progress-bar download icon as soon as the corresponding audit/review artifact is ready.

## Enrichment Plus Output

Workflow 5 still runs as one external enrichment stage, but it now emits three levels of CSV:

```text
heal_observed_variant_enrichment.csv
heal_fon_interpretation_enriched_observed69.csv
heal_fon_interpretation_enrichment_plus.csv
```

`heal_observed_variant_enrichment.csv` remains the technical QA artifact. `heal_fon_interpretation_enriched_observed69.csv` remains the Colab-style interpretive artifact. `heal_fon_interpretation_enrichment_plus.csv` is the richer LLM-facing artifact, adding normalized ClinVar classification/evidence, population-frequency summary, selected VEP transcript/HGVS/MANE/protein fields, CADD/REVEL/AlphaMissense/SIFT/PolyPhen signals when available, GWAS Catalog associations, ClinPGx/PharmGKB clinical and variant annotations, PubMed IDs, source error flags, and compact raw JSON snippets for audit.

If the external enrichment stage fails after match preparation has already produced `heal_fon_deliverable_presentation_audit.csv`, the frontend can call `POST /api/vcf-canon-matches/:jobId/retry-enrichment`. This reuses the prepared audit CSV and reruns only Workflow 5. It does not repeat upload, integrity validation, VCF-canon matching, or match preparation.

## LLM1 Individual Variant Interpretation

The next module consumes only observed variants from the Enrichment Plus CSV. It is intentionally separate from deterministic grouping and global reporting.

```text
Input:  heal_fon_interpretation_enrichment_plus.csv
Output: individual_variant_interpretations.csv
```

Processing:

- prepares `variant_interpretation_payloads.jsonl` and `.csv`;
- passes filtered rows to the configured LLM with controlled parallelism;
- uses strict JSON Schema output;
- writes `individual_variant_interpretation_progress.json` while the stage is running;
- writes `individual_variant_interpretations.csv` incrementally as rows finish;
- writes row-level errors to `individual_variant_interpretation_errors.csv`;
- applies deterministic HEAL calibration after the LLM response, separating technical confidence from deliverable confidence;
- exposes the end-user audit CSV through `GET /api/vcf-canon-matches/:jobId/individual-interpretations`.

Runtime configuration:

```text
HEAL_OPENAI_API_KEY=<secret outside repo>
HEAL_LLM1_MODEL=gpt-5-mini
HEAL_LLM_MAX_WORKERS=3
HEAL_LLM_TIMEOUT_SECONDS=90
HEAL_LLM_ROW_ATTEMPTS=2
HEAL_INDIVIDUAL_INTERPRETATION_ROOT=C:\ServerCIT\services\heal-individual-interpretation
```

This module does not group repeated rsIDs, create system-level summaries, or produce the final family/professional report. Those remain later modules.

## Deterministic QA Normalization

This stage runs after LLM1 and before deterministic grouping or LLM2. It is intentionally separate from LLM1 so prompt/model changes and QA rule changes can be audited independently.

```text
Input:  individual_variant_interpretations.csv
Output: individual_variant_interpretations_normalized.csv
```

Runtime service:

```text
C:\ServerCIT\services\heal-interpretation-normalization
```

Repository source copy:

```text
services/heal-interpretation-normalization
```

Key script:

```text
normalize_individual_interpretations.py
```

Generalized rules:

- normalize repeated `gene + rsID + observed genotype + zygosity + REF/ALT` groups so the core genetic confidence stays stable across modules;
- cap high-confidence synonymous/non-diploid/context-limited rows to `Moderate` when evidence is benign/common/limited and no stronger support is present;
- raise selected non-coding `Low` rows to `Moderate` only when the row has strong, explicit biomarker-style association evidence such as serum IgE, plasma/serum metabolites, or enzyme activity;
- shorten overlong one-sentence Spanish/English summaries while preserving original text in `original_interpretation_one_sentence_*`;
- add `original_final_confidence_level`, `normalization_status`, `normalization_reason`, `duplicate_group_size`, `qa_warnings`, and `normalized_at` columns for audit.

The current smoke test against the 69-row LLM1 output adjusted or flagged 9 rows: COMT duplicate consistency, DRD2 duplicate/locus ambiguity, MAOA confidence cap, FCER1A biomarker association upgrade, and two long Spanish sentence shortenings. It does not use gene-specific or rsID-specific override lists.

## LLM2 Global Interpretation

This module consumes the normalized LLM1 output and creates a structured global synthesis. It does not reinterpret variants individually and does not change per-variant `final_confidence_level` values.

```text
Input:  individual_variant_interpretations_normalized.csv
Output: global_interpretation.json
        global_interpretation_sections.csv
        global_interpretation_payload.json
        deterministic_summary.json
```

Runtime service:

```text
C:\ServerCIT\services\heal-global-interpretation
```

Repository source copy:

```text
services/heal-global-interpretation
```

Key script:

```text
interpret_global_profile.py
```

Before calling the LLM, the backend builds a deterministic summary containing observed variant count, unique rsID count, unique gene count, confidence distribution, repeated rsIDs across categories, genes with multiple distinct variants, category/axis groupings, conflicting variants, professional-review variants, and gene/locus ambiguities.

Model selection is dynamic and applies only to LLM2:

```text
Analisis superficial -> gpt-5-mini
Analisis completo    -> gpt-5.2
Control de Calidad   -> selectable: gpt-5-mini, gpt-5, gpt-5.1, or gpt-5.2
```

Frontend QA/debug mode also exposes LLM2 language and audience options:

```text
language_mode: es | en | both
audience_mode: technical | health_professional | family | all
```

For ordinary quick/full runs, language follows the page language and audience defaults to `family` for quick analysis and `all` for full analysis.

## Colab Output Boundary

The Colab code reaches these deterministic outputs:

```text
sheet_final_consolidated.csv
heal_fon_deliverable_presentation_min.csv
heal_fon_deliverable_presentation_audit.csv
heal_fon_interpretation_enriched_observed69.csv
```

The notebook then documents that the final interpretation of the 69 observed variants was done as a downstream manual/AI-assisted step, not as a fully coded notebook output. The next workflow should therefore be treated as a new controlled interpretation/report workflow, using the enriched observed rows and the full 149-row deliverable table as inputs.

## Operational Notes

- `HEAL - Match Preparation (superseded inactive)` was superseded by the internal preparation stage in `HEAL - VCF Canon Match`; its old public webhook is no longer registered.
- The official n8n safe restart was used on 2026-06-01 to register production webhook updates.
- The workflow imports did not preserve `parentFolderId`; no direct SQLite folder update was performed for Workflow 4 or the inactive superseded Workflow 5.
- `HEAL - Variant Enrichment` is active and registered. The import CLI did not preserve `parentFolderId`, so it is not visually assigned to the `Heal by FON` folder until a specific folder maintenance update is authorized.
- The latest verified n8n backup made during restart was `C:\n8n-backups\daily\20260601-194608`.
