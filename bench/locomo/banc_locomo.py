"""Banc LoCoMo — mesure de la mémoire d'Echos (memoryaicm) contre un BM25 de référence.

Phase A : récupération pure, sans LLM, sans juge. Métrique objective et rejouable.

Protocole
---------
1. Chaque conversation LoCoMo (10 conversations, 300–690 tours, jusqu'à 19 sessions
   étalées sur des mois) est rejouée tour par tour dans un dépôt memoryaicm neuf.
   Chaque tour est écrit avec SA VRAIE DATE (les sessions sont horodatées), donc la
   décroissance ACT-R et les questions temporelles voient le temps réel du jeu.
2. Pour chaque question, on interroge l'index (index.retrieve, sans effet de bord :
   on ne veut pas qu'une question renforce les faits pour la suivante).
3. On regarde si les tours de preuve (`evidence`, p. ex. « D2:8 ») sont dans le top-k.

Métriques (aucun juge LLM, donc aucune subjectivité) :
    hit@k        : au moins un tour de preuve dans le top-k
    recall@k     : part des tours de preuve retrouvés
    chars@k      : taille du contexte rendu (comparable au « tokens per retrieval » publié)

Référence : BM25 (Robertson/Okapi, k1=1.5, b=0.75) sur exactement les mêmes tours,
mêmes questions, même k. C'est la comparaison qui dit si l'index d'Echos vaut mieux
qu'un moteur lexical classique, ou non.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("MEMORYAICM_NO_DOTENV", "1")
os.environ.setdefault("MEMORYAICM_BACKEND", "stub")
os.environ.setdefault("MEMORYAICM_EMBEDDER", "hash")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memoryaicm import model as M                      # noqa: E402
from memoryaicm.config import Settings                 # noqa: E402
from memoryaicm.index import Index                     # noqa: E402
from memoryaicm.log import Journal                     # noqa: E402
from memoryaicm.embed import HashingEmbedder           # noqa: E402
from memoryaicm.policy import WritePolicy              # noqa: E402
from memoryaicm.llm.base import Candidate              # noqa: E402
from memoryaicm.llm.stub import StubBackend            # noqa: E402
from memoryaicm.textutil import tokens                 # noqa: E402

SESSION_RE = re.compile(r"^session_(\d+)$")
DATE_RE = re.compile(r"(?:(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+)?(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.I)
MOIS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1)}


# --------------------------------------------------------------------------- jeu de données
def horodate(s: str) -> float:
    """« 1:56 pm on 8 May, 2023 » -> epoch. Sans heure : midi."""
    m = DATE_RE.search(s or "")
    if not m:
        return time.time()
    hh, mm, ap, jour, mois, annee = m.groups()
    h = int(hh) if hh else 12
    if ap and ap.lower() == "pm" and h < 12:
        h += 12
    if ap and ap.lower() == "am" and h == 12:
        h = 0
    import calendar
    return calendar.timegm((int(annee), MOIS.get(mois.lower(), 1), int(jour), h, int(mm or 0), 0, 0, 0, 0))


def charger(chemin: Path) -> list[dict]:
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    sorties = []
    for ech in brut:
        conv = ech["conversation"]
        nums = sorted(int(SESSION_RE.match(k).group(1)) for k in conv if SESSION_RE.match(k))
        tours = []
        for n in nums:
            ts = horodate(conv.get(f"session_{n}_date_time", ""))
            date = (conv.get(f"session_{n}_date_time") or "").strip()
            for i, t in enumerate(conv[f"session_{n}"]):
                txt = (t.get("text") or "").strip()
                if not txt:
                    continue
                tours.append({"dia_id": t.get("dia_id") or f"D{n}:{i+1}", "speaker": t.get("speaker", "?"),
                              "text": txt, "session": n, "ts": ts + i, "date": date})
        qa = [q for q in ech["qa"] if q.get("category") != 5 and q.get("evidence")]
        sorties.append({"id": ech.get("sample_id", "?"), "tours": tours, "qa": qa,
                        "speakers": [conv.get("speaker_a"), conv.get("speaker_b")]})
    return sorties


def texte_memoire(t: dict) -> str:
    """Ce qui est réellement mis en mémoire : la date de session + qui parle + ce qui est dit."""
    return f"{t['date']} — {t['speaker']}: {t['text']}"


# --------------------------------------------------------------------------- BM25 de référence
class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokens(d) for d in docs]
        self.n = len(self.docs)
        self.avgdl = sum(len(d) for d in self.docs) / max(1, self.n)
        df = Counter()
        for d in self.docs:
            df.update(d)
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def top(self, query: str, k: int) -> list[int]:
        q = tokens(query)
        scores = []
        for i, d in enumerate(self.docs):
            dl = len(d) or 1
            s = 0.0
            for t in q:
                if t in d:
                    s += self.idf.get(t, 0.0) * (self.k1 + 1) / (1 + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: -x[0])
        return [i for _, i in scores[:k]]


# --------------------------------------------------------------------------- ingestion memoryaicm
def ingerer(racine: Path, tours: list[dict], autonomie: int = 2) -> tuple[Journal, Index, dict]:
    if racine.exists():
        shutil.rmtree(racine)
    s = Settings(root=racine, autonomy=autonomie, embedder="hash", backend="stub")
    s.ensure_dirs()
    j = Journal(s.journal_path)
    ix = Index(s.index_path, j, s, embedder=HashingEmbedder(dim=s.embed_dim))
    pol = WritePolicy(j, ix, StubBackend(), s)
    t0 = time.time()
    ecrits = renforces = fusionnes = 0
    refus: dict[str, int] = {}
    for t in tours:
        txt = texte_memoire(t)
        sid = f"s{t['session']}"
        ev = j.append(M.EV_TURN_USER, M.Actor.USER.value, {"session": sid, "text": txt}, ts=t["ts"])
        cand = Candidate(txt=txt, subject=f"locomo:{t['speaker'].lower()}", kind="SEM", src="USER",
                         exclusive=False, durable=True, sens=False, explicit=False)
        rep = pol.from_candidates([cand], session=sid, origin=ev.id, ts=t["ts"], ground_in=txt)
        ecrits += len(rep.written); renforces += len(rep.reinforced); fusionnes += len(rep.merged)
        for _c, why in rep.skipped:
            refus[why] = refus.get(why, 0) + 1
        ix.sync()
    ing = time.time() - t0
    return j, ix, {"ecrits": ecrits, "renforces": renforces, "fusionnes": fusionnes, "refuses": refus,
                   "tours": len(tours), "ingestion_s": round(ing, 1)}


# --------------------------------------------------------------------------- mesure
KS = (1, 3, 5, 8, 10, 20, 30)

# Configurations comparées. « Echos » = les réglages livrés (config.py).
CONFIGS = {
    "echos":      {"w_rel": 1.0, "w_act": 0.35, "w_imp": 0.5},                       # tel que livre
    "rel_seule":  {"w_rel": 1.0, "w_act": 0.0,  "w_imp": 0.0},
    "cidf_repli": {"w_rel": 1.0, "w_act": 0.35, "w_imp": 0.5, "idf": "couverture", "combi": "repli"},
    "cidf_r_a15": {"w_rel": 1.0, "w_act": 0.15, "w_imp": 0.15, "idf": "couverture", "combi": "repli"},
}


class LexIDF:
    """Recouvrement de la question par le fait, chaque mot pondere par son idf, sature en tf (BM25).

    rel = Somme_{t dans q inter d} idf_t * tf_sat(t,d) / Somme_{t dans q} idf_t   dans [0,1]
    C'est exactement `overlap` quand tous les idf sont egaux et tf_sat = 1 : une generalisation.
    """

    def __init__(self, docs: list[set], k1: float = 1.5, b: float = 0.75):
        from collections import Counter as _C
        self.k1, self.b = k1, b
        self.n = max(1, len(docs))
        self.avgdl = sum(len(d) for d in docs) / self.n
        df = _C()
        for d in docs:
            df.update(d)
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.defaut = math.log(1 + (self.n + 0.5) / 0.5)

    def couverture(self, q: set, d: set) -> float:
        """Meme forme que `overlap` (part de la question couverte), mais chaque mot pese son idf."""
        if not q or not d:
            return 0.0
        den = sum(self.idf.get(t, self.defaut) for t in q)
        if not den:
            return 0.0
        return sum(self.idf.get(t, self.defaut) for t in q if t in d) / den

    def rel(self, q: set, d: set) -> float:
        if not q or not d:
            return 0.0
        dl = len(d) or 1
        sat = (self.k1 + 1) / (1 + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        num = sum(self.idf.get(t, self.defaut) * sat for t in q if t in d)
        den = sum(self.idf.get(t, self.defaut) * (self.k1 + 1) for t in q)
        return min(1.0, num / den) if den else 0.0


def _vide(ks=KS):
    return {k: {"hit": 0, "rec": 0.0, "chars": 0} for k in ks}


def _cumul(res, k, ret, ev_set, chars):
    res[k]["hit"] += int(bool(ret & ev_set))
    res[k]["rec"] += len(ret & ev_set) / len(ev_set)
    res[k]["chars"] += chars


def rechercher(ix, query, kmax, lex=True, sem=True, lexidf=None, combi="max"):
    """index.retrieve, avec la possibilité de couper une des deux voies pour le diagnostic."""
    from memoryaicm.textutil import tokens as _tk, overlap as _ov, jaccard as _ja
    q = _tk(query)
    qv = ix.embedder.embed(query) if (sem and query.strip()) else []
    out = []
    for f in ix.all_facts(on_only=True, kinds=("SEM", "EPI")):
        doc = _tk(f.txt + " " + f.subject.replace(":", " "))
        if not lex:
            l = 0.0
        elif lexidf is not None:
            l = lexidf[0].rel(q, doc) if lexidf[1] == "bm25" else lexidf[0].couverture(q, doc)
        else:
            l = max(_ov(q, doc), _ja(q, doc))
        sv = ix.semantic(qv, f) if qv else 0.0
        rel = (l if l > 0 else sv) if combi == "repli" else max(l, sv)
        if rel < ix.s.min_rel:
            continue
        score = ix.s.w_rel * rel + ix.s.w_act * ix.act_norm(f.id) + ix.s.w_imp * f.imp
        out.append((f, score, rel))
    out.sort(key=lambda x: (-x[1], x[0].t_upd))
    return out[:kmax]


def mesurer(conv: dict, racine: Path, verbose: bool = True) -> dict:
    tours = conv["tours"]
    j, ix, stats = ingerer(racine, tours)

    # accélérateur de mesure seulement : les vecteurs en RAM (même calcul, pas de relecture SQLite)
    cache = {f.id: ix.vector(f) for f in ix.all_facts(on_only=True)}
    ix.vector = lambda f: cache[f.id]                     # type: ignore[assignment]
    faits = list(ix.all_facts(on_only=True))
    ix.all_facts = lambda on_only=True, kinds=None: faits  # type: ignore[assignment]
    act = {f.id: ix.act_norm(f.id) for f in faits}
    ix.act_norm = lambda fid, now=None: act[fid]           # type: ignore[assignment]

    txt2dia = {}
    for t in tours:
        txt2dia.setdefault(texte_memoire(t), []).append(t["dia_id"])
    fait2dia = {f.id: set(txt2dia.get(f.txt, [])) for f in faits}

    docs = [texte_memoire(t) for t in tours]
    dia = [t["dia_id"] for t in tours]
    bm = BM25(docs)
    from memoryaicm.textutil import tokens as _tk0
    lexidf = LexIDF([_tk0(f.txt + " " + f.subject.replace(":", " ")) for f in faits])

    kmax = max(KS)
    res = {nom: _vide() for nom in CONFIGS}
    res["bm25"] = _vide()
    par_cat = {}
    lat = {}
    for nom, cfg in CONFIGS.items():
        ix.s.w_rel, ix.s.w_act, ix.s.w_imp = cfg["w_rel"], cfg["w_act"], cfg["w_imp"]
        lex, sem = cfg.get("lex", True), cfg.get("sem", True)
        lidf = (lexidf, cfg["idf"]) if cfg.get("idf") else None
        t1 = time.time()
        for q in conv["qa"]:
            ev_set = set(q["evidence"])
            top = rechercher(ix, q["question"], kmax, lex=lex, sem=sem, lexidf=lidf, combi=cfg.get("combi", "max"))
            obtenus = [fait2dia.get(f.id, set()) for f, _, _ in top]
            tailles = [len(f.txt) for f, _, _ in top]
            for k in KS:
                ret = set().union(*obtenus[:k]) if obtenus[:k] else set()
                _cumul(res[nom], k, ret, ev_set, sum(tailles[:k]))
                if nom == "echos":
                    pc = par_cat.setdefault(q.get("category"), {k2: [0, 0] for k2 in KS})
                    pc[k][0] += int(bool(ret & ev_set)); pc[k][1] += 1
        lat[nom] = 1000 * (time.time() - t1) / max(1, len(conv["qa"]))

    for q in conv["qa"]:
        ev_set = set(q["evidence"])
        idx = bm.top(q["question"], kmax)
        obt = [{dia[i]} for i in idx]
        tail = [len(docs[i]) for i in idx]
        for k in KS:
            ret = set().union(*obt[:k]) if obt[:k] else set()
            _cumul(res["bm25"], k, ret, ev_set, sum(tail[:k]))

    n = len(conv["qa"])
    j.close(); ix.close()
    out = {"conv": conv["id"], "n_questions": n, **stats,
           "latence_ms": {nom: round(v, 1) for nom, v in lat.items()},
           **{nom: {k: {"hit": r[k]["hit"] / n, "rec": r[k]["rec"] / n, "chars": r[k]["chars"] / n} for k in KS}
              for nom, r in res.items()},
           "par_categorie": {str(c): {str(k): {"hit": v[k][0] / max(1, v[k][1]), "n": v[k][1]} for k in KS}
                             for c, v in par_cat.items()}}
    if verbose:
        print(f"  {conv['id']}: {n} questions · {stats['ecrits']} faits ({stats['renforces']} redites,"
              f" {stats['fusionnes']} fusions, refus {stats['refuses'] or 'aucun'}) · ingestion {stats['ingestion_s']}s")
        for nom in list(CONFIGS) + ["bm25"]:
            print(f"      {nom:<10} hit@5 {out[nom][5]['hit']:.3f}  hit@10 {out[nom][10]['hit']:.3f}  "
                  f"hit@30 {out[nom][30]['hit']:.3f}  rec@10 {out[nom][10]['rec']:.3f}")
    return out


def main() -> None:
    ici = Path(__file__).resolve().parent
    jeu = Path(sys.argv[1]) if len(sys.argv) > 1 else ici / "locomo10.json"
    limite = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    convs = charger(jeu)[:limite]
    print(f"LoCoMo : {len(convs)} conversations, {sum(len(c['tours']) for c in convs)} tours, "
          f"{sum(len(c['qa']) for c in convs)} questions (categorie 5 « adversarial » exclue)")
    tmp = ici / "_stores"
    tous = [mesurer(c, tmp / f"c{i}") for i, c in enumerate(convs)]
    tot = sum(t["n_questions"] for t in tous)
    agg = {"conversations": len(tous), "questions": tot}
    for nom in list(CONFIGS) + ["bm25"]:
        agg[nom] = {k: {"hit": sum(t[nom][k]["hit"] * t["n_questions"] for t in tous) / tot,
                        "rec": sum(t[nom][k]["rec"] * t["n_questions"] for t in tous) / tot,
                        "chars": sum(t[nom][k]["chars"] * t["n_questions"] for t in tous) / tot} for k in KS}
    cats = {}
    for t in tous:
        for c, v in t["par_categorie"].items():
            d = cats.setdefault(c, {k: [0.0, 0] for k in KS})
            for k in KS:
                d[k][0] += v[str(k)]["hit"] * v[str(k)]["n"]; d[k][1] += v[str(k)]["n"]
    agg["par_categorie"] = {c: {str(k): {"hit": d[k][0] / max(1, d[k][1]), "n": d[k][1]} for k in KS}
                            for c, d in cats.items()}
    print("\n=== AGREGE ===")
    print(f"{agg['conversations']} conversations · {tot} questions")
    entete = f"{'k':>4}" + "".join(f"{nom:>12}" for nom in list(CONFIGS) + ["bm25"])
    print(entete + "     (hit@k)")
    for k in KS:
        print(f"{k:>4}" + "".join(f"{agg[nom][k]['hit']:>12.3f}" for nom in list(CONFIGS) + ["bm25"]))
    print("\npar categorie (hit@10) — 1 multi-saut · 2 temporel · 3 ouvert · 4 simple")
    for c in sorted(agg["par_categorie"]):
        d = agg["par_categorie"][c]["10"]
        print(f"   cat {c} : {d['hit']:.3f}  ({d['n']} questions)")
    (ici / "resultats_locomo.json").write_text(
        json.dumps({"aggrege": agg, "par_conversation": tous}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\necrit : resultats_locomo.json")


if __name__ == "__main__":
    main()
