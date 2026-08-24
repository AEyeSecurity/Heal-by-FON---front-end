# Prototype envelope constraints

The original v7 payload is inside `payload_v7`. The human-signed scientific decision in `scientific_decision` is authoritative for prototype curation and its effective runtime ceiling is an upper bound. The allowlists are closed.

- The legacy `payload_v7.curated_mechanism` and legacy curation-ready flags may be stale. Use only `scientific_decision` for the mechanism and curation status; do not alter any genetic, genotype, VCF, identity, or runtime evidence fact from `payload_v7`.
- Do not exceed `scientific_decision.effective_runtime_ceiling`.
- `context_only` must never become `initial_guide`.
- `initial_guide_candidate` is only a maximum. Produce `initial_guide` only when `runtime_variant_gate.eligible=true` and limit variant-specific claims to the variant and evidence IDs listed by that gate.
- Cite only IDs present in `allowlists` and never use a source merely because it exists outside the signed allowlist.
- Treat this as a development prototype with formal validation still pending.
- Preserve the existing Luna v7 safety rules, bilingual contract, uncertainty calibration and selective review behavior.
