$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force logs | Out-Null
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uv = $uvCommand.Source
} else {
    $uv = Join-Path $env:APPDATA 'Python\Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) { throw 'Install uv and add it to PATH first.' }
}
if (Test-Path 'logs/server.pid') {
    $serverPid = [int](Get-Content 'logs/server.pid')
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if ($existing -and $existing.CommandLine -like '*uvicorn app.main:app*') {
        Write-Output "Already running: PID $serverPid"
        exit 0
    }
}
$process = Start-Process -FilePath $uv -ArgumentList 'run','--locked','python','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18080' -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru -RedirectStandardOutput 'logs/server.out.log' -RedirectStandardError 'logs/server.err.log'
$process.Id | Set-Content 'logs/launcher.pid'
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18080/health' -TimeoutSec 2
        if ($health.status -eq 'ok') {
            Write-Output "Ready: http://127.0.0.1:18080/docs"
            exit 0
        }
    } catch { }
    if ($process.HasExited) { throw 'Server exited. Inspect logs/server.err.log.' }
}
throw 'Server did not become ready. Inspect logs/server.err.log.'
