# LLM1 v7 internal production candidate

Evidence cutoff: `2026-07-28`.

## Runtime gates

- `HEAL_V2_LLM1_ENABLED=true`
- `HEAL_LLM1_EXECUTION_MODE=internal_auto`
- `HEAL_LLM1_MODEL=gpt-5.6-luna`
- `HEAL_LLM1_PROMPT_PROFILE=luna_v7`
- `HEAL_LLM1_ACTIVE_TIERS=T1`
- `HEAL_LLM1_ACTIVE_AGE_BANDS=age_0_7`
- `HEAL_LLM1_EXPERIMENTAL_CANARIES=IFNG:T3.5`
- `HEAL_TIER1_HUMAN_REVIEW_FOUNDATION` (defaults to the local immutable
  foundation when unset)

These flags are necessary but insufficient. The builder also requires 180 registered groups, registry concordance, complete Tier 1 curation, a hash-valid and released human-review foundation, safe sparse/callability semantics, reconciled evidence, transcript identity, source and token gates. Until all gates pass, no automatic Luna call is made.

## Curation workflow

`tools/prepare_llm1_tier1_curation.py` creates a non-activating candidate. A
`draft` record must remain `draft` until it has actually been assessed; it must
never be converted mechanically to `withheld`. `withheld` means that an
evidence review was performed and still found insufficient support. Unreviewed
GWAS rows become `valid_but_excluded` only when exact-allele replication is
present; otherwise they become `rejected`.

For the twelve reviewed gold groups, the canonical working source is the
immutable human-review foundation under
`data/curation-candidates/tier1-human-review-20260810/foundation-v1`.
It records the human proposal separately from technical packet blockers and
cannot activate the runtime. The historical 2026-08-05 registry and the old
`1 approved / 104 withheld` candidate are retained only as audit history; they
are not evidence that the other mappings were scientifically reviewed.

Candidate files must be reviewed and uploaded through the authenticated internal scientific curation endpoints. Uploading regenerates v7 and recalculates all gates. The application records final per-run approval through `POST /api/vcf-canon-matches/:jobId/llm1-internal-review`.

Before any upload, the foundation validation report must have no unresolved
packet/evidence flags, every proposed source must be present in the refreshed
ledger, the active selection must exclude sources the reviewer marked invalid,
publication lineages must be deduplicated, and a named human reviewer must
replace the pending-signature marker. A twelve-group foundation is not a
complete 105-group Tier 1 registry, so it cannot satisfy the internal-auto
gate by itself.

## Output behavior

The client-simulation interface shows Tier 1 interpretations, explicit coverage-only cards, and an experimental canary appendix. Each card has a readable summary and expandable evidence, variants, gates, conflicts, provenance, and allowlists. ES/EN is a presentation switch over the same validated output.

Critical semantic errors quarantine only the affected group. The run cannot receive initial internal approval while a required visible group is missing, quarantined, technically failed, or still running.
