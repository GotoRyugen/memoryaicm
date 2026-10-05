import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryaicm import Settings, MemoryAgent  # noqa: E402
from memoryaicm.llm.base import Candidate, Context  # noqa: E402
from memoryaicm.llm.stub import StubBackend  # noqa: E402
from memoryaicm.model import Fact  # noqa: E402


class FakeBackend:
    """Backend pilotable : candidats fixés, réponses fixées, échantillons fixés."""
    name = "fake"

    def __init__(self, candidates=None, answer="ok", samples=None, abstract_txt=None):
        self.candidates = candidates or []
        self.answer = answer
        self.samples = samples
        self.abstract_txt = abstract_txt
        self.calls = []

    def extract(self, text):
        self.calls.append(("extract", text))
        return list(self.candidates)

    def complete(self, ctx: Context, temperature: float = 0.0):
        self.calls.append(("complete", ctx))
        return self.answer(ctx) if callable(self.answer) else self.answer

    def abstract(self, facts: list[Fact]):
        return self.abstract_txt or StubBackend().abstract(facts)

    def sample(self, ctx: Context, n: int = 3):
        if self.samples is not None:
            return list(self.samples)[:n]
        return [self.complete(ctx) for _ in range(n)]


@pytest.fixture(autouse=True)
def _neutral_environment(monkeypatch):
    """Aucun test ne dépend du .env ni des variables de la machine (clé API, serveur local, autonomie…)."""
    monkeypatch.setenv("MEMORYAICM_NO_DOTENV", "1")
    for k in list(os.environ):
        if k.startswith("MEMORYAICM_") and k != "MEMORYAICM_NO_DOTENV" or k in ("ANTHROPIC_API_KEY", "OLLAMA_URL", "OLLAMA_MODEL"):
            monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def settings(tmp_path):
    return Settings(root=tmp_path / "data")


@pytest.fixture
def agent(settings):
    a = MemoryAgent(settings, backend=StubBackend())
    yield a
    a.close()


def make_agent(settings, backend):
    return MemoryAgent(settings, backend=backend)


def cand(txt, subject, kind="SEM", src="USER", exclusive=True, durable=True, sens=False, explicit=False):
    return Candidate(txt=txt, subject=subject, kind=kind, src=src, exclusive=exclusive, durable=durable, sens=sens, explicit=explicit)
