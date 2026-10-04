"""E.4: ``close_all`` com ``FakeUser32`` + ``InProcessBackend`` e relógio falso."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.unit.client.launcher.world import World
from rumbapy.errors import ClientTableFull, CloseFailed, InstanceNotClosable
from rumbapy._user32 import SW_SHOWMINNOACTIVE

import pytest
import ast
import re

SRC = Path(__file__).resolve().parents[4] / "src" / "rumbapy"


def test_two_instances_are_both_closed(world: World, notices: list[str]) -> None:
    a = world.add_launcher_window("BRAZIL_D - Pro Client")
    b = world.add_launcher_window("BRAZIL_D - Pro Client [D]", register=None)
    excel = world.user32.add_window("BRAZIL_D.xlsx - Excel", cls="XLMAIN", pid=world.new_pid())
    world.user32.add_process(excel.pid, "excel.exe")
    closed = world.launcher().close_all("D")
    assert closed == 2
    assert [args[0] for args in world.user32_calls("post_close")] == [a.hwnd, b.hwnd]
    assert world.user32.windows[excel.hwnd].alive
    assert world.registered() == []
    assert notices == ["RumbaPy: fechando 2 janela(s) de BRAZIL_D…"]


def test_evict_is_sent_before_wm_close_when_a_worker_exists(world: World) -> None:
    win = world.add_launcher_window()
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": win.pid}
    assert world.launcher().close_all("D") == 1
    assert ("D", "admin") in world.backend_requests
    assert world.index("backend", "admin", "evict") < world.index("user32", "post_close", win.hwnd)


def test_admin_passed_by_open_is_used_for_evict(world: World) -> None:
    win = world.add_launcher_window()
    world.launcher().close_all("D", admin=world.admin)
    assert world.backend_requests == []                       # não pediu outro processo auxiliar
    assert world.index("backend", "admin", "evict") < world.index("user32", "post_close", win.hwnd)


def test_without_worker_only_windows_are_checked(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.add_launcher_window()
    with caplog.at_level("WARNING", logger="rumbapy._launcher"):
        assert world.launcher().close_all("D") == 1
    assert world.backend_requests == []
    assert not [e for e in world.timeline if e[0] == "backend"]
    assert "só as janelas" in caplog.text


def test_evict_failure_is_only_a_warning(world: World) -> None:
    from rumbapy.errors import WorkerCrashed

    win = world.add_launcher_window()
    world.worker = {"pid": 77, "hwnd": win.hwnd}
    world.admin.fail["evict"] = [WorkerCrashed.make(L="D")]
    assert world.launcher().close_all("D") == 1


def test_hidden_instance_is_shown_minimized_before_closing(world: World) -> None:
    win = world.add_launcher_window(visible=False)
    world.launcher().close_all("D")
    assert world.index("user32", "show", win.hwnd, SW_SHOWMINNOACTIVE) < world.index("user32", "post_close", win.hwnd)
    visible = world.add_launcher_window(register=None)
    world.timeline.clear()
    world.launcher().close_all("D")
    assert ("user32", "show", visible.hwnd, SW_SHOWMINNOACTIVE) not in world.timeline


def test_unknown_dialog_gets_wm_close_after_3s_never_a_click(world: World) -> None:
    win = world.add_launcher_window()
    dialog = world.user32.add_dialog("Pro Client", owner=win.hwnd, texts=["Deseja encerrar a sessão?"],
                                     buttons=["&Sim", "&Não"])
    start = world.clock()
    times: dict[int, float] = {}
    original = world.user32.post_close

    def spy(hwnd: int) -> None:
        times.setdefault(hwnd, world.clock())
        original(hwnd)

    world.user32.post_close = spy  # type: ignore[method-assign]
    assert world.launcher().close_all("D") == 1
    assert times[dialog.hwnd] - start >= 3.0
    assert world.user32_calls("click") == [] and world.user32.clicked == []
    assert not world.user32.windows[win.hwnd].alive


def test_connecting_dialog_is_not_touched(world: World) -> None:
    win = world.add_launcher_window()
    dialog = world.user32.add_dialog("Telnet3270 Application", owner=win.hwnd,
                                     texts=["Connecting To Address: host.example.com"], blocks_close=False)
    assert world.launcher().close_all("D") == 1
    assert (dialog.hwnd,) not in world.user32_calls("post_close")
    assert world.user32_calls("click") == []


def test_short_name_dialog_gets_ok_click(world: World) -> None:
    win = world.add_launcher_window()
    dialog = world.user32.add_dialog("RUMBA API Service Control", owner=win.hwnd,
                                     texts=["Short Name already in use"], buttons=["OK"])
    assert world.launcher().close_all("D") == 1
    assert world.user32.clicked == [(dialog.hwnd, "OK")]


def test_hllapi_error_dialog_is_never_clicked(world: World) -> None:
    win = world.add_launcher_window(closable=False)
    world.user32.add_dialog("HLLAPI ERROR", owner=win.hwnd, texts=["Exceeded maximum number of clients"])
    with pytest.raises(ClientTableFull):
        world.launcher().close_all("D", timeout=10)
    assert world.user32_calls("click") == []


def test_deadline_raises_close_failed_with_sanitized_titles(world: World) -> None:
    world.add_launcher_window("BRAZIL_D - Pro Client host01.corp.example.com 10.1.2.3", closable=False)
    with pytest.raises(CloseFailed) as info:
        world.launcher().close_all("D", timeout=10)
    msg = str(info.value)
    assert "corp.example" not in msg and "10.1.2.3" not in msg and "<host>" in msg
    assert "BRAZIL_D" in msg and "feche-as pelo X" in msg
    assert world.clock.now >= 1000.0 + 10


def test_letter_still_registered_without_instance_is_not_closable(world: World) -> None:
    world.worker = {"pid": 77, "hwnd": None}
    ghost = world.user32.add_window("Microsoft Edge", cls="Chrome_WidgetWin_1", pid=world.new_pid())
    world.user32.add_process(ghost.pid, "msedge.exe")
    world.register("D", ghost.hwnd, ghost.pid)             # aba do Edge no modo IE segurando a letra
    world.worker["hwnd"] = ghost.hwnd
    world.worker["hwnd_pid"] = ghost.pid
    with pytest.raises(InstanceNotClosable) as info:
        world.launcher().close_all("D")
    assert "msedge.exe" in str(info.value) and str(ghost.pid) in str(info.value)
    assert world.user32_calls("post_close") == []           # o Edge nunca recebe WM_CLOSE


def test_close_does_not_kill_processes() -> None:
    """Nenhum caminho de fechamento mata processo (TerminateProcess/taskkill/os.kill)."""
    forbidden_names = {"kill", "terminate", "TerminateProcess", "NtTerminateProcess", "killpg", "send_signal"}
    pattern = re.compile(r"taskkill|tskill|TerminateProcess", re.IGNORECASE)
    for name in ("_launcher.py", "_instances.py", "_user32.py", "_legacy.py"):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        docstrings = {id(node.value) for node in ast.walk(tree)
                      if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names, (name, node.attr)
            elif isinstance(node, ast.Name):
                assert node.id not in forbidden_names, (name, node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert not pattern.search(node.value), (name, node.value)
    from tests.fakes.fake_user32 import FakeUser32

    assert not [m for m in dir(FakeUser32) if "kill" in m.lower() or "terminate" in m.lower()]


def test_close_returns_zero_when_nothing_is_open(world: World, notices: list[str]) -> None:
    assert world.launcher().close_all("D") == 0
    assert notices == [] and world.user32_calls("post_close") == []


def test_not_closable_without_hwnd_names_the_hllapi_client_process(world: World) -> None:
    """Registro do worker sem hwnd/hwnd_pid (worker admin recém-subido): o PID vem dos clientes HLLAPI."""
    world.worker = {"pid": 77, "hwnd": None}
    edge_pid = world.new_pid()
    world.user32.add_process(edge_pid, "msedge.exe")
    excel_pid = world.new_pid()
    world.user32.add_process(excel_pid, "excel.exe")
    world.fake.add_session("D", hwnd=0x0BAD, pid=edge_pid)          # aba do Edge no modo IE segurando a letra
    world.hllapi_pids = [77, excel_pid, edge_pid]
    with pytest.raises(InstanceNotClosable) as info:
        world.launcher().close_all("D")
    assert "msedge.exe" in str(info.value) and str(edge_pid) in str(info.value)


def test_close_releases_only_the_admin_connection_it_opened(world: World) -> None:
    world.worker = {"pid": 77, "hwnd": None}
    world.launcher().close_all("D")
    assert world.admin.released == 1
    world.launcher().close_all("D", admin=world.admin)
    assert world.admin.released == 1                                  # o admin do chamador continua com ele


# ------------------------------------------------------------------------------------------- close nunca sobe worker
def test_close_uses_only_an_already_running_worker(world: World) -> None:
    """E.4 passo 1: com ``find_admin`` (produção) o close nunca pede ``get_backend(L, "admin")`` (que subiria worker)."""
    win = world.add_launcher_window()
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": win.pid}
    asked: list[str] = []

    def gone(letter: str):  # type: ignore[no-untyped-def]
        asked.append(letter)
        return None                                           # o worker estava parando: já saiu

    assert world.launcher(find_admin=gone).close_all("D") == 1
    assert asked == ["D"] and world.backend_requests == []
    assert not [e for e in world.timeline if e[0] == "backend"]

    win = world.add_launcher_window()
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": win.pid}
    assert world.launcher(find_admin=lambda letter: world.admin).close_all("D") == 1
    assert world.backend_requests == []
    assert world.index("backend", "admin", "evict") < world.index("user32", "post_close", win.hwnd)
    assert world.admin.released == 1


def test_default_get_backend_exposes_find_admin_that_never_spawns(monkeypatch: pytest.MonkeyPatch) -> None:
    from rumbapy._backend import ExistingAdmin
    from rumbapy.errors import WorkerCrashed
    from rumbapy import _launcher, _bridge

    class Handle:
        pid, alive = 77, True

        def __init__(self) -> None:
            self.requests: list[str] = []

        def request(self, op, args=None, **kw):  # type: ignore[no-untyped-def]
            self.requests.append(op)
            return {"sessions": []} if op == "sessions" else {"evicted_owner": None, "was_connected": False}

    class Bridge:
        def __init__(self, handle) -> None:  # type: ignore[no-untyped-def]
            self.handle, self.released, self.found = handle, [], []

        def find_handle(self, letter, mode="admin", *, python32=None):  # type: ignore[no-untyped-def]
            self.found.append((letter, mode))
            return self.handle

        def get_handle(self, *a, **kw):  # type: ignore[no-untyped-def]
            raise AssertionError("o close subiu um worker")

        get_worker = get_handle

        def release(self, handle) -> None:  # type: ignore[no-untyped-def]
            self.released.append(handle)

    handle = Handle()
    bridge = Bridge(handle)
    monkeypatch.setattr(_bridge, "default_bridge", lambda: bridge)
    get = _launcher._default_get_backend()
    admin = get.find_admin("D")                               # type: ignore[attr-defined]
    assert isinstance(admin, ExistingAdmin) and bridge.found == [("D", "admin")]
    assert admin.evict("close")["evicted_owner"] is None and admin.sessions() == []
    handle.alive = False                                      # saiu entre o evict e a verificação
    with pytest.raises(WorkerCrashed):
        admin.sessions()
    admin.release()
    admin.release()
    assert bridge.released == [handle] and handle.requests == ["evict", "sessions"]
    bridge.handle = None
    assert get.find_admin("D") is None                        # type: ignore[attr-defined]


def test_world_evict_revokes_the_owner_like_the_real_worker(world: World) -> None:
    """Contrato do dublê (= WorkerServer, C.6.2): admin e dono dividem o Engine; o evict desconecta e despeja o dono."""
    from rumbapy.errors import SessionReplaced, TerminalInUse
    from tests.fakes.fake_backend import InProcessBackend

    world.add_launcher_window()
    assert world.owner.connect().connected
    other = InProcessBackend(world.fake, "D", share=world.admin.inner)
    with pytest.raises(TerminalInUse):                        # um dono por vez
        other.connect()
    result = world.admin.evict("close")
    assert result["was_connected"] is True and result["evicted_owner"] is not None
    assert world.fake.connected is None                       # a conexão HLLAPI do "processo" caiu
    with pytest.raises(SessionReplaced):
        world.owner.snapshot()
    world.owner.release()                                     # devolve o handle despejado: o próximo é novo
    assert world.owner.connect().connected
    assert world.admin.evict("close")["evicted_owner"] is not None


def test_sessions_is_queried_only_after_the_launcher_process_exits(world: World) -> None:
    # ao vivo (2026-10-04): WD_QuerySessions durante o encerramento do emulador travou a DLL (watchdog de 20 s)
    world.exit_delay = 0.3
    win = world.add_launcher_window()
    world.alive[win.pid] = 1000 + win.pid
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": win.pid}
    assert world.launcher().close_all("D") == 1
    exit_at = world.index("exit", win.pid)
    sessions = [i for i, e in enumerate(world.timeline) if e[:3] == ("backend", "admin", "sessions")]
    assert sessions and all(i > exit_at for i in sessions)


def test_process_that_hosts_another_profile_is_not_awaited(world: World) -> None:
    world.exit_delay = 0.3
    pid = world.new_pid()
    world.alive[pid] = 1000 + pid
    win = world.add_launcher_window(pid=pid)
    world.add_launcher_window("BRAZIL_Z - Pro Client", pid=pid, register="Z")
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": pid}
    start = world.clock()
    assert world.launcher().close_all("D") == 1
    assert pid in world.alive                                     # o processo segue com o BRAZIL_Z
    assert world.clock() - start < 3.0                            # não esperou os 5 s da saída do processo


def test_stuck_process_waits_at_most_the_exit_cap(world: World) -> None:
    world.exit_delay = None                                       # o processo nunca sai (ex.: diálogo do .NET)
    win = world.add_launcher_window()
    world.alive[win.pid] = 1000 + win.pid
    world.worker = {"pid": 77, "hwnd": win.hwnd, "hwnd_pid": win.pid}
    start = world.clock()
    assert world.launcher().close_all("D") == 1
    assert 5.0 <= world.clock() - start < 7.0                     # PROCESS_EXIT_S e depois a conferência

