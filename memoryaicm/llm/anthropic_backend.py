"""Backend Claude (SDK `anthropic`). Activé si ANTHROPIC_API_KEY est défini et le SDK installé."""

from __future__ import annotations

import json
import os
import re

from ..model import Fact
from .base import Candidate, Context
from .prompts import ABSTRACT_SYSTEM, EXTRACT_SYSTEM, render_messages


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-5", api_key: str | None = None):
        import anthropic  # import tardif : dépendance optionnelle

        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def _call(self, system: str, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1200) -> str:
        r = self.client.messages.create(
            model=self.model, system=system, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()

    def extract(self, text: str) -> list[Candidate]:
        raw = self._call(EXTRACT_SYSTEM, [{"role": "user", "content": text}], max_tokens=800)
        return parse_candidates(raw)

    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        system, messages = render_messages(ctx)
        return self._call(system, messages, temperature=temperature)

    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        body = "\n".join(f"- {f.txt}" for f in facts)
        out = self._call(ABSTRACT_SYSTEM, [{"role": "user", "content": body}], max_tokens=200)
        return out.splitlines()[0].strip() if out else None

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx, temperature=0.9) for _ in range(n)]


def parse_candidates(raw: str) -> list[Candidate]:
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict) or not d.get("txt") or not d.get("subject"):
            continue
        out.append(Candidate(
            txt=str(d["txt"]).strip(), subject=str(d["subject"]).strip().lower(),
            kind=str(d.get("kind", "SEM")).upper() if str(d.get("kind", "SEM")).upper() in ("SEM", "PROC", "EPI") else "SEM",
            src="INFER" if str(d.get("src", "USER")).upper() == "INFER" else "USER",
            exclusive=bool(d.get("exclusive", True)), durable=bool(d.get("durable", True)),
            sens=bool(d.get("sens", False)), explicit=bool(d.get("explicit", False)),
        ))
    return out
