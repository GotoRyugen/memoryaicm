"""Gemini (SDK google-genai) : déclarations de fonctions dérivées des mêmes schémas, exécution par ToolRouter.

    pip install google-genai
    GEMINI_API_KEY=… python adapters/gemini_functions.py --model gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent, Settings
from memoryaicm.tools import ToolRouter, as_gemini, system_prompt


def main() -> None:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("pip install google-genai")
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="gemini-2.0-flash")
    p.add_argument("--home", default="data")
    a = p.parse_args()
    agent = MemoryAgent(Settings(root=Path(a.home)))
    router = ToolRouter(agent, origin="gemini")
    client = genai.Client()  # GEMINI_API_KEY / GOOGLE_API_KEY
    tools = [types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in as_gemini()[0]["function_declarations"]])]
    config = types.GenerateContentConfig(system_instruction=system_prompt() + "\n" + router.brief(), tools=tools,
                                         automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contents: list = []
    while True:
        try:
            line = input("vous> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/quit":
            break
        if not line:
            continue
        contents.append(types.Content(role="user", parts=[types.Part(text=line)]))
        for _ in range(6):
            resp = client.models.generate_content(model=a.model, contents=contents, config=config)
            cand = resp.candidates[0].content
            contents.append(cand)
            calls = [pt.function_call for pt in cand.parts if getattr(pt, "function_call", None)]
            if not calls:
                print("assistant>", resp.text)
                break
            parts = []
            for fc in calls:
                result, err = router.call_safe(fc.name, dict(fc.args or {}))
                parts.append(types.Part(function_response=types.FunctionResponse(name=fc.name, response={"result": result, "error": err})))
            contents.append(types.Content(role="user", parts=parts))
    router.close()


if __name__ == "__main__":
    main()
