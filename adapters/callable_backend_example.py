"""Un modèle local comme backend d'EXTRACTION (faits, abstractions) — sans serveur : une simple fonction.

Exemple avec llama-cpp-python ; remplacer `generate` par n'importe quel modèle (transformers, mlx, vllm offline…).

    pip install llama-cpp-python
    python adapters/callable_backend_example.py --gguf chemin/modele.gguf
Ou, sans code : MEMORYAICM_BACKEND=callable:adapters.callable_backend_example:generate  (la fonction doit exister sans argument)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent, Settings
from memoryaicm.llm.callable_backend import CallableBackend

_llm = None


def generate(system: str, messages: list[dict]) -> str:
    """Signature universelle : (system, messages) -> str."""
    global _llm
    if _llm is None:
        from llama_cpp import Llama
        _llm = Llama(model_path=generate.gguf, n_ctx=4096, verbose=False)
    out = _llm.create_chat_completion(messages=[{"role": "system", "content": system}, *messages], temperature=0.0, max_tokens=800)
    return out["choices"][0]["message"]["content"]


generate.gguf = ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gguf", required=True)
    p.add_argument("--home", default="data")
    a = p.parse_args()
    generate.gguf = a.gguf
    agent = MemoryAgent(Settings(root=Path(a.home)), backend=CallableBackend(generate))
    sid = agent.start_session()
    for text in ("je m'appelle Camille et j'utilise neovim", "comment je m'appelle ?"):
        r = agent.turn(text, sid)
        print(f"vous> {text}\nassistant> {r.answer}\n   [{r.write.summary()}]")
    agent.close()


if __name__ == "__main__":
    main()
