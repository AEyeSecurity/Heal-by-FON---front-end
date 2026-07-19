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

$probe = Start-Process -FilePath "docker.exe" -ArgumentList @("image", "inspect", $Image) -PassThru -NoNewWindow
if (!$probe.WaitForExit(10000)) {
    Stop-Process -Id $probe.Id -Force
    throw "Docker did not respond while checking the HEAL normalizer image. Docker was not restarted."
}
if ($probe.ExitCode -eq 0) {
    Write-Output "HEAL normalizer image is available: $Image"
    exit 0
}

& docker build --tag $Image $dockerfileRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not build HEAL normalizer image $Image."
}
