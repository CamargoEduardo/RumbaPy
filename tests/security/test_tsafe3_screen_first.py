"""T-SAFE-3 consolidado (J.5): nada é digitado antes de a tela de resposta chegar.

* ``send_keys("C<Enter>x<Enter>")`` com o host lento (``response_delay``) e o bloqueio do teclado escondido no começo
  (``lock_visible_after``, inclusive maior que o atraso: bloqueio nunca observado): o ``x`` e a 2ª AID só saem
  depois de a tela do signon estar no lugar;
* a compat (``copy_string_to_ps`` depois de ``send_key("ENTER")``) também só escreve na tela nova;
* ``rumbapy.open`` com ``blank_first_screen(2)`` (PS vazio e ocupado por 2 s) só devolve a ``Session`` depois da 1ª
  tela, sem ter mandado nada ao host.

Complementa ``tests/unit/client/session/test_session_io.py`` (``test_send_keys_waits_screen_between_aids_t_safe_3``)
e ``tests/unit/client/launcher/test_open.py`` (``Launcher.open`` com o mesmo cenário). Tudo com dublês.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit.client.launcher.world import LAUNCHER_EXE, FakeProc, World
from tests.unit.client.session.support import aids, make_session
from tests.fakes.fake_ehllapi import FakeEhllapi, FakeSession, _in_bytes
from tests.fakes.fake_backend import InProcessBackend
from rumbapy import session as session_mod
from tests.fakes import scenarios
from rumbapy import compat
import rumbapy

import time

import pytest


class Spy:
    """Registra, a cada ``WD_SendKey``/``WD_CopyStringToPS``, a tela em que o "host" estava e se havia resposta
    pendente (a AID anterior ainda sem resposta)."""

    def __init__(self, fake: FakeEhllapi, letter: str = "D") -> None:
        self.fake, self.session = fake, fake.sessions[letter]
        self.events: list[tuple[str, bytes, str, int, float]] = []
        for name in ("WD_SendKey", "WD_CopyStringToPS"):
            original = getattr(fake, name)

            def spy(*args: Any, _original: Any = original, _name: str = name) -> int:
                data = _in_bytes(args[1] if _name == "WD_SendKey" else args[2])
                self.events.append((_name, data, self.session.screen_name, self.session.pending_responses,
                                    time.monotonic()))
                return _original(*args)

            setattr(fake, name, spy)

    def typed_on(self, needle: bytes) -> list[tuple[str, int]]:
        """``(tela, respostas pendentes)`` de cada envio cujo corpo contém ``needle``."""
        return [(screen, pending) for _n, data, screen, pending, _t in self.events if needle in data]


@pytest.mark.parametrize(("delay", "visible_after"), [(0.5, 0.3), (0.5, 0.0), (0.4, 0.6)],
                         ids=["bloqueio-visivel-tarde", "bloqueio-visivel", "bloqueio-nunca-visivel"])
def test_send_keys_waits_for_the_new_screen_before_typing(delay: float, visible_after: float) -> None:
    fake = scenarios.slow_host(delay, lock_visible_after=visible_after)
    spy = Spy(fake)
    t, _, _ = make_session(fake, timeout=5.0)
    try:
        start = time.monotonic()
        t.send_keys("C<Enter>x<Enter>")
        assert aids(fake) == ["ENTER", "ENTER"]
        assert spy.typed_on(b"x") and all(screen == "signon" and pending == 0
                                          for screen, pending in spy.typed_on(b"x"))
        enters = [ts for _n, data, _s, _p, ts in spy.events if data.endswith(b"@E")]
        assert len(enters) == 2 and enters[1] - enters[0] >= delay - 0.05
        assert time.monotonic() - start >= 2 * delay - 0.1
        assert spy.session.pending_responses == 0 and t.state == "ok"
    finally:
        t.release()


def test_press_returns_only_after_the_screen_changes() -> None:
    fake = scenarios.slow_host(0.5, lock_visible_after=0.6)
    t, _, _ = make_session(fake, timeout=5.0)
    try:
        t.fill(22, 56, "C")
        t.enter()
        assert fake.sessions["D"].screen_name == "signon"
        assert t.read(4, 6, 8) == "TERMINAL"                       # a leitura logo depois já vê a tela nova
    finally:
        t.release()


def test_compat_writes_only_on_the_new_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "_find_python32", lambda explicit: Path("C:/py32/python.exe"))
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    fake = scenarios.slow_host(0.5, lock_visible_after=0.3)
    spy = Spy(fake)
    sessions: list[Any] = []

    def factory(action: str, terminal: str, *, python32: Any = None, timeout: Any = None) -> Any:
        t, _, _ = make_session(fake, terminal, timeout=5.0)
        sessions.append(t)
        return t

    client = compat.RumbaClient("D", _session_factory=factory)
    try:
        assert client.copy_string_to_ps(22, 56, "C") is True
        assert client.send_key("ENTER") is True
        assert client.copy_string_to_ps(12, 21, "x") is True
        assert spy.typed_on(b"x") == [("signon", 0)]
        assert aids(fake) == ["ENTER"]
    finally:
        for t in sessions:
            t.release()


def test_open_with_blank_first_screen_returns_only_after_the_first_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """``rumbapy.open`` (API pública) com o ``Launcher`` real sobre ``FakeUser32``/``InProcessBackend``."""
    from rumbapy._launcher import Launcher

    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    world = World()
    fake = scenarios.blank_first_screen(2)
    pending: FakeSession = fake.sessions.pop("D")                 # só registra depois do "Popen" do launcher
    admin = InProcessBackend(fake, "D", mode="admin", lease=True)
    owner = InProcessBackend(fake, "D", share=admin)

    def spawn(argv: list[str]) -> FakeProc:
        world.spawned.append(list(argv))
        world.user32.add_process(9000, "host access web launcher.exe")
        world.user32.add_window("BRAZIL_D - Pro Client", pid=9000, hwnd=pending.hwnd)
        fake.sessions["D"] = pending
        return FakeProc(9000)

    def launcher_factory(user32: Any, get_backend: Any) -> Launcher:
        return Launcher(world.user32, lambda letter, mode: admin if mode == "admin" else owner, spawn,
                        find_worker=lambda letter: None, find_launcher=lambda: LAUNCHER_EXE,
                        neutralize=lambda letter: None, process_alive=lambda pid, created: True,
                        process_created=lambda pid: 1)

    session_mod._set_factories(backend_factory=lambda profile, python32: None, user32_factory=lambda: world.user32,
                               launcher_factory=launcher_factory)
    try:
        start = time.monotonic()
        t = rumbapy.open("D", window="minimized", timeout=20)
        elapsed = time.monotonic() - start
        try:
            assert elapsed >= 1.9                                   # a 1ª tela só chega 2 s depois da conexão
            assert "Select CICS Environment" in t.screen.text
            assert t.read(14, 26, 24) == "Select CICS Environment:"
            assert world.spawned and t.info.via == "launcher"
            assert not [c for c in fake.calls if c[0] in ("WD_SendKey", "WD_CopyStringToPS", "WD_CopyStringToField")]
            assert pending.aid_log == []
        finally:
            t.release()
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)
