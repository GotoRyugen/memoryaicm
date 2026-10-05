"""Construit l'asset complet `dist/memoryaicm_asset_v<version>.zip` :

  memoryaicm_asset/
    README_ASSET.md              index de l'asset
    memoryaicm_neutral.zip       l'archive neutre exécutable (+ .sha256)  — python memoryaicm_neutral.zip --help
    docs/REFERENCE.md            référence complète (générée depuis le code)
    docs/reference.html          la même, page hors ligne (navigable)
    docs/PROTOCOL.md             protocole mémoire universel (tout LLM)
    docs/SPEC.md                 spécification d'origine « Mémoire Tri-Couche »
    source/                      l'arborescence complète du dépôt (paquet, tests, adaptateurs, scripts, docs, Windows)
    MANIFEST.json                version, date, SHA-256 de chaque fichier

    python scripts/build_asset.py        (régénère docs + archive neutre, puis emballe)
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from memoryaicm import __version__  # noqa: E402

import build_neutral  # noqa: E402
import gen_docs  # noqa: E402

FIXED_DATE = (2026, 1, 1, 0, 0, 0)


README_ASSET = """# memoryaicm — asset complet v{version}

Tout ce qu'il faut pour utiliser, brancher, auditer et faire évoluer la mémoire tri-couche, en un seul fichier.

| Fichier | Quoi | Commencer par |
|---|---|---|
| `memoryaicm_neutral.zip` | l'archive **exécutable** (zéro dépendance, Python ≥ 3.10) : paquet, adaptateurs, docs, tests | `python memoryaicm_neutral.zip mcp --selftest` puis `install --write --hook` |
| `docs/reference.html` | la **référence complète**, page navigable hors ligne | ouvrir dans un navigateur |
| `docs/REFERENCE.md` | la même, en Markdown (générée depuis le code) | section 0 « En une page » |
| `docs/PROTOCOL.md` | le **protocole universel** : 11 outils, format des faits, consigne système, branchement de chaque LLM | section 4 « Brancher n'importe quel LLM » |
| `docs/SPEC.md` | la spécification d'origine « Mémoire Tri-Couche » | — |
| `source/` | l'arborescence complète du dépôt (identique au contenu de l'archive, dézippée) | `python -m pytest -q` |
| `MANIFEST.json` | version, date de construction, SHA-256 de chaque fichier | vérifier l'intégrité |

## En trois commandes

```bash
python memoryaicm_neutral.zip mcp --selftest                     # 1. tout marche ?
python memoryaicm_neutral.zip install --write --hook             # 2. Claude Desktop / Cowork / Claude Code (tout OS), puis redémarrer Claude
python memoryaicm_neutral.zip agent --provider openai --base-url http://localhost:11434 --model llama3.1   # 3. ou n'importe quel autre LLM
```

## Garanties (rappel)

`Log ⊇ Index ⊇ Adapter` · oubli = désindexer, jamais détruire · journal append-only chaîné SHA-256 · un LLM propose,
la politique décide · contenu externe = donnée · faits sensibles validés par un humain · sommeil automatique testé,
promu ou annulé · niveaux d'autonomie 0–3 · modèles locaux approuvés par empreinte.

Construit le {date} · {tests} tests verts · {lines} lignes de Python dans le paquet.
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    gen_docs.main()
    neutral = ROOT / "dist" / "memoryaicm_neutral.zip"
    m = build_neutral.build(neutral)
    if not build_neutral.verify(neutral):
        sys.exit("archive neutre invalide")
    counts = __import__("build_reference_page").counts()
    files: list[tuple[str, bytes]] = []
    readme = README_ASSET.format(version=__version__, date=time.strftime("%Y-%m-%d"), tests=counts["tests"], lines=counts["lines"])
    files.append(("memoryaicm_asset/README_ASSET.md", readme.encode("utf-8")))
    files.append(("memoryaicm_asset/memoryaicm_neutral.zip", neutral.read_bytes()))
    files.append(("memoryaicm_asset/memoryaicm_neutral.zip.sha256", neutral.with_suffix(".zip.sha256").read_bytes()))
    for name in ("REFERENCE.md", "reference.html", "PROTOCOL.md", "SPEC.md"):
        files.append((f"memoryaicm_asset/docs/{name}", (ROOT / "docs" / name).read_bytes()))
    for rel, data in build_neutral.collect():
        if rel in ("__main__.py",):
            continue
        files.append((f"memoryaicm_asset/source/{rel}", data))
    manifest = {"name": "memoryaicm_asset", "version": __version__, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "neutral_archive_sha256": m["archive_sha256"], "tests": counts["tests"], "lines": counts["lines"],
                "files": {n: {"sha256": sha(d), "bytes": len(d)} for n, d in files}}
    files.append(("memoryaicm_asset/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")))
    out = ROOT / "dist" / f"memoryaicm_asset_v{__version__}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.comment = f"memoryaicm asset v{__version__} — archive neutre + référence + protocole + spec + source".encode("utf-8")
        for name, data in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o644 & 0xFFFF) << 16
            z.writestr(zi, data)
    out.with_suffix(".zip.sha256").write_text(f"{sha(out.read_bytes())}  {out.name}\n", encoding="utf-8")
    print(f"{out} : {out.stat().st_size} octets · {len(files)} fichiers")


if __name__ == "__main__":
    main()
