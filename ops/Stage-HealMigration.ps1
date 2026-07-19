[CmdletBinding()]
param(
    [string]$SourceAppRoot = "C:\Heal by FON",
    [string]$SourceServicesRoot = "C:\ServerCIT\services",
    [string]$SourceConfigRoot = "C:\ProgramData\HealByFonApi",
    [string]$SourceReferenceRoot = "D:\ServerCIT\services\heal-reference-data",
    [string]$TargetHome = "F:\Heal by FON",
    [switch]$Apply,
    [switch]$UseLocalGitSource,
    [switch]$CodeOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-TargetHome([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if ($resolved -ne "F:\Heal by FON") {
        throw "Migration target must be exactly F:\Heal by FON. Received: $resolved"
    }
}

function Copy-HealTree([string]$Source, [string]$Destination, [string]$Label) {
    if (!(Test-Path -LiteralPath $Source)) {
        return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "source_missing" }
    }
    if (!$Apply) {
        return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "planned" }
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Copy failed for $Label with robocopy exit code $LASTEXITCODE." }
    return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "copied" }
}

function Copy-NormalizationRuns([string]$Source, [string]$Destination) {
    if (!(Test-Path -LiteralPath $Source)) {
        return [pscustomobject]@{ label = "normalization_artifacts"; source = $Source; destination = $Destination; status = "source_missing" }
    }
    $results = foreach ($run in Get-ChildItem -LiteralPath $Source -Directory -Force) {
        $summary = Join-Path $run.FullName "normalization_summary.json"
        if (!(Test-Path -LiteralPath $summary)) {
            [pscustomobject]@{ label = "normalization_artifacts"; source = $run.FullName; destination = (Join-Path $Destination $run.Name); status = "skipped_incomplete_run" }
            continue
        }
        # The normalized VCF is a temporary bridge into matching, never a retained migration artifact.
        $target = Join-Path $Destination $run.Name
        if ($Apply) {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            & robocopy.exe $run.FullName $target /E /COPY:DAT /DCOPY:T /R:2 /W:2 /XF normalization_input.vcf normalized.vcf.gz /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -gt 7) { throw "Copy failed for completed normalization run $($run.Name)." }
            [pscustomobject]@{ label = "normalization_artifacts"; source = $run.FullName; destination = $target; status = "copied_without_temporary_vcf" }
        } else {
            [pscustomobject]@{ label = "normalization_artifacts"; source = $run.FullName; destination = $target; status = "planned_without_temporary_vcf" }
        }
    }
    return @($results)
}

function Copy-HealFile([string]$Source, [string]$Destination, [string]$Label) {
    if (!(Test-Path -LiteralPath $Source)) {
        return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "source_missing" }
    }
    if (!$Apply) {
        return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "planned" }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    return [pscustomobject]@{ label = $Label; source = $Source; destination = $Destination; status = "copied" }
}

Assert-TargetHome $TargetHome
$targetData = Join-Path $TargetHome "data"
$targetApp = Join-Path $TargetHome "app"
$targetOps = Join-Path $TargetHome "ops"
$targetConfig = Join-Path $TargetHome "config"
$mappings = @(
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-vcf-integrity\incoming"; destination = Join-Path $targetData "uploads"; label = "uploads" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-canon-intake\incoming"; destination = Join-Path $targetData "canon\incoming"; label = "canon_incoming" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-canon-intake\runs"; destination = Join-Path $targetData "canon\runs"; label = "canon_runs" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-canon-intake\current"; destination = Join-Path $targetData "canon\current"; label = "canon_current" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-rsid-resolution\runs"; destination = Join-Path $targetData "legacy-rsid\runs"; label = "legacy_rsid_runs" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-rsid-resolution\current"; destination = Join-Path $targetData "legacy-rsid\current"; label = "legacy_rsid_current" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-rsid-resolution\cache"; destination = Join-Path $targetData "legacy-rsid\cache"; label = "legacy_rsid_cache" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-vcf-canon-match\jobs"; destination = Join-Path $targetData "jobs"; label = "job_records" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-vcf-canon-match\runs"; destination = Join-Path $targetData "runs\legacy-match"; label = "legacy_match_artifacts" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-vcf-normalization\runs"; destination = Join-Path $targetData "runs\legacy-normalization"; label = "normalization_artifacts" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-match-preparation\runs"; destination = Join-Path $targetData "runs\legacy-preparation"; label = "preparation_artifacts" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-ai-triage\runs"; destination = Join-Path $targetData "runs\legacy-triage"; label = "triage_artifacts" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-variant-enrichment\runs"; destination = Join-Path $targetData "runs\legacy-enrichment"; label = "enrichment_artifacts" },
    [pscustomobject]@{ source = Join-Path $SourceServicesRoot "heal-variant-enrichment\cache"; destination = Join-Path $targetData "enrichment-cache\legacy-rsid"; label = "legacy_enrichment_cache" },
    [pscustomobject]@{ source = $SourceReferenceRoot; destination = Join-Path $targetData "references"; label = "managed_references" },
    [pscustomobject]@{ source = (Join-Path $SourceAppRoot "logs"); destination = Join-Path $TargetHome "logs\historical-app"; label = "historical_app_logs" },
    [pscustomobject]@{ source = (Join-Path $SourceAppRoot "backups"); destination = Join-Path $TargetHome "backups"; label = "historical_backups" },
    [pscustomobject]@{ source = "C:\ServerCIT\logs\heal-vcf-api"; destination = Join-Path $TargetHome "logs\historical-api"; label = "historical_api_logs" },
    [pscustomobject]@{ source = (Join-Path $SourceConfigRoot ""); destination = $targetConfig; label = "configuration" },
    [pscustomobject]@{ source = "C:\ProgramData\Cloudflared-HealApi\token.txt"; destination = Join-Path $targetConfig "cloudflared-token.txt"; label = "cloudflared_token"; mode = "file" },
    [pscustomobject]@{ source = "C:\ServerCIT\logs\cloudflared\HealApi.log"; destination = Join-Path $TargetHome "logs\historical-cloudflared\HealApi.log"; label = "historical_cloudflared_log"; mode = "file" }
)

if ($Apply) {
    New-Item -ItemType Directory -Force -Path $TargetHome | Out-Null
    if (!(Test-Path -LiteralPath $targetApp)) {
        $cloneSource = $SourceAppRoot
        if (!$UseLocalGitSource) {
            $cloneSource = (& git -C $SourceAppRoot remote get-url origin 2>$null).Trim()
            if ($LASTEXITCODE -ne 0 -or !$cloneSource) { throw "Could not determine the HEAL Git origin from $SourceAppRoot." }
        }
        & git clone --branch main $cloneSource $targetApp
        if ($LASTEXITCODE -ne 0) { throw "Could not clone HEAL app into $targetApp." }
    }
    elseif (!(Test-Path -LiteralPath (Join-Path $targetApp ".git"))) {
        throw "Target app exists but is not a Git checkout. Refusing to overwrite $targetApp."
    }
}

$results = if ($CodeOnly) {
    @([pscustomobject]@{ label = "data_copy"; source = "not_requested"; destination = $targetData; status = "skipped_code_only" })
} else {
    foreach ($mapping in $mappings) {
        if ($mapping.label -eq "normalization_artifacts") {
            Copy-NormalizationRuns $mapping.source $mapping.destination
        } elseif (($mapping.PSObject.Properties.Name -contains "mode") -and $mapping.mode -eq "file") {
            Copy-HealFile $mapping.source $mapping.destination $mapping.label
        } else {
            Copy-HealTree $mapping.source $mapping.destination $mapping.label
        }
    }
}
$results = @($results)

if ($Apply) {
    $opsResult = Copy-HealTree (Join-Path $targetApp "ops") $targetOps "operational_scripts"
    $results = @($results) + @($opsResult)

    if (!$CodeOnly) {
        # Only F:\Heal by FON\config receives these ACLs; shared ProgramData remains untouched.
        & icacls.exe $targetConfig /inheritance:r /grant:r "$env:USERNAME`:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C | Out-Null
        if ($LASTEXITCODE -gt 0) { throw "Could not restrict ACLs on $targetConfig." }
        & (Join-Path $targetOps "Rebase-HealState.ps1") -HealHome $TargetHome | Out-Null
    }

    $archiveRoot = Join-Path $TargetHome "archive"
    New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
    $ledger = [ordered]@{
        schemaVersion = "heal-migration-ledger/v1"
        stagedAt = [DateTime]::UtcNow.ToString("o")
        sourceFrozenUntilApproval = $true
        sourceRemovalNotBefore = [DateTime]::UtcNow.AddDays(7).ToString("o")
        sourceRoots = @($SourceAppRoot, $SourceServicesRoot, $SourceConfigRoot, $SourceReferenceRoot)
        targetHome = $TargetHome
        copyResults = @($results)
        note = if ($CodeOnly) { "Only the committed app checkout and ops scripts were staged. No data or configuration was copied." } else { "No source path was deleted. Cutover remains a separate action limited to the two HEAL scheduled tasks." }
    }
    $ledger | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $archiveRoot "migration-ledger.json") -Encoding utf8
}

[pscustomobject]@{ mode = if ($Apply) { "apply" } else { "dry-run" }; targetHome = $TargetHome; mappings = @($results) } | ConvertTo-Json -Depth 6
