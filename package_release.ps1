param(
    [string]$GameDir = $env:BAZAAR_GAME_DIR,
    [switch]$SkipGamePluginSync
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseRoot = Join-Path $ProjectRoot "release\BazaarHelper"
$DistRoot = Join-Path $ProjectRoot "dist\BazaarHelper"
$GuidesRoot = Join-Path $ProjectRoot "guides"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$BuildPythonPathFile = Join-Path $ProjectRoot "build_python_path.txt"
$InternalTestKey = Join-Path $ProjectRoot "runtime\deepseek_api_key.txt"
$VersionFile = Join-Path $ProjectRoot "VERSION"
$RuntimeGameDirFile = Join-Path $env:LOCALAPPDATA "BazaarHelper\runtime\game_dir.txt"

Set-Location $ProjectRoot

function Test-BuildPython {
    param(
        [string]$PythonExe,
        [string[]]$PythonArgs = @()
    )

    if (-not $PythonExe) {
        return $false
    }

    try {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
        & $PythonExe @PythonArgs -c "import socket, pytest, PyInstaller" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        if (Get-Variable -Name previousNativeErrorPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
    }
}

function Resolve-BuildPython {
    $candidates = New-Object System.Collections.Generic.List[object]
    if (Test-Path $BuildPythonPathFile) {
        $configuredPython = (Get-Content -LiteralPath $BuildPythonPathFile -Raw -Encoding UTF8).Trim()
        if ($configuredPython) {
            $candidates.Add([pscustomobject]@{
                Exe = $configuredPython
                Args = @()
                Label = "$configuredPython (build_python_path.txt)"
            })
        }
    }
    if ($env:BAZAAR_HELPER_BUILD_PYTHON) {
        $candidates.Add([pscustomobject]@{
            Exe = $env:BAZAAR_HELPER_BUILD_PYTHON
            Args = @()
            Label = $env:BAZAAR_HELPER_BUILD_PYTHON
        })
    }
    $candidates.Add([pscustomobject]@{
        Exe = $VenvPython
        Args = @()
        Label = $VenvPython
    })
    $systemPython = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($systemPython -and $systemPython.Source) {
        $candidates.Add([pscustomobject]@{
            Exe = $systemPython.Source
            Args = @()
            Label = $systemPython.Source
        })
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyLauncher -and $pyLauncher.Source) {
        $candidates.Add([pscustomobject]@{
            Exe = $pyLauncher.Source
            Args = @("-3")
            Label = "$($pyLauncher.Source) -3"
        })
    }
    $candidates.Add([pscustomobject]@{
        Exe = $BundledPython
        Args = @()
        Label = $BundledPython
    })

    foreach ($candidate in $candidates) {
        if (Test-BuildPython $candidate.Exe $candidate.Args) {
            return $candidate
        }
    }

    throw @"
No usable packaging Python was found.
Need a Python that can import: socket, pytest, PyInstaller.

Recommended setup:
  C:\Path\To\python.exe -m pip install pytest pyinstaller openpyxl et_xmlfile
  Set-Content build_python_path.txt 'C:\Path\To\python.exe' -Encoding UTF8

Or use a virtual environment:
  py -3.13 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install pytest pyinstaller openpyxl et_xmlfile

Or set:
  `$env:BAZAAR_HELPER_BUILD_PYTHON='C:\Path\To\python.exe'
"@
}

$BuildPython = Resolve-BuildPython
$BuildPythonExe = $BuildPython.Exe
$BuildPythonArgs = [string[]]$BuildPython.Args
Write-Host "Using packaging Python: $($BuildPython.Label)"
if (-not (Test-Path $BuildPythonPathFile) -and $BuildPythonArgs.Count -eq 0) {
    try {
        Set-Content -LiteralPath $BuildPythonPathFile -Value $BuildPythonExe -Encoding UTF8
        Write-Host "Saved packaging Python path to: $BuildPythonPathFile"
    } catch {
        Write-Host "Unable to save packaging Python path: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if (-not (Test-Path $VersionFile)) {
    throw "Version file not found: $VersionFile"
}
$ReleaseVersion = (Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8).Trim()

Write-Host "[1/6] Rebuilding generated event data..."
& $BuildPythonExe @BuildPythonArgs .\scripts\build_events_from_encounters.py `
    --encounters .\data\encounters_generated.json `
    --output .\data\events.json
if ($LASTEXITCODE -ne 0) {
    throw "Event data build failed."
}
if (-not (Test-Path (Join-Path $ProjectRoot "data\events.json"))) {
    throw "Generated event data missing: data\events.json"
}

Write-Host "[2/6] Running release tests..."
$PytestTemp = Join-Path $ProjectRoot ".tmp\pytest-release"
if (Test-Path $PytestTemp) {
    $resolvedPytestTemp = [System.IO.Path]::GetFullPath($PytestTemp)
    $expectedPytestTemp = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".tmp\pytest-release"))
    if ($resolvedPytestTemp -ne $expectedPytestTemp) {
        throw "Unexpected pytest temp path: $resolvedPytestTemp"
    }
    Remove-Item -LiteralPath $resolvedPytestTemp -Recurse -Force
}
New-Item -ItemType Directory -Path $PytestTemp -Force | Out-Null
& $BuildPythonExe @BuildPythonArgs -m pytest -q tests\test_app_paths.py tests\test_web_app.py `
    tests\test_guide_retriever.py `
    tests\test_update_manager.py `
    tests\test_build_simulation_evaluator.py `
    tests\test_combat_simulator.py `
    -p no:cacheprovider --basetemp $PytestTemp
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed."
}

Write-Host "[3/6] Building BepInEx plugin..."
dotnet build .\bepinex\BazaarStateExporter\BazaarStateExporter.csproj -c Release
if ($LASTEXITCODE -ne 0) {
    throw "BepInEx plugin build failed."
}

Write-Host "[4/6] Building BazaarHelper.exe..."
& $BuildPythonExe @BuildPythonArgs -m PyInstaller --noconfirm .\BazaarHelper.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Write-Host "[5/6] Assembling complete release folder..."
& $BuildPythonExe @BuildPythonArgs .\scripts\build_user_guide.py
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
if (Test-Path $GuidesRoot) {
    Copy-Item -LiteralPath $GuidesRoot -Destination $ReleaseRoot -Recurse
} else {
    New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "guides") | Out-Null
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot "examples") -Destination $ReleaseRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "start.bat") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "update_helper.ps1") -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "install_update.bat") -Destination $ReleaseRoot
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

Write-Host "[6/6] Verifying release files..."
$CommonGuidesDir = "guides\" + [string]([char]0x901A) + [string]([char]0x7528)
$requiredPaths = @(
    "BazaarHelper.exe",
    "_internal",
    "data",
    "data\cardpacks.json",
    "data\events.json",
    "data\cards_generated.json",
    "data\encounters_generated.json",
    "data\skills_generated.json",
    "data\translations_zh_cn.json",
    "guides",
    "guides\Dooley",
    "guides\Jules",
    "guides\Karnok",
    "guides\Mak",
    "guides\Pygmalien",
    "guides\Stelle",
    "guides\Vanessa",
    $CommonGuidesDir,
    "examples",
    "start.bat",
    "update_helper.ps1",
    "install_update.bat",
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
