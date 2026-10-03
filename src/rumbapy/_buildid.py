"""Identidade do código em disco (H.10): o mesmo cálculo no cliente 64-bit e no worker 32-bit. Puro (só lê arquivos).

Calculado no ``import`` (antes e depois dos imports), antes de cada spawn, pelo worker no boot e ao fim de cada
lease. Qualquer mudança num ``.py``, ``.js`` ou ``.json`` do pacote muda o resultado; ``__pycache__`` não conta.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import hashlib
import os

SUFFIXES = frozenset({".py", ".js", ".json"})


def compute(pkgdir: str | os.PathLike[str]) -> str:
    """16 hex do SHA-256 de (caminho relativo POSIX + NUL + bytes + NUL) de cada arquivo, em ordem."""
    files = sorted(p for p in Path(pkgdir).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix in SUFFIXES)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(pkgdir).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]
