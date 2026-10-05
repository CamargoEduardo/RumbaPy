"""Regras de camada (A.4 e as proibições verificáveis de A.3), checadas por ``ast`` sobre os fontes do pacote.

Cada regra só se aplica aos módulos que já existem (os pacotes das ondas seguintes entram sozinhos). Os testes
``test_rule_*`` mais abaixo provam que cada regra realmente acusa uma violação (fontes sintéticos).

Desvio consciente (registrado no relatório do P-0, para a A.4.1 da especificação): ``_worker/*`` também pode
importar ``rumbapy._locks`` — o idle do watchdog (D.4 e) precisa de ``file_lock("spawn-<tag>-<L>")`` e a A.4.1 não o
cita. ``rumbapy._version`` NÃO entra: a versão e o protocolo do ``hello``/registro (C.3, D.7) vêm de
``rumbapy._protocol.VERSION``/``PROTOCOL``, que já estão na lista.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import ast
import sys

PKG = Path(__file__).resolve().parents[2] / "src" / "rumbapy"
STDLIB = frozenset(sys.stdlib_module_names)

WORKER_ALLOWED = frozenset({
    "rumbapy._protocol", "rumbapy._ipc", "rumbapy._codec", "rumbapy._buildid", "rumbapy._win32", "rumbapy._paths",
    "rumbapy._log", "rumbapy._types", "rumbapy.errors", "rumbapy.keys",
    "rumbapy._locks",                              # desvio consciente (ver docstring)
})
UPDATE_ALLOWED = frozenset({"rumbapy._bootstrap", "rumbapy._version", "rumbapy._paths", "rumbapy._log",
                            "rumbapy._locks", "rumbapy.errors"})
NO_CTYPES = frozenset({"rumbapy.errors", "rumbapy.keys", "rumbapy.screen", "rumbapy.waits", "rumbapy._codec",
                       "rumbapy._types", "rumbapy._protocol", "rumbapy._buildid", "rumbapy._instances",
                       "rumbapy._worker.structs", "rumbapy._worker.mask"})
CTYPES_FORBIDDEN_ATTRS = frozenset({"windll", "cdll", "oledll", "pydll", "pythonapi", "CDLL", "OleDLL", "PyDLL",
                                    "LibraryLoader"})
PRINT_ALLOWED = {"rumbapy.__main__": None, "rumbapy._log": "notice", "rumbapy._bootstrap": "say"}
# A.3 (NÃO DEVE): módulo -> prefixos proibidos (importações em tempo de execução)
FORBIDDEN_IMPORTS = {
    "rumbapy.session": ("ctypes", "_ctypes", "rumbapy._bridge"),
    "rumbapy._launcher": ("rumbapy._worker",),
    "rumbapy._bridge": ("rumbapy.screen", "rumbapy.recipes", "rumbapy.session", "rumbapy._launcher",
                        "rumbapy._instances", "rumbapy._user32"),
    "rumbapy.waits": ("ctypes", "_ctypes", "socket", "subprocess", "multiprocessing", "rumbapy._bridge",
                      "rumbapy._backend", "rumbapy._worker", "rumbapy._ipc"),
    "rumbapy.recipes": ("ctypes", "_ctypes", "rumbapy._backend", "rumbapy._bridge", "rumbapy._worker"),
}
# código v0.1.5 ainda não substituído pelo P-E (some sozinho quando o marcador sumir do arquivo)
LEGACY_MARKERS = {"rumbapy.api": "from pywinauto import"}


@dataclass(frozen=True)
class Module:
    name: str
    is_package: bool
    source: str
    tree: ast.Module

    @property
    def package(self) -> str:
        return self.name if self.is_package else self.name.rpartition(".")[0]


@dataclass(frozen=True)
class Ref:
    """Uma importação: ``target`` é o nome absoluto do módulo (ou ``pacote.nome`` em ``from pacote import nome``)."""

    target: str
    line: int
    type_checking: bool
    function: str | None


def module_from_source(name: str, source: str, *, is_package: bool = False) -> Module:
    return Module(name, is_package, source, ast.parse(source))


def load_package(root: Path = PKG) -> list[Module]:
    modules = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root.parent).with_suffix("")
        parts = list(rel.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        source = path.read_text(encoding="utf-8")
        modules.append(module_from_source(".".join(parts), source, is_package=is_package))
    return modules


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
        (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _resolve(module: Module, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    base = module.package.split(".")
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def walk(tree: ast.AST) -> Iterator[tuple[ast.AST, bool, str | None]]:
    """Todos os nós com (dentro de ``if TYPE_CHECKING``?, função mais interna)."""
    stack: list[tuple[ast.AST, bool, str | None]] = [(tree, False, None)]
    while stack:
        node, tc, func = stack.pop()
        yield node, tc, func
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend((child, True, func) for child in node.body)
            stack.extend((child, tc, func) for child in node.orelse)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        stack.extend((child, tc, func) for child in ast.iter_child_nodes(node))


def imports_of(module: Module) -> list[Ref]:
    refs = []
    for node, tc, func in walk(module.tree):
        if isinstance(node, ast.Import):
            refs.extend(Ref(alias.name, node.lineno, tc, func) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node)
            if node.module is None or base == "rumbapy":     # "from . import x" / "from rumbapy import x": submódulos
                refs.extend(Ref(f"{base}.{alias.name}", node.lineno, tc, func) for alias in node.names)
            else:
                refs.append(Ref(base, node.lineno, tc, func))
    return refs


def _is_stdlib(target: str) -> bool:
    return target.split(".")[0] in STDLIB


def _under(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def _where(module: Module, line: int) -> str:
    return f"{module.name}:{line}"


# ------------------------------------------------------------------------------------------- regras

def rule_worker_imports(modules: Iterable[Module]) -> list[str]:
    """A.4.1: ``_worker/*`` importa só stdlib, ``rumbapy._worker.*`` e a lista fechada."""
    out = []
    for m in modules:
        if not _under(m.name, "rumbapy._worker"):
            continue
        for ref in imports_of(m):
            if ref.type_checking or _is_stdlib(ref.target) or _under(ref.target, "rumbapy._worker"):
                continue
            if ref.target not in WORKER_ALLOWED:
                out.append(f"{_where(m, ref.line)}: worker importa {ref.target}")
    return out


def rule_bootstrap_and_update(modules: Iterable[Module]) -> list[str]:
    """A.4.2: ``_bootstrap`` só stdlib; ``_update`` só stdlib + lista."""
    out = []
    for m in modules:
        if m.name not in ("rumbapy._bootstrap", "rumbapy._update"):
            continue
        for ref in imports_of(m):
            if _is_stdlib(ref.target):
                continue
            if m.name == "rumbapy._update" and ref.target in UPDATE_ALLOWED:
                continue
            out.append(f"{_where(m, ref.line)}: {m.name} importa {ref.target}")
    return out


def rule_no_ctypes(modules: Iterable[Module]) -> list[str]:
    """A.4.3: módulos puros não importam ctypes; ``errors`` só importa ``screen`` sob ``TYPE_CHECKING``."""
    out = []
    for m in modules:
        refs = imports_of(m)
        if m.name in NO_CTYPES:
            out += [f"{_where(m, r.line)}: {m.name} importa {r.target}" for r in refs
                    if _under(r.target, "ctypes") or _under(r.target, "_ctypes")]
        if m.name == "rumbapy.errors":
            out += [f"{_where(m, r.line)}: errors importa screen fora de TYPE_CHECKING" for r in refs
                    if _under(r.target, "rumbapy.screen") and not r.type_checking]
    return out


def rule_ctypes_loaders(modules: Iterable[Module]) -> list[str]:
    """A.4.4: nada de ``ctypes.windll``/``cdll``/…; só ``ctypes.WinDLL(nome, use_last_error=True)`` em nome privado."""
    out = []
    for m in modules:
        aliases = {"ctypes"}
        winddl_names = set()
        for node, _, _ in walk(m.tree):
            if isinstance(node, ast.Import):
                aliases |= {a.asname or a.name for a in node.names if a.name == "ctypes"}
            elif isinstance(node, ast.ImportFrom) and node.module == "ctypes" and not node.level:
                for alias in node.names:
                    if alias.name in CTYPES_FORBIDDEN_ATTRS:
                        out.append(f"{_where(m, node.lineno)}: from ctypes import {alias.name}")
                    if alias.name == "WinDLL":
                        winddl_names.add(alias.asname or alias.name)
        for node, _, _ in walk(m.tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases \
                    and node.attr in CTYPES_FORBIDDEN_ATTRS:
                out.append(f"{_where(m, node.lineno)}: ctypes.{node.attr}")
            if isinstance(node, ast.Call):
                func = node.func
                is_windll = (isinstance(func, ast.Attribute) and func.attr == "WinDLL" and isinstance(func.value, ast.Name)
                             and func.value.id in aliases) or (isinstance(func, ast.Name) and func.id in winddl_names)
                if is_windll and not any(k.arg == "use_last_error" and isinstance(k.value, ast.Constant)
                                         and k.value.value is True for k in node.keywords):
                    out.append(f"{_where(m, node.lineno)}: WinDLL sem use_last_error=True")
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                func = node.value.func
                if (isinstance(func, ast.Attribute) and func.attr == "WinDLL") or \
                        (isinstance(func, ast.Name) and func.id in winddl_names):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
                        if name and not name.startswith("_"):
                            out.append(f"{_where(m, node.lineno)}: WinDLL em nome público {name!r}")
    return out


def rule_print_pickle_connection(modules: Iterable[Module]) -> list[str]:
    """A.4.5: ``print`` só em ``__main__``/``_log.notice``/``_bootstrap.say``; sem pickle; sem ``Connection.send/recv``."""
    out = []
    for m in modules:
        refs = imports_of(m)
        uses_mp = any(_under(r.target, "multiprocessing") for r in refs)
        out += [f"{_where(m, r.line)}: importa {r.target}" for r in refs
                if r.target.split(".")[0] in ("pickle", "_pickle", "cPickle")]
        for node, _, func in walk(m.tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                allowed = m.name in PRINT_ALLOWED and (PRINT_ALLOWED[m.name] is None or PRINT_ALLOWED[m.name] == func)
                if not allowed:
                    out.append(f"{_where(m, node.lineno)}: print fora de __main__/_log.notice/_bootstrap.say")
            if uses_mp and isinstance(node.func, ast.Attribute) and node.func.attr in ("send", "recv"):
                out.append(f"{_where(m, node.lineno)}: .{node.func.attr}( de Connection (use send_bytes/recv_bytes)")
    return out


def rule_no_pandas_and_python312(modules: Iterable[Module]) -> list[str]:
    """A.4.6: ninguém importa pandas; tudo compila com a gramática do Python 3.12 (substitui o pyright local)."""
    out = []
    for m in modules:
        out += [f"{_where(m, r.line)}: importa pandas" for r in imports_of(m) if _under(r.target, "pandas")]
        try:
            ast.parse(m.source, feature_version=(3, 12))
        except SyntaxError as exc:
            out.append(f"{_where(m, exc.lineno or 0)}: sintaxe além do Python 3.12 ({exc.msg})")
    return out


def rule_a3_prohibitions(modules: Iterable[Module]) -> list[str]:
    """A.3: proibições de responsabilidade que dá para verificar estaticamente."""
    out = []
    for m in modules:
        if m.name == "rumbapy._bootstrap":
            out += [f"{_where(m, r.line)}: _bootstrap importa {r.target}" for r in imports_of(m)
                    if _under(r.target, "rumbapy")]
        for owner, forbidden in FORBIDDEN_IMPORTS.items():
            if not _under(m.name, owner):
                continue
            out += [f"{_where(m, r.line)}: {m.name} não deve importar {r.target}" for r in imports_of(m)
                    if not r.type_checking and any(_under(r.target, f) for f in forbidden)]
    return out


RULES = [rule_worker_imports, rule_bootstrap_and_update, rule_no_ctypes, rule_ctypes_loaders,
         rule_print_pickle_connection, rule_no_pandas_and_python312, rule_a3_prohibitions]


def package_modules() -> list[Module]:
    modules = load_package()
    return [m for m in modules if not (m.name in LEGACY_MARKERS and LEGACY_MARKERS[m.name] in m.source)]


# ------------------------------------------------------------------------------------------- o pacote real

def test_package_was_found() -> None:
    names = {m.name for m in load_package()}
    assert {"rumbapy.errors", "rumbapy._protocol", "rumbapy._worker.guard", "rumbapy._worker.hllapi"} <= names


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.__name__)
def test_package_follows_the_rule(rule) -> None:  # type: ignore[no-untyped-def]
    assert rule(package_modules()) == []


def test_legacy_skip_only_while_legacy() -> None:
    skipped = {m.name for m in load_package()} - {m.name for m in package_modules()}
    assert skipped <= set(LEGACY_MARKERS)


# ------------------------------------------------------------------------------------------- cada regra acusa

def _check(rule, name: str, source: str, *, is_package: bool = False) -> list[str]:  # type: ignore[no-untyped-def]
    return rule([module_from_source(name, source, is_package=is_package)])


@pytest.mark.parametrize("source, bad", [
    ("from ..session import Session\n", True),
    ("from .. import profiles\n", True),
    ("import rumbapy._bridge\n", True),
    ("from rumbapy import _update\n", True),
    ("import rumbapy\n", True),
    ("from .. import errors, keys, _types\nfrom .._codec import decode\nfrom . import guard\nimport json\n", False),
    ("from .._locks import file_lock\nfrom .._protocol import VERSION, PROTOCOL\n", False),
    ("from .._version import __version__\n", True),                  # use _protocol.VERSION
    ("from .. import _version\n", True),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from ..screen import Screen\n", False),
    ("def f():\n    from ..session import Session\n", True),
])
def test_rule_worker_imports(source: str, bad: bool) -> None:
    assert bool(_check(rule_worker_imports, "rumbapy._worker.engine", source)) == bad


def test_rule_worker_package_init() -> None:
    assert _check(rule_worker_imports, "rumbapy._worker", "from . import guard\n", is_package=True) == []
    assert _check(rule_worker_imports, "rumbapy._worker", "from .. import session\n", is_package=True)


@pytest.mark.parametrize("name, source, bad", [
    ("rumbapy._bootstrap", "import json, urllib.request\n", False),
    ("rumbapy._bootstrap", "from . import _version\n", True),
    ("rumbapy._bootstrap", "def heal():\n    from .errors import RumbaError\n", True),
    ("rumbapy._update", "from . import _bootstrap, _paths, _log, _locks, errors\nfrom ._version import x\n", False),
    ("rumbapy._update", "from . import session\n", True),
    ("rumbapy._update", "from ._bridge import get_worker\n", True),
])
def test_rule_bootstrap_and_update(name: str, source: str, bad: bool) -> None:
    assert bool(_check(rule_bootstrap_and_update, name, source)) == bad


@pytest.mark.parametrize("name, source, bad", [
    ("rumbapy.keys", "import ctypes\n", True),
    ("rumbapy._worker.structs", "from ctypes import c_ushort\n", True),
    ("rumbapy._worker.hllapi", "import ctypes\n", False),
    ("rumbapy.errors", "from .screen import Screen\n", True),
    ("rumbapy.errors", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .screen import Screen\n", False),
])
def test_rule_no_ctypes(name: str, source: str, bad: bool) -> None:
    assert bool(_check(rule_no_ctypes, name, source)) == bad


@pytest.mark.parametrize("source, bad", [
    ("import ctypes\nx = ctypes.windll.user32\n", True),
    ("import ctypes as c\nx = c.cdll.msvcrt\n", True),
    ("from ctypes import windll\n", True),
    ("import ctypes\n_k = ctypes.CDLL('kernel32')\n", True),
    ("import ctypes\n_u = ctypes.WinDLL('user32')\n", True),
    ("import ctypes\n_u = ctypes.WinDLL('user32', use_last_error=False)\n", True),
    ("import ctypes\nuser32 = ctypes.WinDLL('user32', use_last_error=True)\n", True),
    ("import ctypes\n_user32 = ctypes.WinDLL('user32', use_last_error=True)\n", False),
    ("from ctypes import WinDLL\n_k = WinDLL('kernel32')\n", True),
    ("import ctypes\ndef load(p):\n    return ctypes.WinDLL(p, use_last_error=True)\n", False),
])
def test_rule_ctypes_loaders(source: str, bad: bool) -> None:
    assert bool(_check(rule_ctypes_loaders, "rumbapy._win32", source)) == bad


@pytest.mark.parametrize("name, source, bad", [
    ("rumbapy.session", "print('x')\n", True),
    ("rumbapy._log", "def notice(m):\n    print(m)\n", False),
    ("rumbapy._log", "def other(m):\n    print(m)\n", True),
    ("rumbapy._bootstrap", "def say(m):\n    print(m)\n", False),
    ("rumbapy.__main__", "def main():\n    print('ok')\n", False),
    ("rumbapy._ipc", "import pickle\n", True),
    ("rumbapy._ipc", "from multiprocessing.connection import PipeConnection\ndef f(c):\n    c.send({})\n", True),
    ("rumbapy._ipc", "import multiprocessing.connection\ndef f(c):\n    return c.recv()\n", True),
    ("rumbapy._ipc", "import multiprocessing.connection\ndef f(c):\n    c.send_bytes(b'')\n", False),
    ("rumbapy._legacy", "import socket\ndef f(s):\n    return s.recv(4096)\n", False),
])
def test_rule_print_pickle_connection(name: str, source: str, bad: bool) -> None:
    assert bool(_check(rule_print_pickle_connection, name, source)) == bad


@pytest.mark.parametrize("source, bad", [
    ("import pandas as pd\n", True),
    ("def f():\n    import pandas\n", True),
    ("type Alias = list[int]\n", False),
    ("def f[T = int](x: T) -> T:\n    return x\n", True),
])
def test_rule_no_pandas_and_python312(source: str, bad: bool) -> None:
    assert bool(_check(rule_no_pandas_and_python312, "rumbapy.screen", source)) == bad


@pytest.mark.parametrize("name, source, bad", [
    ("rumbapy.session", "from . import _bridge\n", True),
    ("rumbapy.session", "import ctypes\n", True),
    ("rumbapy.session", "from . import _backend, waits\n", False),
    ("rumbapy._launcher", "from ._worker import hllapi\n", True),
    ("rumbapy._bridge", "from .screen import Screen\n", True),
    ("rumbapy.waits", "import socket\n", True),
    ("rumbapy.recipes.cics", "from .._backend import Backend\n", True),
    ("rumbapy.recipes.cics", "from ..screen import Screen\n", False),
])
def test_rule_a3_prohibitions(name: str, source: str, bad: bool) -> None:
    assert bool(_check(rule_a3_prohibitions, name, source)) == bad
