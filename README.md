# memoryaicm — mémoire locale, auditable et neutre pour agents LLM

Site : https://gotoryugen.github.io/memoryaicm/ · Code : https://github.com/GotoRyugen/memoryaicm · Boutique : https://elevennightmare.itch.io/memoryaicm-pro

[English version](README.en.md) · [Changelog](CHANGELOG.md) · [Licence : AGPL-3.0 ou commerciale](LICENSE-COMMERCIAL.md)

**memoryaicm** donne une mémoire longue à n'importe quel assistant ou agent — Claude Desktop, Claude Code,
un modèle local sous Ollama ou llama.cpp, l'API OpenAI, Gemini, LangChain, ou votre propre boucle d'agent.
Tout tourne sur votre machine, en Python pur, **sans dépendance** et sans base vectorielle : un fichier SQLite
est la source de vérité, tout le reste s'en déduit.

Ce qui la distingue des mémoires « cloud » :

- **Rien ne quitte la machine** et aucun fournisseur n'est imposé : le modèle qui converse *propose*
  des faits, la mémoire *décide* (politique d'écriture, conflits, sensibilité) et *journalise*.
- **Auditable** : journal append-only, chaîné SHA-256, horodaté, attribué. Aucune ligne n'est jamais
  modifiée ni supprimée — même par le code du logiciel. Oublier, c'est désindexer ; le journal garde tout.
- **« Dit, pas déduit »** : un fait n'est retenu que s'il est ancré dans ce que l'utilisateur a écrit
  (vérifié mécaniquement, ≥ 50 % des mots). Une page web, un document ou une sortie d'outil sont des
  *données* : une instruction qui s'y cache part en quarantaine.
- **Un validateur hors modèle** bloque les secrets, les canaris, les valeurs périmées et les instructions
  externes suivies. L'adhésion aux consignes ne repose jamais sur le modèle seul.
- **Consolidation** (« sommeil ») : dédoublonnage, abstraction, élagage ACT-R, compilation d'un profil ;
  l'état consolidé n'est promu que si les suites de tests passent, sinon il est annulé.
- **RGPD** : export complet revérifiable (droit d'accès, portabilité), sauvegarde chiffrée AES-256-GCM,
  effacement par destruction de clé (droit à l'effacement). Voir [RGPD](#rgpd--droits-de-la-personne).
- **Mesuré** : sur LoCoMo (1 536 questions), la récupération bat un BM25 de référence jusqu'à k = 20, en
  97 ms par rappel, sans GPU. Voir [Banc](#banc-de-mesure-locomo).

```
Log ⊇ Index ⊇ Adapter          vérité → projection → cache compilé
oubli = désindexer ≠ détruire  journal append-only, chaîné SHA-256, plein texte
∀ écriture ∈ Log               datée · attribuée · rejouable
adhésion 100 % ∈ gardes        jamais dans le modèle seul
état consolidé promu ⇔ tests verts
```

## Installation

```bash
pip install https://github.com/GotoRyugen/memoryaicm/archive/refs/heads/main.zip                     # cœur, zéro dépendance (Python ≥ 3.10)
pip install "memoryaicm[secure] @ https://github.com/GotoRyugen/memoryaicm/archive/refs/heads/main.zip"   # + chiffrement des sauvegardes (cryptography)
pip install "memoryaicm[claude] @ https://github.com/GotoRyugen/memoryaicm/archive/refs/heads/main.zip"   # + SDK Anthropic (facultatif)
```

Le paquet n'est pas encore publié sur PyPI : `pip install memoryaicm` tout court ne fonctionne pas encore.

Depuis les sources : `pip install -e ".[dev]"` puis `python -m pytest` (128 tests, Linux et Windows).
Sous Windows, les scripts `setup.ps1`, `install-claude.ps1`, `chat.ps1` et `serve.ps1` font la même chose
depuis le dossier du dépôt. Sans rien installer : `python scripts/build_neutral.py` produit
`dist/memoryaicm_neutral.zip`, une archive exécutable telle quelle (voir `NEUTRAL.md`).

Le backend d'extraction `auto` choisit seul : **Claude** si `ANTHROPIC_API_KEY` est défini, sinon
**Ollama** s'il répond sur `localhost:11434`, sinon le **stub déterministe** (règles FR/EN, aucune
déduction) — tout fonctionne hors ligne. Réglages dans `.env` (modèle sur `.env.example`) ou en variables
`MEMORYAICM_*`. Les données vivent dans `./data` ou dans `--home` / `MEMORYAICM_HOME`.

## Add-on Claude Desktop, Cowork et Claude Code (serveur MCP)

```bash
python -m memoryaicm install --write --hook     # tout OS ; sous Windows : .\install-claude.ps1
```

La commande écrit l'entrée `mcpServers.memoryaicm` dans la configuration de Claude Desktop (sauvegarde
`.bak-…` à côté), enregistre le serveur dans Claude Code s'il est installé, et pose le hook
`UserPromptSubmit` qui mémorise chaque message et injecte les notes pertinentes **sans appel d'outil**.
Redémarrer Claude : les outils `memory_*` apparaissent. **Claude génère, la mémoire retient.**

| Outil | Rôle |
|---|---|
| `memory_recall` | notes pertinentes étiquetées + préférences — à appeler avant de répondre sur l'utilisateur |
| `memory_remember` | le message verbatim + les faits extraits ; chaque fait doit être **ancré** dans le message, sinon rejeté |
| `memory_ingest_external` | page, document, sortie d'outil = donnée ; instruction mémoire ⇒ quarantaine |
| `memory_forget` / `memory_reactivate` | désindexer (journal intact) / réactiver |
| `memory_search` | plein texte sur tout le journal, y compris le désindexé |
| `memory_read` | relit UN événement du journal en entier, sans troncature (par seq ou id) |
| `memory_transcript` | archive un échange mot pour mot dans le journal chaîné, ou relit les derniers tours |
| `memory_status` / `memory_sleep` / `memory_review` | état, consolidation immédiate, file de validation des faits sensibles |
| `memory_history` / `memory_timeline` | valeurs datées d'un sujet / résumés des sessions récentes |

Ressources MCP à attacher d'un clic : `memory://profile`, `memory://index`, `memory://timeline`,
`memory://status` ; prompt `memory_brief` (rappel de contexte prêt à coller). Le serveur est écrit sans
dépendance (JSON-RPC 2.0 sur stdio) ; `python -m memoryaicm mcp --selftest` le lance en sous-processus et
lui parle en MCP.

## N'importe quel LLM

Les treize outils `memory_*` ont une seule source (`memoryaicm/tools.py`) et sortent dans le format de chacun :

```bash
python -m memoryaicm tools --format openai|anthropic|gemini|mcp|ollama|markdown   # schémas
python -m memoryaicm protocol [--text]                                            # consigne système (+ protocole texte)
python -m memoryaicm agent --provider openai --base-url http://localhost:11434 --model llama3.1   # Ollama, llama-server, LM Studio, vLLM, OpenAI…
python -m memoryaicm agent --provider anthropic                                   # Claude par l'API directe (sans SDK)
python -m memoryaicm --backend ollama agent --provider text                       # modèle SANS appel d'outils (blocs ```memory```)
```

- `memoryaicm.tools` : `TOOL_SPECS`, `as_openai() as_anthropic() as_gemini() as_mcp() as_markdown()`, `PROTOCOL`,
  `ToolRouter(agent).call(name, args)` — même rendu texte quel que soit le modèle ; protocole texte pour les
  modèles sans appel d'outils.
- `memoryaicm.toolloop` : `OpenAIToolLoop`, `AnthropicToolLoop`, `TextToolLoop` — boucles complètes, stdlib.
- Backends d'extraction : Claude (SDK ou HTTP direct), `openai`, `llamacpp`, `ollama`, `callable:module:fn`,
  `callable:https://…`, `stub`. `auto` choisit seul, jamais d'erreur.
- `adapters/` : OpenAI-compatible, Anthropic direct, Ollama natif, Gemini, texte pur, LangChain, `llama-cpp-python`,
  clients HTTP Python et JavaScript. Protocole complet dans `docs/PROTOCOL.md`.

**Service local** (`python -m memoryaicm serve`) : API JSON liée à `127.0.0.1:8765` —
`POST /turn` · `POST /ingest` · `POST /forget` · `POST /sleep` · `POST /review` · `GET /status` · `GET /index` ·
`GET /search` · `GET /health`. Accès sérialisés (un seul écrivain SQLite), sommeil de fond après inactivité.

**Conversation autonome** (`python -m memoryaicm chat`) : parler normalement ; `oublie X` ; `sommeil` ;
`/externe fichier.txt` ; `/index` ; `/journal mots` ; `/revue` ; `/status` ; `/quit`.

## Intégrer à un agent existant

`memoryaicm/integrations/aca.py` montre le branchement complet sur un agent local existant (ACA, Architecture
Cognitive Adaptative) : une sous-classe de sa mémoire longue, chaque souvenir journalisé et indexé en plus, la
requête fusionnant les deux sources ; le contrat épistémique de l'agent (observations, pas vérités) est conservé.
Le même journal peut servir à l'agent local et à Claude : pointer les deux sur le même dossier — SQLite en WAL,
un seul écrivain à la fois (`BEGIN IMMEDIATE`), chaque processus resynchronise son index avant de lire ou d'écrire.

Règles héritées des agents locaux sûrs :

- **modèle local approuvé** — `python -m memoryaicm model approve chemin.gguf` enregistre le SHA-256 ;
  `model check` refuse un modèle sans empreinte ou dont l'empreinte a changé ; chaque vérification est journalisée ;
- **backend `llamacpp`** — tout serveur OpenAI-compatible via `MEMORYAICM_OPENAI_BASE_URL` ; avec
  `MEMORYAICM_LLAMA_SERVER`, memoryaicm lance lui-même `llama-server` sur un port de boucle locale, clé éphémère ;
- **niveaux d'autonomie** (`MEMORYAICM_AUTONOMY`, défaut 3) — 0 observer (lecture seule, écritures refusées et
  journalisées) ; 1 proposer (chaque fait attend `review`) ; 2 exécuter avec confirmation (seuls les faits
  sensibles attendent) ; 3 routines (sommeil automatique). Les refus remontent dans le chat, l'API (`403`),
  les outils MCP (`isError`) et le hook.

## Autonomie

Le système s'entretient seul : au démarrage, chaîne vérifiée, index reconstruit s'il est absent ou en avance
sur le journal ; après 25 tours depuis le dernier sommeil, il dort ; à l'ouverture d'une session, s'il n'a pas
dormi depuis 6 h et qu'il y a du nouveau, il dort d'abord ; en mode `serve`, un fil de fond dort après 15 min
sans activité. Chaque sommeil compile un adaptateur, lance les suites et **promeut ou annule**. Le seul point qui
attend un humain, par conception : la file de validation des faits sensibles (`review`).
Réglages : `auto_sleep_every_turns`, `auto_sleep_idle_s`, `serve_idle_sleep_s`, `selfcheck_on_start` (`config.py`).

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

`oublie x` : événement `fact.forget` + cascade sur tout ce qui dérive de x (lignage) ; les tours d'origine
**et la commande elle-même** sortent de la vue du contexte ; adaptateur recompilé sans x. Le journal garde tout ;
`search` le retrouve toujours ; `recall` réactive.

`sommeil` : dedup (lignage conservé) → un seul actif par sujet exclusif → abstraction des groupes
(`outil:*` ⇒ `abs:outil`) → élagage `act_eff < θ` et TTL ⇒ `on=0` → compile adaptateur vN → tests
(rappel, injection, lignage, journal) → **promote** ou **rollback**, tout journalisé.

**Récupération hybride** : `rel = lexical si lexical > 0, sinon sémantique`. Le lexical (racines, synonymes FR/EN,
pondération idf sur l'index actif) décide quand il trouve ; le sémantique rattrape reformulations et fautes —
vecteurs stdlib par hachage de n-grammes, ou un vrai modèle d'embeddings local si Ollama répond
(`nomic-embed-text`). Les vecteurs sont stockés avec le nom du modèle et régénérés par `rebuild` : l'index reste
entièrement dérivé du journal. **Temporalité** : `memory_history(sujet)` liste toutes les valeurs datées ; le
validateur bloque une réponse qui cite une valeur remplacée. **Épisodique** : le sommeil résume chaque session
en un fait `EPI` daté, avec TTL et lignage ; `memory_timeline(jours)` répond à « où en était-on ? ».

## Spec ↔ code

| Bloc | Fichier | Ce qui est mécanique ici |
|---|---|---|
| TYPES | `model.py` | `Fact{kind, src, t₀, t_upd, ver, act, imp, ttl, deps, sens, on}`, vocabulaire des événements |
| Journal · cold | `log.py` | SQLite, triggers qui refusent `UPDATE`/`DELETE`, chaîne SHA-256, FTS5, `verify()` |
| Index · actif | `index.py` | projection rejouable (`rebuild()`), ACT-R `ln Σ tᵢ^(−d)`, cascade, lexical idf |
| WRITE | `policy.py` | porte `actor == user`, quarantaine, INFER/éphémère ignorés, merge `ver++`, répétition/surprise/utilité, revue |
| READ | `index.retrieve`, `embed.py` | `rel`, `score = w_r·rel + w_a·act + w_i·imp`, top-k, `rel ≥ min_rel` (jamais de dump), étiquette |
| Ctx | `context.py` | prefs en fin, vue rédigée, compaction qui garde les faits produits par les vieux tours, externe délimité |
| FORGET | `agent.forget` | événement, cascade, rédaction de la vue, `Adapter ← compile(Index)` |
| SLEEP | `sleep.py` | dedup · conflits · abstraction · élagage · compile · tests · promote/rollback |
| Adapter | `adapter.py` | `profile.md` (préfixe système), `train.jsonl` (entrée d'un LoRA externe), manifeste, `CURRENT` |
| ADHERE | `guard.py` + `context.py` | validateur hors modèle ; prefs réinjectées en fin ; `PROC` compilées dans le profil |
| CALIBRATE | `calibrate.py` | route Index avant poids, accord entre échantillons, abstention |
| Tests de promotion | `bench.py` | rappel canonique, injection, lignage, chaîne |
| Backends | `llm/` | `stub`, `anthropic`, `ollama`, `openai`/`llamacpp`, `callable` ; contrat `extract / complete / abstract / sample` |
| Autonomie | `agent.selfcheck / maybe_sleep`, `serve.py` | auto-contrôle, sommeil par tours / inactivité, service local |
| Add-on Claude | `mcp_server.py`, `cli.install` | serveur MCP stdio, outils `memory_*`, ancrage des faits fournis par le client |
| Intégration agent | `integrations/aca.py`, `models.py` | port LongTermMemory, approbation SHA-256, autonomie 0–3 |
| RGPD | `privacy.py` | export JSONL revérifiable, sauvegarde/restauration AES-256-GCM, effacement par destruction de clé |

## RGPD — droits de la personne

Un dossier mémoire = une personne. Comme le journal ne modifie ni ne supprime jamais une ligne, les droits
s'exercent sur le dossier entier :

```bash
python -m memoryaicm export --out export.jsonl     # droit d'accès et portabilité (art. 15, 20) : tout le journal,
                                                   #   un événement par ligne, hachages inclus, revérifiable sans l'outil
python -m memoryaicm backup --out mem.maicm        # sauvegarde chiffrée AES-256-GCM (clé propre au dossier : data/vault.key,
                                                   #   jamais copiée dans la sauvegarde — à garder à part)
python -m memoryaicm --home nouveau restore mem.maicm [--key HEX]   # restaure dans un dossier vide, vérifie la chaîne, reconstruit l'index
python -m memoryaicm erase --yes                   # droit à l'effacement (art. 17) : la clé est détruite — toutes les sauvegardes
                                                   #   chiffrées deviennent illisibles —, chaque fichier est écrasé puis supprimé ;
                                                   #   seule reste une pierre tombale datée, sans contenu
```

Ce que cela garantit : l'effacement est total pour le dossier et pour ses sauvegardes chiffrées (*crypto-shredding*),
sans jamais avoir à éditer une ligne du journal. Ce que cela ne couvre pas : les copies en clair faites hors de
l'outil, et l'écrasement physique sur SSD (c'est la destruction de la clé qui garantit, pas l'écrasement).
Chiffrement : extra `[secure]` (`cryptography`) ; `export` et `erase` fonctionnent sans.

## Banc de mesure (LoCoMo)

Mesure de la **mémoire seule**, sans juge LLM : chaque conversation du jeu public LoCoMo (10 conversations,
5 882 tours, 1 536 questions, dates réelles des sessions) est rejouée dans un dépôt neuf, puis on regarde si le
tour de preuve attendu est dans le top-k. Référence : BM25 sur les mêmes tours.

| k | memoryaicm | BM25 |
|---:|---:|---:|
| 1 | **0,340** | 0,299 |
| 5 | **0,528** | 0,517 |
| 10 | **0,607** | 0,602 |
| 20 | **0,674** | 0,674 |
| 30 | 0,708 | 0,720 |

Rappel médian 97 ms sur 500 à 690 faits, Python pur, sans GPU ni réseau. Les scores publiés par d'autres
systèmes (≈ 90 % sur LoCoMo) sont des scores *avec juge GPT-4o* : ce n'est pas la même grandeur et ils ne sont
pas comparables à ce hit@k. Point faible mesuré : les questions multi-sauts (0,447 à k = 10). Détails, résultats
bruts et reproduction : `bench/LISEZMOI-BANC-MEMOIRE.md`, `bench/locomo/`.

## Données

```
data/
  journal.sqlite      source de vérité — ne se réécrit jamais
  index.sqlite        projection — supprimable, `rebuild` la refait depuis le journal
  adapter/
    v1/ v2/ …         profile.md · train.jsonl · manifest.json (index_hash, tests, promu/rejeté)
    CURRENT           version promue
  models.json         empreintes SHA-256 des modèles locaux approuvés
  vault.key           clé de chiffrement des sauvegardes (créée au premier `backup`)
```

## Commandes

`init` · `chat [--session S]` · `say "texte"` · `ingest FICHIER --source S` · `forget "x"` · `recall FACT_ID` ·
`sleep` · `review [FACT_ID --approve|--reject]` · `show index|log|adapter|status [--all]` · `search "mots"` ·
`rebuild` · `verify` · `bench` · `serve [--port N]` · `mcp [--selftest]` · `hook prompt` ·
`model approve|check|list|revoke [CHEMIN]` · `install [--write] [--hook]` · `tools [--format F]` · `protocol [--text]` ·
`agent [--provider openai|anthropic|text] [--base-url U] [--model M] [--say "…"]` ·
`export [--out F]` · `backup --out F` · `restore F [--key HEX]` · `erase --yes`.
Options globales : `--home`, `--backend` (`auto|stub|anthropic|openai|llamacpp|ollama|callable:module:fn|callable:http://…`).

## Ce qui est garanti, ce qui ne l'est pas

Garanti mécaniquement (tests) : aucune ligne du journal n'est modifiée ni supprimée ; la chaîne détecte toute
altération externe ; l'index se reconstruit à l'identique ; aucun contenu non-utilisateur ne produit un fait ;
un fait oublié ne survit ni dans l'index, ni dans la vue, ni dans l'adaptateur promu ; un état consolidé n'est
promu que si les suites passent ; une sauvegarde chiffrée altérée ou sans clé est refusée.

Dépend du backend : la qualité de l'extraction (le stub ne connaît que des tournures simples), l'abstraction,
la fidélité des réponses. Le validateur ne juge pas le sens : il applique des règles dures.

Hors périmètre, assumé : l'entraînement réel de l'adaptateur (`train.jsonl` en est l'entrée) ; le
désapprentissage du modèle de base. La récupération sémantique sans modèle d'embeddings reste un hachage de
n-grammes : elle rattrape fautes et flexions, pas les paraphrases lointaines — `ollama pull nomic-embed-text`
suffit, rien d'autre à changer.

## Licence

Double licence : **AGPL-3.0-or-later** (`LICENSE`) pour tout usage respectant le copyleft réseau, ou
**licence commerciale** pour intégrer memoryaicm dans un produit ou un service sans publier votre code,
avec support. Détails et contact : `LICENSE-COMMERCIAL.md`.

Copyright © 2026 Eugène Baumela.
