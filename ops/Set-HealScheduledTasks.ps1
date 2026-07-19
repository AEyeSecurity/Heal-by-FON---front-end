[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$healHome = Split-Path -Parent $PSScriptRoot
$runtimeIdentity = "$env:USERDOMAIN\$env:USERNAME"
$tasks = @(
    [pscustomobject]@{ name = "HEAL VCF API"; script = Join-Path $PSScriptRoot "Start-HealApi.ps1" },
    [pscustomobject]@{ name = "Cloudflared HEAL API"; script = Join-Path $PSScriptRoot "Start-HealCloudflared.ps1" }
)
$created = [System.Collections.Generic.List[string]]::new()
$updated = [System.Collections.Generic.List[string]]::new()

foreach ($task in $tasks) {
    $action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($task.script)`""
    $existing = Get-ScheduledTask -TaskName $task.name -ErrorAction SilentlyContinue
    if ($existing) {
        & schtasks.exe /Change /TN $task.name /TR $action | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not update scheduled task: $($task.name)" }
        $updated.Add($task.name)
        continue
    }
    $scheduledAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$($task.script)`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $runtimeIdentity
    $principal = New-ScheduledTaskPrincipal -UserId $runtimeIdentity -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $task.name -Action $scheduledAction -Trigger $trigger -Principal $principal -Description "HEAL by FON isolated runtime task" -Force | Out-Null
    $created.Add($task.name)
}

if ($Restart) {
    foreach ($task in $tasks) { Stop-ScheduledTask -TaskName $task.name -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    foreach ($task in $tasks) { Start-ScheduledTask -TaskName $task.name }
}

[pscustomobject]@{ createdTasks = @($created); updatedTasks = @($updated); restarted = [bool]$Restart; healHome = $healHome } | ConvertTo-Json
