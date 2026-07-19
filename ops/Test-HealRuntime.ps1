[CmdletBinding()]
param(
    [switch]$RequireApi
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $healHome "data"
$reference = Join-Path $dataRoot "references\GRCh38\hg38.fa"
$checks = [ordered]@{
    healHome = Test-Path -LiteralPath $healHome
    app = Test-Path -LiteralPath (Join-Path $healHome "app\server\dev-api.js")
    config = Test-Path -LiteralPath (Join-Path $healHome "config\heal-vcf-api.env")
    reference = (Test-Path -LiteralPath $reference) -and (Test-Path -LiteralPath "$reference.fai")
    normalizerImage = $false
    api = $false
}
& docker image inspect "heal-vcf-normalizer:1.0.0" 2>$null | Out-Null
$checks.normalizerImage = $LASTEXITCODE -eq 0
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/health" -TimeoutSec 5
    $checks.api = [bool]$health.ok
    $checks["apiHealth"] = $health
}
catch {
    $checks["apiError"] = $_.Exception.Message
}
if ($RequireApi -and !$checks.api) { throw "HEAL API health check did not pass." }
$checks | ConvertTo-Json -Depth 8
