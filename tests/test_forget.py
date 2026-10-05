"""FORGET : événement, pas delete · cascade lignage · vue rédigée · adaptateur recompilé · journal intact."""

from memoryaicm import model as M


def test_forget_is_an_event_and_journal_stays_intact(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille", s)
    n_before = len(agent.journal)
    fr = agent.forget("Camille", session=s)
    assert fr.ids and len(agent.journal) > n_before
    assert agent.journal.verify()[0]
    f = agent.index.fact(fr.ids[0])
    assert f.on is False and agent.index.get(f.id)["off_reason"] == "forget"
    # le journal garde tout : le texte reste consultable en plein texte
    assert agent.journal.search("Camille")


def test_forget_cascades_to_derived_facts(agent):
    s = agent.start_session("s1")
    agent.turn("j'utilise neovim", s)
    agent.turn("j'utilise tmux", s)
    agent.turn("j'utilise ripgrep", s)
    rep = agent.sleep()
    assert rep.abstracted, "3 outils ⇒ une abstraction"
    abs_fact = agent.index.fact(rep.abstracted[0])
    fr = agent.forget("tmux", session=s)
    assert abs_fact.id in fr.cascade
    assert agent.index.fact(abs_fact.id).on is False


def test_forget_redacts_turns_from_context_view_only(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille", s)
    agent.turn("et j'habite à Lyon", s)
    res = agent.turn("oublie Camille", s)
    assert res.level == "forget" and res.forgotten
    ctx, _ = agent.ctx.build(s, "bonjour")
    texts = [t for _, t in ctx.history]
    assert not any("Camille" in t for t in texts), "le tour d'origine sort de la vue"
    assert any("Lyon" in t for t in texts), "les autres tours restent"
    # mais le journal contient toujours le tour
    assert any(e.payload.get("text") == "je m'appelle Camille" for e in agent.journal.replay(types=(M.EV_TURN_USER,)))


def test_forget_recompiles_adapter_without_the_fact(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille et j'habite à Lyon", s)
    agent.sleep()
    assert "Camille" in agent.adapter.profile()
    agent.forget("Camille", session=s)
    prof = agent.adapter.profile()
    assert "Camille" not in prof and "Lyon" in prof
    assert agent.adapter.manifest(agent.adapter.current())["reason"] == "forget"


def test_forget_unknown_is_noop(agent):
    s = agent.start_session("s1")
    fr = agent.forget("licorne", session=s)
    assert fr.ids == [] and "rien ne correspond" in fr.summary()


def test_recall_reactivates(agent):
    s = agent.start_session("s1")
    r = agent.turn("je m'appelle Camille", s)
    fid = r.write.written[0].id
    agent.forget("Camille", session=s)
    agent.reactivate(fid)
    assert agent.index.fact(fid).on is True
