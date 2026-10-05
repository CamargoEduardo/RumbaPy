"""``WorkerHandle.request``: prazos e Ctrl+C (C.7), erros de transporte (C.8), eventos (C.3), trava por handle."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit.client.bridge.fakes import FakeChannel, FakeClock, ok
from rumbapy.errors import (KeyboardLocked, ProtocolError, SessionReplaced, SessionStopped, WorkerCrashed,
                            WorkerError, WorkerHung, to_wire)
from rumbapy import _bridge, _paths, _protocol

import threading
import time

import pytest


@pytest.fixture
def alive(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    state = {"alive": True}
    monkeypatch.setattr(_bridge, "process_alive", lambda pid, created=None: state["alive"])
    return state


def make(channel: FakeChannel, tmp_path: Path) -> _bridge.WorkerHandle:
    return _bridge.WorkerHandle(channel, letter="D", mode="owner", pid=4321, created=None, hello={},
                                registry=tmp_path / "worker.json")


def test_result_and_hwnd_only_on_checked_ops(alive: dict, tmp_path: Path) -> None:
    ch = FakeChannel(responder=ok({"text": "x", "busy": False, "nul_at": -1, "nul_tail": 0}))
    h = make(ch, tmp_path)
    assert h.request("read", {"row": 1, "col": 1, "length": 1}, hwnd=77)["text"] == "x"
    assert ch.sent[0] == {"id": 1, "op": "read", "args": {"row": 1, "col": 1, "length": 1}, "hwnd": 77}
    h.request("sessions", hwnd=77)
    assert ch.sent[1]["id"] == 2 and "hwnd" not in ch.sent[1]


def test_worker_error_is_raised_as_sent(alive: dict, tmp_path: Path) -> None:
    exc = KeyboardLocked.make(rc=5, function="WD_SendKey", delivery="partial", details={"chunks_sent": 1})
    ch = FakeChannel(responder=lambda m: [_protocol.make_error(m["id"], exc)])
    h = make(ch, tmp_path)
    with pytest.raises(KeyboardLocked) as info:
        h.request("send", {"chunks": ["a"], "secret": False})
    assert info.value.delivery == "partial" and info.value.terminal == "D"
    assert h.alive                                                      # erro do worker não mata o canal


def test_events_evicted_then_session_replaced(alive: dict, tmp_path: Path) -> None:
    def respond(m: dict) -> list[Any]:
        return [{"event": "evicted", "reason": "close"}, _protocol.make_result(m["id"], {"uptime_s": 1})]

    h = make(FakeChannel(responder=respond), tmp_path)
    h.request("ping")
    assert h.evicted
    with pytest.raises(SessionReplaced):
        h.request("read", {"row": 1, "col": 1, "length": 1})
    h.request("ping")                                                   # ops admin continuam


def test_shutting_down_during_request_is_crash(alive: dict, tmp_path: Path) -> None:
    h = make(FakeChannel(responder=lambda m: [{"event": "shutting_down", "reason": "idle"}]), tmp_path)
    with pytest.raises(WorkerCrashed) as info:
        h.request("read", {"row": 1, "col": 1, "length": 1})
    assert info.value.delivery is None and h.dead and h.exiting


def _eof() -> Any:
    raise EOFError


@pytest.mark.parametrize("op, args, delivery", [
    ("read", {"row": 1, "col": 1, "length": 1}, None),
    ("send", {"chunks": ["a"], "secret": False}, "unknown"),
])
def test_eof_is_worker_crashed(alive: dict, tmp_path: Path, op: str, args: dict, delivery: str | None) -> None:
    h = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(WorkerCrashed) as info:
        h.request(op, args)
    assert info.value.delivery == delivery and h.channel.closed
    if delivery:
        assert "Não sei se a entrada chegou ao host" in info.value.message


def test_exit_reason_from_registry_picks_the_class(alive: dict, tmp_path: Path) -> None:
    reg = tmp_path / "worker.json"
    _paths.write_json(reg, {"pid": 4321, "exit_reason": "window_gone"})
    h = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(SessionStopped):
        h.request("send", {"chunks": ["a"], "secret": False})
    assert h.exiting
    _paths.write_json(reg, {"pid": 4321, "exit_reason": "hung"})
    h2 = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(WorkerHung):
        h2.request("read", {"row": 1, "col": 1, "length": 1})
    _paths.write_json(reg, {"pid": 9999, "exit_reason": "hung"})     # registro de outro worker: não conta
    h3 = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(WorkerCrashed):
        h3.request("read", {"row": 1, "col": 1, "length": 1})


def test_dead_pid_detected_every_second(alive: dict, tmp_path: Path) -> None:
    clock = FakeClock()
    ch = FakeChannel()
    h = make(ch, tmp_path)
    alive["alive"] = False

    def recv(timeout: float) -> None:
        clock.now += timeout
        return None

    ch.recv_msg = recv  # type: ignore[method-assign]
    with pytest.raises(WorkerCrashed):
        h.request("read", {"row": 1, "col": 1, "length": 1}, clock=clock, sleep=clock.sleep)
    assert clock.now - 1000.0 <= 1.2


def test_client_timeout_is_worker_hung_after_pid_exits(alive: dict, tmp_path: Path) -> None:
    clock = FakeClock()
    ch = FakeChannel()

    def recv(timeout: float) -> None:
        clock.now += timeout
        if clock.now > 1000.0 + 25.0:
            alive["alive"] = False                                       # o worker sai depois do prazo
        return None

    ch.recv_msg = recv  # type: ignore[method-assign]
    h = make(ch, tmp_path)
    with pytest.raises(WorkerHung) as info:
        h.request("read", {"row": 1, "col": 1, "length": 1}, clock=clock, sleep=clock.sleep)
    assert info.value.details["client_timeout"] is True and ch.closed
    assert 1025.0 <= clock.now < 1027.0


def test_client_timeout_worker_never_exits(alive: dict, tmp_path: Path) -> None:
    clock = FakeClock()
    ch = FakeChannel()

    def recv(timeout: float) -> None:
        clock.now += timeout
        return None

    ch.recv_msg = recv  # type: ignore[method-assign]
    h = make(ch, tmp_path)
    with pytest.raises(WorkerError) as info:
        h.request("send", {"chunks": ["a"], "secret": False}, clock=clock, sleep=clock.sleep)
    assert type(info.value) is WorkerError
    assert "(PID 4321) não encerrou" in info.value.message and "Gerenciador de Tarefas" in info.value.hint
    assert clock.now >= 1000.0 + 30.0 + 30.0                              # 25·1+5 de prazo + 30 s esperando


def test_ctrl_c_closes_connection_and_reraises(alive: dict, tmp_path: Path) -> None:
    def interrupt() -> Any:
        raise KeyboardInterrupt

    ch = FakeChannel(responder=lambda m: [interrupt])
    h = make(ch, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        h.request("send", {"chunks": ["a"], "secret": False})
    assert ch.closed and h.dead and not h.lock.locked()


def test_protocol_error_closes(alive: dict, tmp_path: Path) -> None:
    ch = FakeChannel(responder=lambda m: [{"id": m["id"] + 5, "ok": True, "result": {}}])
    h = make(ch, tmp_path)
    with pytest.raises(ProtocolError):
        h.request("ping")
    assert ch.closed


def test_worker_hung_reply_marks_exiting(alive: dict, tmp_path: Path) -> None:
    exc = WorkerHung.make(funcao="WD_CopyPS", s=20, function="WD_CopyPS")
    ch = FakeChannel(responder=lambda m: [{"id": m["id"], "ok": False, "error": to_wire(exc)}])
    h = make(ch, tmp_path)
    with pytest.raises(WorkerHung):
        h.request("snapshot", {"fields": False})
    assert h.exiting and h.dead
    alive["alive"] = False
    h.wait_gone(1.0)                                                      # já saiu: volta na hora


def test_abandoned_thread_does_not_block(alive: dict, tmp_path: Path) -> None:
    """Uma thread presa com a trava do handle não prende quem vem depois quando o handle morre."""
    h = make(FakeChannel(), tmp_path)
    h.lock.acquire()                                                      # "thread abandonada"
    done: list[BaseException] = []

    def other() -> None:
        try:
            h.request("ping")
        except BaseException as exc:                                      # noqa: BLE001
            done.append(exc)

    t = threading.Thread(target=other)
    t.start()
    time.sleep(0.3)
    h.dead = True                                                         # o worker caiu / foi reciclado
    t.join(2.0)
    assert not t.is_alive() and isinstance(done[0], WorkerCrashed)
    h.lock.release()


def test_client_timeout_worker_never_exits_on_input_op_is_unknown(alive: dict, tmp_path: Path) -> None:
    """Op N enviada e o PID não sai em 30 s: a AID pode ter chegado -> ``delivery=unknown`` (Session uncertain)."""
    clock = FakeClock()
    ch = FakeChannel()

    def recv(timeout: float) -> None:
        clock.now += timeout
        return None

    ch.recv_msg = recv  # type: ignore[method-assign]
    h = make(ch, tmp_path)
    with pytest.raises(WorkerError) as info:
        h.request("send", {"chunks": ["@E"], "secret": False}, clock=clock, sleep=clock.sleep)
    assert info.value.delivery == "unknown"
    assert info.value.details["op"] == "send" and info.value.details["client_timeout"] is True


def test_client_timeout_worker_never_exits_on_read_has_no_delivery(alive: dict, tmp_path: Path) -> None:
    clock = FakeClock()
    ch = FakeChannel()

    def recv(timeout: float) -> None:
        clock.now += timeout
        return None

    ch.recv_msg = recv  # type: ignore[method-assign]
    h = make(ch, tmp_path)
    with pytest.raises(WorkerError) as info:
        h.request("read", {"row": 1, "col": 1, "length": 1}, clock=clock, sleep=clock.sleep)
    assert info.value.delivery is None


def _bad_json() -> Any:
    raise ProtocolError.make(L="D", detalhe="JSON inválido")


@pytest.mark.parametrize("reply", [
    "bad_json",
    "wrong_id",
])
@pytest.mark.parametrize("op, args, delivery", [
    ("read", {"row": 1, "col": 1, "length": 1}, None),
    ("send", {"chunks": ["@E"], "secret": False}, "unknown"),
    ("write", {"row": 1, "col": 1, "text": "a", "secret": False}, "unknown"),
    ("set_cursor", {"row": 1, "col": 1}, "unknown"),
])
def test_local_protocol_error_after_send(alive: dict, tmp_path: Path, reply: str, op: str, args: dict,
                                         delivery: str | None) -> None:
    """C.8: resposta corrompida depois do envio é falha de transporte; op N de entrada -> ``delivery=unknown``."""
    def respond(m: dict) -> list[Any]:
        if reply == "bad_json":
            return [_bad_json]
        return [{"id": m["id"] + 7, "ok": True, "result": {}}]

    ch = FakeChannel(responder=respond)
    h = make(ch, tmp_path)
    with pytest.raises(ProtocolError) as info:
        h.request(op, args)
    assert info.value.delivery == delivery and ch.closed


def test_protocol_error_sent_by_worker_is_kept(alive: dict, tmp_path: Path) -> None:
    """``ProtocolError`` mandado pelo próprio worker (recusa da requisição) sobe como veio, sem ``unknown``."""
    exc = ProtocolError.make(L="D", detalhe="args inválidos")
    ch = FakeChannel(responder=lambda m: [_protocol.make_error(m["id"], exc)])
    h = make(ch, tmp_path)
    with pytest.raises(ProtocolError) as info:
        h.request("send", {"chunks": ["a"], "secret": False})
    assert info.value.delivery is None


@pytest.mark.parametrize("op, args, delivery", [
    ("send", {"chunks": ["@E"], "secret": False}, "none"),
    ("read", {"row": 1, "col": 1, "length": 1}, None),
])
def test_dead_before_send_is_not_uncertain(alive: dict, tmp_path: Path, op: str, args: dict,
                                           delivery: str | None) -> None:
    """Outra thread já viu o EOF: nada é enviado, então a Session não fica ``uncertain`` à toa."""
    ch = FakeChannel()
    h = make(ch, tmp_path)
    h.dead = True
    with pytest.raises(WorkerCrashed) as info:
        h.request(op, args)
    assert info.value.delivery == delivery and ch.sent == []
    assert "Não sei se a entrada" not in info.value.message


def test_dead_while_waiting_for_lock_is_not_uncertain(alive: dict, tmp_path: Path) -> None:
    h = make(FakeChannel(), tmp_path)
    h.lock.acquire()
    errors: list[BaseException] = []

    def other() -> None:
        try:
            h.request("send", {"chunks": ["a"], "secret": False})
        except BaseException as exc:                                      # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=other)
    t.start()
    time.sleep(0.2)
    h.dead = True
    t.join(2.0)
    h.lock.release()
    assert not t.is_alive() and isinstance(errors[0], WorkerCrashed) and errors[0].delivery == "none"


def test_held_acquires_in_slices() -> None:
    lock = threading.Lock()
    calls: list[float | None] = []

    class Spy:
        def acquire(self, timeout: float = -1) -> bool:
            calls.append(timeout)
            return len(calls) >= 3 and lock.acquire(timeout=timeout)

        def release(self) -> None:
            lock.release()

    with _bridge.held(Spy()):
        assert lock.locked()
    assert calls == [_bridge.SLICE_S] * 3 and not lock.locked()


def test_exit_reason_hung_message_uses_the_op_limit(alive: dict, tmp_path: Path) -> None:
    reg = tmp_path / "worker.json"
    _paths.write_json(reg, {"pid": 4321, "exit_reason": "hung"})
    h = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(WorkerHung) as info:
        h.request("connect", {"letter": "D"})
    assert "WD_ConnectPS em 15 s" in info.value.message
    _paths.write_json(reg, {"pid": 4321, "exit_reason": "stop_while_hung"})
    h2 = make(FakeChannel(responder=lambda m: [_eof]), tmp_path)
    with pytest.raises(WorkerHung) as info2:
        h2.request("read", {"row": 1, "col": 1, "length": 1})
    assert "em 5 s" in info2.value.message
