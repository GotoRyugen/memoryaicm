"""PROFONDEUR : récupération hybride · vecteurs reconstructibles · historique · résumés de session ·
garde anti-valeur périmée · ressources/prompts MCP · hook Claude Code."""

import json
import subprocess
import sys

from memoryaicm import MemoryAgent, Settings
from memoryaicm import model as M
from memoryaicm.embed import HashingEmbedder, cosine
from memoryaicm.guard import Guard
from memoryaicm.llm.base import Context
from memoryaicm.llm.stub import StubBackend
from memoryaicm.mcp_server import MCPServer


# ----------------------------------------------------------------------------- récupération
def test_hybrid_retrieval_handles_synonyms_and_typos(agent):
    s = agent.start_session("s1")
    agent.turn("mon éditeur est neovim, j'habite à Lyon et j'utilise burpsuite", s)
    assert agent.index.retrieve("quel IDE j'utilise ?", k=1)[0][0].subject == "éditeur"
    assert agent.index.retrieve("où est-ce que je vis ?", k=1)[0][0].subject == "ville"
    assert agent.index.retrieve("neovm", k=1)[0][0].subject == "éditeur", "faute de frappe rattrapée par les n-grammes"
    assert agent.index.retrieve("quelle est la capitale de la Mongolie ?") == [], "jamais de dump"


def test_hashing_embedder_is_deterministic_and_content_based():
    e = HashingEmbedder()
    a, b = e.embed("habite à Lyon ville"), e.embed("où est-ce que je vis ?")
    assert e.embed("habite à Lyon ville") == a
    assert cosine(a, b) > 0.3
    assert cosine(a, e.embed("écris un poème sur la mer")) < 0.12
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6


def test_vectors_are_stored_and_rebuilt(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille et j'utilise nmap", s)
    n = agent.index.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    assert n == 2
    agent.index.rebuild()
    assert agent.index.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 2
    assert agent.index.retrieve("nmap", k=1)[0][0].subject == "outil:nmap"


# ----------------------------------------------------------------------------- temporel
def test_history_lists_dated_versions(agent):
    s = agent.start_session("s1")
    agent.turn("j'habite à Paris", s)
    agent.turn("j'habite à Lyon", s)
    h = agent.index.history("ville")
    assert [x["txt"] for x in h] == ["habite à Paris", "habite à Lyon"]
    assert h[0]["off_reason"] == "superseded" and h[1]["on"] and h[1]["ver"] == 2
    assert all(x["date"] for x in h)


def test_sleep_summarizes_sessions_into_episodic_memory(agent):
    s = agent.start_session("s1")
    agent.turn("je m'appelle Camille", s)
    agent.turn("on passe à Postgres pour le projet", s)
    rep = agent.sleep()
    assert len(rep.sessions) == 1
    epi = agent.index.fact(rep.sessions[0])
    assert epi.kind == M.Kind.EPI and epi.subject == "epi:session:s1" and "Camille" in epi.txt and epi.ttl > 0
    assert [f.id for f in agent.index.timeline(7)] == [epi.id]
    assert agent.index.retrieve("qu'est-ce qu'on a décidé pour postgres ?")
    # oublier un fait retire le résumé qui le cite (lignage), le journal garde tout
    fr = agent.forget("Camille", session=s)
    assert epi.id in fr.cascade and agent.index.fact(epi.id).on is False
    # un second sommeil ne résume pas deux fois la même session
    assert agent.sleep().sessions == []


# ----------------------------------------------------------------------------- garde temporelle
def _note(txt, prev):
    return M.Fact(id="f_ville", kind=M.Kind.SEM, txt=txt, subject="ville", src=M.Src.USER, t0=1, t_upd=2, ver=2, prev=prev)


def test_guard_blocks_stale_value():
    f = _note("habite à Lyon", ["habite à Paris"])
    ctx = Context(system="sys", notes=[(f, f.label())])
    r = Guard().validate("Tu habites à Paris, non ?", ctx)
    assert not r.ok and "valeur périmée" in r.reasons[0] and "Lyon" in r.reasons[0]
    assert Guard().validate("Tu habites à Lyon (avant, Paris).", ctx).ok
    assert Guard().validate("Tu habites à Lyon.", ctx).ok


# ----------------------------------------------------------------------------- MCP : ressources, prompt, outils
def test_mcp_resources_prompt_history_timeline(tmp_path):
    a = MemoryAgent(Settings(root=tmp_path / "d"), backend=StubBackend())
    srv = MCPServer(a, session="t")
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert set(init["result"]["capabilities"]) == {"tools", "resources", "prompts"}
    call = lambda n, **kw: srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": n, "arguments": kw}})["result"]["content"][0]["text"]
    call("memory_remember", user_text="j'habite à Paris", facts=[{"txt": "habite à Paris", "subject": "ville"}])
    call("memory_remember", user_text="j'habite à Lyon", facts=[{"txt": "habite à Lyon", "subject": "ville"}])
    h = call("memory_history", subject="ville")
    assert "habite à Paris" in h and "ACTIF — habite à Lyon" in h
    assert "aucune session" in call("memory_timeline", days=7)
    call("memory_sleep")
    assert "session t" in call("memory_timeline", days=7)
    res = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["result"]["resources"]
    assert {r["uri"] for r in res} == {"memory://profile", "memory://index", "memory://timeline", "memory://status"}
    prof = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "memory://profile"}})["result"]["contents"][0]
    assert prof["mimeType"] == "text/markdown" and "habite à Lyon" in prof["text"]
    assert srv.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "memory://nope"}})["error"]["code"] == -32002
    pr = srv.handle({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "memory_brief", "arguments": {"topic": "où j'habite"}}})
    text = pr["result"]["messages"][0]["content"]["text"]
    assert "habite à Lyon" in text and "Sessions récentes" in text and "Notes sur" in text
    assert srv.handle({"jsonrpc": "2.0", "id": 7, "method": "prompts/list"})["result"]["prompts"][0]["name"] == "memory_brief"
    a.close()


# ----------------------------------------------------------------------------- hook Claude Code
def test_claude_code_hook_remembers_then_recalls(tmp_path):
    home = str(tmp_path / "d")

    def hook(prompt):
        p = subprocess.run([sys.executable, "-m", "memoryaicm", "--home", home, "--backend", "stub", "hook", "prompt"],
                           input=json.dumps({"session_id": "abc", "prompt": prompt}).encode("utf-8"),
                           capture_output=True, timeout=60)
        assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
        return p.stdout.decode("utf-8")

    out = hook("je m'appelle Camille et je préfère les réponses courtes")
    assert "(mémoire : écrit" in out and "<memoryaicm>" in out
    out = hook("comment je m'appelle ?")
    assert "s'appelle Camille" in out and "réponses courtes" in out and "[note f_" in out
    assert hook("/help") == "" or "<memoryaicm>" in hook("/help")  # une commande slash n'est jamais mémorisée
