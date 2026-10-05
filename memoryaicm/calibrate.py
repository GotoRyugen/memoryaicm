"""CALIBRATE · W

fait      → Index avant W        // vérifier ses notes avant de faire confiance aux poids
abstention entraînée             // ici : abstention mécanique quand l'accord est faible
detect    : entropie sémantique lite (accord entre n échantillons)
confiance : ctx > note > W
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from .config import Settings
from .index import Index
from .llm.base import Backend, Context
from .model import Fact
from .textutil import jaccard, tokens

_FACTUAL_ABOUT_USER = re.compile(
    r"(\?|^\s*(?:quel|quelle|quels|quelles|qui|où|ou|comment|c'est quoi|what|where|which|who|how)\b).*"
    r"\b(mon|ma|mes|je|j'|moi|my|i|me)\b|\b(mon|ma|mes|my)\b.*\?", re.I | re.S,
)

ABSTAIN = "Je ne suis pas sûr de ça et je préfère ne pas deviner : mes réponses divergent d'un essai à l'autre. Dis-le-moi et je le noterai."


@dataclass
class Answer:
    text: str
    level: str                  # ctx | note | model | abstain
    agreement: float = 1.0
    samples: list[str] = field(default_factory=list)
    note: Fact | None = None


class Calibrator:
    def __init__(self, index: Index, backend: Backend, settings: Settings | None = None):
        self.index, self.backend = index, backend
        self.s = settings or Settings()

    @staticmethod
    def is_factual_about_user(text: str) -> bool:
        return bool(_FACTUAL_ABOUT_USER.search(text or ""))

    def answer(self, ctx: Context, retrieved: list[Fact]) -> Answer:
        user = ctx.user or ""
        if not self.is_factual_about_user(user):
            return Answer(self.backend.complete(ctx), level="ctx")

        # 1. route : une note pertinente répond (le modèle la cite, le validateur vérifie l'étiquette)
        best = self._best_note(user, retrieved)
        if best is not None:
            return Answer(self.backend.complete(ctx), level="note", note=best)

        # 2. pas de note : on mesure l'accord entre échantillons avant de faire confiance aux poids
        samples = self.backend.sample(ctx, n=self.s.entropy_samples)
        agreement = self.agreement(samples)
        if agreement < self.s.agreement_min:
            return Answer(ABSTAIN, level="abstain", agreement=agreement, samples=samples)
        return Answer(samples[0], level="model", agreement=agreement, samples=samples)

    def _best_note(self, user: str, retrieved: list[Fact]) -> Fact | None:
        q = tokens(user)
        best, best_rel = None, 0.0
        for f in retrieved:
            doc = tokens(f.txt + " " + f.subject.replace(":", " ").replace(".", " "))
            rel = len(q & doc) / float(len(q)) if q else 0.0
            if rel > best_rel:
                best, best_rel = f, rel
        return best if best_rel >= self.s.note_answer_rel else None

    @staticmethod
    def agreement(samples: list[str]) -> float:
        """Entropie sémantique lite : accord moyen (Jaccard) entre toutes les paires d'échantillons."""
        if len(samples) < 2:
            return 1.0
        toks = [tokens(s) for s in samples]
        pairs = list(combinations(range(len(toks)), 2))
        return sum(jaccard(toks[i], toks[j]) for i, j in pairs) / len(pairs)
