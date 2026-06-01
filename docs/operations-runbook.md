# HEAL VCF API Operations Runbook

## Health Checks

Public API health:

```powershell
Invoke-RestMethod https://heal-api.aeye.com.ar/api/health
```

Expected key fields:

```text
ok: true
storageConfigured: true
validatorConfigured: true
requireOrigin: true
turnstileRequired: true
turnstileAllowedHostnames: healbyfon.aeye.com.ar
```

Local API health:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/health
```

Cloudflare Tunnel connector health:

```powershell
Invoke-RestMethod http://127.0.0.1:20243/ready
```

Expected:

```text
status: 200
readyConnections: 4
```

## Logs

Backend API:

```powershell
Get-Content C:\ServerCIT\logs\heal-vcf-api\heal-vcf-api.log -Tail 100
```

HEAL Cloudflare Tunnel:

```powershell
Get-Content C:\ServerCIT\logs\cloudflared\HealApi.log -Tail 100
```

Cloudflared transient QUIC timeouts can appear during reconnects. Treat them as actionable only if the tunnel does not recover or `/ready` is not healthy.

## Upload Storage

Upload root:

```text
C:\ServerCIT\services\heal-vcf-integrity\incoming
```

Inspect uploaded workspaces:

```powershell
$root='C:\ServerCIT\services\heal-vcf-integrity\incoming'
Get-ChildItem $root | ForEach-Object {
  $size = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum
  [pscustomobject]@{
    Name=$_.Name
    GB=[math]::Round($size/1GB,3)
    Modified=$_.LastWriteTime
  }
}
```

Configured retention:

```text
HEAL_MAX_UPLOADS=12
HEAL_UPLOAD_TTL_HOURS=24
```

## Safe Manual Cleanup

Only remove upload folders under:

```text
C:\ServerCIT\services\heal-vcf-integrity\incoming
```

Example for deleting a known old upload:

```powershell
$root = (Resolve-Path 'C:\ServerCIT\services\heal-vcf-integrity\incoming').Path
$target = Join-Path $root '<uploadId>'
$resolved = (Resolve-Path $target).Path
if ($resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
```

Do not delete an upload folder that is currently being validated or intended for the next n8n step.

## Environment Files

Frontend public production config:

```text
.env.production
```

Only public `VITE_*` values are stored there.

Backend environment file:

```text
C:\ProgramData\HealByFonApi\heal-vcf-api.env
```

Do not commit this file. It may contain:

```text
HEAL_TURNSTILE_SECRET
HEAL_N8N_UPLOAD_WEBHOOK_URL
HEAL_N8N_VALIDATION_WEBHOOK_URL
HEAL_N8N_CANON_WEBHOOK_URL
HEAL_N8N_RSID_RESOLUTION_WEBHOOK_URL
HEAL_N8N_VCF_CANON_MATCH_WEBHOOK_URL
HEAL_N8N_MATCH_PREPARATION_WEBHOOK_URL
HEAL_N8N_WEBHOOK_TOKEN
```

Cloudflare Tunnel token file:

```text
C:\ProgramData\Cloudflared-HealApi\token.txt
```

Do not commit this file.

## Restart Commands

Restart only the HEAL API process:

```powershell
$apiProcs = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*server/dev-api.js*' }
if ($apiProcs) { Stop-Process -Id ($apiProcs.ProcessId) -Force }
Start-Process -FilePath 'powershell.exe' `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\ServerCIT\services\heal-vcf-api\start_heal_vcf_api.ps1' `
  -WindowStyle Hidden `
  -WorkingDirectory 'C:\ServerCIT\services\heal-vcf-api'
```

Restart only the HEAL Cloudflare connector:

```powershell
$cfProcs = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like 'cloudflared*' -and $_.CommandLine -like '*20243*' }
if ($cfProcs) { Stop-Process -Id ($cfProcs.ProcessId) -Force }
Start-Process -FilePath 'powershell.exe' `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\ServerCIT\scripts\start_cloudflared_heal_api.ps1' `
  -WindowStyle Hidden `
  -WorkingDirectory 'C:\ServerCIT\scripts'
```

These commands intentionally target only HEAL API resources.

## Persistence

Persistence scripts were created but require elevated PowerShell:

```powershell
C:\ServerCIT\scripts\install_cloudflared_heal_api_task.ps1
C:\ServerCIT\services\heal-vcf-api\install_heal_vcf_api_task.ps1
```

Run them from PowerShell as Administrator.

Current fallback persistence has also been added to the current user's Startup folder:

```text
C:\Users\Usuario\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\heal_vcf_api_start.cmd
C:\Users\Usuario\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\cloudflared_heal_api_start.cmd
```

This matches the current operational pattern used by n8n on this server: it restores availability after user login, not before login. For production-grade unattended recovery, replace this fallback with the elevated scheduled tasks above.

## Deployment

Frontend deployment is managed by Cloudflare Pages from GitHub:

```text
Repository: AEyeSecurity/Heal-by-FON---front-end
Branch: main
Build command: npm run build
Output directory: dist
```

Push to `main` triggers deployment.

## Manual Functional Test

1. Open `https://healbyfon.aeye.com.ar`.
2. Select a small VCF.
3. Complete Turnstile.
4. Run quick analysis.
5. Confirm `VCF validado`.
6. Re-select the same file.
7. Confirm duplicate reuse popup appears.
8. Choose `Usar VCF existente`.
9. Confirm validation starts without upload.
10. After a valid VCF, confirm the pipeline reaches `Match preparation`.
11. Download:
    - VCF-canon matches CSV
    - prepared audit CSV
    - minimal prepared CSV

## Current Operational Warnings

- Rotate the dedicated HEAL tunnel token before long-term production.
- Configure scheduled tasks from elevated PowerShell before relying on automatic recovery after reboot.
- Configure n8n webhooks before starting downstream workflow automation.
