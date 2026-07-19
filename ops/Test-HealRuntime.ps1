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
$dockerProbe = Start-Process -FilePath "docker.exe" -ArgumentList @("image", "inspect", "heal-vcf-normalizer:1.0.0") -PassThru -NoNewWindow
if ($dockerProbe.WaitForExit(5000)) {
    $checks.normalizerImage = $dockerProbe.ExitCode -eq 0
} else {
    Stop-Process -Id $dockerProbe.Id -Force
    $checks["dockerError"] = "Docker image probe timed out; Docker was not restarted."
}
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
