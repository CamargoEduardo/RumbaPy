"""Módulo legado da v0.1.5 (B.9.2): ``from rumbapy.client import RumbaClient`` continua funcionando.

Na v2 não há servidor TCP nem pacotes no Python 32-bit: ``RumbaClient`` vem de ``rumbapy.compat``;
``update_python_64bit()`` força o auto-update (instala e levanta ``UpdateRequired``, nunca reexecuta);
``ensure_python_32bit``/``update_python_32bit`` só validam o Python 32-bit; ``find_python_32bit()`` o procura.
"""
# ruff: noqa: I001
from __future__ import annotations

from .compat import (Mnemonic, RumbaClient, ensure_python_32bit, find_python_32bit, update_python_32bit,
                     update_python_64bit)
from ._version import GIT_URL

RUMBAPY_REPO = GIT_URL           # "https://github.com/CamargoEduardo/RumbaPy.git" (mesmo valor da v0.1.5)

__all__ = ["RumbaClient", "Mnemonic", "RUMBAPY_REPO", "update_python_64bit", "ensure_python_32bit",
           "update_python_32bit", "find_python_32bit"]
