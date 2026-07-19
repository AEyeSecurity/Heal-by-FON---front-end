[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$configRoot = Join-Path $healHome "config"
$logRoot = Join-Path $healHome "logs"
$tokenPath = Join-Path $configRoot "cloudflared-token.txt"
if (!(Test-Path -LiteralPath $tokenPath)) {
    throw "Cloudflared token file was not found at $tokenPath."
}
$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if (!$token) { throw "Cloudflared token file is empty." }

$cloudflared = if ($env:HEAL_CLOUDFLARED_EXE) {
    $env:HEAL_CLOUDFLARED_EXE
} else {
    (Get-Command cloudflared.exe -ErrorAction Stop).Source
}
$logDirectory = Join-Path $logRoot "cloudflared"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory ("heal-api-tunnel-" + (Get-Date -Format "yyyyMMdd") + ".log")

if ($ValidateOnly) {
    [pscustomobject]@{ healHome = $healHome; executable = $cloudflared; logPath = $logPath } | ConvertTo-Json
    exit 0
}

& $cloudflared --no-autoupdate --metrics "127.0.0.1:20243" tunnel run --token $token *>> $logPath
exit $LASTEXITCODE
