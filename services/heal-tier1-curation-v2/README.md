# Tier 1 mechanism curation v2

This service replaces the invalid `draft -> withheld` fallback with evidence-backed, VCF-independent scientific curation for the 105 Tier 1 gene-module groups.

It is candidate-only by design. Evidence collection, gold preparation, prompt calibration, the full double pass, and manual review never alter the active mechanism registry. Publication requires the separate `publish` command, a complete review manifest, a valid approval artifact, an exact confirmation string, and a new destination.

## Fixed scientific contract

- Publication cutoff: `2026-07-28`.
- Database snapshots: NCBI Gene, UniProt and Reactome at retrieval time, with URL, retrieval timestamp and response hash.
- Model: `gpt-5.6-sol` through Responses API.
- Curator and critic: `reasoning.effort=high`.
- Arbiter and prompt optimizer: `reasoning.effort=xhigh`.
- Strict JSON Schema, `store=false`, one technical retry, no semantic retry.
- Reviews are discovery-only. Database records alone cannot approve.
- Contextual approval is separate from `initial_guide_candidate`.
- Absence of evidence is `withheld`; `rejected` requires positive evidence.

The model configuration follows the [official GPT-5.6 migration guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6#migrate-to-gpt-56): explicit reasoning effort, Responses API, structured outputs, and representative evals before migration.

## Workflow

1. `build_evidence_packets.py` creates immutable packets and a complete ledger. At most 20 eligible sources are selected for a model call; excluded and overflow results remain traceable.
2. `run_curation_v2.py prepare-gold` creates the fixed 12-card gold template and a reviewer-friendly evidence artifact.
3. The internal owner fills and approves state, evidence, direction, limitations and ceiling for all 12 cards.
4. `calibrate` runs only the six calibration groups. It never reads or executes holdout.
5. A failed calibration candidate can be generalized with `optimize`; the optimizer rejects any non-calibration report. Five candidates are allowed, and three repeated dominant failures pause the loop.
6. `freeze-prompt` requires passing calibration and freezes prompt, schema, Gold, evidence and hashes before holdout.
7. `evaluate-holdout` executes the six holdout groups once. A failure is labeled `holdout_failed_requires_new_unseen_holdout`; those cases cannot be reused as unseen holdout after tuning.
8. The 105-group run, review and publication remain separate future stages and are not authorized by the 12-group evaluation.

## Semantic verification v1

`run_semantic_verification_v1.py` is the restricted replacement for prompt
iteration after run6. It deliberately exposes only `historical-regression` and
`run-protocol`; it cannot optimize prompts, publish, run Luna, process VCFs, or
curate the 105 groups.

Sol now returns `mechanism_curation_semantic_v1`: scientific status, identity,
module relevance, per-source interpretation, limitations, and confidence. The
backend converts that assessment into the unchanged
`mechanism_curation_v2_decision` contract and is the sole owner of weighted
scores, direction, margins, conflict flags, context usability, inference
ceiling, grouped evidence IDs, and expert-review provenance.

The verification campaign is fixed to:

- candidate name `semantic-v1-verification-1` with candidate-3 as semantic provenance;
- six calibration groups followed by at most one frozen holdout execution;
- no prompt optimizer or semantic retry;
- `high` curator/critic and `xhigh` arbiter;
- at most two arbiters per phase;
- USD 10 per phase and USD 15 for the complete campaign;
- 600-second timeout and no automatic retry after an ambiguous timeout.

Run5, run6, the signed Gold, and the active registry are hashed outside their
directories before execution and verified again at termination.

## Signed reconciled Gold v4

The PubMed-reconciled foundation and frozen Gold are under:

`F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\reconciliation-v4`

The evidence manifest contains 12/12 technically valid packets, an authoritative
PubMed snapshot of 1,320 unique PMID, and a zero-error PMID/title/DOI QA report.
All twelve decisions were signed by Martina Liz Ceballos on 2026-08-14 and the
signature was inherited only as `technical_metadata_only`. `validate-gold`
returned zero errors. No Sol or Luna call was triggered by reconciliation.

The packets were regenerated with:

```powershell
python services\heal-tier1-curation-v2\build_evidence_packets.py `
  --review-proposal-csv "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\source\fichas_gold_humanas_COMPLETADO_PROPUESTAS_2026-08-10.csv" `
  --cache-dir "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\evidence-cache" `
  --output-dir "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\reconciliation-v3\evidence"
```

The next gate is Sol calibration using the secure `HEAL_OPENAI_API_KEY`. A
successful calibration must be frozen before the single holdout execution.

## Safety gates

- A draft is never auto-classified.
- Out-of-cutoff publications remain in the ledger as excluded and cannot be selected or cited.
- Same-cohort/derived publications do not count as independent replication.
- Every citation must belong to the packet and selected window.
- Directional scores are recomputed deterministically using `30/25/20/15/10` weights.
- A material conflict with margin below 15 cannot approve.
- The active mechanism registry and Luna runtime remain unchanged until separate publication and downstream preflight.
