# Service local JSON sur 127.0.0.1:8765 (sommeil automatique sur inactivité). Ctrl+C pour arrêter.
Set-Location $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Lance d'abord .\setup.ps1"; exit 1 }
& $venvPy -m memoryaicm serve @args
