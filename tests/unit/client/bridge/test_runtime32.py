"""``_runtime32`` (I): saídas gravadas de ``py -0p`` (dois formatos), globs, sonda, cache, argumento sem fallback.

Nada é instalado nem baixado: a sonda e o ``py`` são dublês (``Finder(run=...)``), salvo o teste que sonda o próprio
interpretador dos testes (um Python sem a DLL).
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from rumbapy.errors import Python32NotFound
from rumbapy import _runtime32

import struct
import json
import sys
import os

import pytest

# `py -0p` do lançador clássico (py.exe do instalador python.org), gravado
CLASSIC = """\
 -V:3.13-32       C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python313-32\\python.exe
 -V:3.13 *        C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python313\\python.exe
 -V:3.12-32       C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python312-32\\python.exe
 -V:3.11-32       C:\\Users\\u\\AppData\\Local\\Programs\\Python\\Python311-32\\python.exe
"""
# formato antigo do lançador (3.8-era): sem "V:", asterisco no fim
CLASSIC_OLD = """\
Installed Pythons found by py Launcher for Windows
 -3.10-64       C:\\Python310\\python.exe *
 -3.12-32       C:\\Program Files (x86)\\Python312-32\\python.exe
"""
# Python Install Manager (pymanager, 2025+), gravado
PYMANAGER = """\
 -V:3.14[-64] *     C:\\Users\\u\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe
 -V:3.13-32         C:\\Users\\u\\AppData\\Local\\Python\\pythoncore-3.13-32\\python.exe
 -V:3.12-arm64      C:\\Users\\u\\AppData\\Local\\Python\\pythoncore-3.12-arm64\\python.exe
 -V:PythonCore/3.12-32 C:\\Users\\u\\AppData\\Local\\Python\\pythoncore-3.12-32\\python.exe
"""


def test_parse_classic() -> None:
    assert _runtime32.parse_py_list(CLASSIC) == [
        r"C:\Users\u\AppData\Local\Programs\Python\Python313-32\python.exe",
        r"C:\Users\u\AppData\Local\Programs\Python\Python312-32\python.exe",
        r"C:\Users\u\AppData\Local\Programs\Python\Python311-32\python.exe",
    ]


def test_parse_classic_old_format() -> None:
    assert _runtime32.parse_py_list(CLASSIC_OLD) == [r"C:\Program Files (x86)\Python312-32\python.exe"]


def test_parse_python_install_manager() -> None:
    assert _runtime32.parse_py_list(PYMANAGER) == [
        r"C:\Users\u\AppData\Local\Python\pythoncore-3.13-32\python.exe",
        r"C:\Users\u\AppData\Local\Python\pythoncore-3.12-32\python.exe",
    ]


def test_default_globs_and_order() -> None:
    env = {"LOCALAPPDATA": r"C:\L", "ProgramFiles(x86)": r"C:\PF86"}
    assert _runtime32.default_globs(env) == [r"C:\L\Programs\Python\Python3*-32\python.exe",
                                             r"C:\L\Python\pythoncore-3.*-32\python.exe",
                                             r"C:\PF86\Python3*\python.exe"]
    found = [r"C:\L\Programs\Python\Python312-32\python.exe", r"C:\L\Programs\Python\Python313-32\python.exe",
             r"C:\L\Programs\Python\Python39-32\python.exe"]
    assert _runtime32.expand_glob("x", lambda pattern: found) == [found[1], found[0], found[2]]


def info(bits: int = 32, version: tuple[int, int, int] = (3, 13, 2), level: str = "final",
         real: str | None = None) -> str:
    return json.dumps({"bits": bits, "version": list(version), "level": level, "real": real or "C:/real/python.exe"})


class Fake:
    """``run`` falso: ``py -0p`` -> ``py_out``; sonda -> ``probes[exe]`` (``(código, stdout)``)."""

    def __init__(self, tmp_path: Path, py_out: str | None = None, **probes: Any) -> None:
        self.tmp = tmp_path
        self.py_out = py_out
        self.probes: dict[str, tuple[int | None, str]] = {}
        self.calls: list[list[str]] = []

    def exe(self, name: str, result: tuple[int | None, str]) -> str:
        path = self.tmp / name / "python.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")
        self.probes[os.path.normcase(str(path))] = result
        return str(path)

    def run(self, argv: list[str], timeout: float) -> tuple[int | None, str]:
        self.calls.append(argv)
        if argv[:2] == ["py", "-0p"]:
            if self.py_out is None:
                raise FileNotFoundError("py")
            return 0, self.py_out
        assert argv[1:4] == ["-I", "-S", "-c"] and argv[4] == _runtime32.PROBE
        return self.probes[os.path.normcase(argv[0])]

    def finder(self, *, is_32: bool = False, env: dict[str, str] | None = None, globs: list[str] | None = None,
               globber: Any = None) -> _runtime32.Finder:
        return _runtime32.Finder(run=self.run, env=env or {}, is_32=is_32, globs=globs or [],
                                 globber=globber or (lambda p: []), cache_path=self.tmp / "runtime.json")


def test_py_list_first_valid_wins_and_cache_is_written(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    bad = fake.exe("Python313-32", (0, info(level="beta")))           # pré-lançamento recusado
    good = fake.exe("Python312-32", (0, info(version=(3, 12, 9), real="C:/base/python.exe")))
    fake.py_out = f" -V:3.13-32   {bad}\n -V:3.12-32   {good}\n"
    rt = fake.finder().find()
    assert rt == _runtime32.Runtime32(good, "C:/base/python.exe", (3, 12, 9))
    cache = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    assert cache["path"] == good and cache["real"] == "C:/base/python.exe" and cache["version"] == [3, 12, 9]


def test_globs_after_py(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    good = fake.exe("Python313-32", (0, info()))
    rt = fake.finder(globs=["pat"], globber=lambda p: [good]).find()
    assert rt.path == good


def test_explicit_invalid_has_no_fallback(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    x64 = fake.exe("Python313", (0, info(bits=64)))
    good = fake.exe("Python313-32", (0, info()))
    fake.py_out = f" -V:3.13-32   {good}\n"
    with pytest.raises(Python32NotFound) as e:
        fake.finder().find(x64)
    assert "64-bit" in e.value.message and "argumento python32" in e.value.message
    assert not any(c[:2] == ["py", "-0p"] for c in fake.calls)          # não caiu para as outras fontes
    with pytest.raises(Python32NotFound):
        fake.finder().find(tmp_path / "nao-existe.exe")


def test_env_var_invalid_has_no_fallback(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    old = fake.exe("Python311-32", (0, info(version=(3, 11, 9))))
    with pytest.raises(Python32NotFound) as e:
        fake.finder(env={"RUMBAPY_PYTHON32": old}).find()
    assert "RUMBAPY_PYTHON32" in e.value.message and ">= 3.12" in e.value.message


def test_probe_failures(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    for name, result, text in [("a", (None, ""), "não respondeu"), ("b", (1, ""), "falhou na sonda"),
                               ("c", (0, "lixo"), "resposta inválida")]:
        exe = fake.exe(name, result)
        with pytest.raises(Python32NotFound) as e:
            fake.finder().find(exe)
        assert text in e.value.message


def test_nothing_found_message(tmp_path: Path) -> None:
    fake = Fake(tmp_path, py_out="")
    with pytest.raises(Python32NotFound) as e:
        fake.finder().find()
    assert e.value.message == "Não encontrei um Python 32-bit >= 3.12 (o RUMBA só existe em 32-bit)."
    assert 'instale o "Windows installer (32-bit)"' in e.value.hint and 'python32=r"C:' in e.value.hint


def test_cache_revalidated_by_size_and_mtime(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    good = fake.exe("Python313-32", (0, ""))
    fake.probes[os.path.normcase(good)] = (0, info(real=good))         # o "real" precisa existir para o cache valer
    finder = fake.finder(globs=["p"], globber=lambda p: [good])
    finder.find()
    probes = sum(1 for c in fake.calls if c[0] == good)
    again = fake.finder()                                               # processo novo: lê o runtime.json
    assert again.find().path == good and sum(1 for c in fake.calls if c[0] == good) == probes
    Path(good).write_bytes(b"MZ-maior")                                 # Python atualizado: sonda de novo
    third = fake.finder()
    assert third.find().path == good and sum(1 for c in fake.calls if c[0] == good) == probes + 1


def test_memo_one_discovery_per_process(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    good = fake.exe("Python313-32", (0, info()))
    finder = fake.finder(globs=["p"], globber=lambda p: [good])
    assert finder.find() is finder.find()


def test_probe_real_interpreter_reports_real_executable() -> None:
    """Sonda de verdade no Python dos testes (venv): ``real`` é o executável base, não o lançador do venv (E14)."""
    finder = _runtime32.Finder(cache_path=Path(os.devnull))
    if struct.calcsize("P") == 4:
        rt = finder.probe(sys.executable)
        assert rt.version == tuple(sys.version_info[:3])
        assert os.path.normcase(rt.real) == os.path.normcase(os.path.realpath(
            getattr(sys, "_base_executable", sys.executable)))
    else:
        with pytest.raises(_runtime32._Invalid) as e:
            finder.probe(sys.executable)
        assert "64-bit" in e.value.reason


def test_self_runtime_when_32bit(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    finder = fake.finder(is_32=True)
    finder.executable = sys.executable
    if struct.calcsize("P") == 4:
        assert finder.find().path == sys.executable and fake.calls == []
    else:
        with pytest.raises(Python32NotFound):                          # 64-bit fingindo 32: a checagem real recusa
            finder.find()


class _StuckProc:
    """Dublê de ``Popen``: a sonda estoura o prazo e o stdout continua aberto (neto do redirecionador de venv)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[float | None] = []
        self.killed = False
        self.stdout = _Stream()
        self.stderr = None
        self.returncode = None
        _StuckProc.last = self

    last: Any = None

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        import subprocess

        self.calls.append(timeout)
        raise subprocess.TimeoutExpired("python", timeout or 0)

    def kill(self) -> None:
        self.killed = True


class _Stream:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_run_capture_never_waits_without_deadline_after_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runtime32.subprocess, "Popen", _StuckProc)
    assert _runtime32.run_capture(["python.exe", "-c", "pass"], 0.01) == (None, "")
    proc = _StuckProc.last
    assert proc.killed
    assert proc.calls == [0.01, _runtime32.REAP_TIMEOUT]         # o 2º communicate também tem prazo
    assert proc.stdout.closed
