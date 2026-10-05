"""Sonda AO VIVO do L8 (E.8): a v2 neutraliza um servidor 0.1.5 REAL (código do ``master``) rodando em 32-bit.

A lógica mora em ``tests/live/scripts.py`` (``l8``) e roda pelo executor ``tests/live/runner.py`` (a mesma do
``pytest tests/live -m live``), com as salvaguardas dos roteiros da J.6 (``RUMBAPY_LIVE=1``, sem sandbox, branch
``dev``, confirmação ``SIM D`` digitada no console, ``RUMBAPY_HOME`` temporário).

Pré-requisito (fora do repositório): um venv 32-bit com ``pywin32`` e ``pywinauto`` e o código da 0.1.5 exportado:
    py -3.13-32 -m venv %TEMP%\\rumbapy-l8\\venv32 && %TEMP%\\rumbapy-l8\\venv32\\Scripts\\pip install pywin32 pywinauto
    git archive --format=tar master src/rumbapy | tar -x -C %TEMP%\\rumbapy-l8

Nenhum terminal é aberto: o servidor 0.1.5 só carrega a DLL e, ao receber ``disconnect``, chama ``WD_DisconnectPS``
(rc 1, nada conectado). Ele roda num invólucro que sai por ``os._exit(0)`` (ExitProcess: o DllMain libera o slot
HLLAPI) quando o arquivo de parada aparece; nunca é morto. Recusa rodar se a porta 8500 já tiver um ouvinte (pode ser
um servidor 0.1.5 de produção do usuário).

Rode com o Python 64-bit do repositório: ``.venv\\Scripts\\python.exe tools\\research\\live_legacy_probe.py``.
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
    return runner.main(["--script", "l8", *extra])


if __name__ == "__main__":
    sys.exit(main())
