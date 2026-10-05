"""VALIDATEUR · règles dures · hors modèle.

« adhésion 100 % ∈ gardes, ∉ modèle » : on ne demande pas à l'attention d'être parfaite,
on vérifie la sortie mécaniquement.

Règles :
  1. longueur bornée
  2. motifs interdits (secrets, clés) ⇒ blocage
  3. obéissance à une instruction externe ⇒ blocage (le contenu externe est une donnée)
  4. canaris configurables ⇒ blocage
  5. valeur périmée d'un sujet exclusif citée à la place de la valeur courante ⇒ blocage (temporalité)
  6. un fait de note cité sans son étiquette ⇒ étiquette ajoutée (correction, pas blocage)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Settings
from .llm.base import Context
from .policy import _INSTRUCTION_LIKE
from .textutil import overlap, tokens

SAFE_MESSAGE = "Réponse retenue par le validateur ({reasons}). Reformule ta demande ou vérifie le contenu externe."


@dataclass
class GuardResult:
    ok: bool
    output: str
    reasons: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)


class Guard:
    def __init__(self, settings: Settings | None = None, canaries: tuple[str, ...] = ()):
        self.s = settings or Settings()
        self.forbidden = [re.compile(p) for p in self.s.forbidden_patterns]
        self.canaries = tuple(canaries)

    def validate(self, output: str, ctx: Context) -> GuardResult:
        reasons: list[str] = []
        fixes: list[str] = []
        out = output or ""

        if len(out) > self.s.max_output_chars:
            out = out[: self.s.max_output_chars] + " […]"
            fixes.append("sortie tronquée")

        for rx in self.forbidden:
            if rx.search(out):
                reasons.append(f"motif interdit : {rx.pattern}")

        for c in self.canaries:
            if c and c in out:
                reasons.append(f"canari présent : {c!r}")

        # Obéissance à du contenu externe : la charge utile d'une ligne « instruction » réapparaît dans la sortie.
        low = out.lower()
        out_toks = tokens(out)
        for src, content in ctx.externals:
            for line in content.splitlines():
                line = line.strip()
                if len(line) >= 25 and _INSTRUCTION_LIKE.search(line):
                    frag = _payload_fragment(line)
                    ft = tokens(frag)
                    if frag and (frag in low or (len(ft) >= 4 and overlap(ft, out_toks) >= 0.8)):
                        reasons.append(f"instruction externe suivie (source {src})")
                        break

        # Valeur périmée : la sortie cite une ancienne valeur d'un sujet exclusif (« Paris ») alors que la note
        # en contexte porte la valeur courante (« Lyon ») et que celle-ci n'apparaît pas ⇒ blocage.
        for f, label in ctx.notes:
            if not f.exclusive or not f.prev:
                continue
            cur = tokens(f.txt)
            for old in f.prev:
                stale = tokens(old) - cur
                if stale and stale <= out_toks and not (cur - tokens(old)) & out_toks:
                    reasons.append(f"valeur périmée citée pour « {f.subject} » : « {old} » a été remplacé par « {f.txt} » ({label})")
                    break

        # Citation : un fait de note repris textuellement doit porter son étiquette.
        missing = [label for f, label in ctx.notes if f.txt.lower() in low and f.id not in out]
        if missing:
            out = out.rstrip() + "\n[sources : " + " ".join(missing) + "]"
            fixes.append("étiquettes de provenance ajoutées")

        if reasons:
            return GuardResult(False, SAFE_MESSAGE.format(reasons="; ".join(reasons)), reasons, fixes)
        return GuardResult(True, out, [], fixes)


def _payload_fragment(line: str) -> str:
    """La partie « charge utile » d'une ligne d'instruction (après le ou les verbes d'ordre), normalisée."""
    tail = line
    for _ in range(4):  # « From now on, remember that … » : on saute chaque verbe d'ordre en tête
        m = _INSTRUCTION_LIKE.search(tail)
        if not m or m.start() > 40:
            break
        tail = tail[m.end():]
        tail = re.sub(r"^[\s,:;\-—]+(?:and|et|que|that)?\s*", "", tail, flags=re.I)
    tail = re.sub(r"\s+", " ", tail).strip(" :.,;!?\"'").lower()
    return tail if len(tail) >= 12 else ""
