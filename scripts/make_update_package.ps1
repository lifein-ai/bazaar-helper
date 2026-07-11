param(
    [string]$ReleaseRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "release\BazaarHelper"),
    [string]$OutputRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "releases"),
    [string[]]$Changelog = @()
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ReleaseRoot)) {
    throw "Release folder not found: $ReleaseRoot"
}

$versionPath = Join-Path $ReleaseRoot "version.json"
if (Test-Path $versionPath) {
    $versionInfo = Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $version = [string]$versionInfo.version
} else {
    $version = (Get-Content -LiteralPath (Join-Path $ReleaseRoot "VERSION") -Raw -Encoding UTF8).Trim()
}

if (-not $version) {
    throw "Unable to determine release version."
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$manifest = [ordered]@{
    name = "BazaarHelper"
    version = $version
    download_url = "TODO: paste Quark share link here"
    sha256 = ""
    changelog = $Changelog
    force_update = $false
    published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$manifestPath = Join-Path $OutputRoot "latest.template.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "Manifest template created:" -ForegroundColor Green
Write-Host $manifestPath
Write-Host ""
Write-Host "Compress release\BazaarHelper yourself, upload it to Quark, then paste the Quark link into download_url."
