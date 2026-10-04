"""Roteiros AO VIVO da API v2 (J.6) contra o RUMBA real, com o guard do worker SEMPRE ligado.

A lógica (validada ao vivo em 2026-10-03/04) mora em ``tests/live`` e é a mesma da suíte pytest
(``pytest tests/live -m live``); esta sonda só chama o executor ``tests/live/runner.py``.

Rode com o Python 64-bit do repositório (``set RUMBAPY_LIVE=1``):
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script <roteiro> [--code tree] [--keep-home]

    l1      readonly  abrir minimizado/ler/fechar (latência do snapshot)
    l2      readonly  2 instâncias -> open fecha as duas
    l3      readonly  lease entre processos, evict, reciclagem
    l3b     readonly  conexão que sai por os._exit(3) sem DisconnectPS -> ConnectPS do worker novo
    l4      text      escrever e apagar (TESTE çãÉ€), sem AID
    l5      readonly  show/minimize com leituras
    l6      login     logon CICS, credenciais digitadas
    l8      readonly  neutralização de um servidor 0.1.5 real (sem terminal; = live_legacy_probe.py)
    l9      readonly  BRAZIL_Z aberto + open D
    compat  login     script da v0.1.5 sem alteração
    (l7 = logon RHELP: só com RUMBAPY_LIVE_L7_AUTORIZADO=1, depois de autorização explícita do dono)

* O código testado é o do ``HEAD`` (``git archive`` para uma pasta temporária; ``--code tree`` usa ``src/``).
* ``RUMBAPY_DEV=1`` (sem auto-update), ``RUMBAPY_HOME`` temporário e ``RUMBAPY_GUARD`` do roteiro: ``readonly`` recusa
  TODA tecla e escrita; ``text`` libera só escrita local (nenhuma AID/tecla local); ``login`` libera texto e ENTER.
* Recusa rodar se já houver janela de BRAZIL_D/BRAZIL_Z aberta (não mexe na sessão de ninguém), fora da branch
  ``dev``, com ``RUMBAPY_GUARD`` (teto do dono) mais restritivo que o do roteiro ou sem a confirmação ``SIM <letra>``
  digitada no console (``CONIN$``; sem console, recusa).
* L6/compat pedem usuário e senha no console (senha sem eco) e NUNCA mostram os valores: o relatório só diz se a
  senha apareceu (não deveria) em algum log/repr/exceção, e onde (as ocorrências viram ``***``).
* O relatório traz o registro do guard tirado do log do worker (linhas ``op=`` de escrita/AID/conexão com ``rc``,
  AIDs enviadas, ``op=connect``, rc do NORESET); o ``RUMBAPY_HOME`` temporário é apagado (``--keep-home`` mantém).
* No fim: ``close`` das instâncias, ``shutdown`` dos workers e conferência de que não sobrou janela.

O relatório completo vai para ``docs/interno/referencia/`` (fora do Git); no console sai o mesmo JSON (sem as telas).
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
    return runner.main(argv)


if __name__ == "__main__":
    sys.exit(main())
