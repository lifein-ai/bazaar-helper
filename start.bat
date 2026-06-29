@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist "runtime" mkdir "runtime"
if not exist "runtime\deepseek_api_key.txt" type nul > "runtime\deepseek_api_key.txt"

if not exist "BazaarHelper.exe" (
    echo 没找到 BazaarHelper.exe
    echo 请确认这个 bat 和 BazaarHelper.exe 在同一个文件夹。
    pause
    exit /b 1
)

start "BazaarHelper" "%~dp0BazaarHelper.exe" --port 8765

timeout /t 1 >nul
start "" "http://127.0.0.1:8765"

echo The Bazaar AI 助手已启动：
echo http://127.0.0.1:8765
echo.
echo 如果要用 AI 分析，把 DeepSeek key 填到：
echo %~dp0runtime\deepseek_api_key.txt
echo.

endlocal