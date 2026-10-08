#!/usr/bin/env bash
# memoryaicm Pro : installation en une commande sous Linux et macOS.
#   ./install-claude.sh        (ou : bash install-claude.sh)
# Cree l'environnement Python, verifie le serveur de memoire, ecrit l'entree dans la configuration de
# Claude Desktop (sauvegarde a cote), installe le hook Claude Code et l'ajoute a Claude Code s'il est present.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
exec > >(tee "$ROOT/install.log") 2>&1
echo "== memoryaicm : installation $(date '+%Y-%m-%d %H:%M') =="

# 1. Python 3.10+ et environnement
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then echo "Python 3 introuvable. Installez Python 3.10 ou plus, puis relancez."; exit 1; fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 ou plus est requis (trouve : $("$PY" -V 2>&1))."; exit 1; }
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || { echo "Impossible de creer l'environnement. Sous Debian/Ubuntu : sudo apt install python3-venv"; exit 1; }
fi
VPY="$ROOT/.venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -e .
DATA="$ROOT/data"
mkdir -p "$DATA"

# 2. auto-test du serveur MCP sur un dossier temporaire
TMP="$(mktemp -d)"
if ! "$VPY" -m memoryaicm --home "$TMP" --backend stub mcp --selftest; then
  echo "Le serveur MCP ne passe pas son auto-test : installation annulee."; rm -rf "$TMP"; exit 1
fi
rm -rf "$TMP"

# 3. Claude Desktop / Cowork (entree mcpServers) et hook Claude Code
"$VPY" -m memoryaicm --home "$DATA" install --write --hook

# 4. Claude Code, s'il est installe
if command -v claude >/dev/null 2>&1; then
  claude mcp remove memoryaicm --scope user >/dev/null 2>&1 || true
  claude mcp add memoryaicm --scope user -e "MEMORYAICM_HOME=$DATA" -- "$VPY" -m memoryaicm mcp \
    && echo "Claude Code : serveur memoryaicm ajoute."
else
  echo "Claude Code (si installe plus tard) :"
  echo "  claude mcp add memoryaicm --scope user -e MEMORYAICM_HOME=$DATA -- \"$VPY\" -m memoryaicm mcp"
fi

echo
echo "Termine. Redemarrez Claude Desktop : les outils memory_* apparaissent dans la liste des outils."
echo "Vos donnees vivent dans $DATA et ne quittent jamais votre machine. Detail : install.log"
