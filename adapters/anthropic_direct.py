"""Claude par l'API Messages directe (sans SDK) : la mémoire est un jeu d'outils comme un autre.

    ANTHROPIC_API_KEY=… python adapters/anthropic_direct.py --model claude-sonnet-4-5
Pour Claude Desktop / Cowork / Claude Code, préférer le serveur MCP : `python -m memoryaicm install --write`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent, Settings
from memoryaicm.toolloop import AnthropicToolLoop


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="")
    p.add_argument("--home", default="data")
    a = p.parse_args()
    agent = MemoryAgent(Settings(root=Path(a.home)))
    loop = AnthropicToolLoop(agent, model=a.model)
    print(f"mémoire {agent.s.root} · {loop.model} · /quit pour sortir")
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/quit":
            break
        if line:
            print("assistant>", loop.ask(line))
    loop.close()


if __name__ == "__main__":
    main()
