# 久留美模拟器 · 一键停止
# 结束占用 8000（后端）与 5173（前端）的进程
$ErrorActionPreference = 'SilentlyContinue'

foreach ($port in 8000, 5173) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        if ($procId) {
            Write-Host "  停止端口 $port 的进程 (PID $procId)" -ForegroundColor Yellow
            Stop-Process -Id $procId -Force
        }
    }
}

Start-Sleep -Milliseconds 500
Write-Host '  已停止（8000 / 5173）' -ForegroundColor Green
