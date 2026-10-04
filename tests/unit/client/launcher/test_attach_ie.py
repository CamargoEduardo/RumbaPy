"""E.6 (``attach``, janela) e E.7 (fallback Internet Explorer) com dublês; recusa do sandbox para ``Popen``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.unit.client.launcher.world import World
from rumbapy.errors import (LaunchFailed, MultipleInstances, RumbaNotInstalled, SandboxViolation, TerminalInUse,
                            TerminalNotOpen)
from rumbapy._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE
from rumbapy import _launcher, profiles

import subprocess
import pytest

D = profiles.resolve("D")
PKG = Path(_launcher.__file__).resolve().parent


# ------------------------------------------------------------------------------------------- attach
def test_attach_with_one_instance(world: World) -> None:
    win = world.add_launcher_window()
    opened = world.launcher().attach("D", timeout=10)
    assert opened.info.via == "attach" and opened.info.launched is False and opened.info.hwnd == win.hwnd
    assert world.neutralized == ["D"] and world.owner.bound == [win.hwnd]
    assert world.user32_calls("post_close") == [] and world.user32_calls("show") == [] and world.spawned == []


def test_attach_terminal_returns_tuple(world: World) -> None:
    world.add_launcher_window()
    backend, info = world.launcher().attach_terminal("D", timeout=10)
    assert backend is world.owner and info.via == "attach"


def test_attach_with_zero_instances_is_terminal_not_open(world: World) -> None:
    with pytest.raises(TerminalNotOpen) as info:
        world.launcher().attach("D")
    assert "rumbapy.open('D')" in str(info.value)
    assert ("D", "owner") not in world.backend_requests


def test_attach_registered_letter_without_window_still_connects(world: World) -> None:
    world.register("D", 0x5000, 1234)                       # janela que não conseguimos classificar
    opened = world.launcher().attach("D")
    assert opened.info.via == "attach"


def test_attach_with_two_instances_is_multiple_instances(world: World) -> None:
    a = world.add_launcher_window()
    b = world.add_launcher_window("BRAZIL_D - Pro Client [D]", register=None)
    with pytest.raises(MultipleInstances) as info:
        world.launcher().attach("D")
    msg = str(info.value)
    assert "Há 2 janelas BRAZIL_D abertas" in msg and str(a.pid) in msg and str(b.pid) in msg
    assert "rumbapy.open('D') fecha todas" in msg
    assert world.user32_calls("post_close") == []


def test_attach_with_live_owner_is_terminal_in_use(world: World) -> None:
    world.add_launcher_window()
    world.owner_error = TerminalInUse.make("lease", L="D", pid=4321, script="lote.py", hora="10:00")
    with pytest.raises(TerminalInUse):
        world.launcher().attach("D")
    assert world.user32_calls("post_close") == []


def test_attach_connect_timeout_releases_owner(world: World) -> None:
    world.add_launcher_window()
    world.fake.connect_ready_at["D"] = float("inf")
    from rumbapy.errors import LaunchTimeout

    with pytest.raises(LaunchTimeout):
        world.launcher().attach("D", timeout=5)
    assert world.owner.released == 1 and world.user32_calls("post_close") == []


def test_window_helpers_never_activate(world: World) -> None:
    win = world.add_launcher_window("BRAZIL_D - Pro Client host.corp.example.com")
    launcher = world.launcher()
    launcher.minimize(win.hwnd)
    launcher.show(win.hwnd)
    assert world.user32_calls("show") == [(win.hwnd, SW_SHOWMINNOACTIVE), (win.hwnd, SW_SHOWNOACTIVATE)]
    info = launcher.window_info("D", win.hwnd)
    assert info is not None and info.kind == "launcher" and info.process == "host access web launcher.exe"
    assert "corp" not in info.title and info.pid == win.pid
    assert launcher.window_info("D", 0) is None


# ------------------------------------------------------------------------------------------- fallback IE
def _ie_world(world: World, output: bytes, code: int | None = 0) -> None:
    world.launcher_path = None
    world.script_output = (code, output)

    def appear(w: World) -> None:
        pid = w.new_pid()
        w.user32.add_process(pid, "iexplore.exe", parent_pid=None)
        win = w.user32.add_window("BRAZIL_D - Pro Client - Internet Explorer", cls="IEFrame", pid=pid)
        w.clock.at(5.0, lambda: w.register("D", win.hwnd, pid))
        w.fake.connect_ready_at["D"] = w.clock() + 6.0

    world.on_script = appear


def test_ie_fallback_without_launcher(world: World) -> None:
    _ie_world(world, b'{"ok":true,"step":"clicked","ie_hwnd":1234,"title":"Sess\\u00e3o"}\r\n')
    opened = world.launcher().open("D", timeout=90)
    assert opened.info.via == "ie" and world.spawned == []
    argv, timeout = world.scripts[0]
    assert argv[:4] == [_launcher.CSCRIPT, "//NoLogo", "//E:JScript", str(PKG / "_ie_open.js")]
    assert argv[4:6] == [profiles.index_url(), D.link_text]
    assert 10 <= int(argv[6]) <= 60 and timeout == int(argv[6]) + 10


def test_ie_output_with_escaped_accent_is_parsed() -> None:
    out = _launcher.parse_ie_output(b'lixo\r\n{"ok":false,"step":"navigate","error":"Acesso negado \\u00e0 p\\u00e1gina"}')
    assert out == {"ok": False, "step": "navigate", "error": "Acesso negado à página"}
    assert _launcher.parse_ie_output(b"") == {} and _launcher.parse_ie_output("{quebrado") == {}


def test_ie_ok_false_is_launch_failed_sanitized(world: World) -> None:
    _ie_world(world, b'{"ok":false,"step":"load","error":"https://portal.example.com/x n\\u00e3o carregou"}', code=3)
    with pytest.raises(LaunchFailed) as info:
        world.launcher().open("D", timeout=90)
    msg = str(info.value)
    assert "portal.example.com" not in msg and "<url>" in msg and "load" in msg and "não carregou" in msg


def test_ie_forced_by_env(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUMBAPY_LAUNCH", "ie")
    _ie_world(world, b'{"ok":true,"step":"clicked"}')
    world.launcher_path = "C:/existe/launcher.exe"
    assert world.launcher().open("D", timeout=90).info.via == "ie"
    assert world.spawned == []


def test_launch_mode_launcher_without_launcher_is_not_installed(world: World,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUMBAPY_LAUNCH", "launcher")
    world.launcher_path = None
    with pytest.raises(RumbaNotInstalled):
        world.launcher().open("D", timeout=90)
    assert world.scripts == []


def test_ie_script_is_public_and_has_timeout_and_escape() -> None:
    text = (PKG / "_ie_open.js").read_text(encoding="utf-8")
    assert "D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E" in text
    assert "InternetExplorer.Application" not in text
    assert "timeout_s" in text and "\\\\u" in text
    assert "http" not in text.lower() and "volvo" not in text.lower()
    assert all(ord(ch) < 128 for ch in text)


# ------------------------------------------------------------------------------------------- sandbox
def _no_popen(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        raise AssertionError("Popen chamado no sandbox")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    return calls


def test_sandbox_refuses_launcher_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_popen(monkeypatch)
    with pytest.raises(SandboxViolation):
        _launcher.default_spawn([_launcher.DEFAULT_LAUNCHER, "hawlauncher://x"])
    assert calls == []


def test_sandbox_refuses_cscript_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_popen(monkeypatch)
    with pytest.raises(SandboxViolation):
        _launcher.default_run_script([_launcher.CSCRIPT, "//NoLogo"], 10)
    assert calls == []


def test_sandbox_refuses_default_launcher_open(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem ``spawn`` injetado, ``open`` chega ao ``Popen`` real e é recusado antes dele."""
    calls = _no_popen(monkeypatch)
    from rumbapy._launcher import Launcher

    launcher = Launcher(world.user32, world.get_backend, clock=world.clock, sleep=world.clock.sleep,
                        find_worker=lambda letter: None, find_launcher=lambda: _launcher.DEFAULT_LAUNCHER,
                        neutralize=lambda letter: None)
    with pytest.raises(SandboxViolation):
        launcher.open("D", timeout=90)
    world.launcher_path = None
    launcher = Launcher(world.user32, world.get_backend, clock=world.clock, sleep=world.clock.sleep,
                        find_worker=lambda letter: None, find_launcher=lambda: None, neutralize=lambda letter: None)
    with pytest.raises(SandboxViolation):
        launcher.open("D", timeout=90)
    assert calls == []


def test_first_token_of_registry_command() -> None:
    assert _launcher._first_token('"C:\\P F\\L.exe" "%1"') == "C:\\P F\\L.exe"
    assert _launcher._first_token("C:\\L.exe %1") == "C:\\L.exe"


# ------------------------------------------------------------------------------------------- correções da verificação
def test_attach_releases_admin_and_keeps_owner(world: World) -> None:
    world.add_launcher_window()
    opened = world.launcher().attach("D", timeout=10)
    assert opened.backend is world.owner and world.admin.released == 1 and world.owner.released == 0


def test_attach_replaces_a_stale_owner_lease(world: World) -> None:
    from tests.unit.client.launcher.world import RecordingBackend
    from tests.fakes.fake_backend import InProcessBackend
    from rumbapy.errors import SessionReplaced

    win = world.add_launcher_window()
    stale = RecordingBackend(InProcessBackend(world.fake, "D"), "stale", world.timeline)
    stale.fail["connect"] = [SessionReplaced.make(L="D", terminal="D")]
    queue = [stale, world.owner]
    launcher = world.launcher()
    launcher.get_backend = lambda letter, mode: world.admin if mode == "admin" else queue.pop(0)
    opened = launcher.attach("D", timeout=10)
    assert opened.backend is world.owner and opened.info.hwnd == win.hwnd and stale.released == 1


def test_ie_script_timeout_cleans_up_the_late_ie_window(world: World) -> None:
    """O ``cscript`` estourou o prazo (código ``None``) mas abriu o IE: a janela nova é fechada (E.5 passo 10)."""
    from rumbapy.errors import LaunchTimeout

    _ie_world(world, b"", code=None)
    old = world.add_launcher_window("BRAZIL_Z - Pro Client", register="Z")
    with pytest.raises(LaunchTimeout) as info:
        world.launcher().open("D", timeout=90)
    assert info.value.details.get("via") == "ie"
    ie_windows = [w for w in world.user32.windows.values() if w.cls == "IEFrame"]
    assert ie_windows and not any(w.alive for w in ie_windows)
    assert world.user32.windows[old.hwnd].alive
