"""JOURNAL · cold · source de vérité.

append-only · immuable · horodaté · attribué · chaîné (SHA-256) · full-text · ⊇ tout · jamais détruit.

Les triggers SQLite refusent UPDATE et DELETE : même le code de ce module ne peut pas
modifier ou supprimer une ligne. « Oublier » est un événement qu'on ajoute, pas une
ligne qu'on retire.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from .model import Event, new_id

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    id        TEXT NOT NULL UNIQUE,
    ts        REAL NOT NULL,
    actor     TEXT NOT NULL,
    type      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type);
CREATE INDEX IF NOT EXISTS ix_events_ts   ON events(ts);

-- Immuabilité : toute tentative de modification ou de suppression est refusée.
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'journal append-only : UPDATE interdit'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'journal append-only : DELETE interdit'); END;

-- Recherche plein texte sur le journal (⊇ tout, toujours consultable).
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(id UNINDEXED, text);
"""


class Journal:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False : le mode serveur sérialise les accès avec un verrou
        self.db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ écriture
    def append(self, type: str, actor: str, payload: dict, ts: float | None = None) -> Event:
        # float() obligatoire : la colonne ts est REAL. Un appelant qui passe un entier
        # (int(time.time()), une date analysée, une archive importée) verrait son événement
        # haché sur « 1683553000 » puis relu en « 1683553000.0 » — chaîne invérifiable À VIE,
        # puisque le journal est append-only et qu'aucune ligne ne peut être corrigée.
        ts = time.time() if ts is None else float(ts)
        eid = new_id("ev")
        with self.db:  # transaction : la chaîne ne peut pas se fourcher
            self.db.execute("BEGIN IMMEDIATE")
            prev = self.last_hash()
            h = Event.compute_hash(prev, eid, ts, actor, type, payload)
            cur = self.db.execute(
                "INSERT INTO events(id, ts, actor, type, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (eid, ts, actor, type, json.dumps(payload, ensure_ascii=False, sort_keys=True), prev, h),
            )
            text = _searchable(type, payload)
            if text:
                self.db.execute("INSERT INTO events_fts(id, text) VALUES (?,?)", (eid, text))
            self.db.execute("COMMIT")
        return Event(cur.lastrowid, eid, ts, actor, type, payload, prev, h)

    # ------------------------------------------------------------------ lecture
    def last_hash(self) -> str:
        row = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS

    def __len__(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def get(self, event_id: str) -> Event | None:
        row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def replay(self, since_seq: int = 0, types: tuple[str, ...] | None = None) -> Iterator[Event]:
        """Rejoue le journal dans l'ordre. C'est la seule source de l'Index."""
        if types:
            q = f"SELECT * FROM events WHERE seq>? AND type IN ({','.join('?' * len(types))}) ORDER BY seq"
            rows = self.db.execute(q, (since_seq, *types))
        else:
            rows = self.db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (since_seq,))
        for row in rows:
            yield _row_to_event(row)

    def last_seq(self) -> int:
        row = self.db.execute("SELECT MAX(seq) FROM events").fetchone()
        return int(row[0] or 0)

    def last_of(self, type: str) -> Event | None:
        row = self.db.execute("SELECT * FROM events WHERE type=? ORDER BY seq DESC LIMIT 1", (type,)).fetchone()
        return _row_to_event(row) if row else None

    def count_since(self, type: str, seq: int) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM events WHERE type=? AND seq>?", (type, seq)).fetchone()[0])

    def count_types_since(self, types: tuple[str, ...], seq: int) -> int:
        q = f"SELECT COUNT(*) FROM events WHERE seq>? AND type IN ({','.join('?' * len(types))})"
        return int(self.db.execute(q, (seq, *types)).fetchone()[0])

    def search(self, query: str, limit: int = 20) -> list[Event]:
        """Plein texte sur tout le journal, y compris ce qui est désindexé."""
        q = " ".join(f'"{t}"' for t in query.replace('"', " ").split() if t)
        if not q:
            return []
        rows = self.db.execute(
            "SELECT e.* FROM events_fts f JOIN events e ON e.id=f.id WHERE events_fts MATCH ? ORDER BY e.seq DESC LIMIT ?",
            (q, limit),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def verify(self) -> tuple[bool, str]:
        """Vérifie la chaîne de hachage de bout en bout (auditable)."""
        prev = GENESIS
        for ev in self.replay():
            if ev.prev_hash != prev:
                return False, f"rupture de chaîne avant {ev.id} (seq {ev.seq})"
            if Event.compute_hash(prev, ev.id, ev.ts, ev.actor, ev.type, ev.payload) != ev.hash:
                return False, f"hachage invalide pour {ev.id} (seq {ev.seq})"
            prev = ev.hash
        return True, f"{len(self)} événements, chaîne intacte"

    def close(self) -> None:
        self.db.close()


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        seq=row["seq"], id=row["id"], ts=row["ts"], actor=row["actor"], type=row["type"],
        payload=json.loads(row["payload"]), prev_hash=row["prev_hash"], hash=row["hash"],
    )


def _searchable(type: str, payload: dict) -> str:
    parts = []
    for key in ("text", "txt", "reason", "query", "content"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    fact = payload.get("fact")
    if isinstance(fact, dict) and fact.get("txt"):
        parts.append(str(fact["txt"]))
    return " ".join(parts)
