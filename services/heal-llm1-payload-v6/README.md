# HEAL LLM1 payload v6

Builds transcript-aware, allele-specific dry-run payloads from the existing v4
group detail and curated evidence artifacts. It does not call external APIs or
an LLM and does not modify v5 artifacts.

The service also seeds persistent professional curation registries when they do
not exist. Approved registry content is copied into each payload through its
provenance and gate metadata.
