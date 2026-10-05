"""Suites de promotion : rappel · injection (journal en mémoire, rien n'est écrit) · lignage · journal."""

from memoryaicm import model as M
from memoryaicm.bench import injection_suite, load_injection_cases, recall_suite, run_all


def test_injection_suite_never_writes_and_quarantines(agent):
    n_before = len(agent.journal)
    rep = injection_suite(agent.backend, agent.s)
    assert rep["pass"] and rep["leaks"] == 0 and rep["quarantined"] >= 4
    assert len(agent.journal) == n_before, "la suite tourne sur un journal en mémoire"
    assert len(load_injection_cases()) >= 4


def test_recall_suite_on_real_facts(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille, j'habite à Lyon et mon éditeur est neovim", s)
    rep = recall_suite(agent.index)
    assert rep["total"] == 3 and rep["ok"] == 3 and rep["pass"]


def test_run_all_green_then_lineage_violation_detected(agent):
    s = agent.start_session("s1")
    agent.turn("j'utilise neovim", s)
    ok, rep = run_all(agent.journal, agent.index, agent.backend, agent.s)
    assert ok and [x["name"] for x in rep["suites"]] == ["recall", "injection", "lineage", "journal"]
    # violation artificielle : un fait actif qui dépend d'un fait oublié
    base = agent.index.all_facts()[0]
    agent.journal.append(M.EV_FACT_FORGET, "user", {"ids": [base.id], "cascade": [], "query": "x"})
    child = M.Fact(id=M.new_id("f"), kind=M.Kind.SEM, txt="dérivé", subject="x:d", src=M.Src.USER, t0=1, t_upd=1, deps=[base.id])
    agent.journal.append(M.EV_FACT_WRITE, "user", {"fact": child.to_dict()})
    agent.index.sync()
    ok, rep = run_all(agent.journal, agent.index, agent.backend, agent.s)
    assert not ok and rep["suites"][2]["violations"] == [child.id]
