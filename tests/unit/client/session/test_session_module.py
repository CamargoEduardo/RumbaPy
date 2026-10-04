"""``rumbapy.open/attach/close`` (B.2) com fábricas injetadas (``session._set_factories``) e a janela (E.6)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import ParameterError, RumbaError, TerminalNotOpen, UnknownTerminal
from tests.unit.client.session.support import FakeLauncher, connect, make_session, table_screen, user32_with_window
from tests.fakes.fake_backend import InProcessBackend
from rumbapy._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE
from tests.fakes import scenarios
from rumbapy import session as session_mod

import rumbapy

import pytest


@pytest.fixture
def launcher(monkeypatch):
    fake = scenarios.cics_login()
    launcher = FakeLauncher(fake)
    user32 = user32_with_window()
    updates: list[str] = []
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: updates.append("open"))
    seen: dict = {}

    def backend_factory(profile, python32):
        seen["python32"] = python32
        return None

    session_mod._set_factories(backend_factory=backend_factory, user32_factory=lambda: user32,
                               launcher_factory=lambda u32, get: launcher)
    launcher.updates = updates          # type: ignore[attr-defined]
    launcher.seen = seen                # type: ignore[attr-defined]
    launcher.user32 = user32            # type: ignore[attr-defined]
    yield launcher
    session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


def test_open_defaults_and_update_check(launcher, monkeypatch):
    monkeypatch.delenv("RUMBAPY_OPEN_TIMEOUT", raising=False)
    t = rumbapy.open("brazil_d", python32="C:/py32/python.exe")
    try:
        assert launcher.calls[0] == ("open", {"profile": "D", "reuse": False, "window": "normal", "timeout": 90.0})
        assert launcher.updates == ["open"]
        assert launcher.seen["python32"] == "C:/py32/python.exe"
        assert t.terminal == "D" and t.info.via == "launcher" and t.state == "ok"
        assert t.read(14, 26, 23) == "Select CICS Environment"
    finally:
        t.release()


def test_open_env_timeout_reuse_and_window(launcher, monkeypatch):
    monkeypatch.setenv("RUMBAPY_OPEN_TIMEOUT", "12")
    t = rumbapy.open("D", reuse=True, window="minimized")
    t.release()
    assert launcher.calls[0][1] == {"profile": "D", "reuse": True, "window": "minimized", "timeout": 12.0}
    t = rumbapy.open("D", timeout=5)
    t.release()
    assert launcher.calls[1][1]["timeout"] == 5.0


def test_open_validates_before_touching_anything(launcher):
    with pytest.raises(UnknownTerminal):
        rumbapy.open("Q")
    with pytest.raises(ParameterError):
        rumbapy.open("D", window="hidden")                    # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        rumbapy.open("D", timeout=-1)
    with pytest.raises(ParameterError):
        rumbapy.open("D", reuse="sim")                        # type: ignore[arg-type]
    assert launcher.calls == [] and launcher.updates == []


def test_attach_and_close(launcher):
    t = rumbapy.attach("D", timeout=3)
    assert launcher.calls[-1] == ("attach", {"profile": "D", "timeout": 3.0})
    assert t.info.via == "attach"
    assert rumbapy.close("D") == 1
    assert t.state == "replaced"                              # close() de módulo revoga as Session da letra


def test_attach_without_terminal(launcher):
    launcher.fake.remove_session("D")
    with pytest.raises(TerminalNotOpen):
        rumbapy.attach("D")


def test_legacy_shim_role_refuses(launcher, monkeypatch):
    monkeypatch.setattr(rumbapy, "_ROLE", "legacy-shim")
    for call in (lambda: rumbapy.open("D"), lambda: rumbapy.attach("D"), lambda: rumbapy.close("D")):
        with pytest.raises(RumbaError) as info:
            call()
        assert info.value.message == "uso interno: papel legacy-shim"
    assert launcher.calls == []


def test_open_attach_close_not_in_all():
    assert "open" not in rumbapy.__all__ and "attach" not in rumbapy.__all__ and "close" not in rumbapy.__all__
    assert rumbapy.open is session_mod.open


# ------------------------------------------------------------------------------------------- janela (E.6)

def test_show_minimize_never_activate():
    user32 = user32_with_window()
    t, _, _ = make_session(table_screen(), user32=user32)
    try:
        t.minimize()
        t.show()
        assert user32.calls_to("show") == [(scenarios.HWND_D, SW_SHOWMINNOACTIVE),
                                           (scenarios.HWND_D, SW_SHOWNOACTIVATE)]
        info = t.window
        assert info.hwnd == scenarios.HWND_D and info.pid == scenarios.PID_D
        assert info.title == "BRAZIL_D - Pro Client" and not info.minimized
    finally:
        t.release()


def test_window_gone_is_terminal_not_open():
    user32 = user32_with_window()
    t, _, _ = make_session(table_screen(), user32=user32)
    try:
        user32.destroy(scenarios.HWND_D)
        with pytest.raises(TerminalNotOpen):
            t.show()
    finally:
        t.release()


# ------------------------------------------------------------------------------------------- com o Launcher real (P-C)

def test_attach_through_real_launcher():
    from rumbapy._launcher import Launcher

    fake = scenarios.cics_login()
    user32 = user32_with_window()
    backends: dict = {}

    def get_backend(letter: str, mode: str) -> InProcessBackend:
        if mode not in backends:
            backends[mode] = InProcessBackend(fake, letter)
        return backends[mode]

    session_mod._set_factories(
        backend_factory=lambda profile, py32: get_backend, user32_factory=lambda: user32,
        launcher_factory=lambda u32, get: Launcher(u32, get, neutralize=lambda letter: None,
                                                   find_worker=lambda letter: None))
    try:
        t = rumbapy.attach("D", timeout=5)
        assert t.info.via == "attach" and t.info.hwnd == scenarios.HWND_D
        assert t.read(14, 26, 23) == "Select CICS Environment"
        t.release()
        assert backends["owner"].released == 1
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)
    assert connect is not None


def test_window_of_an_edge_ie_mode_tab_is_refused():
    """Sessão HLLAPI de uma aba do Edge no modo IE: a janela-raiz é a do navegador inteiro (E.3) — nunca mexer."""
    from tests.fakes.fake_user32 import FakeUser32, FakeWindow

    user32 = FakeUser32()
    user32.add_process(900, "msedge.exe")
    user32.add_process(901, "iexplore.exe", parent_pid=900)
    edge = user32.add_window("Caixa de entrada - Outlook - Microsoft Edge", cls="Chrome_WidgetWin_1", pid=900)
    user32.windows[scenarios.HWND_D] = FakeWindow(scenarios.HWND_D, "", "Internet Explorer_Server", 901,
                                                  parent=edge.hwnd)
    t, _, _ = make_session(table_screen(), user32=user32)
    try:
        for action in (t.show, t.minimize, lambda: t.window):
            with pytest.raises(ParameterError) as info:
                action()
            assert "Edge" in info.value.message and "Outlook" not in str(info.value)
        assert user32.calls_to("show") == []
        assert t.state == "ok"
    finally:
        t.release()
