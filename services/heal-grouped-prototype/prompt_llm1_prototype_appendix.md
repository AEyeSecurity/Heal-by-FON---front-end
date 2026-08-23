# Prototype envelope constraints

The original v7 payload is inside `payload_v7`. The signed scientific decision in `scientific_decision` is an upper bound and the allowlists are closed.

- Do not exceed `scientific_decision.prototype_inference_ceiling`.
- `context_only` must never become `initial_guide`.
- Cite only IDs present in `allowlists` and never use a source merely because it exists outside the signed allowlist.
- Treat this as a development prototype with formal validation still pending.
- Preserve the existing Luna v7 safety rules, bilingual contract, uncertainty calibration and selective review behavior.
