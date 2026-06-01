[CmdletBinding()]
param(
    [string]$InputJsonBase64,
    [string]$FilePath,
    [string]$CalculateChecksum = 'false',
    [string]$MaxVariants = '20',
    [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'validate_vcf_integrity.py'

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Validator script not found: $scriptPath"
}

$arguments = @($scriptPath)

if (-not [string]::IsNullOrWhiteSpace($InputJsonBase64)) {
    $arguments += @('--input-json-base64', $InputJsonBase64)
}
elseif (-not [string]::IsNullOrWhiteSpace($FilePath)) {
    $arguments += @('--path', $FilePath)
    if ($CalculateChecksum -match '^(?i:true|1|yes)$') {
        $arguments += '--checksum'
    }
    if (-not [string]::IsNullOrWhiteSpace($MaxVariants)) {
        $arguments += @('--max-variants', $MaxVariants)
    }
}
else {
    $arguments += @('--path', '')
}

& $PythonExe @arguments

# n8n must receive the validator JSON even when the VCF is invalid.
# The JSON status carries valid/warning/invalid; the wrapper exits 0 so the
# next node can parse and return the structured result.
exit 0
