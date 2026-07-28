# HEAL LLM1 payload v5

Builds bounded, citation-addressable `gene + module` payloads from existing v2
curation artifacts. It does not repeat VCF normalization, matching, triage or
external enrichment and never calls an LLM.

The complete evidence remains in `group_evidence_packets.jsonl.gz`. The payload
contains deterministic selections and summaries with a hard 25,000-token gate.
Every source record is reconciled in `group_evidence_coverage_audit.csv` as full,
digest, deterministic summary, reference-only or excluded from the prompt with
a reason. Exclusion from a prompt never removes evidence from HEAL.

The optional `heal-evidence-digest` service may compress only public assertion
descriptions and publication abstracts. It is disabled by default, uses a
separately configured model and falls back per group to reference-only evidence.
LLM1 execution still requires a professionally approved pilot manifest.
