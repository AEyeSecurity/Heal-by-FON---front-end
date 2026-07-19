[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$tasks = @(
    [pscustomobject]@{ name = "HEAL VCF API"; script = Join-Path $PSScriptRoot "Start-HealApi.ps1" },
    [pscustomobject]@{ name = "Cloudflared HEAL API"; script = Join-Path $PSScriptRoot "Start-HealCloudflared.ps1" }
)

foreach ($task in $tasks) {
    & schtasks.exe /Query /TN $task.name /FO LIST 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Required HEAL scheduled task was not found: $($task.name)" }
    $action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($task.script)`""
    & schtasks.exe /Change /TN $task.name /TR $action | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not update scheduled task: $($task.name)" }
}

if ($Restart) {
    foreach ($task in $tasks) { Stop-ScheduledTask -TaskName $task.name -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    foreach ($task in $tasks) { Start-ScheduledTask -TaskName $task.name }
}

[pscustomobject]@{ updatedTasks = @($tasks.name); restarted = [bool]$Restart; healHome = $healHome } | ConvertTo-Json
