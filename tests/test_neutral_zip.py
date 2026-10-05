"""ARCHIVE NEUTRE : construction reproductible, exécution telle quelle depuis un répertoire vierge, manifeste, dézippage."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_neutral  # noqa: E402
from memoryaicm.tools import TOOL_NAMES  # noqa: E402


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist") / "memoryaicm_neutral.zip"
    build_neutral.build(out)
    return out


def _run(archive: Path, cwd: Path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORYAICM_") and k not in ("ANTHROPIC_API_KEY", "PYTHONPATH")}
    env.update(MEMORYAICM_NO_DOTENV="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(archive), *args], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def test_archive_is_structured_reproducible_and_manifested(archive, tmp_path):
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        assert {"__main__.py", "MANIFEST.json", "NEUTRAL.md", "README.md", "pyproject.toml", "docs/PROTOCOL.md",
                "memoryaicm/__init__.py", "memoryaicm/tools.py", "memoryaicm/toolloop.py", "memoryaicm/mcp_server.py",
                "memoryaicm/data/injection_cases.jsonl", "adapters/openai_compatible.py", "tests/test_neutral.py"} <= names
        assert not any("__pycache__" in n or n.endswith(".sqlite") or n == ".env" for n in names)
        manifest = json.loads(z.read("MANIFEST.json"))
        for name, meta in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"]
        assert manifest["version"] == __import__("memoryaicm").__version__ and manifest["dependencies"] == []
        assert b"memoryaicm" in z.comment
    # reproductible : reconstruire donne les mêmes octets (hors MANIFEST.built_at)
    again = tmp_path / "again.zip"
    build_neutral.build(again)
    with zipfile.ZipFile(archive) as a, zipfile.ZipFile(again) as b:
        for n in a.namelist():
            if n != "MANIFEST.json":
                assert a.read(n) == b.read(n)
    assert archive.with_suffix(".zip.sha256").read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_archive_runs_from_a_clean_directory(archive, tmp_path):
    cwd = tmp_path / "vierge"; cwd.mkdir()
    home = str(cwd / "data")
    r = _run(archive, cwd, "--home", home, "--backend", "stub", "mcp", "--selftest")
    assert r.returncode == 0 and "selftest MCP : OK" in r.stderr, r.stderr[-800:]
    r = _run(archive, cwd, "tools", "--format", "gemini")
    assert r.returncode == 0 and len(json.loads(r.stdout)[0]["function_declarations"]) == len(TOOL_NAMES)
    r = _run(archive, cwd, "--home", home, "--backend", "stub", "say", "je m'appelle Camille")
    assert r.returncode == 0 and "s'appelle Camille" in r.stdout
    r = _run(archive, cwd, "--home", home, "--backend", "stub", "bench")
    assert r.returncode == 0 and "ÉCHEC" not in r.stdout
    r = _run(archive, cwd, "--home", home, "install")
    cfg = json.loads(r.stdout.split("\n# Claude Code")[0].split("\n", 1)[1])
    assert cfg["mcpServers"]["memoryaicm"]["args"][0] == str(archive.resolve()) and cfg["mcpServers"]["memoryaicm"]["args"][-1] == "mcp"
    assert (cwd / "data" / "journal.sqlite").exists()


def test_unzipped_tree_is_importable_and_tests_are_there(archive, tmp_path):
    dest = tmp_path / "unz"
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    r = subprocess.run([sys.executable, "-c", "import memoryaicm, memoryaicm.toolloop; print(memoryaicm.__version__)"],
                       cwd=dest, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == __import__("memoryaicm").__version__
    assert (dest / "tests" / "conftest.py").exists() and (dest / "adapters" / "langchain_tools.py").exists()
