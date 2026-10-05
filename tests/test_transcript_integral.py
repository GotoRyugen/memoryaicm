"""L'add-on Claude doit pouvoir relire MOT POUR MOT ce que la mémoire a gardé.

Le journal conservait déjà tout, mais `memory_search` coupait sa sortie à 200 caractères : le
lecteur ne voyait jamais plus de 200 caractères de ce qui était pourtant conservé — la mémoire
était complète, la fenêtre ne l'était pas. `memory_read` rend un événement entier, et
`memory_transcript` archive la conversation elle-même, sans plafond ni résumé.
"""

from __future__ import annotations

from memoryaicm.tools import ToolRouter

LONG = "Le propriétaire explique en détail son architecture. " * 700 + " ancre_tout_au_bout"


def test_memory_transcript_archive_sans_plafond(agent):
    r = ToolRouter(agent, session="t", origin="test")
    out = r.call("memory_transcript", {"user_text": LONG, "assistant_text": "bien reçu", "session": "t"})
    assert "2 tour(s)" in out
    lus = r.call("memory_transcript", {"session": "t", "n": 5})
    assert "ancre_tout_au_bout" in lus and len(LONG) > 30000


def test_memory_read_rend_l_evenement_entier(agent):
    r = ToolRouter(agent, session="t", origin="test")
    r.call("memory_transcript", {"user_text": LONG, "assistant_text": "ok", "session": "t"})
    trouve = r.call("memory_search", {"query": "ancre_tout_au_bout"})
    seq = int(trouve.split("#", 1)[1].split(" ", 1)[0])
    entier = r.call("memory_read", {"seq": seq})
    assert "ancre_tout_au_bout" in entier
    assert entier.count("architecture") > 600          # tout le texte, pas un extrait


def test_memory_search_annonce_ce_qu_il_coupe(agent):
    r = ToolRouter(agent, session="t", origin="test")
    r.call("memory_transcript", {"user_text": LONG, "assistant_text": "ok", "session": "t"})
    court = r.call("memory_search", {"query": "ancre_tout_au_bout", "chars": 100})
    assert "memory_read seq=" in court and "car." in court
    tout = r.call("memory_search", {"query": "ancre_tout_au_bout", "chars": 0})
    assert "ancre_tout_au_bout" in tout


def test_la_chaine_tient_apres_archivage(agent):
    r = ToolRouter(agent, session="t", origin="test")
    r.call("memory_transcript", {"user_text": "café « déjà » — 100 %\nligne\t2 🧠", "assistant_text": "ok", "session": "t"})
    ok, msg = agent.journal.verify()
    assert ok, msg
    lus = r.call("memory_transcript", {"session": "t", "n": 2})
    assert "café « déjà » — 100 %" in lus and "🧠" in lus


def test_archiver_n_est_pas_retenir(agent):
    """La transcription trace ce qui a été dit ; elle ne fabrique aucun fait durable."""
    avant = len(agent.index.all_facts(on_only=False))
    ToolRouter(agent, session="t", origin="test").call(
        "memory_transcript", {"user_text": "je m'appelle Zorglub et j'habite à Trifouillis", "assistant_text": "ok", "session": "t"})
    assert len(agent.index.all_facts(on_only=False)) == avant
