param(
  [Parameter(Mandatory = $true)]
  [string]$InputJsonBase64
)

$ErrorActionPreference = "Stop"

$python = $env:HEAL_PYTHON_EXE
if ([string]::IsNullOrWhiteSpace($python)) {
  $python = "python"
}

$script = $env:HEAL_LEGACY_MATCHER_SCRIPT
if ([string]::IsNullOrWhiteSpace($script)) {
  $script = Join-Path $PSScriptRoot "match_vcf_to_rsid_ready.py"
}

& $python $script --input-json-base64 $InputJsonBase64
exit $LASTEXITCODE
