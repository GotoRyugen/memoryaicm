"""JOURNAL : append-only · chaîne de hachage · plein texte · Log ⊇ Index (rebuild identique)."""

import sqlite3

import pytest

from memoryaicm import Journal, Index, Settings
from memoryaicm import model as M


def test_update_and_delete_are_refused(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    ev = j.append(M.EV_TURN_USER, "user", {"session": "s", "text": "bonjour"})
    with pytest.raises(sqlite3.DatabaseError):
        j.db.execute("UPDATE events SET payload='{}' WHERE id=?", (ev.id,))
    with pytest.raises(sqlite3.DatabaseError):
        j.db.execute("DELETE FROM events WHERE id=?", (ev.id,))
    assert len(j) == 1 and j.get(ev.id).payload["text"] == "bonjour"


def test_hash_chain_links_and_detects_tampering(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    a = j.append(M.EV_TURN_USER, "user", {"session": "s", "text": "un"})
    b = j.append(M.EV_TURN_USER, "user", {"session": "s", "text": "deux"})
    assert b.prev_hash == a.hash
    ok, msg = j.verify()
    assert ok and "2 événements" in msg
    # on retire volontairement la protection pour simuler une altération hors système
    j.db.execute("DROP TRIGGER events_no_update")
    j.db.execute("UPDATE events SET payload=? WHERE id=?", ('{"session": "s", "text": "trois"}', a.id))
    ok, msg = j.verify()
    assert not ok and a.id in msg


def test_fulltext_search_covers_everything(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.append(M.EV_TURN_USER, "user", {"session": "s", "text": "je m'appelle Camille"})
    j.append(M.EV_FACT_WRITE, "user", {"fact": {"txt": "habite à Lyon"}})
    assert [e.type for e in j.search("Lyon")] == [M.EV_FACT_WRITE]
    assert len(j.search("Camille")) == 1
    assert j.search("") == []


def test_index_rebuild_is_identical(agent):
    s = agent.start_session("s1")
    agent.turn("Je m'appelle Camille et j'utilise neovim. Je préfère les réponses courtes.", s)
    agent.turn("En fait j'habite à Lyon", s)
    agent.turn("oublie neovim", s)
    before = agent.index.state_hash()
    facts_before = {(f.id, f.on, f.ver) for f in agent.index.all_facts(on_only=False)}
    n = agent.index.rebuild()
    assert n == len(agent.journal)
    assert agent.index.state_hash() == before
    assert {(f.id, f.on, f.ver) for f in agent.index.all_facts(on_only=False)} == facts_before


def test_memory_journal_works(settings):
    j = Journal(":memory:")
    ix = Index(":memory:", j, settings)
    j.append(M.EV_TURN_USER, "user", {"session": "s", "text": "x"})
    assert ix.sync() == 1
