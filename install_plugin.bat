@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "DLL_SRC=%~dp0bepinex_plugin\BazaarStateExporter.dll"

if not defined LOCALAPPDATA (
    echo ERROR: LOCALAPPDATA is not available.
    pause
    exit /b 1
)

set "RUNTIME_DIR=%LOCALAPPDATA%\BazaarHelper\runtime"
set "OUTPUT_PATH=%RUNTIME_DIR%\game_state.json"
set "INSTALL_STATUS_PATH=%RUNTIME_DIR%\install_status.json"
set "GAME_DIR_PATH=%RUNTIME_DIR%\game_dir.txt"
set "HELPER_EXE=%~dp0BazaarHelper.exe"

if not exist "!RUNTIME_DIR!" mkdir "!RUNTIME_DIR!"
if not exist "!RUNTIME_DIR!" (
    echo ERROR: Cannot create runtime directory:
    echo !RUNTIME_DIR!
    pause
    exit /b 1
)

> "!RUNTIME_DIR!\.write_test" echo ok
if errorlevel 1 (
    echo ERROR: Runtime directory is not writable:
    echo !RUNTIME_DIR!
    pause
    exit /b 1
)
del /Q "!RUNTIME_DIR!\.write_test" >nul 2>nul

> "!INSTALL_STATUS_PATH!" echo {
>> "!INSTALL_STATUS_PATH!" echo   "source": "installer",
>> "!INSTALL_STATUS_PATH!" echo   "status": "installed"
>> "!INSTALL_STATUS_PATH!" echo }

if not exist "!DLL_SRC!" (
    echo ERROR: BazaarStateExporter.dll was not found:
    echo !DLL_SRC!
    echo.
    echo Please keep BazaarStateExporter.dll inside the bepinex_plugin folder.
    pause
    exit /b 1
)

echo Please enter The Bazaar game install directory.
echo Examples:
echo C:\Program Files (x86)\Steam\steamapps\common\The Bazaar
echo E:\SteamLibrary\steamapps\common\The Bazaar
echo.
set /p "GAME_DIR=Game directory: "

if not defined GAME_DIR (
    echo ERROR: No game directory was entered.
    pause
    exit /b 1
)

if not exist "!GAME_DIR!\BepInEx" (
    echo ERROR: BepInEx was not found:
    echo !GAME_DIR!\BepInEx
    echo.
    echo Install BepInEx for the game first, then run this installer again.
    pause
    exit /b 1
)

set "PLUGIN_DIR=!GAME_DIR!\BepInEx\plugins\BazaarStateExporter"
set "CONFIG_DIR=!GAME_DIR!\BepInEx\config"
set "CONFIG_FILE=!CONFIG_DIR!\local.bazaar.stateexporter.cfg"

if not exist "!PLUGIN_DIR!" mkdir "!PLUGIN_DIR!"
if not exist "!CONFIG_DIR!" mkdir "!CONFIG_DIR!"

copy /Y "!DLL_SRC!" "!PLUGIN_DIR!\BazaarStateExporter.dll" >nul
if errorlevel 1 (
    echo ERROR: Cannot copy plugin DLL to:
    echo !PLUGIN_DIR!\BazaarStateExporter.dll
    pause
    exit /b 1
)

set "BAZAAR_CONFIG_FILE=!CONFIG_FILE!"
set "BAZAAR_OUTPUT_PATH=!OUTPUT_PATH!"
set "BAZAAR_HELPER_EXE=!HELPER_EXE!"
set "BAZAAR_GAME_DIR_FILE=!GAME_DIR_PATH!"
set "BAZAAR_GAME_DIR=!GAME_DIR!"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$lines = @('[Export]', ('OutputPath = ' + $env:BAZAAR_OUTPUT_PATH), 'PollIntervalSeconds = 1', 'EnableHudResourceScanning = true', 'EnableVisibleCardScanning = true', '', '[Debug]', 'WritePlaceholderWhenEmpty = false', 'EnableRuntimeInspection = false', '', '[Overlay]', 'EnableInGameOverlay = true', 'HelperBaseUrl = http://127.0.0.1:8765', 'AutoStartHelper = true', ('HelperExecutablePath = ' + $env:BAZAAR_HELPER_EXE), 'IncludeAi = false', 'AutoAnalyze = false', 'ToggleKey = F7', 'LockToggleKey = F6', 'ManualAnalysisKey = F8', 'AiAnalysisKey = F5', 'Locked = true', 'RecommendationX = 16', 'RecommendationY = 56', 'RecommendationWidth = 500', 'RecommendationHeight = 620', 'BuildX = 532', 'BuildY = 56', 'BuildWidth = 400', 'BuildHeight = 620'); [System.IO.File]::WriteAllLines($env:BAZAAR_CONFIG_FILE, $lines, [System.Text.UTF8Encoding]::new($false)); [System.IO.File]::WriteAllText($env:BAZAAR_GAME_DIR_FILE, $env:BAZAAR_GAME_DIR, [System.Text.UTF8Encoding]::new($false))"
if errorlevel 1 (
    echo ERROR: Cannot write plugin config:
    echo !CONFIG_FILE!
    pause
    exit /b 1
)

echo.
echo Install complete.
echo Plugin copied to:
echo !PLUGIN_DIR!\BazaarStateExporter.dll
echo.
echo Live state file:
echo !OUTPUT_PATH!
echo.
echo The plugin creates this file after it loads in the game.
echo Start/restart the game and enter a run.
echo The in-game overlay will start BazaarHelper automatically.
echo If automatic startup is blocked, run start.bat manually as a fallback.
pause

endlocal
