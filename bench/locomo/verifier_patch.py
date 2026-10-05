"""Vérification bout en bout : le CODE PATCHÉ (index.retrieve tel quel) donne-t-il bien
les chiffres mesurés par le banc ? On n'appelle plus la fonction du banc, on appelle la mémoire.

Usage : python3 verifier_patch.py [n_conversations]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MEMORYAICM_NO_DOTENV", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from banc_locomo import charger, ingerer, texte_memoire, BM25, KS  # noqa: E402

def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    jeu = Path(__file__).resolve().parent / "locomo10.json"
    convs = charger(jeu)[:n]
    tot = 0
    res = {k: {"hit": 0, "rec": 0.0, "chars": 0} for k in KS}
    ref = {k: {"hit": 0, "rec": 0.0} for k in KS}
    lat = []
    for i, c in enumerate(convs):
        j, ix, stats = ingerer(Path(__file__).resolve().parent / "_verif" / f"c{i}", c["tours"])
        cache = {f.id: ix.vector(f) for f in ix.all_facts(on_only=True)}
        ix.vector = lambda f: cache[f.id]                      # accélérateur de mesure seulement
        txt2dia = {}
        for t in c["tours"]:
            txt2dia.setdefault(texte_memoire(t), []).append(t["dia_id"])
        docs = [texte_memoire(t) for t in c["tours"]]
        dia = [t["dia_id"] for t in c["tours"]]
        bm = BM25(docs)
        for q in c["qa"]:
            ev = set(q["evidence"])
            t0 = time.time()
            top = ix.retrieve(q["question"], k=max(KS))          # <<< le vrai chemin de la mémoire
            lat.append(time.time() - t0)
            got = [set(txt2dia.get(f.txt, [])) for f, _, _ in top]
            size = [len(f.txt) for f, _, _ in top]
            idx = bm.top(q["question"], max(KS))
            gotb = [{dia[x]} for x in idx]
            for k in KS:
                r = set().union(*got[:k]) if got[:k] else set()
                res[k]["hit"] += int(bool(r & ev)); res[k]["rec"] += len(r & ev) / len(ev)
                res[k]["chars"] += sum(size[:k])
                rb = set().union(*gotb[:k]) if gotb[:k] else set()
                ref[k]["hit"] += int(bool(rb & ev)); ref[k]["rec"] += len(rb & ev) / len(ev)
            tot += 1
        j.close(); ix.close()
        print(f"  {c['id']} fait ({stats['ecrits']} faits, {len(c['qa'])} questions)")
    print(f"\n{len(convs)} conversations · {tot} questions · rappel median "
          f"{1000 * sorted(lat)[len(lat)//2]:.0f} ms")
    print(f"{'k':>4} {'memoire hit':>12} {'memoire rec':>12} {'BM25 hit':>10} {'car./rappel':>12}")
    for k in KS:
        print(f"{k:>4} {res[k]['hit']/tot:>12.3f} {res[k]['rec']/tot:>12.3f} "
              f"{ref[k]['hit']/tot:>10.3f} {res[k]['chars']/tot:>12.0f}")
    (Path(__file__).resolve().parent / "resultats_patch.json").write_text(json.dumps(
        {"questions": tot, "memoire": {k: {m: v / tot for m, v in res[k].items()} for k in KS},
         "bm25": {k: {m: v / tot for m, v in ref[k].items()} for k in KS}},
        ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
