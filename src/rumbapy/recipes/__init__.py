"""Receitas (B.8): fluxos de logon e detectores de erro do host, escritos sobre a ``Session`` pública.

``t.cics`` (perfis ``kind="cics"``: D, Z) e ``t.ims`` (perfis ``kind="ims"``: A). Toda receita só escreve depois de
reconhecer a tela esperada, confere que o campo da senha é **oculto** antes de escrevê-la e nunca envia ao host nada
além do necessário ao logon.
"""
# ruff: noqa: I001
from __future__ import annotations

from .cics import Cics
from .ims import Ims

__all__ = ["Cics", "Ims"]
