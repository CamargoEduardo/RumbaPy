"""Configuração dos roteiros ao vivo (J.6).

* Sem ``RUMBAPY_LIVE=1`` o ``tests/conftest.py`` raiz pula todo teste marcado ``live`` (e liga o sandbox); com
  ``RUMBAPY_SANDBOX=1`` o próprio teste pula (``support.skip_reason``). No CI: ``-m "not live"``.
* O processo do pytest NUNCA importa ``rumbapy`` nos roteiros: cada roteiro roda em ``python -m tests.live.runner``
  (processo, ``RUMBAPY_HOME`` e worker novos), com ``RUMBAPY_DEV=1`` e o guard do roteiro.
* ``RUMBAPY_GUARD`` (opcional) é o teto do dono: ``readonly`` roda só os roteiros readonly, ``text`` acrescenta o L4,
  ``login`` libera L6/L7/compat; valor inválido (ou ``none``) pula tudo (``support.guard_ceiling_reason``).
* ``CONIN$``: sem console todo roteiro é pulado (E30); com console, o executor (que herda o console) pede
  ``SIM <letra>`` e, nos roteiros de login, usuário e senha (sem eco). Nada disso passa por variável de ambiente.
* Rode: ``set RUMBAPY_LIVE=1`` e ``.venv\\Scripts\\python.exe -m pytest tests/live -m live -p no:randomly -x -rs``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator

from tests.live import support

import warnings
import sys
import os

import pytest

LIVE = os.environ.get("RUMBAPY_LIVE") == "1"


@pytest.fixture(scope="session", autouse=True)
def _live_final_windows_check() -> Iterator[None]:
    """No fim da sessão ao vivo: confere (só lendo) que não sobrou janela de terminal."""
    yield
    if LIVE and os.environ.get("RUMBAPY_SANDBOX") != "1":
        try:
            left = support.terminal_windows(("D", "Z", "A"))
        except OSError:
            return
        if left:
            warnings.warn(f"sobraram {len(left)} janela(s) de terminal depois dos roteiros ao vivo", stacklevel=1)


@pytest.fixture
def no_rumbapy_in_pytest() -> None:
    """O pytest dos roteiros não pode ter importado ``rumbapy`` (auto-update e logs iriam para a pasta real)."""
    if LIVE and "rumbapy" in sys.modules:
        pytest.fail("rumbapy foi importado no processo do pytest ao vivo; rode só tests/live", pytrace=False)
