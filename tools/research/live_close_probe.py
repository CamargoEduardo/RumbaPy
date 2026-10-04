"""Sonda AO VIVO do fechamento (E.4): quais janelas a instância tem e quando a DLL volta a responder.

A lógica mora em ``tests/live/scripts.py`` (``close_probe``) e roda pelo executor ``tests/live/runner.py`` com as
mesmas salvaguardas dos roteiros da J.6 (``RUMBAPY_LIVE=1``, sem sandbox, branch ``dev``, nenhuma janela de
BRAZIL_D/BRAZIL_Z aberta, confirmação ``SIM D`` digitada no console, ``RUMBAPY_GUARD=readonly``, ``RUMBAPY_HOME``
temporário). Passos:
  1. ``rumbapy.open("D", window="minimized")``; lista TODAS as janelas de nível superior do PID do launcher
     (título, classe, visível, dono) — só leitura.
  2. ``evict`` pela conexão admin (o worker faz ``WD_DisconnectPS``).
  3. ``WM_CLOSE`` só na janela principal (raiz do HWND do HLLAPI) e acompanhamento SEM chamar a DLL: janela some?
     processo do launcher sai? (``IsWindow``/``process_alive`` a cada 100 ms).
  4. Só depois de o processo sair (ou de 30 s): ``sessions`` (``WD_QuerySessions``) cronometrado.
  5. Limpeza do executor: ``rumbapy.close("D")`` se sobrou algo, ``shutdown`` do worker, conferência final.

Rode com o Python 64-bit do repositório: ``.venv\\Scripts\\python.exe tools\\research\\live_close_probe.py``.
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
    return runner.main(["--script", "close", *extra])


if __name__ == "__main__":
    sys.exit(main())
