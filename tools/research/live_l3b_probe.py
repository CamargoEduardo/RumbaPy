"""Sonda AO VIVO do L3b (J.6, R8): uma conexão HLLAPI do D sai por ``os._exit(3)`` SEM ``WD_DisconnectPS`` (como o
watchdog do worker) e o worker novo mede o rc e o tempo do ``WD_ConnectPS``.

Esta sonda só chama o executor ``tests/live/runner.py --script l3b`` (lógica em ``tests/live/scripts.py``), com as
salvaguardas da J.6: ``RUMBAPY_LIVE=1``, sem sandbox, branch ``dev``, nenhuma janela de BRAZIL_D/BRAZIL_Z aberta,
confirmação ``SIM D`` digitada no console (``CONIN$``), ``RUMBAPY_GUARD=readonly`` no worker e ``GuardedDll``
readonly também no processo de teste (que tem watchdog próprio: ``os._exit(9)`` se uma chamada à DLL travar).

A versão anterior (avulsa, executada em 2026-10-04: ConnectPS rc 0 em ~14 ms depois do ``os._exit(3)``) carregava a
DLL sem guard e abria o launcher por caminho fixo; está no histórico do Git.

Rode com o Python 64-bit do repositório (``set RUMBAPY_LIVE=1``):
    .venv\\Scripts\\python.exe tools\\research\\live_l3b_probe.py [--code tree] [--keep-home]
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
    return runner.main(["--script", "l3b", *extra])


if __name__ == "__main__":
    sys.exit(main())
