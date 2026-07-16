@echo off
setlocal

set "RUNTIME_DIR=%LOCALAPPDATA%\BazaarHelper\runtime"
set "KEY_FILE=%RUNTIME_DIR%\deepseek_api_key.txt"
set "LEGACY_KEY=%~dp0runtime\deepseek_api_key.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%KEY_FILE%" (
    if exist "%LEGACY_KEY%" (
        for %%A in ("%LEGACY_KEY%") do if %%~zA GTR 0 copy /Y "%LEGACY_KEY%" "%KEY_FILE%" >nul
    )
    if not exist "%KEY_FILE%" type nul > "%KEY_FILE%"
)
if exist "%KEY_FILE%" if exist "%LEGACY_KEY%" (
    for %%A in ("%KEY_FILE%") do if %%~zA EQU 0 for %%B in ("%LEGACY_KEY%") do if %%~zB GTR 0 copy /Y "%LEGACY_KEY%" "%KEY_FILE%" >nul
)

start "" notepad.exe "%KEY_FILE%"

endlocal
