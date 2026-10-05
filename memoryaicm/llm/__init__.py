"""Fabrique de backend.

`auto` (défaut) choisit seul, sans rien demander :
  1. Claude        si ANTHROPIC_API_KEY est défini (SDK `anthropic` si installé, sinon HTTP direct — aucune dépendance)
  2. llamacpp      si MEMORYAICM_OPENAI_BASE_URL est défini (llama-server, LM Studio, vLLM, Ollama /v1…) — modèle GGUF
                   vérifié par SHA-256 si MEMORYAICM_MODEL_PATH est donné (règle Echo-Core)
  3. Ollama        si un serveur répond sur OLLAMA_URL (sondé avec un délai court)
  4. stub          sinon — déterministe, hors ligne, tout fonctionne quand même

Choix explicites (MEMORYAICM_BACKEND ou --backend) :
  anthropic | claude          Claude (SDK ou HTTP)
  openai                      api.openai.com (OPENAI_API_KEY, OPENAI_MODEL) ou MEMORYAICM_OPENAI_BASE_URL si défini
  llamacpp                    serveur OpenAI-compatible local (MEMORYAICM_OPENAI_BASE_URL) ou llama-server lancé ici
  ollama                      Ollama natif (/api/chat)
  callable:module:fonction    n'importe quelle fonction Python (system, messages) -> str
  callable:http(s)://url      n'importe quel point d'API maison : POST {system, messages} → {text}
  stub                        règles locales, hors ligne
La neutralité est totale : le cœur (journal, index, politique, sommeil, gardes) ne dépend d'aucun fournisseur.
"""

from __future__ import annotations

import os
import urllib.request

from ..config import Settings
from .base import Backend, Candidate, Context, SYSTEM_BASE
from .stub import StubBackend

__all__ = ["Backend", "Candidate", "Context", "SYSTEM_BASE", "StubBackend", "get_backend", "ollama_reachable", "ollama_models", "ollama_pick_model"]


def ollama_models(url: str, timeout: float = 0.4) -> list[str] | None:
    """Noms des modèles servis par Ollama, ou None si le serveur ne répond pas."""
    try:
        import json
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            if r.status != 200:
                return None
            return [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
    except Exception:
        return None


def ollama_reachable(url: str, timeout: float = 0.4) -> bool:
    return ollama_models(url, timeout) is not None


def ollama_pick_model(url: str, preferred: str, timeout: float = 0.4) -> str | None:
    """Le modèle configuré s'il est servi (avec ou sans étiquette), sinon le premier disponible, sinon None."""
    models = ollama_models(url, timeout)
    if not models:
        return None
    base = preferred.split(":")[0]
    for m in models:
        if m == preferred or m.split(":")[0] == base:
            return m
    return models[0]


def get_backend(settings: Settings | None = None, approvals=None) -> Backend:
    s = settings or Settings()
    choice = (s.backend or "auto").lower()

    if choice.startswith("callable:"):
        from .callable_backend import backend_from_spec
        return backend_from_spec(choice[len("callable:"):])

    if choice in ("anthropic", "claude") or (choice == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        try:
            from .anthropic_backend import AnthropicBackend
            return AnthropicBackend(model=s.model)
        except ImportError:
            if os.environ.get("ANTHROPIC_API_KEY"):
                from .anthropic_http_backend import AnthropicHTTPBackend   # sans SDK : HTTP direct
                return AnthropicHTTPBackend(model=s.model)
            if choice != "auto":
                raise RuntimeError("ANTHROPIC_API_KEY manquant (backend Claude demandé)")

    if choice == "openai" and not s.openai_base_url:
        from .openai_compat_backend import OpenAICompatBackend
        model = s.openai_model if s.openai_model != "local" else os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAICompatBackend(base_url="https://api.openai.com", api_key=s.openai_api_key or os.environ.get("OPENAI_API_KEY", ""),
                                   model=model, require_approval=False)

    if choice in ("llamacpp", "openai") or (choice == "auto" and s.openai_base_url):
        from .openai_compat_backend import OpenAICompatBackend
        if approvals is None and s.model_path and s.require_model_approval:
            from ..models import ModelApprovals
            approvals = ModelApprovals(s)
        return OpenAICompatBackend(base_url=s.openai_base_url, api_key=s.openai_api_key, model=s.openai_model,
                                   model_path=s.model_path, approvals=approvals, require_approval=s.require_model_approval,
                                   llama_server_exe=s.llama_server_exe, ctx=s.llama_ctx, gpu_layers=s.llama_gpu_layers)

    if choice == "ollama":
        from .ollama_backend import OllamaBackend
        return OllamaBackend(url=s.ollama_url, model=s.ollama_model)
    if choice == "auto":
        model = ollama_pick_model(s.ollama_url, s.ollama_model, s.ollama_probe_timeout_s)  # jamais un modèle absent (404)
        if model:
            from .ollama_backend import OllamaBackend
            return OllamaBackend(url=s.ollama_url, model=model)

    return StubBackend()
