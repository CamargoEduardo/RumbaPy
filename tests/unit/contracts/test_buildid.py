"""Aceite P-0: ``_buildid.compute`` (H.10) — estável e sensível a qualquer ``.py``/``.js``/``.json``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from rumbapy import _buildid

import subprocess
import hashlib
import sys

import pytest

PKG = Path(__file__).resolve().parents[3] / "src" / "rumbapy"


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    root = tmp_path / "rumbapy"
    (root / "_worker").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "__init__.py").write_bytes(b"x = 1\n")
    (root / "_worker" / "main.py").write_bytes(b"def main():\n    return 0\n")
    (root / "_ie_open.js").write_bytes(b"WScript.Echo(1);\n")
    (root / "dados.json").write_bytes(b'{"a": 1}\n')
    (root / "LEIA.txt").write_bytes(b"ignorado\n")
    (root / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0\1")
    (root / "__pycache__" / "y.py").write_bytes(b"tambem ignorado")
    return root


def expected(root: Path) -> str:
    files = ["__init__.py", "_ie_open.js", "_worker/main.py", "dados.json"]
    h = hashlib.sha256()
    for rel in sorted(files, key=lambda r: Path(root, r)):
        h.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return h.hexdigest()[:16]


def test_compute_matches_the_spec_and_is_stable(pkg: Path) -> None:
    first = _buildid.compute(pkg)
    assert first == _buildid.compute(str(pkg)) == expected(pkg)
    assert len(first) == 16 and int(first, 16) >= 0


@pytest.mark.parametrize("rel", ["__init__.py", "_worker/main.py", "_ie_open.js", "dados.json"])
def test_any_relevant_change_changes_the_id(pkg: Path, rel: str) -> None:
    before = _buildid.compute(pkg)
    target = pkg / rel
    target.write_bytes(target.read_bytes() + b" ")
    assert _buildid.compute(pkg) != before


def test_new_renamed_and_ignored_files(pkg: Path) -> None:
    before = _buildid.compute(pkg)
    (pkg / "LEIA.txt").write_bytes(b"mudou")
    (pkg / "__pycache__" / "y.py").write_bytes(b"mudou")
    (pkg / "__pycache__" / "z.json").write_bytes(b"{}")
    assert _buildid.compute(pkg) == before
    (pkg / "novo.py").write_bytes(b"")
    with_new = _buildid.compute(pkg)
    assert with_new != before
    (pkg / "novo.py").rename(pkg / "outro.py")
    assert _buildid.compute(pkg) not in (before, with_new)


def test_real_package_same_result_in_a_fresh_interpreter() -> None:
    code = ("import sys, importlib.util; spec = importlib.util.spec_from_file_location('b', sys.argv[1]); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.compute(sys.argv[2]))")
    out = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(PKG / "_buildid.py"), str(PKG)],
                         capture_output=True, text=True, timeout=60, check=True).stdout.strip()
    assert out == _buildid.compute(PKG)
