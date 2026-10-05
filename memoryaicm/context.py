"""L2 · CONTEXTE — Ctx = [ sys · notes{f+label} · hist · prefs@fin · user ]

- notes : top-k depuis l'Index, chacune avec son étiquette (jamais un fait nu)
- hist  : vue RÉDIGÉE de la session — les tours « oubliés » n'y figurent plus (le journal les garde)
- compaction : au-delà de N tours, l'ancien est remplacé par les faits qu'il a produits (décisions, contraintes)
- prefs : PROC, réinjectées en fin (récence > position)
- externe : donnée délimitée, jamais instruction
"""

from __future__ import annotations

from . import model as M
from .config import Settings
from .index import Index
from .llm.base import Context, SYSTEM_BASE
from .log import Journal


class ContextBuilder:
    def __init__(self, journal: Journal, index: Index, settings: Settings | None = None,
                 max_history_turns: int = 20, profile_loader=None):
        self.journal, self.index = journal, index
        self.s = settings or Settings()
        self.max_history_turns = max_history_turns
        self.profile_loader = profile_loader  # () -> str | None : profil de l'adaptateur promu

    def build(self, session: str, user_text: str, externals: list[tuple[str, str]] | None = None,
              exclude_turn: str | None = None) -> tuple[Context, list[M.Fact]]:
        system = SYSTEM_BASE
        profile = self.profile_loader() if self.profile_loader else None
        if profile:
            system += "\n\n## PROFIL COMPILÉ (adaptateur, dérivé de l'index — l'index prime en cas de désaccord)\n" + profile

        scored = self.index.retrieve(user_text)
        notes = [(f, f.label()) for f, _, _ in scored]
        history = self._history(session, exclude_turn)
        ctx = Context(system=system, notes=notes, history=history, externals=list(externals or []),
                      prefs=self.index.prefs(), user=user_text)
        return ctx, [f for f, _, _ in scored]

    # ------------------------------------------------------------------ vue rédigée + compaction
    def _history(self, session: str, exclude_turn: str | None) -> list[tuple[str, str]]:
        redacted = self.index.redacted_turns(session)
        turns: list[tuple[str, str, str]] = []  # (event_id, role, text)
        for ev in self.journal.replay(types=(M.EV_TURN_USER, M.EV_TURN_ASSISTANT)):
            if ev.payload.get("session") != session or ev.id == exclude_turn or ev.id in redacted:
                continue
            role = "user" if ev.type == M.EV_TURN_USER else "assistant"
            turns.append((ev.id, role, ev.payload.get("text", "")))
        if len(turns) <= self.max_history_turns:
            return [(r, t) for _, r, t in turns]
        old, recent = turns[: -self.max_history_turns], turns[-self.max_history_turns:]
        old_ids = {i for i, _, _ in old}
        kept = [f.txt for f in self.index.all_facts(on_only=True) if f.origin in old_ids]
        summary = "[résumé des tours précédents — décisions et contraintes retenues] " + (
            " ; ".join(kept) if kept else "(rien de durable)"
        )
        return [("user", summary)] + [(r, t) for _, r, t in recent]
