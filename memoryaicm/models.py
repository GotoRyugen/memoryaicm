"""Approbation des modèles locaux par empreinte SHA-256 — règle reprise d'Echo-Core.

« Si approved_sha256 est vide, le modèle est refusé. Ce n'est pas un bug, c'est une sécurité. »

data/models.json : { "approved": { "<chemin absolu>": {"sha256": …, "approved_at": …, "size": …} } }
Chaque vérification est journalisée (model.check) : chemin, empreinte, verdict — auditable comme le reste.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import model as M
from .config import Settings
from .log import Journal


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class ModelApprovals:
    def __init__(self, settings: Settings, journal: Journal | None = None):
        self.s = settings
        self.journal = journal
        self.path = settings.root / "models.json"

    # ------------------------------------------------------------------ registre
    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"approved": {}}
        return {"approved": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> dict:
        return self._load().get("approved", {})

    @staticmethod
    def _key(path: Path | str) -> str:
        return str(Path(path).resolve())

    # ------------------------------------------------------------------ approbation
    def approve(self, path: Path | str) -> dict:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"modèle introuvable : {p}")
        digest = sha256_file(p)
        data = self._load()
        entry = {"sha256": digest, "approved_at": time.time(), "size": p.stat().st_size}
        data.setdefault("approved", {})[self._key(p)] = entry
        self._save(data)
        self._log("approve", p, digest, True, "approuvé")
        return dict(entry, path=self._key(p))

    def revoke(self, path: Path | str) -> bool:
        data = self._load()
        removed = data.get("approved", {}).pop(self._key(path), None) is not None
        self._save(data)
        self._log("revoke", Path(path), "", removed, "révoqué" if removed else "absent")
        return removed

    # ------------------------------------------------------------------ vérification
    def check(self, path: Path | str) -> tuple[bool, str]:
        """(ok, raison). Refuse si : fichier absent, empreinte approuvée vide/absente, ou empreinte différente."""
        p = Path(path)
        if not p.is_file():
            self._log("check", p, "", False, "fichier introuvable")
            return False, f"modèle introuvable : {p}"
        approved = self.list().get(self._key(p), {}).get("sha256", "")
        if not approved:
            self._log("check", p, "", False, "approved_sha256 vide")
            return False, ("approved_sha256 est vide : modèle non approuvé. "
                           f"Ce n'est pas une panne : lancer `memoryaicm model approve \"{p}\"` après vérification.")
        digest = sha256_file(p)
        if digest != approved:
            self._log("check", p, digest, False, "empreinte différente")
            return False, f"empreinte différente de l'empreinte approuvée ({digest[:12]}… ≠ {approved[:12]}…) : modèle refusé."
        self._log("check", p, digest, True, "conforme")
        return True, f"modèle approuvé ({digest[:12]}…)"

    def _log(self, action: str, p: Path, digest: str, ok: bool, detail: str) -> None:
        if self.journal is not None:
            self.journal.append(M.EV_MODEL_CHECK, M.Actor.SYSTEM.value,
                                {"action": action, "path": str(p), "sha256": digest, "ok": ok, "detail": detail})
