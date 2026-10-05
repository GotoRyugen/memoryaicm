"""Construit l'archive neutre `dist/memoryaicm_neutral.zip` : structurée (paquet, adaptateurs, docs, tests),
exécutable telle quelle (`python memoryaicm_neutral.zip …`), installable (`pip install memoryaicm_neutral.zip`),
reproductible (horodatages fixes) et auto-vérifiée (selftest MCP + bench depuis un répertoire vierge).

    python scripts/build_neutral.py [--out dist/memoryaicm_neutral.zip] [--no-verify]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memoryaicm import __version__  # noqa: E402

FIXED_DATE = (2026, 1, 1, 0, 0, 0)   # reproductible : même contenu ⇒ même archive
INCLUDE_DIRS = ("memoryaicm", "adapters", "docs", "tests", "bench", "scripts")
INCLUDE_FILES = ("README.md", "README.en.md", "NEUTRAL.md", "LICENSE", "LICENSE-COMMERCIAL.md", "CHANGELOG.md", ".env.example", "pyproject.toml", ".gitignore",
                 "setup.ps1", "install-claude.ps1", "install-claude.cmd", "chat.ps1", "serve.ps1")
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".venv", "dist", "build", "_to_delete", "site", "mem", "_verif", ".github"}
EXCLUDE_TOP = {"data"}   # dossier de données d'exécution (pas memoryaicm/data, qui est embarqué)
EXCLUDE_SUFFIXES = (".pyc", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".log", ".maicm", ".key")
EXCLUDE_NAMES = {"locomo10.json", "run_complet.log", "phaseB.log"}   # jeu tiers et traces : pas dans l'archive

MAIN_PY = '''"""memoryaicm_neutral.zip — exécutable tel quel : python memoryaicm_neutral.zip <commande>  (--help)"""
from memoryaicm.cli import main

main()
'''


def collect() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ROOT)
            if not p.is_file() or any(part in EXCLUDE_PARTS for part in rel.parts) or rel.parts[0] in EXCLUDE_TOP or p.suffix in EXCLUDE_SUFFIXES:
                continue
            if p.name == ".env" or p.name in EXCLUDE_NAMES or ".bak-" in p.name:
                continue
            files.append((p.relative_to(ROOT).as_posix(), p.read_bytes()))
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists():
            files.append((f, p.read_bytes()))
    files.append(("__main__.py", MAIN_PY.encode("utf-8")))
    return files


def build(out: Path) -> dict:
    files = collect()
    manifest = {
        "name": "memoryaicm_neutral", "version": __version__, "python": ">=3.10", "dependencies": [],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": ["python memoryaicm_neutral.zip --help", "python memoryaicm_neutral.zip mcp --selftest",
                "python memoryaicm_neutral.zip install --write", "unzip memoryaicm_neutral.zip -d memoryaicm",
                "pip install memoryaicm_neutral.zip"],
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files},
    }
    files.append(("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")))
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.comment = f"memoryaicm {__version__} — mémoire tri-couche neutre (tout LLM). python memoryaicm_neutral.zip --help".encode("utf-8")
        for name, data in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o644 & 0xFFFF) << 16
            z.writestr(zi, data)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    manifest["archive_sha256"] = digest
    manifest["archive_bytes"] = out.stat().st_size
    return manifest


def verify(out: Path) -> bool:
    """Depuis un répertoire vierge, sans le dépôt dans sys.path : selftest MCP, schémas, bench, statut."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORYAICM_") and k not in ("ANTHROPIC_API_KEY", "PYTHONPATH")}
    env["MEMORYAICM_NO_DOTENV"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory() as td:
        home = str(Path(td) / "data")

        def run(*args, timeout=120):
            return subprocess.run([sys.executable, str(out.resolve()), *args], cwd=td, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)

        r = run("--home", home, "--backend", "stub", "mcp", "--selftest")
        ok1 = r.returncode == 0 and "selftest MCP : OK" in r.stderr
        r = run("tools", "--format", "anthropic")
        ok2 = r.returncode == 0 and len(json.loads(r.stdout)) == 13
        r = run("--home", home, "--backend", "stub", "bench")
        ok3 = r.returncode == 0 and "ÉCHEC" not in r.stdout
        r = run("--home", home, "--backend", "stub", "show", "status")
        ok4 = r.returncode == 0 and json.loads(r.stdout)["journal_chain"]
        r = run("--home", home, "install")
        ok5 = r.returncode == 0 and out.name in r.stdout and "mcpServers" in r.stdout
        for label, ok in (("selftest MCP", ok1), ("schémas", ok2), ("bench", ok3), ("statut", ok4), ("install", ok5)):
            print(f"  {label:14s} {'OK' if ok else 'ÉCHEC'}")
        if not (ok1 and ok2 and ok3 and ok4 and ok5):
            print(r.stdout[-1500:], r.stderr[-1500:])
        return ok1 and ok2 and ok3 and ok4 and ok5


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "dist" / "memoryaicm_neutral.zip"))
    p.add_argument("--no-verify", action="store_true")
    a = p.parse_args()
    out = Path(a.out)
    m = build(out)
    print(f"{out} : {m['archive_bytes']} octets · {len(m['files'])} fichiers · sha256 {m['archive_sha256'][:16]}… · v{m['version']}")
    if not a.no_verify:
        ok = verify(out)
        print("archive vérifiée" if ok else "ARCHIVE INVALIDE")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
