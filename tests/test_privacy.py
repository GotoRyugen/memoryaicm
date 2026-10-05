"""RGPD : export (accès, portabilité), backup/restore chiffrés, erase (effacement, crypto-shredding)."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from memoryaicm import Settings, MemoryAgent, privacy
from memoryaicm.cli import main
from memoryaicm.llm.stub import StubBackend
from memoryaicm.log import Journal


def _seed(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille et j'habite à Lyon", s)
    agent.turn("mon éditeur est neovim", s)
    return s


# ---------------------------------------------------------------------------- export
def test_export_jsonl_est_complet_et_reverifiable(agent, tmp_path):
    _seed(agent)
    out = tmp_path / "export.jsonl"
    r = privacy.export_jsonl(agent.journal, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    header, events = json.loads(lines[0]), [json.loads(l) for l in lines[1:]]
    assert header["memoryaicm_export"] == 1 and header["events"] == len(events) == r["events"] == len(agent.journal)
    assert header["last_hash"] == events[-1]["hash"]
    # revérification hors outil, avec la règle écrite dans l'en-tête
    prev = "0" * 64
    for e in events:
        body = json.dumps({"prev": prev, "id": e["id"], "ts": e["ts"], "actor": e["actor"], "type": e["type"], "payload": e["payload"]},
                          sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        assert e["prev_hash"] == prev and hashlib.sha256(body.encode("utf-8")).hexdigest() == e["hash"]
        prev = e["hash"]
    assert any("Camille" in json.dumps(e["payload"], ensure_ascii=False) for e in events)


def test_export_via_cli(agent, settings, tmp_path, capsys):
    _seed(agent)
    agent.close()
    out = tmp_path / "e.jsonl"
    main(["--home", str(settings.root), "export", "--out", str(out)])
    assert out.exists() and "export :" in capsys.readouterr().out


# ---------------------------------------------------------------------------- backup / restore
def test_backup_restore_roundtrip(agent, settings, tmp_path):
    pytest.importorskip("cryptography")
    _seed(agent)
    n = len(agent.journal)
    bk = tmp_path / "mem.maicm"
    r = privacy.backup(settings, bk)
    assert bk.exists() and r["journal_bytes"] > 0
    assert privacy.key_path(settings).exists()
    blob = bk.read_bytes()
    assert blob.startswith(privacy.MAGIC) and b"Camille" not in blob and b"SQLite format" not in blob
    key = privacy.vault_key(settings, create=False)
    # restauration dans un dossier vide, avec la clé fournie
    s2 = Settings(root=tmp_path / "restored")
    r2 = privacy.restore(s2, bk, key=key)
    assert r2["events"] == n and "intacte" in r2["verify"]
    a2 = MemoryAgent(s2, backend=StubBackend())
    try:
        a2.index.rebuild()
        assert any("Camille" in f.txt for f, _, _ in a2.index.retrieve("comment je m'appelle ?", k=3))
    finally:
        a2.close()


def test_restore_refuse_mauvaise_cle_et_alteration(agent, settings, tmp_path):
    pytest.importorskip("cryptography")
    _seed(agent)
    bk = tmp_path / "mem.maicm"
    privacy.backup(settings, bk)
    with pytest.raises(privacy.PrivacyError):
        privacy.restore(Settings(root=tmp_path / "r1"), bk, key=bytes(32))
    blob = bytearray(bk.read_bytes()); blob[-1] ^= 0xFF
    (tmp_path / "bad.maicm").write_bytes(bytes(blob))
    with pytest.raises(privacy.PrivacyError):
        privacy.restore(Settings(root=tmp_path / "r2"), tmp_path / "bad.maicm", key=privacy.vault_key(settings, create=False))


def test_restore_refuse_dossier_non_vide(agent, settings, tmp_path):
    pytest.importorskip("cryptography")
    _seed(agent)
    bk = tmp_path / "mem.maicm"
    privacy.backup(settings, bk)
    with pytest.raises(privacy.PrivacyError):
        privacy.restore(settings, bk)


@pytest.mark.skipif(sys.platform == "win32", reason="droits POSIX")
def test_cle_lisible_par_le_seul_proprietaire(settings):
    settings.ensure_dirs()
    privacy.vault_key(settings)
    assert (privacy.key_path(settings).stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------- erase
def test_erase_exige_confirmation(agent, settings):
    _seed(agent)
    with pytest.raises(privacy.PrivacyError):
        privacy.erase(settings)
    assert settings.journal_path.exists()


def test_erase_detruit_cle_et_fichiers_et_laisse_une_pierre_tombale(agent, settings, tmp_path):
    pytest.importorskip("cryptography")
    _seed(agent)
    bk = tmp_path / "mem.maicm"
    privacy.backup(settings, bk)
    agent.sleep()                                   # adaptateur compilé : des fichiers dans adapter/
    agent.close()
    r = privacy.erase(settings, confirm=True)
    assert r["files"] >= 3 and r["bytes"] > 0 and r["locked"] == []
    rest = [p for p in settings.root.rglob("*")]
    assert rest == [settings.root / privacy.TOMBSTONE]
    assert not privacy.key_path(settings).exists()
    # la sauvegarde chiffrée est désormais illisible : plus de clé (crypto-shredding)
    with pytest.raises(privacy.PrivacyError):
        privacy.restore(Settings(root=tmp_path / "again"), bk)
    # la mémoire repart de zéro
    a2 = MemoryAgent(settings, backend=StubBackend())
    try:
        assert len(a2.journal) >= 0 and not a2.index.retrieve("comment je m'appelle ?", k=3)
    finally:
        a2.close()


def test_erase_via_cli(agent, settings, capsys):
    _seed(agent)
    agent.close()
    with pytest.raises(SystemExit) as e:
        main(["--home", str(settings.root), "erase"])
    assert e.value.code == 2 and settings.journal_path.exists()
    main(["--home", str(settings.root), "erase", "--yes"])
    assert "effacé" in capsys.readouterr().out and not settings.journal_path.exists()
    assert (settings.root / privacy.TOMBSTONE).exists()


def test_erase_dossier_inexistant(tmp_path):
    r = privacy.erase(Settings(root=tmp_path / "nope"), confirm=True)
    assert r["existed"] is False
