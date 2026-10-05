"""Papéis (A.7): ``-m rumbapy`` = cli, ``-m rumbapy.server`` = legacy-shim, todo o resto = user."""
# ruff: noqa: I001
from __future__ import annotations

import sys

import pytest

from rumbapy import _role


@pytest.mark.parametrize(("argv", "role"), [
    (["python", "-m", "rumbapy"], "cli"),
    (["python", "-m", "rumbapy", "update"], "cli"),
    (["python", "-m", "rumbapy.server", "--terminal_type", "D"], "legacy-shim"),
    (["python", "script.py"], "user"),
    (["python"], "user"),
    ([], "user"),
    (["python", "-c", "import rumbapy"], "user"),
    (["python", "-m", "pytest", "tests"], "user"),
    (["python", "-m", "rumbapy.client"], "user"),
    (["python", "-X", "utf8", "-m", "rumbapy"], "user"),          # só sys.orig_argv[1:3] conta (A.7)
    (["C:/py32/python.exe", "meu_script.py"], "user"),           # Python 32-bit rodando script direto
])
def test_detect(argv: list[str], role: str) -> None:
    assert _role.detect(argv) == role


def test_detect_uses_orig_argv_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "rumbapy.server"])
    assert _role.detect() == "legacy-shim"
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "rumbapy", "version"])
    assert _role.detect() == "cli"
    assert _role.cli_command() == "version"


def test_cli_command() -> None:
    assert _role.cli_command(["python", "-m", "rumbapy", "close", "D"]) == "close"
    assert _role.cli_command(["python", "-m", "rumbapy"]) is None
    assert _role.cli_command(["python", "x.py", "update"]) is None


def test_role_has_no_side_effects() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(_role))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] if node.module else "." for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom)}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
