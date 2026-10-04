"""Sonda AO VIVO do worker 32-bit da v2 contra a ehlapi32.Dll real: somente leitura (L1) ou escrever e apagar (L4).

Esta sonda só chama o executor ``tests/live/runner.py`` (lógica em ``tests/live/scripts.py``), com as salvaguardas
da J.6: ``RUMBAPY_LIVE=1``, sem sandbox, branch ``dev``, nenhuma janela de BRAZIL_D/BRAZIL_Z aberta, confirmação
``SIM D`` digitada no console (``CONIN$``), ``RUMBAPY_HOME`` temporário, código do ``HEAD`` (``git archive``) e o
guard do roteiro no worker:

* sem opção: ``--script l1`` (guard ``readonly``): ``open`` minimizado, 1ª tela, campos, cursor, ``read``, latência
  do ``snapshot`` e ``close``;
* ``--write-erase``: ``--script l4`` (guard ``text``: só escrita local, nenhuma AID): ``TESTE çãÉ€`` vai e volta pelo
  PS local, ``Truncated``/``ProtectedField`` sem copiar nada, apaga o campo e fecha.

A versão anterior (worker lançado à mão, launcher por caminho fixo, sem a confirmação no console) está no histórico
do Git.

Rode com o Python 64-bit do repositório (``set RUMBAPY_LIVE=1``):
    .venv\\Scripts\\python.exe tools\\research\\live_worker_probe.py [--write-erase] [--code tree] [--keep-home]
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
    script = "l4" if "--write-erase" in extra else "l1"
    return runner.main(["--script", script, *(a for a in extra if a != "--write-erase")])


if __name__ == "__main__":
    sys.exit(main())
