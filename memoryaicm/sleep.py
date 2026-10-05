"""SLEEP · idle · Log → Index → Adapter

Log[Δt] → extract(SEM) → dedup → merge(conflits, dates) → abstract
        → act<θ ⇒ on=0                          // oubli actif = désindexer
Adapter ← compile(Index) (+ train.jsonl pour replay/EWC externe)
tests   : rappel · injection · lignage · journal
        → promote | rollback ; versionné       // sommeil réversible
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import model as M
from .adapter import Adapter
from .bench import run_all
from .config import Settings
from .index import Index
from .llm.base import Backend
from .log import Journal
from .textutil import similarity, tokens


@dataclass
class SleepReport:
    deduped: list[tuple[str, str]] = field(default_factory=list)      # (gardé, fusionné)
    conflicts: list[str] = field(default_factory=list)                 # ids désactivés (doublon exclusif)
    abstracted: list[str] = field(default_factory=list)                # ids des abstractions créées
    sessions: list[str] = field(default_factory=list)                  # ids des résumés de session (EPI)
    pruned: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    version: int | None = None
    promoted: bool = False
    tests: dict = field(default_factory=dict)
    reason: str = "manual"

    def summary(self) -> str:
        return (f"[{self.reason}] sessions {len(self.sessions)} · dédoublonné {len(self.deduped)} · conflits {len(self.conflicts)} · "
                f"abstractions {len(self.abstracted)} · élagués {len(self.pruned)} · expirés {len(self.expired)} · "
                f"adaptateur v{self.version} {'promu' if self.promoted else 'rejeté (rollback)'}")


class Sleep:
    def __init__(self, journal: Journal, index: Index, backend: Backend, adapter: Adapter,
                 settings: Settings | None = None, tests_runner=None):
        self.journal, self.index, self.backend, self.adapter = journal, index, backend, adapter
        self.s = settings or Settings()
        self.tests_runner = tests_runner or (lambda: run_all(self.journal, self.index, self.backend, self.s))

    def run(self, now: float | None = None, reason: str = "manual") -> SleepReport:
        now = time.time() if now is None else now
        rep = SleepReport(reason=reason)
        self.index.sync()
        self._sessions(rep, now)
        self._dedup(rep, now)
        self._conflicts(rep, now)
        self._abstract(rep, now)
        self._prune(rep, now)

        rep.version = self.adapter.compile(reason=f"sleep:{reason}")
        ok, tests = self.tests_runner()
        rep.tests = tests
        if ok:
            self.adapter.promote(rep.version, tests)
            rep.promoted = True
        else:
            self.adapter.rollback(rep.version, reason="tests rouges", tests=tests)
        self.journal.append(M.EV_SLEEP, M.Actor.SYSTEM.value, {
            "reason": reason, "sessions": rep.sessions, "deduped": rep.deduped, "conflicts": rep.conflicts, "abstracted": rep.abstracted,
            "pruned": rep.pruned, "expired": rep.expired, "version": rep.version, "promoted": rep.promoted,
            "tests_pass": ok,
        }, ts=now)
        self.index.sync()
        return rep

    # ------------------------------------------------------------------ étapes
    def _sessions(self, rep: SleepReport, now: float) -> None:
        """Mémoire épisodique : chaque session depuis le dernier sommeil devient un fait EPI daté (TTL),
        avec les faits qu'elle a produits et ses sujets — pour « où en était-on ? »."""
        last = self.journal.last_of(M.EV_SLEEP)
        since = last.seq if last else 0
        turns: dict[str, list[M.Event]] = defaultdict(list)
        for ev in self.journal.replay(since_seq=since, types=(M.EV_TURN_USER,)):
            sid = ev.payload.get("session") or ""
            if sid and sid != "bench":
                turns[sid].append(ev)
        for sid, evs in turns.items():
            if len(evs) < self.s.session_summary_min_turns:
                continue
            ids = {e.id for e in evs}
            produced_facts = [f for f in self.index.all_facts(on_only=True, kinds=("SEM", "PROC")) if f.origin in ids]
            produced = [f.txt for f in produced_facts]
            words: dict[str, int] = defaultdict(int)
            for e in evs:
                for t in tokens(e.payload.get("text", "")):
                    words[t] += 1
            topics = [w for w, _ in sorted(words.items(), key=lambda x: (-x[1], x[0]))[:6]]
            day = time.strftime("%Y-%m-%d", time.localtime(evs[0].ts))
            txt = (f"session {sid} du {day} ({len(evs)} tours) : "
                   + ("retenu : " + " ; ".join(produced[:6]) if produced else "rien de durable")
                   + (" · sujets : " + ", ".join(topics) if topics else ""))
            subject = f"epi:session:{sid}"
            existing = self.index.by_subject(subject, on_only=True)
            fact = M.Fact(
                id=M.new_id("f"), kind=M.Kind.EPI, txt=txt, subject=subject, src=M.Src.USER,
                t0=evs[0].ts, t_upd=now, ver=(existing[0].ver + 1) if existing else 1, imp=0.4,
                ttl=self.s.ttl_by_kind.get("EPI", 0), deps=[f.id for f in produced_facts],  # lignage : oublier un fait
                sens=False, on=True, exclusive=True,                                          # retire le résumé qui le cite
                origin="sleep", session=sid, prev=(existing[0].prev + [existing[0].txt]) if existing else [],
            )
            if existing:
                fact.deps = sorted(set(fact.deps) | {existing[0].id})
                self.journal.append(M.EV_FACT_MERGE, M.Actor.SYSTEM.value,
                                    {"fact": fact.to_dict(), "supersedes": existing[0].id}, ts=now)
            else:
                self.journal.append(M.EV_FACT_WRITE, M.Actor.SYSTEM.value, {"fact": fact.to_dict()}, ts=now)
            rep.sessions.append(fact.id)
        self.index.sync()

    def _dedup(self, rep: SleepReport, now: float) -> None:
        """Deux faits actifs de même type quasi identiques ⇒ le plus récent absorbe l'autre (lignage conservé)."""
        facts = self.index.all_facts(on_only=True, kinds=("SEM", "PROC"))
        absorbed: set[str] = set()
        for i, a in enumerate(facts):
            if a.id in absorbed:
                continue
            for b in facts[i + 1:]:
                if b.id in absorbed or a.kind != b.kind or a.subject == b.subject:
                    continue
                if similarity(a.txt, b.txt) >= self.s.dedup_sim:
                    keep, drop = (a, b) if a.t_upd >= b.t_upd else (b, a)
                    merged = M.Fact(
                        id=M.new_id("f"), kind=keep.kind, txt=keep.txt, subject=keep.subject, src=M.Src.USER,
                        t0=min(a.t0, b.t0), t_upd=now, ver=keep.ver + 1, imp=max(a.imp, b.imp), ttl=keep.ttl,
                        deps=sorted({keep.id, drop.id}), sens=a.sens or b.sens, on=True, exclusive=keep.exclusive,
                        origin=keep.origin, session=keep.session, prev=keep.prev + [drop.txt],
                    )
                    self.journal.append(M.EV_FACT_MERGE, M.Actor.SYSTEM.value,
                                        {"fact": merged.to_dict(), "supersedes": keep.id, "absorbs": drop.id}, ts=now)
                    self.journal.append(M.EV_FACT_OFF, M.Actor.SYSTEM.value,
                                        {"ids": [drop.id], "reason": "dedup"}, ts=now)
                    absorbed.update({a.id, b.id})
                    rep.deduped.append((merged.id, drop.id))
                    break
        self.index.sync()

    def _conflicts(self, rep: SleepReport, now: float) -> None:
        """Un seul fait actif par sujet exclusif : le plus récent gagne, les autres sortent (datés)."""
        by_subject: dict[str, list[M.Fact]] = defaultdict(list)
        for f in self.index.all_facts(on_only=True):
            if f.exclusive:
                by_subject[f.subject].append(f)
        for subject, group in by_subject.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda f: (f.t_upd, f.ver), reverse=True)
            losers = [f.id for f in group[1:]]
            self.journal.append(M.EV_FACT_OFF, M.Actor.SYSTEM.value, {"ids": losers, "reason": "conflict"}, ts=now)
            rep.conflicts.extend(losers)
        self.index.sync()

    def _abstract(self, rep: SleepReport, now: float) -> None:
        """Groupes non exclusifs (outil:*, décision:*, pref:*) ⇒ une ligne d'abstraction, avec lignage."""
        groups: dict[str, list[M.Fact]] = defaultdict(list)
        for f in self.index.all_facts(on_only=True, kinds=("SEM", "PROC")):
            if ":" in f.subject and not f.subject.startswith("abs:"):
                groups[f.subject.split(":")[0]].append(f)
        for prefix, members in groups.items():
            if len(members) < self.s.abstract_min_group:
                continue
            existing = self.index.by_subject(f"abs:{prefix}", on_only=True)
            member_ids = sorted(f.id for f in members)
            if existing and sorted(existing[0].deps) == member_ids:
                continue  # déjà à jour
            txt = self.backend.abstract(members)
            if not txt:
                continue
            fact = M.Fact(
                id=M.new_id("f"), kind=members[0].kind, txt=txt, subject=f"abs:{prefix}", src=M.Src.USER,
                t0=min(f.t0 for f in members), t_upd=now, ver=(existing[0].ver + 1) if existing else 1,
                imp=max(f.imp for f in members), ttl=0, deps=member_ids, sens=any(f.sens for f in members),
                on=True, exclusive=True, origin="sleep", session="",
                prev=(existing[0].prev + [existing[0].txt]) if existing else [],
            )
            if existing:
                self.journal.append(M.EV_FACT_MERGE, M.Actor.SYSTEM.value,
                                    {"fact": fact.to_dict(), "supersedes": existing[0].id}, ts=now)
            else:
                self.journal.append(M.EV_FACT_WRITE, M.Actor.SYSTEM.value, {"fact": fact.to_dict()}, ts=now)
            rep.abstracted.append(fact.id)
        self.index.sync()

    def _prune(self, rep: SleepReport, now: float) -> None:
        """act_eff < θ ⇒ on=0 (désindexé, jamais détruit) ; ttl dépassé ⇒ idem."""
        pruned, expired = [], []
        for f in self.index.all_facts(on_only=True):
            if f.ttl and (now - f.t_upd) > f.ttl:
                expired.append(f.id)
            elif self.index.effective(f, now) < self.s.theta_off:
                pruned.append(f.id)
        if pruned:
            self.journal.append(M.EV_FACT_OFF, M.Actor.SYSTEM.value, {"ids": pruned, "reason": "prune"}, ts=now)
        if expired:
            self.journal.append(M.EV_FACT_OFF, M.Actor.SYSTEM.value, {"ids": expired, "reason": "ttl"}, ts=now)
        rep.pruned, rep.expired = pruned, expired
        self.index.sync()
