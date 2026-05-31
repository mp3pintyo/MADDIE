$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
  $pythonArgs = @('-3', '-m', 'venv', '.venv')
} else {
  $pythonCommand = Get-Command python -ErrorAction Stop
  $pythonArgs = @('-m', 'venv', '.venv')
}

if (-not (Test-Path '.venv')) {
  & $pythonCommand.Source @pythonArgs
}

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

$requestedPort = 8000
if ($env:MADDIE_PORT) {
  $requestedPort = [int]$env:MADDIE_PORT
}

$selectedPort = $requestedPort
while (Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue) {
  $selectedPort++
}

if ($selectedPort -ne $requestedPort) {
  Write-Host "Port $requestedPort foglalt, indítás a következő szabad porton: $selectedPort"
}

Write-Host "MADDIE indul: http://127.0.0.1:$selectedPort"
& $pythonExe -m uvicorn app.main:app --host 127.0.0.1 --port $selectedPort --reload