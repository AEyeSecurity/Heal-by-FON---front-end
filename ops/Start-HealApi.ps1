[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptParent = Split-Path -Parent $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $scriptParent "server\dev-api.js")) {
    # Repository layout after the C: -> F: migration: <HEAL_HOME>\app\ops.
    $appRoot = $scriptParent
    $healHome = Split-Path -Parent $appRoot
} else {
    # Backward-compatible packaged layout: <HEAL_HOME>\ops.
    $healHome = $scriptParent
    $appRoot = Join-Path $healHome "app"
}
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
$env:HEAL_CANON_CURATION_ROOT = Join-Path $env:HEAL_CANON_ROOT "curation"
$env:HEAL_RSID_RESOLUTION_ROOT = Join-Path $dataRoot "legacy-rsid"
$env:HEAL_RUN_ROOT = Join-Path $dataRoot "runs"
$env:HEAL_JOB_ROOT = Join-Path $dataRoot "jobs"
$env:HEAL_ENRICHMENT_CACHE_ROOT = Join-Path $dataRoot "enrichment-cache"
$env:HEAL_REFERENCE_DATA_ROOT = Join-Path $dataRoot "references"
if ([string]::IsNullOrWhiteSpace($env:HEAL_TIER1_CURATION_V2_ROOT)) {
    $env:HEAL_TIER1_CURATION_V2_ROOT = Join-Path $dataRoot "curation-candidates\tier1-v2"
}
$env:HEAL_GRCH38_REFERENCE_FASTA = Join-Path $env:HEAL_REFERENCE_DATA_ROOT "GRCh38\hg38.fa"
$env:HEAL_GRCH38_REFERENCE_MANIFEST = Join-Path $env:HEAL_REFERENCE_DATA_ROOT "GRCh38\reference_manifest.json"
$env:HEAL_VCF_CANON_MATCH_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VCF_NORMALIZATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_MATCH_PREPARATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_AI_TRIAGE_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VARIANT_ENRICHMENT_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GROUPED_INTERPRETATION_PREP_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GROUPED_INDIVIDUAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GROUPED_PROTOTYPE_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_INDIVIDUAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_INTERPRETATION_NORMALIZATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_GLOBAL_INTERPRETATION_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_FINAL_REPORT_ROOT = $env:HEAL_RUN_ROOT
$env:HEAL_VALIDATOR_SCRIPT = Join-Path $appRoot "services\heal-vcf-integrity\validate_vcf_integrity.py"
$env:HEAL_CANON_PROCESSOR_SCRIPT = Join-Path $appRoot "services\heal-canon-intake\process_heal_canon.py"
$env:HEAL_MATCH_PREPARATION_SCRIPT = Join-Path $appRoot "services\heal-match-preparation\prepare_match_deliverable.py"
$env:HEAL_VARIANT_ENRICHMENT_SCRIPT = Join-Path $appRoot "services\heal-variant-enrichment\enrich_observed_variants.py"
$env:HEAL_V2_LLM1_ENABLED = "false"
if ([string]::IsNullOrWhiteSpace($env:HEAL_V2_LLM1_PILOT_ENABLED)) {
    $env:HEAL_V2_LLM1_PILOT_ENABLED = "false"
}
if ([string]::IsNullOrWhiteSpace($env:HEAL_GROUPED_PROTOTYPE_ENABLED)) {
    $env:HEAL_GROUPED_PROTOTYPE_ENABLED = "false"
}
if ([string]::IsNullOrWhiteSpace($env:HEAL_PROTOTYPE_EXECUTION_MODE)) {
    $env:HEAL_PROTOTYPE_EXECUTION_MODE = "disabled"
}
if ([string]::IsNullOrWhiteSpace($env:HEAL_V2_EVIDENCE_DIGEST_ENABLED)) {
    $env:HEAL_V2_EVIDENCE_DIGEST_ENABLED = "false"
}
$env:HEAL_MECHANISM_REGISTRY_PATH = Join-Path $env:HEAL_CANON_CURATION_ROOT "mechanism_registry_v1.csv"
$env:HEAL_GWAS_TRAIT_MODULE_MAP_PATH = Join-Path $env:HEAL_CANON_CURATION_ROOT "gwas_module_relevance_registry_v1.csv"
# V2 QA remains conservative but allows the known test VCF's VEP coverage.
$env:HEAL_V2_MIN_VEP_COVERAGE = "0.90"

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

foreach ($directory in @($dataRoot, $logRoot, $env:HEAL_UPLOAD_ROOT, $env:HEAL_CANON_ROOT, $env:HEAL_CANON_CURATION_ROOT, $env:HEAL_RSID_RESOLUTION_ROOT, $env:HEAL_RUN_ROOT, $env:HEAL_JOB_ROOT, $env:HEAL_ENRICHMENT_CACHE_ROOT, $env:HEAL_REFERENCE_DATA_ROOT, $env:HEAL_TIER1_CURATION_V2_ROOT)) {
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
        v2Llm1PilotEnabled = $env:HEAL_V2_LLM1_PILOT_ENABLED
        groupedPrototypeEnabled = $env:HEAL_GROUPED_PROTOTYPE_ENABLED
        groupedPrototypeExecutionMode = $env:HEAL_PROTOTYPE_EXECUTION_MODE
        v2EvidenceDigestEnabled = $env:HEAL_V2_EVIDENCE_DIGEST_ENABLED
        tier1CurationV2Enabled = $env:HEAL_TIER1_CURATION_V2_ENABLED
        tier1CurationV2Model = $env:HEAL_TIER1_CURATION_V2_MODEL
        tier1CurationV2Cutoff = $env:HEAL_TIER1_CURATION_V2_CUTOFF
    } | ConvertTo-Json -Depth 3
    exit 0
}

Set-Location $appRoot
& $node (Join-Path $appRoot "server\dev-api.js") *>> $logPath
exit $LASTEXITCODE
