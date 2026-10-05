"""INDEX · actif · projection.

Index = { f ∈ Log : f.on ∧ act(f) > θ }

Rien n'est écrit ici directement : l'Index applique des événements du journal.
On peut supprimer index.sqlite et le reconstruire à l'identique (`rebuild`).
"""

from __future__ import annotations

import json
import re
import math
import sqlite3
import time
from pathlib import Path

from . import model as M
from .config import Settings
from .log import Journal
from .textutil import tokens, overlap, jaccard

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    txt       TEXT NOT NULL,
    subject   TEXT NOT NULL,
    src       TEXT NOT NULL,
    t0        REAL NOT NULL,
    t_upd     REAL NOT NULL,
    ver       INTEGER NOT NULL DEFAULT 1,
    imp       REAL NOT NULL DEFAULT 0.5,
    ttl       REAL NOT NULL DEFAULT 0,
    deps      TEXT NOT NULL DEFAULT '[]',
    sens      INTEGER NOT NULL DEFAULT 0,
    "on"      INTEGER NOT NULL DEFAULT 1,
    exclusive INTEGER NOT NULL DEFAULT 1,
    origin    TEXT NOT NULL DEFAULT '',
    session   TEXT NOT NULL DEFAULT '',
    prev      TEXT NOT NULL DEFAULT '[]',
    pending   INTEGER NOT NULL DEFAULT 0,
    sessions  TEXT NOT NULL DEFAULT '[]',
    off_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS ix_facts_on ON facts("on");
CREATE TABLE IF NOT EXISTS uses (
    fact_id TEXT NOT NULL,
    ts      REAL NOT NULL,
    useful  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_uses_fact ON uses(fact_id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS redactions (session TEXT NOT NULL, turn_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (fact_id TEXT PRIMARY KEY, queued_ts REAL NOT NULL, decided INTEGER NOT NULL DEFAULT 0, approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vectors (fact_id TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (fact_id, model));
"""


class Index:
    def __init__(self, path: Path | str, journal: Journal, settings: Settings | None = None, embedder=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal = journal
        self.s = settings or Settings()
        self.db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        from .embed import HashingEmbedder
        self.embedder = embedder or HashingEmbedder(dim=self.s.embed_dim)
        self._idf: dict[str, float] | None = None      # invalidé à chaque projection
        self._idf_defaut = 0.0

    # ------------------------------------------------------------- pertinence lexicale (idf)
    def _idf_table(self) -> dict[str, float] | None:
        """idf des mots de l'index actif. None tant qu'il y a trop peu de faits pour que l'idf ait un sens."""
        if self._idf is not None:
            return self._idf or None
        docs = [tokens(self._embed_text(f)) for f in self.all_facts(on_only=True)]
        if len(docs) < self.s.idf_min_facts:
            self._idf = {}
            return None
        n = len(docs)
        df: dict[str, int] = {}
        for d in docs:
            for t in d:
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self._idf_defaut = math.log(1.0 + (n + 0.5) / 0.5)
        return self._idf

    def lexical(self, q: set[str], doc: set[str]) -> float:
        """Part de la question couverte par le fait — chaque mot pesant l'information qu'il porte.

        Avec des idf tous égaux, c'est exactement `overlap` : le mot rare (« Kolkata ») ne pèse
        plus autant qu'un mot banal (« week »). Sous `idf_min_facts` faits, l'idf n'a pas de sens
        statistique et on garde le comportement d'origine.
        """
        if not q or not doc:
            return 0.0
        idf = self._idf_table()
        if idf is None:
            return max(overlap(q, doc), jaccard(q, doc))
        den = sum(idf.get(t, self._idf_defaut) for t in q)
        if den <= 0:
            return max(overlap(q, doc), jaccard(q, doc))
        return sum(idf.get(t, self._idf_defaut) for t in q if t in doc) / den

    # ------------------------------------------------------------- vecteurs
    # 05/10 : un SUJET-CLÉ machine (composite « a | b | c », abstraction « abs: », clé de conflit, épisode,
    # session de conversation, fait de contrôle d'éval) n'est PAS un vrai sujet : le verser dans le doc lexical
    # faisait collisionner 71 faits mal classés avec les questions canoniques (« comment je m'appelle ? » battu
    # par des faits bug-bounty qui avaient hérité du jeton « nom » dans leur clé composite). On ne verse le sujet
    # que s'il est propre ; le txt du fait reste entier. Un vrai sujet simple (« nom », « ville ») est inchangé.
    _SUJET_CLE = re.compile(r" \| |^abs:|^clé de conflit|^epi:|^conversation:|^contrôle ", re.I)

    def _embed_text(self, f: M.Fact) -> str:
        subject = "" if self._SUJET_CLE.search(f.subject or "") else f.subject.replace(":", " ").replace(".", " ")
        return f.txt + " " + subject

    def vector(self, f: M.Fact) -> list[float]:
        """Vecteur du fait pour l'embedder courant ; calculé et stocké à la demande (rebuild les régénère)."""
        from .embed import pack, unpack
        row = self.db.execute("SELECT vec FROM vectors WHERE fact_id=? AND model=?", (f.id, self.embedder.name)).fetchone()
        if row:
            return unpack(row["vec"])
        vec = self.embedder.embed(self._embed_text(f))
        self.db.execute("INSERT OR REPLACE INTO vectors(fact_id, model, vec) VALUES (?,?,?)", (f.id, self.embedder.name, pack(vec)))
        return vec

    def semantic(self, query_vec: list[float], f: M.Fact) -> float:
        """Cosinus ramené sur [0,1] entre sem_floor et sem_ceiling (0 = sans rapport, 1 = même chose)."""
        from .embed import cosine
        c = cosine(query_vec, self.vector(f))
        lo, hi = self.s.sem_floor, self.s.sem_ceiling
        return max(0.0, min(1.0, (c - lo) / (hi - lo))) if hi > lo else 0.0

    # ------------------------------------------------------------- projection
    def applied_seq(self) -> int:
        row = self.db.execute("SELECT v FROM meta WHERE k='applied_seq'").fetchone()
        return int(row["v"]) if row else 0

    def sync(self) -> int:
        """Applique les événements du journal non encore projetés. Retourne le nombre appliqué."""
        n = 0
        for ev in self.journal.replay(since_seq=self.applied_seq()):
            self.apply(ev)
            n += 1
        return n

    def rebuild(self) -> int:
        """Supprime la projection et la reconstruit depuis le journal : Log ⊇ Index."""
        with self.db:
            for t in ("facts", "uses", "meta", "redactions", "reviews", "vectors"):
                self.db.execute(f"DELETE FROM {t}")
        return self.sync()

    def apply(self, ev: M.Event) -> None:
        """Projette un événement, atomiquement (tout ou rien, comme le journal)."""
        self._idf = None          # l'index actif change : la table idf est refaite au prochain rappel
        self.db.execute("BEGIN")
        try:
            self._apply(ev)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _apply(self, ev: M.Event) -> None:
        p = ev.payload
        t = ev.type
        if t == M.EV_FACT_WRITE:
            self._upsert(M.Fact.from_dict(p["fact"]), sessions=[p["fact"].get("session", "")])
            self._use(p["fact"]["id"], ev.ts, useful=False)
        elif t == M.EV_FACT_MERGE:
            new = M.Fact.from_dict(p["fact"])
            old = self.get(p["supersedes"])
            seen = json.loads(old["sessions"]) if old else []
            if new.session and new.session not in seen:
                seen.append(new.session)
            self._set_on(p["supersedes"], False, "superseded")
            self._upsert(new, sessions=seen)
            self._use(new.id, ev.ts, useful=False)
        elif t == M.EV_FACT_FORGET:
            for fid in p.get("ids", []) + p.get("cascade", []):
                self._set_on(fid, False, "forget")
        elif t == M.EV_FACT_OFF:
            for fid in p.get("ids", []):
                self._set_on(fid, False, p.get("reason", "off"))
        elif t == M.EV_FACT_ON:
            for fid in p.get("ids", []):
                self._set_on(fid, True, "")
        elif t == M.EV_FACT_USE:
            useful = set(p.get("useful", []))
            for fid in p.get("ids", []):
                self._use(fid, ev.ts, useful=fid in useful)
                if fid in useful:
                    self.db.execute(
                        "UPDATE facts SET imp=MIN(1.0, imp+?) WHERE id=?", (self.s.utility_bonus, fid)
                    )
                sess = p.get("session")
                if sess:  # redit dans une autre session ⇒ compte pour la répétition
                    row = self.get(fid)
                    if row:
                        seen = json.loads(row["sessions"])
                        if sess not in seen:
                            seen.append(sess)
                            self.db.execute("UPDATE facts SET sessions=? WHERE id=?", (json.dumps(seen), fid))
        elif t == M.EV_REVIEW_QUEUE:
            self.db.execute(
                "INSERT OR REPLACE INTO reviews(fact_id, queued_ts, decided, approved) VALUES (?,?,0,0)",
                (p["fact_id"], ev.ts),
            )
            self.db.execute("UPDATE facts SET pending=1, \"on\"=0 WHERE id=?", (p["fact_id"],))
        elif t == M.EV_REVIEW_DECIDE:
            ok = 1 if p.get("approve") else 0
            self.db.execute(
                "UPDATE reviews SET decided=1, approved=? WHERE fact_id=?", (ok, p["fact_id"])
            )
            self.db.execute(
                "UPDATE facts SET pending=0, \"on\"=?, off_reason=? WHERE id=?",
                (ok, "" if ok else "review-rejected", p["fact_id"]),
            )
        elif t == M.EV_TURN_REDACT:
            for tid in p.get("turn_ids", []):
                self.db.execute("INSERT INTO redactions(session, turn_id) VALUES (?,?)", (p.get("session", ""), tid))
        # les autres types (tours, sommeil, adaptateur, garde) n'ont pas de projection
        self.db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('applied_seq', ?)", (str(ev.seq),))

    def _upsert(self, f: M.Fact, sessions: list[str]) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO facts(id, kind, txt, subject, src, t0, t_upd, ver, imp, ttl, deps, sens, "on",
               exclusive, origin, session, prev, pending, sessions, off_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f.id, f.kind.value, f.txt, f.subject, f.src.value, f.t0, f.t_upd, f.ver, f.imp, f.ttl,
                json.dumps(f.deps), int(f.sens), int(f.on), int(f.exclusive), f.origin, f.session,
                json.dumps(f.prev, ensure_ascii=False), 0, json.dumps([s for s in sessions if s]), "",
            ),
        )
        self.db.execute("DELETE FROM vectors WHERE fact_id=?", (f.id,))
        self.vector(f)  # vecteur calculé à l'écriture, régénéré au rebuild

    def _set_on(self, fid: str, on: bool, reason: str) -> None:
        self.db.execute("UPDATE facts SET \"on\"=?, off_reason=? WHERE id=?", (int(on), reason if not on else "", fid))

    def _use(self, fid: str, ts: float, useful: bool) -> None:
        self.db.execute("INSERT INTO uses(fact_id, ts, useful) VALUES (?,?,?)", (fid, ts, int(useful)))

    # ------------------------------------------------------------- lecture
    def get(self, fid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()

    def fact(self, fid: str) -> M.Fact | None:
        row = self.get(fid)
        return _row_to_fact(row) if row else None

    def all_facts(self, on_only: bool = True, kinds: tuple[str, ...] | None = None) -> list[M.Fact]:
        q = "SELECT * FROM facts"
        conds, args = [], []
        if on_only:
            conds.append('"on"=1')
        if kinds:
            conds.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY t_upd DESC"
        return [_row_to_fact(r) for r in self.db.execute(q, args)]

    def by_subject(self, subject: str, on_only: bool = True) -> list[M.Fact]:
        q = "SELECT * FROM facts WHERE subject=?" + (' AND "on"=1' if on_only else "")
        return [_row_to_fact(r) for r in self.db.execute(q, (subject,))]

    def sessions_of(self, fid: str) -> list[str]:
        row = self.get(fid)
        return json.loads(row["sessions"]) if row else []

    def dependents(self, fid: str) -> list[str]:
        """Cascade de lignage : tous les faits qui dérivent (transitivement) de fid."""
        out, frontier = [], [fid]
        while frontier:
            cur = frontier.pop()
            for r in self.db.execute("SELECT id, deps FROM facts"):
                if cur in json.loads(r["deps"]) and r["id"] not in out and r["id"] != fid:
                    out.append(r["id"])
                    frontier.append(r["id"])
        return out

    def redacted_turns(self, session: str) -> set[str]:
        return {r["turn_id"] for r in self.db.execute("SELECT turn_id FROM redactions WHERE session=?", (session,))}

    def pending_reviews(self) -> list[M.Fact]:
        rows = self.db.execute(
            "SELECT f.* FROM facts f JOIN reviews r ON r.fact_id=f.id WHERE r.decided=0"
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    # ------------------------------------------------------------- ACT-R
    def activation(self, fid: str, now: float | None = None) -> float:
        """act(f) = ln Σᵢ tᵢ^(−d)  — usage récent et fréquent ⇒ activation haute."""
        now = time.time() if now is None else now
        rows = self.db.execute("SELECT ts FROM uses WHERE fact_id=?", (fid,)).fetchall()
        if not rows:
            return -math.inf
        total = 0.0
        for r in rows:
            dt = max(self.s.min_interval_s, now - r["ts"])
            total += dt ** (-self.s.decay_d)
        return math.log(total) if total > 0 else -math.inf

    def effective(self, f: M.Fact, now: float | None = None) -> float:
        """Activation corrigée par l'importance : sert à l'élagage (act_eff < θ ⇒ on=0)."""
        return self.activation(f.id, now) + (f.imp - 0.5) * 2.0

    def act_norm(self, fid: str, now: float | None = None) -> float:
        a = self.activation(fid, now)
        if a == -math.inf:
            return 0.0
        return max(0.0, min(1.0, (a - self.s.theta_off) / (0.0 - self.s.theta_off)))

    # ------------------------------------------------------------- READ
    def retrieve(self, query: str, k: int | None = None, now: float | None = None,
                 kinds: tuple[str, ...] = ("SEM", "EPI")) -> list[tuple[M.Fact, float, float]]:
        """score = w_r·rel + w_a·act + w_i·imp ; top-k ; jamais de dump (rel ≥ min_rel)."""
        k = self.s.top_k if k is None else k
        q = tokens(query)
        qv = self.embedder.embed(query) if query.strip() else []
        scored = []
        for f in self.all_facts(on_only=True, kinds=kinds):
            doc = tokens(self._embed_text(f))
            lex = self.lexical(q, doc)
            sem = self.semantic(qv, f) if qv else 0.0
            # le lexical est précis : quand il trouve, il décide. Le sémantique est un RECOURS
            # (reformulation, faute de frappe), pas un concurrent : en `max`, il double les faits
            # vaguement proches par-dessus le fait exact (mesuré sur LoCoMo : −5 points de hit@10).
            rel = lex if lex > 0 else sem
            if rel < self.s.min_rel:
                continue
            score = self.s.w_rel * rel + self.s.w_act * self.act_norm(f.id, now) + self.s.w_imp * f.imp
            scored.append((f, score, rel))
        scored.sort(key=lambda x: (-x[1], x[0].t_upd))
        return scored[:k]

    # ------------------------------------------------------------- temporel
    def history(self, subject: str) -> list[dict]:
        """Toutes les valeurs qu'un sujet a prises, datées, depuis le journal (y compris désindexées)."""
        out = []
        for ev in self.journal.replay(types=(M.EV_FACT_WRITE, M.EV_FACT_MERGE)):
            fact = ev.payload.get("fact") or {}
            if fact.get("subject") != subject:
                continue
            row = self.get(fact["id"])
            out.append({
                "ts": ev.ts, "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ev.ts)), "id": fact["id"],
                "txt": fact.get("txt", ""), "ver": fact.get("ver", 1), "event": ev.type,
                "on": bool(row["on"]) if row else False, "off_reason": row["off_reason"] if row else "",
            })
        return out

    def timeline(self, days: float = 7.0, now: float | None = None) -> list[M.Fact]:
        """Faits épisodiques (résumés de session, événements) des N derniers jours, du plus récent au plus ancien."""
        now = time.time() if now is None else now
        since = now - days * 86400
        return [f for f in self.all_facts(on_only=True, kinds=("EPI",)) if f.t_upd >= since]

    def prefs(self) -> list[M.Fact]:
        """PROC : réinjectées en fin de contexte, toujours (elles sont peu nombreuses)."""
        return self.all_facts(on_only=True, kinds=("PROC",))

    def most_similar(self, txt: str, kinds: tuple[str, ...] | None = None) -> tuple[M.Fact | None, float]:
        best, best_s = None, 0.0
        t = tokens(txt)
        for f in self.all_facts(on_only=True, kinds=kinds):
            s = jaccard(t, tokens(f.txt))
            if s > best_s:
                best, best_s = f, s
        return best, best_s

    def state_hash(self) -> str:
        """Empreinte de l'état actif : sert au manifeste de l'adaptateur."""
        import hashlib
        h = hashlib.sha256()
        for f in sorted(self.all_facts(on_only=True), key=lambda x: x.id):
            h.update(f"{f.id}|{f.ver}|{f.txt}|{f.kind.value}\n".encode("utf-8"))
        return h.hexdigest()[:16]

    def close(self) -> None:
        self.db.close()


def _row_to_fact(r: sqlite3.Row) -> M.Fact:
    return M.Fact(
        id=r["id"], kind=M.Kind(r["kind"]), txt=r["txt"], subject=r["subject"], src=M.Src(r["src"]),
        t0=r["t0"], t_upd=r["t_upd"], ver=r["ver"], imp=r["imp"], ttl=r["ttl"], deps=json.loads(r["deps"]),
        sens=bool(r["sens"]), on=bool(r["on"]), exclusive=bool(r["exclusive"]), origin=r["origin"],
        session=r["session"], prev=json.loads(r["prev"]),
    )
