# Installe memoryaicm comme add-on Claude (serveur MCP local) pour Claude Desktop / Cowork, et affiche
# la commande pour Claude Code.   powershell -ExecutionPolicy Bypass -File .\install-claude.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
try { Start-Transcript -Path (Join-Path $PSScriptRoot "install.log") -Force | Out-Null } catch {}
try {

# 1. environnement (setup.ps1 si absent)
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
$dataDir = Join-Path $PSScriptRoot "data"

# 2. auto-test du serveur MCP (initialize → tools/list → remember → recall → forget) sur un dossier temporaire
$tmp = Join-Path $env:TEMP ("memoryaicm-selftest-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
& $venvPy -m memoryaicm --home $tmp --backend stub mcp --selftest
if ($LASTEXITCODE -ne 0) { Write-Host "Le serveur MCP ne passe pas son auto-test : installation annulée."; exit 1 }
# nettoyage facultatif : sous Windows, Remove-Item -Recurse peut lever une erreur terminale (fichiers SQLite
# encore verrouilles quelques instants apres la sortie du sous-processus) ; un dossier temporaire qui reste
# n'est pas un echec d'installation
try { Start-Sleep -Milliseconds 800; Remove-Item -Recurse -Force $tmp -ErrorAction Stop } catch { Write-Host "  (dossier temporaire conserve : $tmp)" }

# 3. Claude Desktop (et Cowork) : %APPDATA%\Claude\claude_desktop_config.json, entrée mcpServers.memoryaicm
$cfgDir = Join-Path $env:APPDATA "Claude"
$cfg = Join-Path $cfgDir "claude_desktop_config.json"
if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir | Out-Null }
$json = [pscustomobject]@{}
if (Test-Path $cfg) {
    Copy-Item $cfg ($cfg + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $raw = Get-Content $cfg -Raw
    if ($raw.Trim()) { $json = $raw | ConvertFrom-Json }
}
if (-not ($json.PSObject.Properties.Name -contains "mcpServers")) {
    $json | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue ([pscustomobject]@{})
}
$entry = [pscustomobject]@{
    command = $venvPy
    args    = @("-m", "memoryaicm", "mcp")
    env     = [pscustomobject]@{
        MEMORYAICM_HOME  = $dataDir
        PYTHONPATH       = $PSScriptRoot
        PYTHONIOENCODING = "utf-8"
        PYTHONUTF8       = "1"
    }
}
if ($json.mcpServers.PSObject.Properties.Name -contains "memoryaicm") {
    $json.mcpServers.memoryaicm = $entry
} else {
    $json.mcpServers | Add-Member -NotePropertyName "memoryaicm" -NotePropertyValue $entry
}
# UTF-8 sans BOM : le lecteur JSON de Claude Desktop n'accepte pas le BOM que Set-Content -Encoding UTF8 ajoute
[System.IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Claude Desktop / Cowork : entrée 'memoryaicm' écrite dans $cfg"
Write-Host "  -> redémarre Claude Desktop ; les outils memory_* apparaissent (icône outils / connecteurs)."

# 4. Claude Code : serveur MCP (portée utilisateur) + hook UserPromptSubmit (mémoire automatique, sans appel d'outil)
$cc = Get-Command claude -ErrorAction SilentlyContinue
$ccDir = Join-Path $env:USERPROFILE ".claude"
if ($cc) {
    & claude mcp add memoryaicm --scope user -e "MEMORYAICM_HOME=$dataDir" -e "PYTHONPATH=$PSScriptRoot" -e "PYTHONUTF8=1" -- $venvPy -m memoryaicm mcp
    Write-Host "Claude Code : serveur 'memoryaicm' ajouté (portée utilisateur)."
} else {
    Write-Host "Claude Code (si installé plus tard) :"
    Write-Host "  claude mcp add memoryaicm --scope user -e MEMORYAICM_HOME=$dataDir -e PYTHONPATH=$PSScriptRoot -e PYTHONUTF8=1 -- `"$venvPy`" -m memoryaicm mcp"
}
if ($cc -or (Test-Path $ccDir)) {
    if (-not (Test-Path $ccDir)) { New-Item -ItemType Directory -Path $ccDir | Out-Null }
    $ccSettings = Join-Path $ccDir "settings.json"
    $cs = [pscustomobject]@{}
    if (Test-Path $ccSettings) {
        Copy-Item $ccSettings ($ccSettings + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        $raw = Get-Content $ccSettings -Raw
        if ($raw.Trim()) { $cs = $raw | ConvertFrom-Json }
    }
    if (-not ($cs.PSObject.Properties.Name -contains "hooks")) { $cs | Add-Member -NotePropertyName "hooks" -NotePropertyValue ([pscustomobject]@{}) }
    $pyFwd = $venvPy -replace '\\', '/'
    $dataFwd = $dataDir -replace '\\', '/'
    $hookCmd = "`"$pyFwd`" -m memoryaicm --home `"$dataFwd`" hook prompt"
    $entry = [pscustomobject]@{ hooks = @([pscustomobject]@{ type = "command"; command = $hookCmd; timeout = 20 }) }
    $existing = @()
    if ($cs.hooks.PSObject.Properties.Name -contains "UserPromptSubmit") {
        $existing = @($cs.hooks.UserPromptSubmit | Where-Object { ($_ | ConvertTo-Json -Depth 6 -Compress) -notmatch "memoryaicm" })
        $cs.hooks.UserPromptSubmit = @($existing + $entry)
    } else {
        $cs.hooks | Add-Member -NotePropertyName "UserPromptSubmit" -NotePropertyValue $null
        $cs.hooks.UserPromptSubmit = @($entry)   # affectation : le tableau a un seul element reste un tableau
    }
    [System.IO.File]::WriteAllText($ccSettings, ($cs | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Claude Code : hook UserPromptSubmit écrit dans $ccSettings (mémoire injectée à chaque message)."
}
Write-Host "`nDonnées : $dataDir   (journal.sqlite, index.sqlite, adapter\)"
} catch {
    Write-Host ""
    Write-Host "ERREUR : $($_.Exception.Message)"
    Write-Host ("  " + $_.InvocationInfo.PositionMessage)
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
try { Stop-Transcript | Out-Null } catch {}
exit 0
