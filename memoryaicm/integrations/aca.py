"""memoryaicm comme mémoire longue d'ACA (Architecture Cognitive Adaptative) / Echo-Core.

ACA injecte sa mémoire par deux objets : un `LongTermMemoryPort` (remember / query / recent / count)
et un `MemoryCoordinator` (retrieve avant raisonnement, commit après décision). Ce module fournit les
deux, avec exactement les mêmes signatures et les mêmes formes de `to_dict()` que `aca.memory`, pour
un branchement en trois lignes dans le lanceur :

    from memoryaicm.integrations.aca import MemoryaicmLongTermMemory, MemoryaicmCoordinator
    store = MemoryaicmLongTermMemory("/chemin/vers/memoryaicm/data")       # même journal que Claude (MCP) si on veut
    runtime = ACARuntime(..., memory_store=store, memory_coordinator=MemoryaicmCoordinator())

Ce qu'ACA gagne : journal append-only chaîné, index ACT-R, politique d'écriture (dit ≠ déduit, conflit
⇒ version, sensible ⇒ revue), sommeil (dédoublonnage, abstraction, résumés de session, élagage),
oubli par désindexation avec lignage, récupération hybride, historique daté. Ce que memoryaicm garde :
le contrat épistémique d'ACA — un souvenir est une observation avec provenance et confiance, jamais
une vérité ; la demande courante et les preuves récentes priment.

Si `aca.memory` est importable, ses classes sont utilisées telles quelles (isinstance compatible) ;
sinon des équivalents locaux de même forme prennent le relais.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import model as M
from ..agent import MemoryAgent
from ..config import Settings
from ..textutil import slug, tokens

# ----------------------------------------------------------------------------- classes ACA (réelles ou équivalentes)
try:  # pragma: no cover - dépend de l'environnement
    from aca.memory import MemoryContext, MemoryHit, MemoryKind, MemoryRecord, MemoryStatus  # type: ignore
    ACA_AVAILABLE = True
except Exception:  # noqa: BLE001
    ACA_AVAILABLE = False

    class MemoryKind(str):  # type: ignore[no-redef]
        USER_FACT = "user_fact"; USER_PREFERENCE = "user_preference"; CONVERSATION = "conversation"
        ACTION_RESULT = "action_result"; CORRECTION = "correction"; PLAN = "plan"

        @property
        def value(self) -> str:
            return str(self)

    class MemoryStatus(str):  # type: ignore[no-redef]
        OBSERVED = "observed"; USER_ASSERTED = "user_asserted"; VALIDATED = "validated"
        SUPERSEDED = "superseded"; REJECTED = "rejected"

        @property
        def value(self) -> str:
            return str(self)

    @dataclass(frozen=True)
    class MemoryRecord:  # type: ignore[no-redef]
        memory_id: str
        kind: Any
        content: str
        confidence: float
        status: Any
        provenance: str
        evidence: tuple = ()
        metadata: dict = field(default_factory=dict)
        created_at_utc: str = ""
        supersedes_id: str | None = None

        @classmethod
        def create(cls, *, kind, content, confidence, status, provenance, evidence=(), metadata=None, supersedes_id=None):
            return cls(memory_id=M.new_id("m"), kind=kind, content=" ".join(content.split()), confidence=confidence,
                       status=status, provenance=provenance, evidence=tuple(evidence), metadata=dict(metadata or {}),
                       created_at_utc=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), supersedes_id=supersedes_id)

        def to_dict(self) -> dict:
            return {"memory_id": self.memory_id, "kind": _val(self.kind), "content": self.content,
                    "confidence": self.confidence, "status": _val(self.status), "provenance": self.provenance,
                    "evidence": list(self.evidence), "metadata": self.metadata, "created_at_utc": self.created_at_utc,
                    "supersedes_id": self.supersedes_id}

    @dataclass(frozen=True)
    class MemoryHit:  # type: ignore[no-redef]
        record: Any
        score: float
        matched_terms: tuple = ()

        def to_dict(self) -> dict:
            d = self.record.to_dict()
            d.update({"retrieval_score": self.score, "matched_terms": list(self.matched_terms)})
            return d

    @dataclass(frozen=True)
    class MemoryContext:  # type: ignore[no-redef]
        query: str
        hits: tuple = ()
        scanned_records: int = 0

        @classmethod
        def empty(cls, query: str = ""):
            return cls(query=" ".join(query.split()))

        def to_dict(self) -> dict:
            return {"query": self.query, "hit_count": len(self.hits), "scanned_records": self.scanned_records,
                    "hits": [h.to_dict() for h in self.hits],
                    "epistemic_notice": ("Ces entrees sont des observations contextualisees, pas des verites. "
                                         "La demande humaine courante et les preuves recentes priment.")}


def _val(x: Any) -> str:
    return getattr(x, "value", None) or str(x)


# ----------------------------------------------------------------------------- correspondances
_KIND_TO_MEM = {"user_fact": M.Kind.SEM, "user_preference": M.Kind.PROC, "correction": M.Kind.SEM,
                "conversation": M.Kind.EPI, "action_result": M.Kind.EPI, "plan": M.Kind.EPI}
_MEM_TO_KIND = {"SEM": "user_fact", "PROC": "user_preference", "EPI": "conversation"}
_REMEMBER = re.compile(r"^\s*(?:souviens[- ]toi que|m[ée]morise que|retiens que|rappelle[- ]toi que)\s+(.+)$", re.I)


def _kind_of(v: str):
    """MemoryKind réel (Enum) si ACA est importable, sinon l'équivalent local (str avec .value)."""
    return MemoryKind(v)


def _status_of(v: str):
    return MemoryStatus(v)


# Contenus ACA à la troisième personne (« habite à Lyon ») → sujets typés de memoryaicm.
_SUBJECT_RULES = (
    (re.compile(r"^s['’]appelle\s+", re.I), lambda m, c: ("nom", True)),
    (re.compile(r"^habite\s+(?:à|a|en|au|aux)\s+", re.I), lambda m, c: ("ville", True)),
    (re.compile(r"^travaille\s+", re.I), lambda m, c: ("travail", True)),
    (re.compile(r"^[ée]diteur\s*:", re.I), lambda m, c: ("éditeur", True)),
    (re.compile(r"^utilise\s+([\w\-\+#\.]+)", re.I), lambda m, c: (f"outil:{slug(m.group(1))}", False)),
    (re.compile(r"^pr[ée]f[èe]re\s+", re.I), lambda m, c: ("pref:" + slug(" ".join(c.split()[1:4])), True)),
    (re.compile(r"^d[ée]cision\s*:", re.I), lambda m, c: ("décision:" + slug(" ".join(c.split()[2:5])), False)),
)


def _typed_subject(content: str, default: str) -> tuple[str, bool]:
    for rx, fn in _SUBJECT_RULES:
        m = rx.match(content)
        if m:
            return fn(m, content)
    return default, False


# ----------------------------------------------------------------------------- le port
class MemoryaicmLongTermMemory:
    """Implémente `LongTermMemoryPort` d'ACA au-dessus d'un MemoryAgent memoryaicm."""

    def __init__(self, home: str | Path | Settings | None = None, *, agent: MemoryAgent | None = None,
                 session: str | None = None, backend=None):
        if agent is not None:
            self.agent = agent
        else:
            s = home if isinstance(home, Settings) else Settings(root=Path(home)) if home else Settings()
            self.agent = MemoryAgent(s, backend=backend)
        self.session = session or self.agent.start_session("aca_" + time.strftime("%Y%m%d-%H%M%S"))
        self.scan_limit = 500

    # --- ce qu'ACA lit dans health() : chemin du magasin
    @property
    def path(self) -> Path:
        return self.agent.s.journal_path

    # --- LongTermMemoryPort ---------------------------------------------------------------------
    def remember(self, record) -> Any:
        """Un MemoryRecord ACA → journal (+ fait indexé selon sa nature et son statut)."""
        kind = _val(record.kind)
        status = _val(record.status)
        content = " ".join(str(record.content).split())
        meta = {"aca_memory_id": record.memory_id, "aca_kind": kind, "aca_status": status,
                "provenance": record.provenance, "confidence": float(record.confidence),
                "evidence": list(record.evidence), "metadata": dict(record.metadata or {})}
        if status == "rejected":
            self.agent.journal.append(M.EV_EXTERNAL, M.Actor.SYSTEM.value,
                                      {"session": self.session, "source": f"aca:{record.provenance}", "content": content,
                                       "aca": meta, "rejected": True})
            return record
        if kind == "conversation":
            # « Demande : X | Reponse ACA : Y » → un tour user + un tour assistant dans le journal ;
            # le sommeil en fera un résumé de session ; la politique d'écriture extrait les faits durables.
            m = re.match(r"Demande\s*:\s*(.*?)\s*\|\s*Reponse ACA\s*:\s*(.*)$", content, re.S)
            user_txt, reply = (m.group(1), m.group(2)) if m else (content, "")
            rep = self.agent.remember(user_txt, self.session)
            self.agent.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                      {"session": self.session, "text": reply[:4000], "used": [], "level": "aca",
                                       "aca": meta, "written": [f.id for f in rep.written]})
            self.agent.index.sync()
            return record
        if kind in ("action_result", "plan"):
            fact = M.Fact(id=M.new_id("f"), kind=M.Kind.EPI, txt=content[:1000], subject=f"aca:{kind}:{slug(content, 32)}",
                          src=M.Src.TOOL if kind == "action_result" else M.Src.USER, t0=M.now(), t_upd=M.now(),
                          imp=min(1.0, max(0.1, float(record.confidence))), ttl=self.agent.s.ttl_by_kind.get("EPI", 0),
                          exclusive=False, origin="aca", session=self.session)
            self.agent.journal.append(M.EV_FACT_WRITE, M.Actor.SYSTEM.value, {"fact": fact.to_dict(), "aca": meta})
            self.agent.index.sync()
            return record
        # user_fact / user_preference / correction : passe par la politique d'écriture (dit, durable, conflit, sensible)
        explicit = status in ("user_asserted", "validated")
        cands = self.agent.backend.extract(content) or []
        if not cands:
            from ..llm.base import Candidate
            default = ("pref:" if kind == "user_preference" else "aca:") + slug(content, 40)
            subject, exclusive = _typed_subject(content, default)
            cands = [Candidate(txt=content[:500], subject=subject,
                               kind="PROC" if (kind == "user_preference" or subject.startswith("pref:")) else "SEM",
                               exclusive=exclusive or kind == "correction", durable=True, sens=False, explicit=explicit)]
        for c in cands:
            c.explicit = c.explicit or explicit
        ev = self.agent.journal.append(M.EV_TURN_USER, M.Actor.USER.value,
                                       {"session": self.session, "text": content, "aca": meta})
        self.agent.policy.from_candidates(cands, session=self.session, origin=ev.id, ts=ev.ts)
        self.agent.maybe_sleep()
        return record

    def query(self, text: str, *, limit: int = 6):
        clean = " ".join(str(text).split())
        if not clean or not tokens(clean):
            return MemoryContext.empty(clean)
        self.agent.index.sync()
        scored = self.agent.index.retrieve(clean, k=max(1, min(int(limit), 20)))
        q = tokens(clean)
        hits = []
        for f, score, rel in scored:
            hits.append(MemoryHit(self._to_record(f), round(float(score), 6),
                                  tuple(sorted(q & tokens(f.txt + " " + f.subject.replace(":", " "))))))
        if scored:
            self.agent.journal.append(M.EV_FACT_USE, M.Actor.SYSTEM.value,
                                      {"ids": [f.id for f, _, _ in scored], "useful": [], "reason": "aca-query"})
            self.agent.index.sync()
        # les préférences sont toujours pertinentes pour ACA (comment répondre)
        seen = {h.record.metadata.get("fact_id") for h in hits}
        for p in self.agent.index.prefs():
            if p.id not in seen:
                hits.append(MemoryHit(self._to_record(p), 0.0, ()))
        return MemoryContext(clean, tuple(hits), len(self.agent.index.all_facts(on_only=True)))

    def recent(self, *, limit: int = 10) -> tuple:
        n = max(0, min(int(limit), 100))
        return tuple(self._to_record(f) for f in self.agent.index.all_facts(on_only=True)[:n])

    def count(self) -> int:
        self.agent.index.sync()
        return len(self.agent.index.all_facts(on_only=True))

    # --- au-delà du port : ce qu'ACA n'avait pas -----------------------------------------------
    def forget(self, query: str):
        return self.agent.forget(query, session=self.session)

    def sleep(self, reason: str = "aca"):
        return self.agent.sleep(reason=reason)

    def history(self, subject: str) -> list[dict]:
        return self.agent.index.history(subject)

    def status(self) -> dict:
        return self.agent.status()

    # --- conversion Fact → MemoryRecord -----------------------------------------------------------
    def _to_record(self, f: M.Fact):
        kind = _MEM_TO_KIND.get(f.kind.value, "user_fact")
        if f.subject.startswith("aca:action_result"):
            kind = "action_result"
        elif f.subject.startswith("aca:plan"):
            kind = "plan"
        status = "user_asserted" if f.src == M.Src.USER else "observed"
        if f.sens:
            status = "validated"  # un fait sensible n'est actif qu'après validation humaine
        return MemoryRecord.create(
            kind=_kind_of(kind), content=f.txt, confidence=round(min(1.0, max(0.0, f.imp)), 3),
            status=_status_of(status), provenance=f"memoryaicm {f.label()}",
            evidence=(f"journal:{f.origin}",) if f.origin else (),
            metadata={"fact_id": f.id, "subject": f.subject, "kind": f.kind.value, "ver": f.ver,
                      "deps": list(f.deps), "prev": list(f.prev)},
        )


# ----------------------------------------------------------------------------- codex ACA + memoryaicm en un seul objet
def codex_memory(aca_path: str | Path, memoryaicm_home: str | Path | Settings | None = None, *, backend=None, **kwargs):
    """La mémoire ACA existante (SQLiteLongTermMemory — codex évolutif, prédictions, consolidation) reste
    intacte et garde ses commandes (/codex, /consolider, /predire, /observer : les `isinstance` passent,
    c'est une sous-classe) ; chaque souvenir est en plus journalisé et indexé par memoryaicm, et la
    récupération fusionne les deux (memoryaicm d'abord, doublons écartés).

        memory_store = codex_memory(PROJECT_ROOT / "memory" / "aca_memory.sqlite3", "/chemin/vers/memoryaicm/data")
    """
    from aca.memory import SQLiteLongTermMemory  # type: ignore

    class MemoryaicmCodexMemory(SQLiteLongTermMemory):
        def __init__(self, path, *, memoryaicm_home=None, backend=None, **kw):
            super().__init__(Path(path), **kw)
            self.mem = MemoryaicmLongTermMemory(memoryaicm_home, backend=backend)

        def remember(self, record):
            rec = super().remember(record)
            self.mem.remember(record)
            return rec

        def query(self, text: str, *, limit: int = 6):
            ctx_a = super().query(text, limit=limit)
            ctx_m = self.mem.query(text, limit=limit)
            seen, merged = set(), []
            for h in list(ctx_m.hits) + list(ctx_a.hits):
                key = " ".join(str(h.record.content).lower().split())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(h)
            return MemoryContext(ctx_m.query or ctx_a.query, tuple(merged[: max(1, min(int(limit), 20)) + 4]),
                                 ctx_a.scanned_records + ctx_m.scanned_records)

        # au-delà d'ACA : oubli, sommeil, historique, état — sur la partie memoryaicm
        def forget(self, query: str):
            return self.mem.forget(query)

        def sleep(self, reason: str = "aca"):
            return self.mem.sleep(reason)

        def history(self, subject: str):
            return self.mem.history(subject)

        def memoryaicm_status(self) -> dict:
            return self.mem.status()

    return MemoryaicmCodexMemory(aca_path, memoryaicm_home=memoryaicm_home, backend=backend, **kwargs)


# ----------------------------------------------------------------------------- le coordinateur
class MemoryaicmCoordinator:
    """Même surface que `aca.memory.MemoryCoordinator` ; la politique d'écriture remplace les règles ad hoc."""

    def retrieve(self, store, message: str, *, limit: int = 6):
        return store.query(message, limit=limit) if store is not None else MemoryContext.empty(message)

    @classmethod
    def explicit_payload(cls, message: str) -> str | None:
        m = _REMEMBER.match(" ".join(message.split()))
        return m.group(1).strip(" .") if m else None

    def remember_explicit(self, store, content: str, *, provenance: str = "explicit_user_command"):
        rec = MemoryRecord.create(kind=_kind_of("user_preference" if re.search(r"pr[ée]f[èe]re|j'aime|souhaite", content, re.I) else "user_fact"),
                                  content=content, confidence=0.8, status=_status_of("user_asserted"),
                                  provenance=provenance, evidence=("saisie humaine explicite",))
        return store.remember(rec)

    def commit(self, store, *, message: str, response: str, route: str, selected_models: tuple = (),
               action_execution: Any | None = None) -> tuple:
        if store is None:
            return ()
        records = []
        bounded = " ".join(str(response).split())[:1500]
        conv = MemoryRecord.create(kind=_kind_of("conversation"),
                                   content=f"Demande : {message} | Reponse ACA : {bounded}", confidence=0.5,
                                   status=_status_of("observed"), provenance="aca_pipeline_turn",
                                   evidence=("interaction locale observee",),
                                   metadata={"route": route, "selected_models": list(selected_models)})
        records.append(store.remember(conv))  # la politique d'écriture extrait ce qui est durable
        if action_execution is not None:
            data = action_execution.to_dict() if hasattr(action_execution, "to_dict") else dict(action_execution)
            content = (f"Action {data.get('kind', 'locale')} sur {data.get('target', '?')} : "
                       f"statut {data.get('status', 'inconnu')}; raison {data.get('reason', '')}.")
            act = MemoryRecord.create(kind=_kind_of("action_result"), content=content,
                                      confidence=1.0 if bool(data.get("executed")) else 0.95,
                                      status=_status_of("observed"), provenance="aca_action_kernel",
                                      evidence=(json.dumps(data, ensure_ascii=False),),
                                      metadata={"route": route, "selected_models": list(selected_models)})
            records.append(store.remember(act))
        return tuple(records)
