"""E.8: neutralização de servidores v0.1.5 com um servidor TCP falso em porta efêmera de 127.0.0.1."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from rumbapy import _legacy

import threading
import socket
import pytest
import json
import os

OUR_SESSION = 1


class FakeLegacyServer:
    """Imita o servidor 0.1.5: lê JSON até EOF, grava a requisição e responde conforme ``reply(req)``."""

    def __init__(self, reply: Callable[[dict], dict | None]) -> None:
        self.reply = reply
        self.requests: list[dict] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(3)
                data = b""
                try:
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    continue
                req = json.loads(data.decode("utf-8"))
                self.requests.append(req)
                answer = self.reply(req)
                if answer is None:                 # "ocupado": segura a conexão sem responder
                    self._stop.wait(1.0)
                    continue
                try:
                    conn.sendall(json.dumps(answer).encode("utf-8"))
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self.sock.close()
        self.thread.join(2)


@pytest.fixture
def server_factory() -> Iterator[Callable[[Callable[[dict], dict | None]], FakeLegacyServer]]:
    servers: list[FakeLegacyServer] = []

    def make(reply: Callable[[dict], dict | None]) -> FakeLegacyServer:
        server = FakeLegacyServer(reply)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


def _run(server: FakeLegacyServer | None, *, pid: int | None = 4321, session: int = OUR_SESSION,
         letter: str = "D", **kw: Any) -> _legacy.LegacyStatus:
    port = server.port if server is not None else 1
    return _legacy.neutralize(letter, port=port, listener_pid=lambda p: pid, session_of=lambda p: session,
                              our_session=lambda: OUR_SESSION, **kw)


def v015(req: dict) -> dict:
    return {"success": True}


def test_real_015_receives_exactly_ping_and_disconnect(server_factory, caplog) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(v015)
    with caplog.at_level("WARNING", logger="rumbapy._legacy"):
        st = _run(server)
    assert [r["action"] for r in server.requests] == ["ping", "disconnect"]
    for req in server.requests:
        assert req["session_id"] == "rumbapy2-migracao" and req["params"] == {"terminal_type": "D"}
    assert st.present and st.disconnected == 1 and not st.busy
    assert f"Servidor legado v0.1.5 (porta {server.port}) desconectado do terminal D" in caplog.text
    assert "close_terminal" not in json.dumps(server.requests)


def test_shim_is_left_alone(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(lambda req: {"success": True, "shim": True})
    st = _run(server)
    assert [r["action"] for r in server.requests] == ["ping"]
    assert st.shim and st.disconnected == 0


def test_server_of_another_windows_session_is_untouched(server_factory, caplog) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(v015)
    with caplog.at_level("WARNING", logger="rumbapy._legacy"):
        st = _run(server, session=7)
    assert server.requests == [] and st.other_session
    assert "outra sessão" in caplog.text


def test_no_listener_does_nothing(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(v015)
    st = _run(server, pid=None)
    assert server.requests == [] and not st.present


def test_refused_connection_does_nothing() -> None:
    # (no Windows, um RST de porta local fechada pode demorar ~2 s: aqui a recusa é injetada)
    def refused(address, timeout):  # type: ignore[no-untyped-def]
        raise ConnectionRefusedError(10061, "recusado")

    st = _legacy.neutralize("D", port=1, listener_pid=lambda p: 99, session_of=lambda p: OUR_SESSION,
                            our_session=lambda: OUR_SESSION, connect=refused)
    assert not st.present and not st.busy


def test_connect_timeout_marks_busy() -> None:
    def slow(address, timeout):  # type: ignore[no-untyped-def]
        raise TimeoutError("timed out")

    st = _legacy.neutralize("D", port=1, listener_pid=lambda p: 99, session_of=lambda p: OUR_SESSION,
                            our_session=lambda: OUR_SESSION, connect=slow)
    assert st.present and st.busy
    assert "v0.1.5" in _legacy.busy_extra("D") and str(1) in _legacy.busy_extra("D")


def test_silent_server_marks_busy(server_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_legacy, "READ_TIMEOUT", 0.3)
    server = server_factory(lambda req: None)
    st = _run(server)
    assert st.busy and [r["action"] for r in server.requests] == ["ping"]


def test_reply_without_success_stops(server_factory, caplog) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(lambda req: {"success": False, "error": "x"})
    with caplog.at_level("WARNING", logger="rumbapy._legacy"):
        st = _run(server)
    assert [r["action"] for r in server.requests] == ["ping"] and st.disconnected == 0
    assert "success=true" in caplog.text


def test_once_per_process_and_letter(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(v015)
    first = _run(server)
    again = _run(server)
    assert again is first and len(server.requests) == 2
    _run(server, force=True)
    assert len(server.requests) == 4
    assert _legacy.status("D") is not None and _legacy.busy_extra("D") == ""


def test_ports_by_letter() -> None:
    assert [_legacy.port_for(x) for x in "DAZX"] == [8500, 8600, 8700, 8800]


def test_never_raises(server_factory) -> None:  # type: ignore[no-untyped-def]
    def boom(port):  # type: ignore[no-untyped-def]
        raise OSError("tabela TCP ilegível")

    st = _legacy.neutralize("D", port=1, listener_pid=boom, session_of=lambda p: OUR_SESSION,
                            our_session=lambda: OUR_SESSION)
    assert not st.present


def test_own_pid_listener_counts_as_same_session(server_factory) -> None:  # type: ignore[no-untyped-def]
    """Com o PID do próprio teste e a sessão real lida por ``ProcessIdToSessionId`` (só leitura)."""
    server = server_factory(lambda req: {"success": True, "shim": True})
    st = _legacy.neutralize("Z", port=server.port, listener_pid=lambda p: os.getpid())
    assert st.shim and [r["action"] for r in server.requests] == ["ping"]


def test_sandbox_never_touches_the_real_legacy_ports() -> None:
    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("contato com porta real no sandbox")

    st = _legacy.neutralize("D", listener_pid=forbidden, connect=forbidden)
    assert st.port == 8500 and not st.present and _legacy.status("D") is st


# ------------------------------------------------------------------------------------------- vários ouvintes
def test_two_listeners_get_up_to_three_rounds(server_factory) -> None:  # type: ignore[no-untyped-def]
    """``SO_REUSEADDR``: não se sabe qual processo atende; repete ping/disconnect (idempotente) até 3 vezes."""
    server = server_factory(v015)
    st = _legacy.neutralize("D", port=server.port, listener_pids=lambda p: [4321, 4322],
                            session_of=lambda p: OUR_SESSION, our_session=lambda: OUR_SESSION)
    assert [r["action"] for r in server.requests] == ["ping", "disconnect"] * 3
    assert st.disconnected == 3 and st.pids == [4321, 4322]


def test_one_listener_of_another_session_blocks_all(server_factory, caplog) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(v015)
    with caplog.at_level("WARNING", logger="rumbapy._legacy"):
        st = _legacy.neutralize("D", port=server.port, listener_pids=lambda p: [4321, 4322],
                                session_of=lambda p: OUR_SESSION if p == 4321 else 9,
                                our_session=lambda: OUR_SESSION)
    assert server.requests == [] and st.other_session and "4322" in caplog.text


def test_shim_then_real_server_on_the_same_port(server_factory) -> None:  # type: ignore[no-untyped-def]
    replies = iter([{"success": True, "shim": True}, {"success": True}, {"success": True},
                    {"success": True}, {"success": True}])
    server = server_factory(lambda req: next(replies))
    st = _legacy.neutralize("D", port=server.port, listener_pids=lambda p: [10, 11],
                            session_of=lambda p: OUR_SESSION, our_session=lambda: OUR_SESSION)
    assert [r["action"] for r in server.requests] == ["ping", "ping", "disconnect", "ping", "disconnect"]
    assert st.shim and st.disconnected == 2


def test_default_listener_scan_reads_the_tcp_table_only() -> None:
    """``_listener_pids`` lê a tabela TCP (só leitura) e acha o próprio ouvinte deste teste."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert os.getpid() in _legacy._listener_pids(port)
    finally:
        sock.close()
