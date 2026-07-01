@echo off
setlocal

cd /d "%~dp0"

set "DLL_SRC=%~dp0bepinex_plugin\BazaarStateExporter.dll"

if "%LOCALAPPDATA%"=="" (
    echo ERROR: LOCALAPPDATA is not available.
    pause
    exit /b 1
)

set "RUNTIME_DIR=%LOCALAPPDATA%\BazaarHelper\runtime"
set "OUTPUT_PATH=%RUNTIME_DIR%\game_state.json"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%RUNTIME_DIR%" (
    echo ERROR: Cannot create runtime directory:
    echo %RUNTIME_DIR%
    pause
    exit /b 1
)

> "%RUNTIME_DIR%\.write_test" echo ok
if errorlevel 1 (
    echo ERROR: Runtime directory is not writable:
    echo %RUNTIME_DIR%
    pause
    exit /b 1
)
del /Q "%RUNTIME_DIR%\.write_test" >nul 2>nul

if not exist "%OUTPUT_PATH%" (
    > "%OUTPUT_PATH%" echo {
    >> "%OUTPUT_PATH%" echo   "source": "installer",
    >> "%OUTPUT_PATH%" echo   "status": "waiting_for_game",
    >> "%OUTPUT_PATH%" echo   "message": "Bazaar State Exporter is installed. Start the game and enter a run to generate live state."
    >> "%OUTPUT_PATH%" echo }
)

if not exist "%DLL_SRC%" (
    echo ERROR: BazaarStateExporter.dll was not found:
    echo %DLL_SRC%
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

if "%GAME_DIR%"=="" (
    echo ERROR: No game directory was entered.
    pause
    exit /b 1
)

if not exist "%GAME_DIR%\BepInEx" (
    echo ERROR: BepInEx was not found:
    echo %GAME_DIR%\BepInEx
    echo.
    echo Install BepInEx for the game first, then run this installer again.
    pause
    exit /b 1
)

set "PLUGIN_DIR=%GAME_DIR%\BepInEx\plugins\BazaarStateExporter"
set "CONFIG_DIR=%GAME_DIR%\BepInEx\config"
set "CONFIG_FILE=%CONFIG_DIR%\local.bazaar.stateexporter.cfg"

if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"

copy /Y "%DLL_SRC%" "%PLUGIN_DIR%\BazaarStateExporter.dll" >nul
if errorlevel 1 (
    echo ERROR: Cannot copy plugin DLL to:
    echo %PLUGIN_DIR%\BazaarStateExporter.dll
    pause
    exit /b 1
)

> "%CONFIG_FILE%" echo [Export]
>> "%CONFIG_FILE%" echo OutputPath = %OUTPUT_PATH%
>> "%CONFIG_FILE%" echo PollIntervalSeconds = 1
>> "%CONFIG_FILE%" echo.
>> "%CONFIG_FILE%" echo [Debug]
>> "%CONFIG_FILE%" echo WritePlaceholderWhenEmpty = false
>> "%CONFIG_FILE%" echo EnableRuntimeInspection = false
>> "%CONFIG_FILE%" echo EnableVisibleCardScanning = true
if errorlevel 1 (
    echo ERROR: Cannot write plugin config:
    echo %CONFIG_FILE%
    pause
    exit /b 1
)

echo.
echo Install complete.
echo Plugin copied to:
echo %PLUGIN_DIR%\BazaarStateExporter.dll
echo.
echo Live state file:
echo %OUTPUT_PATH%
echo.
echo If the file still says source=installer, start/restart the game and enter a run.
echo Then run start.bat to open BazaarHelper.
pause

endlocal
