[CmdletBinding()]
param(
    [switch]$Apply,
    [int]$UploadRetentionHours = 24,
    [int]$AuditRetentionDays = 14
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $healHome "data"
$cutoffUploads = (Get-Date).ToUniversalTime().AddHours(-1 * [Math]::Max(1, $UploadRetentionHours))
$cutoffAudits = (Get-Date).ToUniversalTime().AddDays(-1 * [Math]::Max(1, $AuditRetentionDays))
$candidates = [System.Collections.Generic.List[object]]::new()

function Add-ExpiredDirectories([string]$Root, [datetime]$Cutoff, [string]$Kind) {
    if (!(Test-Path -LiteralPath $Root)) { return }
    foreach ($item in Get-ChildItem -LiteralPath $Root -Directory -Force) {
        if ($item.LastWriteTimeUtc -lt $Cutoff) {
            $candidates.Add([pscustomobject]@{ kind = $Kind; path = $item.FullName; lastWriteUtc = $item.LastWriteTimeUtc })
        }
    }
}

function Add-ExpiredFiles([string]$Root, [datetime]$Cutoff, [string]$Kind) {
    if (!(Test-Path -LiteralPath $Root)) { return }
    foreach ($item in Get-ChildItem -LiteralPath $Root -File -Force) {
        if ($item.LastWriteTimeUtc -lt $Cutoff) {
            $candidates.Add([pscustomobject]@{ kind = $Kind; path = $item.FullName; lastWriteUtc = $item.LastWriteTimeUtc })
        }
    }
}

# Protected roots are deliberately omitted: canon, references, enrichment-cache, backups, and archive.
Add-ExpiredDirectories (Join-Path $dataRoot "uploads") $cutoffUploads "upload_workspace"
Add-ExpiredDirectories (Join-Path $dataRoot "runs") $cutoffAudits "run_workspace"
Add-ExpiredFiles (Join-Path $dataRoot "jobs") $cutoffAudits "job_record"

$result = [ordered]@{
    mode = if ($Apply) { "apply" } else { "dry-run" }
    generatedAt = [DateTime]::UtcNow.ToString("o")
    uploadCutoffUtc = $cutoffUploads.ToString("o")
    auditCutoffUtc = $cutoffAudits.ToString("o")
    candidates = @($candidates)
    protectedRoots = @("data/canon", "data/references", "data/enrichment-cache", "backups", "archive")
}

if ($Apply) {
    foreach ($candidate in $candidates) {
        if ($candidate.kind -eq "job_record") {
            Remove-Item -LiteralPath $candidate.path -Force
        } else {
            Remove-Item -LiteralPath $candidate.path -Recurse -Force
        }
    }
}

$result | ConvertTo-Json -Depth 5
