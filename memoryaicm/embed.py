"""Vecteurs pour la récupération sémantique — le complément du lexical.

- HashingEmbedder : stdlib, déterministe. Mots (racines) + n-grammes de caractères hachés dans un vecteur
  de dimension fixe, normalisé. Robuste aux flexions et aux fautes, sans modèle ni réseau.
- OllamaEmbedder  : un vrai modèle d'embeddings local (nomic-embed-text, mxbai-embed-large…) si Ollama répond.

Le nom du modèle est stocké avec chaque vecteur : si l'embedder change, les vecteurs sont recalculés
à la demande, et `rebuild` les régénère tous depuis le journal (Log ⊇ Index, vecteurs compris).
"""

from __future__ import annotations

import json
import math
import struct
import urllib.request
import zlib

from .textutil import tokens


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


class HashingEmbedder:
    """Sac de mots + n-grammes de caractères (3 à 5), hachage signé, L2-normalisé."""

    def __init__(self, dim: int = 512, ngram: tuple[int, int] = (3, 5), word_weight: float = 2.0):
        self.dim = dim
        self.ngram = ngram
        self.word_weight = word_weight
        self.name = f"hash-ngram-v1-{dim}"

    def _features(self, text: str) -> dict[str, float]:
        """Mots de contenu (racines + synonymes) et n-grammes DANS ces mots : les mots vides ne font pas de bruit."""
        feats: dict[str, float] = {}
        lo, hi = self.ngram
        for t in tokens(text):
            feats[f"w:{t}"] = feats.get(f"w:{t}", 0.0) + self.word_weight
            s = f" {t} "
            for n in range(lo, hi + 1):
                for i in range(0, max(0, len(s) - n + 1)):
                    g = s[i:i + n]
                    if g.strip():
                        feats[f"g{n}:{g}"] = feats.get(f"g{n}:{g}", 0.0) + 1.0
        return feats

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, w in self._features(text).items():
            h = zlib.crc32(feat.encode("utf-8"))
            idx = h % self.dim
            sign = 1.0 if (h >> 31) & 1 == 0 else -1.0
            vec[idx] += sign * w
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class OllamaEmbedder:
    def __init__(self, url: str = "http://localhost:11434", model: str = "nomic-embed-text", timeout: float = 30.0):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.name = f"ollama:{model}"
        self.dim = 0

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/embeddings", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            vec = json.loads(r.read().decode("utf-8")).get("embedding") or []
        self.dim = len(vec)
        return [float(x) for x in vec]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


_PROBE_CACHE: dict[tuple[str, str], bool] = {}


def ollama_embedder_if_available(url: str, model: str, timeout: float = 0.4) -> OllamaEmbedder | None:
    """Sonde une fois par processus ; retourne l'embedder si le modèle répond, sinon None (le hachage prend le relais)."""
    key = (url, model)
    if key not in _PROBE_CACHE:
        try:
            e = OllamaEmbedder(url, model, timeout=timeout)
            _PROBE_CACHE[key] = bool(e.embed("sonde"))
        except Exception:
            _PROBE_CACHE[key] = False
    return OllamaEmbedder(url, model) if _PROBE_CACHE[key] else None


def get_embedder(settings) -> HashingEmbedder | OllamaEmbedder:
    choice = (getattr(settings, "embedder", "auto") or "auto").lower()
    if choice in ("auto", "ollama"):
        e = ollama_embedder_if_available(settings.ollama_url, settings.embed_model, settings.ollama_probe_timeout_s)
        if e is not None:
            return e
        if choice == "ollama":
            raise RuntimeError(f"embedder Ollama demandé mais {settings.embed_model} ne répond pas sur {settings.ollama_url}")
    return HashingEmbedder(dim=settings.embed_dim)
