param(
    [string]$PackagePath = "",
    [string]$ExpectedVersion = "",
    [string]$ExpectedSha256 = "",
    [switch]$Quiet,
    [switch]$Relaunch
)

$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalAppData = $env:LOCALAPPDATA
if (-not $LocalAppData) {
    $LocalAppData = Join-Path $env:USERPROFILE "AppData\Local"
}
$StateRoot = Join-Path $LocalAppData "BazaarHelper"
$UpdateRoot = Join-Path $StateRoot "updates"
$BackupRoot = Join-Path $StateRoot "backups"
$LogPath = Join-Path $StateRoot "update.log"

function Write-UpdateLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    try {
        New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    } catch {
        if (-not $Quiet) {
            Write-Host "Unable to write update log: $($_.Exception.Message)"
        }
    }
    if (-not $Quiet) {
        Write-Host $Message
    }
}

function Read-LocalVersion {
    param([string]$Root)
    $jsonPath = Join-Path $Root "version.json"
    if (Test-Path $jsonPath) {
        $versionInfo = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($versionInfo.version) {
            return [string]$versionInfo.version
        }
    }

    $textPath = Join-Path $Root "VERSION"
    if (Test-Path $textPath) {
        return (Get-Content -LiteralPath $textPath -Raw -Encoding UTF8).Trim()
    }

    return "0.0.0"
}

function Convert-ToVersion {
    param([string]$Value)
    $clean = ($Value -replace "^[vV]", "").Trim()
    try {
        return [version]$clean
    } catch {
        throw "Invalid version value: $Value"
    }
}

function Test-ZipSafety {
    param([string]$ZipPath)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace("\", "/")
            if (-not $name) {
                continue
            }
            if ($name.StartsWith("/") -or $name -match "^[A-Za-z]:") {
                throw "Unsafe absolute path in update package: $name"
            }
            $parts = $name.Split("/") | Where-Object { $_ -ne "" }
            if ($parts -contains "..") {
                throw "Unsafe path traversal in update package: $name"
            }
        }
    } finally {
        $zip.Dispose()
    }
}

function Get-PayloadRoot {
    param([string]$ExtractRoot)
    $required = @(
        "BazaarHelper.exe",
        "_internal",
        "data",
        "data\events.json",
        "data\cards_generated.json",
        "VERSION",
        "version.json",
        "start.bat",
        "update_helper.ps1",
        "install_update.bat"
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add($ExtractRoot)
    Get-ChildItem -LiteralPath $ExtractRoot -Directory | ForEach-Object {
        $candidates.Add($_.FullName)
    }

    foreach ($candidate in $candidates) {
        $missing = @()
        foreach ($relative in $required) {
            if (-not (Test-Path (Join-Path $candidate $relative))) {
                $missing += $relative
            }
        }
        if ($missing.Count -eq 0) {
            return $candidate
        }
    }

    throw "Update package does not match the BazaarHelper release structure."
}

function Copy-Payload {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot
    )
    Get-ChildItem -LiteralPath $SourceRoot -Force | ForEach-Object {
        $destination = Join-Path $DestinationRoot $_.Name
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse -Force
    }
}

function Remove-ReplacedProgramPaths {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot
    )
    $replaceNames = @("_internal", "data", "examples", "bepinex_plugin")
    foreach ($name in $replaceNames) {
        if (Test-Path (Join-Path $SourceRoot $name)) {
            $target = Join-Path $DestinationRoot $name
            if (Test-Path $target) {
                Remove-Item -LiteralPath $target -Recurse -Force
            }
        }
    }
}

function Get-InstalledGameDir {
    if ($env:BAZAAR_GAME_DIR -and (Test-Path $env:BAZAAR_GAME_DIR)) {
        return $env:BAZAAR_GAME_DIR
    }

    $runtimeGameDir = Join-Path $StateRoot "runtime\game_dir.txt"
    if (Test-Path $runtimeGameDir) {
        $stored = (Get-Content -LiteralPath $runtimeGameDir -Raw -Encoding UTF8).Trim()
        if ($stored -and (Test-Path $stored)) {
            return $stored
        }
    }

    return ""
}

function Set-PluginConfigValue {
    param(
        [string]$ConfigPath,
        [string]$Key,
        [string]$Value
    )

    if (-not (Test-Path $ConfigPath)) {
        return
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([string[]](Get-Content -LiteralPath $ConfigPath -Encoding UTF8))
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "$Key = $Value"
            $found = $true
            break
        }
    }
    if (-not $found) {
        $overlayIndex = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*\[Overlay\]\s*$") {
                $overlayIndex = $i
                break
            }
        }
        if ($overlayIndex -ge 0) {
            $insertAt = $overlayIndex + 1
            while ($insertAt -lt $lines.Count -and $lines[$insertAt] -notmatch "^\s*\[") {
                $insertAt++
            }
            $lines.Insert($insertAt, "$Key = $Value")
        }
    }
    [System.IO.File]::WriteAllLines($ConfigPath, $lines, [System.Text.UTF8Encoding]::new($false))
}

function Sync-GamePlugin {
    param([string]$PayloadRoot)

    $gameDir = Get-InstalledGameDir
    if (-not $gameDir) {
        Write-UpdateLog "Game directory is not recorded; run install_plugin.bat once to update the in-game plugin."
        return
    }

    $sourceDll = Join-Path $PayloadRoot "bepinex_plugin\BazaarStateExporter.dll"
    if (-not (Test-Path $sourceDll)) {
        Write-UpdateLog "Update package does not include the BepInEx plugin DLL; skipping game plugin sync."
        return
    }

    $pluginDir = Join-Path $gameDir "BepInEx\plugins\BazaarStateExporter"
    $configPath = Join-Path $gameDir "BepInEx\config\local.bazaar.stateexporter.cfg"
    New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
    Copy-Item -LiteralPath $sourceDll -Destination (Join-Path $pluginDir "BazaarStateExporter.dll") -Force

    Set-PluginConfigValue $configPath "HelperExecutablePath" (Join-Path $AppRoot "BazaarHelper.exe")
    Set-PluginConfigValue $configPath "ManualAnalysisKey" "F8"
    if (Test-Path $configPath) {
        $configText = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
        if ($configText -notmatch "(?m)^\s*AutoAnalyze\s*=") {
            Set-PluginConfigValue $configPath "AutoAnalyze" "false"
        }
    }
    Write-UpdateLog "Synced BepInEx plugin to game directory: $gameDir"
}

function Start-UpdatedApp {
    $exe = Join-Path $AppRoot "BazaarHelper.exe"
    if (Test-Path $exe) {
        Start-Process -FilePath $exe -ArgumentList "--port 8765 --api-only" -WorkingDirectory $AppRoot
    }
}

function Select-ManualPackage {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = "Select BazaarHelper update package"
        $dialog.Filter = "Zip update package (*.zip)|*.zip|All files (*.*)|*.*"
        $downloads = Join-Path $env:USERPROFILE "Downloads"
        if (Test-Path $downloads) {
            $dialog.InitialDirectory = $downloads
        }
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $dialog.FileName
        }
    } catch {
        Write-UpdateLog "Unable to open file picker: $($_.Exception.Message)"
    }

    if (-not $Quiet) {
        return Read-Host "Enter the full path of the BazaarHelper update zip"
    }
    return ""
}

if (-not $PackagePath) {
    Write-UpdateLog "No local update package was provided; opening manual selector."
    $PackagePath = Select-ManualPackage
    if (-not $PackagePath) {
        Write-UpdateLog "No update package selected; skipping update."
        exit 0
    }
}

if (-not $Quiet) {
    Write-Host ""
    Write-Host "Please close The Bazaar before continuing." -ForegroundColor Yellow
    Write-Host "The update may replace the BepInEx plugin DLL, which can be locked while the game is running."
    Read-Host "Press Enter to continue after closing the game"
}

New-Item -ItemType Directory -Path $UpdateRoot -Force | Out-Null
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$resolvedPackage = [System.IO.Path]::GetFullPath($PackagePath)
if (-not (Test-Path $resolvedPackage)) {
    throw "Update package not found: $resolvedPackage"
}

$currentVersionText = Read-LocalVersion $AppRoot
Write-UpdateLog "Current version: $currentVersionText"
Write-UpdateLog "Installing local update package: $resolvedPackage"

if ($ExpectedSha256) {
    $actualHash = (Get-FileHash -LiteralPath $resolvedPackage -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash = $ExpectedSha256.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA256 mismatch. Expected $expectedHash, got $actualHash."
    }
    Write-UpdateLog "Package hash verified."
}

Test-ZipSafety $resolvedPackage

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$extractRoot = Join-Path $UpdateRoot ("extract-manual-{0}" -f $timestamp)
if (Test-Path $extractRoot) {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

[System.IO.Compression.ZipFile]::ExtractToDirectory($resolvedPackage, $extractRoot)
$payloadRoot = Get-PayloadRoot $extractRoot
$payloadVersion = Read-LocalVersion $payloadRoot

if ($ExpectedVersion -and $payloadVersion -ne $ExpectedVersion) {
    throw "Package version $payloadVersion does not match expected version $ExpectedVersion."
}

if ((Convert-ToVersion $payloadVersion) -le (Convert-ToVersion $currentVersionText)) {
    throw "Package version $payloadVersion is not newer than current version $currentVersionText."
}

$backupPath = Join-Path $BackupRoot ("BazaarHelper-{0}-{1}" -f $currentVersionText, $timestamp)
Write-UpdateLog "Creating backup: $backupPath"
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
Copy-Payload $AppRoot $backupPath

Write-UpdateLog "Stopping running BazaarHelper.exe processes."
Get-Process -Name "BazaarHelper" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

try {
    Write-UpdateLog "Installing update version $payloadVersion."
    Remove-ReplacedProgramPaths $payloadRoot $AppRoot
    Copy-Payload $payloadRoot $AppRoot
    try {
        Sync-GamePlugin $AppRoot
    } catch {
        Write-UpdateLog "Game plugin sync failed; close the game and run install_plugin.bat if needed. Error: $($_.Exception.Message)"
    }
    Write-UpdateLog "Update complete: $currentVersionText -> $payloadVersion"
    if ($Relaunch) {
        Start-UpdatedApp
    }
    exit 0
} catch {
    Write-UpdateLog "Update failed, attempting rollback: $($_.Exception.Message)"
    try {
        Remove-ReplacedProgramPaths $backupPath $AppRoot
        Copy-Payload $backupPath $AppRoot
        Write-UpdateLog "Rollback complete."
    } catch {
        Write-UpdateLog "Rollback failed: $($_.Exception.Message)"
    }
    throw
}
