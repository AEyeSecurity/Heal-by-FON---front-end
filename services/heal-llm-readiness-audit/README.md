# HEAL LLM Readiness Audit v2

Read-only audit for a completed `gene_module_v2` run. It reconciles pipeline cardinalities, profiles annotation completeness, classifies physical variants, selects a deterministic 50-variant review sample, and emits explicit pre-LLM gates.

The service never calls an LLM and never mutates run artifacts or enrichment caches.

```powershell
python audit_llm_readiness_v2.py `
  --run-dir "F:\Heal by FON\data\runs\<run-id>" `
  --canon-clean "F:\Heal by FON\data\canon\runs\<canon-run-id>\heal-canon-v2-clean-rows.csv" `
  --output-dir "F:\Heal by FON\backups\qa-audit\<run-id>\llm-readiness"
```

The canonical status artifact intentionally reports `callable=unknown` and `hom_ref=unknown` for absent sites in a sparse VCF. CNV and VNTR remain `not_assessed` until dedicated evidence sources are supported.
