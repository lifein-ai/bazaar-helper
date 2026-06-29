@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "DLL_SRC=%~dp0bepinex_plugin\BazaarStateExporter.dll"
set "OUTPUT_PATH=%~dp0runtime\game_state.json"

if not exist "runtime" mkdir "runtime"

if not exist "%DLL_SRC%" (
    echo 没找到插件 DLL：
    echo %DLL_SRC%
    echo.
    echo 请确认 BazaarStateExporter.dll 放在 bepinex_plugin 文件夹里。
    pause
    exit /b 1
)

echo 请输入 The Bazaar 游戏安装目录。
echo 例如：
echo C:\Program Files (x86)\Steam\steamapps\common\The Bazaar
echo E:\SteamLibrary\steamapps\common\The Bazaar
echo.
set /p "GAME_DIR=游戏目录: "

if "%GAME_DIR%"=="" (
    echo 没有输入游戏目录。
    pause
    exit /b 1
)

if not exist "%GAME_DIR%\BepInEx" (
    echo 没检测到 BepInEx：
    echo %GAME_DIR%\BepInEx
    echo.
    echo 请先给游戏安装 BepInEx，再运行这个脚本。
    pause
    exit /b 1
)

set "PLUGIN_DIR=%GAME_DIR%\BepInEx\plugins\BazaarStateExporter"
set "CONFIG_DIR=%GAME_DIR%\BepInEx\config"
set "CONFIG_FILE=%CONFIG_DIR%\local.bazaar.stateexporter.cfg"

if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"

copy /Y "%DLL_SRC%" "%PLUGIN_DIR%\BazaarStateExporter.dll" >nul

> "%CONFIG_FILE%" echo [Export]
>> "%CONFIG_FILE%" echo OutputPath = %OUTPUT_PATH%
>> "%CONFIG_FILE%" echo PollIntervalSeconds = 1
>> "%CONFIG_FILE%" echo.
>> "%CONFIG_FILE%" echo [Debug]
>> "%CONFIG_FILE%" echo WritePlaceholderWhenEmpty = false
>> "%CONFIG_FILE%" echo EnableRuntimeInspection = false

echo.
echo 安装完成。
echo 插件已复制到：
echo %PLUGIN_DIR%\BazaarStateExporter.dll
echo.
echo 插件输出路径已设置为：
echo %OUTPUT_PATH%
echo.
echo 现在启动游戏，再双击 start.bat 打开助手。
pause

endlocal