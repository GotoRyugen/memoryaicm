# Conversation avec mémoire. Arguments transmis à la CLI (ex. .\chat.ps1 --session projet).
Set-Location $PSScriptRoot
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Lance d'abord .\setup.ps1"; exit 1 }
& $venvPy -m memoryaicm chat @args
