"""Normalisation de texte sans dépendance : accents, casse, mots vides, racines courtes.

Sert à la pertinence (READ), au dédoublonnage (SLEEP) et à la nouveauté (surprise).
Un backend d'embeddings peut remplacer `similarity` sans toucher au reste.
"""

from __future__ import annotations

import re
import unicodedata

_STOP = set(
    """
    le la les un une des du de d l au aux et ou où mais donc or ni car que qui quoi dont
    ce cet cette ces se sa son ses mon ma mes ton ta tes notre nos votre vos leur leurs
    je tu il elle on nous vous ils elles me te lui y en ne pas plus moins très tres est
    suis es sommes etes êtes sont ai as a avons avez ont été ete etre être avoir fait faire
    pour par avec sans sur sous dans chez vers entre comme si oui non c ça ca cela ceci
    the a an of to in on at for and or but is are was were be been i you he she it we they
    my your his her its our their this that these those with without from as by not no yes
    """.split()
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_\-\.]*", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(s: str) -> str:
    return strip_accents(s).lower().strip()


def stem(tok: str) -> str:
    # racine très courte : suffit pour « éditeurs/éditeur », « préfère/préférée »
    for suf in ("ements", "ement", "ees", "ee", "es", "s", "e"):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


# Synonymes FR/EN ramenés à la racine canonique des notes : « quel IDE ? » retrouve « éditeur : neovim ».
_SYN = {
    "ide": "editeur", "editor": "editeur", "edit": "editeur",
    "logiciel": "outil", "software": "outil", "tool": "outil", "programm": "outil", "app": "outil", "appli": "outil",
    "prenom": "nom", "name": "nom", "appell": "nom", "call": "nom",
    "city": "ville", "town": "ville", "habit": "ville", "vit": "ville", "vis": "ville", "vivr": "ville", "live": "ville",
    "resid": "ville", "demeur": "ville", "adress": "ville",
    "repond": "repons", "reponds": "repons", "answer": "repons", "reply": "repons",
    "job": "travail", "work": "travail", "boulot": "travail", "metier": "travail", "entrepris": "travail",
    "societ": "travail", "compani": "travail", "employeur": "travail",
    "pref": "prefer", "aim": "prefer", "like": "prefer", "veu": "prefer", "want": "prefer",
    "decid": "decision", "choix": "decision", "choisi": "decision", "chose": "decision", "opt": "decision",
    "langage": "langu", "language": "langu", "langue": "langu",
}


def tokens(s: str) -> set[str]:
    out = set()
    for t in _WORD.findall(normalize(s)):
        t = t.strip("._-")
        if len(t) < 2 or t in _STOP:
            continue
        r = stem(t)
        out.add(r)
        syn = _SYN.get(r) or _SYN.get(t)
        if syn:
            out.add(syn)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a | b))


def overlap(query: set[str], doc: set[str]) -> float:
    """Part de la requête couverte par le document : mieux que Jaccard pour une question courte."""
    if not query or not doc:
        return 0.0
    return len(query & doc) / float(len(query))


def similarity(a: str, b: str) -> float:
    return jaccard(tokens(a), tokens(b))


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", normalize(s)).strip("-")
    return s[:n] or "x"
