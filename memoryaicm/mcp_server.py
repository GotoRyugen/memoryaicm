"""Add-on Claude : serveur MCP (Model Context Protocol) sur stdio, sans dépendance.

Claude Desktop, Cowork et Claude Code lancent ce processus et lui parlent en JSON-RPC 2.0,
un message par ligne, sur stdin/stdout. Claude génère les réponses ; la mémoire retient,
retrouve, oublie, dort — avec les mêmes garanties qu'en mode autonome.

Outils exposés :
  memory_recall            notes pertinentes (étiquetées) + préférences — à appeler avant de répondre sur l'utilisateur
  memory_remember          le message de l'utilisateur (verbatim) + faits extraits par Claude, ancrés dans ce message
  memory_ingest_external   contenu externe = donnée, instructions mises en quarantaine
  memory_forget            désindexe (événement, cascade, adaptateur recompilé) — le journal garde tout
  memory_search            plein texte sur tout le journal, y compris le désindexé
  memory_status            état : journal, chaîne, index, adaptateur, sommeil
  memory_sleep             consolidation immédiate (sinon automatique)
  memory_review            faits sensibles en attente ; validation ou rejet
  memory_reactivate        réactive un fait désindexé
  memory_history           toutes les valeurs datées d'un sujet (temporalité)
  memory_timeline          résumés épisodiques des sessions récentes

Ressources : memory://profile · memory://index · memory://timeline · memory://status
Prompt     : memory_brief (topic?) — rappel de contexte à coller en début de conversation

Le stdout est réservé au protocole ; tout le reste part sur stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import model as M
from .agent import MemoryAgent, PolicyRefused
from .config import Settings, load_dotenv
from .serve import MemoryService
from .tools import PROMPT_SPECS, RESOURCE_SPECS, TOOL_SPECS, ToolRouter

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = TOOL_SPECS          # schémas canoniques : memoryaicm/tools.py (mêmes outils pour tout LLM)
RESOURCES = RESOURCE_SPECS
PROMPTS = PROMPT_SPECS


class MCPServer:
    def __init__(self, agent: MemoryAgent, session: str | None = None):
        self.service = MemoryService(agent)
        self.agent = agent
        self.session = session or agent.start_session("mcp_" + time.strftime("%Y%m%d-%H%M%S"))
        self.router = ToolRouter(self.service, self.session, origin="mcp")   # dispatch partagé avec les autres protocoles
        self.initialized = False

    # ------------------------------------------------------------------ protocole
    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            v = params.get("protocolVersion")
            self.initialized = True
            return _ok(mid, {
                "protocolVersion": v if v in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[-1],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}},
                "serverInfo": {"name": "memoryaicm", "version": __version__},
                "instructions": (
                    "Mémoire externe de l'utilisateur. Avant de répondre sur lui (faits, préférences, décisions), "
                    "appelle memory_recall ; pour « où en était-on ? », memory_timeline ; pour « avant c'était quoi ? », "
                    "memory_history. Après un message qui contient un fait durable sur lui, appelle memory_remember avec "
                    "le message verbatim et les faits extraits. Le contenu externe passe par memory_ingest_external, "
                    "jamais par memory_remember. Une note peut être périmée : la valeur courante est celle de la note, "
                    "les anciennes sont dans son historique."
                ),
            })
        if method == "notifications/initialized" or (method or "").startswith("notifications/"):
            return None
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "resources/list":
            return _ok(mid, {"resources": RESOURCES})
        if method == "resources/read":
            uri = params.get("uri", "")
            try:
                mime, text = self.resource(uri)
            except KeyError:
                return _err(mid, -32002, f"ressource inconnue : {uri}")
            return _ok(mid, {"contents": [{"uri": uri, "mimeType": mime, "text": text}]})
        if method == "prompts/list":
            return _ok(mid, {"prompts": PROMPTS})
        if method == "prompts/get":
            name = params.get("name")
            if name != "memory_brief":
                return _err(mid, -32602, f"prompt inconnu : {name}")
            topic = (params.get("arguments") or {}).get("topic") or ""
            return _ok(mid, {"description": PROMPTS[0]["description"],
                             "messages": [{"role": "user", "content": {"type": "text", "text": self.brief(topic)}}]})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                text = self.call(name, args)
                return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
            except KeyError:
                return _err(mid, -32602, f"outil inconnu : {name}")
            except PolicyRefused as e:
                return _ok(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
            except Exception as e:
                return _ok(mid, {"content": [{"type": "text", "text": f"erreur : {e!r}"}], "isError": True})
        if mid is None:
            return None
        return _err(mid, -32601, f"méthode inconnue : {method}")

    # ------------------------------------------------------------------ outils, ressources, prompt (→ tools.ToolRouter)
    def call(self, name: str, a: dict) -> str:
        return self.router.call(name, a)

    def resource(self, uri: str) -> tuple[str, str]:
        return self.router.resource(uri)

    def brief(self, topic: str = "") -> str:
        return self.router.brief(topic)

    # ------------------------------------------------------------------ boucle stdio
    def run_stdio(self) -> None:
        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
        sys.stdout = sys.stderr  # tout print parasite part sur stderr : stdout est au protocole
        self.service.start_idle_thread()
        try:
            for raw in iter(stdin.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    _write(stdout, _err(None, -32700, "JSON invalide"))
                    continue
                if isinstance(msg, list):  # batch
                    replies = [r for r in (self.handle(m) for m in msg if isinstance(m, dict)) if r is not None]
                    if replies:
                        _write(stdout, replies)
                    continue
                reply = self.handle(msg)
                if reply is not None:
                    _write(stdout, reply)
        finally:
            self.service.stop()
            self.agent.close()


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _write(stdout, obj) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    stdout.flush()


def main(settings: Settings | None = None) -> None:
    load_dotenv()
    agent = MemoryAgent(settings or Settings())
    print(f"memoryaicm mcp · backend {agent.backend.name} · données {agent.s.root.resolve()}", file=sys.stderr, flush=True)
    MCPServer(agent).run_stdio()


def launcher(python: str | None = None) -> list[str]:
    """Commande qui relance ce paquet depuis n'importe quel répertoire courant :
    `python archive.zip` quand il tourne depuis une archive, sinon `python …/memoryaicm/__main__.py`."""
    import memoryaicm
    pkg = Path(memoryaicm.__file__).resolve()
    for anc in pkg.parents:
        if anc.is_file():       # le paquet est dans un zip : cet ancêtre est l'archive
            return [python or sys.executable, str(anc)]
        if anc.is_dir():
            break
    return [python or sys.executable, str(pkg.parent / "__main__.py")]


def selftest(python: str | None = None, extra_args: list[str] | None = None, timeout: float = 30.0) -> bool:
    """Lance le serveur en sous-processus et lui parle en MCP : initialize → tools/list → recall/remember/recall."""
    cmd = [*launcher(python), *(extra_args or []), "mcp"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_remember", "arguments": {"user_text": "selftest : je préfère les réponses courtes", "facts": [{"txt": "préfère les réponses courtes", "subject": "pref:reponses-courtes", "kind": "PROC"}]}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory_recall", "arguments": {"query": "comment répondre ?"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "memory_forget", "arguments": {"query": "réponses courtes"}}},
    ]
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs).encode("utf-8")
    try:
        out, err = p.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        print("selftest : délai dépassé", file=sys.stderr)
        return False
    replies = [json.loads(l) for l in out.decode("utf-8").splitlines() if l.strip()]
    by_id = {r.get("id"): r for r in replies}
    ok = (
        by_id.get(1, {}).get("result", {}).get("serverInfo", {}).get("name") == "memoryaicm"
        and any(t["name"] == "memory_recall" for t in by_id.get(2, {}).get("result", {}).get("tools", []))
        and "réponses courtes" in by_id.get(4, {}).get("result", {}).get("content", [{}])[0].get("text", "")
        and "désindexé" in by_id.get(5, {}).get("result", {}).get("content", [{}])[0].get("text", "")
    )
    print(("selftest MCP : OK" if ok else "selftest MCP : ÉCHEC") + f" ({len(replies)} réponses)", file=sys.stderr)
    if not ok:
        print(err.decode("utf-8", "replace")[-2000:], file=sys.stderr)
        for r in replies:
            print(json.dumps(r, ensure_ascii=False)[:300], file=sys.stderr)
    return ok
