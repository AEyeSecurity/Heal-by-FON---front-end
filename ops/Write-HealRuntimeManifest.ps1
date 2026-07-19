[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $healHome "app"
$configRoot = Join-Path $healHome "config"
New-Item -ItemType Directory -Force -Path $configRoot | Out-Null
$sha = (& git -C $appRoot rev-parse HEAD 2>$null).Trim()
if ($LASTEXITCODE -ne 0) { $sha = "unknown" }

$manifest = [ordered]@{
    schemaVersion = "heal-runtime-manifest/v1"
    generatedAt = [DateTime]::UtcNow.ToString("o")
    deploymentSha = $sha
    home = $healHome
    components = @(
        [ordered]@{ logicalComponent = "frontend-and-api"; code = "app/server/dev-api.js and app/src"; data = "data/uploads, data/jobs, data/runs"; config = "config/heal-vcf-api.env"; start = "Scheduled Task: HEAL VCF API -> ops/Start-HealApi.ps1"; health = "GET http://127.0.0.1:8787/api/health"; logs = "logs/api"; retention = "uploads 24h, jobs/runs 14d"; support = "legacy-rsid and gene_module_v2" },
        [ordered]@{ logicalComponent = "canon-intake"; code = "app/services/heal-canon-intake"; data = "data/canon/incoming, data/canon/runs, data/canon/current"; config = "HEAL_CANON_ROOT"; start = "API child process"; health = "active canon in API health"; logs = "logs/api"; retention = "active canon protected"; support = "legacy-rsid and gene_module_v2" },
        [ordered]@{ logicalComponent = "vcf-v2"; code = "app/services/heal-vcf-normalization, heal-vcf-canon-match, heal-variant-enrichment"; data = "data/runs/<job-id>/<stage>, data/enrichment-cache, data/references/GRCh38"; config = "HEAL_REFERENCE_DATA_ROOT, HEAL_RUN_ROOT"; start = "API child processes"; health = "normalizer image/reference checks in API health"; logs = "logs/api"; retention = "scratch 24h, audit 14d"; support = "gene_module_v2; LLM1 blocked" },
        [ordered]@{ logicalComponent = "legacy-rsid"; code = "app/services/heal-rsid-resolution and legacy matcher/enrichment"; data = "data/legacy-rsid and data/runs"; config = "HEAL_RSID_RESOLUTION_ROOT"; start = "API local fallback during migration; two HEAL-only n8n workflows after authenticated path update"; health = "legacy canon/match smoke test"; logs = "logs/api"; retention = "active resolution protected"; support = "legacy-rsid" },
        [ordered]@{ logicalComponent = "public-tunnel"; code = "shared cloudflared executable"; data = "none"; config = "config/cloudflared-token.txt"; start = "Scheduled Task: Cloudflared HEAL API -> ops/Start-HealCloudflared.ps1"; health = "local cloudflared metrics on 127.0.0.1:20243"; logs = "logs/cloudflared"; retention = "rotated logs"; support = "all API schemas" }
    )
    sharedDependencies = @("Docker Desktop image store", "n8n shared application/database (historical HEAL webhooks bypassed during freeze)", "Node.js", "Python", "cloudflared")
    cleanup = [ordered]@{ script = "ops/Cleanup-HealData.ps1"; defaultMode = "dry-run"; uploadsHours = 24; auditDays = 14; protected = @("canon", "references", "enrichment-cache", "backups") }
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $configRoot "runtime-manifest.json") -Encoding utf8
