"""E.5: ``Launcher.open`` (Web Launcher falso, ``FakeUser32``, ``InProcessBackend``, relógio falso)."""
# ruff: noqa: I001
from __future__ import annotations

from tests.unit.client.launcher.world import (LAUNCHER_EXE, REGISTER_AFTER, RecordingBackend, World, silent_plan,
                                             standard_plan)
from tests.fakes.fake_backend import InProcessBackend
from tests.fakes.fake_ehllapi import FakeScreen
from rumbapy.errors import (ClientTableFull, HllapiSystemError, LaunchFailed, LaunchTimeout, MultipleInstances,
                            TerminalInUse)
from rumbapy._user32 import SW_SHOWMINNOACTIVE
from rumbapy import _paths, profiles

import pytest

D = profiles.resolve("D")


def test_open_happy_path(world: World) -> None:
    opened = world.launcher().open("D", timeout=90)
    info = opened.info
    assert world.spawned == [[LAUNCHER_EXE, profiles.launcher_arg(D)]]
    assert world.neutralized == ["D"]
    assert info.via == "launcher" and info.launched is True and info.closed_instances == 0
    assert info.hwnd == world.session_hwnd["D"] and info.hwnd_pid == world.procs[0].pid
    assert (info.rows, info.cols) == (24, 80)
    assert REGISTER_AFTER <= info.seconds_to_register <= REGISTER_AFTER + 0.3
    assert info.seconds_to_connect >= 11.3
    assert opened.backend is world.owner and world.owner.bound == [info.hwnd]
    assert opened.screen is not None and "Select CICS Environment" in opened.screen.text
    # bind_instance logo depois do connect, antes do 1º snapshot (A.9 i)
    assert world.index("backend", "owner", "bind_instance") < world.index("backend", "owner", "snapshot")
    # nada foi digitado
    assert not [c for c in world.fake.calls if c[0] in ("WD_SendKey", "WD_CopyStringToPS", "WD_CopyStringToField")]
    record = _paths.read_json(_paths.launched_registry("D"))
    assert record is not None and [p["pid"] for p in record["pids"]] == [world.procs[0].pid]


def test_open_terminal_returns_backend_and_info(world: World) -> None:
    backend, info = world.launcher().open_terminal("D", timeout=90)
    assert backend is world.owner and info.via == "launcher"


def test_launcher_accepts_get_backend_bound_to_the_letter(world: World) -> None:
    """Fábrica da ``session`` (P-E): ``get_backend(mode)`` já ligado à letra."""
    from rumbapy._launcher import Launcher

    seen: list[str] = []

    def by_mode(mode: str = "owner"):  # type: ignore[no-untyped-def]
        seen.append(mode)
        return world.admin if mode == "admin" else world.owner

    launcher = Launcher(world.user32, by_mode, world.spawn, clock=world.clock, sleep=world.clock.sleep,
                        find_worker=lambda letter: None, find_launcher=lambda: LAUNCHER_EXE,
                        neutralize=lambda letter: None, process_alive=lambda pid, created: True,
                        process_created=lambda pid: 1)
    backend, info = launcher.open_terminal("D", timeout=90)
    assert backend is world.owner and seen == ["admin", "owner"]


def test_open_closes_existing_instances_first(world: World, notices: list[str]) -> None:
    old1 = world.add_launcher_window("BRAZIL_D - Pro Client")
    old2 = world.add_launcher_window("BRAZIL_D - Pro Client [D]", register=None)
    opened = world.launcher().open("D", timeout=90)
    assert opened.info.closed_instances == 2
    assert not world.user32.windows[old1.hwnd].alive and not world.user32.windows[old2.hwnd].alive
    assert world.index("backend", "admin", "evict") < world.index("user32", "post_close", old1.hwnd)
    assert world.index("user32", "post_close", old2.hwnd) < world.index("spawn")
    assert "RumbaPy: fechando 2 janela(s) de BRAZIL_D…" in notices


def test_reuse_with_one_instance_connects_without_launching(world: World) -> None:
    win = world.add_launcher_window()
    opened = world.launcher().open("D", reuse=True, timeout=90)
    assert world.spawned == [] and world.user32_calls("post_close") == []
    assert opened.info.via == "reuse" and opened.info.launched is False and opened.info.hwnd == win.hwnd
    assert world.owner.bound == [win.hwnd]


def test_reuse_with_two_instances_closes_and_opens_with_notice(world: World, notices: list[str]) -> None:
    world.add_launcher_window("BRAZIL_D - Pro Client")
    world.add_launcher_window("BRAZIL_D - Pro Client [D]", register=None)
    opened = world.launcher().open("D", reuse=True, timeout=90)
    assert len(world.spawned) == 1 and opened.info.via == "launcher" and opened.info.closed_instances == 2
    assert any("reuse" in n and "2 janelas" in n for n in notices)


def test_reuse_with_broken_instance_falls_back_to_open(world: World, notices: list[str]) -> None:
    win = world.add_launcher_window()
    world.fake.connect_ready_at["D"] = float("inf")       # rc 1 para sempre: a instância não conecta
    plan = standard_plan()

    def relaunch(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        w.fake.connect_ready_at.pop("D", None)
        plan(w, proc)

    world.launch_plan = relaunch
    opened = world.launcher().open("D", reuse=True, timeout=90)
    assert opened.info.via == "launcher" and not world.user32.windows[win.hwnd].alive
    assert world.owner.released >= 1                       # o lease do reuse fracassado foi devolvido
    assert notices and "reaproveitar" in notices[0]


def test_launcher_exit_code_zero_is_not_a_failure(world: World) -> None:
    plan = standard_plan()

    def single_instance(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        proc.code = 0                                  # o launcher repassou a outro e saiu
        plan(w, proc)

    world.launch_plan = single_instance
    opened = world.launcher().open("D", timeout=90)
    assert opened.info.via == "launcher" and world.procs[0].polls > 0


def test_launcher_nonzero_exit_is_launch_failed(world: World) -> None:
    def crash(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        proc.code = 3
        w.clock.at(1.0, lambda: w.add_launcher_window(pid=proc.pid, register=None))

    world.launch_plan = crash
    with pytest.raises(LaunchFailed) as info:
        world.launcher().open("D", timeout=90)
    assert "código 3" in str(info.value)


def test_short_name_during_registration_is_multiple_instances_and_cleans_only_new(world: World) -> None:
    other_pid = world.new_pid()
    other = world.add_launcher_window("BRAZIL_Z - Pro Client", pid=other_pid, register="Z")
    edge = world.user32.add_window("Portal - Microsoft Edge", cls="Chrome_WidgetWin_1", pid=world.new_pid())
    world.user32.add_process(edge.pid, "msedge.exe")

    def short_name(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        def appear() -> None:
            new = w.add_launcher_window(pid=proc.pid, register=None)
            w.user32.add_dialog("RUMBA API Service Control", pid=proc.pid, texts=["Short Name already in use"],
                                buttons=["OK"], blocks_close=False)
            w.created.append(new.hwnd)
        w.clock.at(3.0, appear)

    world.created = []  # type: ignore[attr-defined]
    world.launch_plan = short_name
    with pytest.raises(MultipleInstances) as info:
        world.launcher().open("D", timeout=90)
    assert "usa a letra D" in str(info.value)
    assert [text for _hwnd, text in world.user32.clicked] == ["OK"]
    new_hwnd = world.created[0]  # type: ignore[attr-defined]
    assert not world.user32.windows[new_hwnd].alive               # a instância nova foi fechada
    assert world.user32.windows[other.hwnd].alive and world.user32.windows[edge.hwnd].alive
    closed = {args[0] for args in world.user32_calls("post_close")}
    assert other.hwnd not in closed and edge.hwnd not in closed


def test_connect_rc1_for_11s_then_connects(world: World) -> None:
    world.launch_plan = standard_plan(register_after=1.0, connect_after=12.0)
    opened = world.launcher().open("D", timeout=90)
    rc1 = [c for c in world.fake.calls if c[0] == "WD_ConnectPS"]
    assert len(rc1) >= 20                                          # ~22 tentativas a cada 500 ms
    assert opened.info.seconds_to_connect >= 12.0


def test_connect_terminal_in_use_rc11(world: World) -> None:
    def busy(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        w.fake.rc_override["WD_ConnectPS"] = [11]
        standard_plan(connect_after=0)(w, proc)

    world.launch_plan = busy
    launcher = world.launcher(legacy_busy_extra=lambda letter: " (servidor v0.1.5 ocupado na porta 8500)")
    with pytest.raises(TerminalInUse) as info:
        launcher.open("D", timeout=90)
    assert "v0.1.5" in str(info.value) and info.value.rc == 11
    assert world.user32.alive_titles() == []                       # limpeza da instância nova
    assert world.owner.released == 1


def test_three_system_errors_in_a_row_raise(world: World) -> None:
    def broken(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        w.fake.rc_override["WD_ConnectPS"] = [9, 9, 9]
        standard_plan(connect_after=0)(w, proc)

    world.launch_plan = broken
    with pytest.raises(HllapiSystemError):
        world.launcher().open("D", timeout=90)
    assert len([c for c in world.fake.calls if c[0] == "WD_ConnectPS"]) == 3


def test_two_system_errors_then_success(world: World) -> None:
    def flaky(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        w.fake.rc_override["WD_ConnectPS"] = [9, 9]
        standard_plan(connect_after=0)(w, proc)

    world.launch_plan = flaky
    assert world.launcher().open("D", timeout=90).info.via == "launcher"


def test_register_timeout(world: World) -> None:
    world.launch_plan = silent_plan()
    with pytest.raises(LaunchTimeout) as info:
        world.launcher().open("D", timeout=90)
    assert info.value.details["phase"] == "register"
    assert "a letra não foi registrada" in str(info.value)
    assert world.clock.now - 1000.0 <= 60                          # min(D - 15, t_lanc + 45)


def test_connect_timeout_with_connecting_dialog_hint(world: World) -> None:
    def never_connects(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        def appear() -> None:
            win = w.add_launcher_window(pid=proc.pid)
            w.user32.add_dialog("Telnet3270 Application", owner=win.hwnd,
                                texts=["Connecting To Address: host.example.com"], blocks_close=False)
        w.fake.connect_ready_at["D"] = float("inf")
        w.clock.at(REGISTER_AFTER, appear)

    world.launch_plan = never_connects
    with pytest.raises(LaunchTimeout) as info:
        world.launcher().open("D", timeout=60)
    assert info.value.details["phase"] == "connect"
    assert "verifique VPN/rede" in str(info.value) and "ainda está tentando conectar" in str(info.value)
    assert "host.example.com" not in str(info.value)
    assert world.user32.alive_titles() == []                       # limpeza: a instância nova foi fechada


def test_first_screen_timeout(world: World) -> None:
    blank = FakeScreen(lines=[""] * 24, name="blank")
    world.launch_plan = standard_plan(screen=blank)
    with pytest.raises(LaunchTimeout) as info:
        world.launcher().open("D", timeout=60)
    assert info.value.details["phase"] == "first_screen"
    assert "primeira tela" in str(info.value)
    assert world.owner.released == 1


def test_blank_first_screen_returns_only_after_the_first_screen() -> None:
    """T-SAFE-3 / cenário ``blank_first_screen(2)`` com o relógio real (a tela chega por ``threading.Timer``)."""
    from tests.fakes.scenarios import blank_first_screen
    from tests.fakes.fake_backend import InProcessBackend
    from rumbapy._launcher import Launcher

    import time

    world = World()
    fake = blank_first_screen(2)
    session = fake.sessions.pop("D")                              # só registra depois do "Popen"
    admin, owner = InProcessBackend(fake, "D"), InProcessBackend(fake, "D")

    def spawn(argv):  # type: ignore[no-untyped-def]
        world.spawned.append(argv)
        world.user32.add_process(9000, "host access web launcher.exe")
        win = world.user32.add_window("BRAZIL_D - Pro Client", pid=9000, hwnd=session.hwnd)
        assert win.hwnd == session.hwnd
        fake.sessions["D"] = session
        from tests.unit.client.launcher.world import FakeProc
        return FakeProc(9000)

    launcher = Launcher(world.user32, lambda letter, mode: admin if mode == "admin" else owner, spawn,
                        find_worker=lambda letter: None, find_launcher=lambda: LAUNCHER_EXE,
                        neutralize=lambda letter: None, process_alive=lambda pid, created: True,
                        process_created=lambda pid: 1)
    start = time.monotonic()
    opened = launcher.open("D", timeout=20)
    assert time.monotonic() - start >= 1.9
    assert opened.screen is not None and "Select CICS Environment" in opened.screen.text
    assert not [c for c in fake.calls if c[0] == "WD_SendKey"]


def test_minimized_is_applied_early(world: World) -> None:
    def window_first(w: World, proc) -> None:  # type: ignore[no-untyped-def]
        w.fake.connect_ready_at["D"] = w.clock() + 11.3

        def appear() -> None:
            w.window = w.add_launcher_window(pid=proc.pid, register=None)

        def register() -> None:
            w.register("D", w.window.hwnd, proc.pid)

        w.clock.at(5.0, appear)
        w.clock.at(REGISTER_AFTER, register)

    world.launch_plan = window_first
    opened = world.launcher().open("D", window="minimized", timeout=90)
    hwnd = opened.info.hwnd
    first_show = world.index("user32", "show", hwnd, SW_SHOWMINNOACTIVE)
    assert first_show < world.index("get_backend", "D", "owner")    # antes do registro terminar
    assert world.user32.windows[hwnd].iconic


def test_preflight_hllapi_error_dialog_is_client_table_full(world: World) -> None:
    pid = world.new_pid()
    world.user32.add_process(pid, "host access web launcher.exe")
    world.user32.add_dialog("HLLAPI ERROR", pid=pid, texts=["Exceeded maximum number of clients"])
    with pytest.raises(ClientTableFull):
        world.launcher().open("D", timeout=90)
    assert world.backend_requests == [] and world.spawned == []
    assert world.user32_calls("click") == []


def test_window_mode_is_validated(world: World) -> None:
    with pytest.raises(ValueError):
        world.launcher().open("D", window="hidden")  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- lease antigo / admin
def _stale_owner(world: World) -> RecordingBackend:
    """``Backend`` de dono de uma ``Session`` anterior deste processo, já despejado pelo ``evict`` do passo 3."""
    from rumbapy.errors import SessionReplaced

    stale = RecordingBackend(InProcessBackend(world.fake, "D"), "stale", world.timeline)
    stale.fail["connect"] = [SessionReplaced.make(L="D", terminal="D") for _ in range(5)]
    return stale


def _owner_sequence(world: World, *owners: RecordingBackend):  # type: ignore[no-untyped-def]
    queue = list(owners)

    def get_backend(letter: str, mode: str) -> RecordingBackend:
        world.backend_requests.append((letter, mode))
        return world.admin if mode == "admin" else queue.pop(0)

    return get_backend


def test_second_open_in_the_same_process_replaces_the_stale_owner_lease(world: World) -> None:
    """2º ``open('D')`` no mesmo processo (célula do Jupyter rodada de novo): o 1º handle de dono veio despejado."""
    stale = _stale_owner(world)
    launcher = world.launcher()
    launcher.get_backend = _owner_sequence(world, stale, world.owner)
    opened = launcher.open("D", timeout=90)
    assert opened.backend is world.owner and opened.info.via == "launcher"
    assert stale.released == 1 and world.owner.released == 0
    assert world.owner.bound == [opened.info.hwnd] and stale.bound == []
    assert world.user32.windows[opened.info.hwnd].alive               # a janela recém-aberta continua aberta


def test_session_replaced_twice_propagates_and_cleans_up(world: World) -> None:
    from rumbapy.errors import SessionReplaced

    first, second = _stale_owner(world), _stale_owner(world)
    launcher = world.launcher()
    launcher.get_backend = _owner_sequence(world, first, second)
    with pytest.raises(SessionReplaced):
        launcher.open("D", timeout=90)
    assert first.released == 1 and second.released == 1
    assert world.user32.alive_titles() == []                          # limpeza da instância nova


def test_reuse_replaces_the_stale_owner_lease(world: World) -> None:
    win = world.add_launcher_window()
    stale = _stale_owner(world)
    launcher = world.launcher()
    launcher.get_backend = _owner_sequence(world, stale, world.owner)
    opened = launcher.open("D", reuse=True, timeout=90)
    assert opened.info.via == "reuse" and opened.backend is world.owner and opened.info.hwnd == win.hwnd
    assert stale.released == 1 and world.spawned == []


def test_open_releases_the_admin_connection(world: World) -> None:
    world.launcher().open("D", timeout=90)
    assert world.admin.released == 1 and world.owner.released == 0


def test_failed_open_releases_the_admin_connection(world: World) -> None:
    world.launch_plan = silent_plan()
    with pytest.raises(LaunchTimeout):
        world.launcher().open("D", timeout=90)
    assert world.admin.released == 1


def test_admin_that_is_also_the_owner_is_not_released(world: World) -> None:
    from rumbapy._launcher import Launcher

    shared = world.owner
    launcher = Launcher(world.user32, lambda letter, mode: shared, world.spawn, clock=world.clock,
                        sleep=world.clock.sleep, find_worker=lambda letter: None, find_launcher=lambda: LAUNCHER_EXE,
                        neutralize=lambda letter: None, process_alive=lambda pid, created: True,
                        process_created=lambda pid: 1)
    opened = launcher.open("D", timeout=90)
    assert opened.backend is shared and shared.released == 0


def test_reuse_slow_first_screen_propagates_and_keeps_the_window(world: World) -> None:
    """Conectou no reuse, mas a 1ª tela não estabiliza: ``LaunchTimeout`` com o prazo inteiro, janela intacta."""
    blank = FakeScreen(lines=[""] * 24, name="blank")
    win = world.add_launcher_window(screen=blank)
    with pytest.raises(LaunchTimeout) as info:
        world.launcher().open("D", reuse=True, timeout=60)
    assert info.value.details["phase"] == "first_screen"
    assert world.clock.now - 1000.0 >= 60                              # prazo D, não 10 s
    assert world.user32.windows[win.hwnd].alive and world.user32_calls("post_close") == []
    assert world.spawned == [] and world.owner.released == 1


def test_launched_entry_without_created_is_ignored(world: World) -> None:
    from rumbapy._launcher import Launcher

    pid = world.new_pid()
    world.alive[pid] = 42
    _paths.write_json(_paths.launched_registry("D"), {"pids": [{"pid": pid, "created": None, "at": 0}]})
    assert world.launcher().launched_pids("D") == []
    launcher = Launcher(world.user32, world.get_backend, world.spawn, process_alive=lambda p, c: True,
                        process_created=lambda p: None)
    launcher.record_launch("D", pid)                                  # sem ``created``: não grava
    record = _paths.read_json(_paths.launched_registry("D"))
    assert record is not None and [i["pid"] for i in record["pids"]] == [pid] and record["pids"][0]["created"] is None
