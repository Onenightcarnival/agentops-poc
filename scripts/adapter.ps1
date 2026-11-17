param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($Action -eq 'status') {
    Invoke-RestMethod -Uri 'http://127.0.0.1:18081/health' -TimeoutSec 3
    exit 0
}
if (Test-Path 'logs/adapter.pid') {
    $adapterPid = [int](Get-Content 'logs/adapter.pid')
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $adapterPid"
    if ($existing -and $existing.CommandLine -like '*uvicorn json_adapter:app*' -and $existing.CommandLine -like '*18081*') {
        if ($Action -eq 'stop') {
            Stop-Process -Id $adapterPid
            Write-Output "Stopped adapter PID $adapterPid"
        } else {
            Write-Output "Already running: adapter PID $adapterPid"
        }
        exit 0
    }
}
if ($Action -eq 'stop') { Write-Output 'Adapter is not running'; exit 0 }
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$uv = if ($uvCommand) { $uvCommand.Source } else { Join-Path $env:APPDATA 'Python\Scripts\uv.exe' }
if (-not (Test-Path -LiteralPath $uv)) { throw 'Install uv first.' }
New-Item -ItemType Directory -Force logs | Out-Null
$process = Start-Process -FilePath $uv -ArgumentList 'run','--locked','python','-m','uvicorn','json_adapter:app','--host','127.0.0.1','--port','18081' -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru -RedirectStandardOutput 'logs/adapter.out.log' -RedirectStandardError 'logs/adapter.err.log'
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18081/health' -TimeoutSec 2
        if ($health.service -eq 'json-adapter') { Write-Output 'Ready: http://127.0.0.1:18081'; exit 0 }
    } catch { }
    if ($process.HasExited) { throw 'Adapter exited. Inspect logs/adapter.err.log.' }
}
throw 'Adapter did not become ready. Inspect logs/adapter.err.log.'
