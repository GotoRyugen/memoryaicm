"""Backend Claude par HTTP direct (Messages API), sans SDK — pour le fichier neutre et les environnements sans pip."""

from __future__ import annotations

import json
import os
import urllib.request

from ..model import Fact
from .anthropic_backend import parse_candidates
from .base import Candidate, Context
from .prompts import ABSTRACT_SYSTEM, EXTRACT_SYSTEM, render_messages


class AnthropicHTTPBackend:
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-5", api_key: str | None = None,
                 base_url: str = "https://api.anthropic.com", timeout: float = 120.0):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY manquant")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, system: str, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1200) -> str:
        body = json.dumps({"model": self.model, "system": system, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/v1/messages", data=body, headers={
            "Content-Type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()

    def extract(self, text: str) -> list[Candidate]:
        return parse_candidates(self._call(EXTRACT_SYSTEM, [{"role": "user", "content": text}], max_tokens=800))

    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        system, messages = render_messages(ctx)
        return self._call(system, messages, temperature=temperature)

    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        out = self._call(ABSTRACT_SYSTEM, [{"role": "user", "content": "\n".join(f"- {f.txt}" for f in facts)}], max_tokens=200)
        return out.splitlines()[0].strip() if out else None

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx, temperature=0.9) for _ in range(n)]
