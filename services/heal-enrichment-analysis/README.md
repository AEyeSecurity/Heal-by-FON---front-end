# HEAL v2 Enrichment Analysis

Offline, read-only analysis of a preserved v2 enrichment cache. The analyzer
does not call external APIs and does not modify the cache or production run
artifacts.

```powershell
python services/heal-enrichment-analysis/analyze_v2_enrichment.py `
  --cache F:\Heal by FON\backups\<snapshot>\enrichment-cache-final\enrichment_cache.sqlite `
  --triage F:\Heal by FON\data\runs\<job>\ai-triage\heal_fon_ai_triage.csv `
  --matching F:\Heal by FON\data\runs\<job>\matching\sheet_final_consolidated.csv `
  --progress F:\Heal by FON\data\runs\<job>\enrichment\enrichment_progress.json `
  --output-dir F:\Heal by FON\backups\<snapshot>\analysis
```

Outputs include source completeness, variant evidence matrix, identity audit,
cross-source conflicts, deterministic sample review, group quality summary,
JSON summary and a Markdown report.
