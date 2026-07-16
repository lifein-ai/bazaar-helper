param(
    [string]$ReleaseRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "release\BazaarHelper"),
    [string]$OutputRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "releases"),
    [string]$DownloadUrl = "TODO: paste Quark share link here",
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

$releaseItem = Get-Item -LiteralPath $ReleaseRoot
$packageName = "BazaarHelper-$version.zip"
$packagePath = Join-Path $OutputRoot $packageName
if (Test-Path $packagePath) {
    Remove-Item -LiteralPath $packagePath -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open(
    $packagePath,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -Force | ForEach-Object {
        if (-not $_.PSIsContainer) {
            $relative = $_.FullName.Substring($releaseItem.FullName.Length).TrimStart("\", "/")
            $entryName = ("BazaarHelper/" + $relative).Replace("\", "/")
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
} finally {
    $zip.Dispose()
}

$sha256 = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()

$manifest = [ordered]@{
    name = "BazaarHelper"
    version = $version
    download_url = $DownloadUrl
    sha256 = $sha256
    changelog = $Changelog
    force_update = $false
    published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$manifestPath = Join-Path $OutputRoot "latest.template.json"
$manifestJson = $manifest | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $manifestPath,
    $manifestJson + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Update package created:" -ForegroundColor Green
Write-Host $packagePath
Write-Host "SHA256: $sha256"
Write-Host ""
Write-Host "Manifest template created:" -ForegroundColor Green
Write-Host $manifestPath
Write-Host ""
Write-Host "Upload $packageName to Quark, then paste the share link into download_url."
