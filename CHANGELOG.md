# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) · versions : [SemVer](https://semver.org/lang/fr/).

## [0.6.0] — 2026-10-05

### Ajouté
- **RGPD** (`memoryaicm/privacy.py`) : `export` (droit d'accès et portabilité — journal complet en JSONL
  revérifiable hors outil), `backup` / `restore` (instantané cohérent du journal, chiffré AES-256-GCM avec
  une clé propre au dossier, `vault.key`), `erase --yes` (droit à l'effacement : clé détruite — toute
  sauvegarde chiffrée devient illisible —, fichiers écrasés puis supprimés, pierre tombale datée).
  Extra `[secure]` (`cryptography`) pour le chiffrement ; export et erase n'en dépendent pas.
- Banc LoCoMo : résultats complets (`bench/locomo/resultats_locomo.json`, `run_complet.log`),
  script de vérification bout en bout (`verifier_patch.py`) et mode d'emploi (`bench/locomo/README.md`).
- Intégration continue GitHub (Linux + Windows, Python 3.10 → 3.13), publication PyPI sur tag.

### Modifié
- **Licence** : MIT → double licence **AGPL-3.0-or-later OU commerciale** (`LICENSE`, `LICENSE-COMMERCIAL.md`).
- Exemples, tests et documentation neutralisés (nom d'exemple « Camille », chemins génériques).
- Métadonnées de paquet complètes (auteur, mots-clés, classifieurs, extras).

## [0.5.0] — 2026-09-04
- Neutre : outils `memory_*` en une seule source rendue en OpenAI / Anthropic / Gemini / MCP / texte ;
  boucles d'outils ; backends `openai`, `llamacpp`, `callable` ; archive `memoryaicm_neutral.zip` exécutable.

## [0.4.0] — 2026-09-04
- Intégration ACA (port LongTermMemory, codex sous-classé) ; règles Echo-Core : approbation SHA-256 des
  modèles locaux, backend llama-server, niveaux d'autonomie 0–3.

## [0.3.0] — 2026-09
- Récupération hybride (lexical + synonymes FR/EN, sémantique par hachage ou embeddings Ollama),
  `memory_history`, mémoire épisodique, ressources MCP, hook Claude Code.

## [0.2.0] / [0.1.0] — 2026-09
- Journal append-only chaîné, index ACT-R, politique d'écriture, sommeil/consolidation avec
  promotion ou annulation, adaptateur compilé, validateur hors modèle, serveur MCP, service local.
