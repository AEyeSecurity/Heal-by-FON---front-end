# Global enrichment cache and English presentation

## Global cache v3

`enrichment_cache_v3.sqlite` is a shared cache for public, successful
enrichment queries. Its key is limited to normalized variant identity,
assembly, source, query mode, the exact request fingerprint, and the public
query-contract version. It deliberately excludes VCF, upload, job, user,
patient, sample, and file fingerprint.

Only `success` rows are reusable. `not_found`, timeouts, provider errors, and
other failures are recorded in the attempts ledger, never overwrite a success,
and always result in a new provider request next time. Successes have no TTL;
a future refresh policy must be introduced separately by changing the query
contract or by an explicit maintenance task.

The previous enrichment and evidence-refinement databases remain read-only
audit stores. A v2 entry is copied into the new cache only when its exact
request fingerprint, URL (for evidence refinement), identity, and `success`
status are compatible.

## English presentation

Spanish remains the canonical report and audit source. English cards use the
English text already emitted by LLM1, plus deterministic English coverage
templates. A report translation is a separate, protected presentation sidecar:
OpenAI receives only an explicit list of visible Spanish prose fields and must
return the same paths in English. Genes, variants, evidence IDs, counts,
modules, ceilings, gates, and validation state never enter the translation
surface.

Generation is explicit for historical jobs through the protected
`/grouped-prototype/presentation` endpoint and requires both the job access
token and curation access. `GET` routes never call OpenAI. There is one
technical retry; after a failure English is marked `unavailable`, and Spanish
is never mislabeled as English.

Enable a new English presentation only after deployment by setting
`HEAL_PROTOTYPE_TRANSLATION_ENABLED=true` and, optionally,
`HEAL_PROTOTYPE_TRANSLATION_MODEL=gpt-5.6-luna`. This change does not alter
LLM1, LLM2, prompts, the signed snapshot, or the active registry.
