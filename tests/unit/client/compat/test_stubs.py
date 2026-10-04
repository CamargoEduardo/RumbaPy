"""Stubs legados: ``rumbapy.server`` (B.9.4, sem DLL) e ``rumbapy.api`` (B.9.3, ``ImportError``).

O servidor é exercitado só em portas efêmeras do loopback (nunca nas portas 8500/8600/8700/8800 da v0.1.5).
"""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy import server as stub

import importlib
import threading
import logging
import socket
import json
import time
import sys

import pytest


def _request(port: int, payload: dict) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(json.dumps(payload).encode("utf-8"))
        conn.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := conn.recv(4096):
            data += chunk
    return json.loads(data.decode("utf-8"))


@pytest.fixture
def listening():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    thread = threading.Thread(target=stub.serve, args=(server,), kwargs={"lifetime": 10.0}, daemon=True)
    thread.start()
    yield port, thread
    thread.join(5)
    server.close()


def test_ping_then_any_action_ends_the_stub(listening, caplog):
    port, thread = listening
    with caplog.at_level(logging.DEBUG, logger="rumbapy.server"):
        pong = _request(port, {"session_id": "D_x", "action": "ping", "params": {"terminal_type": "D"}})
        assert pong["success"] is True and pong["shim"] is True and pong["message"] == "Server is running"
        assert pong["version"] == "2.0.0"
        assert thread.is_alive()
        again = _request(port, {"action": "ping", "params": {}})
        assert again["shim"] is True
        answer = _request(port, {"session_id": "D_x", "action": "logon_cics",
                                 "params": {"uid": "A000000", "pwd": "Zq7pw9k"}})
        assert answer == {"success": False, "error": "O RumbaPy foi atualizado para a v2. Rode o script de novo."}
        thread.join(5)
    assert not thread.is_alive()                               # saiu após a 1ª ação que não é ping
    assert "Zq7pw9k" not in caplog.text and "A000000" not in caplog.text


def test_garbage_is_an_error_and_ends(listening):
    port, thread = listening
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(b"\xff not json")
        conn.shutdown(socket.SHUT_WR)
        reply = json.loads(conn.recv(4096).decode("utf-8"))
    assert reply["success"] is False
    thread.join(5)
    assert not thread.is_alive()


def test_lifetime_ends_without_connections():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    start = time.monotonic()
    try:
        stub.serve(server, lifetime=0.3)
    finally:
        server.close()
    assert 0.25 <= time.monotonic() - start < 3


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_main_binds_letter_port(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(stub, "PORTS", {"D": port})
    seen = {}

    def fake_serve(server, **kw):
        seen["addr"] = server.getsockname()

    monkeypatch.setattr(stub, "serve", fake_serve)
    assert stub.main(["--terminal_type=D"]) == 0
    assert seen["addr"] == ("127.0.0.1", port)


def test_main_lowercase_letter_uses_default_port_like_v015(monkeypatch):
    # o cliente v0.1.5 faz ports.get(terminal_type, 8800) sem normalizar: 'd' conecta na 8800
    port, other = _free_port(), _free_port()
    monkeypatch.setattr(stub, "PORTS", {"D": other})
    monkeypatch.setattr(stub, "DEFAULT_PORT", port)
    seen = {}

    def fake_serve(server, **kw):
        seen["addr"] = server.getsockname()

    monkeypatch.setattr(stub, "serve", fake_serve)
    assert stub.main(["--terminal_type=d"]) == 0
    assert seen["addr"] == ("127.0.0.1", port)


def test_main_port_in_use_exits_quietly(monkeypatch):
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        monkeypatch.setattr(stub, "PORTS", {"D": holder.getsockname()[1]})
        monkeypatch.setattr(stub, "serve", lambda server, **kw: pytest.fail("não deveria servir"))
        assert stub.main(["--terminal_type=D"]) == 0
    finally:
        holder.close()


def test_server_stub_has_no_dll_or_ctypes():
    source = open(stub.__file__, encoding="utf-8").read()
    assert "ctypes" not in source and "ehlapi" not in source.lower()


def test_import_api_raises_import_error():
    sys.modules.pop("rumbapy.api", None)
    with pytest.raises(ImportError) as info:
        importlib.import_module("rumbapy.api")
    assert str(info.value) == "rumbapy.api foi removido na v2; use rumbapy.open(...) ou RumbaClient"
    assert "rumbapy.api" not in sys.modules
