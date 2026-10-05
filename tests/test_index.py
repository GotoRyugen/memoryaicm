"""INDEX : activation ACT-R · top-k sans dump · cascade de lignage · PROC séparées."""

import math

from memoryaicm import model as M


def _write(agent, txt, subject, kind="SEM", ts=None, exclusive=True):
    f = M.Fact(id=M.new_id("f"), kind=M.Kind(kind), txt=txt, subject=subject, src=M.Src.USER,
               t0=ts or M.now(), t_upd=ts or M.now(), exclusive=exclusive)
    agent.journal.append(M.EV_FACT_WRITE, "user", {"fact": f.to_dict()}, ts=ts)
    agent.index.sync()
    return f


def test_activation_rises_with_use_and_decays_with_time(agent):
    t0 = 1_000_000.0
    f = _write(agent, "s'appelle Camille", "nom", ts=t0)
    a_fresh = agent.index.activation(f.id, now=t0 + 10)
    a_week = agent.index.activation(f.id, now=t0 + 7 * 86400)
    assert a_fresh > a_week
    agent.journal.append(M.EV_FACT_USE, "system", {"ids": [f.id], "useful": []}, ts=t0 + 7 * 86400 - 5)
    agent.index.sync()
    assert agent.index.activation(f.id, now=t0 + 7 * 86400) > a_week
    assert agent.index.activation("inconnu") == -math.inf


def test_effective_activation_uses_importance(agent):
    t0 = 1_000_000.0
    f = _write(agent, "utilise neovim", "outil:neovim", ts=t0, exclusive=False)
    base = agent.index.effective(f, now=t0 + 86400)
    agent.journal.append(M.EV_FACT_USE, "system", {"ids": [f.id], "useful": [f.id]}, ts=t0 + 86400 - 1)
    agent.index.sync()
    assert agent.index.fact(f.id).imp > 0.5
    assert agent.index.effective(agent.index.fact(f.id), now=t0 + 86400) > base


def test_retrieve_is_topk_and_never_dumps(agent):
    _write(agent, "s'appelle Camille", "nom")
    _write(agent, "habite à Lyon", "ville")
    _write(agent, "utilise neovim", "outil:neovim", exclusive=False)
    hits = agent.index.retrieve("où j'habite ?")
    assert hits and hits[0][0].subject == "ville"
    assert agent.index.retrieve("quelle est la capitale de la Mongolie ?") == []
    assert len(agent.index.retrieve("neovim Lyon Camille", k=2)) == 2


def test_dependents_cascade_is_transitive(agent):
    a = _write(agent, "a", "x:a", exclusive=False)
    b = M.Fact(id=M.new_id("f"), kind=M.Kind.SEM, txt="b", subject="x:b", src=M.Src.USER, t0=1, t_upd=1, deps=[a.id])
    c = M.Fact(id=M.new_id("f"), kind=M.Kind.SEM, txt="c", subject="x:c", src=M.Src.USER, t0=1, t_upd=1, deps=[b.id])
    for f in (b, c):
        agent.journal.append(M.EV_FACT_WRITE, "user", {"fact": f.to_dict()})
    agent.index.sync()
    assert set(agent.index.dependents(a.id)) == {b.id, c.id}


def test_prefs_are_proc_only(agent):
    _write(agent, "préfère les réponses courtes", "pref:courtes", kind="PROC")
    _write(agent, "s'appelle Camille", "nom")
    assert [f.kind for f in agent.index.prefs()] == [M.Kind.PROC]
    assert all(f.kind != M.Kind.PROC for f, _, _ in agent.index.retrieve("réponses courtes"))
