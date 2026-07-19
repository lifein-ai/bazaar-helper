@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "RUNTIME_DIR=%LOCALAPPDATA%\BazaarHelper\runtime"
set "KEY_FILE=%RUNTIME_DIR%\deepseek_api_key.txt"
set "HELPER_PATH_FILE=%RUNTIME_DIR%\helper_path.txt"
set "HELPER_EXE=%~dp0BazaarHelper.exe"
set "BUNDLED_KEY=%~dp0bundled_ai_key.txt"
set "LEGACY_KEY=%~dp0runtime\deepseek_api_key.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%KEY_FILE%" (
    if exist "%LEGACY_KEY%" (
        for %%A in ("%LEGACY_KEY%") do if %%~zA GTR 0 copy /Y "%LEGACY_KEY%" "%KEY_FILE%" >nul
    )
    if not exist "%KEY_FILE%" if exist "%BUNDLED_KEY%" (
        copy /Y "%BUNDLED_KEY%" "%KEY_FILE%" >nul
    )
    if not exist "%KEY_FILE%" (
        type nul > "%KEY_FILE%"
    )
)
if exist "%KEY_FILE%" if exist "%LEGACY_KEY%" (
    for %%A in ("%KEY_FILE%") do if %%~zA EQU 0 for %%B in ("%LEGACY_KEY%") do if %%~zB GTR 0 copy /Y "%LEGACY_KEY%" "%KEY_FILE%" >nul
)
if exist "%KEY_FILE%" if exist "%BUNDLED_KEY%" (
    for %%A in ("%KEY_FILE%") do if %%~zA EQU 0 copy /Y "%BUNDLED_KEY%" "%KEY_FILE%" >nul
)

if not exist "%HELPER_EXE%" (
    echo BazaarHelper.exe was not found.
    echo Please run this script from the release folder that contains BazaarHelper.exe.
    pause
    exit /b 1
)

set "BAZAAR_HELPER_PATH_FILE=%HELPER_PATH_FILE%"
set "BAZAAR_HELPER_EXE=%HELPER_EXE%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[System.IO.File]::WriteAllText($env:BAZAAR_HELPER_PATH_FILE, $env:BAZAAR_HELPER_EXE, [System.Text.UTF8Encoding]::new($false))" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -UseBasicParsing -Uri 'http://127.0.0.1:8765/' -TimeoutSec 1; if ($r.ok -eq $true -and $r.mode -eq 'api-only' -and $r.analysis_endpoint -eq '/api/analysis') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo BazaarHelper local API service is already running:
    echo http://127.0.0.1:8765/api/analysis
    echo No restart was needed.
    echo.
    endlocal
    exit /b 0
)

taskkill /IM BazaarHelper.exe /F >nul 2>&1
start "BazaarHelper" "%HELPER_EXE%" --port 8765 --api-only

echo BazaarHelper local API service started for the in-game overlay:
echo http://127.0.0.1:8765/api/analysis
echo The browser UI is no longer opened automatically.
echo.
echo To use AI analysis, put your DeepSeek key here:
echo %RUNTIME_DIR%\deepseek_api_key.txt
echo.

endlocal
