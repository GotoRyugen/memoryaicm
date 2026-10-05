"""N'importe quel LLM en une fonction : `fn(system: str, messages: list[dict]) -> str`.

    from memoryaicm.llm.callable_backend import CallableBackend
    backend = CallableBackend(lambda system, messages: mon_modele.generate(system, messages))
    agent = MemoryAgent(settings, backend=backend)

C'est le point d'adaptation universel : un modèle local, une API maison, un agent existant, un test.
L'extraction et l'abstraction passent par les mêmes prompts que les autres backends ; si la fonction
ne renvoie pas de JSON exploitable, l'extraction par règles (stub) prend le relais.
"""

from __future__ import annotations

from typing import Callable

from ..model import Fact
from .anthropic_backend import parse_candidates
from .base import Candidate, Context
from .prompts import ABSTRACT_SYSTEM, EXTRACT_SYSTEM, render_messages
from .stub import StubBackend

Fn = Callable[[str, list], str]


class CallableBackend:
    name = "callable"

    def __init__(self, fn: Fn, fallback_rules: bool = True):
        self.fn = fn
        self._rules = StubBackend() if fallback_rules else None

    def _call(self, system: str, messages: list[dict], temperature: float = 0.0) -> str:
        out = self.fn(system, messages)
        return (out or "").strip() if isinstance(out, str) else str(out or "").strip()

    def extract(self, text: str) -> list[Candidate]:
        try:
            cands = parse_candidates(self._call(EXTRACT_SYSTEM, [{"role": "user", "content": text}]))
        except Exception:
            cands = []
        if not cands and self._rules is not None:
            cands = self._rules.extract(text)
        return cands

    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        system, messages = render_messages(ctx)
        return self._call(system, messages, temperature=temperature)

    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        try:
            out = self._call(ABSTRACT_SYSTEM, [{"role": "user", "content": "\n".join(f"- {f.txt}" for f in facts)}])
        except Exception:
            out = ""
        if not out and self._rules is not None:
            return self._rules.abstract(facts)
        return out.splitlines()[0].strip() if out else None

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx, temperature=0.9) for _ in range(n)]


class HTTPCallableBackend(CallableBackend):
    """Un point d'API maison : POST {"system": …, "messages": […]} → {"text": "…"} (ou texte brut)."""
    name = "http"

    def __init__(self, url: str, api_key: str = "", timeout: float = 120.0, fallback_rules: bool = True):
        import json
        import urllib.request

        self.url, self.api_key, self.timeout = url, api_key, timeout

        def fn(system: str, messages: list) -> str:
            body = json.dumps({"system": system, "messages": messages}).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(self.url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            if isinstance(data, dict):
                return str(data.get("text") or data.get("content") or data.get("response") or "")
            return str(data)

        super().__init__(fn, fallback_rules=fallback_rules)


def backend_from_spec(spec: str, fallback_rules: bool = True) -> CallableBackend:
    """`MEMORYAICM_BACKEND=callable:<spec>` :
         module.sousmodule:fonction   → importe et enveloppe la fonction (system, messages) -> str
         http://… | https://…         → HTTPCallableBackend
    """
    spec = (spec or "").strip()
    if spec.startswith(("http://", "https://")):
        return HTTPCallableBackend(spec, fallback_rules=fallback_rules)
    if ":" not in spec:
        raise RuntimeError("spec callable attendue : module:fonction ou http(s)://url")
    import importlib

    mod_name, fn_name = spec.rsplit(":", 1)
    fn = getattr(importlib.import_module(mod_name), fn_name)
    if isinstance(fn, type):
        fn = fn()  # une classe : instanciée, doit être appelable
    return CallableBackend(fn, fallback_rules=fallback_rules)
