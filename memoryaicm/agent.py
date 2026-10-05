"""Orchestration d'un tour : tout passe par le journal.

tour user → Log → WRITE (porte, durabilité, conflit, saillance, sensible) → Index
          → Ctx = [sys · notes · hist(vue rédigée) · externe=donnée · prefs@fin · user]
          → CALIBRATE (route note | accord | abstention) → génération → VALIDATEUR → Log
« oublie x » → événement forget + cascade lignage + vue rédigée + recompilation de l'adaptateur
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import model as M
from .adapter import Adapter
from .calibrate import Calibrator
from .config import Settings, load_dotenv
from .context import ContextBuilder
from .embed import get_embedder
from .guard import Guard, GuardResult
from .index import Index
from .llm import get_backend
from .llm.base import Backend, Candidate
from .log import Journal
from .models import ModelApprovals
from .policy import WritePolicy, WriteReport
from .sleep import Sleep, SleepReport
from .textutil import similarity, slug, tokens, overlap

_FORGET = re.compile(r"^\s*(?:/?oublie|/?forget)\s+(.+?)\s*[.!]?\s*$", re.I)
_SLEEP = re.compile(r"^\s*/?(?:sommeil|dors|sleep)\s*$", re.I)


class PolicyRefused(RuntimeError):
    """Action refusée par le niveau d'autonomie (journalisée en policy.refuse)."""


@dataclass
class TurnResult:
    answer: str
    write: WriteReport
    used: list[M.Fact] = field(default_factory=list)
    level: str = "ctx"
    agreement: float = 1.0
    guard: GuardResult | None = None
    forgotten: list[str] = field(default_factory=list)
    sleep: SleepReport | None = None


@dataclass
class ForgetReport:
    query: str
    ids: list[str] = field(default_factory=list)
    cascade: list[str] = field(default_factory=list)
    redacted_turns: list[str] = field(default_factory=list)
    adapter_version: int | None = None

    def summary(self) -> str:
        if not self.ids:
            return f"rien ne correspond à « {self.query} » dans l'index actif"
        return (f"désindexé {len(self.ids)} fait(s) + {len(self.cascade)} dérivé(s) ; "
                f"{len(self.redacted_turns)} tour(s) retiré(s) de la vue ; adaptateur recompilé v{self.adapter_version} ; "
                f"journal intact")


class MemoryAgent:
    def __init__(self, settings: Settings | None = None, backend: Backend | None = None,
                 guard: Guard | None = None):
        load_dotenv()
        self.s = settings or Settings()
        self.s.ensure_dirs()
        self.journal = Journal(self.s.journal_path)
        self.index = Index(self.s.index_path, self.journal, self.s, embedder=get_embedder(self.s))
        self.selfcheck_result: dict = self.selfcheck() if self.s.selfcheck_on_start else {}
        self.index.sync()
        self.approvals = ModelApprovals(self.s, self.journal)   # règle Echo-Core : modèle local approuvé par SHA-256
        self.backend: Backend = backend or get_backend(self.s, approvals=self.approvals)
        self.policy = WritePolicy(self.journal, self.index, self.backend, self.s)
        self.adapter = Adapter(self.s, self.journal, self.index)
        self.ctx = ContextBuilder(self.journal, self.index, self.s, profile_loader=self.adapter.profile)
        self.guard = guard or Guard(self.s)
        self.calibrator = Calibrator(self.index, self.backend, self.s)
        self.sleeper = Sleep(self.journal, self.index, self.backend, self.adapter, self.s)
        self._externals: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.last_auto_sleep: SleepReport | None = None

    # ------------------------------------------------------------------ autonomie
    def selfcheck(self) -> dict:
        """Au démarrage, sans rien demander : chaîne vérifiée ; index absent ou en avance ⇒ reconstruit."""
        ok, msg = self.journal.verify()
        rebuilt = False
        applied, last = self.index.applied_seq(), self.journal.last_seq()
        if last and (applied == 0 or applied > last):
            self.index.rebuild()
            rebuilt = True
        res = {"chain_ok": ok, "chain": msg, "index_rebuilt": rebuilt, "applied_seq": applied, "journal_seq": last}
        if last and (rebuilt or not ok):
            self.journal.append(M.EV_SELFCHECK, M.Actor.SYSTEM.value, res)
        return res

    def _sleep_due(self, at_session_start: bool = False) -> str | None:
        """Motif du sommeil automatique, ou None. Ne dort que s'il y a du nouveau depuis le dernier sommeil."""
        last = self.journal.last_of(M.EV_SLEEP)
        since = last.seq if last else 0
        new_turns = self.journal.count_since(M.EV_TURN_USER, since)
        if new_turns == 0:
            return None
        if self.s.auto_sleep_every_turns and new_turns >= self.s.auto_sleep_every_turns:
            return "auto:turns"
        if at_session_start and last is not None and (M.now() - last.ts) >= self.s.auto_sleep_idle_s:
            return "auto:idle"
        return None

    def maybe_sleep(self, at_session_start: bool = False) -> SleepReport | None:
        if self.s.autonomy < 3:  # les routines (sommeil automatique) commencent au niveau 3
            return None
        reason = self._sleep_due(at_session_start)
        if reason is None:
            return None
        self.last_auto_sleep = self.sleep(reason=reason)
        return self.last_auto_sleep

    # ------------------------------------------------------------------ sessions
    def start_session(self, name: str | None = None) -> str:
        sid = name or M.new_id("s")
        self.journal.append(M.EV_SESSION, M.Actor.SYSTEM.value, {"session": sid})
        self.maybe_sleep(at_session_start=True)
        return sid

    # ------------------------------------------------------------------ tour
    def turn(self, text: str, session: str) -> TurnResult:
        self.index.sync()  # un autre processus (ACA, MCP, hook) a pu écrire dans le journal
        ev = self.journal.append(M.EV_TURN_USER, M.Actor.USER.value, {"session": session, "text": text})

        m = _FORGET.match(text)
        if m:
            try:
                fr = self.forget(m.group(1), session=session)
            except PolicyRefused as e:
                answer = str(e)
                self.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                    {"session": session, "text": answer, "used": [], "level": "refused"})
                return TurnResult(answer=answer, write=WriteReport(), level="refused")
            answer = "D'accord. " + fr.summary() + "."
            reply = self.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                        {"session": session, "text": answer, "used": [], "level": "forget"})
            # « oublie x » redit x : la commande et sa réponse sortent aussi de la vue (éléphant rose)
            self.journal.append(M.EV_TURN_REDACT, M.Actor.USER.value,
                                {"session": session, "turn_ids": [ev.id, reply.id], "reason": "forget-command"})
            self.index.sync()
            return TurnResult(answer=answer, write=WriteReport(), level="forget", forgotten=fr.ids + fr.cascade)
        if _SLEEP.match(text):
            try:
                sr = self.sleep()
            except PolicyRefused as e:
                answer = str(e)
                self.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                    {"session": session, "text": answer, "used": [], "level": "refused"})
                return TurnResult(answer=answer, write=WriteReport(), level="refused")
            answer = "Sommeil terminé : " + sr.summary() + "."
            self.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                {"session": session, "text": answer, "used": [], "level": "sleep"})
            return TurnResult(answer=answer, write=WriteReport(), level="sleep", sleep=sr)

        write = self.policy.from_event(ev)
        externals = self._externals.pop(session, [])
        ctx, used = self.ctx.build(session, text, externals=externals, exclude_turn=ev.id)
        ans = self.calibrator.answer(ctx, used)
        g = self.guard.validate(ans.text, ctx)
        answer = g.output
        if not g.ok:
            self.journal.append(M.EV_GUARD_BLOCK, M.Actor.SYSTEM.value,
                                {"session": session, "reasons": g.reasons, "original": ans.text[:500]})

        low = answer.lower()
        useful = [f.id for f in used if f.id in answer or f.txt.lower() in low]
        ids = [f.id for f in used] + [p.id for p in ctx.prefs]
        if ids:
            self.journal.append(M.EV_FACT_USE, M.Actor.SYSTEM.value, {"ids": ids, "useful": useful})
        self.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value, {
            "session": session, "text": answer, "used": [f.id for f in used], "level": ans.level,
            "agreement": ans.agreement, "blocked": not g.ok,
        })
        self.index.sync()
        auto = self.maybe_sleep()  # entretien automatique : après N tours, le système dort seul
        return TurnResult(answer=answer, write=write, used=used, level=ans.level, agreement=ans.agreement,
                          guard=g, sleep=auto)

    # ------------------------------------------------------------------ externe = donnée
    def ingest_external(self, session: str, source: str, content: str) -> bool:
        """Retourne True si une tentative d'instruction a été mise en quarantaine."""
        ev = self.journal.append(M.EV_EXTERNAL, M.Actor.EXTERNAL.value,
                                 {"session": session, "source": source, "content": content})
        self.policy.from_event(ev)   # jamais de fait ; quarantaine si ça ressemble à une instruction
        self._externals[session].append((source, content))
        return any(e.type == M.EV_QUARANTINE and e.payload.get("event") == ev.id
                   for e in self.journal.replay(since_seq=ev.seq))

    # ------------------------------------------------------------------ oubli
    def forget(self, query: str, session: str = "") -> ForgetReport:
        self._require_autonomy(1, "oubli")
        fr = ForgetReport(query=query)
        self.index.sync()
        targets = self._resolve(query)
        if not targets:
            return fr
        ids = [f.id for f in targets]
        cascade: list[str] = []
        for fid in ids:
            for d in self.index.dependents(fid):
                if d not in ids and d not in cascade:
                    cascade.append(d)
        self.journal.append(M.EV_FACT_FORGET, M.Actor.USER.value, {"ids": ids, "cascade": cascade, "query": query})

        # vue rédigée : les tours d'origine (et la réponse qui a suivi) sortent du contexte, pas du journal
        origins = {f.origin for f in targets if f.origin and f.origin.startswith("ev_")}
        turn_ids = self._turns_to_redact(origins)
        if turn_ids:
            self.journal.append(M.EV_TURN_REDACT, M.Actor.USER.value,
                                {"session": session, "turn_ids": turn_ids, "reason": "forget"})
        self.index.sync()

        # Adapter ← compile(Index) ⇒ x ∉ W
        v = self.adapter.compile(reason="forget")
        self.adapter.promote(v, tests={"skipped": "recompilation après oubli"})
        fr.ids, fr.cascade, fr.redacted_turns, fr.adapter_version = ids, cascade, turn_ids, v
        return fr

    def _resolve(self, query: str) -> list[M.Fact]:
        q = query.strip()
        if re.fullmatch(r"f_[0-9a-f]{12}", q):
            f = self.index.fact(q)
            return [f] if f and f.on else []
        qt = tokens(q)
        out = []
        for f in self.index.all_facts(on_only=True):
            doc = tokens(f.txt + " " + f.subject.replace(":", " "))
            if overlap(qt, doc) >= 0.5 or similarity(q, f.txt) >= 0.5 or f.subject == q.lower():
                out.append(f)
        # un fait dérivé d'un autre fait ciblé sort par la cascade, pas comme cible directe
        ids = {f.id for f in out}
        derived = set()
        for f in out:
            derived.update(d for d in self.index.dependents(f.id) if d in ids)
        return [f for f in out if f.id not in derived]

    def _turns_to_redact(self, origins: set[str]) -> list[str]:
        if not origins:
            return []
        out, pending_session = [], None
        for ev in self.journal.replay(types=(M.EV_TURN_USER, M.EV_TURN_ASSISTANT)):
            if ev.id in origins:
                out.append(ev.id)
                pending_session = ev.payload.get("session")
            elif pending_session and ev.type == M.EV_TURN_ASSISTANT and ev.payload.get("session") == pending_session:
                out.append(ev.id)
                pending_session = None
        return out

    # ------------------------------------------------------------------ sommeil, revue, état
    def sleep(self, reason: str = "manual") -> SleepReport:
        self._require_autonomy(1, "sommeil")
        return self.sleeper.run(reason=reason)

    # ------------------------------------------------------------------ niveaux d'autonomie (Echo-Core)
    AUTONOMY_LABELS = {0: "observer (lecture seule)", 1: "proposer (chaque fait attend validation)",
                       2: "exécuter avec confirmation (sensible ⇒ validation)", 3: "routines (sommeil automatique)"}

    def _require_autonomy(self, level: int, action: str) -> None:
        if self.s.autonomy < level:
            self.journal.append(M.EV_POLICY_REFUSE, M.Actor.SYSTEM.value,
                                {"level": self.s.autonomy, "action": action, "required": level})
            raise PolicyRefused(f"{action} refusé : niveau d'autonomie {self.s.autonomy} "
                                f"({self.AUTONOMY_LABELS.get(self.s.autonomy, '?')}) ; requis ≥ {level}")

    def review(self, fact_id: str, approve: bool) -> None:
        self.journal.append(M.EV_REVIEW_DECIDE, M.Actor.USER.value, {"fact_id": fact_id, "approve": approve})
        self.index.sync()

    def reactivate(self, fact_id: str) -> None:
        """Réactive un fait désindexé (l'inverse d'« oublie ») — lui aussi un simple événement."""
        self.journal.append(M.EV_FACT_ON, M.Actor.USER.value, {"ids": [fact_id], "reason": "recall"})
        self.index.sync()

    # ------------------------------------------------------------------ mode add-on : Claude génère, la mémoire retient
    def remember(self, user_text: str, session: str, facts: list[dict] | None = None) -> WriteReport:
        """Un tour utilisateur sans génération : journal + politique d'écriture + sommeil auto.

        `facts` : candidats extraits par le client (Claude) ; chacun doit être ancré dans `user_text`.
        Sans `facts`, l'extraction revient au backend local.
        """
        self.index.sync()
        ev = self.journal.append(M.EV_TURN_USER, M.Actor.USER.value, {"session": session, "text": user_text})
        m = _FORGET.match(user_text)
        if m:
            try:
                fr = self.forget(m.group(1), session=session)
            except PolicyRefused as e:
                rep = WriteReport(); rep.refused = str(e)
                return rep
            self.journal.append(M.EV_TURN_REDACT, M.Actor.USER.value,
                                {"session": session, "turn_ids": [ev.id], "reason": "forget-command"})
            self.index.sync()
            rep = WriteReport()
            rep.forgotten = fr.ids + fr.cascade
            return rep
        if facts is not None:
            cands = [_candidate_from_dict(d) for d in facts if isinstance(d, dict) and d.get("txt")]
            rep = self.policy.from_candidates(cands, session=session, origin=ev.id, ts=ev.ts, ground_in=user_text)
        else:
            rep = self.policy.from_event(ev)
        self.maybe_sleep()
        return rep

    def recall_notes(self, query: str, k: int | None = None, session: str = "") -> tuple[list[M.Fact], list[M.Fact]]:
        """Notes pertinentes (top-k, étiquetées) + préférences ; la récupération compte comme un usage (ACT-R)."""
        self.index.sync()
        scored = self.index.retrieve(query, k=k)
        notes = [f for f, _, _ in scored]
        prefs = self.index.prefs()
        ids = [f.id for f in notes] + [p.id for p in prefs]
        if ids:  # pas de « session » ici : une récupération n'est pas une réénonciation
            self.journal.append(M.EV_FACT_USE, M.Actor.SYSTEM.value, {"ids": ids, "useful": [], "reason": "recall"})
            self.index.sync()
        return notes, prefs


    def status(self) -> dict:
        ok, msg = self.journal.verify()
        active = self.index.all_facts(on_only=True)
        last_sleep = self.journal.last_of(M.EV_SLEEP)
        since = last_sleep.seq if last_sleep else 0
        return {
            "backend": self.backend.name, "journal_events": len(self.journal), "journal_chain": msg,
            "facts_active": len(active), "facts_total": len(self.index.all_facts(on_only=False)),
            "pending_reviews": len(self.index.pending_reviews()), "adapter_current": self.adapter.current(),
            "adapter_versions": self.adapter.versions(),
            "selfcheck": self.selfcheck_result,
            "last_sleep": (last_sleep.payload.get("reason"), last_sleep.ts) if last_sleep else None,
            "turns_since_sleep": self.journal.count_since(M.EV_TURN_USER, since),
            "auto_sleep_every_turns": self.s.auto_sleep_every_turns,
            "autonomy": {"level": self.s.autonomy, "label": self.AUTONOMY_LABELS.get(self.s.autonomy, "?")},
            "models_approved": len(self.approvals.list()),
        }

    def close(self) -> None:
        self.index.close(); self.journal.close()


def _candidate_from_dict(d: dict) -> Candidate:
    kind = str(d.get("kind", "SEM")).upper()
    return Candidate(
        txt=str(d["txt"]).strip(), subject=str(d.get("subject") or "autre:" + slug(str(d["txt"]))).strip().lower(),
        kind=kind if kind in ("SEM", "PROC", "EPI") else "SEM",
        src="INFER" if str(d.get("src", "USER")).upper() == "INFER" else "USER",
        exclusive=bool(d.get("exclusive", True)), durable=bool(d.get("durable", True)),
        sens=bool(d.get("sens", False)), explicit=bool(d.get("explicit", False)),
    )
