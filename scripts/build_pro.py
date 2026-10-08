"""Construit le zip de l'offre Pro : arbre des sources (dernière révision git), sans les fichiers de
développement (CI, site), plus la notice d'installation et les conditions Pro.

    python scripts/build_pro.py            → dist/memoryaicm-pro-<version>.zip
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from memoryaicm import __version__  # noqa: E402

DROP = ("site", ".github", "pro", "bench/locomo/Lancer-BancB.cmd")


def main() -> None:
    name = f"memoryaicm-pro-{__version__}"
    out = ROOT / "dist" / f"{name}.zip"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tree = Path(td) / name
        tree.mkdir()
        tar = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=tar, check=True)
        for d in DROP:
            p = tree / d
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        for f in ("LISEZMOI-INSTALLATION.txt", "LICENCE-PRO.txt"):
            (tree / f).write_text((ROOT / "pro" / f).read_text(encoding="utf-8").replace("{version}", __version__), encoding="utf-8")
        sh = tree / "install-claude.sh"
        sh.write_text((ROOT / "pro" / "install-claude.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        sh.chmod(0o755)
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(tree.rglob("*")):
                if p.is_file():
                    info = zipfile.ZipInfo.from_file(p, p.relative_to(tree.parent).as_posix())
                    info.compress_type = zipfile.ZIP_DEFLATED
                    if p.name.endswith(".sh"):
                        info.external_attr = (0o100755 << 16)
                        info.create_system = 3   # Unix : le bit executable est conserve a la decompression
                    with open(p, "rb") as fh:
                        z.writestr(info, fh.read())
    n = len(zipfile.ZipFile(out).namelist())
    print(f"{out} : {out.stat().st_size} octets · {n} fichiers · v{__version__}")


if __name__ == "__main__":
    main()
