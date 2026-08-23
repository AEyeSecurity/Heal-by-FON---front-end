# Tier 1 mechanism recuration v2 — implementation status

## Root cause corrected

The previous 99% `withheld` result did not reflect a scientific evaluation. The v1 preparation script retained four existing decisions and converted 101 unresolved drafts to `withheld`. The fallback has been removed: an unevaluated draft now blocks that legacy command and directs the workflow to curation v2.

## Implemented

- Closed evidence-packet and curation-decision schemas.
- VCF-independent packet builder for all 105 Tier 1 groups.
- PubMed and Europe PMC discovery up to `2026-07-28`.
- Versioned snapshots of NCBI Gene, UniProt and Reactome.
- Complete ledger, source classification candidates, deduplication, cutoff exclusion and deterministic selection of at most 20 sources.
- Separation of `core_status`, `context_usable` and `inference_ceiling`.
- Deterministic approval, rejection, citation, independence, conflict and direction validators.
- Independent Sol curator/critic calls at `high` and conditional arbiter at `xhigh`.
- Generic prompt optimization with a fixed five-version maximum and repeated-failure pause.
- Fixed 12-group gold with frozen calibration and holdout split.
- Full-run orchestration for 210 base calls plus adjudications, resumable per role and group.
- Cross-group auditor that produces flags without mutating decisions.
- Manual-review sampling and publication guard.
- Token-protected internal API and dashboard. The dashboard clearly states that it creates candidates and does not alter the active registry.

## Generated artifacts

Snapshot `tier1-v2-20260805-r2` contains:

- 12/12 gold evidence packets;
- 20 selected sources per group, with all overflow retained in the ledger;
- 12/12 NCBI Gene snapshots;
- 11/12 UniProt and Reactome snapshots;
- one retained UniProt technical failure for FADS1, without blocking other evidence;
- one CYCS publication after cutoff retained as excluded and unavailable to the model;
- pending gold manifest and reviewer cards.

No GPT-5.6 Sol curation call has been made because the gold requires internal human approval first. No Luna call has been made. The active mechanism registry was not modified.

## Next manual gate

The responsible internal reviewer must complete and approve:

- `gold/gold_manifest.json`: accepted status alternatives, context usability, inference ceiling, valid/invalid evidence, directions, limitations, reviewer and date;
- `gold/gold_review_cards.json`: evidence summary used to make those decisions.

Only after `validate-gold` returns zero errors should candidate 1 consume Sol calls. Evidence collection for the other 93 groups can run independently or immediately after gold approval; it does not depend on either VCF.

