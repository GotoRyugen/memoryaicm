"""RGPD / GDPR — droits de la personne sur sa mémoire.

Un dossier mémoire (`home`) = une personne. Le journal ne modifie ni ne supprime jamais une ligne ;
les droits s'exercent donc sur le dossier entier :

- **droit d'accès et portabilité** (art. 15 et 20) : `export` — tout le journal en JSONL lisible,
  chaque événement avec sa date, son auteur, son contenu et son hachage, donc revérifiable ;
- **sauvegarde chiffrée** : `backup` / `restore` — instantané cohérent du journal, chiffré AES-256-GCM
  avec une clé propre au dossier (`vault.key`, jamais copiée dans la sauvegarde) ;
- **droit à l'effacement** (art. 17) : `erase` — la clé est détruite (toute sauvegarde chiffrée devient
  illisible : *crypto-shredding*), chaque fichier du dossier est écrasé puis supprimé, et seule reste
  une pierre tombale datée, sans contenu.

Le chiffrement s'appuie sur le paquet `cryptography` (extra `[secure]`) ; export et erase n'en ont pas
besoin. Rien ici ne touche au contrat du journal : on n'édite pas une ligne, on exporte ou on remplace
le dossier entier.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .config import Settings
from .log import Journal

MAGIC = b"MAICM-BACKUP-1\n"
KEY_FILE = "vault.key"
TOMBSTONE = "ERASED"
_NONCE_LEN = 12


class PrivacyError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- clé et chiffrement
def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    except ImportError as e:  # pragma: no cover - dépend de l'environnement
        raise PrivacyError(
            "le chiffrement demande le paquet `cryptography` : pip install \"memoryaicm[secure]\""
        ) from e
    return AESGCM


def key_path(settings: Settings) -> Path:
    return settings.root / KEY_FILE


def vault_key(settings: Settings, create: bool = True) -> bytes:
    """Clé AES-256 propre au dossier mémoire. Créée au premier usage, lisible par le seul propriétaire."""
    p = key_path(settings)
    if p.exists():
        key = p.read_bytes()
        if len(key) != 32:
            # Avant 0.6.1, Windows écrivait la clé en mode texte : chaque octet 0x0A devenait \r\n.
            # La conversion est sans ambiguïté (un \r\n d'origine serait devenu \r\r\n) : on répare.
            fixed = key.replace(b"\r\n", b"\n")
            if len(fixed) != 32:
                raise PrivacyError(f"clé invalide : {p}")
            _write_key(p, fixed, replace=True)
            key = fixed
        return key
    if not create:
        raise PrivacyError(f"aucune clé : {p}")
    settings.root.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    _write_key(p, key)
    return key


def _write_key(p: Path, key: bytes, replace: bool = False) -> None:
    """Écriture binaire (O_BINARY : sans lui, Windows transforme 0x0A en \r\n), lisible du seul propriétaire."""
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
    target = p.with_name(p.name + ".tmp") if replace else p
    fd = os.open(str(target), flags | (os.O_TRUNC if replace else os.O_EXCL), 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    if replace:
        os.replace(target, p)


def encrypt_bytes(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    AESGCM = _aesgcm()
    nonce = secrets.token_bytes(_NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, data, aad)


def decrypt_bytes(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    AESGCM = _aesgcm()
    if len(blob) < _NONCE_LEN + 16:
        raise PrivacyError("sauvegarde tronquée")
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad)
    except Exception as e:
        raise PrivacyError("déchiffrement refusé : mauvaise clé ou fichier altéré") from e


# ---------------------------------------------------------------------------- export (accès, portabilité)
def export_jsonl(journal: Journal, out: Path) -> dict:
    """Tout le journal, un événement par ligne, précédé d'une ligne d'en-tête. Revérifiable hors outil :
    hash = sha256(json{prev,id,ts,actor,type,payload}) en clés triées, sans espaces, UTF-8."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        header = {
            "memoryaicm_export": 1, "exported_at": time.time(), "events": len(journal),
            "last_hash": journal.last_hash(), "hash_rule": "sha256(json.dumps({prev,id,ts,actor,type,payload}, sort_keys, separators=(',',':')))",
        }
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for ev in journal.replay():
            f.write(json.dumps({
                "seq": ev.seq, "id": ev.id, "ts": ev.ts, "actor": ev.actor, "type": ev.type,
                "payload": ev.payload, "prev_hash": ev.prev_hash, "hash": ev.hash,
            }, ensure_ascii=False) + "\n")
            n += 1
    return {"path": str(out), "events": n, "bytes": out.stat().st_size}


# ---------------------------------------------------------------------------- sauvegarde chiffrée
def _snapshot_journal(path: Path) -> bytes:
    """Octets du journal dans un état cohérent, même si d'autres connexions sont ouvertes :
    API de sauvegarde SQLite vers un fichier temporaire à côté du journal (donc pas plus exposé
    que lui), lu puis écrasé et supprimé."""
    tmp = path.with_name(path.name + ".snapshot.tmp")
    src = sqlite3.connect(str(path), isolation_level=None)
    try:
        dst = sqlite3.connect(str(tmp), isolation_level=None)
        try:
            src.backup(dst)
        finally:
            dst.close()
        return tmp.read_bytes()
    finally:
        src.close()
        if tmp.exists():
            _shred_file(tmp)


def backup(settings: Settings, out: Path) -> dict:
    """Instantané chiffré du journal (la seule chose à sauvegarder : index et adaptateur se reconstruisent)."""
    jp = settings.journal_path
    if not jp.exists():
        raise PrivacyError(f"aucun journal : {jp}")
    key = vault_key(settings, create=True)
    data = _snapshot_journal(jp)
    meta = json.dumps({"format": 1, "created_at": time.time(), "bytes": len(data)}, sort_keys=True).encode("utf-8")
    blob = MAGIC + len(meta).to_bytes(4, "big") + meta + encrypt_bytes(key, data, aad=meta)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return {"path": str(out), "bytes": out.stat().st_size, "journal_bytes": len(data), "key": str(key_path(settings))}


def restore(settings: Settings, src: Path, key: bytes | None = None) -> dict:
    """Restaure une sauvegarde dans un dossier mémoire SANS journal, puis vérifie la chaîne.
    La clé vient de `vault.key` du dossier cible, ou de `key` (octets) si on restaure ailleurs."""
    jp = settings.journal_path
    if jp.exists():
        raise PrivacyError(f"un journal existe déjà : {jp} — restaurer dans un dossier vide")
    blob = Path(src).read_bytes()
    if not blob.startswith(MAGIC):
        raise PrivacyError("ce fichier n'est pas une sauvegarde memoryaicm")
    pos = len(MAGIC)
    mlen = int.from_bytes(blob[pos:pos + 4], "big")
    meta = blob[pos + 4:pos + 4 + mlen]
    key = key if key is not None else vault_key(settings, create=False)
    data = decrypt_bytes(key, blob[pos + 4 + mlen:], aad=meta)
    settings.ensure_dirs()
    jp.write_bytes(data)
    j = Journal(jp)
    try:
        ok, msg = j.verify()
        n = len(j)
    finally:
        j.close()
    if not ok:
        raise PrivacyError(f"journal restauré mais chaîne invalide : {msg}")
    return {"journal": str(jp), "events": n, "verify": msg}


# ---------------------------------------------------------------------------- effacement
def _shred_file(p: Path) -> int:
    """Écrase le contenu avant de supprimer (meilleur effort : sur SSD, c'est la clé détruite qui garantit)."""
    size = 0
    try:
        size = p.stat().st_size
        with p.open("r+b") as f:
            remaining = size
            while remaining > 0:
                chunk = min(remaining, 1 << 20)
                f.write(secrets.token_bytes(chunk))
                remaining -= chunk
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    p.unlink()
    return size


def erase(settings: Settings, confirm: bool = False) -> dict:
    """Droit à l'effacement : clé détruite, fichiers écrasés et supprimés, pierre tombale datée.

    Refuse sans `confirm=True`. Les fichiers verrouillés par un autre processus (serveur MCP ou `serve`
    encore ouverts, surtout sous Windows) sont listés dans `locked` : fermer et relancer."""
    if not confirm:
        raise PrivacyError("effacement non confirmé (erase --yes)")
    root = settings.root
    if not root.exists():
        return {"root": str(root), "files": 0, "bytes": 0, "locked": [], "existed": False}
    report = {"root": str(root), "files": 0, "bytes": 0, "locked": [], "existed": True}
    kp = key_path(settings)
    order = [kp] if kp.exists() else []           # la clé d'abord : à partir de là, les sauvegardes sont mortes
    order += sorted(p for p in root.rglob("*") if p.is_file() and p != kp)
    for p in order:
        try:
            report["bytes"] += _shred_file(p)
            report["files"] += 1
        except OSError as e:
            report["locked"].append(f"{p} ({e.strerror or e})")
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report["erased_at"] = stamp
    (root / TOMBSTONE).write_text(f"erased_at={stamp}\nfiles={report['files']}\n", encoding="utf-8")
    return report
