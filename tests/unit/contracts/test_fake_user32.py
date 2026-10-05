"""Aceite P-0: ``FakeUser32`` (E.2, J.3) — janelas, processos, diálogos, fechamento com atraso e registro de chamadas."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE, RawWindow, User32
from tests.fakes.fake_user32 import FakeUser32

import inspect

import pytest

LAUNCHER = "host access web launcher.exe"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def world(clock: Clock) -> FakeUser32:
    u = FakeUser32(clock=clock, close_delay=0.5)
    u.add_process(500, "Host Access Web Launcher.exe", parent_pid=4)
    u.add_process(4, "explorer.exe")
    u.add_process(700, "iexplore.exe", parent_pid=701)
    u.add_process(701, "msedge.exe")
    return u


def test_implements_the_user32_protocol() -> None:
    u = FakeUser32()
    assert isinstance(u, User32)
    protocol_methods = {n for n, v in vars(User32).items() if inspect.isfunction(v) and not n.startswith("_")}
    for name in protocol_methods:
        assert inspect.signature(getattr(FakeUser32, name)).parameters.keys() == \
            inspect.signature(getattr(User32, name)).parameters.keys()


def test_has_no_way_to_kill_processes() -> None:
    names = [n.lower() for n in dir(FakeUser32)]
    assert not any(word in n for n in names for word in ("kill", "terminate", "taskkill"))


def test_windows_and_processes(world: FakeUser32) -> None:
    main = world.add_window("BRAZIL_D - Pro Client", pid=500)
    hidden = world.add_window("BRAZIL_Z - Pro Client", pid=500, visible=False)
    ie = world.add_window("x - BRAZIL_D - Pro Client - Internet Explorer", cls="IEFrame", pid=700)
    child = world.add_child(ie.hwnd, "Internet Explorer_Server", "")
    tops = world.top_windows()
    assert [w.hwnd for w in tops] == [main.hwnd, hidden.hwnd, ie.hwnd]
    assert tops[0] == RawWindow(main.hwnd, "BRAZIL_D - Pro Client", main.cls, 500, 0, True, False)
    assert tops[1].visible is False
    assert world.process_image(500) == LAUNCHER and world.process_image(999) is None
    assert world.parent_image(700) == "msedge.exe" and world.parent_image(4) is None
    assert world.root_of(child.hwnd) == ie.hwnd and world.root_of(main.hwnd) == main.hwnd
    assert world.root_of(123) == 0
    assert world.is_window(child.hwnd) and not world.is_window(123)


def test_post_close_after_delay_takes_dialogs_along(world: FakeUser32, clock: Clock) -> None:
    main = world.add_window("BRAZIL_D - Pro Client", pid=500)
    connecting = world.add_dialog("Telnet3270 Application", owner=main.hwnd,
                                  texts=["Connecting To Address: <host>"], buttons=["Cancel"], blocks_close=False)
    world.post_close(main.hwnd)
    assert world.is_window(main.hwnd)                                 # ainda dentro do atraso
    clock.now += 0.6
    assert not world.is_window(main.hwnd) and not world.is_window(connecting.hwnd)
    assert world.closed[:2] == [main.hwnd, connecting.hwnd]
    assert world.calls_to("post_close") == [(main.hwnd,)]


def test_blocking_dialog_needs_ok_before_close(world: FakeUser32, clock: Clock) -> None:
    main = world.add_window("BRAZIL_D - Pro Client", pid=500)
    dialog = world.add_dialog("RUMBA API Service Control", owner=main.hwnd, texts=["Short Name already in use"],
                              buttons=["OK"])
    assert world.static_texts(dialog.hwnd) == ["Short Name already in use"]
    [(ok_hwnd, ok_text)] = world.buttons(dialog.hwnd)
    assert ok_text == "OK"
    world.post_close(main.hwnd)
    clock.now += 10
    assert world.is_window(main.hwnd)                                 # o diálogo modal segura a janela
    world.click(ok_hwnd)
    assert world.clicked == [(dialog.hwnd, "OK")] and not world.is_window(dialog.hwnd)
    world.post_close(main.hwnd)                                       # é preciso postar de novo
    clock.now += 1
    assert not world.is_window(main.hwnd)


def test_unknown_dialog_closed_by_post_close(world: FakeUser32, clock: Clock) -> None:
    main = world.add_window("BRAZIL_D - Pro Client", pid=500)
    unknown = world.add_dialog("Pergunta", owner=main.hwnd, texts=["Deseja salvar?"], buttons=["Sim", "Não"])
    world.post_close(unknown.hwnd)
    clock.now += 1
    assert not world.is_window(unknown.hwnd)
    assert world.clicked == []                                       # nenhum botão afirmativo


def test_stubborn_window_and_per_window_delay(world: FakeUser32, clock: Clock) -> None:
    stubborn = world.add_window("BRAZIL_D - Pro Client", pid=500, closable=False)
    slow = world.add_window("BRAZIL_Z - Pro Client", pid=500, close_delay=5.0)
    world.post_close(stubborn.hwnd)
    world.post_close(slow.hwnd)
    clock.now += 1
    assert world.is_window(stubborn.hwnd) and world.is_window(slow.hwnd)
    clock.now += 5
    assert world.is_window(stubborn.hwnd) and not world.is_window(slow.hwnd)


def test_show_changes_state_without_activating(world: FakeUser32) -> None:
    hidden = world.add_window("BRAZIL_D - Pro Client", pid=500, visible=False)
    world.show(hidden.hwnd, SW_SHOWMINNOACTIVE)
    assert hidden.visible and hidden.iconic
    world.show(hidden.hwnd, SW_SHOWNOACTIVATE)
    assert hidden.visible and not hidden.iconic
    world.show(hidden.hwnd, 0)
    assert not hidden.visible
    assert world.calls_to("show") == [(hidden.hwnd, 7), (hidden.hwnd, 4), (hidden.hwnd, 0)]


def test_schedule_and_call_log(world: FakeUser32, clock: Clock) -> None:
    world.schedule(9.4, lambda u: u.add_window("BRAZIL_D - Pro Client", pid=500))
    assert world.top_windows() == []
    clock.now += 9.5
    assert [w.title for w in world.top_windows()] == ["BRAZIL_D - Pro Client"]
    assert [name for name, _ in world.calls] == ["top_windows", "top_windows"]
    assert world.alive_titles() == ["BRAZIL_D - Pro Client"]
