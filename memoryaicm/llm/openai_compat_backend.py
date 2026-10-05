"""Backend OpenAI-compatible : llama-server (llama.cpp), LM Studio, vLLM, Ollama (/v1)… stdlib uniquement.

Règle Echo-Core : si un chemin de modèle (GGUF) est configuré, il doit être approuvé par empreinte SHA-256
avant tout appel — vide ou différent ⇒ refus, journalisé. Optionnellement, ce backend lance lui-même
`llama-server` sur un port de boucle locale avec une clé éphémère et sans interface web (comme ACA).
"""

from __future__ import annotations

import json
import secrets
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from ..model import Fact
from .anthropic_backend import parse_candidates
from .base import Candidate, Context
from .prompts import ABSTRACT_SYSTEM, EXTRACT_SYSTEM, render_messages


class LlamaServerProcess:
    """llama-server local : boucle locale, clé éphémère, pas d'interface web, un seul slot."""

    def __init__(self, exe: Path, model_path: Path, ctx: int = 4096, gpu_layers: int = 99, startup_timeout: float = 180.0):
        if not exe.is_file():
            raise FileNotFoundError(f"llama-server introuvable : {exe}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.api_key = secrets.token_urlsafe(32)
        self.base_url = f"http://127.0.0.1:{self.port}"
        cmd = [str(exe), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(self.port), "--api-key", self.api_key,
               "--no-webui", "--parallel", "1", "--jinja", "--gpu-layers", str(gpu_layers), "--ctx-size", str(ctx)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=flags)
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server s'est arrêté au démarrage")
            try:
                req = urllib.request.Request(self.base_url + "/health", headers={"Authorization": f"Bearer {self.api_key}"})
                with urllib.request.urlopen(req, timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        self.close()
        raise TimeoutError("llama-server n'a pas répondu à temps")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class OpenAICompatBackend:
    name = "llamacpp"

    def __init__(self, base_url: str = "", api_key: str = "", model: str = "local", model_path: str = "",
                 approvals=None, require_approval: bool = True, llama_server_exe: str = "", ctx: int = 4096,
                 gpu_layers: int = 99, timeout: float = 600.0):
        self.model, self.timeout = model, timeout
        self.server: LlamaServerProcess | None = None
        if model_path and require_approval:
            if approvals is None:
                raise RuntimeError("approbation requise mais aucun registre de modèles fourni")
            ok, reason = approvals.check(model_path)
            if not ok:
                raise RuntimeError(f"modèle refusé — {reason}")
        if not base_url:
            if not (llama_server_exe and model_path):
                raise RuntimeError("MEMORYAICM_OPENAI_BASE_URL absent et pas de llama-server à lancer "
                                   "(MEMORYAICM_LLAMA_SERVER + MEMORYAICM_MODEL_PATH)")
            self.server = LlamaServerProcess(Path(llama_server_exe), Path(model_path), ctx=ctx, gpu_layers=gpu_layers)
            base_url, api_key = self.server.base_url, self.server.api_key
        self.base_url, self.api_key = base_url.rstrip("/"), api_key

    def _call(self, system: str, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1200) -> str:
        body = json.dumps({"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
                           "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        choices = data.get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""

    def extract(self, text: str) -> list[Candidate]:
        return parse_candidates(self._call(EXTRACT_SYSTEM, [{"role": "user", "content": text}], max_tokens=800))

    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        system, messages = render_messages(ctx)
        return self._call(system, messages, temperature=temperature)

    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        out = self._call(ABSTRACT_SYSTEM, [{"role": "user", "content": "\n".join(f"- {f.txt}" for f in facts)}], max_tokens=200)
        return out.splitlines()[0].strip() if out else None

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx, temperature=0.9) for _ in range(n)]

    def close(self) -> None:
        if self.server:
            self.server.close()
