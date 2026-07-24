# LLM1 v3 readiness

## Current decision

Production LLM1 is blocked for `gene_module_v2`. The supported output is a
deterministic dry-run payload plus an auditable readiness package.

## Layer contract

1. Genetic facts: normalized observed allele, genotype, quality, assembly and
   identity class. Sparse VCF absence does not imply hom-ref or callability.
2. Scientific evidence: transcript-aware VEP plus source-specific statuses for
   ClinVar, Ensembl Variation, MyVariant, GWAS and PharmGKB.
3. Curated mechanisms: versioned gene-module records approved by a professional.
4. Interpretation: downstream only, after all readiness gates pass.

## Post-enrichment readiness

Initial AI triage remains an enrichment eligibility filter because transcript
evidence does not exist until VEP completes. The readiness audit and grouped
preparation apply the transcript-aware decision after enrichment:

- local UTR plus target-transcript intron is not focus eligible;
- SpliceAI uses maximum `DS_*`, not JSON presence;
- source errors remain distinct from `not_found`;
- unresolved identity cannot promote secondary evidence;
- GWAS or PharmGKB record presence does not add ranking points by itself.

## Payload

`llm1_group_payload_v3` contains:

- `group_context`
- `canonical_status`
- `genetic_facts`
- `scientific_evidence`
- `curated_mechanisms`
- `deterministic_summary`
- `focus_variants`
- `context_variants`
- `unresolved_and_failed`
- `provenance`

Groups include at most 20 focus variants. Complete detail stays in the variant
detail artifact. The API stops before interpretation unless
`groupPayloadReady=pass`, even if the environment feature flag is enabled.

## Gates

- `extraction_contract_ready`
- `annotation_ready`
- `group_payload_ready`
- `llm1_pilot_ready`

The final gate also requires documented bioinformatician approval.
