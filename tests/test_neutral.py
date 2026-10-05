"""NEUTRALITÉ LLM : mêmes outils pour tout fournisseur · boucles d'outils (OpenAI, Anthropic, texte) · backends
callable / HTTP · Claude sans SDK · CLI tools/protocol."""

import io
import json
import sys
import threading
import types
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from memoryaicm import MemoryAgent, Settings
from memoryaicm import model as M
from memoryaicm import tools
from memoryaicm.cli import main as cli_main
from memoryaicm.llm import get_backend
from memoryaicm.llm.callable_backend import CallableBackend, HTTPCallableBackend, backend_from_spec
from memoryaicm.llm.stub import StubBackend
from memoryaicm.mcp_server import MCPServer, TOOLS
from memoryaicm.toolloop import AnthropicToolLoop, OpenAIToolLoop, TextToolLoop, make_loop


def _agent(tmp_path, name="d"):
    return MemoryAgent(Settings(root=tmp_path / name), backend=StubBackend())


# ----------------------------------------------------------------------------- schémas : une source, tous les formats
def test_schemas_in_every_format_share_names_and_shapes():
    names = set(tools.TOOL_NAMES)
    assert len(names) == len(tools.TOOL_SPECS) and TOOLS is tools.TOOL_SPECS
    assert {t["name"] for t in tools.as_mcp()} == names and all("inputSchema" in t for t in tools.as_mcp())
    oa = tools.as_openai()
    assert {t["function"]["name"] for t in oa} == names and all(t["type"] == "function" and "parameters" in t["function"] for t in oa)
    assert tools.as_ollama() == oa
    an = tools.as_anthropic()
    assert {t["name"] for t in an} == names and all("input_schema" in t and "inputSchema" not in t for t in an)
    ge = tools.as_gemini()
    decls = ge[0]["function_declarations"]
    assert {d["name"] for d in decls} == names
    assert "minimum" not in json.dumps(decls) and "maximum" not in json.dumps(decls)
    md = tools.as_markdown()
    assert all(n in md for n in names) and "memory_recall**(query: string, k?: integer)" in md
    # les copies sont indépendantes : modifier un format ne touche pas la source
    oa[0]["function"]["parameters"]["properties"]["query"]["type"] = "zzz"
    assert tools.TOOL_SPECS[0]["inputSchema"]["properties"]["query"]["type"] == "string"
    with pytest.raises(ValueError):
        tools.schemas("cobol")


def test_protocol_texts():
    p = tools.system_prompt()
    assert all(n in p for n in ("memory_recall", "memory_remember", "memory_ingest_external", "memory_forget", "memory_timeline", "memory_history"))
    assert "DONNÉE" in p and "conversation en cours > notes" in p
    t = tools.system_prompt(text_protocol=True)
    assert "```memory" in t and "memory_reactivate" in t and t.startswith(p)


# ----------------------------------------------------------------------------- routeur = même rendu que MCP
def test_router_and_mcp_render_the_same(tmp_path):
    a = _agent(tmp_path)
    srv = MCPServer(a, session="s")
    router = tools.ToolRouter(a, session="s")
    facts = [{"txt": "s'appelle Camille", "subject": "nom"}, {"txt": "préfère les réponses courtes", "subject": "pref:reponses-courtes", "kind": "PROC"}]
    r1 = router.call("memory_remember", {"user_text": "je m'appelle Camille et je préfère les réponses courtes", "facts": facts})
    assert "écrit : s'appelle Camille ; préfère" in r1
    via_mcp = srv.call("memory_recall", {"query": "comment je m'appelle ?"})
    via_router = router.call("memory_recall", {"query": "comment je m'appelle ?"})
    assert via_mcp == via_router and "s'appelle Camille" in via_router and "PRÉFÉRENCES" in via_router
    assert router.resource("memory://status")[0] == "application/json" and srv.resource("memory://index")[1] == router.resource("memory://index")[1]
    assert router.brief("nom").startswith("Contexte mémoire") and "s'appelle Camille" in router.brief("nom")
    # call_safe : jamais d'exception
    txt, err = router.call_safe("memory_nope", {})
    assert err and "inconnu" in txt
    txt, err = router.call_safe("memory_recall", {})
    assert err and "query" in txt
    txt, err = router.call_safe("memory_sleep", {})
    assert not err and "tests" in txt
    assert a.journal.last_of(M.EV_SLEEP).payload["reason"] == "tool"
    a.close()


def test_router_autonomy_refusal_is_reported_not_raised(tmp_path):
    a = MemoryAgent(Settings(root=tmp_path / "d", autonomy=0), backend=StubBackend())
    router = tools.ToolRouter(a, session="s")
    txt, err = router.call_safe("memory_forget", {"query": "x"})
    assert err and "autonomie" in txt.lower()
    a.close()


# ----------------------------------------------------------------------------- protocole texte
def test_text_protocol_parsing():
    reply = ('Je regarde.\n```memory\n{"name": "memory_recall", "arguments": {"query": "prénom"}}\n```\n'
             'et\n```memory\n{"name": "memory_hack", "arguments": {}}\n```\n```memory\npas du json\n```\nfin')
    calls = tools.extract_text_calls(reply)
    assert calls == [("memory_recall", {"query": "prénom"})]
    assert tools.strip_text_calls(reply) == "Je regarde.\n\net\n\n\nfin".strip() or "memory" not in tools.strip_text_calls(reply)


def test_text_tool_loop_with_scripted_chat(tmp_path):
    a = _agent(tmp_path)
    script = iter([
        '```memory\n{"name": "memory_remember", "arguments": {"user_text": "je m\'appelle Camille", '
        '"facts": [{"txt": "s\'appelle Camille", "subject": "nom"}]}}\n```',
        "Enchanté, Camille.",
        '```memory\n{"name": "memory_recall", "arguments": {"query": "prénom"}}\n```',
        "Tu t'appelles Camille [note].",
    ])
    seen = []

    def chat(system, messages):
        seen.append((system, list(messages)))
        return next(script)

    loop = TextToolLoop(a, chat, session="s")
    assert "```memory" in loop.system and "memory_remember" in loop.system
    ans = loop.ask("je m'appelle Camille")
    assert ans == "Enchanté, Camille." and loop.calls[0][0] == "memory_remember" and "écrit : s'appelle Camille" in loop.calls[0][2]
    assert seen[1][1][-1]["content"].startswith("RÉSULTATS MÉMOIRE")
    ans = loop.ask("comment je m'appelle ?")
    assert "Camille" in ans and loop.calls[0][0] == "memory_recall" and "s'appelle Camille" in loop.calls[0][2]
    assert a.index.all_facts()[0].txt == "s'appelle Camille"
    loop.close()


# ----------------------------------------------------------------------------- boucles HTTP (fournisseurs simulés)
class _Provider(BaseHTTPRequestHandler):
    """Simule OpenAI (/v1/chat/completions) et Anthropic (/v1/messages) : 1er appel → outil, 2e → réponse finale."""
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        _Provider.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path.endswith("/chat/completions"):
            msgs = body["messages"]
            if msgs[-1]["role"] == "tool":
                out = {"choices": [{"message": {"role": "assistant", "content": "D'après la mémoire : " + msgs[-1]["content"][:60]}}]}
            else:
                out = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "memory_recall", "arguments": json.dumps({"query": "prénom"})}}]}}]}
        else:  # anthropic
            msgs = body["messages"]
            if isinstance(msgs[-1]["content"], list) and msgs[-1]["content"][0].get("type") == "tool_result":
                out = {"content": [{"type": "text", "text": "Réponse Claude : " + msgs[-1]["content"][0]["content"][:60]}], "stop_reason": "end_turn"}
            else:
                out = {"content": [{"type": "text", "text": "je regarde"},
                                   {"type": "tool_use", "id": "tu_1", "name": "memory_remember",
                                    "input": {"user_text": msgs[-1]["content"], "facts": [{"txt": "utilise nmap", "subject": "outil:nmap", "exclusive": False}]}}],
                       "stop_reason": "tool_use"}
        raw = json.dumps(out).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def provider():
    srv = HTTPServer(("127.0.0.1", 0), _Provider)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    _Provider.seen.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown(); srv.server_close()


def test_openai_tool_loop_round_trip(tmp_path, provider):
    a = _agent(tmp_path)
    a.remember("je m'appelle Camille", "s0", [{"txt": "s'appelle Camille", "subject": "nom"}])
    loop = OpenAIToolLoop(a, base_url=provider + "/v1", model="qwen", api_key="k", session="s")
    ans = loop.ask("comment je m'appelle ?")
    assert ans.startswith("D'après la mémoire") and "NOTES" in ans
    assert loop.calls[0][0] == "memory_recall" and "s'appelle Camille" in loop.calls[0][2]
    path, headers, body = _Provider.seen[0]
    assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer k"
    assert body["messages"][0]["role"] == "system" and "memory_recall" in body["messages"][0]["content"]
    assert body["tools"] == tools.as_openai() and body["tool_choice"] == "auto"
    assert [m["role"] for m in loop.history] == ["user", "assistant", "tool", "assistant"]
    assert loop.history[2]["tool_call_id"] == "call_1"
    loop.close()


def test_anthropic_tool_loop_round_trip(tmp_path, provider, monkeypatch):
    a = _agent(tmp_path)
    loop = AnthropicToolLoop(a, model="claude-x", api_key="sk-test", base_url=provider, session="s")
    ans = loop.ask("j'utilise nmap pour mes audits")
    assert ans.startswith("Réponse Claude") and "écrit : utilise nmap" in ans
    assert loop.calls[0][0] == "memory_remember" and a.index.all_facts()[0].txt == "utilise nmap"
    path, headers, body = _Provider.seen[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "sk-test" and headers["anthropic-version"] == "2023-06-01"
    assert body["tools"] == tools.as_anthropic() and "memory_remember" in body["system"]
    assert loop.history[2]["content"][0]["type"] == "tool_result" and loop.history[2]["content"][0]["is_error"] is False
    with pytest.raises(RuntimeError):
        AnthropicToolLoop(a, api_key="", session="s2")
    loop.close()


def test_make_loop_aliases(tmp_path, provider):
    a = _agent(tmp_path)
    assert isinstance(make_loop(a, "ollama", base_url=provider, session="s"), OpenAIToolLoop)
    assert isinstance(make_loop(a, "text", chat=lambda s, m: "ok", session="s"), TextToolLoop)
    with pytest.raises(ValueError):
        make_loop(a, "cobol")
    a.close()


# ----------------------------------------------------------------------------- backends neutres
def test_callable_backend_json_and_fallback_rules():
    calls = []

    def good(system, messages):
        calls.append(system[:20])
        return 'voici : [{"txt": "utilise vim", "subject": "éditeur", "kind": "SEM"}]'

    b = CallableBackend(good)
    cands = b.extract("j'utilise vim")
    assert b.name == "callable" and [c.txt for c in cands] == ["utilise vim"] and calls
    # sortie inexploitable ⇒ règles locales
    b2 = CallableBackend(lambda s, m: "je ne sais pas produire du JSON")
    assert [c.txt for c in b2.extract("je m'appelle Camille")] == ["s'appelle Camille"]
    b3 = CallableBackend(lambda s, m: (_ for _ in ()).throw(RuntimeError("panne")))
    assert [c.txt for c in b3.extract("je m'appelle Camille")] == ["s'appelle Camille"]
    assert CallableBackend(lambda s, m: "rien", fallback_rules=False).extract("je m'appelle Camille") == []
    # abstraction : ligne du modèle, sinon règles
    from memoryaicm.model import Fact, Kind, Src
    facts = [Fact(id=f"f_{t}", kind=Kind.SEM, txt=f"utilise {t}", subject=f"outil:{t}", src=Src.USER, t0=0.0, t_upd=0.0) for t in ("a", "b", "c")]
    assert CallableBackend(lambda s, m: "outils : a, b, c\nignoré").abstract(facts) == "outils : a, b, c"
    assert CallableBackend(lambda s, m: "").abstract(facts) == StubBackend().abstract(facts)


def test_backend_from_spec_module_function_and_agent_integration(tmp_path, monkeypatch):
    mod = types.ModuleType("mon_llm")
    mod.chat = lambda system, messages: '[{"txt": "habite à Lyon", "subject": "ville"}]'
    monkeypatch.setitem(sys.modules, "mon_llm", mod)
    b = backend_from_spec("mon_llm:chat")
    assert isinstance(b, CallableBackend) and [c.txt for c in b.extract("j'habite à Lyon")] == ["habite à Lyon"]
    with pytest.raises(RuntimeError):
        backend_from_spec("sans-deux-points")
    assert isinstance(backend_from_spec("http://127.0.0.1:1/x"), HTTPCallableBackend)
    # par les réglages : MEMORYAICM_BACKEND=callable:mon_llm:chat
    a = MemoryAgent(Settings(root=tmp_path / "d", backend="callable:mon_llm:chat"))
    assert a.backend.name == "callable"
    r = a.turn("j'habite à Lyon", a.start_session("s"))
    assert r.write.written and a.index.all_facts()[0].txt == "habite à Lyon"
    a.close()


def test_http_callable_backend(tmp_path):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            assert body["system"] and body["messages"][-1]["role"] == "user"
            raw = json.dumps({"text": '[{"txt": "préfère le thé", "subject": "pref:the", "kind": "PROC"}]'}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = HTTPCallableBackend(f"http://127.0.0.1:{srv.server_address[1]}/complete", api_key="k")
        assert b.name == "http" and [c.txt for c in b.extract("je préfère le thé")] == ["préfère le thé"]
    finally:
        srv.shutdown(); srv.server_close()


def test_claude_without_sdk_uses_direct_http(monkeypatch):
    import urllib.request
    monkeypatch.setitem(sys.modules, "anthropic", None)   # SDK absent
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    b = get_backend(Settings(backend="anthropic", model="claude-x"))
    assert type(b).__name__ == "AnthropicHTTPBackend" and b.name == "anthropic"
    seen = {}

    class R:
        def __init__(self, data):
            self._d = data

        def read(self):
            return json.dumps(self._d).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data.decode())
        return R({"content": [{"type": "text", "text": '[{"txt": "code en Rust", "subject": "outil:rust", "exclusive": false}]'}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert [c.txt for c in b.extract("je code en Rust")] == ["code en Rust"]
    assert seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["x-api-key"] == "sk-ant-test"
    assert seen["body"]["model"] == "claude-x" and seen["body"]["messages"][0]["role"] == "user"
    # auto sans clé ni SDK : stub, jamais d'erreur
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert get_backend(Settings(backend="auto", openai_base_url="", ollama_url="http://127.0.0.1:1")).name == "stub"
    with pytest.raises(RuntimeError):
        get_backend(Settings(backend="anthropic"))


def test_generic_openai_backend_defaults_to_api_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    b = get_backend(Settings(backend="openai", openai_base_url=""))
    assert b.name == "llamacpp" and b.base_url == "https://api.openai.com" and b.api_key == "sk-oa" and b.model == "gpt-x"
    b = get_backend(Settings(backend="openai", openai_base_url="http://127.0.0.1:1234", openai_model="local"))
    assert b.base_url == "http://127.0.0.1:1234"


# ----------------------------------------------------------------------------- CLI
def test_cli_tools_and_protocol(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        cli_main(["--home", str(tmp_path / "d"), "tools", "--format", "anthropic"])
    data = json.loads(out.getvalue())
    assert data == tools.as_anthropic()
    out = io.StringIO()
    with redirect_stdout(out):
        cli_main(["--home", str(tmp_path / "d"), "tools", "--format", "markdown"])
    assert out.getvalue().startswith("- **memory_recall**")
    out = io.StringIO()
    with redirect_stdout(out):
        cli_main(["--home", str(tmp_path / "d"), "protocol", "--text"])
    assert "```memory" in out.getvalue()


def test_cli_agent_text_provider_with_callable_backend(tmp_path, monkeypatch):
    mod = types.ModuleType("mon_chat")
    replies = iter(['```memory\n{"name": "memory_status", "arguments": {}}\n```', "État consulté."])
    mod.chat = lambda system, messages: next(replies)
    monkeypatch.setitem(sys.modules, "mon_chat", mod)
    out = io.StringIO()
    with redirect_stdout(out):
        cli_main(["--home", str(tmp_path / "d"), "--backend", "callable:mon_chat:chat", "agent", "--provider", "text", "--say", "où en est la mémoire ?"])
    text = out.getvalue()
    assert "[outil memory_status" in text and "assistant> État consulté." in text


def test_auto_backend_never_picks_a_missing_ollama_model(monkeypatch):
    """Ollama répond mais le modèle configuré n'existe pas ⇒ premier modèle servi ; aucun modèle ⇒ pas d'Ollama."""
    from memoryaicm import llm as L
    monkeypatch.setattr(L, "ollama_models", lambda url, timeout=0.4: ["gemma3:latest", "phi3:latest"])
    b = get_backend(Settings(backend="auto", ollama_model="llama3.1", openai_base_url=""))
    assert b.name == "ollama" and b.model == "gemma3:latest"
    b = get_backend(Settings(backend="auto", ollama_model="phi3", openai_base_url=""))
    assert b.model == "phi3:latest"
    monkeypatch.setattr(L, "ollama_models", lambda url, timeout=0.4: [])
    assert get_backend(Settings(backend="auto", openai_base_url="")).name == "stub"
    monkeypatch.setattr(L, "ollama_models", lambda url, timeout=0.4: None)
    assert get_backend(Settings(backend="auto", openai_base_url="")).name == "stub"
    # explicite : le modèle configuré est respecté même absent (l'erreur sera nette)
    assert get_backend(Settings(backend="ollama", ollama_model="llama3.1")).model == "llama3.1"


def test_extraction_backend_failure_falls_back_to_rules(tmp_path):
    """Une panne du backend (404, réseau) ne casse jamais un tour : règles locales + panne journalisée."""
    class Broken:
        name = "broken"

        def extract(self, text):
            raise RuntimeError("HTTP Error 404: Not Found")

        def complete(self, ctx, temperature=0.0):
            return "ok"

        def abstract(self, facts):
            return None

        def sample(self, ctx, n=3):
            return ["ok"] * n
    a = MemoryAgent(Settings(root=tmp_path / "d"), backend=Broken())
    r = a.turn("je m'appelle Camille", a.start_session("s"))
    assert [f.txt for f in r.write.written] == ["s'appelle Camille"]
    errs = [e for e in a.journal.replay(types=(M.EV_SELFCHECK,)) if "backend_error" in e.payload]
    assert errs and "404" in errs[0].payload["backend_error"] and errs[0].payload["fallback"] == "stub"
    rep = a.remember("j'habite à Lyon", "s")
    assert [f.txt for f in rep.written] == ["habite à Lyon"]
    a.close()
