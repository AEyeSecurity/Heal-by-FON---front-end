# Tier 1 human-review foundation

This is the single working source for the reviewed twelve-group gold set. It
keeps the original human-readable package, the completed proposal, and the
machine-verifiable reconciliation result together without changing the active
curation registry or enabling Luna.

## Location

`F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810`

- `source/` preserves the supplied review package and handoff files.
- `foundation-v3/source_manifest.json` hashes the source files used.
- `foundation-v3/tier1_human_review_foundation.json` contains the twelve
  proposed scientific decisions, limits, source allowlists, and blockers.
- `foundation-v3/validation_report.json` records that all technical checks pass
  and all twelve signatures remain pending.
- `foundation-v3/human_signoff_template.csv` is the controlled signature input.
- `foundation-v3/runtime_import_preview.csv` is an inspection-only mapping to
  the mechanism-registry columns. It must not be uploaded while
  `release_ready=false`.

## Safety model

The proposal is not a signed gold release. Every group is kept
`release_ready=false` until both conditions are met:

1. source records are regenerated/reconciled against the reviewer allowlist;
2. a named final human reviewer signs the decision.

The active runtime stays disabled. The current builder also requires all 105
Tier 1 groups to be classified before internal-auto LLM1 can run, so these
twelve groups alone cannot bypass the gate.

## Regeneration command

Run from `F:\Heal by FON\app` only when creating a new, empty foundation
directory:

```powershell
python services\heal-tier1-curation-v2\build_human_review_foundation.py `
  --package-dir "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\source" `
  --proposal-csv "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\source\fichas_gold_humanas_COMPLETADO_PROPUESTAS_2026-08-10.csv" `
  --evidence-manifest "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\reconciliation-v3\evidence\evidence_packet_manifest.json" `
  --output-dir "F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\foundation-v3"
```

The command refuses to overwrite an existing foundation. Do not hand-edit a
generated JSON. Human decisions are entered in the CSV/XLSX review package.
`compile_signed_gold.py` verifies the source manifest, refreshed evidence
hashes, all twelve decisions and the explicit confirmation before producing a
new immutable gold candidate. It cannot update the active registry.

## P0 controls now enforced

- NCBI gene identity requires an exact official symbol; aliases cannot finalize
  identity, preventing PEMT from resolving to MUC1/GeneID 4582.
- Scientific status is separated from packet technical flags.
- Reviewer-marked invalid sources, reviewer-required sources missing from the
  ledger, sources after the cutoff, and duplicate selected publication titles
  are explicit blockers.
- The foundation preserves `approved_with_conflict` and the proposed inference
  ceiling, but does not treat either as an LLM runtime permission.
- A reviewer-valid source is pinned into the model window only when it also
  passes identity, cutoff, source-type and lineage-deduplication checks.
- Reviewer-invalid sources remain in the ledger for audit but cannot be
  selected or cited.
