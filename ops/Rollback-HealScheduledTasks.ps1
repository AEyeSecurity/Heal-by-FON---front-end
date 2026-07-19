[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$tasks = @(
    [pscustomobject]@{ name = "HEAL VCF API"; action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\ServerCIT\services\heal-vcf-api\start_heal_vcf_api.ps1`"" },
    [pscustomobject]@{ name = "Cloudflared HEAL API"; action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\ServerCIT\scripts\start_cloudflared_heal_api.ps1`"" }
)

foreach ($task in $tasks) {
    & schtasks.exe /Query /TN $task.name /FO LIST 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Required HEAL scheduled task was not found: $($task.name)" }
    & schtasks.exe /Change /TN $task.name /TR $task.action | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restore scheduled task: $($task.name)" }
}
if ($Restart) {
    foreach ($task in $tasks) { Stop-ScheduledTask -TaskName $task.name -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    foreach ($task in $tasks) { Start-ScheduledTask -TaskName $task.name }
}
[pscustomobject]@{ restoredTasks = @($tasks.name); restarted = [bool]$Restart } | ConvertTo-Json
