"""JOURNAL · la chaîne doit tenir quel que soit le TYPE de l'horodatage fourni.

La colonne `ts` est REAL. Un appelant qui passe un entier (int(time.time()), une date
analysée, une archive rejouée) faisait hacher « 1683553000 » puis relire « 1683553000.0 » :
l'événement devenait invérifiable, et comme le journal est append-only, définitivement.
Trouvé en rejouant LoCoMo avec les vraies dates des sessions (9 septembre 2026).
"""

from __future__ import annotations

import time

from memoryaicm import model as M
from memoryaicm.log import Journal


def test_horodatage_entier_ne_casse_pas_la_chaine(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": "avec un entier"}, ts=1683553000)
    ok, msg = j.verify()
    assert ok, msg
    j.close()


def test_horodatages_meles_entiers_flottants_none(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": "un"}, ts=1683553000)
    j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": "deux"}, ts=1683553001.5)
    j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": "trois"})
    j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": "quatre"}, ts=True and 1683553003)
    ok, msg = j.verify()
    assert ok, msg
    assert len(j) == 4
    j.close()


def test_le_texte_ressort_octet_pour_octet(tmp_path):
    """Accents, guillemets, antislash, sauts de ligne, emoji : rien ne doit bouger."""
    textes = ["café « déjà » — 100 %", 'guillemets "doubles" et \\antislash\\',
              "deux\nlignes\tet\ttabulations", "emoji 🧠 et ideogrammes 漢字", " " * 3 + "espaces  "]
    j = Journal(tmp_path / "j.sqlite")
    for i, t in enumerate(textes):
        j.append(M.EV_TURN_USER, M.Actor.USER.value, {"text": t}, ts=1683553000 + i)
    relus = [ev.payload["text"] for ev in j.replay(types=(M.EV_TURN_USER,))]
    assert relus == textes
    assert j.verify()[0]
    j.close()
