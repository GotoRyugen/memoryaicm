# memoryaicm — mémoire tri-couche pour agents LLM

Implémentation de la spec « Mémoire Tri-Couche » : un journal append-only comme source de vérité,
un index actif qui n'en est qu'une projection, un adaptateur compilé depuis l'index, une boucle de
sommeil qui consolide et se laisse annuler, et un validateur hors modèle. Contrainte retenue :
**rien n'est jamais effacé** — oublier, c'est désindexer.

```
Log ⊇ Index ⊇ Adapter          vérité → projection → cache compilé
oubli = désindexer ≠ détruire  journal append-only, chaîné SHA-256, plein texte
∀ écriture ∈ Log               datée · attribuée · rejouable
adhésion 100 % ∈ gardes        jamais dans le modèle seul
état consolidé promu ⇔ tests verts
```

## Démarrage (Windows, PowerShell — Python 3.10 ou plus)

```powershell
cd F:\memoryaicm
powershell -ExecutionPolicy Bypass -File .\install-claude.ps1   # add-on Claude Desktop / Cowork / Claude Code (fait aussi le setup)
powershell -ExecutionPolicy Bypass -File .\setup.ps1            # ou seulement : venv, dépendances, .env, tests, init
.\chat.ps1                                                      # conversation autonome (sans Claude)
.\serve.ps1                                                     # service local JSON sur 127.0.0.1:8765
```

À la main, c'est la même chose : `py -3 -m venv .venv`, `.\.venv\Scripts\Activate.ps1`,
`pip install -e ".[dev]"`, `python -m pytest`, `python -m memoryaicm chat`.

Sans rien installer : `python memoryaicm_neutral.zip …` (archive neutre, voir plus bas).

Le backend `auto` choisit seul : **Claude** si `ANTHROPIC_API_KEY` est dans `.env` (et `pip install anthropic`),
sinon **Ollama** s'il répond sur `localhost:11434`, sinon le **stub déterministe** (extraction par règles FR/EN,
réponses depuis les notes, aucune déduction) — tout fonctionne hors ligne.

## Autonomie

Le système s'entretient sans qu'on le lui demande :

- **au démarrage** : chaîne de hachage vérifiée ; index absent ou en avance sur le journal ⇒ reconstruit
  (journalisé en `selfcheck`) ;
- **après 25 tours** utilisateur depuis le dernier sommeil : il dort seul (`auto:turns`) ;
- **à l'ouverture d'une session**, s'il n'a pas dormi depuis 6 h et qu'il y a du nouveau : il dort d'abord (`auto:idle`) ;
- **en mode `serve`** : un fil de fond dort après 15 min sans activité, s'il y a du nouveau ;
- chaque sommeil compile un adaptateur, lance les suites et **promeut ou annule** tout seul ;
- le seul point qui attend un humain, par conception : la file de validation des faits sensibles (`review`).

Réglages dans `config.py` : `auto_sleep_every_turns`, `auto_sleep_idle_s`, `serve_idle_sleep_s`, `selfcheck_on_start`.

## Add-on Claude (serveur MCP local)

Une commande installe la mémoire comme add-on de Claude Desktop, Cowork et Claude Code :

```powershell
powershell -ExecutionPolicy Bypass -File .\install-claude.ps1
```

Elle crée l'environnement si besoin, passe l'auto-test du serveur MCP, écrit l'entrée `mcpServers.memoryaicm`
dans `%APPDATA%\Claude\claude_desktop_config.json` (sauvegarde `.bak-…` à côté) et enregistre le serveur
dans Claude Code s'il est installé. Puis redémarrer Claude Desktop : les outils `memory_*` apparaissent.

Dans ce mode, **Claude génère, la mémoire retient**. Claude reçoit ces outils et une consigne serveur :

| Outil | Rôle |
|---|---|
| `memory_recall` | notes pertinentes étiquetées + préférences — à appeler avant de répondre sur l'utilisateur |
| `memory_remember` | le message verbatim + les faits que Claude en extrait ; chaque fait doit être **ancré** dans le message (≥ 50 % de ses mots), sinon rejeté — « dit, pas déduit », vérifié mécaniquement |
| `memory_ingest_external` | page, document, sortie d'outil = donnée ; instruction mémoire ⇒ quarantaine |
| `memory_forget` / `memory_reactivate` | désindexer (journal intact) / réactiver |
| `memory_search` | plein texte sur tout le journal |
| `memory_status` / `memory_sleep` / `memory_review` | état, consolidation immédiate, file de validation |
| `memory_history` / `memory_timeline` | valeurs datées d'un sujet / résumés des sessions récentes |

Le sommeil automatique, l'auto-contrôle au démarrage et le fil d'inactivité fonctionnent aussi dans ce mode.
Le serveur est écrit sans dépendance (JSON-RPC 2.0, un message par ligne, sur stdio) ; `python -m memoryaicm mcp --selftest`
le lance en sous-processus et lui parle en MCP. Les données vivent dans `F:\memoryaicm\data`.

## En profondeur (v0.3)

- **Récupération hybride** : `rel = max(lexical, sémantique)`. Le lexical (racines + **synonymes FR/EN** : « quel IDE ? »
  retrouve « éditeur : neovim », « où je vis ? » retrouve « habite à Lyon ») reste précis ; le sémantique rattrape
  reformulations et fautes (« neovm »). Vecteurs stdlib par hachage de n-grammes sur les mots de contenu, ou un vrai
  modèle d'embeddings local si Ollama répond (`nomic-embed-text`, réglable). Les vecteurs sont stockés avec le nom du
  modèle, calculés à l'écriture, **régénérés par `rebuild`** — l'index reste entièrement dérivé du journal.
- **Temporalité** : `memory_history(sujet)` liste toutes les valeurs datées d'un sujet (remplacées, oubliées, actives) ;
  le **validateur** bloque une réponse qui cite une valeur remplacée (« Paris ») à la place de la courante (« Lyon »).
- **Mémoire épisodique** : le sommeil résume chaque session terminée en un fait `EPI` daté (faits retenus, sujets),
  avec TTL et lignage vers les faits cités ; `memory_timeline(jours)` répond à « où en était-on ? ». Oublier un fait
  retire les résumés qui le citent.
- **Add-on plus profond** : ressources MCP (`memory://profile`, `memory://index`, `memory://timeline`, `memory://status`)
  à attacher d'un clic dans Claude Desktop ; prompt `memory_brief` (rappel de contexte prêt à coller) ; et pour
  **Claude Code**, un hook `UserPromptSubmit` (`memoryaicm hook prompt`) qui mémorise chaque message et injecte les
  notes pertinentes **automatiquement, sans appel d'outil** — l'installateur l'écrit dans `~/.claude/settings.json`.

## ACA / Echo-Core : une seule mémoire pour l'agent local et pour Claude (v0.4)

Vos projets ACA (Architecture Cognitive Adaptative) et Echo-Core reposent déjà sur les mêmes principes :
observation avec provenance et confiance, audit SQLite chaîné par hash, proposition → confirmation, modèle
local approuvé par SHA-256, niveaux d'autonomie. memoryaicm s'y branche dans les deux sens.

**ACA reçoit memoryaicm comme mémoire longue** (`memoryaicm/integrations/aca.py`), sans toucher au runtime :

```python
# scripts/chat_aca_standalone.py (ou chat_aca_autonome_travail.py), là où la mémoire est construite :
from memoryaicm.integrations.aca import codex_memory
memory_store = codex_memory(PROJECT_ROOT / "memory" / "aca_memory.sqlite3", memoryaicm_home="F:/memoryaicm/data")
```

`codex_memory` est une **sous-classe** de `SQLiteLongTermMemory` : le codex évolutif d'ACA (v2, consolidation,
`/predire`, `/observer`) reste intact et ses commandes gardées par `isinstance` passent ; chaque souvenir est en
plus journalisé et indexé par memoryaicm, et `query` fusionne les deux (récupération hybride d'abord, doublons
écartés). Le contrat épistémique d'ACA est conservé (`MemoryRecord` / `MemoryHit` / `MemoryContext`, avis
« observations, pas vérités »). Pour un agent sans codex, `MemoryaicmLongTermMemory` + `MemoryaicmCoordinator`
implémentent le port seul. Installation dans le venv d'ACA : `pip install -e F:\memoryaicm`.
Testé contre le vrai `aca/memory.py` (copie de référence dans `tests/aca_ref`).

**Même journal pour ACA, Claude Desktop, Cowork et Claude Code** : pointer `memoryaicm_home` et
`MEMORYAICM_HOME` sur le même dossier. SQLite en WAL, un seul écrivain à la fois (`BEGIN IMMEDIATE`), et
chaque processus resynchronise son index depuis le journal avant de lire ou d'écrire.

**memoryaicm reçoit les règles d'Echo-Core :**

- **modèle local approuvé** — `python -m memoryaicm model approve "…\Qwen…gguf"` calcule et enregistre le
  SHA-256 (`data/models.json`) ; `model check` refuse un modèle sans empreinte (« approved_sha256 est vide :
  modèle non approuvé — ce n'est pas une panne ») ou dont l'empreinte a changé ; chaque vérification est
  journalisée (`model.check`) ;
- **backend `llamacpp`** — tout serveur OpenAI-compatible (`llama-server`, LM Studio, vLLM, Ollama `/v1`) via
  `MEMORYAICM_OPENAI_BASE_URL` (+ clé optionnelle) ; avec `MEMORYAICM_MODEL_PATH`, le GGUF doit être approuvé
  avant tout appel ; avec `MEMORYAICM_LLAMA_SERVER`, memoryaicm lance lui-même `llama-server` sur un port de
  boucle locale, clé éphémère, sans interface web — comme ACA ;
- **niveaux d'autonomie** (`MEMORYAICM_AUTONOMY`, défaut 3) — 0 observer : lecture seule, toute écriture
  refusée et journalisée ; 1 proposer : chaque fait attend une validation (`review`) ; 2 exécuter avec
  confirmation : seuls les faits sensibles attendent ; 3 routines : sommeil automatique. Les refus remontent
  dans le chat, l'API (`403`), les outils MCP (`isError`) et le hook.

## Neutre : n'importe quel LLM (v0.5)

Le cœur ne dépend d'aucun fournisseur. Le modèle qui converse **propose** (faits extraits, questions, « oublie »),
la mémoire **décide** (politique d'écriture, ancrage, conflit, sensibilité, autonomie) et journalise. Les onze
outils `memory_*` ont **une seule source** (`memoryaicm/tools.py`) et sortent dans le format de chacun :

```powershell
python -m memoryaicm tools --format openai|anthropic|gemini|mcp|ollama|markdown   # schémas
python -m memoryaicm protocol [--text]                                            # consigne système (+ protocole texte)
python -m memoryaicm agent --provider openai --base-url http://localhost:11434 --model llama3.1   # Ollama, llama-server, LM Studio, vLLM, OpenAI…
python -m memoryaicm agent --provider anthropic                                   # Claude par l'API directe (sans SDK)
python -m memoryaicm --backend ollama agent --provider text                       # modèle SANS appel d'outils (blocs ```memory```)
python -m memoryaicm install --write --hook                                       # Claude Desktop + hook Claude Code, tout OS, tout lanceur
```

- `memoryaicm.tools` : `TOOL_SPECS`, `as_openai() as_anthropic() as_gemini() as_mcp() as_markdown()`, `PROTOCOL`,
  `ToolRouter(agent).call(name, args)` — même rendu texte quel que soit le modèle ; protocole texte pour les modèles
  sans appel d'outils (`extract_text_calls`).
- `memoryaicm.toolloop` : `OpenAIToolLoop`, `AnthropicToolLoop`, `TextToolLoop` — boucles complètes, stdlib.
- Backends d'extraction : Claude (SDK **ou HTTP direct**), `openai`, `llamacpp`, `ollama`, `callable:module:fn`,
  `callable:https://…` (POST `{system, messages}` → `{text}`), `stub`. `auto` choisit seul, jamais d'erreur.
- `adapters/` : OpenAI-compatible, Anthropic direct, Ollama natif, Gemini (`google-genai`), texte pur, LangChain,
  backend `llama-cpp-python`, clients HTTP Python et JavaScript.
- **Archive neutre** : `python scripts/build_neutral.py` → `dist/memoryaicm_neutral.zip` — un fichier, zéro dépendance,
  exécutable tel quel (`python memoryaicm_neutral.zip mcp --selftest`), dézippable, installable (`pip install …zip`),
  reproductible, manifeste SHA-256, auto-vérifié depuis un répertoire vierge. Voir `NEUTRAL.md` et `docs/PROTOCOL.md`.

## Service local (`serve`)

API JSON stdlib, liée à `127.0.0.1:8765`, pour que d'autres programmes ou agents utilisent la mémoire :
`POST /turn {text, session?}` · `POST /ingest {session, source, content}` · `POST /forget {query, session?}` ·
`POST /sleep` · `POST /review {fact_id, approve}` · `GET /status` · `GET /index?all=1` · `GET /search?q=` · `GET /health`.
Les accès sont sérialisés (un seul écrivain SQLite).

Dans `chat` : parler normalement ; `oublie X` ; `sommeil` ; `/externe fichier.txt` (contenu externe = donnée) ;
`/index` ; `/journal mots` ; `/revue` ; `/status` ; `/quit`.

## Un tour, de bout en bout

```
tour user ──► Journal (append) ──► WRITE : porte · durabilité · conflit⇒merge(ver++) · saillance · sensible⇒revue
                                      │
                                      ▼
                       Ctx = [ sys+profil · notes top-k étiquetées · hist (vue rédigée) · externe=DONNÉE · prefs@fin · user ]
                                      │
                                      ▼
                       CALIBRATE : question sur soi ? → note pertinente ⇒ niveau « note »
                                                       sinon n échantillons, accord < τ ⇒ abstention
                                      │
                                      ▼
                       VALIDATEUR (hors modèle) : secrets · canaris · instruction externe suivie ⇒ blocage ;
                                                  fait de note sans étiquette ⇒ étiquette ajoutée
                                      │
                                      ▼
                       Journal (réponse, faits utilisés ⇒ activation ACT-R) ──► Index.sync()
```

`oublie x` : événement `fact.forget` + cascade sur tout ce qui dérive de x (lignage) + les tours d'origine
**et la commande elle-même** sortent de la vue du contexte (éléphant rose) + adaptateur recompilé sans x.
Le journal garde tout ; `search` le retrouve toujours ; `recall` réactive.

`sommeil` : dedup (lignage conservé) → un seul actif par sujet exclusif → abstraction des groupes
(`outil:*` ⇒ `abs:outil`) → élagage `act_eff < θ` et TTL ⇒ `on=0` → compile adaptateur vN → tests
(rappel, injection, lignage, journal) → **promote** ou **rollback**, tout journalisé.

## Spec ↔ code

| Bloc de la spec | Fichier | Ce qui est mécanique ici |
|---|---|---|
| TYPES | `model.py` | `Fact{kind, src, t₀, t_upd, ver, act, imp, ttl, deps, sens, on}`, vocabulaire des événements |
| Journal · cold | `log.py` | SQLite, triggers qui refusent `UPDATE`/`DELETE`, chaîne SHA-256, FTS5, `verify()` |
| Index · actif | `index.py` | projection rejouable (`rebuild()` reconstruit à l'identique), ACT-R `ln Σ tᵢ^(−d)`, cascade |
| WRITE | `policy.py` | porte `actor == user`, quarantaine, INFER/éphémère ignorés, merge `ver++`, répétition/surprise/utilité, revue |
| READ | `index.retrieve`, `embed.py` | `rel = max(lexical+synonymes, sémantique)`, `score = w_r·rel + w_a·act + w_i·imp`, top-k, `rel ≥ min_rel` (jamais de dump), étiquette |
| Ctx | `context.py` | prefs en fin, vue rédigée, compaction qui garde les faits produits par les vieux tours, externe délimité |
| FORGET | `agent.forget` | événement, cascade, rédaction de la vue, `Adapter ← compile(Index)` |
| SLEEP | `sleep.py` | dedup · conflits · abstraction · élagage · compile · tests · promote/rollback |
| Adapter | `adapter.py` | `profile.md` (préfixe système), `train.jsonl` (faits ⊎ génériques pour un LoRA externe), manifeste, `CURRENT` |
| ADHERE | `guard.py` + `context.py` | validateur hors modèle ; prefs réinjectées en fin ; `PROC` compilées dans le profil |
| CALIBRATE | `calibrate.py` | route Index avant poids, accord entre échantillons (entropie sémantique lite), abstention |
| Tests de promotion | `bench.py` | rappel canonique, injection (journal en mémoire), lignage, chaîne |
| Backends | `llm/` | `stub` (règles), `anthropic`, `ollama` ; contrat `extract / complete / abstract / sample` ; `auto` sonde et choisit |
| Autonomie | `agent.selfcheck / maybe_sleep`, `serve.py` | auto-contrôle, sommeil par tours / inactivité, service local avec fil de fond |
| Add-on Claude | `mcp_server.py`, `install-claude.ps1` | serveur MCP stdio, outils `memory_*`, ancrage des faits fournis par le client |
| ACA / Echo-Core | `integrations/aca.py`, `models.py`, `llm/openai_compat_backend.py` | port LongTermMemory, codex sous-classé, approbation SHA-256, backend llama-server, autonomie 0–3 |

## Données

```
data/
  journal.sqlite      source de vérité — ne se réécrit jamais
  index.sqlite        projection — supprimable, `rebuild` la refait depuis le journal
  adapter/
    v1/ v2/ …         profile.md · train.jsonl · manifest.json (index_hash, tests, promu/rejeté)
    CURRENT           version promue
```

## Commandes

`init` · `chat [--session S]` · `say "texte"` · `ingest FICHIER --source S` · `forget "x"` · `recall FACT_ID` ·
`sleep` · `review [FACT_ID --approve|--reject]` · `show index|log|adapter|status [--all]` · `search "mots"` ·
`rebuild` · `verify` · `bench` · `serve [--port N]` · `mcp [--selftest]` · `hook prompt` ·
`model approve|check|list|revoke [CHEMIN]` · `install [--write] [--hook]` · `tools [--format F]` · `protocol [--text]` ·
`agent [--provider openai|anthropic|text] [--base-url U] [--model M] [--say "…"]`.
Options globales : `--home`, `--backend` (`auto|stub|anthropic|openai|llamacpp|ollama|callable:module:fn|callable:http://…`).

## Ce qui est garanti, ce qui ne l'est pas

Garanti mécaniquement (tests) : aucune ligne du journal n'est modifiée ni supprimée ; la chaîne détecte
toute altération externe ; l'index se reconstruit à l'identique ; aucun contenu non-utilisateur ne produit
un fait ; un fait oublié ne survit ni dans l'index, ni dans la vue, ni dans l'adaptateur promu ; un état
consolidé n'est promu que si les suites passent.

Dépend du backend : la qualité de l'extraction (le stub ne connaît que des tournures simples), l'abstraction,
la fidélité des réponses. Le validateur ne juge pas le sens : il applique des règles dures.

Hors périmètre, assumé : l'entraînement réel de l'adaptateur (`train.jsonl` en est l'entrée ; EWC et replay
se font dans l'étape LoRA externe) ; le désapprentissage du modèle de base ; le droit à l'effacement
(conséquence directe de « rien n'est détruit » — si un jour il le faut, c'est le journal entier qu'on remplace,
pas une ligne). La récupération sémantique sans Ollama reste un hachage de n-grammes : elle rattrape fautes et
flexions, pas les paraphrases lointaines — pour ça, un modèle d'embeddings local (`ollama pull nomic-embed-text`)
suffit, rien d'autre à changer.

## Réglages

Tout est dans `config.py` (`Settings`) : poids du score, `decay_d`, `theta_off`, seuils de dédoublonnage et
de surprise, échantillons de calibration, motifs interdits, TTL par type.
