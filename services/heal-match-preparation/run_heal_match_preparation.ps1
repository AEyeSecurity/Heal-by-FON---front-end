param(
  [Parameter(Mandatory = $true)]
  [string]$InputJsonBase64
)

$ErrorActionPreference = "Stop"

$python = $env:HEAL_PYTHON_EXE
if ([string]::IsNullOrWhiteSpace($python)) {
  $python = "python"
}

$script = $env:HEAL_MATCH_PREPARATION_SCRIPT
if ([string]::IsNullOrWhiteSpace($script)) {
  $script = "C:\ServerCIT\services\heal-match-preparation\prepare_match_deliverable.py"
}

& $python $script --input-json-base64 $InputJsonBase64
exit $LASTEXITCODE
