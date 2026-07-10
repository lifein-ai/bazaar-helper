param(
    [string]$GameDir = $env:BAZAAR_GAME_DIR,
    [switch]$SkipGamePluginSync
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseRoot = Join-Path $ProjectRoot "release\BazaarHelper"
$DistRoot = Join-Path $ProjectRoot "dist\BazaarHelper"
$VenvSitePackages = Join-Path $ProjectRoot ".venv\Lib\site-packages"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$InternalTestKey = Join-Path $ProjectRoot "runtime\deepseek_api_key.txt"
$VersionFile = Join-Path $ProjectRoot "VERSION"
$RuntimeGameDirFile = Join-Path $env:LOCALAPPDATA "BazaarHelper\runtime\game_dir.txt"

Set-Location $ProjectRoot

if (-not (Test-Path $BundledPython)) {
    throw "Python not found: $BundledPython"
}
if (-not (Test-Path $VersionFile)) {
    throw "Version file not found: $VersionFile"
}
$ReleaseVersion = (Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8).Trim()

$env:PYTHONPATH = $VenvSitePackages

Write-Host "[1/5] Running release tests..."
$PytestTemp = Join-Path $ProjectRoot ".tmp\pytest-release"
New-Item -ItemType Directory -Path $PytestTemp -Force | Out-Null
& $BundledPython -m pytest -q tests\test_app_paths.py tests\test_web_app.py `
    -p no:cacheprovider --basetemp $PytestTemp
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed."
}

Write-Host "[2/5] Building BepInEx plugin..."
dotnet build .\bepinex\BazaarStateExporter\BazaarStateExporter.csproj -c Release
if ($LASTEXITCODE -ne 0) {
    throw "BepInEx plugin build failed."
}

Write-Host "[3/5] Building BazaarHelper.exe..."
& $BundledPython -m PyInstaller --noconfirm .\BazaarHelper.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Write-Host "[4/5] Assembling complete release folder..."
& $BundledPython .\scripts\build_user_guide.py
if ($LASTEXITCODE -ne 0) {
    throw "User guide build failed."
}
if (Test-Path $ReleaseRoot) {
    $resolvedRelease = [System.IO.Path]::GetFullPath($ReleaseRoot)
    $expectedRelease = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "release\BazaarHelper"))
    if ($resolvedRelease -ne $expectedRelease) {
        throw "Unexpected release path: $resolvedRelease"
    }
    Remove-Item -LiteralPath $resolvedRelease -Recurse -Force
}

New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "bepinex_plugin") | Out-Null

Copy-Item -LiteralPath (Join-Path $DistRoot "BazaarHelper.exe") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $DistRoot "_internal") -Destination $ReleaseRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "data") -Destination $ReleaseRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "examples") -Destination $ReleaseRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "start.bat") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "update_helper.ps1") -Destination $ReleaseRoot
Copy-Item -LiteralPath $VersionFile -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "install_plugin.bat") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "set_ai_key.bat") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs\BazaarHelper_User_Guide.docx") -Destination $ReleaseRoot
if (-not (Test-Path $InternalTestKey) -or (Get-Item $InternalTestKey).Length -eq 0) {
    throw "Internal test API key is missing or empty: $InternalTestKey"
}
Copy-Item -LiteralPath $InternalTestKey -Destination (Join-Path $ReleaseRoot "bundled_ai_key.txt")
Copy-Item `
    -LiteralPath (Join-Path $ProjectRoot "bepinex\BazaarStateExporter\bin\Release\net472\BazaarStateExporter.dll") `
    -Destination (Join-Path $ReleaseRoot "bepinex_plugin\BazaarStateExporter.dll")

$versionInfo = [ordered]@{
    name = "BazaarHelper"
    version = $ReleaseVersion
    built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$versionInfo | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ReleaseRoot "version.json") -Encoding UTF8

Write-Host "[5/5] Verifying release files..."
$requiredPaths = @(
    "BazaarHelper.exe",
    "_internal",
    "data",
    "examples",
    "start.bat",
    "update_helper.ps1",
    "VERSION",
    "version.json",
    "install_plugin.bat",
    "set_ai_key.bat",
    "BazaarHelper_User_Guide.docx",
    "bundled_ai_key.txt",
    "bepinex_plugin\BazaarStateExporter.dll"
)

foreach ($relativePath in $requiredPaths) {
    $fullPath = Join-Path $ReleaseRoot $relativePath
    if (-not (Test-Path $fullPath)) {
        throw "Release file missing: $fullPath"
    }
}

if (-not $SkipGamePluginSync) {
    if (-not $GameDir -and (Test-Path $RuntimeGameDirFile)) {
        $GameDir = (Get-Content -LiteralPath $RuntimeGameDirFile -Raw -Encoding UTF8).Trim()
    }

    if ($GameDir) {
        if (-not (Test-Path (Join-Path $GameDir "BepInEx"))) {
            throw "GameDir does not contain BepInEx: $GameDir"
        }

        $pluginDir = Join-Path $GameDir "BepInEx\plugins\BazaarStateExporter"
        $configPath = Join-Path $GameDir "BepInEx\config\local.bazaar.stateexporter.cfg"
        New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
        Copy-Item `
            -LiteralPath (Join-Path $ReleaseRoot "bepinex_plugin\BazaarStateExporter.dll") `
            -Destination (Join-Path $pluginDir "BazaarStateExporter.dll") `
            -Force
        if (Test-Path $configPath) {
            $lines = [System.Collections.Generic.List[string]]::new()
            $lines.AddRange([string[]](Get-Content -LiteralPath $configPath -Encoding UTF8))
            $manualKeyFound = $false
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match "^\s*ManualAnalysisKey\s*=") {
                    $lines[$i] = "ManualAnalysisKey = F8"
                    $manualKeyFound = $true
                    break
                }
            }
            if (-not $manualKeyFound) {
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
                    $lines.Insert($insertAt, "ManualAnalysisKey = F8")
                }
            }
            $autoAnalyzeFound = $false
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match "^\s*AutoAnalyze\s*=") {
                    $autoAnalyzeFound = $true
                    break
                }
            }
            if (-not $autoAnalyzeFound) {
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
                    $lines.Insert($insertAt, "AutoAnalyze = false")
                }
            }
            [System.IO.File]::WriteAllLines($configPath, $lines, [System.Text.UTF8Encoding]::new($false))
        }
        Write-Host "Synced BepInEx plugin to game directory:" -ForegroundColor Green
        Write-Host $GameDir
    } else {
        Write-Host "Game directory not recorded; release was packaged but the in-game plugin was not installed." -ForegroundColor Yellow
        Write-Host "Run release\BazaarHelper\install_plugin.bat once, or rerun package_release.ps1 -GameDir '<The Bazaar folder>'."
    }
}

Write-Host ""
Write-Host "Complete release package created:" -ForegroundColor Green
Write-Host $ReleaseRoot
