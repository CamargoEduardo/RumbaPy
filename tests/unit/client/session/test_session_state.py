"""Session: máquina de estados (B.3) e a tabela "quem libera o quê" (I8)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import (HostConnectionLost, KeyboardLocked, NotConnected, SessionReplaced, SessionUncertain,
                            TerminalNotOpen, WaitTimeout)
from tests.unit.client.session.support import (FakeLauncher, aids, echo_host, make_session, send_calls,
                                               table_screen, user32_with_window)
from rumbapy.screen import OIA
from tests.fakes import scenarios
from rumbapy import session as session_mod

import dataclasses
import gc

import pytest


@pytest.fixture
def table():
    t, backend, fake = make_session(table_screen())
    echo_host(fake)
    yield t, backend, fake
    t.release()


def _input_ops(backend) -> list[str]:
    return [op for op in backend.calls if op in ("write", "send", "set_cursor")]


# ------------------------------------------------------------------------------------------- uncertain

def test_partial_delivery_makes_uncertain_and_blocks_input_without_dll(table):
    t, backend, fake = table
    fake.rc_override["WD_SendKey"] = [0, 5]           # 2º chunk recusado: parte já foi digitada
    t.move_to(3, 14)
    with pytest.raises(KeyboardLocked) as info:
        t.send_keys("<Home>" * 100)                    # 200 bytes -> 2 chunks
    assert info.value.delivery == "partial"
    assert t.state == "uncertain"
    before = list(backend.calls)
    for action in (lambda: t.write(3, 14, "X"), lambda: t.fill(3, 14, "X"), lambda: t.type("X"),
                   lambda: t.move_to(3, 14), lambda: t.press("ENTER"), lambda: t.send_keys("X"), t.enter):
        with pytest.raises(SessionUncertain) as blocked:
            action()
        assert blocked.value.delivery == "none" and blocked.value.terminal == "D"
    assert backend.calls == before                     # nada tocou a DLL
    assert t.read(1, 2, 5) == "VOLVO"                  # leituras e esperas funcionam
    t.wait_text("VOLVO", 1, 2)
    assert t.state == "ok"
    t.write(3, 14, "X")


def test_delivery_none_keeps_ok(table):
    t, _, fake = table
    fake.rc_override["WD_SendKey"] = [5]
    with pytest.raises(KeyboardLocked) as info:
        t.press("TAB")
    assert info.value.delivery == "none"
    assert t.state == "ok"


def test_acknowledge_leaves_uncertain(table):
    t, _, _ = table
    t._mark_uncertain()
    assert t.state == "uncertain"
    assert t.acknowledge() is t and t.state == "ok"


@pytest.mark.parametrize("op", ["send", "write", "set_cursor"])
def test_ctrl_c_during_input_op_makes_uncertain(table, monkeypatch, op):
    t, backend, _ = table

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(backend, op, interrupted)
    action = {"send": lambda: t.press("TAB"), "write": lambda: t.write(3, 14, "A"),
              "set_cursor": lambda: t.move_to(3, 14)}[op]
    with pytest.raises(KeyboardInterrupt):
        action()
    assert t.state == "uncertain"


def test_ctrl_c_during_read_keeps_ok(table, monkeypatch):
    t, backend, _ = table

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(backend, "snapshot", interrupted)
    with pytest.raises(KeyboardInterrupt):
        t.snapshot()
    assert t.state == "ok"


def test_ctrl_c_while_waiting_the_answer_makes_uncertain(table, monkeypatch):
    t, _, fake = table
    fake.sessions["D"].on_aid = None                    # host mudo
    calls = {"n": 0}
    original = t._sleep

    def sleep(seconds):
        calls["n"] += 1
        if t._aid_attempts:
            raise KeyboardInterrupt
        original(seconds)

    t._sleep = sleep
    with pytest.raises(KeyboardInterrupt):
        t.enter()
    assert aids(fake) == ["ENTER"]
    assert t.state == "uncertain"


def test_aid_error_without_delivery_does_not_count_as_sent(table, monkeypatch):
    t, backend, _ = table
    real = backend.send

    def send(chunks, **kw):
        if chunks == ["@E"]:
            raise NotConnected.make(L="D", terminal="D", rc=1)       # delivery=None: a AID não saiu
        return real(chunks, **kw)

    monkeypatch.setattr(backend, "send", send)
    with pytest.raises(NotConnected):
        t.enter()
    assert t._aid_attempts == 0


def test_aid_error_with_unknown_delivery_counts_as_sent(table, monkeypatch):
    t, backend, _ = table
    real = backend.send

    def send(chunks, **kw):
        if chunks == ["@E"]:
            raise KeyboardLocked.make(delivery="unknown", terminal="D", rc=5)
        return real(chunks, **kw)

    monkeypatch.setattr(backend, "send", send)
    with pytest.raises(KeyboardLocked):
        t.enter()
    assert t._aid_attempts == 1 and t.state == "uncertain"


def test_host_connection_lost_makes_uncertain(table, monkeypatch):
    t, backend, _ = table
    offline = OIA.from_raw(bytes([1]) + b"\x10" * 80 + bytes(22))      # G1 = 0: sem sessão com o host
    real = backend.snapshot
    monkeypatch.setattr(backend, "snapshot", lambda **kw: dataclasses.replace(real(**kw), oia=offline))
    now = {"t": 0.0}
    t._clock = lambda: now["t"]
    t._sleep = lambda s: now.__setitem__("t", now["t"] + s)
    with pytest.raises(HostConnectionLost) as info:
        t.wait_text("NUNCA", 1, 2, timeout=10)
    assert info.value.terminal == "D"
    assert t.state == "uncertain"


# ------------------------------------------------------------------------------------------- replaced / closed

def test_replaced_by_other_hwnd(table):
    t, backend, fake = table
    backend.bind_instance(0x7777)                       # outra instância (como depois de fechar/reabrir)
    with pytest.raises(SessionReplaced):
        t.read(1, 2, 5)
    assert t.state == "replaced" and not t.connected
    before = list(backend.calls)
    with pytest.raises(SessionReplaced):
        t.snapshot()
    with pytest.raises(SessionReplaced):
        t.acknowledge()
    assert backend.calls == before
    assert backend.released == 1


def test_replaced_by_evict(table):
    t, backend, _ = table
    backend.evict("close")
    with pytest.raises(SessionReplaced):
        t.read(1, 2, 5)
    assert t.state == "replaced" and not t.connected


def test_second_open_replaces_and_close_closes(monkeypatch):
    fake = scenarios.cics_login()
    launcher = FakeLauncher(fake)
    user32 = user32_with_window()
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    session_mod._set_factories(backend_factory=lambda profile, py32: None, user32_factory=lambda: user32,
                               launcher_factory=lambda u32, get: launcher)
    try:
        first = session_mod.open("D")
        second = session_mod.open("d")
        assert first.state == "replaced" and second.state == "ok"
        with pytest.raises(SessionReplaced):
            first.read(1, 1, 5)
        with pytest.raises(SessionReplaced):
            first.close()                               # não fecha o terminal NOVO (A.11 c)
        assert first.state == "replaced" and second.state == "ok"
        assert not any(call[0] == "close_all" for call in launcher.calls)
        second.close()
        assert second.state == "closed" and not second.connected
        assert ("close_all", {"profile": "D", "timeout": 15.0}) in launcher.calls
        with pytest.raises(TerminalNotOpen):
            second.read(1, 1, 5)
        with pytest.raises(TerminalNotOpen):
            second.write(22, 56, "C")
        second.close()                                   # idempotente
        assert launcher.closed == 1
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


# ------------------------------------------------------------------------------------------- quem libera o quê (I8)

def test_release_keeps_hllapi_and_next_op_retakes_lease(table):
    t, backend, fake = table
    t.release()
    assert backend.released == 1 and not t.connected
    assert fake.connected == "D"                        # HLLAPI continua conectado no worker
    assert t.read(1, 2, 5) == "VOLVO" and t.connected
    t.release()
    t.release()
    assert backend.released == 2


def test_two_sessions_same_backend_release_only_with_the_last():
    t1, backend, fake = make_session(table_screen())
    from rumbapy.session import Session

    t2 = Session(backend, t1.profile, t1.info, timeout=1.0)
    t1.release()
    assert backend.released == 0
    t2.release()
    assert backend.released == 1


def test_disconnect_then_reconnect(table):
    t, backend, fake = table
    t.disconnect()
    assert "disconnect" in backend.calls and backend.released == 1
    assert fake.connected is None
    with pytest.raises(NotConnected):
        t.read(1, 2, 5)
    t.reconnect()
    assert t.connected and t.read(1, 2, 5) == "VOLVO"


def test_reconnect_other_instance_is_replaced(table):
    t, backend, fake = table
    t.disconnect()
    fake.sessions["D"].hwnd = 0x5555
    with pytest.raises(SessionReplaced):
        t.reconnect()
    assert t.state == "replaced"


def test_reconnect_without_hwnd_is_not_connected(table):
    t, backend, fake = table
    t.disconnect()
    fake.sessions["D"].hwnd = 0                          # GetSessionHWND = 0: não dá para provar a instância
    with pytest.raises(NotConnected) as info:
        t.reconnect()
    assert info.value.function == "WD_GetSessionHWND"
    assert t.state == "ok" and not t.connected


def test_gc_releases_the_lease():
    t, backend, _ = make_session(table_screen())
    del t
    gc.collect()
    assert backend.released == 1


def test_with_releases_and_never_masks(table):
    t, backend, _ = table
    with pytest.raises(ZeroDivisionError):
        with t:
            1 / 0
    assert backend.released == 1 and t.state == "ok"


def test_close_on_exit(monkeypatch):
    launcher = FakeLauncher(scenarios.cics_login())
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    session_mod._set_factories(backend_factory=lambda profile, py32: None, user32_factory=lambda: None,
                               launcher_factory=lambda u32, get: launcher)
    try:
        with session_mod.open("D", close_on_exit=True) as t:
            pass
        assert t.state == "closed" and launcher.closed == 1
        assert launcher.backends[0].released == 1
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


def test_close_on_exit_skips_replaced_session(monkeypatch):
    fake = scenarios.cics_login()
    launcher = FakeLauncher(fake)
    user32 = user32_with_window()
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    session_mod._set_factories(backend_factory=lambda profile, py32: None, user32_factory=lambda: user32,
                               launcher_factory=lambda u32, get: launcher)
    try:
        with session_mod.open("D", close_on_exit=True) as first:
            second = session_mod.open("D")
        assert first.state == "replaced" and second.state == "ok"
        assert launcher.closed == 0                      # o terminal NOVO continua aberto
        second.close()
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


def test_repr_has_no_screen_content(table):
    t, _, _ = table
    assert repr(t) == "<Session D BRAZIL_D 24x80 ok>"
    assert "VOLVO" not in repr(t)


def test_wait_timeout_does_not_make_uncertain(table):
    t, _, _ = table
    with pytest.raises(WaitTimeout):
        t.wait_text("NUNCA", timeout=0.1)
    assert t.state == "ok"
    assert send_calls(t._backend.fake) == []
