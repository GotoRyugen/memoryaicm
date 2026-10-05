"""Règles Echo-Core : approbation SHA-256 des modèles · backend OpenAI-compatible · niveaux d'autonomie 0–3."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from memoryaicm import MemoryAgent, Settings
from memoryaicm import model as M
from memoryaicm.agent import PolicyRefused
from memoryaicm.llm import get_backend
from memoryaicm.llm.openai_compat_backend import OpenAICompatBackend
from memoryaicm.llm.stub import StubBackend
from memoryaicm.mcp_server import MCPServer
from memoryaicm.models import ModelApprovals, sha256_file


# ----------------------------------------------------------------------------- approbation des modèles
def test_model_must_be_approved_by_sha256(tmp_path):
    s = Settings(root=tmp_path / "d")
    a = MemoryAgent(s, backend=StubBackend())
    gguf = tmp_path / "modele.gguf"
    gguf.write_bytes(b"GGUF" + b"\x00" * 1024)
    ok, reason = a.approvals.check(gguf)
    assert not ok and "approved_sha256 est vide" in reason
    entry = a.approvals.approve(gguf)
    assert entry["sha256"] == sha256_file(gguf) and len(entry["sha256"]) == 64
    ok, reason = a.approvals.check(gguf)
    assert ok and "approuvé" in reason
    gguf.write_bytes(b"GGUF" + b"\x01" * 1024)  # le fichier change ⇒ refus
    ok, reason = a.approvals.check(gguf)
    assert not ok and "différente" in reason
    assert a.approvals.revoke(gguf) and not a.approvals.check(gguf)[0]
    checks = [e for e in a.journal.replay(types=(M.EV_MODEL_CHECK,))]
    assert len(checks) >= 5 and checks[0].payload["ok"] is False and checks[1].payload["action"] == "approve"
    assert a.status()["models_approved"] == 0
    a.close()


def test_missing_model_file_is_refused(tmp_path):
    ap = ModelApprovals(Settings(root=tmp_path / "d"))
    ok, reason = ap.check(tmp_path / "absent.gguf")
    assert not ok and "introuvable" in reason
    with pytest.raises(FileNotFoundError):
        ap.approve(tmp_path / "absent.gguf")


# ----------------------------------------------------------------------------- backend OpenAI-compatible
class _FakeLLM(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        _FakeLLM.seen.append((self.path, self.headers.get("Authorization"), body))
        user = body["messages"][-1]["content"]
        if "tableau JSON" in body["messages"][0]["content"]:
            reply = '[{"txt": "utilise nmap", "subject": "outil:nmap", "kind": "SEM", "src": "USER", "exclusive": false}]'
        else:
            reply = "Réponse du modèle local : " + user[-40:]
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out)))
        self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_llm():
    srv = HTTPServer(("127.0.0.1", 0), _FakeLLM)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    _FakeLLM.seen.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown(); srv.server_close()


def test_openai_compat_backend_talks_v1_chat_completions(tmp_path, fake_llm):
    s = Settings(root=tmp_path / "d", backend="llamacpp", openai_base_url=fake_llm, openai_api_key="k-test", openai_model="qwen")
    be = get_backend(s)
    assert isinstance(be, OpenAICompatBackend) and be.name == "llamacpp"
    cands = be.extract("j'utilise nmap")
    assert cands and cands[0].subject == "outil:nmap"
    a = MemoryAgent(s, backend=be)
    sid = a.start_session("s1")
    res = a.turn("écris une phrase", sid)
    assert res.answer.startswith("Réponse du modèle local")
    assert all(auth == "Bearer k-test" and path == "/v1/chat/completions" for path, auth, _ in _FakeLLM.seen)
    a.close()


def test_openai_compat_backend_refuses_unapproved_model(tmp_path, fake_llm):
    gguf = tmp_path / "m.gguf"; gguf.write_bytes(b"x" * 100)
    s = Settings(root=tmp_path / "d", backend="llamacpp", openai_base_url=fake_llm, model_path=str(gguf))
    with pytest.raises(RuntimeError, match="modèle refusé"):
        get_backend(s)
    ModelApprovals(s).approve(gguf)
    assert get_backend(s).name == "llamacpp"
    s2 = Settings(root=tmp_path / "d", backend="llamacpp", openai_base_url=fake_llm, model_path=str(gguf), require_model_approval=False)
    ModelApprovals(s2).revoke(gguf)
    assert get_backend(s2).name == "llamacpp", "l'exigence d'approbation est un réglage explicite"


def test_backend_auto_prefers_configured_openai_endpoint(tmp_path, fake_llm, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(root=tmp_path / "d", backend="auto", openai_base_url=fake_llm)
    assert get_backend(s).name == "llamacpp"


# ----------------------------------------------------------------------------- niveaux d'autonomie
def test_level0_is_read_only(tmp_path):
    a = MemoryAgent(Settings(root=tmp_path / "d", autonomy=0), backend=StubBackend())
    sid = a.start_session("s1")
    res = a.turn("je m'appelle Camille", sid)
    assert not res.write.written and res.write.skipped[0][1].startswith("autonomie 0")
    assert a.index.all_facts() == []
    assert a.turn("oublie Camille", sid).level == "refused"
    assert a.turn("sommeil", sid).level == "refused"
    with pytest.raises(PolicyRefused):
        a.forget("x")
    types = [e.type for e in a.journal.replay()]
    assert types.count(M.EV_POLICY_REFUSE) >= 3
    assert a.status()["autonomy"]["level"] == 0
    a.close()


def test_level1_queues_every_fact_for_review(tmp_path):
    a = MemoryAgent(Settings(root=tmp_path / "d", autonomy=1, auto_sleep_every_turns=1), backend=StubBackend())
    sid = a.start_session("s1")
    res = a.turn("je m'appelle Camille", sid)
    assert res.write.queued and a.index.all_facts() == [] and len(a.index.pending_reviews()) == 1
    assert res.sleep is None, "pas de routine automatique en dessous du niveau 3"
    a.review(res.write.queued[0].id, approve=True)
    assert [f.subject for f in a.index.all_facts()] == ["nom"]
    assert a.sleep().promoted, "le sommeil manuel reste possible dès le niveau 1"
    a.close()


def test_level2_confirms_sensitive_only_and_level3_runs_routines(tmp_path):
    a2 = MemoryAgent(Settings(root=tmp_path / "d2", autonomy=2, auto_sleep_every_turns=1), backend=StubBackend())
    sid = a2.start_session("s1")
    res = a2.turn("je m'appelle Camille", sid)
    assert res.write.written and res.sleep is None
    assert a2.turn("je travaille chez Acme depuis ma maladie", sid).write.queued
    a2.close()
    a3 = MemoryAgent(Settings(root=tmp_path / "d3", autonomy=3, auto_sleep_every_turns=1), backend=StubBackend())
    sid = a3.start_session("s1")
    assert a3.turn("je m'appelle Camille", sid).sleep is not None
    a3.close()


def test_mcp_reports_refusals_as_tool_errors(tmp_path):
    a = MemoryAgent(Settings(root=tmp_path / "d", autonomy=0), backend=StubBackend())
    srv = MCPServer(a, session="t")
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_forget", "arguments": {"query": "x"}}})
    assert r["result"]["isError"] and "niveau d'autonomie 0" in r["result"]["content"][0]["text"]
    r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_remember", "arguments": {"user_text": "je m'appelle Camille"}}})
    assert "autonomie 0" in r["result"]["content"][0]["text"]
    r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_recall", "arguments": {"query": "nom"}}})
    assert not r["result"]["isError"], "la lecture reste possible au niveau 0"
    a.close()
