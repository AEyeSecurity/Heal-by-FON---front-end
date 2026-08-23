# Tier 1 twelve-group reconciliation — 2026-08-14

## Outcome

The twelve signed gold cards were reconciled against an authoritative PubMed
snapshot. All twelve pass the deterministic technical and signature gates.

- Evidence packets complete: 12/12.
- Technical blocking groups: 0.
- Human signatures reconciled: 12/12 (`technical_metadata_only`).
- Unique PMID verified: 1,320.
- PMID/title/DOI QA errors after repair: 0.
- `validate-gold` errors: 0.
- Sol curation calls executed: 0.
- Luna runtime calls or activation: 0.
- Active registry changes: 0.

## Immutable inputs and outputs

- Evidence manifest:
  `F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\reconciliation-v3\evidence\evidence_packet_manifest.json`
- Evidence-manifest SHA-256:
  `e2bd6f24b3a28f2b1a5c5857fb8901563466dd4645b9b96932738a57a31e7609`
- Reconciled foundation:
  `F:\Heal by FON\data\curation-candidates\tier1-human-review-20260810\foundation-v3`
- Foundation snapshot SHA-256:
  `d4401047137c33c8638b282c3c07449f33e7885facd94fba01790f500f0fb40c`

PEMT is resolved to the exact official NCBI identity `GeneID 10400`.
`GeneID 4582` is retained only as invalid audit evidence and is not selected.

## Completed work

Martina Liz Ceballos approved all twelve scientific decisions on 2026-08-14.
The source CSV/XLSX hashes were preserved. DOI and packet-hash changes were
reconciled without changing PMID, normalized title or evidence sets.

The signed CSV is accepted only by `compile_signed_gold.py` with the exact
confirmation `APPROVE_TIER1_GOLD_12`. Successful compilation creates a new
gold candidate; it does not publish `mechanism_registry_v1.csv`.

## Current scientific limitations

The signed limitations remain unchanged. This is internal scientific curation,
not independent clinical or bioinformatic validation, and no individual-level
conclusion may exceed the signed inference ceiling.

## Remaining pre-Sol gates

The Gold is ready. Sol requires a securely configured `HEAL_OPENAI_API_KEY`.
Calibration may use only its six development groups; the prompt must then be
frozen before the six holdout groups are executed once.
