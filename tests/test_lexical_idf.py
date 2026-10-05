"""READ · pertinence lexicale pondérée par l'information (idf) et sémantique en RECOURS.

Mesuré sur LoCoMo (10 conversations, 5 882 tours, 1 536 questions) :
    recouvrement plat + max(lex, sem)   hit@10 = 0.553   hit@1 = 0.264
    idf + sémantique en recours         hit@10 = 0.607   hit@1 = 0.340
    BM25 (référence 1994)               hit@10 = 0.602   hit@1 = 0.298
Ces tests fixent les deux propriétés qui produisent l'écart, pour qu'elles ne repartent pas.
"""

from __future__ import annotations

from memoryaicm import model as M
from memoryaicm.config import Settings
from memoryaicm.index import Index
from memoryaicm.log import Journal
from memoryaicm.textutil import tokens


def _poser(j: Journal, ix: Index, txt: str, subject: str) -> M.Fact:
    f = M.Fact(id=M.new_id("f"), kind=M.Kind.SEM, txt=txt, subject=subject, src=M.Src.USER,
               t0=M.now(), t_upd=M.now(), imp=0.5, on=True, exclusive=False)
    ix.apply(j.append(M.EV_FACT_WRITE, M.Actor.USER.value, {"fact": f.to_dict()}))
    return f


def _memoire(tmp_path, textes: list[tuple[str, str]]) -> tuple[Journal, Index]:
    s = Settings(root=tmp_path)
    s.ensure_dirs()
    j = Journal(s.journal_path)
    ix = Index(s.index_path, j, s)
    for txt, sub in textes:
        _poser(j, ix, txt, sub)
    return j, ix


def test_le_mot_rare_pese_plus_que_le_mot_banal(tmp_path):
    """« semaine » est partout, « Kolkata » nulle part : la question doit ramener Kolkata."""
    banal = [(f"cette semaine j'ai vu un film numero {i}", f"film:{i}") for i in range(20)]
    banal.append(("cette semaine je suis alle a Kolkata", "voyage:kolkata"))
    j, ix = _memoire(tmp_path, banal)
    top = ix.retrieve("semaine Kolkata", k=1)
    assert top and top[0][0].subject == "voyage:kolkata"
    j.close(); ix.close()


def test_sous_le_seuil_lidf_ne_sapplique_pas(tmp_path):
    """Avec une poignee de faits, l'idf n'a aucun sens statistique : on garde le recouvrement."""
    j, ix = _memoire(tmp_path, [("j'utilise neovim", "editeur"), ("j'habite a Lyon", "ville")])
    assert len(ix.all_facts(on_only=True)) < Settings().idf_min_facts
    assert ix._idf_table() is None
    q, d = tokens("quel editeur ?"), tokens("j'utilise neovim editeur")
    from memoryaicm.textutil import overlap, jaccard
    assert ix.lexical(q, d) == max(overlap(q, d), jaccard(q, d))
    j.close(); ix.close()


def test_le_semantique_est_un_recours_pas_un_concurrent(tmp_path):
    """Un fait qui contient les mots de la question passe devant un fait seulement 'proche'."""
    textes = [(f"note de remplissage numero {i} sur des sujets varies", f"pad:{i}") for i in range(15)]
    textes += [("le serveur de production tombe le vendredi soir", "incident:vendredi"),
               ("les serveurs de preproduction redemarrent le samedi matin", "incident:samedi")]
    j, ix = _memoire(tmp_path, textes)
    top = ix.retrieve("production vendredi", k=1)
    assert top and top[0][0].subject == "incident:vendredi"
    j.close(); ix.close()


def test_la_table_idf_se_refait_quand_lindex_change(tmp_path):
    """Log ⊇ Index : un fait ajoute change les frequences, donc la table doit etre invalidee."""
    j, ix = _memoire(tmp_path, [(f"fait numero {i} banal", f"s:{i}") for i in range(15)])
    avant = dict(ix._idf_table() or {})
    assert avant
    _poser(j, ix, "un fait avec le mot rarissime zzyzx", "s:rare")
    apres = ix._idf_table() or {}
    assert "zzyzx" in apres and apres != avant
    j.close(); ix.close()
