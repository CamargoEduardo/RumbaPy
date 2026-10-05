"""IPC real em processo (C.1, C.2): pipe com DACL, autenticação mútua, limite de mensagem, PID do servidor, chave."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rumbapy import _ipc, _paths, _protocol, _win32
from rumbapy import errors

import threading
import secrets
import hashlib
import hmac
import re
import os

import pytest

KEY = secrets.token_bytes(32)


def _swallow(fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except (OSError, EOFError, ValueError):
        pass


def pipe_name() -> str:
    return _paths.pipe_name("D", _paths.new_pipe_rand())


@contextmanager
def server(name: str, handler: Callable[[_ipc.Channel], Any]) -> Iterator[dict[str, Any]]:
    """Uma instância do pipe + thread que aceita 1 cliente e roda ``handler``; ``out`` guarda resultado/erro."""
    security = _win32.pipe_security_attributes()
    handle = _ipc.create_pipe(name, first=True, security=security)
    out: dict[str, Any] = {"handle": handle}
    stop = threading.Event()

    def run() -> None:
        try:
            if _ipc.accept(handle, stop.is_set):
                channel = _ipc.wrap(handle, letter="D")
                out["channel"] = channel
                out["result"] = handler(channel)
        except BaseException as exc:                 # noqa: BLE001 - devolvido ao teste
            out["error"] = exc
            if "channel" in out:                     # como o worker: erro na conexão => fecha
                out["channel"].close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield out
    finally:
        stop.set()
        thread.join(5)
        channel = out.get("channel")
        if channel is not None:
            channel.close()
        else:
            _win32.close_handle(handle)


def auth_then_echo(key: bytes) -> Callable[[_ipc.Channel], Any]:
    def handler(channel: _ipc.Channel) -> Any:
        _ipc.server_auth(channel, key)
        msg = channel.recv_msg(5.0)
        channel.send_msg({"echo": msg})
        return msg
    return handler


def test_right_key_authenticates_both_ways() -> None:
    name = pipe_name()
    with server(name, auth_then_echo(KEY)) as out:
        client = _ipc.connect(name, os.getpid(), letter="D")
        _ipc.client_auth(client, KEY, server_pid=os.getpid())
        client.send_msg({"oi": "çãÉ€"})
        assert client.recv_msg(5.0) == {"echo": {"oi": "çãÉ€"}}
        client.close()
    assert out.get("result") == {"oi": "çãÉ€"} and "error" not in out


def test_wrong_client_key_is_refused_by_the_worker() -> None:
    name = pipe_name()
    with server(name, auth_then_echo(KEY)) as out:
        client = _ipc.connect(name, os.getpid(), letter="D")
        with pytest.raises(_ipc.AuthRejected):
            _ipc.client_auth(client, secrets.token_bytes(32), server_pid=os.getpid())
        client.close()
    assert isinstance(out.get("error"), _ipc.AuthFailed)


def test_impostor_worker_is_refused_by_the_client() -> None:
    """O servidor não conhece a chave: o ``mw`` não confere -> ``ChannelHijacked``."""
    wrong = secrets.token_bytes(32)

    def impostor(channel: _ipc.Channel) -> None:
        nw = secrets.token_bytes(32)
        channel.send_msg({"t": "auth", "v": 1, "nw": nw.hex()})
        reply = channel.recv_msg(5.0)
        nc = bytes.fromhex(reply["nc"])
        channel.send_msg({"t": "auth_ok", "mw": hmac.new(wrong, b"rumbapy2|w|" + nc + nw, hashlib.sha256).hexdigest()})
        channel.recv_msg(2.0)

    name = pipe_name()
    with server(name, impostor):
        client = _ipc.connect(name, os.getpid(), letter="D")
        with pytest.raises(errors.ChannelHijacked):
            _ipc.client_auth(client, KEY, server_pid=os.getpid())
        client.close()


def test_silent_client_times_out_on_the_worker() -> None:
    name = pipe_name()
    with server(name, auth_then_echo(KEY)) as out:
        client = _ipc.connect(name, os.getpid(), letter="D")
        assert client.recv_msg(3.0)["t"] == "auth"                   # não responde
        deadline_thread = threading.Event()
        deadline_thread.wait(_protocol.AUTH_STEP_S + 0.5)
        client.close()
    assert isinstance(out.get("error"), _ipc.AuthFailed) and "2 s" in str(out["error"])


def test_message_over_1_mib_is_a_protocol_error() -> None:
    def handler(channel: _ipc.Channel) -> Any:
        return channel.recv_msg(5.0)

    name = pipe_name()
    with server(name, handler) as out:
        client = _ipc.connect(name, os.getpid(), letter="D")
        # a escrita grande pode ficar presa no WriteFile do multiprocessing: vai numa thread à parte
        writer = threading.Thread(target=_swallow, args=(client.conn.send_bytes, b"x" * (_protocol.MAX_MESSAGE + 10)),
                                  daemon=True)
        writer.start()
        deadline = threading.Event()
        for _ in range(100):
            if "error" in out or "result" in out:
                break
            deadline.wait(0.05)
        assert isinstance(out.get("error"), errors.ProtocolError), out
        assert out["channel"].closed
        client.close()
        writer.join(5)
    with pytest.raises(errors.ProtocolError):
        _protocol.encode({"x": "a" * _protocol.MAX_MESSAGE})


def test_pipe_dacl_has_only_the_user_and_system() -> None:
    security = _win32.pipe_security_attributes()
    handle = _ipc.create_pipe(pipe_name(), first=True, security=security)
    try:
        sddl = _win32.object_dacl_sddl(handle)
    finally:
        _win32.close_handle(handle)
    assert sddl.startswith("D:P")                                   # protegida: nada herdado
    aces = re.findall(r"\(([^)]*)\)", sddl)
    sids = {ace.split(";")[-1] for ace in aces}
    assert sids == {_win32.user_sid(), "SY"}
    assert all(ace.split(";")[0] == "A" for ace in aces)
    assert security.sddl == f"D:P(A;;GA;;;{_win32.user_sid()})(A;;GA;;;SY)"


def test_first_instance_twice_and_missing_pipe() -> None:
    name = pipe_name()
    security = _win32.pipe_security_attributes()
    handle = _ipc.create_pipe(name, first=True, security=security)
    try:
        with pytest.raises(_ipc.PipeExists):
            _ipc.create_pipe(name, first=True, security=security)
        second = _ipc.create_pipe(name, first=False, security=security)
        _win32.close_handle(second)
    finally:
        _win32.close_handle(handle)
    with pytest.raises(_ipc.PipeNotFound):
        _ipc.connect(pipe_name(), os.getpid(), timeout=1.0, letter="D")


def test_server_pid_must_match_the_registry() -> None:
    name = pipe_name()
    with server(name, lambda ch: ch.recv_msg(1.0)):
        with pytest.raises(errors.ChannelHijacked) as info:
            _ipc.connect(name, os.getpid() + 1, letter="D")
        assert str(os.getpid()) in info.value.message


def test_accept_stops_promptly() -> None:
    handle = _ipc.create_pipe(pipe_name(), first=True, security=_win32.pipe_security_attributes())
    try:
        assert _ipc.accept(handle, lambda: True, slice_s=0.05) is False
    finally:
        _win32.close_handle(handle)


def test_keys(tmp_path: Path) -> None:
    path = tmp_path / "authkey.bin"
    key = _ipc.load_or_create_key(path, workers_alive=lambda: False)
    assert len(key) == 32 and path.read_bytes() == key
    assert _ipc.load_or_create_key(path, workers_alive=lambda: True) == key
    assert not list(tmp_path.glob("*.tmp-*"))
    path.write_bytes(b"curta")
    with pytest.raises(ValueError):
        _ipc.read_key(path)
    with pytest.raises(errors.WorkerError) as info:
        _ipc.load_or_create_key(path, workers_alive=lambda: True)
    assert "authkey.bin está corrompido" in info.value.message
    new = _ipc.load_or_create_key(path, workers_alive=lambda: False)
    assert len(new) == 32 and new != key


def test_process_alive_ignores_stale_last_error_for_invalid_pids() -> None:
    import ctypes

    for bad in (0, -1, True, None, "12"):
        ctypes.set_last_error(5)                                           # ERROR_ACCESS_DENIED "antigo"
        assert _win32.process_alive(bad) is False  # type: ignore[arg-type]
    assert _win32.process_alive(os.getpid()) is True


def test_hllapi_error_dialog_check_never_sends_window_messages() -> None:
    import inspect

    source = inspect.getsource(_win32.hllapi_error_dialog_visible)
    assert "InternalGetWindowText" in source and "_GetWindowTextW" not in source
    assert _win32.hllapi_error_dialog_visible(os.getpid()) is False


class _SlowConn:
    """``PipeConnection`` de mentira: ``send_bytes`` espera ``gate`` (escrita em curso em T-dll)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.writing = threading.Event()
        self.sent: list[bytes] = []
        self.closed_at: list[int] = []

    def send_bytes(self, data: bytes) -> None:
        self.writing.set()
        assert self.gate.wait(5)
        assert not self.closed_at, "escreveu num handle já fechado"
        self.sent.append(data)

    def close(self) -> None:
        self.closed_at.append(len(self.sent))

    def fileno(self) -> int:
        return 0


def test_close_waits_for_the_write_in_progress() -> None:
    conn = _SlowConn()
    channel = _ipc.Channel(conn, letter="D")  # type: ignore[arg-type]
    writer = threading.Thread(target=channel.send_msg, args=({"id": 1, "ok": True},), daemon=True)
    writer.start()
    assert conn.writing.wait(5)
    closer = threading.Thread(target=channel.close, daemon=True)
    closer.start()
    closer.join(0.2)
    assert closer.is_alive() and conn.closed_at == []                      # close() espera a trava de escrita
    conn.gate.set()
    writer.join(5)
    closer.join(5)
    assert conn.closed_at == [1]                                           # fechou só depois da escrita
    with pytest.raises(BrokenPipeError):                                   # canal fechado: nada de escrever
        channel.send_msg({"id": 2, "ok": True})
    assert len(conn.sent) == 1


def test_close_that_gives_up_waiting_leaves_closing_to_the_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ipc, "CLOSE_LOCK_S", 0.05)
    conn = _SlowConn()
    channel = _ipc.Channel(conn, letter="D")  # type: ignore[arg-type]
    writer = threading.Thread(target=_swallow, args=(channel.send_msg, {"id": 1, "ok": True}), daemon=True)
    writer.start()
    assert conn.writing.wait(5)
    channel.close()                                                        # desistiu: não fecha por baixo da escrita
    assert channel.closed and conn.closed_at == []
    conn.gate.set()
    writer.join(5)
    assert conn.closed_at == [1]                                           # quem escrevia fechou ao soltar a trava


def test_pipe_rejects_remote_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, int] = {}

    def fake_create(name: str, open_mode: int, pipe_mode: int, *rest: Any) -> int:
        seen["pipe_mode"] = pipe_mode
        return 1234

    monkeypatch.setattr(_ipc._winapi, "CreateNamedPipe", fake_create)
    assert _ipc.create_pipe(pipe_name(), first=True, security=_win32.pipe_security_attributes()) == 1234
    assert seen["pipe_mode"] & _ipc.PIPE_REJECT_REMOTE_CLIENTS
    assert seen["pipe_mode"] & _ipc.PIPE_TYPE_MESSAGE and seen["pipe_mode"] & _ipc.PIPE_READMODE_MESSAGE


def test_corrupt_key_is_recreated_only_under_the_key_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rumbapy import _locks

    monkeypatch.setattr(_ipc, "KEY_LOCK_S", 0.2)
    path = tmp_path / "authkey.bin"
    path.write_bytes(b"curta")
    held = _locks.file_lock("key", 1.0)                                    # outro processo mexendo na chave
    try:
        with pytest.raises(errors.WorkerError, match="outro processo"):
            _ipc.load_or_create_key(path, workers_alive=lambda: False)
        assert path.read_bytes() == b"curta"                               # sem a trava, nada foi recriado
    finally:
        held.release()
    key = _ipc.load_or_create_key(path, workers_alive=lambda: False)       # trava padrão: locks\key.lock
    assert len(key) == 32 and path.read_bytes() == key
