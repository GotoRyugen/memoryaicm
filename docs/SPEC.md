# Mémoire Tri-Couche — spécification (v1, 2026-09-04)

*Contrainte : aucun effacement. Thèse : `Log ⊇ Index ⊇ Adapter` · oubli = désindexer · sommeil = la boucle manquante ·
adhésion 100 % ∈ gardes, ∉ modèle. Cette page est la spec d'origine ; `REFERENCE.md` décrit l'implémentation (section 16 :
spec ↔ code).*

## Couches

| Couche | Nature | Contenu |
|---|---|---|
| **L1 · poids** | procédural · lent · reconstructif | `W = Base ⊕ Adapter(u)` ; `gen = f(Base ⊕ Adapter, Ctx)` ; habitudes ici, consignes dans Ctx ; désapprendre Base : hors périmètre |
| **L2 · contexte** | travail · littéral · fini · volatil | `Ctx = [ sys · notes{f+label} · hist · prefs@fin · user ]` ; attention diluée ⇒ critique en tête / fin ; externe = donnée, jamais instruction ; « oublie » ⇒ vue rédigée, pas « ignore » |
| **L3 · magasin** | épisodique + sémantique · externe · inspectable · append-only | Journal (cold, source de vérité) ⊇ Index (actif, projection) ; sommeil (idle, consolidation) |

## Types

```
Fact  { id, kind:EPI|SEM|PROC, txt, src:USER|INFER|TOOL, t₀, t_upd, ver, act∈ℝ, imp, ttl, deps[id], sens, on }
Log   = append-only · immuable · full-text          // cold : ⊇ tout
Index = { f∈Log : f.on ∧ act(f)>θ }                  // actif : injectable
Ctx   = [ sys · notes{f+label} · hist · prefs@fin · user ]
W     = Base ⊕ Adapter(u)                            // Adapter = compile(Index), régénérable
```

## Write — Ctx → Log → Index

```
src ≠ tour_user          → quarantaine              // injection = donnée
¬durable(x) ∨ src=INFER  → skip
conflit(x, y∈Index)      → y' = merge(y,x) ; ver++ ; y.on=0 ; y'.deps ∋ y
imp↑ ⇐ « retiens » | vu ≥3 sessions | surprise>θ | a changé une réponse
sens(x)                  → file de validation humaine
∀x : append(Log) ⇒ horodaté · attribué · auditable
```

## Read — Index → Ctx

```
act(f)   = ln Σᵢ tᵢ^(−d)                 // ACT-R : usage↑ · temps↓
score    = w_r·rel + w_a·act + w_i·imp ; top-k
inject   = "note · src · date · ver"     // jamais un fait nu
place    : critique en tête / fin ; jamais de dump
confiance: ctx > note > W                // W = reconstructif
```

## Forget — aucun effacement

```
« oublie x » → append(Log, forget(x))    // événement, pas delete
Index : x.on=0 ; ∀y: x∈deps(y) → y.on=0  // cascade lignage
Ctx   : vue rédigée avant l'appel suivant // ≠ « ignore » (éléphant rose)
W     : Adapter ← compile(Index) ⇒ x ∉ W
Log   : intact                           // choix : rien n'est détruit
coût  : pas de droit à l'effacement      // assumé
```

## Sleep — idle · Log → Index → Adapter

```
Log[Δt] → extract(SEM) → dedup → merge(conflits, dates) → abstract
        → act<θ ⇒ on=0                   // oubli actif = désindexer
Adapter ← finetune(SEM_consolidé ⊎ générique) + EWC   // replay ≈ rêve
tests   : LongMemEval · LoCoMo · injection · régression
        → promote | rollback ; versionné // sommeil réversible
```

## Adhere — Ctx · W · garde

```
prefs   : réinjectées @fin ctx           // récence > position
dures   : validateur hors modèle         // 100 % ∉ attention
hiérarchie d'instructions (entraînée)
N sessions → PROC → Adapter              // consigne → habitude
valeurs > consignes                      // pas d'obéissance aveugle
```

## Calibrate — W

```
fait      → Index avant W                // vérifier ses notes
abstention entraînée                     // ne plus payer la devinette
detect    : entropie sémantique | sondes internes
verify    : sources ; citation ∈ réponse
```

## Invariants — ∀ couches

```
Log ⊇ Index ⊇ Adapter                    // vérité → projection → cache
oubli = désindexer ≠ détruire            // Funes : littéral + abstrait
∀ écriture ∈ Log : datée · attribuée · rejouable
plasticité ∝ surface d'attaque           // apprend vite de vous ⇒ de qui vous imite
adhésion 100 % ∈ gardes, ∉ modèle
état consolidé promu ⇔ tests verts
```

## Bio ↔ IA

| Biologie | Mécanisme IA | Statut |
|---|---|---|
| hippocampe (encodage rapide, épisodique) | Log + Index (EPI) | ✔ |
| néocortex (lent, sémantique, procédural) | W = Base ⊕ Adapter | ✔ |
| mémoire de travail (PFC) | Ctx — plus large, littéral, volatil | ✔ |
| interférence catastrophique → deux systèmes | Base figé + Adapter isolé, jetable | ✔ |
| sommeil, ondes SWR (rejeu hippocampe → cortex) | SLEEP : extract · merge · abstract · prune | ✔ |
| rêve (rejeu génératif) | replay SEM ⊎ générique + EWC → Adapter | ~ |
| homéostasie synaptique (oubli actif) | act = ln Σ tᵢ^(−d) ; act<θ ⇒ on=0 ; régularisation W | ✔ |
| amygdale (marquage émotionnel) | imp↑ : « retiens » · répétition · surprise · utilité | ~ |
| reconsolidation au rappel | merge-on-conflict ; ver++ ; deps | ✔ |
| oubli dirigé (suppression ≠ effacement) | on=0 + vue rédigée ; Log intact | ✔ |
| confabulation | hallucination(W) → route Index ; abstention entraînée | ~ |
| confusion des sources | label src · date · ver ; externe = donnée | ✔ |
| métamémoire (sentiment de savoir) | entropie sémantique · sondes internes | ~ |
| habitude > consigne | validateur hors modèle ; PROC → Adapter | ~ |
| Funes / Luria (tout retenir ⇒ ne plus penser) | Log littéral ⊇ Index abstrait | ✔ |
| stabilité ↔ plasticité | déplacé, non résolu ; plasticité ∝ attaque | ✖ |
| effacement dans les poids de base | SISA / RMU partiels — hors périmètre (aucun effacement) | ✖ |

✔ ingénierie, disponible · ~ recherche · ✖ ouvert / hors périmètre
