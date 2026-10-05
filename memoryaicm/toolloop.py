"""Boucle d'outils générique : N'IMPORTE QUEL LLM pilote la mémoire par ses outils.

Trois transports, stdlib uniquement (urllib) :

  OpenAIToolLoop      /v1/chat/completions + tools   → OpenAI, llama-server, LM Studio, vLLM, Ollama (/v1), Groq, Mistral…
  AnthropicToolLoop   /v1/messages + tools           → Claude par l'API directe (sans SDK)
  TextToolLoop        aucun appel d'outil natif      → le modèle écrit des blocs ```memory {...}``` (tout modèle de chat)

Le protocole (memoryaicm.tools.PROTOCOL) est mis en consigne système, les schémas sont envoyés dans le format
du fournisseur, et chaque appel est exécuté par ToolRouter — la politique d'écriture reste la seule autorité.

    loop = OpenAIToolLoop(agent, base_url="http://localhost:11434", model="llama3.1")
    print(loop.ask("je m'appelle Camille et je préfère les réponses courtes"))
    print(loop.ask("comment je m'appelle ?"))
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .agent import MemoryAgent
from .tools import ToolRouter, as_anthropic, as_openai, extract_text_calls, strip_text_calls, system_prompt

MAX_ROUNDS = 6


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} sur {url} : {detail}") from None


class _Loop:
    """Base : historique de conversation + routeur + consigne système."""

    def __init__(self, agent: MemoryAgent, session: str | None = None, origin: str = "tools", timeout: float = 120.0,
                 brief: bool = True):
        self.router = ToolRouter(agent, session, origin=origin)
        self.timeout = timeout
        self.history: list[dict] = []
        self.calls: list[tuple[str, dict, str]] = []   # (outil, arguments, résultat) — trace de la dernière question
        self.system = system_prompt(text_protocol=False)
        if brief:
            self.system += "\n" + self.router.brief()

    def reset(self) -> None:
        self.history = []

    def close(self) -> None:
        self.router.close()


class OpenAIToolLoop(_Loop):
    """Chat Completions avec `tools` (format OpenAI). `base_url` sans /v1 (ex. http://localhost:11434, https://api.openai.com)."""

    def __init__(self, agent: MemoryAgent, base_url: str = "", model: str = "", api_key: str = "", **kw):
        super().__init__(agent, origin="openai", **kw)
        self.base_url = (base_url or os.environ.get("MEMORYAICM_OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.model = model or os.environ.get("MEMORYAICM_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        self.api_key = api_key or os.environ.get("MEMORYAICM_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.tools = as_openai()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def ask(self, text: str) -> str:
        self.calls = []
        self.history.append({"role": "user", "content": text})
        for _ in range(MAX_ROUNDS):
            data = _post(self.base_url + "/v1/chat/completions",
                         {"model": self.model, "messages": [{"role": "system", "content": self.system}, *self.history],
                          "tools": self.tools, "tool_choice": "auto", "temperature": 0.2},
                         self._headers(), self.timeout)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                answer = (msg.get("content") or "").strip()
                self.history.append({"role": "assistant", "content": answer})
                return answer
            self.history.append({"role": "assistant", "content": msg.get("content") or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result, _err = self.router.call_safe(name, args)
                self.calls.append((name, args, result))
                self.history.append({"role": "tool", "tool_call_id": tc.get("id", name), "name": name, "content": result})
        return "(trop d'appels d'outils successifs — réponse interrompue)"


class AnthropicToolLoop(_Loop):
    """Messages API Anthropic avec `tools` (sans SDK)."""

    def __init__(self, agent: MemoryAgent, model: str = "", api_key: str = "", base_url: str = "https://api.anthropic.com", **kw):
        super().__init__(agent, origin="anthropic", **kw)
        self.model = model or os.environ.get("MEMORYAICM_MODEL") or "claude-sonnet-4-5"
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY manquant")
        self.base_url = base_url.rstrip("/")
        self.tools = as_anthropic()

    def ask(self, text: str) -> str:
        self.calls = []
        self.history.append({"role": "user", "content": text})
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        for _ in range(MAX_ROUNDS):
            data = _post(self.base_url + "/v1/messages",
                         {"model": self.model, "system": self.system, "messages": self.history, "tools": self.tools,
                          "max_tokens": 1500, "temperature": 0.2}, headers, self.timeout)
            content = data.get("content") or []
            uses = [b for b in content if b.get("type") == "tool_use"]
            self.history.append({"role": "assistant", "content": content})
            if not uses:
                answer = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                return answer
            results = []
            for u in uses:
                result, err = self.router.call_safe(u.get("name", ""), u.get("input") or {})
                self.calls.append((u.get("name", ""), u.get("input") or {}, result))
                results.append({"type": "tool_result", "tool_use_id": u.get("id"), "content": result, "is_error": bool(err)})
            self.history.append({"role": "user", "content": results})
        return "(trop d'appels d'outils successifs — réponse interrompue)"


class TextToolLoop(_Loop):
    """Modèle sans appel d'outils : protocole texte (blocs ```memory```) au-dessus de n'importe quelle fonction de chat.

    `chat(system, messages) -> str` : la même signature que CallableBackend — un modèle local, une API, un test.
    """

    def __init__(self, agent: MemoryAgent, chat, **kw):
        super().__init__(agent, origin="text", **kw)
        self.chat = chat
        self.system = system_prompt(text_protocol=True) + ("\n" + self.router.brief() if kw.get("brief", True) else "")

    def ask(self, text: str) -> str:
        self.calls = []
        self.history.append({"role": "user", "content": text})
        reply = ""
        for _ in range(MAX_ROUNDS):
            reply = (self.chat(self.system, list(self.history)) or "").strip()
            calls = extract_text_calls(reply)
            self.history.append({"role": "assistant", "content": reply})
            if not calls:
                return reply
            out = ["RÉSULTATS MÉMOIRE :"]
            for name, args in calls:
                result, err = self.router.call_safe(name, args)
                self.calls.append((name, args, result))
                out.append(f"### {name}{' (erreur)' if err else ''}\n{result}")
            self.history.append({"role": "user", "content": "\n".join(out)})
        return strip_text_calls(reply) or "(trop d'appels d'outils successifs — réponse interrompue)"


def make_loop(agent: MemoryAgent, provider: str = "openai", **kw):
    """Fabrique : openai (défaut, tout serveur compatible) · anthropic · text (kw['chat'] requis)."""
    provider = (provider or "openai").lower()
    if provider in ("openai", "llamacpp", "ollama", "lmstudio", "vllm", "groq", "mistral"):
        return OpenAIToolLoop(agent, **kw)
    if provider in ("anthropic", "claude"):
        return AnthropicToolLoop(agent, **kw)
    if provider == "text":
        return TextToolLoop(agent, **kw)
    raise ValueError(f"fournisseur inconnu : {provider} (openai | anthropic | text)")
