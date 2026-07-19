[CmdletBinding()]
param(
    [string]$ReferenceRoot = $(
        if ($env:HEAL_REFERENCE_DATA_ROOT) { $env:HEAL_REFERENCE_DATA_ROOT }
        elseif ($env:HEAL_DATA_ROOT) { Join-Path $env:HEAL_DATA_ROOT "references" }
        else { Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "data\references" }
    ),
    [string]$Image = "heal-vcf-normalizer:1.0.0"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$assembly = "GRCh38"
$sourceUrl = "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz"
$assemblyRoot = Join-Path $ReferenceRoot $assembly
$fastaPath = Join-Path $assemblyRoot "hg38.fa"
$indexPath = "$fastaPath.fai"
$manifestPath = Join-Path $assemblyRoot "reference_manifest.json"
$serviceRoot = Split-Path -Parent $PSScriptRoot

if ((Test-Path -LiteralPath $fastaPath) -and (Test-Path -LiteralPath $indexPath) -and (Test-Path -LiteralPath $manifestPath)) {
    Write-Output "Managed GRCh38 reference already provisioned at $assemblyRoot"
    exit 0
}

$drive = (Get-Item -LiteralPath (Split-Path -Qualifier $ReferenceRoot)).PSDrive
if ($drive.Free -lt 8GB) {
    throw "At least 8 GB of free space is required to provision the GRCh38 reference."
}

& docker version --format "{{.Server.Version}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is required to provision the managed GRCh38 reference."
}

& docker build --tag $Image $PSScriptRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not build $Image."
}

New-Item -ItemType Directory -Force -Path $ReferenceRoot | Out-Null
$temporaryRoot = Join-Path $ReferenceRoot (".grch38-provision-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null

try {
    $archivePath = Join-Path $temporaryRoot "hg38.fa.gz"
    & curl.exe --fail --location --retry 3 --output $archivePath $sourceUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the GRCh38 reference from UCSC."
    }

    $volume = "{0}:/data" -f $temporaryRoot
    & docker run --rm -v $volume --entrypoint sh $Image -c "gzip -dc /data/hg38.fa.gz > /data/hg38.fa && samtools faidx /data/hg38.fa"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not decompress and index the GRCh38 reference."
    }

    $temporaryFasta = Join-Path $temporaryRoot "hg38.fa"
    $temporaryIndex = "$temporaryFasta.fai"
    if (!(Test-Path -LiteralPath $temporaryFasta) -or !(Test-Path -LiteralPath $temporaryIndex)) {
        throw "Reference provisioning completed without a FASTA and index."
    }

    $manifest = [ordered]@{
        assembly = $assembly
        source = "UCSC hg38"
        sourceUrl = $sourceUrl
        retrievedAt = [DateTime]::UtcNow.ToString("o")
        fastaFile = "hg38.fa"
        fastaSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporaryFasta).Hash.ToLowerInvariant()
        indexFile = "hg38.fa.fai"
        indexSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporaryIndex).Hash.ToLowerInvariant()
        normalizerImage = $Image
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $temporaryRoot "reference_manifest.json") -Encoding utf8
    Remove-Item -LiteralPath $archivePath -Force

    if (Test-Path -LiteralPath $assemblyRoot) {
        throw "Reference target appeared while provisioning; leaving the completed temporary reference untouched at $temporaryRoot."
    }
    Move-Item -LiteralPath $temporaryRoot -Destination $assemblyRoot
    Write-Output "Provisioned managed GRCh38 reference at $assemblyRoot"
}
catch {
    throw
}
