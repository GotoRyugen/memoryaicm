"""ACA / Echo-Core : memoryaicm comme LongTermMemoryPort, testé contre le vrai module `aca/memory.py`
(copie de référence dans tests/aca_ref, ou chemin MEMORYAICM_ACA_MEMORY) et, à défaut, contre les équivalents locaux."""

import importlib
import os
import sys
from pathlib import Path

import pytest

from memoryaicm import MemoryAgent, Settings
from memoryaicm import model as M
from memoryaicm.llm.stub import StubBackend

REF = Path(os.environ.get("MEMORYAICM_ACA_MEMORY") or (Path(__file__).parent / "aca_ref" / "memory.py"))


@pytest.fixture(scope="module")
def aca_memory():
    """Charge `aca.memory` réel dans un paquet `aca` factice, pour que l'intégration prenne les vraies classes."""
    if not REF.exists():
        pytest.skip("module aca.memory de référence absent")
    pkg_dir = REF.parent
    import types
    pkg = types.ModuleType("aca"); pkg.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
    sys.modules["aca"] = pkg
    spec = importlib.util.spec_from_file_location("aca.memory", REF)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aca.memory"] = mod
    spec.loader.exec_module(mod)
    # recharge l'intégration pour qu'elle voie les classes ACA
    import memoryaicm.integrations.aca as integ
    importlib.reload(integ)
    assert integ.ACA_AVAILABLE
    yield mod, integ
    sys.modules.pop("aca.memory", None); sys.modules.pop("aca", None)
    importlib.reload(integ)


def test_fallback_shims_without_aca(tmp_path):
    import memoryaicm.integrations.aca as integ
    if integ.ACA_AVAILABLE:
        pytest.skip("aca importable : les équivalents locaux ne sont pas utilisés")
    store = _store(tmp_path, integ)
    rec = integ.MemoryRecord.create(kind="user_fact", content="utilise nmap", confidence=0.7, status="observed", provenance="x")
    store.remember(rec)
    ctx = store.query("quels outils ?")
    assert ctx.hits and ctx.to_dict()["hits"][0]["content"] == "utilise nmap"


def _store(tmp_path, integ):
    a = MemoryAgent(Settings(root=tmp_path / "d"), backend=StubBackend())
    return integ.MemoryaicmLongTermMemory(agent=a, session="aca_test")


def test_port_contract_with_real_aca_classes(tmp_path, aca_memory):
    aca, integ = aca_memory
    store = _store(tmp_path, integ)
    rec = aca.MemoryRecord.create(kind=aca.MemoryKind.USER_FACT, content="habite à Lyon", confidence=0.8,
                                  status=aca.MemoryStatus.USER_ASSERTED, provenance="explicit_user_command")
    assert store.remember(rec) is rec
    assert store.count() == 1
    ctx = store.query("où est-ce que je vis ?", limit=6)
    assert isinstance(ctx, aca.MemoryContext) and ctx.hits and isinstance(ctx.hits[0], aca.MemoryHit)
    hit = ctx.hits[0]
    assert isinstance(hit.record, aca.MemoryRecord) and hit.record.content == "habite à Lyon"
    assert hit.record.kind == aca.MemoryKind.USER_FACT and hit.record.status == aca.MemoryStatus.USER_ASSERTED
    assert hit.record.provenance.startswith("memoryaicm [note ")
    d = ctx.to_dict()
    assert d["hit_count"] == 1 and "epistemic_notice" in d and "retrieval_score" in d["hits"][0]
    assert store.recent(limit=5)[0].content == "habite à Lyon"
    assert Path(store.path).name == "journal.sqlite"


def test_conversation_records_become_turns_and_facts(tmp_path, aca_memory):
    aca, integ = aca_memory
    store = _store(tmp_path, integ)
    coord = integ.MemoryaicmCoordinator()
    writes = coord.commit(store, message="souviens-toi que je préfère les réponses courtes",
                         response="Entendu.", route="direct_rules", selected_models=("granite",))
    assert len(writes) == 1 and isinstance(writes[0], aca.MemoryRecord)
    prefs = store.agent.index.prefs()
    assert prefs and "réponses courtes" in prefs[0].txt and prefs[0].imp == 1.0
    coord.commit(store, message="je m'appelle Camille et j'utilise burpsuite", response="Noté.", route="contextual_rules")
    types = [e.type for e in store.agent.journal.replay()]
    assert M.EV_TURN_USER in types and M.EV_TURN_ASSISTANT in types and M.EV_FACT_WRITE in types
    ctx = coord.retrieve(store, "comment je m'appelle ?")
    assert any("Camille" in h.record.content for h in ctx.hits)
    assert any(h.record.kind == aca.MemoryKind.USER_PREFERENCE for h in ctx.hits), "les préférences accompagnent toujours"


def test_action_results_are_episodic_and_conflicts_version(tmp_path, aca_memory):
    aca, integ = aca_memory
    store = _store(tmp_path, integ)
    coord = integ.MemoryaicmCoordinator()

    class Exec:
        def to_dict(self):
            return {"kind": "create_note", "target": "/chemin/vers/workspace/notes.md", "status": "done", "reason": "ok", "executed": True}

    writes = coord.commit(store, message="crée une note", response="fait", route="simulate", action_execution=Exec())
    assert [w.kind for w in writes] == [aca.MemoryKind.CONVERSATION, aca.MemoryKind.ACTION_RESULT]
    epi = [f for f in store.agent.index.all_facts() if f.kind == M.Kind.EPI]
    assert epi and epi[0].subject.startswith("aca:action_result") and epi[0].src == M.Src.TOOL
    # correction ACA d'un fait exclusif ⇒ nouvelle version, l'ancienne remplacée, historique daté
    store.remember(aca.MemoryRecord.create(kind=aca.MemoryKind.USER_FACT, content="habite à Paris", confidence=0.8,
                                           status=aca.MemoryStatus.USER_ASSERTED, provenance="x"))
    store.remember(aca.MemoryRecord.create(kind=aca.MemoryKind.CORRECTION, content="habite à Lyon", confidence=0.9,
                                           status=aca.MemoryStatus.VALIDATED, provenance="x"))
    h = store.history("ville")
    assert [x["txt"] for x in h] == ["habite à Paris", "habite à Lyon"] and h[1]["ver"] == 2
    assert store.query("où j'habite ?").hits[0].record.content == "habite à Lyon"


def test_rejected_records_never_index_and_forget_sleep_available(tmp_path, aca_memory):
    aca, integ = aca_memory
    store = _store(tmp_path, integ)
    store.remember(aca.MemoryRecord.create(kind=aca.MemoryKind.USER_FACT, content="habite à Oslo", confidence=0.1,
                                           status=aca.MemoryStatus.REJECTED, provenance="doute"))
    assert store.count() == 0
    store.remember(aca.MemoryRecord.create(kind=aca.MemoryKind.USER_FACT, content="s'appelle Camille", confidence=0.8,
                                           status=aca.MemoryStatus.USER_ASSERTED, provenance="x"))
    assert store.forget("Camille").ids and store.count() == 0
    rep = store.sleep()
    assert rep.promoted and store.status()["journal_chain"].endswith("chaîne intacte")


def test_codex_memory_keeps_aca_store_and_mirrors_into_memoryaicm(tmp_path, aca_memory):
    aca, integ = aca_memory
    store = integ.codex_memory(tmp_path / "aca_memory.sqlite3", Settings(root=tmp_path / "d"), backend=StubBackend())
    assert isinstance(store, aca.SQLiteLongTermMemory), "sous-classe : les commandes ACA gardées par isinstance passent"
    rec = aca.MemoryRecord.create(kind=aca.MemoryKind.USER_FACT, content="habite à Lyon", confidence=0.8,
                                  status=aca.MemoryStatus.USER_ASSERTED, provenance="x")
    assert store.remember(rec) is rec
    assert store.count() == 1 and store.mem.count() == 1, "les deux magasins ont le souvenir"
    ctx = store.query("où est-ce que je vis ?")
    assert isinstance(ctx, aca.MemoryContext) and len(ctx.hits) == 1, "fusion sans doublon"
    assert ctx.hits[0].record.provenance.startswith("memoryaicm"), "memoryaicm d'abord (hybride), ACA ensuite"
    ctx2 = store.query("Lyon")
    assert ctx2.hits and ctx2.scanned_records >= 2
    assert store.history("ville")[0]["txt"] == "habite à Lyon"
    assert store.forget("Lyon").ids and store.mem.count() == 0 and store.count() == 1, "ACA garde son enregistrement ; memoryaicm désindexe"
    assert store.sleep().promoted
