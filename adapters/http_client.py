"""Depuis n'importe quel langage : le service HTTP local (`python -m memoryaicm serve`).

    curl -s -X POST localhost:8765/remember -H 'Content-Type: application/json' \
         -d '{"user_text":"je m\'appelle Camille","facts":[{"txt":"s\'appelle Camille","subject":"nom"}],"session":"s1"}'
    curl -s -X POST localhost:8765/recall -d '{"query":"prénom"}'
    curl -s localhost:8765/status

Client Python minimal (stdlib) :
"""

from __future__ import annotations

import json
import urllib.request


class MemoryClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", session: str = "client"):
        self.base_url, self.session = base_url.rstrip("/"), session

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path: str) -> dict | list:
        with urllib.request.urlopen(self.base_url + path, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def remember(self, user_text: str, facts: list[dict] | None = None) -> dict:
        return self._post("/remember", {"user_text": user_text, "facts": facts, "session": self.session})

    def recall(self, query: str, k: int | None = None) -> dict:
        return self._post("/recall", {"query": query, "k": k})

    def ingest(self, source: str, content: str) -> dict:
        return self._post("/ingest", {"session": self.session, "source": source, "content": content})

    def forget(self, query: str) -> dict:
        return self._post("/forget", {"query": query, "session": self.session})

    def turn(self, text: str) -> dict:
        return self._post("/turn", {"text": text, "session": self.session})

    def status(self) -> dict:
        return self._get("/status")

    def search(self, q: str) -> list:
        return self._get("/search?q=" + urllib.parse.quote(q))


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    c = MemoryClient()
    print(c.remember("je m'appelle Camille", [{"txt": "s'appelle Camille", "subject": "nom"}]))
    print(c.recall("comment je m'appelle ?"))
