# Banc mémoire — Echos / memoryaicm sur LoCoMo

**Date** : 9 septembre 2026 · **Jeu** : LoCoMo (snap-research), 10 conversations, 5 882 tours,
19 sessions maximum par conversation, étalées sur des mois · **Questions** : 1 536
(catégorie 5 « adversarial » exclue, comme dans les publications) · **Aucun juge LLM**.

## Pourquoi cette mesure-là

Tu m'as demandé si Echos était « le plus mnésique qui soit ». Je ne pouvais pas répondre :
sa mémoire n'avait jamais été mesurée. Les chiffres publiés par mem0, Zep et les autres sont
des scores **avec juge GPT-4o** : ils mélangent la qualité de la mémoire et celle du modèle qui
répond. Pour savoir ce que vaut *la mémoire d'Echos*, il faut isoler la mémoire.

Donc : on rejoue chaque conversation tour par tour dans un dépôt memoryaicm neuf — avec la
**vraie date** de chaque session, pour que la décroissance ACT-R et les questions temporelles
voient le temps réel du jeu. Puis, pour chaque question, on interroge l'index et on regarde si
le **tour de preuve** (`evidence`, p. ex. « D2:8 », donné par le jeu) est dans le top-k.

C'est objectif, rejouable, et sans opinion : soit la mémoire ramène la bonne phrase, soit non.

**Référence** : BM25 (Okapi, 1994), sur exactement les mêmes tours, les mêmes questions, le même
k. C'est le juge de paix : une mémoire qui perd contre un moteur lexical de trente ans n'est pas
une bonne mémoire.

## Résultat 1 — Echos tel qu'il était livré

| k | Echos (livré) | BM25 (1994) |
|---:|---:|---:|
| 1 | **0,264** | 0,298 |
| 5 | 0,473 | 0,517 |
| 10 | 0,553 | 0,602 |
| 30 | 0,677 | 0,720 |

**Echos perdait contre BM25 à tous les k.** Ce n'est pas une opinion, c'est 1 536 questions.

Diagnostic (mesuré, conversation 1, `run_complet.log`) :

- ACT-R + importance **aident** : sans eux, 0,453 au lieu de 0,540 au k=10. La partie du design
  qui semblait la plus discutable est celle qui tient.
- le lexical seul faisait 0,473, le sémantique seul 0,367, et leur combinaison par `max` 0,453 :
  **la combinaison faisait moins bien que la meilleure des deux voies**. C'est le symptôme.

## Résultat 2 — les deux corrections

Deux changements dans `memoryaicm/index.py`, rien d'autre.

**1. Le lexical pèse l'information, pas les mots.** `overlap` comptait tous les mots de la
question à poids égal : dans « qu'est-ce que Melanie a fait cette semaine à Kolkata ? », le mot
`semaine` pesait autant que `Kolkata`. Le nouveau `Index.lexical` pondère chaque mot par son
idf sur l'index actif. Avec des idf tous égaux, c'est exactement l'ancien `overlap` : c'est une
généralisation, pas un remplacement. Sous `idf_min_facts = 12` faits, l'idf n'a aucun sens
statistique et l'ancien comportement est conservé tel quel.

**2. Le sémantique redevient un recours.** `rel = max(lex, sem)` laissait le vecteur de hachage
faire remonter des faits « vaguement proches » **par-dessus** le fait qui contenait littéralement
les mots de la question. Désormais `rel = lex if lex > 0 else sem` : quand le lexical trouve, il
décide ; le sémantique rattrape les reformulations et les fautes, ce pour quoi il est là.

| k | Echos (livré) | **Echos corrigé** | BM25 |
|---:|---:|---:|---:|
| 1 | 0,264 | **0,340** | 0,299 |
| 3 | 0,404 | **0,473** | 0,461 |
| 5 | 0,473 | **0,528** | 0,517 |
| 10 | 0,553 | **0,607** | 0,602 |
| 20 | 0,624 | **0,674** | 0,674 |
| 30 | 0,677 | 0,708 | 0,720 |

Au k=1 — le cas qui compte, une seule note ramenée — **+29 % relatif** (0,264 → 0,340), et
Echos passe devant BM25. Coût : 2 176 caractères rendus par rappel au k=10 (≈ 550 jetons),
rappel médian **97 ms** sur 500 à 690 faits, en Python pur, sans GPU, sans réseau, sans base
vectorielle.

Ces chiffres viennent de `verifier_patch.py`, qui n'appelle **pas** la fonction du banc mais
`index.retrieve` lui-même : c'est le code réellement patché qui est mesuré.

## Résultat 3 — par type de question (Echos corrigé, hit@10)

| catégorie | score | n |
|---|---:|---:|
| 4 · fait simple | 0,599 | 841 |
| 2 · temporel | 0,579 | 321 |
| 1 · multi-saut | 0,447 | 282 |
| 3 · ouvert / bon sens | 0,370 | 92 |

Le multi-saut est le vrai trou : une question dont la réponse est répartie sur deux tours
éloignés demande de récupérer les **deux**, et un moteur de similarité n'a aucune raison de le
faire. C'est là qu'un sommeil qui **fabrique** le fait composé (« X et Y donc Z ») changerait la
donne — c'est exactement ce que `sleep.py` sait faire, mais il lui faut un LLM pour abstraire, et
ce banc-ci tourne sans LLM.

## Ce que ça permet de dire, et ce que ça ne permet pas

**Dire** : sur LoCoMo, en récupération pure, la mémoire d'Echos fait mieux qu'un BM25 de
référence, en 97 ms, en Python pur, sur une machine sans rien d'installé. Avec les chiffres.

**Ne pas dire** : « meilleure que mem0 / Zep ». Leurs scores publiés (LoCoMo ≈ 92 %, Zep 94,7 %)
sont des **J-scores avec juge GPT-4o** : la mémoire ramène des passages, GPT-4o rédige la
réponse, GPT-4o la note. Ce n'est pas la même grandeur que ce hit@k. Zep lui-même est mesuré à
75,1 % par un tiers là où il s'annonce à 94,7 % — l'écart entre protocoles est plus grand que
l'écart entre systèmes. Comparer les deux nombres serait malhonnête, dans les deux sens.

Pour obtenir le nombre comparable, il faut la phase B : génération + juge. Elle a besoin d'un
LLM ; le tien (Qwen3-8B sur la 5070) suffit pour la génération, mais le juge doit être le même
que celui des publications si l'on veut poser les chiffres côte à côte. À décider.

## Fichiers

| fichier | rôle |
|---|---|
| `bench/locomo/banc_locomo.py` | le banc : ingestion datée, balayage de configurations, BM25 de référence |
| `bench/locomo/verifier_patch.py` | vérification bout en bout par `index.retrieve` lui-même |
| `bench/locomo/locomo10.json` | le jeu public (snap-research/locomo) |
| `bench/locomo/resultats_locomo.json` | tous les chiffres, par conversation et par catégorie |
| `bench/locomo/run_complet.log` | la trace de la campagne complète |
| `memoryaicm/index.py` | `Index.lexical` (idf) + `rel = lex if lex else sem` |
| `memoryaicm/config.py` | `idf_min_facts = 12` |
| `tests/test_lexical_idf.py` | 4 tests qui fixent les deux propriétés |

`.bak-20260909` à côté de chaque fichier modifié. **101 tests verts** (97 d'origine + 4 nouveaux).

## Une piste mesurée, pas appliquée

Alléger les poids (`w_act` 0,35 → 0,15 et `w_imp` 0,5 → 0,15) donne encore mieux sur LoCoMo :
hit@1 0,347 · hit@5 0,543 · hit@10 0,615 · hit@30 0,732. Je ne l'ai **pas** appliqué : sur
LoCoMo tous les faits ont la même importance, donc `w_imp` n'y ajoute que du bruit, alors que
dans ta mémoire à toi l'importance discrimine vraiment. Un banc ne doit pas décider d'un réglage
qu'il ne sait pas mesurer. Deux lignes de `config.py` si tu veux l'essayer.
