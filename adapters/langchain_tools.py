"""LangChain / LangGraph : les onze outils mémoire comme StructuredTool, depuis les mêmes schémas.

    pip install langchain-core
    from adapters.langchain_tools import memory_tools
    tools = memory_tools(agent)          # → bind_tools(tools), create_react_agent(model, tools)…
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import MemoryAgent
from memoryaicm.tools import TOOL_SPECS, ToolRouter


def memory_tools(agent: MemoryAgent, session: str | None = None) -> list:
    from langchain_core.tools import StructuredTool

    router = ToolRouter(agent, session, origin="langchain")

    def make(name: str):
        def run(**kwargs):
            text, _err = router.call_safe(name, kwargs)
            return text
        run.__name__ = name
        return run

    return [StructuredTool.from_function(func=make(spec["name"]), name=spec["name"], description=spec["description"],
                                         args_schema=dict(spec["inputSchema"], title=spec["name"]))
            for spec in TOOL_SPECS]
