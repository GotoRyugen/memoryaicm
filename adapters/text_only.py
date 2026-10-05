"""Un modèle SANS appel d'outils utilise quand même la mémoire : protocole texte (blocs ```memory```).

`chat(system, messages) -> str` peut être n'importe quoi : un modèle transformers local, llama-cpp-python,
un service maison, Ollama (/api/generate)… Ici : Ollama, sans dépendance.

    python adapters/text_only.py --model gemma3
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent, Settings
from memoryaicm.toolloop import TextToolLoop


def make_chat(url: str, model: str):
    def chat(system: str, messages: list[dict]) -> str:
        body = json.dumps({"model": model, "stream": False, "messages": [{"role": "system", "content": system}, *messages]}).encode()
        req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return (json.loads(r.read().decode("utf-8")).get("message") or {}).get("content", "")
    return chat


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:11434")
    p.add_argument("--model", default="llama3.1")
    p.add_argument("--home", default="data")
    a = p.parse_args()
    agent = MemoryAgent(Settings(root=Path(a.home)))
    loop = TextToolLoop(agent, make_chat(a.url, a.model))
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/quit":
            break
        if line:
            answer = loop.ask(line)
            for name, args, result in loop.calls:
                print(f"   [{name} → {result.splitlines()[0][:80] if result else ''}]")
            print("assistant>", answer)
    loop.close()


if __name__ == "__main__":
    main()
