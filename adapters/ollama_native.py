"""Ollama par son API native (/api/chat) avec appel d'outils — stdlib uniquement.

    python adapters/ollama_native.py --model llama3.1
(Ollama expose aussi /v1 : `adapters/openai_compatible.py` fonctionne tel quel.)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent, Settings
from memoryaicm.tools import ToolRouter, as_ollama, system_prompt


def chat(url: str, model: str, messages: list[dict], tools: list[dict]) -> dict:
    body = json.dumps({"model": model, "messages": messages, "tools": tools, "stream": False}).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def ask(router: ToolRouter, url: str, model: str, history: list[dict], text: str) -> str:
    history.append({"role": "user", "content": text})
    for _ in range(6):
        msg = chat(url, model, history, as_ollama()).get("message") or {}
        calls = msg.get("tool_calls") or []
        history.append(msg)
        if not calls:
            return (msg.get("content") or "").strip()
        for c in calls:
            fn = c.get("function") or {}
            result, _err = router.call_safe(fn.get("name", ""), fn.get("arguments") or {})
            history.append({"role": "tool", "content": result})
    return "(trop d'appels d'outils)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:11434")
    p.add_argument("--model", default="llama3.1")
    p.add_argument("--home", default="data")
    a = p.parse_args()
    agent = MemoryAgent(Settings(root=Path(a.home)))
    router = ToolRouter(agent, origin="ollama")
    history = [{"role": "system", "content": system_prompt() + "\n" + router.brief()}]
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/quit":
            break
        if line:
            print("assistant>", ask(router, a.url, a.model, history, line))
    router.close()


if __name__ == "__main__":
    main()
