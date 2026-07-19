[CmdletBinding()]
param(
    [switch]$ApproveSourceRemoval
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$ledgerPath = Join-Path $healHome "archive\migration-ledger.json"

function Assert-ExactLegacyPath([string]$Path) {
    $allowed = @(
        "C:\Heal by FON",
        "C:\ServerCIT\services\heal-vcf-integrity",
        "C:\ServerCIT\services\heal-canon-intake",
        "C:\ServerCIT\services\heal-rsid-resolution",
        "C:\ServerCIT\services\heal-vcf-canon-match",
        "C:\ServerCIT\services\heal-vcf-normalization",
        "C:\ServerCIT\services\heal-match-preparation",
        "C:\ServerCIT\services\heal-ai-triage",
        "C:\ServerCIT\services\heal-variant-enrichment",
        "C:\ServerCIT\services\heal-vcf-api",
        "C:\ServerCIT\logs\heal-vcf-api",
        "D:\ServerCIT\services\heal-reference-data",
        "C:\ProgramData\HealByFonApi",
        "C:\ProgramData\Cloudflared-HealApi\token.txt",
        "C:\ServerCIT\logs\cloudflared\HealApi.log",
        "C:\ServerCIT\scripts\install_cloudflared_heal_api_task.ps1"
    )
    $startupRoot = "C:\Users\Usuario\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    $isDisabledHealStartup = $Path -match ("^" + [Regex]::Escape($startupRoot) + "\\(heal_vcf_api_start|cloudflared_heal_api_start)\.cmd\.disabled-\d+$")
    if (($allowed -notcontains $Path) -and -not $isDisabledHealStartup) {
        throw "Refusing to operate on a non-HEAL legacy path: $Path"
    }
}

function Get-LegacyPaths() {
    $paths = @(
        "C:\Heal by FON",
        "C:\ServerCIT\services\heal-vcf-integrity",
        "C:\ServerCIT\services\heal-canon-intake",
        "C:\ServerCIT\services\heal-rsid-resolution",
        "C:\ServerCIT\services\heal-vcf-canon-match",
        "C:\ServerCIT\services\heal-vcf-normalization",
        "C:\ServerCIT\services\heal-match-preparation",
        "C:\ServerCIT\services\heal-ai-triage",
        "C:\ServerCIT\services\heal-variant-enrichment",
        "C:\ServerCIT\services\heal-vcf-api",
        "C:\ServerCIT\logs\heal-vcf-api",
        "D:\ServerCIT\services\heal-reference-data",
        "C:\ProgramData\HealByFonApi",
        "C:\ProgramData\Cloudflared-HealApi\token.txt",
        "C:\ServerCIT\logs\cloudflared\HealApi.log",
        "C:\ServerCIT\scripts\install_cloudflared_heal_api_task.ps1"
    )
    $startupRoot = "C:\Users\Usuario\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    $paths += @(Get-ChildItem -LiteralPath $startupRoot -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(heal_vcf_api_start|cloudflared_heal_api_start)\.cmd\.disabled-\d+$' } |
        Select-Object -ExpandProperty FullName)
    return @($paths)
}

function Assert-CutoverHealthy() {
    $requiredTasks = @("HEAL VCF API", "Cloudflared HEAL API")
    foreach ($taskName in $requiredTasks) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $arguments = [string]$task.Actions[0].Arguments
        if ($arguments -notmatch [Regex]::Escape("F:\Heal by FON\ops\")) {
            throw "Scheduled task $taskName does not point to the F: runtime."
        }
    }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/health" -TimeoutSec 15
    if (-not $health.ok) { throw "HEAL API health check did not succeed." }
}

if (!(Test-Path -LiteralPath $ledgerPath)) {
    throw "Migration ledger is missing: $ledgerPath"
}
$ledger = Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json
if ([string]$ledger.targetHome -ne "F:\Heal by FON") {
    throw "Migration ledger does not describe the approved F: target."
}

$legacyPaths = @(Get-LegacyPaths)
$preview = foreach ($path in $legacyPaths) {
    Assert-ExactLegacyPath $path
    [pscustomobject]@{ path = $path; exists = Test-Path -LiteralPath $path }
}

if (!$ApproveSourceRemoval) {
    [pscustomobject]@{
        mode = "preview"
        removalNotBefore = $ledger.sourceRemovalNotBefore
        requiresExplicitApproval = $true
        n8nWorkflowMigration = $ledger.n8nWorkflowMigration.status
        paths = @($preview)
    } | ConvertTo-Json -Depth 5
    exit 0
}

$removalNotBefore = [DateTimeOffset]::Parse([string]$ledger.sourceRemovalNotBefore)
if ([DateTimeOffset]::UtcNow -lt $removalNotBefore.ToUniversalTime()) {
    throw "Source removal is blocked until $($ledger.sourceRemovalNotBefore)."
}
if ([string]$ledger.n8nWorkflowMigration.status -ne "complete") {
    throw "Source removal is blocked until the two HEAL n8n workflow paths are updated through authenticated UI."
}

Assert-CutoverHealthy
foreach ($entry in $preview | Where-Object { $_.exists }) {
    Assert-ExactLegacyPath $entry.path
    if (Test-Path -LiteralPath $entry.path -PathType Container) {
        Remove-Item -LiteralPath $entry.path -Recurse -Force
    } else {
        Remove-Item -LiteralPath $entry.path -Force
    }
}

$ledger.sourceFrozenUntilApproval = $false
$ledger.sourceRemovedAt = [DateTime]::UtcNow.ToString("o")
$ledger.sourceRemovalApproved = $true
$ledger | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ledgerPath -Encoding utf8
[pscustomobject]@{ mode = "applied"; removed = @($preview | Where-Object { $_.exists } | Select-Object -ExpandProperty path) } | ConvertTo-Json -Depth 5
