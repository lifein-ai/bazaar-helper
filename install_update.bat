@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist "update_helper.ps1" (
    echo update_helper.ps1 was not found.
    echo Please run this script from the BazaarHelper folder.
    pause
    exit /b 1
)

echo Please close The Bazaar before installing an update.
echo The update may replace the BepInEx plugin DLL.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_helper.ps1" -Relaunch

if errorlevel 1 (
    echo.
    echo Update failed. See:
    echo %LOCALAPPDATA%\BazaarHelper\update.log
    pause
    exit /b 1
)

endlocal
