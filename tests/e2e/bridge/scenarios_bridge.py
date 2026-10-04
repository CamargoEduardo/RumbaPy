"""Fábricas de cenário para os e2e da ponte (``--fake-dll tests.e2e.bridge.scenarios_bridge:<fábrica>``).

Rodam DENTRO do worker 32-bit (importadas via ``--fake-path <repo>``). Cada worker novo chama a fábrica de novo; um
arquivo-marcador em ``RUMBAPY_HOME`` distingue o 1º worker dos seguintes (para "trava só no 1º" e "hwnd novo no 2º").
O watchdog do worker é encurtado só aqui (``DEFAULT_LIMIT``), para os testes não esperarem 20 s.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.fakes.fake_ehllapi import FakeEhllapi
from tests.fakes import scenarios

import os

WATCHDOG_S = 2.0
HWND_NEW = 0x5000


def _first(name: str) -> bool:
    """``True`` só na 1ª chamada (entre processos) para ``name`` nesta pasta de dados."""
    flag = Path(os.environ["RUMBAPY_HOME"]) / f"scenario-{name}.flag"
    try:
        fd = os.open(flag, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _short_watchdog() -> None:
    from rumbapy._worker import watchdog

    watchdog.DEFAULT_LIMIT = WATCHDOG_S


def cics() -> FakeEhllapi:
    """O ``cics_login`` de sempre (atalho com nome curto)."""
    return scenarios.cics_login()


def hang_read_once() -> FakeEhllapi:
    """1º worker: ``WD_CopyPSToString`` trava (watchdog de 2 s); os seguintes funcionam (mesmo hwnd)."""
    fake = scenarios.cics_login()
    if _first("hang_read_once"):
        _short_watchdog()
        fake.hang_on.add("WD_CopyPSToString")
    return fake


def hang_send_once() -> FakeEhllapi:
    """1º worker: ``WD_SendKey`` trava (watchdog de 2 s); os seguintes funcionam."""
    fake = scenarios.cics_login()
    if _first("hang_send_once"):
        _short_watchdog()
        fake.hang_on.add("WD_SendKey")
    return fake


def hang_read_then_new_hwnd() -> FakeEhllapi:
    """1º worker: ``WD_CopyPSToString`` trava; o 2º vê a sessão D numa janela nova (outra instância)."""
    first = _first("hang_read_then_new_hwnd")
    fake = FakeEhllapi()
    if first:
        _short_watchdog()
        scenarios._cics_session(fake, "D", hwnd=scenarios.HWND_D, pid=scenarios.PID_D)
        fake.hang_on.add("WD_CopyPSToString")
    else:
        scenarios._cics_session(fake, "D", hwnd=HWND_NEW, pid=scenarios.PID_D + 1)
    return fake


STOP_DELAY_S = 2.5


def slow_stop() -> FakeEhllapi:
    """``WD_ResetSystem`` (só na parada limpa, D.4) demora ``STOP_DELAY_S``: o worker fica em ``stopping`` com o pipe
    aberto tempo bastante para outro cliente bater nele (D.8 "stopping -> espera")."""
    import time

    from tests.fakes.fake_ehllapi import _FakeFunction

    fake = scenarios.cics_login()
    original = fake.WD_ResetSystem

    def slow(h: object) -> int:
        time.sleep(STOP_DELAY_S)
        return original._fn(h)

    fake.WD_ResetSystem = _FakeFunction(slow, "WD_ResetSystem", fake)
    return fake
