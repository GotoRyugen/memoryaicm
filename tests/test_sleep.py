"""SLEEP : dedup · conflits · abstraction · élagage/ttl · compile → tests → promote | rollback."""

import json

from memoryaicm import model as M
from memoryaicm.sleep import Sleep
from conftest import FakeBackend, cand, make_agent


def _raw(agent, txt, subject, kind="SEM", ts=None, exclusive=True, ttl=0.0):
    f = M.Fact(id=M.new_id("f"), kind=M.Kind(kind), txt=txt, subject=subject, src=M.Src.USER,
               t0=ts or M.now(), t_upd=ts or M.now(), exclusive=exclusive, ttl=ttl)
    agent.journal.append(M.EV_FACT_WRITE, "user", {"fact": f.to_dict()}, ts=ts)
    agent.index.sync()
    return f


def test_dedup_merges_near_identical_facts_with_lineage(agent):
    a = _raw(agent, "utilise neovim", "outil:neovim", exclusive=False)
    b = _raw(agent, "il utilise neovim", "explicit:il-utilise-neovim", exclusive=False)
    rep = agent.sleep()
    assert len(rep.deduped) == 1
    active = agent.index.all_facts()
    merged = [f for f in active if set(f.deps) == {a.id, b.id}]
    assert merged and agent.index.fact(a.id).on is False and agent.index.fact(b.id).on is False


def test_conflict_keeps_most_recent_exclusive(agent):
    now = M.now()
    old = _raw(agent, "habite à Paris", "ville", ts=now - 100)
    new = _raw(agent, "habite à Lyon", "ville", ts=now - 50)
    rep = agent.sleep()
    assert rep.conflicts == [old.id]
    assert [f.id for f in agent.index.by_subject("ville")] == [new.id]


def test_abstraction_groups_non_exclusive_subjects(agent):
    for t in ("neovim", "tmux", "ripgrep"):
        _raw(agent, f"utilise {t}", f"outil:{t}", exclusive=False)
    rep = agent.sleep()
    assert len(rep.abstracted) == 1
    ab = agent.index.fact(rep.abstracted[0])
    assert ab.subject == "abs:outil" and len(ab.deps) == 3 and "neovim" in ab.txt
    # un second sommeil sans changement ne recrée rien
    assert agent.sleep().abstracted == []


def test_prune_and_ttl_deindex_without_deleting(agent):
    t0 = 1_000_000.0
    stale = _raw(agent, "s'appelle Camille", "nom", ts=t0)
    epi = _raw(agent, "a demandé un script", "epi:script", kind="EPI", ts=t0, ttl=90 * 86400)
    far = t0 + 400 * 86400
    rep = Sleep(agent.journal, agent.index, agent.backend, agent.adapter, agent.s).run(now=far)
    assert stale.id in rep.pruned and epi.id in rep.expired
    assert agent.index.fact(stale.id).on is False and agent.index.get(stale.id)["off_reason"] == "prune"
    assert agent.index.get(epi.id)["off_reason"] == "ttl"
    assert agent.journal.search("Camille"), "désindexé, jamais détruit"


def test_promote_when_tests_green(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille et j'habite à Lyon", s)
    rep = agent.sleep()
    assert rep.promoted and agent.adapter.current() == rep.version
    m = agent.adapter.manifest(rep.version)
    assert m["promoted"] and m["tests"]["pass"] and m["index_hash"] == agent.index.state_hash()
    assert (agent.s.adapter_dir / f"v{rep.version}" / "train.jsonl").exists()
    lines = [json.loads(l) for l in (agent.s.adapter_dir / f"v{rep.version}" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(l.get("generic") for l in lines) and any("fact_id" in l for l in lines)


def test_rollback_when_tests_red(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille", s)
    first = agent.sleep()
    assert first.promoted
    red = Sleep(agent.journal, agent.index, agent.backend, agent.adapter, agent.s,
                tests_runner=lambda: (False, {"pass": False, "suites": []}))
    rep = red.run()
    assert not rep.promoted and agent.adapter.current() == first.version
    assert agent.adapter.manifest(rep.version)["rejected"] == "tests rouges"
    types = [e.type for e in agent.journal.replay()]
    assert M.EV_ADAPTER_ROLLBACK in types and M.EV_SLEEP in types


def test_sleep_is_journaled_and_index_rebuilds(agent):
    s = agent.start_session("s1")
    agent.turn("j'utilise neovim", s)
    agent.sleep()
    h = agent.index.state_hash()
    agent.index.rebuild()
    assert agent.index.state_hash() == h
