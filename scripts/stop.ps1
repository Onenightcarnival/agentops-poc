$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path 'logs/server.pid') {
    $serverPid = [int](Get-Content 'logs/server.pid')
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if ($process -and $process.CommandLine -like '*uvicorn app.main:app*' -and $process.CommandLine -like '*18080*') {
        try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18080/telemetry/flush' | Out-Null } catch { Write-Warning 'Flush unavailable' }
        # Windows venv launchers may spawn the actual Python interpreter.
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $serverPid" | Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' -and $_.CommandLine -like '*18080*' }
        foreach ($child in $children) { Stop-Process -Id $child.ProcessId }
        Stop-Process -Id $serverPid
        Write-Output "Stopped PID $serverPid"
    }
}
