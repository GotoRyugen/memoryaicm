# Banc LoCoMo — reproduire les chiffres

Les résultats cités dans `../LISEZMOI-BANC-MEMOIRE.md` (hit@k sur 1 536 questions, 10 conversations)
sont dans `resultats_locomo.json` et `run_complet.log`. Pour les rejouer :

1. Récupérer le jeu de données (non redistribué ici, licence tierce) :
   https://github.com/snap-research/locomo — fichier `data/locomo10.json`, à placer dans ce dossier.
2. Depuis la racine du dépôt, avec le paquet installé (`pip install -e .`) :

   ```
   python bench/locomo/banc_locomo.py            # campagne complète : Echos livré / corrigé / BM25, par k et par catégorie
   python bench/locomo/verifier_patch.py 3       # vérification bout en bout par Index.retrieve, sur 3 conversations
   ```

Aucun LLM n'est appelé : la mesure isole la mémoire (le tour de preuve est-il dans le top-k ?).
`banc_locomo_phaseB.py` est l'ébauche de la phase B (génération + juge), qui exige un LLM ; elle
n'a pas été menée à terme et ses chiffres ne sont pas publiés.
