[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$startupRoot = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$archiveRoot = Join-Path $healHome "archive\legacy-startup"
New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
$disabled = [System.Collections.Generic.List[string]]::new()

foreach ($name in @("heal_vcf_api_start.cmd", "cloudflared_heal_api_start.cmd")) {
    $source = Join-Path $startupRoot $name
    if (!(Test-Path -LiteralPath $source)) { continue }
    Copy-Item -LiteralPath $source -Destination (Join-Path $archiveRoot $name) -Force
    $disabledPath = "$source.disabled-$(Get-Date -Format 'yyyyMMddHHmmss')"
    Move-Item -LiteralPath $source -Destination $disabledPath
    $disabled.Add($disabledPath)
}

[pscustomobject]@{ disabledStartupEntries = @($disabled); archiveRoot = $archiveRoot } | ConvertTo-Json
