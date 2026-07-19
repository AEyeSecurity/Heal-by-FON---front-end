[CmdletBinding()]
param(
    [switch]$SkipNpmInstall,
    [switch]$SkipDockerCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $healHome "app"
if (!(Test-Path -LiteralPath (Join-Path $appRoot "package-lock.json"))) {
    throw "HEAL application checkout is missing from $appRoot."
}

if (!$SkipNpmInstall) {
    Push-Location $appRoot
    try {
        & npm.cmd ci --omit=dev
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed for HEAL." }
    }
    finally { Pop-Location }
}
if (!$SkipDockerCheck) {
    & (Join-Path $PSScriptRoot "Ensure-HealNormalizerImage.ps1")
}
& (Join-Path $PSScriptRoot "Write-HealRuntimeManifest.ps1")
