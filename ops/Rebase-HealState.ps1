[CmdletBinding()]
param(
    [string]$HealHome = "F:\Heal by FON"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$dataRoot = Join-Path $HealHome "data"
$pathMappings = [ordered]@{
    "C:\ServerCIT\services\heal-vcf-integrity\incoming" = (Join-Path $dataRoot "uploads")
    "C:\ServerCIT\services\heal-canon-intake" = (Join-Path $dataRoot "canon")
    "C:\ServerCIT\services\heal-rsid-resolution" = (Join-Path $dataRoot "legacy-rsid")
    "C:\ServerCIT\services\heal-vcf-canon-match\runs" = (Join-Path $dataRoot "runs\legacy-match")
    "C:\ServerCIT\services\heal-vcf-normalization\runs" = (Join-Path $dataRoot "runs\legacy-normalization")
    "C:\ServerCIT\services\heal-match-preparation\runs" = (Join-Path $dataRoot "runs\legacy-preparation")
    "C:\ServerCIT\services\heal-ai-triage\runs" = (Join-Path $dataRoot "runs\legacy-triage")
    "C:\ServerCIT\services\heal-variant-enrichment\runs" = (Join-Path $dataRoot "runs\legacy-enrichment")
    "C:\ServerCIT\services\heal-variant-enrichment\cache" = (Join-Path $dataRoot "enrichment-cache\legacy-rsid")
    "D:\ServerCIT\services\heal-reference-data" = (Join-Path $dataRoot "references")
}

function Rebase-Value([object]$Value) {
    if ($Value -is [string]) {
        foreach ($source in $pathMappings.Keys) {
            if ($Value.StartsWith($source, [StringComparison]::OrdinalIgnoreCase)) {
                return (Join-Path $pathMappings[$source] $Value.Substring($source.Length).TrimStart("\\", "/"))
            }
        }
        return $Value
    }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in @($Value.Keys)) { $Value[$key] = Rebase-Value $Value[$key] }
        return $Value
    }
    if ($Value -is [System.Collections.IList]) {
        for ($index = 0; $index -lt $Value.Count; $index += 1) { $Value[$index] = Rebase-Value $Value[$index] }
        return $Value
    }
    return $Value
}

function Rebase-JsonFile([string]$Path) {
    try {
        $payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable -Depth 100
    }
    catch {
        return $false
    }
    $payload = Rebase-Value $payload
    $payload | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Path -Encoding utf8
    return $true
}

$changed = 0
foreach ($root in @(
    (Join-Path $dataRoot "uploads"),
    (Join-Path $dataRoot "canon\runs"),
    (Join-Path $dataRoot "legacy-rsid\runs"),
    (Join-Path $dataRoot "jobs")
)) {
    if (!(Test-Path -LiteralPath $root)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $root -Filter *.json -File -Recurse) {
        if (Rebase-JsonFile $file.FullName) { $changed += 1 }
    }
}

$canonManifest = Join-Path $dataRoot "canon\current\current.json"
if (Test-Path -LiteralPath $canonManifest) {
    $manifest = Get-Content -LiteralPath $canonManifest -Raw | ConvertFrom-Json -AsHashtable
    if ($manifest.runId) {
        $manifest.summaryPath = "runs/$($manifest.runId)/canon_summary.json"
        $manifest.previewPath = "runs/$($manifest.runId)/canon_preview.json"
        $manifest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $canonManifest -Encoding utf8
    }
}

$rsidManifest = Join-Path $dataRoot "legacy-rsid\current\current.json"
if (Test-Path -LiteralPath $rsidManifest) {
    $manifest = Get-Content -LiteralPath $rsidManifest -Raw | ConvertFrom-Json -AsHashtable
    if ($manifest.runId) {
        $manifest.summaryPath = "runs/$($manifest.runId)/rsid_resolution_summary.json"
        $manifest.rsidMatchReadyCsv = "runs/$($manifest.runId)/rsid_match_ready.csv"
        $manifest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $rsidManifest -Encoding utf8
    }
}

[pscustomobject]@{ rebasedJsonFiles = $changed; dataRoot = $dataRoot } | ConvertTo-Json
