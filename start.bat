@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "RUNTIME_DIR=%LOCALAPPDATA%\BazaarHelper\runtime"
set "KEY_FILE=%RUNTIME_DIR%\deepseek_api_key.txt"
set "BUNDLED_KEY=%~dp0bundled_ai_key.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%KEY_FILE%" (
    if exist "%BUNDLED_KEY%" (
        copy /Y "%BUNDLED_KEY%" "%KEY_FILE%" >nul
    ) else (
        type nul > "%KEY_FILE%"
    )
)
if exist "%KEY_FILE%" if exist "%BUNDLED_KEY%" (
    for %%A in ("%KEY_FILE%") do if %%~zA EQU 0 copy /Y "%BUNDLED_KEY%" "%KEY_FILE%" >nul
)

if not exist "BazaarHelper.exe" (
    echo BazaarHelper.exe was not found.
    echo Please run this script from the release folder that contains BazaarHelper.exe.
    pause
    exit /b 1
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8765 .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
)
taskkill /IM BazaarHelper.exe /F >nul 2>&1
start "BazaarHelper" "%~dp0BazaarHelper.exe" --port 8765 --api-only

echo BazaarHelper local API service started for the in-game overlay:
echo http://127.0.0.1:8765/api/analysis
echo The browser UI is no longer opened automatically.
echo.
echo To use AI analysis, put your DeepSeek key here:
echo %RUNTIME_DIR%\deepseek_api_key.txt
echo.

endlocal
