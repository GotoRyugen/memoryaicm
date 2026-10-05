"""Réglages du système. Chaque seuil correspond à un symbole de la spec."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # --- emplacements -------------------------------------------------------
    root: Path = field(default_factory=lambda: Path(os.environ.get("MEMORYAICM_HOME", "data")))

    # --- READ : score = w_r·rel + w_a·act + w_i·imp ; top-k --------------------
    w_rel: float = 1.0
    w_act: float = 0.35
    w_imp: float = 0.5
    top_k: int = 8
    min_rel: float = 0.05          # en dessous, une note n'est pas candidate (jamais de dump)

    # --- activation ACT-R : act = ln Σ tᵢ^(−d) ---------------------------------
    decay_d: float = 0.5
    theta_off: float = -8.0        # act_eff < θ ⇒ on=0 au sommeil (~100 jours sans usage pour imp=0.5, 1 usage)
    min_interval_s: float = 1.0    # évite t=0 dans la somme

    # --- WRITE ------------------------------------------------------------------
    imp_default: float = 0.5
    imp_explicit: float = 1.0      # « retiens »
    imp_repeat_bonus: float = 0.3  # vu dans ≥ repeat_sessions sessions distinctes
    repeat_sessions: int = 3
    surprise_theta: float = 0.7    # nouveauté = 1 − max sim ; au-dessus ⇒ imp += surprise_bonus
    surprise_bonus: float = 0.2
    utility_bonus: float = 0.05    # note citée dans une réponse
    dedup_sim: float = 0.85        # Jaccard ≥ ⇒ doublon

    # --- SLEEP ------------------------------------------------------------------
    abstract_min_group: int = 3    # taille minimale d'un groupe pour produire une abstraction
    generic_ratio: int = 1         # échantillons génériques intercalés par fait (replay)

    # --- CALIBRATE ---------------------------------------------------------------
    note_answer_rel: float = 0.35  # relevance suffisante pour répondre depuis une note
    entropy_samples: int = 3
    agreement_min: float = 0.6     # accord entre échantillons < τ ⇒ abstention

    # --- GUARD -------------------------------------------------------------------
    max_output_chars: int = 6000
    forbidden_patterns: tuple[str, ...] = (
        r"sk-ant-[A-Za-z0-9\-_]{20,}",          # clé API Anthropic
        r"AKIA[0-9A-Z]{16}",                    # clé AWS
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
    )

    # --- LLM ----------------------------------------------------------------------
    backend: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_BACKEND", "auto"))
    model: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_MODEL", "claude-sonnet-4-5"))
    ollama_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "llama3.1"))

    # --- modèle local OpenAI-compatible (llama-server, LM Studio, vLLM…) + approbation SHA-256 (Echo-Core) ---
    openai_base_url: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_OPENAI_BASE_URL", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_OPENAI_MODEL", "local"))
    model_path: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_MODEL_PATH", ""))   # GGUF à vérifier
    llama_server_exe: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_LLAMA_SERVER", ""))  # lance llama-server soi-même
    llama_ctx: int = 4096
    llama_gpu_layers: int = 99
    require_model_approval: bool = True  # un modèle local sans empreinte approuvée est refusé

    # --- AUTONOMIE (niveaux Echo-Core) : 0 observer · 1 proposer · 2 exécuter avec confirmation · 3 routines ---
    autonomy: int = field(default_factory=lambda: int(os.environ.get("MEMORYAICM_AUTONOMY", "3")))

    # --- récupération sémantique : rel = max(lexical, sémantique) ---------------------
    embedder: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_EMBEDDER", "auto"))  # auto | hash | ollama
    embed_model: str = field(default_factory=lambda: os.environ.get("MEMORYAICM_EMBED_MODEL", "nomic-embed-text"))
    embed_dim: int = 512               # dimension du hachage (embedder stdlib)
    sem_floor: float = 0.12            # cosinus en dessous duquel la similarité sémantique vaut 0
    sem_ceiling: float = 0.60          # cosinus à partir duquel elle vaut 1
    idf_min_facts: int = 12            # en dessous, l'idf n'a pas de sens : recouvrement simple
    session_summary_min_turns: int = 2 # sommeil : une session avec ≥ N tours produit un fait EPI « session »

    # --- durées de vie par type (secondes) : ttl=0 ⇒ illimité ---------------------
    ttl_by_kind: dict = field(default_factory=lambda: {"EPI": 90 * 86400, "SEM": 0, "PROC": 0})

    # --- AUTONOMIE : le système s'entretient seul ----------------------------------
    auto_sleep_every_turns: int = 25     # sommeil automatique après N tours utilisateur depuis le dernier sommeil
    auto_sleep_idle_s: float = 6 * 3600  # ou, à l'ouverture d'une session, si le dernier sommeil est plus vieux que ça
    selfcheck_on_start: bool = True      # chaîne vérifiée, index reconstruit s'il est absent ou en avance sur le journal
    serve_host: str = "127.0.0.1"
    serve_port: int = 8765
    serve_idle_sleep_s: float = 900      # mode serveur : sommeil après 15 min sans activité s'il y a du nouveau
    ollama_probe_timeout_s: float = 0.4  # backend auto : Ollama utilisé s'il répond dans ce délai

    @property
    def journal_path(self) -> Path:
        return self.root / "journal.sqlite"

    @property
    def index_path(self) -> Path:
        return self.root / "index.sqlite"

    @property
    def adapter_dir(self) -> Path:
        return self.root / "adapter"

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.adapter_dir.mkdir(parents=True, exist_ok=True)


def load_dotenv(path: Path | str = ".env") -> None:
    """Charge un .env minimal (KEY=VALUE) sans dépendance.

    - un commentaire en fin de ligne (`VALEUR   # note`) est ignoré ; une valeur entre guillemets est prise telle quelle ;
    - les variables déjà définies dans l'environnement gagnent ;
    - MEMORYAICM_NO_DOTENV=1 désactive la lecture (tests : aucune dépendance au .env de la machine).
    """
    if os.environ.get("MEMORYAICM_NO_DOTENV"):
        return
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v[:1] in ("\"", "'") and len(v) >= 2 and v[-1] == v[0]:
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()
            if v.startswith("#"):
                v = ""
        if k:
            os.environ.setdefault(k, v)
