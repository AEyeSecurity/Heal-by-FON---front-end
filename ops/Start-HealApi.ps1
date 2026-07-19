[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $healHome "app"
$configRoot = Join-Path $healHome "config"
$dataRoot = Join-Path $healHome "data"
$logRoot = Join-Path $healHome "logs"
$envFile = Join-Path $configRoot "heal-vcf-api.env"

function Import-HealEnvironment([string]$Path) {
    if (!(Test-Path -LiteralPath $Path)) {
        throw "HEAL API configuration was not found at $Path."
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (!$trimmed -or $trimmed.StartsWith("#") -or $trimmed.StartsWith(";")) { continue }
        $pair = $trimmed.Split("=", 2)
        if ($pair.Count -ne 2 -or !$pair[0].Trim()) { continue }
        [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1], "Process")
    }
}

Import-HealEnvironment $envFile
$env:HEAL_HOME = $healHome
$env:HEAL_APP_ROOT = $appRoot
$env:HEAL_SERVICE_CODE_ROOT = Join-Path $appRoot "services"
$env:HEAL_CONFIG_ROOT = $configRoot
$env:HEAL_DATA_ROOT = $dataRoot
$env:HEAL_LOG_ROOT = $logRoot
$env:HEAL_BACKUP_ROOT = Join-Path $healHome "backups"
$env:HEAL_UPLOAD_ROOT = Join-Path $dataRoot "uploads"
$env:HEAL_CANON_ROOT = Join-Path $dataRoot "canon"
$env:HEAL_RSID_RESOLUTION_ROOT = Join-Path $dataRoot "legacy-rsid"
$env:HEAL_RUN_ROOT = Join-Path $dataRoot "runs"
$env:HEAL_JOB_ROOT = Join-Path $dataRoot "jobs"
$env:HEAL_ENRICHMENT_CACHE_ROOT = Join-Path $dataRoot "enrichment-cache"
$env:HEAL_REFERENCE_DATA_ROOT = Join-Path $dataRoot "references"
$env:HEAL_GRCH38_REFERENCE_FASTA = Join-Path $env:HEAL_REFERENCE_DATA_ROOT "GRCh38\hg38.fa"
$env:HEAL_GRCH38_REFERENCE_MANIFEST = Join-Path $env:HEAL_REFERENCE_DATA_ROOT "GRCh38\reference_manifest.json"
$env:HEAL_VCF_CANON_MATCH_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VCF_NORMALIZATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_MATCH_PREPARATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_AI_TRIAGE_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VARIANT_ENRICHMENT_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GROUPED_INTERPRETATION_PREP_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GROUPED_INDIVIDUAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_INDIVIDUAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_INTERPRETATION_NORMALIZATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GLOBAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_FINAL_REPORT_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VALIDATOR_SCRIPT = Join-Path $appRoot "services\heal-vcf-integrity\validate_vcf_integrity.py"
$env:HEAL_CANON_PROCESSOR_SCRIPT = Join-Path $appRoot "services\heal-canon-intake\process_heal_canon.py"
$env:HEAL_MATCH_PREPARATION_SCRIPT = Join-Path $appRoot "services\heal-match-preparation\prepare_match_deliverable.py"
$env:HEAL_VARIANT_ENRICHMENT_SCRIPT = Join-Path $appRoot "services\heal-variant-enrichment\enrich_observed_variants.py"
$env:HEAL_V2_LLM1_ENABLED = "false"

# The shared n8n definitions remain frozen during the migration window. Do not
# send HEAL requests to their historical C: paths; the API uses its local F:
# fallbacks until the two definitions are updated through authenticated n8n UI.
foreach ($name in @(
    "HEAL_N8N_UPLOAD_WEBHOOK_URL",
    "HEAL_N8N_VALIDATION_WEBHOOK_URL",
    "HEAL_N8N_CANON_WEBHOOK_URL",
    "HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL",
    "HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL",
    "HEAL_N8N_VARIANT_ENRICHMENT_WEBHOOK_URL",
    "HEAL_N8N_INDIVIDUAL_INTERPRETATION_WEBHOOK_URL",
    "HEAL_N8N_GLOBAL_INTERPRETATION_WEBHOOK_URL"
)) {
    [Environment]::SetEnvironmentVariable($name, "", "Process")
}

foreach ($directory in @($dataRoot, $logRoot, $env:HEAL_UPLOAD_ROOT, $env:HEAL_CANON_ROOT, $env:HEAL_RSID_RESOLUTION_ROOT, $env:HEAL_RUN_ROOT, $env:HEAL_JOB_ROOT, $env:HEAL_ENRICHMENT_CACHE_ROOT, $env:HEAL_REFERENCE_DATA_ROOT)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (!(Test-Path -LiteralPath (Join-Path $appRoot "server\dev-api.js"))) {
    throw "HEAL API source is missing from $appRoot."
}
if (!(Test-Path -LiteralPath (Join-Path $appRoot "node_modules\express"))) {
    throw "Node dependencies are missing. Run F:\Heal by FON\ops\Deploy-HealApp.ps1 first."
}

$node = if ($env:HEAL_NODE_EXE) { $env:HEAL_NODE_EXE } else { (Get-Command node.exe -ErrorAction Stop).Source }
$sha = (& git -C $appRoot rev-parse HEAD 2>$null).Trim()
if ($LASTEXITCODE -eq 0 -and $sha) { $env:HEAL_DEPLOYMENT_SHA = $sha }
else { $env:HEAL_DEPLOYMENT_SHA = "unknown" }

$logDirectory = Join-Path $logRoot "api"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory ("heal-vcf-api-" + (Get-Date -Format "yyyyMMdd") + ".log")

if ($ValidateOnly) {
    [pscustomobject]@{
        healHome = $healHome
        appRoot = $appRoot
        dataRoot = $dataRoot
        logPath = $logPath
        deploymentSha = $env:HEAL_DEPLOYMENT_SHA
        v2Llm1Enabled = $env:HEAL_V2_LLM1_ENABLED
    } | ConvertTo-Json -Depth 3
    exit 0
}

Set-Location $appRoot
& $node (Join-Path $appRoot "server\dev-api.js") *>> $logPath
exit $LASTEXITCODE
