"""Sonda AO VIVO e SOMENTE LEITURA de uma sessão RUMBA: janela minimizada/mostrada e leituras em cada estado.

Esta sonda só chama o executor ``tests/live/runner.py --script l5`` (lógica em ``tests/live/scripts.py``), com as
salvaguardas da J.6: ``RUMBAPY_LIVE=1``, sem sandbox, branch ``dev``, nenhuma janela de BRAZIL_D/BRAZIL_Z aberta,
confirmação ``SIM D`` digitada no console (``CONIN$``), worker 32-bit com ``RUMBAPY_GUARD=readonly`` (nenhuma tecla,
nenhuma escrita), ``RUMBAPY_HOME`` temporário e relatório em ``docs/interno/referencia/``.

A versão da 1ª validação (Python 32-bit chamando ``WD_*`` direto, sem guard, com o launcher por caminho fixo; dela
vieram o encoding cp1252, o HWND da janela-raiz e o ``WD_ShowSession`` que não minimiza, ``docs/RUMBA_EHLLAPI.md``
§11.1) está no histórico do Git.

Rode com o Python 64-bit do repositório (``set RUMBAPY_LIVE=1``):
    .venv\\Scripts\\python.exe tools\\research\\live_session_probe.py [--code tree] [--keep-home]
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.live import runner  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    extra = list(sys.argv[1:] if argv is None else argv)
    return runner.main(["--script", "l5", *extra])


if __name__ == "__main__":
    sys.exit(main())
