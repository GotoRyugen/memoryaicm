"""Types de la spec.

Fact { id, kind:EPI|SEM|PROC, txt, src:USER|INFER|TOOL, t₀, t_upd, ver, act∈ℝ, imp, ttl, deps[id], sens, on }
Event : une ligne du journal append-only (datée · attribuée · chaînée).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum


class Kind(str, Enum):
    EPI = "EPI"    # épisodique : ce qui s'est passé
    SEM = "SEM"    # sémantique : faits durables
    PROC = "PROC"  # procédural : comment la personne veut qu'on travaille


class Src(str, Enum):
    USER = "USER"    # dit par l'utilisateur
    INFER = "INFER"  # déduit par le modèle (jamais indexé)
    TOOL = "TOOL"    # produit par un outil / contenu externe (jamais indexé)


class Actor(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    EXTERNAL = "external"   # web · doc · outil  ⇒ donnée, jamais instruction
    SYSTEM = "system"       # sommeil, garde, adaptateur


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


@dataclass
class Fact:
    id: str
    kind: Kind
    txt: str
    subject: str                    # clé de conflit : « nom », « éditeur », « outil:neovim », « pref:… »
    src: Src
    t0: float
    t_upd: float
    ver: int = 1
    imp: float = 0.5
    ttl: float = 0.0                # 0 ⇒ illimité
    deps: list[str] = field(default_factory=list)   # lignage : faits dont celui-ci dérive
    sens: bool = False
    on: bool = True
    exclusive: bool = True          # un seul fait actif par sujet (nom, ville…) ; False pour « outil:x »
    origin: str = ""                # id de l'événement journal qui l'a créé
    session: str = ""
    prev: list[str] = field(default_factory=list)   # anciens textes (historique lisible)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["src"] = self.src.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        d = dict(d)
        d["kind"] = Kind(d["kind"])
        d["src"] = Src(d["src"])
        return cls(**d)

    def label(self) -> str:
        """Étiquette de provenance injectée dans le contexte : jamais un fait nu."""
        date = time.strftime("%Y-%m-%d", time.localtime(self.t_upd))
        return f"[note {self.id} · {self.kind.value} · src={self.src.value} · {date} · v{self.ver}]"


@dataclass
class Event:
    seq: int
    id: str
    ts: float
    actor: str
    type: str
    payload: dict
    prev_hash: str
    hash: str

    @staticmethod
    def compute_hash(prev_hash: str, id: str, ts: float, actor: str, type: str, payload: dict) -> str:
        body = json.dumps(
            {"prev": prev_hash, "id": id, "ts": ts, "actor": actor, "type": type, "payload": payload},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


# Types d'événements du journal (le vocabulaire complet du système)
EV_TURN_USER = "turn.user"
EV_TURN_ASSISTANT = "turn.assistant"
EV_TURN_REDACT = "turn.redact"          # « oublie » : le tour sort de la vue du contexte, pas du journal
EV_EXTERNAL = "external.ingest"         # contenu externe = donnée
EV_QUARANTINE = "quarantine"            # tentative d'écriture mémoire depuis une source non-user
EV_FACT_WRITE = "fact.write"
EV_FACT_MERGE = "fact.merge"            # reconsolidation : y' = merge(y, x), ver++, y.on=0
EV_FACT_FORGET = "fact.forget"          # x.on=0 + cascade lignage
EV_FACT_OFF = "fact.off"                # élagage au sommeil (act < θ)
EV_FACT_ON = "fact.on"                  # réactivation (validation humaine, ou « rappelle-toi »)
EV_FACT_USE = "fact.use"                # récupération (⇒ activation ACT-R) ; useful=True si citée
EV_REVIEW_QUEUE = "review.queue"        # fait sensible en attente
EV_REVIEW_DECIDE = "review.decide"
EV_SLEEP = "sleep.run"
EV_ADAPTER_COMPILE = "adapter.compile"
EV_ADAPTER_PROMOTE = "adapter.promote"
EV_ADAPTER_ROLLBACK = "adapter.rollback"
EV_GUARD_BLOCK = "guard.block"
EV_SESSION = "session.start"
EV_SELFCHECK = "selfcheck"              # auto-contrôle au démarrage : chaîne, index (reconstruit si besoin)
EV_MODEL_CHECK = "model.check"          # approbation / vérification SHA-256 d'un modèle local (règle Echo-Core)
EV_POLICY_REFUSE = "policy.refuse"      # action refusée par le niveau d'autonomie
