"""Backend Ollama (modèle local, HTTP via urllib — aucune dépendance)."""

from __future__ import annotations

import json
import urllib.request

from ..model import Fact
from .anthropic_backend import parse_candidates
from .base import Candidate, Context
from .prompts import ABSTRACT_SYSTEM, EXTRACT_SYSTEM, render_messages


class OllamaBackend:
    name = "ollama"

    def __init__(self, url: str = "http://localhost:11434", model: str = "llama3.1", timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _call(self, system: str, messages: list[dict], temperature: float = 0.0) -> str:
        body = json.dumps({
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system}, *messages],
            "options": {"temperature": temperature},
        }).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("message") or {}).get("content", "").strip()

    def extract(self, text: str) -> list[Candidate]:
        return parse_candidates(self._call(EXTRACT_SYSTEM, [{"role": "user", "content": text}]))

    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        system, messages = render_messages(ctx)
        return self._call(system, messages, temperature=temperature)

    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        body = "\n".join(f"- {f.txt}" for f in facts)
        out = self._call(ABSTRACT_SYSTEM, [{"role": "user", "content": body}])
        return out.splitlines()[0].strip() if out else None

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx, temperature=0.9) for _ in range(n)]
