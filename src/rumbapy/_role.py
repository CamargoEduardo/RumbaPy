"""Papel do processo que importou o RumbaPy (A.7). Só stdlib, sem efeitos colaterais.

| Papel         | Condição                                          | Update | Opera terminal |
|---------------|---------------------------------------------------|--------|----------------|
| ``legacy-shim`` | ``sys.orig_argv[1:3] == ["-m", "rumbapy.server"]`` | não    | não            |
| ``cli``         | ``sys.orig_argv[1:3] == ["-m", "rumbapy"]``        | sim    | sim            |
| ``user``        | todo o resto (inclusive Python 32-bit com script)  | sim    | sim            |

Não existe papel ``worker`` nem ``RUMBAPY_ROLE`` (C6): o worker 32-bit nunca executa ``rumbapy/__init__.py`` (D.1).
O ``__init__`` repete este cálculo inline quando o próprio ``_role`` não pôde ser importado (A.6).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import sys

Role = Literal["user", "cli", "legacy-shim"]
ROLES: tuple[str, ...] = ("user", "cli", "legacy-shim")
OPERATING_ROLES: frozenset[str] = frozenset({"user", "cli"})    # papéis que atualizam e operam terminais


def detect(orig_argv: Sequence[str] | None = None) -> Role:
    """Papel deste processo a partir de ``sys.orig_argv`` (ou de ``orig_argv``, para testes)."""
    argv = list(sys.orig_argv if orig_argv is None else orig_argv)
    head = argv[1:3]
    if head == ["-m", "rumbapy.server"]:
        return "legacy-shim"
    if head == ["-m", "rumbapy"]:
        return "cli"
    return "user"


def cli_command(orig_argv: Sequence[str] | None = None) -> str | None:
    """Subcomando de ``python -m rumbapy <subcomando>`` (``None`` se o papel não for ``cli`` ou não houver)."""
    argv = list(sys.orig_argv if orig_argv is None else orig_argv)
    if detect(argv) != "cli" or len(argv) < 4:
        return None
    return argv[3]
