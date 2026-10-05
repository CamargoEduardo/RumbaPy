"""Auxiliares dos testes da ``Session``, das receitas e da compat (pacote P-E).

Tudo em processo: ``InProcessBackend`` (``Dispatcher`` do worker sobre o ``FakeEhllapi``) e ``FakeUser32``. Nada carrega a
DLL real, abre janelas ou fala com o mainframe. As fábricas de cenário novas ficam aqui (os cenários comuns vêm de
``tests/fakes/scenarios.py`` e ``make_cics_login_host``).
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, FakeSession, _in_bytes
from tests.fakes.fake_backend import InProcessBackend
from tests.fakes.fake_user32 import FakeUser32
from tests.fakes import scenarios

from rumbapy._types import OpenInfo
from rumbapy.session import Session
from rumbapy import profiles

import time

LAUNCHER_IMAGE = "host access web launcher.exe"


def info_from(result: Any, *, via: str = "attach") -> OpenInfo:
    return OpenInfo(launched=via == "launcher", closed_instances=0, hwnd=result.hwnd, hwnd_pid=result.hwnd_pid,
                    seconds_to_register=None, seconds_to_connect=0.0, rows=result.rows or 24,
                    cols=result.cols or 80, via=via)


def connect(fake: FakeEhllapi, letter: str = "D") -> tuple[InProcessBackend, Any]:
    backend = InProcessBackend(fake, letter)
    result = backend.connect()
    assert result.connected, result
    return backend, result


def make_session(fake: FakeEhllapi | None = None, letter: str = "D", *, timeout: float = 2.0,
                 user32: Any = None, close_on_exit: bool = False) -> tuple[Session, InProcessBackend, FakeEhllapi]:
    """``Session`` conectada sobre o ``FakeEhllapi`` (padrão: ``scenarios.cics_login()``)."""
    if fake is None:
        fake = scenarios.ims_login() if letter == "A" else scenarios.cics_login()
    backend, result = connect(fake, letter)
    session = Session(backend, profiles.resolve(letter), info_from(result), timeout=timeout, user32=user32,
                      close_on_exit=close_on_exit)
    return session, backend, fake


def fake_session(fake: FakeEhllapi, letter: str = "D") -> FakeSession:
    return fake.sessions[letter]


def aids(fake: FakeEhllapi, letter: str = "D") -> list[str]:
    """AIDs que o "host" recebeu, em ordem (T-SAFE-2)."""
    return [aid for aid, _ in fake.sessions[letter].aid_log]


def send_calls(fake: FakeEhllapi) -> list[bytes]:
    out = []
    for name, args in fake.calls:
        if name == "WD_SendKey":
            out.append(_in_bytes(args[0]))
    return out


def wait_until(predicate: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ------------------------------------------------------------------------------------------- cenários extras (P-E)

def table_screen() -> FakeEhllapi:
    """D com uma tela de campos variados: (3,14) desprotegido de 8, (5,10) protegido, (7,30) numérico de 5,
    (9,20) oculto de 8; linha 1 = "VOLVO SP0001T"."""
    fake = FakeEhllapi()
    session = fake.add_session("D", hwnd=scenarios.HWND_D, pid=scenarios.PID_D)
    session.load(FakeScreen(name="tabela", lines=[" VOLVO SP0001T", "", "  PN:"] + [""] * 21, fields=[
        {"row": 3, "col": 14, "length": 8, "name": "pn"},
        {"row": 5, "col": 10, "length": 20, "name": "titulo", "protected": True, "text": "TITULO"},
        {"row": 7, "col": 30, "length": 5, "name": "custo"},
        {"row": 9, "col": 20, "length": 8, "name": "pwd", "hidden": True},
        {"row": 23, "col": 7, "length": 60, "name": "msg", "protected": True},
    ]))
    return fake


def echo_host(fake: FakeEhllapi, letter: str = "D", message: str = "ATUALIZADO") -> None:
    """Cada ENTER escreve ``message`` + contador em (23,7) (a tela muda a cada AID)."""
    session = fake.sessions[letter]
    count = {"n": 0}

    def host(s: FakeSession, aid: str, fields: dict) -> None:
        count["n"] += 1
        text = f"{message} {count['n']}".encode("cp1252")
        start = s.pos(23, 7) - 1
        s.buffer[start:start + len(text)] = text

    session.on_aid = host


@dataclass
class FakeLauncher:
    """Dublê do ``_launcher.Launcher`` (P-C) para os testes de ``open``/``attach``/``close`` da ``session``."""

    fake: FakeEhllapi
    letter: str = "D"
    calls: list[tuple[str, dict]] = field(default_factory=list)
    backends: list[InProcessBackend] = field(default_factory=list)
    closed: int = 0

    def _opened(self, via: str) -> Any:
        from rumbapy._launcher import Opened

        backend, result = connect(self.fake, self.letter)
        backend.bind_instance(result.hwnd)
        self.backends.append(backend)
        return Opened(backend, info_from(result, via=via), profiles.resolve(self.letter), None)

    def open(self, profile: Any, *, reuse: bool = False, window: str = "normal", timeout: float | None = None) -> Any:
        self.calls.append(("open", {"profile": profile.letter, "reuse": reuse, "window": window, "timeout": timeout}))
        return self._opened("launcher")

    def attach(self, profile: Any, timeout: float = 10.0) -> Any:
        self.calls.append(("attach", {"profile": profile.letter, "timeout": timeout}))
        if self.letter not in self.fake.sessions:
            from rumbapy.errors import TerminalNotOpen

            raise TerminalNotOpen.make(L=profile.letter, terminal=profile.letter, rc=1)
        return self._opened("attach")

    def close_all(self, profile: Any, *, timeout: float = 15.0) -> int:
        self.calls.append(("close_all", {"profile": profile.letter, "timeout": timeout}))
        self.closed += 1
        return 1


def user32_with_window(hwnd: int = scenarios.HWND_D, pid: int = scenarios.PID_D,
                       title: str = "BRAZIL_D - Pro Client") -> FakeUser32:
    user32 = FakeUser32()
    user32.add_process(pid, LAUNCHER_IMAGE)
    user32.add_window(title, pid=pid, hwnd=hwnd)
    return user32
