[CmdletBinding()]
param(
    [string]$Image = "heal-vcf-normalizer:1.0.0"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$dockerfileRoot = Join-Path $healHome "app\services\heal-vcf-normalization"
if (!(Test-Path -LiteralPath (Join-Path $dockerfileRoot "Dockerfile"))) {
    throw "HEAL normalizer Dockerfile is missing from $dockerfileRoot."
}

& docker image inspect $Image 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Output "HEAL normalizer image is available: $Image"
    exit 0
}

& docker build --tag $Image $dockerfileRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not build HEAL normalizer image $Image."
}
