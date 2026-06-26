$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$KeyFile = Join-Path $RuntimeDir "deepseek_api_key.txt"
$Port = 8765
$Url = "http://127.0.0.1:$Port"

Set-Location $ProjectRoot

if (-not (Test-Path $RuntimeDir)) {
    New-Item -ItemType Directory -Path $RuntimeDir | Out-Null
}

if (-not (Test-Path $KeyFile)) {
    New-Item -ItemType File -Path $KeyFile | Out-Null
}

$Python = $BundledPython
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$OldProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -like "*src\web_app.py*" -or
        $_.CommandLine -like "*src/web_app.py*"
    }

foreach ($Process in $OldProcesses) {
    Stop-Process -Id $Process.ProcessId -Force
}

Start-Process `
    -FilePath $Python `
    -ArgumentList "src\web_app.py --port $Port" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden

Start-Sleep -Seconds 1
Start-Process $Url

Write-Host ""
Write-Host "The Bazaar AI 助手已启动：" -ForegroundColor Green
Write-Host $Url
Write-Host ""

if ((Get-Item $KeyFile).Length -eq 0) {
    Write-Host "提示：DeepSeek key 文件还是空的：" -ForegroundColor Yellow
    Write-Host $KeyFile
    Write-Host "如果要用 AI 分析，把 key 直接粘进去，只保留一行。"
}
