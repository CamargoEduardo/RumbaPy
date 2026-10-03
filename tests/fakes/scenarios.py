"""Fábricas de ``FakeEhllapi`` prontos, para ``--fake-dll tests.fakes.scenarios:<fábrica>`` (D.1) e testes em processo.

O worker chama a fábrica SEM argumentos (``importlib.import_module(mod).<fábrica>()``); por isso todas têm padrões.
Para outros valores, use o nome com ``__<valor>`` (resolvido por ``__getattr__`` deste módulo)::

    --fake-dll tests.fakes.scenarios:hang__WD_CopyPSToString
    --fake-dll tests.fakes.scenarios:slow_host__0p5          # 0p5 = 0.5 (o ponto também vale)
    --fake-dll tests.fakes.scenarios:blank_first_screen__2

Este módulo só depende de ``fake_ehllapi`` (nada de ``rumbapy``): é importado dentro do worker 32-bit.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, FakeSession, make_cics_login_host

import threading

VALID_USER = "A000000"
VALID_PASSWORD = "senha"
HWND_D, PID_D = 0x1000, 4242
HWND_Z, PID_Z = 0x2000, 4343
HWND_A, PID_A = 0x3000, 4444


def _cics_session(fake: FakeEhllapi, letter: str, *, hwnd: int, pid: int, user: str = VALID_USER,
                  password: str = VALID_PASSWORD, expired: bool = False) -> FakeSession:
    session = fake.add_session(letter, hwnd=hwnd, pid=pid)
    env, host = make_cics_login_host(user, password, expired)
    session.load(env)
    session.on_aid = host
    return session


def cics_login(user: str = VALID_USER, password: str = VALID_PASSWORD, *, expired: bool = False) -> FakeEhllapi:
    """BRAZIL_D (24x80) na tela "Select CICS Environment" com o host de logon CICS simulado."""
    fake = FakeEhllapi()
    _cics_session(fake, "D", hwnd=HWND_D, pid=PID_D, user=user, password=password, expired=expired)
    return fake


class _BlankFirstScreen(FakeEhllapi):
    """O PS fica vazio e ocupado (rc 4) por ``seconds`` a partir da 1ª conexão; depois chega a tela do CICS."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.first_screen_seconds = seconds
        self._armed = False
        self.session = self.add_session("D", hwnd=HWND_D, pid=PID_D)
        self._env, self.session.on_aid = make_cics_login_host()
        self.session.load(FakeScreen(lines=[""] * 24, name="blank"))
        self.session.locked_until = float("inf")

    def WD_ConnectPS(self, h: Any, short: Any) -> int:
        rc = super().WD_ConnectPS(h, short)
        if rc in (0, 4, 5) and not self._armed:
            self._armed = True
            timer = threading.Timer(self.first_screen_seconds, self._first_screen)
            timer.daemon = True
            timer.start()
        return rc

    def _first_screen(self) -> None:
        with self._lock:
            self.session.load(self._env)
            self.session.locked_until = 0.0


def blank_first_screen(seconds: float = 2.0) -> FakeEhllapi:
    """Como ``cics_login``, mas a 1ª tela só aparece ``seconds`` s depois da conexão (live: rc 4 com PS vazio)."""
    return _BlankFirstScreen(float(seconds))


def slow_host(response_delay: float = 0.5, lock_visible_after: float = 0.0) -> FakeEhllapi:
    """Como ``cics_login``, mas o host leva ``response_delay`` s para responder a cada AID."""
    fake = cics_login()
    session = fake.sessions["D"]
    session.response_delay = float(response_delay)
    session.lock_visible_after = float(lock_visible_after)
    return fake


def hang(func: str = "WD_CopyPS") -> FakeEhllapi:
    """Como ``cics_login``, mas ``func`` trava para sempre (para o watchdog do worker)."""
    fake = cics_login()
    fake.hang_on = {func if func.startswith("WD_") else f"WD_{func}"}
    return fake


def two_letters() -> FakeEhllapi:
    """BRAZIL_D e BRAZIL_Z registrados ao mesmo tempo (instâncias e HWNDs diferentes)."""
    fake = FakeEhllapi()
    _cics_session(fake, "D", hwnd=HWND_D, pid=PID_D)
    _cics_session(fake, "Z", hwnd=HWND_Z, pid=PID_Z)
    return fake


def make_ims_login_host(valid_user: str = VALID_USER, valid_password: str = VALID_PASSWORD,
                        uss_command: str = "IMSRS", app: str = "RHELP") -> tuple[FakeScreen, Callable[..., None]]:
    """Host IMS simulado (32x80, âncoras da v0.1.5, a validar ao vivo): USS010 -> Welcome -> Start -> aplicação."""
    uss = FakeScreen(name="uss", lines=["", "USS010 ENTER COMMAND"] + [""] * 30, cursor=(5, 1), fields=[
        {"row": 5, "col": 1, "length": 20, "name": "cmd"},
    ])
    welcome = FakeScreen(name="welcome", lines=[""] * 3 + [" " * 43 + "Welcome"] + [""] * 28, fields=[
        {"row": 10, "col": 47, "length": 8, "name": "uid"},
        {"row": 11, "col": 47, "length": 8, "name": "pwd", "hidden": True},
        {"row": 20, "col": 10, "length": 40, "name": "msg", "protected": True},
    ])
    start = FakeScreen(name="start", lines=[""] * 15 + [" " * 19 + "Start"] + [""] * 16, fields=[
        {"row": 16, "col": 47, "length": 8, "name": "app"},
    ])
    menu = FakeScreen(name="app", lines=["", " " * 9 + "RHELP MAIN MENU"] + [""] * 30, fields=[
        {"row": 1, "col": 2, "length": 70, "name": "cmd"},
    ])

    def host(session: FakeSession, aid: str, fields: dict) -> None:
        if aid != "ENTER":
            return
        if session.screen_name == "uss" and fields.get("cmd", "").strip().upper() == uss_command:
            session.load(welcome)
        elif session.screen_name == "welcome":
            if fields.get("uid") == valid_user and fields.get("pwd") == valid_password:
                session.load(start)
            else:
                session.load(welcome)
                msg = session.fields[2]
                session.buffer[msg.start - 1:msg.start - 1 + 14] = b"SENHA INVALIDA"
        elif session.screen_name == "start" and fields.get("app", "").strip().upper() == app:
            session.load(menu)

    return uss, host


def ims_login(user: str = VALID_USER, password: str = VALID_PASSWORD) -> FakeEhllapi:
    """IMS_A (32x80, modelo 3) na tela USS com o host de logon IMS simulado."""
    fake = FakeEhllapi()
    session = fake.add_session("A", rows=32, cols=80, hwnd=HWND_A, pid=PID_A)
    uss, host = make_ims_login_host(user, password)
    session.load(uss)
    session.on_aid = host
    return fake


_PARAMETRIC: dict[str, Callable[[str], Any]] = {
    "blank_first_screen": lambda raw: blank_first_screen(float(raw.replace("p", "."))),
    "slow_host": lambda raw: slow_host(float(raw.replace("p", "."))),
    "hang": lambda raw: hang(raw),
}


def __getattr__(name: str) -> Callable[[], FakeEhllapi]:
    """``hang__WD_CopyPSToString``, ``slow_host__0p5``, ``blank_first_screen__2`` -> fábrica sem argumentos."""
    base, sep, raw = name.partition("__")
    if sep and raw and base in _PARAMETRIC:
        build = _PARAMETRIC[base]

        def factory() -> FakeEhllapi:
            return build(raw)

        factory.__name__ = name
        return factory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
