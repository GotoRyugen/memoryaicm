# Installation autonome (Windows / PowerShell) : environnement, dépendances, tests, initialisation.
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($cand in @("py -3", "python", "python3")) {
    try { $v = & cmd /c "$cand --version 2>&1"; if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.(1[0-9])") { $py = $cand; break } } catch {}
}
if (-not $py) { Write-Host "Python 3.10+ introuvable (https://www.python.org/downloads/)"; exit 1 }
Write-Host "Python : $py ($v)"

if (-not (Test-Path ".venv")) { & cmd /c "$py -m venv .venv" }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -e ".[dev]" -q
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host ".env créé depuis .env.example (clé API optionnelle)" }

Write-Host "`nTests :"
& $venvPy -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Des tests échouent - le système n'est pas promu."; exit 1 }

Write-Host "`nInitialisation :"
& $venvPy -m memoryaicm init

Write-Host "`nPrêt. Ensuite :  .\chat.ps1   (conversation)   ou   .\serve.ps1   (service local)"
