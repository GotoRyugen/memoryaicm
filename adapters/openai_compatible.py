"""N'importe quel serveur OpenAI-compatible pilote la mémoire par ses outils.

    python adapters/openai_compatible.py --base-url http://localhost:11434 --model llama3.1        # Ollama (/v1)
    python adapters/openai_compatible.py --base-url http://localhost:8080  --model local           # llama-server
    python adapters/openai_compatible.py --base-url http://localhost:1234  --model qwen2.5         # LM Studio
    OPENAI_API_KEY=… python adapters/openai_compatible.py --model gpt-4o-mini                       # OpenAI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # inutile si memoryaicm est installé

from memoryaicm import MemoryAgent, Settings
from memoryaicm.toolloop import OpenAIToolLoop


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="")
    p.add_argument("--model", default="")
    p.add_argument("--api-key", default="")
    p.add_argument("--home", default="data")
    a = p.parse_args()
    agent = MemoryAgent(Settings(root=Path(a.home)))
    loop = OpenAIToolLoop(agent, base_url=a.base_url, model=a.model, api_key=a.api_key)
    print(f"mémoire {agent.s.root} · modèle {loop.model} @ {loop.base_url} · /quit pour sortir")
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("/quit", "/q", ""):
            if line:
                break
            continue
        answer = loop.ask(line)
        for name, args, result in loop.calls:
            print(f"   [{name} {args} → {result.splitlines()[0][:80] if result else ''}]")
        print("assistant>", answer)
    loop.close()


if __name__ == "__main__":
    main()
