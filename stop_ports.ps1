# Stops processes listening on FastAPI / Vite ports (exact port match).
$ErrorActionPreference = 'SilentlyContinue'
$ports = @(8000, 5173, 5174, 5175, 5176, 5177)
foreach ($p in $ports) {
    Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $procId = $_.OwningProcess
        Write-Host "[stop] PID $procId  port $p"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "[stop] finished."
