"""Mode serveur : la mémoire devient un service local que d'autres programmes (ou agents) utilisent.

API JSON, stdlib uniquement, liée à 127.0.0.1 par défaut :

  POST /turn     {"text": "...", "session": "s1"?}          → réponse, niveau, faits utilisés, mémoire
  POST /remember {"user_text", "facts"?, "session"?}        → mode add-on : la mémoire retient, le client génère
  POST /recall   {"query", "k"?}                             → notes étiquetées + préférences
  POST /ingest   {"session", "source", "content"}           → {"quarantined": bool}
  POST /forget   {"query", "session"?}                       → rapport d'oubli
  POST /sleep    {}                                          → rapport de sommeil
  POST /review   {"fact_id", "approve": bool}                → {"ok": true}
  GET  /status                                               → état complet
  GET  /index?all=1                                          → faits (actifs, ou tous)
  GET  /search?q=mots                                        → journal plein texte
  GET  /health                                               → {"ok": true}

Autonomie : un fil de fond dort (consolide) après `serve_idle_sleep_s` sans activité, s'il y a du nouveau.
Tous les accès à l'agent sont sérialisés par un verrou (SQLite, un seul écrivain).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import model as M
from .agent import MemoryAgent, PolicyRefused


class MemoryService:
    def __init__(self, agent: MemoryAgent):
        self.agent = agent
        self.lock = threading.RLock()
        self.last_activity = time.time()
        self._stop = threading.Event()
        self._idle_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ opérations (verrouillées)
    def turn(self, text: str, session: str | None) -> dict:
        with self.lock:
            self.last_activity = time.time()
            sid = session or self.agent.start_session()
            r = self.agent.turn(text, sid)
            return {
                "session": sid, "answer": r.answer, "level": r.level, "agreement": r.agreement,
                "used": [f.id for f in r.used], "memory": r.write.summary(),
                "guard": {"ok": r.guard.ok, "reasons": r.guard.reasons, "fixes": r.guard.fixes} if r.guard else None,
                "forgotten": r.forgotten, "auto_sleep": r.sleep.summary() if r.sleep else None,
            }

    def remember(self, user_text: str, session: str, facts: list[dict] | None = None):
        """Mode add-on : le client génère, la mémoire retient (politique d'écriture, ancrage, sommeil auto)."""
        with self.lock:
            self.last_activity = time.time()
            return self.agent.remember(user_text, session, facts)

    def recall(self, query: str, k: int | None = None):
        with self.lock:
            self.last_activity = time.time()
            return self.agent.recall_notes(query, k=k)

    def ingest(self, session: str, source: str, content: str) -> dict:
        with self.lock:
            self.last_activity = time.time()
            return {"quarantined": self.agent.ingest_external(session, source, content)}

    def forget(self, query: str, session: str) -> dict:
        with self.lock:
            self.last_activity = time.time()
            fr = self.agent.forget(query, session=session)
            return {"ids": fr.ids, "cascade": fr.cascade, "redacted_turns": fr.redacted_turns,
                    "adapter_version": fr.adapter_version, "summary": fr.summary()}

    def sleep(self, reason: str = "api") -> dict:
        with self.lock:
            rep = self.agent.sleep(reason=reason)
            return {"summary": rep.summary(), "version": rep.version, "promoted": rep.promoted, "tests": rep.tests}

    def review(self, fact_id: str, approve: bool) -> dict:
        with self.lock:
            self.agent.review(fact_id, approve)
            return {"ok": True}

    def status(self) -> dict:
        with self.lock:
            return self.agent.status()

    def index(self, show_all: bool) -> list[dict]:
        with self.lock:
            now = time.time()
            return [dict(f.to_dict(), activation=self.agent.index.activation(f.id, now))
                    for f in self.agent.index.all_facts(on_only=not show_all)]

    def search(self, q: str) -> list[dict]:
        with self.lock:
            return [{"seq": e.seq, "ts": e.ts, "actor": e.actor, "type": e.type, "payload": e.payload}
                    for e in self.agent.journal.search(q)]

    def read_event(self, seq: int | None = None, event_id: str | None = None) -> dict | None:
        """Un événement, ENTIER. Le journal garde tout ; encore faut-il pouvoir le relire sans coupe."""
        with self.lock:
            ev = None
            if event_id:
                ev = self.agent.journal.get(str(event_id))
            elif seq is not None:
                for e in self.agent.journal.replay(since_seq=max(0, int(seq) - 1)):
                    if e.seq == int(seq):
                        ev = e
                        break
            if ev is None:
                return None
            return {"seq": ev.seq, "id": ev.id, "ts": ev.ts, "actor": ev.actor, "type": ev.type, "payload": ev.payload}

    # ------------------------------------------------------------------ la conversation elle-même
    def transcript_append(self, session: str, user_text: str, assistant_text: str) -> int:
        """Archive un échange mot pour mot dans le journal chaîné. Aucun plafond : c'est le point.

        Ce n'est PAS `remember` : rien n'est extrait, rien n'est jugé durable, rien n'est résumé.
        C'est la trace de ce qui a été dit, pour qu'on puisse y revenir dans dix ans.
        """
        n = 0
        with self.lock:
            if user_text:
                self.agent.journal.append(M.EV_TURN_USER, M.Actor.USER.value,
                                          {"session": session, "text": user_text, "verbatim": True})
                n += 1
            if assistant_text:
                self.agent.journal.append(M.EV_TURN_ASSISTANT, M.Actor.ASSISTANT.value,
                                          {"session": session, "text": assistant_text, "verbatim": True})
                n += 1
            if n:
                self.agent.index.sync()
        return n

    def transcript_read(self, session: str, n: int = 20) -> list[dict]:
        with self.lock:
            tours = [{"ts": e.ts, "role": "user" if e.type == M.EV_TURN_USER else "assistant",
                      "text": e.payload.get("text", ""), "seq": e.seq}
                     for e in self.agent.journal.replay(types=(M.EV_TURN_USER, M.EV_TURN_ASSISTANT))
                     if e.payload.get("session") == session]
        return tours[-int(n):] if n else tours

    # ------------------------------------------------------------------ sommeil sur inactivité
    def idle_tick(self, now: float | None = None) -> str | None:
        """Une itération du fil de fond ; retourne le motif si un sommeil a eu lieu."""
        now = time.time() if now is None else now
        idle = now - self.last_activity
        if idle < self.agent.s.serve_idle_sleep_s:
            return None
        with self.lock:
            last = self.agent.journal.last_of(M.EV_SLEEP)
            since = last.seq if last else 0
            fresh = self.agent.journal.count_types_since(
                (M.EV_FACT_WRITE, M.EV_FACT_MERGE, M.EV_FACT_FORGET, M.EV_REVIEW_DECIDE), since)
            if fresh == 0:
                self.last_activity = now  # rien de nouveau : on repart pour un cycle
                return None
            self.agent.sleep(reason="auto:idle")
            self.last_activity = now
            return "auto:idle"

    def start_idle_thread(self, period_s: float = 30.0) -> None:
        def loop():
            while not self._stop.wait(period_s):
                try:
                    self.idle_tick()
                except Exception as e:  # le service ne meurt pas d'un sommeil raté ; l'erreur est journalisée
                    with self.lock:
                        self.agent.journal.append(M.EV_SELFCHECK, M.Actor.SYSTEM.value,
                                                  {"idle_sleep_error": repr(e)[:300]})
        self._idle_thread = threading.Thread(target=loop, name="memoryaicm-idle", daemon=True)
        self._idle_thread.start()

    def stop(self) -> None:
        self._stop.set()


class _Handler(BaseHTTPRequestHandler):
    service: MemoryService  # injecté par make_server

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            return {"_invalid": True}

    def log_message(self, fmt, *args):  # silencieux : le journal du système suffit
        pass

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == "/health":
                self._send(200, {"ok": True})
            elif u.path == "/status":
                self._send(200, self.service.status())
            elif u.path == "/index":
                self._send(200, self.service.index(show_all=qs.get("all", ["0"])[0] in ("1", "true")))
            elif u.path == "/search":
                self._send(200, self.service.search(qs.get("q", [""])[0]))
            else:
                self._send(404, {"error": "route inconnue"})
        except Exception as e:
            self._send(500, {"error": repr(e)[:300]})

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        if b.get("_invalid"):
            return self._send(400, {"error": "JSON invalide"})
        try:
            if u.path == "/turn":
                if not b.get("text"):
                    return self._send(400, {"error": "champ text requis"})
                self._send(200, self.service.turn(b["text"], b.get("session")))
            elif u.path == "/remember":
                if not b.get("user_text"):
                    return self._send(400, {"error": "champ user_text requis"})
                rep = self.service.remember(b["user_text"], b.get("session") or "http", b.get("facts"))
                self._send(200, {"memory": rep.summary(), "written": [f.to_dict() for f in rep.written],
                                 "merged": [n.to_dict() for n, _ in rep.merged], "forgotten": rep.forgotten,
                                 "skipped": [[c.txt, r] for c, r in rep.skipped]})
            elif u.path == "/recall":
                if not b.get("query"):
                    return self._send(400, {"error": "champ query requis"})
                notes, prefs = self.service.recall(b["query"], b.get("k"))
                self._send(200, {"notes": [dict(f.to_dict(), label=f.label()) for f in notes],
                                 "prefs": [dict(f.to_dict(), label=f.label()) for f in prefs]})
            elif u.path == "/ingest":
                for k in ("session", "source", "content"):
                    if not b.get(k):
                        return self._send(400, {"error": f"champ {k} requis"})
                self._send(200, self.service.ingest(b["session"], b["source"], b["content"]))
            elif u.path == "/forget":
                if not b.get("query"):
                    return self._send(400, {"error": "champ query requis"})
                self._send(200, self.service.forget(b["query"], b.get("session", "")))
            elif u.path == "/sleep":
                self._send(200, self.service.sleep())
            elif u.path == "/review":
                if not b.get("fact_id"):
                    return self._send(400, {"error": "champ fact_id requis"})
                self._send(200, self.service.review(b["fact_id"], bool(b.get("approve"))))
            else:
                self._send(404, {"error": "route inconnue"})
        except PolicyRefused as e:
            self._send(403, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": repr(e)[:300]})


def make_server(agent: MemoryAgent, host: str | None = None, port: int | None = None) -> tuple[ThreadingHTTPServer, MemoryService]:
    service = MemoryService(agent)
    handler = type("Handler", (_Handler,), {"service": service})
    srv = ThreadingHTTPServer((host or agent.s.serve_host, agent.s.serve_port if port is None else port), handler)
    return srv, service


def serve(agent: MemoryAgent, host: str | None = None, port: int | None = None) -> None:
    srv, service = make_server(agent, host, port)
    service.start_idle_thread()
    h, p = srv.server_address[0], srv.server_address[1]
    print(f"memoryaicm serve · http://{h}:{p} · backend {agent.backend.name} · "
          f"sommeil auto après {int(agent.s.serve_idle_sleep_s)} s d'inactivité · Ctrl+C pour arrêter", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        srv.server_close()
