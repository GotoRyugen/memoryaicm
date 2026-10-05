"""ADAPTER · W = Base ⊕ Adapter(u) · Adapter = compile(Index), régénérable, jetable.

Ce module produit l'artefact « compilé » depuis l'index actif :
  adapter/vN/profile.md    préfixe système distillé (le cache compilé, utilisable sans entraînement)
  adapter/vN/train.jsonl   paires d'entraînement (SEM/PROC ⊎ échantillons génériques) pour une étape LoRA externe
  adapter/vN/manifest.json empreinte de l'index, faits inclus, résultat des tests
  adapter/CURRENT          version promue (pointeur) — promote | rollback, journalisés

Un fait oublié (on=0) n'est jamais dans une compilation : « Adapter ← compile(Index) ⇒ x ∉ W ».
La régularisation type EWC et l'entraînement réel sont hors de ce dépôt : train.jsonl en est l'entrée.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import model as M
from .config import Settings
from .index import Index
from .log import Journal

GENERIC_SAMPLES = [
    ("Explique en deux phrases ce qu'est une table de hachage.",
     "Une table de hachage associe des clés à des valeurs en calculant, à partir de chaque clé, l'indice d'un emplacement. En moyenne, insertion et recherche se font en temps constant."),
    ("Traduis « bonjour » en anglais.", "« Hello »."),
    ("Quelle est la différence entre une liste et un tuple en Python ?",
     "Une liste est modifiable, un tuple ne l'est pas ; un tuple peut donc servir de clé de dictionnaire."),
    ("Résume en une phrase le principe d'un journal append-only.",
     "On n'ajoute qu'en fin de journal, on ne modifie ni ne supprime jamais une ligne existante."),
    ("Donne un exemple de commande shell qui liste les fichiers.", "`ls -la` sous Unix, `dir` sous Windows."),
]


class Adapter:
    def __init__(self, settings: Settings, journal: Journal, index: Index):
        self.s, self.journal, self.index = settings, journal, index
        self.dir: Path = settings.adapter_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ état
    def versions(self) -> list[int]:
        out = []
        for p in self.dir.glob("v*"):
            if p.is_dir() and p.name[1:].isdigit():
                out.append(int(p.name[1:]))
        return sorted(out)

    def current(self) -> int | None:
        p = self.dir / "CURRENT"
        if not p.exists():
            return None
        v = p.read_text(encoding="utf-8").strip()
        return int(v) if v.isdigit() else None

    def profile(self) -> str | None:
        v = self.current()
        if v is None:
            return None
        p = self.dir / f"v{v}" / "profile.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def manifest(self, v: int) -> dict:
        p = self.dir / f"v{v}" / "manifest.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    # ------------------------------------------------------------------ compile
    def compile(self, reason: str = "sleep") -> int:
        v = (self.versions() or [0])[-1] + 1
        out = self.dir / f"v{v}"
        out.mkdir(parents=True, exist_ok=True)
        facts = self.index.all_facts(on_only=True, kinds=("SEM", "PROC"))
        prefs = [f for f in facts if f.kind == M.Kind.PROC]
        sems = [f for f in facts if f.kind == M.Kind.SEM]

        lines = ["# Profil compilé — dérivé de l'index, régénérable", ""]
        lines.append("## Préférences (PROC)")
        lines += [f"- {f.txt}  {f.label()}" for f in prefs] or ["- (aucune)"]
        lines += ["", "## Faits (SEM)"]
        lines += [f"- {f.txt}  {f.label()}" for f in sorted(sems, key=lambda f: -f.imp)] or ["- (aucun)"]
        (out / "profile.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        with (out / "train.jsonl").open("w", encoding="utf-8") as fh:
            gi = 0
            for f in facts:
                q = _question_for(f)
                fh.write(json.dumps({"messages": [{"role": "user", "content": q},
                                                  {"role": "assistant", "content": f"{f.txt} {f.label()}"}],
                                     "fact_id": f.id}, ensure_ascii=False) + "\n")
                for _ in range(self.s.generic_ratio):  # replay : intercaler du générique contre l'oubli
                    gq, ga = GENERIC_SAMPLES[gi % len(GENERIC_SAMPLES)]
                    gi += 1
                    fh.write(json.dumps({"messages": [{"role": "user", "content": gq},
                                                      {"role": "assistant", "content": ga}], "generic": True},
                                        ensure_ascii=False) + "\n")

        manifest = {
            "version": v, "created": time.time(), "reason": reason, "index_hash": self.index.state_hash(),
            "n_facts": len(facts), "fact_ids": [f.id for f in facts], "tests": None, "promoted": False,
            "training": "train.jsonl → étape LoRA externe (EWC/replay : générique intercalé)",
        }
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.journal.append(M.EV_ADAPTER_COMPILE, M.Actor.SYSTEM.value,
                            {"version": v, "reason": reason, "index_hash": manifest["index_hash"], "n_facts": len(facts)})
        return v

    # ------------------------------------------------------------------ promote / rollback
    def promote(self, v: int, tests: dict | None = None) -> None:
        m = self.manifest(v)
        m["tests"], m["promoted"] = tests, True
        (self.dir / f"v{v}" / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        prev = self.current()
        (self.dir / "CURRENT").write_text(str(v), encoding="utf-8")
        self.journal.append(M.EV_ADAPTER_PROMOTE, M.Actor.SYSTEM.value, {"version": v, "previous": prev, "tests": tests})

    def rollback(self, failed_version: int, reason: str, tests: dict | None = None) -> int | None:
        """Garde la version promue précédente ; la version en échec reste sur disque, marquée."""
        m = self.manifest(failed_version)
        m["tests"], m["promoted"], m["rejected"] = tests, False, reason
        (self.dir / f"v{failed_version}" / "manifest.json").write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        cur = self.current()
        self.journal.append(M.EV_ADAPTER_ROLLBACK, M.Actor.SYSTEM.value,
                            {"from": failed_version, "to": cur, "reason": reason, "tests": tests})
        return cur


def _question_for(f: M.Fact) -> str:
    subj = f.subject.split(":")[0]
    return {
        "nom": "Comment je m'appelle ?", "ville": "Où j'habite ?", "travail": "Où est-ce que je travaille ?",
        "éditeur": "Quel est mon éditeur ?", "outil": "Quels outils j'utilise ?", "pref": "Comment je préfère que tu répondes ?",
        "consigne": "Quelle consigne t'ai-je donnée ?", "décision": "Qu'est-ce qu'on a décidé ?",
    }.get(subj, f"Que sais-tu sur « {f.subject} » me concernant ?")
