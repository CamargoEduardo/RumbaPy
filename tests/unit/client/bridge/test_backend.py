"""``WorkerBackend`` (C.5, C.8): reciclagem com repetição 1x só de op idempotente, hwnd, rc 1/11, recusas sem laço."""
# ruff: noqa: I001
from __future__ import annotations

from tests.unit.client.bridge.fakes import CONNECTED, NOT_YET, FakeBridge, FakeClock, FakeHandle, kills
from rumbapy.errors import (ChannelHijacked, HllapiError, NotConnected, ProtocolError, SessionReplaced,
                            TerminalInUse, WorkerCrashed, WorkerHung)
from rumbapy._backend import Backend, WorkerBackend
from rumbapy._types import ConnectResult

import logging

import pytest

READ = {"text": "abc", "busy": False, "nul_at": -1, "nul_tail": 0}


def backend(*handles: FakeHandle) -> tuple[WorkerBackend, FakeBridge, FakeClock]:
    bridge = FakeBridge(list(handles))
    clock = FakeClock()
    be = WorkerBackend("D", bridge=bridge, clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    return be, bridge, clock


def connected(*later: FakeHandle, first: FakeHandle | None = None) -> tuple[WorkerBackend, FakeBridge, FakeClock]:
    first = first or FakeHandle(100, {"connect": [CONNECTED], "read": [READ]})
    be, bridge, clock = backend(first, *later)
    assert be.connect().connected
    return be, bridge, clock


def test_is_a_backend() -> None:
    assert isinstance(WorkerBackend("D"), Backend)


def test_connect_binds_instance_and_sends_hwnd() -> None:
    be, bridge, _ = connected()
    assert be.hwnd == 0x1000
    be.read(1, 1, 3)
    handle = bridge.released and bridge.released[0] or be.handle
    assert handle.calls[-1] == ("read", {"row": 1, "col": 1, "length": 3}, 0x1000)
    assert ("connect", {"letter": "D"}, None) in handle.calls       # connect não leva hwnd


def test_connect_rc1_does_not_bind() -> None:
    be, _, _ = backend(FakeHandle(100, {"connect": [NOT_YET]}))
    result = be.connect()
    assert result == ConnectResult(False, 1) and be.hwnd is None


def test_explicit_bind_is_kept_on_connect() -> None:
    be, _, _ = backend(FakeHandle(100, {"connect": [CONNECTED]}))
    be.bind_instance(None)
    be.connect()
    assert be.hwnd is None                                           # a Session decide (bind explícito prevalece)


@pytest.mark.parametrize("error", [WorkerCrashed.make(L="D"), WorkerHung.make(funcao="x", s=20),
                                   ProtocolError.make(L="D", detalhe="id")])
def test_idempotent_read_is_retried_once_after_recycle(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="rumbapy._backend")
    second = FakeHandle(200, {"connect": [NOT_YET, NOT_YET, CONNECTED], "read": [READ]})
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [kills(error)]})
    be, bridge, clock = connected(second, first=first)
    assert be.read(1, 1, 3).text == "abc"
    assert [c[0] for c in second.calls] == ["connect", "connect", "connect", "read"]
    assert [c[0] for c in first.calls].count("read") == 1
    assert first in bridge.released and first.waited == 1           # esperou o worker antigo sair
    assert f"retry op=read after={type(error).__name__}" in caplog.text
    assert clock.sleeps == [0.5, 0.5]


def test_second_failure_is_raised() -> None:
    second = FakeHandle(200, {"connect": [CONNECTED], "read": [kills(WorkerCrashed.make(L="D"))]})
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [kills(WorkerCrashed.make(L="D"))]})
    be, _, _ = connected(second, first=first)
    with pytest.raises(WorkerCrashed):
        be.read(1, 1, 3)                                             # só 1 repetição


def test_recycle_with_other_hwnd_is_session_replaced() -> None:
    second = FakeHandle(200, {"connect": [dict(CONNECTED, hwnd=0x5000)], "read": [READ]})
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [kills(WorkerHung.make(funcao="x", s=20))]})
    be, _, _ = connected(second, first=first)
    with pytest.raises(SessionReplaced) as info:
        be.read(1, 1, 3)
    assert info.value.details == {"old_hwnd": 0x1000, "new_hwnd": 0x5000}
    assert [c[0] for c in second.calls] == ["connect"]               # nada repetido


def test_rc11_for_15s_is_terminal_in_use_stuck() -> None:
    rc11 = TerminalInUse.make("rc", L="D", extra="", rc=11)
    second = FakeHandle(200, {"connect": [rc11]})
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [kills(WorkerCrashed.make(L="D"))]})
    be, _, clock = connected(second, first=first)
    with pytest.raises(TerminalInUse) as info:
        be.read(1, 1, 3)
    assert info.value.rc == 11 and "pode ter ficado presa" in info.value.message
    assert 15.0 <= sum(clock.sleeps) <= 15.5


def test_rc1_for_15s_is_not_connected() -> None:
    second = FakeHandle(200, {"connect": [NOT_YET]})
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [kills(WorkerCrashed.make(L="D"))]})
    be, _, _ = connected(second, first=first)
    with pytest.raises(NotConnected):
        be.read(1, 1, 3)


@pytest.mark.parametrize("op, call", [
    ("send", lambda be: be.send(["a"], secret=False)),
    ("write", lambda be: be.write(1, 1, "a", secret=False)),
    ("set_cursor", lambda be: be.set_cursor(1, 1)),
])
def test_input_ops_are_never_repeated(op: str, call: object) -> None:
    error = kills(WorkerCrashed.make("input", L="D", delivery="unknown"))
    first = FakeHandle(100, {"connect": [CONNECTED], op: [error]})
    second = FakeHandle(200, {"connect": [CONNECTED], "read": [READ]})
    be, bridge, _ = connected(second, first=first)
    with pytest.raises(WorkerCrashed) as info:
        call(be)  # type: ignore[operator]
    assert info.value.delivery == "unknown"
    assert [c[0] for c in first.calls].count(op) == 1 and bridge.got == [("D", "owner")]
    # a próxima op idempotente pega worker novo, reconecta (mesmo hwnd) e segue
    assert be.read(1, 1, 3).text == "abc"
    assert [c[0] for c in second.calls] == ["connect", "read"]


def test_refused_read_is_raised_without_loop() -> None:
    refused = HllapiError("Não consegui conferir a máscara; a leitura foi recusada para não expor conteúdo oculto.",
                          rc=9, function="WD_FindFieldPosition")
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [refused], "snapshot": [refused]})
    be, bridge, _ = connected(first=first)
    with pytest.raises(HllapiError):
        be.read(1, 1, 3)
    with pytest.raises(HllapiError):
        be.snapshot()
    assert [c[0] for c in first.calls] == ["connect", "read", "snapshot"] and bridge.got == [("D", "owner")]


def test_channel_hijacked_is_final() -> None:
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [ChannelHijacked.make(pid=1, L="D")]})
    be, bridge, _ = connected(first=first)
    with pytest.raises(ChannelHijacked):
        be.read(1, 1, 3)
    assert bridge.got == [("D", "owner")]


def test_ping_and_sessions_retry_without_connect() -> None:
    first = FakeHandle(100, {"sessions": [kills(WorkerCrashed.make(L="D"))]})
    entry = {"letter": "D", "long_name": "BRAZIL_D", "type": "H", "ps_size": 1920}
    second = FakeHandle(200, {"sessions": [{"sessions": [entry]}]})
    be, _, _ = backend(first, second)
    entries = be.sessions()
    assert [e.letter for e in entries] == ["D"] and [c[0] for c in second.calls] == ["sessions"]


def test_evict_uses_own_admin_connection() -> None:
    owner = FakeHandle(100, {"connect": [CONNECTED]})
    admin = FakeHandle(300, {"evict": [{"evicted_owner": {"pid": 1, "script": "x"}, "was_connected": True}]})
    be, bridge, _ = connected(admin, first=owner)
    assert be.evict("close")["was_connected"] is True
    assert bridge.got == [("D", "owner")] and bridge.found == [("D", "admin")] and admin in bridge.released
    assert not any(c[0] == "evict" for c in owner.calls)


def test_shutdown_tolerates_exit_and_releases() -> None:
    h = FakeHandle(100, {"shutdown": [WorkerCrashed.make(L="D")]})
    be, bridge, _ = backend(h)
    be.shutdown("teste")
    assert be.handle is None and h in bridge.released


def test_release_and_next_op_takes_new_handle() -> None:
    first = FakeHandle(100, {"connect": [CONNECTED]})
    second = FakeHandle(100, {"read": [READ]})
    be, bridge, _ = connected(second, first=first)
    be.release()
    assert be.handle is None and bridge.released == [first]
    assert be.read(1, 1, 3).text == "abc"                            # mesmo worker (PID 100): sem reconectar
    assert [c[0] for c in second.calls] == ["read"]


def test_evicted_handle_keeps_raising() -> None:
    first = FakeHandle(100, {"connect": [CONNECTED], "read": [SessionReplaced.make(L="D")]})
    be, bridge, _ = connected(first=first)
    first.evicted = True
    with pytest.raises(SessionReplaced):
        be.read(1, 1, 3)
    assert bridge.got == [("D", "owner")]


def test_ping_recycle_then_read_reconnects_on_new_worker() -> None:
    """Reciclagem por op sem conexão (ping) troca o worker; a op seguinte que exige conexão reconecta (C.8)."""
    first = FakeHandle(100, {"connect": [CONNECTED], "ping": [kills(WorkerCrashed.make(L="D"))]})
    second = FakeHandle(200, {"ping": [{"uptime_s": 1}], "connect": [CONNECTED], "read": [READ]})
    be, bridge, _ = connected(second, first=first)
    assert be.ping() == {"uptime_s": 1} and be.worker_pid == 200
    assert be.read(1, 1, 3).text == "abc"
    assert [c[0] for c in second.calls] == ["ping", "connect", "read"]


def test_ping_recycle_then_read_with_other_hwnd_is_replaced() -> None:
    first = FakeHandle(100, {"connect": [CONNECTED], "ping": [kills(WorkerCrashed.make(L="D"))]})
    second = FakeHandle(200, {"ping": [{"uptime_s": 1}], "connect": [dict(CONNECTED, hwnd=0x2000)]})
    be, _, _ = connected(second, first=first)
    be.ping()
    with pytest.raises(SessionReplaced):
        be.read(1, 1, 3)
    assert [c[0] for c in second.calls] == ["ping", "connect"]


def test_evict_without_live_worker_does_not_spawn() -> None:
    be, bridge, _ = backend()
    assert be.evict("close") == {"evicted_owner": None, "was_connected": False}
    assert bridge.got == [] and bridge.found == [("D", "admin")]


def test_admin_evict_without_live_worker_does_not_spawn() -> None:
    bridge = FakeBridge([])
    be = WorkerBackend("D", mode="admin", bridge=bridge)  # type: ignore[arg-type]
    assert be.evict("close") == {"evicted_owner": None, "was_connected": False}
    assert bridge.got == [] and bridge.found == [("D", "admin")]


def test_shutdown_without_live_worker_is_noop() -> None:
    be, bridge, _ = backend()
    be.shutdown("teste")
    assert bridge.got == [] and bridge.found == [("D", "owner")] and be.handle is None


def test_shutdown_uses_live_worker_found_without_spawn() -> None:
    h = FakeHandle(100, {"shutdown": [{}]})
    be, bridge, _ = backend(h)
    be.shutdown("teste")
    assert bridge.got == [] and [c[0] for c in h.calls] == ["shutdown"] and h in bridge.released
