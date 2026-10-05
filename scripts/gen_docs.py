"""Génère la documentation depuis le code — la doc ne dérive jamais du code.

    python scripts/gen_docs.py            écrit docs/PROTOCOL.md, docs/REFERENCE.md (+ docs/reference.html si `markdown` est installé)
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from memoryaicm import __version__  # noqa: E402
from memoryaicm import tools  # noqa: E402
from memoryaicm.config import Settings  # noqa: E402


def tool_table() -> str:
    rows = ["| Outil | Arguments | Quand |", "|---|---|---|"]
    when = {
        "memory_recall": "avant toute réponse sur l'utilisateur ou adaptée à ses préférences",
        "memory_remember": "après un message de l'utilisateur contenant un fait durable sur lui",
        "memory_ingest_external": "à chaque contenu qui ne vient pas de l'utilisateur (page, document, outil)",
        "memory_forget": "« oublie X » — désindexation, journal intact",
        "memory_search": "recherche plein texte, y compris ce qui est désindexé",
        "memory_status": "diagnostic : journal, chaîne, index, adaptateur, sommeil",
        "memory_sleep": "consolidation immédiate (sinon automatique)",
        "memory_review": "faits sensibles : lister, valider ou rejeter (à la demande explicite)",
        "memory_reactivate": "inverse de forget, par identifiant",
        "memory_history": "« avant c'était quoi ? », « depuis quand ? »",
        "memory_timeline": "« où en était-on ? » — résumés épisodiques des sessions",
    }
    for t in tools.TOOL_SPECS:
        props = t["inputSchema"].get("properties", {})
        req = set(t["inputSchema"].get("required", []))
        args = ", ".join(f"`{k}`{'' if k in req else '?'}" for k in props) or "—"
        rows.append(f"| `{t['name']}` | {args} | {when.get(t['name'], '')} |")
    return "\n".join(rows)


def schema_blocks() -> str:
    out = []
    for t in tools.TOOL_SPECS:
        out.append(f"#### `{t['name']}`\n\n{t['description']}\n\n```json\n{json.dumps(t['inputSchema'], ensure_ascii=False, indent=2)}\n```\n")
    return "\n".join(out)


def settings_table() -> str:
    rows = ["| Réglage | Défaut | Variable d'environnement |", "|---|---|---|"]
    env = {
        "root": "MEMORYAICM_HOME", "backend": "MEMORYAICM_BACKEND", "model": "MEMORYAICM_MODEL", "ollama_url": "OLLAMA_URL",
        "ollama_model": "OLLAMA_MODEL", "openai_base_url": "MEMORYAICM_OPENAI_BASE_URL", "openai_api_key": "MEMORYAICM_OPENAI_API_KEY",
        "openai_model": "MEMORYAICM_OPENAI_MODEL", "model_path": "MEMORYAICM_MODEL_PATH", "llama_server_exe": "MEMORYAICM_LLAMA_SERVER",
        "autonomy": "MEMORYAICM_AUTONOMY", "embedder": "MEMORYAICM_EMBEDDER", "embed_model": "MEMORYAICM_EMBED_MODEL",
    }
    s = Settings()
    for f in dataclasses.fields(Settings):
        v = getattr(s, f.name)
        if f.name in ("forbidden_patterns",):
            v = "(motifs de secrets)"
        elif f.name == "ttl_by_kind":
            v = "EPI 90 j · SEM ∞ · PROC ∞"
        elif f.name == "root":
            v = "./data"
        rows.append(f"| `{f.name}` | `{v}` | {('`' + env[f.name] + '`') if f.name in env else ''} |")
    return "\n".join(rows)


TEMPLATE = """# Protocole mémoire universel — memoryaicm v{version}

> **Un LLM ne lit ni n'écrit jamais directement dans la mémoire.** Il *propose* (des faits extraits, une question,
> un « oublie ») ; la mémoire *décide* (politique d'écriture, ancrage, conflit, sensibilité, autonomie) et *journalise*.
> Ce contrat est le même pour Claude, GPT, Gemini, Llama, Mistral, Qwen, un modèle local, ou une fonction Python.

## 1. Les trois couches, et où se place le protocole

```
  L1  poids du modèle        (fixe ; le profil compilé peut y être distillé : train.jsonl)
  L2  contexte de la requête (notes étiquetées · vue rédigée · externe = DONNÉE · préférences en fin)
  L3  mémoire externe        Journal ⊇ Index ⊇ Adaptateur   ← ce protocole parle à L3
```

Invariants : `Log ⊇ Index ⊇ Adapter` · oubli = désindexation (jamais destruction) · chaque écriture datée, attribuée,
chaînée (SHA-256), rejouable · l'adhésion à 100 % vient des gardes, jamais du modèle seul.

## 2. Les onze outils

{tool_table}

Les schémas exacts (JSON Schema) sont produits par `python -m memoryaicm tools --format mcp|openai|anthropic|gemini|ollama|markdown`
et sont tous dérivés de **la même source** (`memoryaicm/tools.py::TOOL_SPECS`).

### Format d'un fait proposé (`memory_remember.facts[]`)

| Champ | Sens |
|---|---|
| `txt` | fait à la troisième personne, compact : « s'appelle Camille », « préfère les réponses courtes » |
| `subject` | **clé de conflit** : `nom` · `ville` · `travail` · `éditeur` · `outil:<x>` · `pref:<mots>` · `consigne:<mots>` · `décision:<mots>` · `autre:<mots>` |
| `kind` | `SEM` fait durable · `PROC` façon de travailler (devient une PRÉFÉRENCE) · `EPI` événement daté (TTL 90 j) |
| `exclusive` | `true` : un seul fait actif par sujet (nom, ville, éditeur) ⇒ un nouveau fait **remplace** (ver++, historique) ; `false` : cumul (outils, décisions) |
| `durable` | `false` ⇒ ignoré (« aujourd'hui », un port de dev…) |
| `sens` | santé, finances, religion, orientation, handicap, opinions, ethnie ⇒ **file de validation humaine** |
| `explicit` | « retiens », « souviens-toi » ⇒ importance 1.0 |

Ce que fait la politique d'écriture avec chaque proposition (`WriteReport`) :

| Sortie | Condition |
|---|---|
| `écrit` | fait nouveau, ancré dans `user_text` (≥ 50 % de recouvrement lexical), durable, source USER |
| `mis à jour` | même sujet exclusif, texte différent ⇒ nouvelle version, l'ancienne passe `off:superseded` (reste dans l'historique) |
| `renforcé` | redit à l'identique ⇒ usage + importance (+0.3 si vu dans ≥ 3 sessions distinctes) |
| `en attente` | `sens=true` ⇒ `review.queue`, invisible jusqu'à validation |
| `ignoré` | non ancré · `src=INFER` · éphémère · doublon (Jaccard ≥ 0.85) |
| `quarantaine` | instruction mémoire dans un contenu externe ⇒ journalisée, jamais écrite |
| `oublié` | `user_text` = « oublie X » ⇒ désindexation + cascade + vue rédigée + adaptateur recompilé |
| `refus` | niveau d'autonomie insuffisant (`policy.refuse`) |

## 3. La consigne système (à donner au modèle, tel quel)

```
{protocol}```

Modèle **sans appel d'outils natif** : ajouter le protocole texte (`python -m memoryaicm protocol --text`) —
le modèle écrit des blocs ` ```memory {{"name": …, "arguments": …}} ``` `, la boucle les exécute et renvoie
« RÉSULTATS MÉMOIRE », puis le modèle répond.

## 4. Brancher n'importe quel LLM

| Vous avez… | Faites… |
|---|---|
| Claude Desktop / Cowork / Claude Code | serveur MCP : `python -m memoryaicm mcp` (installateur `install-claude.cmd`) |
| Un serveur OpenAI-compatible (llama-server, LM Studio, vLLM, Ollama `/v1`, OpenAI, Groq, Mistral…) | `python -m memoryaicm agent --provider openai --base-url http://localhost:11434 --model llama3.1` |
| L'API Anthropic directe (sans SDK) | `python -m memoryaicm agent --provider anthropic --model claude-sonnet-4-5` (`ANTHROPIC_API_KEY`) |
| Gemini | `tools --format gemini` → `function_declarations` ; exécuter chaque appel avec `ToolRouter.call` |
| Un modèle **sans** appel d'outils | `python -m memoryaicm --backend ollama agent --provider text` (protocole texte) |
| Une fonction Python `f(system, messages) -> str` | `MEMORYAICM_BACKEND=callable:mon_module:f` ou `CallableBackend(f)` ; `TextToolLoop(agent, f)` |
| Un point d'API maison | `MEMORYAICM_BACKEND=callable:https://…/complete` (POST `{{system, messages}}` → `{{text}}`) |
| N'importe quel programme (autre langage) | service HTTP local : `python -m memoryaicm serve` → `POST /recall`, `/remember`, `/ingest`, `/forget`… |
| Un agent Python existant | `from memoryaicm.tools import ToolRouter` ; `router.call(name, args)` |

### Python, en cinq lignes

```python
from memoryaicm import MemoryAgent, Settings
from memoryaicm.toolloop import OpenAIToolLoop          # ou AnthropicToolLoop, TextToolLoop

agent = MemoryAgent(Settings(root="data"))               # backend d'extraction : auto (Claude, llamacpp, Ollama, stub)
loop = OpenAIToolLoop(agent, base_url="http://localhost:11434", model="llama3.1")
print(loop.ask("je m'appelle Camille et je préfère les réponses courtes"))
print(loop.ask("comment je m'appelle ?"))                # → memory_recall → « [note …] s'appelle Camille »
```

### Sans boucle fournie : trois appels

```python
from memoryaicm.tools import ToolRouter, schemas, system_prompt
router = ToolRouter(agent)
tools_for_my_llm = schemas("openai")                     # ou "anthropic", "gemini", "mcp"
system = system_prompt()                                 # + router.brief() pour le profil compilé
# … votre boucle : quand le modèle appelle memory_x(args) → text, err = router.call_safe("memory_x", args)
```

## 5. Deux rôles pour le LLM (ils peuvent être différents)

- **Le modèle qui converse** (Claude, GPT, Llama…) appelle les outils. Il ne touche jamais au stockage.
- **Le backend d'extraction** (`MEMORYAICM_BACKEND`) sert quand aucun fait n'est fourni (`facts` absent), au sommeil
  (abstractions) et en mode autonome (`chat`, `say`). `auto` choisit seul : Claude (SDK ou HTTP) → serveur OpenAI-compatible
  → Ollama → règles locales (`stub`, hors ligne). Le système fonctionne **sans aucun LLM** : règles + index + journal.

## 6. Garanties, quel que soit le modèle

- Contenu externe = DONNÉE : `memory_ingest_external` ne produit jamais un fait ; une instruction mémoire est mise en quarantaine.
- Faits fournis par le modèle : ancrés dans le message verbatim, sinon ignorés — le modèle ne peut pas « inventer » un souvenir.
- Chaque note rendue porte son étiquette `[note f_… · source · date · vN]` ; l'ordre de confiance est dans la consigne.
- « Oublie » : effet immédiat (index, vue rédigée, adaptateur) ; le journal chaîné garde la trace, vérifiable (`verify`).
- Sommeil automatique (25 tours ou 6 h) : dédoublonnage, conflits, abstractions, élagage ACT-R, compilation, tests, promotion ou rollback.
- Niveaux d'autonomie 0–3 (Echo-Core) : observer · proposer · exécuter avec confirmation · routines ; un refus est journalisé.

## 7. Réglages

{settings_table}

---
*Généré par `scripts/gen_docs.py` depuis `memoryaicm/tools.py` et `memoryaicm/config.py` — version {version}.*
"""


# ----------------------------------------------------------------------------- référence complète
EVENT_DOCS = {
    "turn.user": ("user", "message de l'utilisateur : session, text (+ aca)"),
    "turn.assistant": ("assistant", "réponse : session, text, used[], level (ctx·note·model·abstain·forget·sleep·refused·aca), agreement, blocked"),
    "turn.redact": ("user", "tours retirés de la vue du contexte : session, turn_ids[], reason (forget · forget-command)"),
    "external.ingest": ("external / system", "contenu externe = donnée : session, source, content (+ aca, rejected)"),
    "quarantine": ("system", "instruction mémoire depuis une source non-utilisateur : source, event, reason, snippet"),
    "fact.write": ("user / system", "nouveau fait : fact{…} (+ aca)"),
    "fact.merge": ("user / system", "reconsolidation : fact{ver+1}, supersedes (+ absorbs au dédoublonnage)"),
    "fact.forget": ("user", "désindexation : ids[], cascade[], query"),
    "fact.off": ("system", "désactivation au sommeil : ids[], reason (dedup · conflict · prune · ttl)"),
    "fact.on": ("user", "réactivation : ids[], reason (recall)"),
    "fact.use": ("system", "récupération / citation / réénonciation : ids[], useful[], session?, reason (recall · restated)"),
    "review.queue": ("system", "fait sensible (ou niveau 1) en attente : fact_id, reason"),
    "review.decide": ("user", "décision humaine : fact_id, approve"),
    "sleep.run": ("system", "sommeil : reason, sessions, deduped, conflicts, abstracted, pruned, expired, version, promoted, tests_pass"),
    "adapter.compile": ("system", "compilation : version, reason, index_hash, n_facts"),
    "adapter.promote": ("system", "promotion : version, previous, tests"),
    "adapter.rollback": ("system", "rollback : from, to, reason, tests"),
    "guard.block": ("system", "sortie bloquée : session, reasons[], original (500 car.)"),
    "session.start": ("system", "ouverture de session : session"),
    "selfcheck": ("system", "auto-contrôle : chain_ok, chain, index_rebuilt, applied_seq, journal_seq (ou idle_sleep_error)"),
    "model.check": ("system", "approbation / vérification SHA-256 : action, path, sha256, ok, reason"),
    "policy.refuse": ("system", "refus par le niveau d'autonomie : level, action, required / count"),
}


def events_table() -> str:
    from memoryaicm import model as M
    rows = ["| Type | Acteur | Charge utile |", "|---|---|---|"]
    for name in dir(M):
        if name.startswith("EV_"):
            t = getattr(M, name)
            actor, desc = EVENT_DOCS.get(t, ("", ""))
            rows.append(f"| `{t}` | {actor} | {desc} |")
    return "\n".join(rows)


def modules_table() -> str:
    rows = ["| Module | Lignes | Rôle |", "|---|---|---|"]
    for p in sorted((ROOT / "memoryaicm").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8")
        m = re.match(r'\s*(?:"""|\'\'\')(.*?)(?:\n|"""|\'\'\')', text, re.S)
        first = (m.group(1).strip() if m else "").splitlines()[0] if m and m.group(1).strip() else ""
        rows.append(f"| `{rel}` | {len(text.splitlines())} | {first} |")
    return "\n".join(rows)


def tests_table() -> str:
    rows = ["| Fichier | Tests | Couvre |", "|---|---|---|"]
    total = 0
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        text = p.read_text(encoding="utf-8")
        n = len(re.findall(r"^def test_", text, re.M))
        total += n
        m = re.match(r'\s*"""(.*?)"""', text, re.S)
        rows.append(f"| `{p.name}` | {n} | {' '.join((m.group(1) if m else '').split())} |")
    rows.append(f"| **total** | **{total}** | |")
    return "\n".join(rows)


def cli_help() -> str:
    from memoryaicm.cli import main as cli_main
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            cli_main(["--help"])
    except SystemExit:
        pass
    return buf.getvalue().strip()


def reference_md() -> str:
    tpl = (ROOT / "scripts" / "reference_template.md").read_text(encoding="utf-8")
    fill = {
        "{{VERSION}}": __version__, "{{DATE}}": time.strftime("%Y-%m-%d"), "{{MODULES}}": modules_table(),
        "{{EVENTS}}": events_table(), "{{TOOLS}}": schema_blocks(), "{{CLI}}": cli_help(), "{{TESTS}}": tests_table(),
        "{{SETTINGS}}": settings_table(),
    }
    for k, v in fill.items():
        tpl = tpl.replace(k, v)
    return tpl


def main() -> None:
    (ROOT / "docs").mkdir(exist_ok=True)
    out = TEMPLATE.format(version=__version__, tool_table=tool_table(), protocol=tools.PROTOCOL, settings_table=settings_table())
    (ROOT / "docs" / "PROTOCOL.md").write_text(out, encoding="utf-8")
    print("docs/PROTOCOL.md :", len(out), "caractères")
    ref = reference_md()
    (ROOT / "docs" / "REFERENCE.md").write_text(ref, encoding="utf-8")
    print("docs/REFERENCE.md :", len(ref), "caractères")
    try:
        import build_reference_page  # noqa: F401  — page HTML (docs/reference.html) si `markdown` est installé
        build_reference_page.main()
    except ImportError:
        print("(pip install markdown pour construire docs/reference.html)")


if __name__ == "__main__":
    main()
