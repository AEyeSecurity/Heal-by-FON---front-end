# HEAL Runtime Map

This is the operational source of truth for HEAL by FON after the `F:` migration. It is paired with `F:\Heal by FON\config\runtime-manifest.json`, generated at deploy time. Neither file contains secret values.

## Boundary

HEAL owns only `F:\Heal by FON`. The project must not write to `C:` or `D:` after cutover. The following remain shared system dependencies and are intentionally not relocated or restarted by HEAL operations:

- Docker Desktop and its image store. HEAL only inspects/builds `heal-vcf-normalizer:1.0.0`.
- n8n application/database. Only the two HEAL legacy workflow definitions are updated; n8n itself is not restarted.
- Node.js, Python and `cloudflared` executables.
- Windows Task Scheduler metadata. Only `HEAL VCF API` and `Cloudflared HEAL API` point to HEAL scripts.

## Filesystem

| Logical component | Code | Data/artifacts | Configuration | Start and health | Retention | Support |
| --- | --- | --- | --- | --- | --- | --- |
| Frontend/API | `F:\Heal by FON\app\server`, `app\src` | `data\uploads`, `data\jobs`, `data\runs` | `config\heal-vcf-api.env` | Task `HEAL VCF API` -> `ops\Start-HealApi.ps1`; `GET /api/health` | uploads 24h; jobs/runs 14d | legacy and v2 |
| Canon intake | `app\services\heal-canon-intake` | `data\canon\incoming`, `runs`, `current` | `HEAL_CANON_ROOT` | API child process; active canon reported by health | active canon protected | legacy and v2 |
| Legacy rsID | `app\services\heal-rsid-resolution`, legacy matcher/enricher | `data\legacy-rsid`, `data\runs` | `HEAL_RSID_RESOLUTION_ROOT` | HEAL-only n8n workflows or API fallback | active resolution protected | legacy only |
| V2 normalization/match/enrichment | `app\services\heal-vcf-normalization`, `heal-vcf-canon-match`, `heal-variant-enrichment` | `data\runs\<job-id>\<stage>`, `data\enrichment-cache`, `data\references\GRCh38` | `HEAL_RUN_ROOT`, `HEAL_REFERENCE_DATA_ROOT` | API child processes; health checks reference and normalizer image | VCF scratch 24h; audit 14d | v2; LLM1 blocked |
| API tunnel | `ops\Start-HealCloudflared.ps1` | none | `config\cloudflared-token.txt` | Task `Cloudflared HEAL API`; metrics `127.0.0.1:20243` | log rotation | all schemas |

All v2 stages use the same run tree:

```text
F:\Heal by FON\data\runs\<job-id>\
  normalization\
  matching\
  preparation\
  ai-triage\
  enrichment\
```

`normalization_input.vcf` is no longer created. The normalizer filters supported contigs inside the Docker container and writes its artifacts directly under the job stage.

## Operations

| Operation | Command | Scope |
| --- | --- | --- |
| Deploy code/dependencies | `F:\Heal by FON\ops\Deploy-HealApp.ps1` | F only, plus a HEAL Docker image check |
| Health | `F:\Heal by FON\ops\Test-HealRuntime.ps1 -RequireApi` | Read-only except Docker inspect |
| Retention preview | `F:\Heal by FON\ops\Cleanup-HealData.ps1` | F data only, no deletion |
| Retention execution | `F:\Heal by FON\ops\Cleanup-HealData.ps1 -Apply` | Expired uploads/runs/jobs only |
| Task cutover | `F:\Heal by FON\ops\Set-HealScheduledTasks.ps1 -Restart` | Exactly two named HEAL tasks |
| Disable legacy Startup entries | `F:\Heal by FON\ops\Disable-LegacyHealStartup.ps1` | Exactly two HEAL `.cmd` entries, copied to archive first |
| Rollback within freeze period | `F:\Heal by FON\ops\Rollback-HealScheduledTasks.ps1 -Restart` | Exactly two named HEAL tasks |

## Migration State

The staged migration copies data to `F:` and leaves the source directories in `C:`/`D:` intact for seven days. During that period they are rollback-only and no new HEAL writes are permitted there. Do not remove any source directory until the seven-day validation is complete and explicit approval is recorded. The migration ledger is `F:\Heal by FON\archive\migration-ledger.json`.

Historical documents that name `C:\ServerCIT` or `D:\ServerCIT` describe the pre-migration runtime and are not operating instructions.
