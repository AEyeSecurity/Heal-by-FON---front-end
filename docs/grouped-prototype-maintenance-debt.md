# Grouped prototype maintenance debt

Updated: 2026-08-26

## Current contract boundaries

- `llm1_prototype_envelope_v3` is the writable prototype contract. Readers for v1 and v2 remain required for historical audits and replay.
- The signed sandbox snapshot covers 105 of 180 canonical groups. Runtime configuration examples must point to `tier1-105-human-signed-20260823`.
- `tier1_human_review_foundation_v1` still requires exactly 12 groups. This is a frozen pre-prototype release gate, not the current coverage definition. Do not generalize or remove it without a versioned replacement and migration tests.
- The legacy grouped LLM1 validator and the sandbox grouped validator intentionally differ. The sandbox adds clause-level polarity, a signed scientific allowlist and a runtime ceiling. Sharing those behaviors with the legacy benchmark would change historical semantics.

## Deferred refactors

| Area | Why it remains | Risk | Safe removal or migration condition |
|---|---|---|---|
| Legacy v1/v2 envelopes and schemas | Historical replays and audit packages still read them. | Low maintenance overhead; high audit risk if removed. | Inventory all archived artifacts and provide a tested immutable converter to v3. |
| `tier1_human_review_foundation_v1` 12-group gate | It protects the original internal-auto lane and is not used as the 105-group sandbox coverage source. | Misreading it as current coverage. | Introduce a new foundation schema with explicit dynamic counts, then migrate callers. |
| Legacy LLM1 semantic patterns | The benchmark used direct regex matching; the sandbox uses negation-aware clause polarity. | Consolidating them would change benchmark results. | Create a separately approved validator version and replay the benchmark. |
| Repeated small Python I/O/hash helpers | They are local, stable and have different serialization details. | Minor duplication. | Standardize encoding, newline, JSON canonicalization and error policy across services first. |
| Large `server/dev-api.js` and `src/main.jsx` entrypoints | Splitting orchestration while a real job exists would increase regression surface. | Ongoing review and testability cost. | Extract one stateless boundary at a time behind behavior tests; never move job state during a maintenance-only change. |
| Source-inspection recovery tests | Importing the API entrypoint starts runtime behavior, so recovery invariants are currently checked structurally. | Refactors can require test updates without behavior changes. | Move recovery state transitions into a pure module and add state-machine tests before replacing structural assertions. |

## Dead code and markers audit

- No actionable `TODO`, `FIXME`, `HACK` or `XXX` markers exist in tracked application code as of this update. The only `XXX` match is part of a dependency integrity hash.
- No runtime function is removed in this maintenance pass. A function is not considered dead solely because static search cannot see dynamic imports, CLI entrypoints or audit tooling.
- Historical campaign packaging tools are retained because reproducibility and signed-audit lineage take precedence over repository size.

## Ownership and review trigger

The HEAL engineering owner should revisit this inventory after the active real job has completed and before promoting the grouped prototype beyond its current internal status. Any change to scientific allowlists, inference ceilings, registry selection or historical readers requires its own compatibility review rather than being bundled into general cleanup.
