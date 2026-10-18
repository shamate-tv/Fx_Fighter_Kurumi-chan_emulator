# 久留美模拟器 · 一键启动（Windows PowerShell）
# 用法：双击 start.bat，或在项目根目录执行  .\start.ps1
# 可选：-NoBrowser 不自动打开浏览器
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'
$frontend = Join-Path $root 'frontend'

function Test-Port([int]$port) {
    try {
        $client = New-Object Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Host ''
Write-Host '  FX 战士久留美 · 模拟器' -ForegroundColor Magenta
Write-Host '  ========================' -ForegroundColor DarkGray
Write-Host ''

# ---- 后端环境检查 ----
if (-not (Test-Path $python)) {
    Write-Host '  [错误] 未找到 backend\.venv，请先安装后端依赖：' -ForegroundColor Red
    Write-Host '    cd backend' -ForegroundColor Yellow
    Write-Host '    python -m venv .venv' -ForegroundColor Yellow
    Write-Host '    .\.venv\Scripts\pip install -r requirements.txt' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按 Enter 退出'
    exit 1
}

# ---- 后端 ----
if (Test-Port 8000) {
    Write-Host '  [跳过] 后端已在 127.0.0.1:8000 运行' -ForegroundColor Yellow
} else {
    Write-Host '  [启动] 后端 FastAPI → 127.0.0.1:8000' -ForegroundColor Cyan
    Start-Process -FilePath $python `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory (Join-Path $root 'backend') `
        -WindowStyle Normal
}

# ---- 前端依赖 ----
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host '  [安装] 前端依赖（首次运行，需要几分钟）…' -ForegroundColor Cyan
    Push-Location $frontend
    npm install
    Pop-Location
}

# ---- 前端 ----
if (Test-Port 5173) {
    Write-Host '  [跳过] 前端已在 127.0.0.1:5173 运行' -ForegroundColor Yellow
} else {
    Write-Host '  [启动] 前端 Vite → 127.0.0.1:5173' -ForegroundColor Cyan
    Start-Process -FilePath 'npm.cmd' `
        -ArgumentList 'run', 'dev' `
        -WorkingDirectory $frontend `
        -WindowStyle Normal
}

# ---- 等待服务就绪 ----
Write-Host '  [等待] 服务启动中…' -ForegroundColor DarkGray
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    if ((Test-Port 8000) -and (Test-Port 5173)) {
        $ready = $true
        break
    }
    Start-Sleep -Milliseconds 500
}

if ($ready) {
    Write-Host '  [就绪] http://127.0.0.1:5173/' -ForegroundColor Green
    if (-not $NoBrowser) {
        Start-Process 'http://127.0.0.1:5173/'
    }
} else {
    Write-Host '  [提示] 服务可能还在启动，请查看弹出的后端/前端窗口日志' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '  关闭：直接关掉后端和前端两个窗口，或运行 .\stop.ps1' -ForegroundColor DarkGray
Write-Host ''
