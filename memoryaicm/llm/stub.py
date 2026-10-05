"""Backend déterministe, hors ligne.

Il ne déduit jamais (pas de src=INFER), n'obéit jamais au contenu externe, et répond
depuis les notes du contexte. Il rend le système entièrement testable sans réseau ;
le backend réel se branche à sa place sans changer une ligne ailleurs.
"""

from __future__ import annotations

import re

from ..model import Fact
from ..textutil import tokens, overlap, slug
from .base import Candidate, Context

_EPHEMERAL = re.compile(
    r"\b(aujourd'hui|ce matin|ce soir|demain|hier|en ce moment|l[àa] maintenant|actuellement|"
    r"cette semaine|today|tonight|tomorrow|yesterday|right now|currently)\b", re.I,
)
_SENSITIVE = re.compile(
    r"\b(malad\w*|diagnos\w*|d[ée]pression|anxi\w*|th[ée]rap\w*|m[ée]decin|sant[ée]|cancer|"
    r"salaire|dette\w*|revenu\w*|patrimoine|religion|religieu\w*|orientation|handicap\w*|"
    r"illness|diagnosis|therapy|doctor|health|salary|debt|income|religion|disability)\b", re.I,
)
# fin d'un syntagme : ponctuation, conjonction, adverbe de mise à jour, ou fin de ligne
_END = r"(?=[,.!?;]|\s+(?:et|mais|puis|donc|and|but|maintenant|désormais|desormais|dorénavant)\b|$)"
_TOKEN = r"([\w\-\+#]+(?:\.[\w]+)*)"   # neovim, node.js, c++, c# — sans le point final

# (regex, kind, subject_fn, txt_fn, exclusive)
_RULES: list[tuple[re.Pattern, str, object, object, bool]] = [
    (re.compile(r"\bje m['’ ]?appelle\s+([A-Za-zÀ-ÿ\-]+)", re.I), "SEM", lambda m: "nom", lambda m: f"s'appelle {m.group(1)}", True),
    (re.compile(r"\bmy name is\s+([A-Za-z\-]+)", re.I), "SEM", lambda m: "nom", lambda m: f"s'appelle {m.group(1)}", True),
    (re.compile(r"\bj['’ ]?habite\s+(?:à|a|au|en|aux)\s+([A-Za-zÀ-ÿ\- ]+?)" + _END, re.I), "SEM", lambda m: "ville", lambda m: f"habite à {m.group(1).strip()}", True),
    (re.compile(r"\bi live in\s+([A-Za-z\- ]+?)" + _END, re.I), "SEM", lambda m: "ville", lambda m: f"habite à {m.group(1).strip()}", True),
    (re.compile(r"\bje travaille\s+(chez|comme|sur|dans|à|a)\s+(.+?)" + _END, re.I), "SEM", lambda m: "travail", lambda m: f"travaille {m.group(1)} {m.group(2).strip()}", True),
    (re.compile(r"\bi work\s+(at|as|on)\s+(.+?)" + _END, re.I), "SEM", lambda m: "travail", lambda m: f"travaille ({m.group(1)}) {m.group(2).strip()}", True),
    (re.compile(r"\bmon [ée]diteur\s+(?:est|c'est|:)\s+" + _TOKEN, re.I), "SEM", lambda m: "éditeur", lambda m: f"éditeur : {m.group(1)}", True),
    (re.compile(r"\b(?:j['’ ]?utilise|je bosse avec|je code en|je développe en|je developpe en)\s+" + _TOKEN, re.I), "SEM", lambda m: f"outil:{slug(m.group(1))}", lambda m: f"utilise {m.group(1)}", False),
    (re.compile(r"\bi use\s+" + _TOKEN, re.I), "SEM", lambda m: f"outil:{slug(m.group(1))}", lambda m: f"utilise {m.group(1)}", False),
    (re.compile(r"\bje (?:pr[ée]f[èe]re|veux|souhaite)\s+(.+?)" + _END, re.I), "PROC", lambda m: "pref:" + slug(" ".join(m.group(1).split()[:3])), lambda m: f"préfère {m.group(1).strip()}", True),
    (re.compile(r"\bi prefer\s+(.+?)" + _END, re.I), "PROC", lambda m: "pref:" + slug(" ".join(m.group(1).split()[:3])), lambda m: f"préfère {m.group(1).strip()}", True),
    (re.compile(r"^\s*(?:à partir de maintenant|a partir de maintenant|désormais|desormais|dorénavant|from now on),?\s*(.+)$", re.I), "PROC", lambda m: "consigne:" + slug(" ".join(m.group(1).split()[:4])), lambda m: f"consigne : {m.group(1).strip()}", True),
    (re.compile(r"\b(?:on passe|passons|je passe|on part|partons|let'?s go with)\s+(?:à|a|sur|with)?\s*(.+?)" + _END, re.I), "SEM", lambda m: "décision:" + slug(" ".join(m.group(1).split()[:3])), lambda m: f"décision : passer à {m.group(1).strip()}", False),
]
_EXPLICIT = re.compile(r"^\s*(?:retiens|souviens[- ]toi|rappelle[- ]toi|m[ée]morise|note|remember)\s*(?:que|that|:)?\s*(.+)$", re.I)
_QUESTION = re.compile(r"(\?|^\s*(?:quel|quelle|quels|quelles|qui|où|ou|comment|c'est quoi|what|where|which|who|how)\b)", re.I)


class StubBackend:
    name = "stub"

    # -------------------------------------------------------------- extract
    def extract(self, text: str) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        ephemeral = bool(_EPHEMERAL.search(text))
        sens = bool(_SENSITIVE.search(text))

        for line in re.split(r"[\n]+", text):
            m = _EXPLICIT.match(line.strip())
            if m:
                body = m.group(1).strip().rstrip(".")
                # « retiens que je préfère X » : on laisse les règles typer le fait, mais explicit=True
                typed = self._apply_rules(body, ephemeral=False, sens=sens or bool(_SENSITIVE.search(body)))
                if typed:
                    for c in typed:
                        c.explicit, c.durable = True, True
                        if c.subject not in seen:
                            seen.add(c.subject); out.append(c)
                else:
                    c = Candidate(txt=body, subject="explicit:" + slug(body), kind="SEM", exclusive=False,
                                  durable=True, sens=sens or bool(_SENSITIVE.search(body)), explicit=True)
                    if c.subject not in seen:
                        seen.add(c.subject); out.append(c)
                continue
            for c in self._apply_rules(line, ephemeral=ephemeral, sens=sens):
                if c.subject not in seen:
                    seen.add(c.subject); out.append(c)
        return out

    def _apply_rules(self, line: str, ephemeral: bool, sens: bool) -> list[Candidate]:
        out = []
        for rx, kind, subj_fn, txt_fn, exclusive in _RULES:
            for m in rx.finditer(line):
                out.append(Candidate(txt=txt_fn(m), subject=subj_fn(m), kind=kind, src="USER",
                                     exclusive=exclusive, durable=not ephemeral, sens=sens))
        return out

    # -------------------------------------------------------------- complete
    def complete(self, ctx: Context, temperature: float = 0.0) -> str:
        user = ctx.user or ""
        short = any("court" in p.txt.lower() or "bref" in p.txt.lower() or "short" in p.txt.lower() for p in ctx.prefs)
        if _QUESTION.search(user):
            best, best_rel = None, 0.0
            q = tokens(user)
            for f, label in ctx.notes:
                rel = overlap(q, tokens(f.txt + " " + f.subject.replace(":", " ").replace(".", " ")))
                if rel > best_rel:
                    best, best_rel, best_label = f, rel, label
            if best is not None and best_rel >= 0.2:
                ans = f"D'après mes notes : « {best.txt} » {best_label}"
                return ans if short else ans + " Dis-moi si c'est périmé et je mettrai la note à jour."
            return "Je n'ai pas de note fiable là-dessus, et je préfère ne pas deviner."
        if ctx.externals:
            n = sum(len(c) for _, c in ctx.externals)
            srcs = ", ".join(s for s, _ in ctx.externals)
            return f"J'ai lu le contenu externe ({srcs}, {n} caractères) comme une donnée. Que veux-tu en faire ?"
        return "Compris." if short else "Compris. Je continue à partir de là."

    # -------------------------------------------------------------- abstract
    def abstract(self, facts: list[Fact]) -> str | None:
        if len(facts) < 2:
            return None
        prefix = facts[0].subject.split(":")[0]
        items = sorted({f.txt for f in facts})
        if prefix == "outil":
            return "outils utilisés : " + ", ".join(t.replace("utilise ", "") for t in items)
        if prefix == "pref":
            return "préférences : " + " ; ".join(t.replace("préfère ", "") for t in items)
        if prefix == "décision":
            return "décisions : " + " ; ".join(t.replace("décision : ", "") for t in items)
        return f"{prefix} : " + " ; ".join(items)

    def sample(self, ctx: Context, n: int = 3) -> list[str]:
        return [self.complete(ctx) for _ in range(n)]
