# memoryaicm_neutral.zip — la mémoire, neutre, en un fichier

Une archive, zéro dépendance (Python ≥ 3.10, bibliothèque standard), **aucun fournisseur de LLM imposé**.
Le modèle qui converse (Claude, GPT, Gemini, Llama, Mistral, Qwen, un modèle local, une fonction Python) *propose* ;
la mémoire *décide* et *journalise*. Le stockage (journal chaîné + index + adaptateur) ne dépend d'aucun modèle.

## Trois façons de l'utiliser

```bash
python memoryaicm_neutral.zip init                 # 1. tel quel : l'archive s'exécute (zipapp)
unzip memoryaicm_neutral.zip -d memoryaicm         # 2. dézippée : arborescence complète (code, docs, tests, adaptateurs)
pip install memoryaicm_neutral.zip                 # 3. installée : commande `memoryaicm` + `import memoryaicm`
```

Toutes les commandes acceptent `--home DOSSIER` (données, défaut `./data`) et `--backend …` (extraction locale).

## Brancher votre LLM

| Vous avez… | Commande |
|---|---|
| Claude Desktop / Cowork / Claude Code | `python memoryaicm_neutral.zip install --write --hook` puis redémarrer Claude |
| Ollama, llama-server, LM Studio, vLLM, OpenAI, Groq, Mistral… (OpenAI-compatible) | `python memoryaicm_neutral.zip agent --provider openai --base-url http://localhost:11434 --model llama3.1` |
| L'API Anthropic directe | `ANTHROPIC_API_KEY=… python memoryaicm_neutral.zip agent --provider anthropic` |
| Un modèle **sans** appel d'outils | `python memoryaicm_neutral.zip --backend ollama agent --provider text` |
| Gemini, LangChain, Node, un autre langage | voir `adapters/` (dézipper) et `docs/PROTOCOL.md` |
| Rien du tout (hors ligne) | `python memoryaicm_neutral.zip chat` — règles locales, tout fonctionne |

Schémas des outils dans le format de votre fournisseur : `python memoryaicm_neutral.zip tools --format openai|anthropic|gemini|mcp|markdown`.
Consigne système à donner au modèle : `python memoryaicm_neutral.zip protocol [--text]`.

## Vérifier

```bash
python memoryaicm_neutral.zip mcp --selftest       # le serveur MCP répond (initialize, tools/list, remember, recall, forget)
python memoryaicm_neutral.zip bench                # suites : rappel, injection, lignage, journal
python memoryaicm_neutral.zip verify               # chaîne de hachage du journal
python memoryaicm_neutral.zip status               # (show status) état complet
python -m pytest tests                             # après dézippage : la suite complète
```

`MANIFEST.json` liste chaque fichier avec son SHA-256 ; `python memoryaicm_neutral.zip --help` liste les commandes.

## Contenu

```
__main__.py            point d'entrée (python memoryaicm_neutral.zip …)
memoryaicm/            le paquet : journal, index, politique, contexte, gardes, calibrage, sommeil, adaptateur,
                       outils universels (tools.py), boucles d'outils (toolloop.py), serveur MCP, service HTTP, CLI,
                       backends (Claude SDK ou HTTP, OpenAI-compatible, Ollama, callable, stub), intégration ACA/Echo-Core
adapters/              exemples prêts : OpenAI-compatible, Anthropic direct, Ollama natif, Gemini, texte pur, LangChain,
                       backend callable (llama-cpp-python), clients HTTP Python et JavaScript
docs/PROTOCOL.md       le protocole universel (outils, format des faits, consigne, garanties, réglages)
docs/REFERENCE.md      référence complète (architecture, invariants, événements, API, exploitation, sécurité)
tests/                 suite pytest (dont l'intégration ACA contre le vrai aca/memory.py)
bench/                 cas d'injection ; README.md, .env.example, pyproject.toml, MANIFEST.json
```

## Garanties (indépendantes du modèle)

- `Log ⊇ Index ⊇ Adapter` ; oubli = désindexation, **jamais** destruction ; journal append-only chaîné SHA-256, vérifiable.
- Un fait proposé par le modèle doit être **ancré** dans le message verbatim de l'utilisateur, sinon il est ignoré.
- Contenu externe = DONNÉE : jamais un fait, instructions mises en quarantaine.
- Notes étiquetées (provenance, date, version) ; historique daté de chaque sujet ; mémoire épisodique des sessions.
- Sommeil automatique (25 tours / 6 h) : consolidation, tests, promotion ou rollback — sans intervention.
- Faits sensibles : file de validation humaine. Niveaux d'autonomie 0–3 ; modèles locaux approuvés par SHA-256.
