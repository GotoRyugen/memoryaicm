"""Memoire longue ACA locale, typee, bornee et auditable.

La memoire conserve des observations et leur provenance. Elle ne transforme
jamais un souvenir en verite : chaque entree garde un statut et un niveau de
confiance, puis le pipeline decide si elle est pertinente pour la demande
courante.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


class MemoryKind(str, Enum):
    USER_FACT = "user_fact"
    USER_PREFERENCE = "user_preference"
    CONVERSATION = "conversation"
    ACTION_RESULT = "action_result"
    CORRECTION = "correction"
    PLAN = "plan"


class MemoryStatus(str, Enum):
    OBSERVED = "observed"
    USER_ASSERTED = "user_asserted"
    VALIDATED = "validated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    content: str
    confidence: float
    status: MemoryStatus
    provenance: str
    evidence: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=_utc_now)
    supersedes_id: str | None = None

    def __post_init__(self) -> None:
        clean = " ".join(self.content.split())
        if not clean:
            raise ValueError("Une memoire ACA ne peut pas etre vide.")
        if len(clean) > 8192:
            raise ValueError("Une memoire ACA est limitee a 8192 caracteres.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("La confiance memoire doit etre comprise entre 0 et 1.")
        if not self.provenance.strip():
            raise ValueError("La provenance memoire est obligatoire.")
        object.__setattr__(self, "content", clean)

    @classmethod
    def create(
        cls,
        *,
        kind: MemoryKind,
        content: str,
        confidence: float,
        status: MemoryStatus,
        provenance: str,
        evidence: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
        supersedes_id: str | None = None,
    ) -> "MemoryRecord":
        return cls(
            memory_id=str(uuid4()),
            kind=kind,
            content=content,
            confidence=confidence,
            status=status,
            provenance=provenance,
            evidence=evidence,
            metadata=dict(metadata or {}),
            supersedes_id=supersedes_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "content": self.content,
            "confidence": self.confidence,
            "status": self.status.value,
            "provenance": self.provenance,
            "evidence": list(self.evidence),
            "metadata": self.metadata,
            "created_at_utc": self.created_at_utc,
            "supersedes_id": self.supersedes_id,
        }


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        result = self.record.to_dict()
        result.update({"retrieval_score": self.score, "matched_terms": list(self.matched_terms)})
        return result


@dataclass(frozen=True)
class MemoryContext:
    query: str
    hits: tuple[MemoryHit, ...] = ()
    scanned_records: int = 0

    @classmethod
    def empty(cls, query: str = "") -> "MemoryContext":
        return cls(query=" ".join(query.split()))

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "hit_count": len(self.hits),
            "scanned_records": self.scanned_records,
            "hits": [hit.to_dict() for hit in self.hits],
            "epistemic_notice": (
                "Ces entrees sont des observations contextualisees, pas des verites. "
                "La demande humaine courante et les preuves recentes priment."
            ),
        }


class LongTermMemoryPort(Protocol):
    def remember(self, record: MemoryRecord) -> MemoryRecord:
        ...

    def query(self, text: str, *, limit: int = 6) -> MemoryContext:
        ...

    def recent(self, *, limit: int = 10) -> tuple[MemoryRecord, ...]:
        ...

    def count(self) -> int:
        ...


_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_STOPWORDS = {
    "avec", "cela", "cette", "dans", "des", "elle", "est", "les", "leur", "mais",
    "mes", "mon", "nous", "pour", "que", "quel", "quelle", "qui", "son", "sur",
    "tes", "ton", "une", "vous", "comment", "faire", "fait", "plus", "peux", "peut",
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _terms(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(_fold(text)) if token not in _STOPWORDS}


class SQLiteLongTermMemory:
    """Stockage append-only SQLite avec recherche lexicale locale bornee."""

    def __init__(self, path: Path, *, scan_limit: int = 500) -> None:
        if scan_limit < 10 or scan_limit > 5000:
            raise ValueError("scan_limit doit etre compris entre 10 et 5000.")
        self.path = Path(path).resolve()
        self.scan_limit = scan_limit
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    status TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    supersedes_id TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_records(created_at_utc DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_kind_status ON memory_records(kind, status)"
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            kind=MemoryKind(str(row["kind"])),
            content=str(row["content"]),
            confidence=float(row["confidence"]),
            status=MemoryStatus(str(row["status"])),
            provenance=str(row["provenance"]),
            evidence=tuple(json.loads(str(row["evidence_json"]))),
            metadata=dict(json.loads(str(row["metadata_json"]))),
            created_at_utc=str(row["created_at_utc"]),
            supersedes_id=str(row["supersedes_id"]) if row["supersedes_id"] else None,
        )

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_records (
                    memory_id, kind, content, normalized_content, confidence, status,
                    provenance, evidence_json, metadata_json, created_at_utc, supersedes_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id,
                    record.kind.value,
                    record.content,
                    _fold(record.content),
                    record.confidence,
                    record.status.value,
                    record.provenance,
                    json.dumps(record.evidence, ensure_ascii=False),
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at_utc,
                    record.supersedes_id,
                ),
            )
        return record

    def query(self, text: str, *, limit: int = 6) -> MemoryContext:
        clean_query = " ".join(text.split())
        safe_limit = max(1, min(int(limit), 20))
        query_terms = _terms(clean_query)
        if not query_terms:
            return MemoryContext.empty(clean_query)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE status NOT IN (?, ?)
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                (MemoryStatus.REJECTED.value, MemoryStatus.SUPERSEDED.value, self.scan_limit),
            ).fetchall()
        scored: list[MemoryHit] = []
        for index, row in enumerate(rows):
            record = self._from_row(row)
            record_terms = _terms(record.content)
            matched = tuple(sorted(query_terms & record_terms))
            if not matched:
                continue
            overlap = len(matched) / max(1, len(query_terms))
            specificity = len(matched) / max(1, len(record_terms))
            recency = 1.0 / (1.0 + index / 25.0)
            score = 0.62 * overlap + 0.18 * specificity + 0.15 * record.confidence + 0.05 * recency
            scored.append(MemoryHit(record, round(score, 6), matched))
        scored.sort(key=lambda hit: (hit.score, hit.record.created_at_utc), reverse=True)
        return MemoryContext(clean_query, tuple(scored[:safe_limit]), len(rows))

    def recent(self, *, limit: int = 10) -> tuple[MemoryRecord, ...]:
        safe_limit = max(0, min(int(limit), 100))
        if safe_limit == 0:
            return ()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_records ORDER BY created_at_utc DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0])


class MemoryCoordinator:
    """Recupere avant raisonnement et consigne apres decision."""

    _REMEMBER_PREFIXES = (
        "souviens-toi que ", "souviens toi que ", "memorise que ", "mémorise que ",
        "retiens que ", "rappelle-toi que ", "rappelle toi que ",
    )

    def retrieve(
        self,
        store: LongTermMemoryPort | None,
        message: str,
        *,
        limit: int = 6,
    ) -> MemoryContext:
        return store.query(message, limit=limit) if store is not None else MemoryContext.empty(message)

    @classmethod
    def explicit_payload(cls, message: str) -> str | None:
        clean = " ".join(message.split())
        folded = _fold(clean)
        for prefix in cls._REMEMBER_PREFIXES:
            folded_prefix = _fold(prefix)
            if folded.startswith(folded_prefix):
                return clean[len(prefix):].strip(" .")
        return None

    @staticmethod
    def _is_preference(content: str) -> bool:
        folded = _fold(content)
        return any(marker in folded for marker in ("je prefere", "ma preference", "j'aime", "je souhaite"))

    def remember_explicit(
        self,
        store: LongTermMemoryPort,
        content: str,
        *,
        provenance: str = "explicit_user_command",
    ) -> MemoryRecord:
        record = MemoryRecord.create(
            kind=MemoryKind.USER_PREFERENCE if self._is_preference(content) else MemoryKind.USER_FACT,
            content=content,
            confidence=0.80,
            status=MemoryStatus.USER_ASSERTED,
            provenance=provenance,
            evidence=("saisie humaine explicite",),
        )
        return store.remember(record)

    def commit(
        self,
        store: LongTermMemoryPort | None,
        *,
        message: str,
        response: str,
        route: str,
        selected_models: tuple[str, ...] = (),
        action_execution: Any | None = None,
    ) -> tuple[MemoryRecord, ...]:
        if store is None:
            return ()
        records: list[MemoryRecord] = []
        explicit = self.explicit_payload(message)
        if explicit:
            records.append(self.remember_explicit(store, explicit, provenance="conversation_user_assertion"))
        if action_execution is not None:
            action_data = action_execution.to_dict() if hasattr(action_execution, "to_dict") else dict(action_execution)
            action_content = (
                f"Action {action_data.get('kind', 'locale')} sur {action_data.get('target', '?')} : "
                f"statut {action_data.get('status', 'inconnu')}; raison {action_data.get('reason', '')}."
            )
            records.append(
                store.remember(
                    MemoryRecord.create(
                        kind=MemoryKind.ACTION_RESULT,
                        content=action_content,
                        confidence=1.0 if bool(action_data.get("executed")) else 0.95,
                        status=MemoryStatus.OBSERVED,
                        provenance="aca_action_kernel",
                        evidence=(json.dumps(action_data, ensure_ascii=False),),
                        metadata={"route": route, "selected_models": list(selected_models)},
                    )
                )
            )
        if not explicit and action_execution is None:
            bounded_response = " ".join(response.split())[:1500]
            conversation_content = f"Demande : {message} | Reponse ACA : {bounded_response}"
            records.append(
                store.remember(
                    MemoryRecord.create(
                        kind=MemoryKind.CONVERSATION,
                        content=conversation_content,
                        confidence=0.50,
                        status=MemoryStatus.OBSERVED,
                        provenance="aca_pipeline_turn",
                        evidence=("interaction locale observee",),
                        metadata={"route": route, "selected_models": list(selected_models)},
                    )
                )
            )
        return tuple(records)
