"""WRITE · Ctx → Log → Index

src ≠ tour_user            → quarantaine        // injection = donnée
¬durable(x) ∨ src=INFER    → skip
conflit(x, y∈Index)        → y' = merge(y,x) ; ver++ ; y.on=0 ; y'.deps ∋ y
imp↑ ⇐ « retiens » | vu ≥3 sessions | surprise>θ | a changé une réponse
sens(x)                    → file de validation humaine
∀x : append(Log) ⇒ horodaté · attribué · auditable
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import model as M
from .config import Settings
from .index import Index
from .llm.base import Backend, Candidate
from .log import Journal
from .textutil import overlap, similarity, tokens

_INSTRUCTION_LIKE = re.compile(
    r"(ignore\w*\s+(?:les|toutes les|all|previous|prior|the)?\s*(?:instructions?|consignes?|règles?)|"
    r"\b(?:retiens|souviens[- ]toi|rappelle[- ]toi|remember|note que|note that|à partir de maintenant|from now on|"
    r"oublie|forget|system prompt|you are now|tu es (?:maintenant|désormais)|nouvelle(?:s)? (?:instruction|consigne)s?)\b)",
    re.I,
)


@dataclass
class WriteReport:
    written: list[M.Fact] = field(default_factory=list)
    merged: list[tuple[M.Fact, M.Fact]] = field(default_factory=list)     # (nouveau, remplacé)
    reinforced: list[M.Fact] = field(default_factory=list)                 # redit à l'identique
    queued: list[M.Fact] = field(default_factory=list)                     # sensibles, en attente
    skipped: list[tuple[Candidate, str]] = field(default_factory=list)
    quarantined: bool = False
    forgotten: list[str] = field(default_factory=list)                     # « oublie x » reçu via remember()
    refused: str = ""                                                      # refus par le niveau d'autonomie

    def summary(self) -> str:
        parts = []
        if self.refused:
            parts.append(self.refused)
        if self.forgotten:
            parts.append(f"oublié (désindexé) : {len(self.forgotten)} fait(s)")
        if self.written:
            parts.append("écrit : " + " ; ".join(f.txt for f in self.written))
        if self.merged:
            parts.append("mis à jour : " + " ; ".join(f"{n.txt} (v{n.ver}, remplace « {o.txt} »)" for n, o in self.merged))
        if self.reinforced:
            parts.append("renforcé : " + " ; ".join(f.txt for f in self.reinforced))
        if self.queued:
            parts.append("en attente de validation (sensible) : " + " ; ".join(f.txt for f in self.queued))
        if self.skipped:
            parts.append("ignoré : " + " ; ".join(f"{c.txt} [{r}]" for c, r in self.skipped))
        if self.quarantined:
            parts.append("quarantaine : contenu non-utilisateur, rien n'est écrit")
        return " | ".join(parts) if parts else "rien à mémoriser"


_PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|<[^>]{1,40}>|\{[^}]{1,40}\}|\.\.\.|…|\b(?:xxx|todo|tbd|n/a|inconnu|unknown|non précisé|non precise)\b", re.I)


def looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION_LIKE.search(text or ""))


def looks_like_placeholder(text: str) -> bool:
    """« s'appelle [nom] », « travaille sur <travail> », « habite à … » : un gabarit, pas un fait."""
    t = (text or "").strip()
    return not re.search(r"[a-zA-Zà-ÿ]{2}", t) or bool(_PLACEHOLDER.search(t))


class WritePolicy:
    def __init__(self, journal: Journal, index: Index, backend: Backend, settings: Settings | None = None):
        self.journal, self.index, self.backend = journal, index, backend
        self.s = settings or Settings()

    # ------------------------------------------------------------------ porte
    def from_event(self, ev: M.Event) -> WriteReport:
        """Seul un tour de l'utilisateur peut produire des faits. Tout le reste est une donnée."""
        rep = WriteReport()
        if ev.actor != M.Actor.USER.value:
            rep.quarantined = True
            if looks_like_instruction(ev.payload.get("text") or ev.payload.get("content") or ""):
                self.journal.append(M.EV_QUARANTINE, M.Actor.SYSTEM.value, {
                    "source": ev.payload.get("source", ev.actor), "event": ev.id,
                    "reason": "tentative d'instruction mémoire depuis une source non-utilisateur",
                    "snippet": (ev.payload.get("text") or ev.payload.get("content") or "")[:200],
                })
            return rep
        return self.from_text(ev.payload.get("text", ""), session=ev.payload.get("session", ""), origin=ev.id, ts=ev.ts)

    # ------------------------------------------------------------------ écriture
    def from_text(self, text: str, session: str, origin: str, ts: float | None = None) -> WriteReport:
        # les candidats du backend sont ancrés dans le message comme ceux d'un client : « dit, pas déduit »
        return self.from_candidates(self._extract(text, ts), session=session, origin=origin, ts=ts, ground_in=text, ground_min=0.3)

    def _extract(self, text: str, ts: float | None = None) -> list[Candidate]:
        """Extraction par le backend ; s'il tombe en panne (modèle absent, réseau…), les règles locales prennent
        le relais et la panne est journalisée — un tour n'échoue jamais à cause du modèle."""
        try:
            return self.backend.extract(text)
        except Exception as e:  # noqa: BLE001
            self.journal.append(M.EV_SELFCHECK, M.Actor.SYSTEM.value,
                                {"backend_error": f"{getattr(self.backend, 'name', '?')}: {e!r}"[:300], "fallback": "stub"}, ts=ts)
            from .llm.stub import StubBackend
            return StubBackend().extract(text)

    def from_candidates(self, candidates: list[Candidate], session: str, origin: str, ts: float | None = None,
                        ground_in: str | None = None, ground_min: float = 0.5) -> WriteReport:
        """Applique la politique à des candidats (extraits par le backend, ou fournis par un client MCP).

        `ground_in` : texte du tour utilisateur ; un candidat dont les mots ne s'y retrouvent pas
        (≥ `ground_min` : 50 % pour un client, 30 % pour le backend qui reformule) est rejeté — « dit, pas déduit ».
        """
        rep = WriteReport()
        ts = M.now() if ts is None else ts
        ground = tokens(ground_in) if ground_in is not None else None
        if self.s.autonomy <= 0 and candidates:  # niveau 0 : observer — la mémoire ne s'écrit pas
            for c in candidates:
                rep.skipped.append((c, "autonomie 0 : lecture seule"))
            self.journal.append(M.EV_POLICY_REFUSE, M.Actor.SYSTEM.value,
                                {"level": self.s.autonomy, "action": "write", "count": len(candidates), "session": session}, ts=ts)
            return rep
        for c in candidates:
            if c.src == "INFER":
                rep.skipped.append((c, "déduit, pas dit")); continue
            if looks_like_placeholder(c.txt):
                rep.skipped.append((c, "gabarit / valeur manquante")); continue
            if not c.durable and not c.explicit:
                rep.skipped.append((c, "éphémère")); continue
            if ground is not None and overlap(tokens(c.txt), ground) < ground_min:
                rep.skipped.append((c, "non ancré dans le message de l'utilisateur")); continue

            existing = self.index.by_subject(c.subject, on_only=True)
            same = [f for f in existing if similarity(f.txt, c.txt) >= self.s.dedup_sim]
            if same:  # redit à l'identique : on renforce (ACT-R) et on note la session
                f = same[0]
                self.journal.append(M.EV_FACT_USE, M.Actor.SYSTEM.value,
                                    {"ids": [f.id], "useful": [], "session": session, "reason": "restated"}, ts=ts)
                rep.reinforced.append(f)
                continue

            imp = self._importance(c, session)
            kind = M.Kind(c.kind) if c.kind in ("SEM", "PROC", "EPI") else M.Kind.SEM
            conflict = existing[0] if (existing and c.exclusive) else None
            if conflict is not None:  # reconsolidation : y' = merge(y, x)
                new = M.Fact(
                    id=M.new_id("f"), kind=kind, txt=c.txt, subject=c.subject, src=M.Src.USER,
                    t0=conflict.t0, t_upd=ts, ver=conflict.ver + 1, imp=max(imp, conflict.imp),
                    ttl=self.s.ttl_by_kind.get(kind.value, 0), deps=[conflict.id], sens=c.sens or conflict.sens,
                    on=True, exclusive=c.exclusive, origin=origin, session=session,
                    prev=conflict.prev + [conflict.txt],
                )
                self.journal.append(M.EV_FACT_MERGE, M.Actor.USER.value,
                                    {"fact": new.to_dict(), "supersedes": conflict.id}, ts=ts)
                rep.merged.append((new, conflict))
                fact = new
            else:
                fact = M.Fact(
                    id=M.new_id("f"), kind=kind, txt=c.txt, subject=c.subject, src=M.Src.USER,
                    t0=ts, t_upd=ts, ver=1, imp=imp, ttl=self.s.ttl_by_kind.get(kind.value, 0), deps=[],
                    sens=c.sens, on=True, exclusive=c.exclusive, origin=origin, session=session,
                )
                self.journal.append(M.EV_FACT_WRITE, M.Actor.USER.value, {"fact": fact.to_dict()}, ts=ts)
                rep.written.append(fact)

            if c.sens or self.s.autonomy <= 1:  # sensible (ou niveau 1 : proposer) ⇒ hors index tant qu'un humain n'a pas validé
                self.journal.append(M.EV_REVIEW_QUEUE, M.Actor.SYSTEM.value,
                                    {"fact_id": fact.id, "reason": "sensible" if c.sens else "autonomie 1 : proposition"}, ts=ts)
                rep.queued.append(fact)
        self.index.sync()
        return rep

    # ------------------------------------------------------------------ saillance
    def _importance(self, c: Candidate, session: str) -> float:
        imp = self.s.imp_explicit if c.explicit else self.s.imp_default
        # répétition : le même sujet énoncé dans ≥ N sessions distinctes
        sessions: set[str] = {session} if session else set()
        for f in self.index.by_subject(c.subject, on_only=False):
            sessions.update(self.index.sessions_of(f.id))
        if len(sessions) >= self.s.repeat_sessions:
            imp += self.s.imp_repeat_bonus
        # surprise : nouveauté par rapport à tout ce qui est actif
        _, sim = self.index.most_similar(c.txt)
        if (1.0 - sim) > self.s.surprise_theta and self.index.all_facts(on_only=True):
            imp += self.s.surprise_bonus
        return min(1.0, imp)
