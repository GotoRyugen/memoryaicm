#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Banc mémoire LoCoMo — PHASE B : récupération -> génération LLM -> jugement.

Chaîne complète, comme les publications (mem0/Zep), mais 100 % local :
  1. RETRIEVE : Index.retrieve(question) -> top-k notes (la mémoire d'Echos, code installé)
  2. GENERATE : un LLM local (Ollama) répond à partir des seules notes ramenées
  3. JUDGE    : deux notes indépendantes de la réponse
       - juge LLM local : correct / incorrect (proxy du juge GPT-4o des publications)
       - métriques auto sans juge : exact normalisé, F1 de tokens, « contient la réponse-or »

Tout est journalisé et sauvegardé au fil de l'eau (checkpoint tous les CKPT), donc un arrêt ne
perd rien et l'avancement est lisible en direct dans le .log et le .json.

Lancer :  python banc_locomo_phaseB.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import statistics
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mem.config import Settings        # noqa: E402
from mem.log import Journal            # noqa: E402
from mem.index import Index            # noqa: E402
from mem import model as M             # noqa: E402
from mem.textutil import tokens        # noqa: E402

DATA = HERE / "locomo10.json"
OUT = HERE / "resultats_phaseB.json"
LOG = HERE / "phaseB.log"
OLLAMA = "http://127.0.0.1:11434"
K_CONTEXT = 10                 # notes ramenées données au LLM
CKPT = 25                      # sauvegarde toutes les N questions
CATLABEL = {1: "multi-saut", 2: "temporel", 3: "ouvert/bon-sens", 4: "fait-simple"}


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + str(msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


# ------------------------------------------------------------------ Ollama
def ollama_tags() -> list[str]:
    try:
        r = urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10)
        d = json.loads(r.read())
        return [m["name"] for m in d.get("models", [])]
    except Exception as e:  # noqa: BLE001
        log(f"Ollama /api/tags injoignable : {e!r}")
        return []


def pick_models(tags: list[str]) -> tuple[str, str]:
    """(génération, juge). Préférences : gpt-oss > qwen > llama3 > premier venu ; juge = gpt-oss si présent."""
    def find(*prefs):
        for p in prefs:
            for t in tags:
                if p in t.lower():
                    return t
        return tags[0] if tags else ""
    # 12 Go de VRAM : gpt-oss (20B) déborde sur le CPU (~1 min/appel, 500 sporadiques). llama3 (8B)
    # tient sur la carte et reste stable — et c'est la classe de taille du vrai moteur d'Echos (Qwen3-8B).
    gen = find("llama3", "qwen", "llama", "gpt-oss")
    judge = find("llama3", "qwen", "llama", "gpt-oss")
    return gen, judge


def ollama_gen(model: str, prompt: str, timeout: float = 180.0, num_predict: int = 160) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.0, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    last = None
    for _ in range(2):                     # un 500 sporadique d'Ollama ne doit pas perdre la question
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(r.read()).get("response", "").strip()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0)
    raise last


# ------------------------------------------------------------------ dates & ingestion (idem phase A)
def parse_date(s: str) -> float:
    s = (s or "").strip()
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?\s*on\s*(\d{1,2})\s+([A-Za-z]+),?\s*(\d{4})", s, re.I)
    months = {mn: i for i, mn in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
    if not m:
        return time.mktime(time.strptime("2023-01-01", "%Y-%m-%d"))
    hh, mm, ap, dd, mon, yy = m.groups()
    hh = int(hh) % 12
    if (ap or "").lower() == "pm":
        hh += 12
    return time.mktime((int(yy), months.get(mon[:3].lower(), 1), int(dd), hh, int(mm), 0, 0, 0, -1))


def sessions_in_order(conv: dict):
    out = []
    for k in conv:
        m = re.fullmatch(r"session_(\d+)", k)
        if m:
            n = int(m.group(1))
            out.append((n, parse_date(conv.get(f"session_{n}_date_time", "")), conv[k]))
    out.sort(key=lambda x: x[0])
    return out


def build_index(conv: dict, tmp: Path):
    jr = Journal(tmp / "journal.sqlite")
    ix = Index(tmp / "index.sqlite", jr, Settings(root=tmp))
    last_ts = 0.0
    dates: dict[str, str] = {}
    for n, ts, turns in sessions_in_order(conv):
        last_ts = max(last_ts, ts)
        ds = time.strftime("%Y-%m-%d", time.localtime(ts))
        for t in turns:
            did = t.get("dia_id")
            if not did:
                continue
            txt = f"{t.get('speaker','')}: {t.get('text','')}".strip()
            if t.get("blip_caption"):
                txt += f" [image: {t['blip_caption']}]"
            dates[did] = ds
            f = M.Fact(id=did, kind=M.Kind.EPI, txt=txt, subject=did, src=M.Src.USER,
                       t0=ts, t_upd=ts, exclusive=False, session=f"s{n}")
            jr.append(M.EV_FACT_WRITE, M.Actor.USER.value, {"fact": f.to_dict()}, ts=ts)
    ix.sync()
    return ix, last_ts + 86400.0, dates


# ------------------------------------------------------------------ métriques auto
_ARTICLES = {"the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "le", "la", "les", "un", "une", "de", "des"}


def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", str(s).lower())
    return " ".join(w for w in s.split() if w not in _ARTICLES).strip()


def f1(pred: str, gold: str) -> float:
    p, g = norm(pred).split(), norm(gold).split()
    if not p or not g:
        return 0.0
    common = Counter(p) & Counter(g)
    ncom = sum(common.values())
    if ncom == 0:
        return 0.0
    prec, rec = ncom / len(p), ncom / len(g)
    return 2 * prec * rec / (prec + rec)


def contains(pred: str, gold: str) -> bool:
    g = norm(gold)
    return bool(g) and g in norm(pred)


def auto_correct(pred: str, gold: str) -> bool:
    """Objectif, sans juge : réponse-or présente OU fort recouvrement de tokens."""
    return contains(pred, gold) or f1(pred, gold) >= 0.6


# ------------------------------------------------------------------ prompts
GEN_TMPL = (
    "Tu réponds à une question à partir UNIQUEMENT des extraits de conversation ci-dessous "
    "(datés). Réponds en quelques mots, sans phrase inutile. Si l'information n'y est pas, "
    "réponds exactement : Not mentioned.\n\n"
    "Extraits :\n{ctx}\n\nQuestion : {q}\nRéponse :"
)
JUDGE_TMPL = (
    "Tu es un correcteur. La réponse PRÉDITE est-elle correcte au vu de la réponse ATTENDUE, "
    "pour la question posée ? Une reformulation ou une date équivalente compte comme correcte. "
    "Réponds par un seul mot : CORRECT ou INCORRECT.\n\n"
    "Question : {q}\nAttendue : {gold}\nPrédite : {pred}\nVerdict :"
)


def build_ctx(ix: Index, question: str, now: float, dates: dict) -> str:
    res = ix.retrieve(question, k=K_CONTEXT, now=now)
    lines = []
    for f, _s, _r in res:
        lines.append(f"[{dates.get(f.id,'?')}] {f.txt}")
    return "\n".join(lines) if lines else "(aucune note)"


# ------------------------------------------------------------------ nettoyage au démarrage
def startup_cleanup():
    """Termine toute AUTRE instance de ce banc (par ligne de commande, jamais soi-même) et met de côté
    les anciens journaux. C'est du Python lancé depuis le processus (Start-Process bénin), donc hors du
    garde-fou du noyau : on ne tue que python.exe dont la ligne de commande contient ce script."""
    me = os.getpid()
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*banc_locomo_phaseB*' -and $_.ProcessId -ne {me} }} | "
          f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass
    for old in (LOG, OUT, HERE / "reponses_phaseB.json"):
        try:
            if old.exists():
                old.replace(old.with_suffix(old.suffix + ".gptoss"))
        except Exception:  # noqa: BLE001
            pass
    time.sleep(2.0)


# ------------------------------------------------------------------ campagne
def main():
    startup_cleanup()
    tags = ollama_tags()
    if not tags:
        log("ABANDON : Ollama ne répond pas. Démarre Ollama puis relance.")
        return
    gen_model, judge_model = pick_models(tags)
    log(f"modèles Ollama : {tags}")
    log(f"génération = {gen_model} · juge = {judge_model}")

    data = json.load(open(DATA, encoding="utf-8"))
    tally = {"n": 0, "judge_ok": 0, "auto_ok": 0, "contains": 0, "f1_sum": 0.0,
             "gen_model": gen_model, "judge_model": judge_model,
             "par_categorie": {}, "started": time.time()}
    percat = defaultdict(lambda: {"n": 0, "judge_ok": 0, "auto_ok": 0})
    records = []

    def save():
        tally["par_categorie"] = {
            CATLABEL.get(c, str(c)): {
                "n": v["n"],
                "J": round(v["judge_ok"] / v["n"], 3) if v["n"] else 0.0,
                "auto": round(v["auto_ok"] / v["n"], 3) if v["n"] else 0.0,
            } for c, v in sorted(percat.items())
        }
        n = tally["n"] or 1
        tally["J_score_juge"] = round(tally["judge_ok"] / n, 3)
        tally["auto_correct"] = round(tally["auto_ok"] / n, 3)
        tally["contains_gold"] = round(tally["contains"] / n, 3)
        tally["f1_moyen"] = round(tally["f1_sum"] / n, 3)
        tally["secondes"] = round(time.time() - tally["started"], 1)
        json.dump(tally, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for si, sample in enumerate(data):
        conv = sample["conversation"]
        with tempfile.TemporaryDirectory() as d:
            ix, now, dates = build_index(conv, Path(d))
            qas = [q for q in sample.get("qa", []) if q.get("category") != 5]
            for qa in qas:
                gold = str(qa.get("answer", "")).strip()
                if not gold:
                    continue
                question = str(qa.get("question", ""))
                cat = qa.get("category")
                ctx = build_ctx(ix, question, now, dates)
                try:
                    pred = ollama_gen(gen_model, GEN_TMPL.format(ctx=ctx, q=question))
                except Exception as e:  # noqa: BLE001
                    log(f"  gen échec Q{tally['n']+1} : {e!r}")
                    pred = ""
                # jugement LLM
                try:
                    verdict = ollama_gen(judge_model, JUDGE_TMPL.format(q=question, gold=gold, pred=pred),
                                         num_predict=8).upper()
                    jok = "CORRECT" in verdict and "INCORRECT" not in verdict
                except Exception as e:  # noqa: BLE001
                    log(f"  juge échec Q{tally['n']+1} : {e!r}")
                    jok = False
                if tally["n"] < 5:
                    log(f"  Q{tally['n']+1} t_gen+juge mesuré (voir horodatage) · pred={pred[:60]!r}")
                aok = auto_correct(pred, gold)
                c = contains(pred, gold)
                fq = f1(pred, gold)

                tally["n"] += 1
                tally["judge_ok"] += int(jok)
                tally["auto_ok"] += int(aok)
                tally["contains"] += int(c)
                tally["f1_sum"] += fq
                percat[cat]["n"] += 1
                percat[cat]["judge_ok"] += int(jok)
                percat[cat]["auto_ok"] += int(aok)
                records.append({"conv": si, "cat": cat, "q": question, "gold": gold,
                                "pred": pred, "judge": jok, "auto": aok, "f1": round(fq, 2)})

                if tally["n"] % CKPT == 0:
                    save()
                    log(f"  Q{tally['n']} · J={tally['judge_ok']/tally['n']:.3f} "
                        f"auto={tally['auto_ok']/tally['n']:.3f} (conv {si+1}/{len(data)})")
        log(f"conv {si+1}/{len(data)} terminée · Q cumulées={tally['n']}")

    save()
    json.dump(records, open(HERE / "reponses_phaseB.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"=== TERMINÉ === {tally['n']} questions · J(juge)={tally['J_score_juge']} · "
        f"auto={tally['auto_correct']} · contient={tally['contains_gold']} · F1={tally['f1_moyen']}")


if __name__ == "__main__":
    main()
