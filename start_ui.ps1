$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildPythonPathFile = Join-Path $ProjectRoot "build_python_path.txt"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$RuntimeDir = Join-Path $env:LOCALAPPDATA "BazaarHelper\runtime"
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

$Python = ""
if (Test-Path $BuildPythonPathFile) {
    $ConfiguredPython = (Get-Content -LiteralPath $BuildPythonPathFile -Raw -Encoding UTF8).Trim()
    if ($ConfiguredPython -and (Test-Path $ConfiguredPython)) {
        $Python = $ConfiguredPython
    }
}
if (-not $Python -and (Test-Path $BundledPython)) {
    $Python = $BundledPython
}
if (-not $Python) {
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
    -ArgumentList "src\web_app.py --port $Port --api-only" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "The Bazaar AI 助手已启动：" -ForegroundColor Green
Write-Host "$Url/api/analysis"
Write-Host "Browser UI is deprecated and is no longer opened."
Write-Host ""

if ((Get-Item $KeyFile).Length -eq 0) {
    Write-Host "提示：DeepSeek key 文件还是空的：" -ForegroundColor Yellow
    Write-Host $KeyFile
    Write-Host "如果要用 AI 分析，把 key 直接粘进去，只保留一行。"
}
