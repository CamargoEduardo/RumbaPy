"""Servidor do worker em processo (D.3, C.3, C.6): pipe real + ``FakeEhllapi`` + ``exit_fn`` injetado."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rumbapy import _buildid, _ipc, _paths, _protocol, _win32
from rumbapy._worker.server import Config, WorkerServer
from tests.unit.worker.helpers import WireClient
from tests.fakes import scenarios

import threading
import logging
import secrets
import time
import os

import pytest


@dataclass
class Env:
    server: WorkerServer
    fake: Any
    exits: list[int]
    key: bytes
    pipe: str
    build: str
    pkgdir: Path
    registry: Path
    stop_event: str
    peer_pids: list[int]                   # PIDs "do pipe" das próximas conexões (simula outro processo)

    def client(self, mode: str = "owner", **kw: Any) -> WireClient:
        return WireClient(pipe=self.pipe, pid=os.getpid(), key=self.key, mode=mode, build_id=self.build, **kw)

    def wait_exit(self, timeout: float = 10.0) -> int | None:
        deadline = time.monotonic() + timeout
        while not self.exits and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.exits[0] if self.exits else None


@pytest.fixture
def make_env(tmp_path: Path) -> Iterator[Any]:
    envs: list[Env] = []

    def build(fake: Any = None, *, idle: float = 1800.0) -> Env:
        pkgdir = tmp_path / f"pkg{len(envs)}"
        pkgdir.mkdir()
        (pkgdir / "mod.py").write_text("x = 1\n", encoding="utf-8")
        key = secrets.token_bytes(32)
        pipe = _paths.pipe_name("D", _paths.new_pipe_rand())
        security = _win32.pipe_security_attributes()
        first = _ipc.create_pipe(pipe, first=True, security=security)
        stop_event = f"Local\\rumbapy2-test-{secrets.token_hex(4)}-D-stop"
        handle = _win32.open_event(stop_event, create=True)
        registry = tmp_path / f"run{len(envs)}" / "worker.json"
        config = Config(letter="D", pipe=pipe, key=key, registry=registry, build_id=_buildid.compute(pkgdir),
                        pkgdir=str(pkgdir), stop_event=stop_event, idle=idle)
        exits: list[int] = []
        peer_pids: list[int] = []

        def peer_pid(channel: _ipc.Channel) -> int | None:
            return peer_pids.pop(0) if peer_pids else channel.peer_pid(server=True)

        fake = fake if fake is not None else scenarios.cics_login()
        server = WorkerServer(config, dll_loader=lambda: fake, first_pipe=first, security=security,
                              exit_fn=exits.append, is_window=lambda h: True, dialog_visible=lambda p: False,
                              window_pid=lambda h: 4242, peer_pid=peer_pid, stop_event_handle=handle)
        assert server.start_dll() is None
        server.start_service()
        env = Env(server, fake, exits, key, pipe, config.build_id, pkgdir, registry, stop_event, peer_pids)
        envs.append(env)
        return env

    yield build
    for env in envs:
        getattr(env.fake, "hang_event", threading.Event()).set()
        if not env.server.finished.is_set():
            env.server.stop_clean("shutdown")
            env.server.finished.wait(15)


def test_hello_welcome_registry_and_ops(make_env: Any) -> None:
    env = make_env()
    with env.client(script="lote.py") as c:
        hello = c.hello
        assert {k: hello[k] for k in ("t", "protocol", "version", "letter", "state", "owner", "guard")} == {
            "t": "hello", "protocol": 2, "version": _protocol.VERSION, "letter": "D", "state": "listening",
            "owner": None, "guard": "none"}
        assert hello["pid"] == os.getpid() and hello["build_id"] == env.build
        result = c.result("connect", {"letter": "D"})
        assert result["connected"] and result["hwnd"] == 0x1000
        reg = _paths.read_json(env.registry)
        assert reg["state"] == "owned" and reg["connected"] and reg["hwnd"] == 0x1000 and reg["hwnd_pid"] == 4242
        assert reg["owner"]["pid"] == os.getpid() and reg["owner"]["script"] == "lote.py"
        assert set(reg) == set(_protocol.REGISTRY_KEYS) and reg["exit_reason"] is None
        assert c.result("read", {"row": 14, "col": 26, "length": 24}, hwnd=0x1000)["text"] == \
            "Select CICS Environment:"
        bad = c.call("read", {"row": 25, "col": 1, "length": 1}, hwnd=0x1000)
        assert bad["error"]["type"] == "InvalidPosition"                # geometria passada ao validate (A.8 e)
        ping = c.result("ping")
        assert ping["state"] == "owned" and ping["owner_pid"] == os.getpid() and ping["connected"]
    time.sleep(0.2)
    assert _paths.read_json(env.registry)["owner"] is None


def test_ping_is_answered_while_the_dll_is_busy(make_env: Any) -> None:
    env = make_env(scenarios.hang("WD_CopyPS"))
    with env.client() as owner, env.client("admin") as admin:
        hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
        owner.send("snapshot", {"fields": False}, hwnd)                # trava na DLL
        time.sleep(0.2)
        t0 = time.monotonic()
        assert admin.result("ping")["state"] == "owned"
        assert time.monotonic() - t0 < 1.0
        env.fake.hang_event.set()


def test_second_owner_is_rejected_busy(make_env: Any) -> None:
    env = make_env()
    with env.client(script="a.py"):
        env.peer_pids.append(os.getpid() + 1)                          # "outro processo" no pipe
        other = env.client(client_pid=os.getpid() + 1, script="b.py").open(expect_welcome=False)
        assert other.hello["state"] == "owned" and other.hello["owner"]["script"] == "a.py"
        assert other.answer == {"t": "reject", "reason": "busy", "owner": other.hello["owner"]}
        other.close()
        with env.client("admin") as admin:                             # admin entra mesmo com dono
            assert admin.result("ping")["state"] == "owned"


def test_dead_owner_process_does_not_block_a_new_owner(make_env: Any) -> None:
    env = make_env()
    env.peer_pids.append(0x7FFFFFF0)
    ghost = env.client(client_pid=0x7FFFFFF0, script="morto.py").open()   # PID que não existe
    other = env.client(script="novo.py").open()                         # não é rejeitado como busy
    assert other.result("ping")["owner_pid"] == os.getpid()
    ghost.close()
    other.close()


def test_declared_pid_must_match_the_pipe(make_env: Any, caplog: Any) -> None:
    env = make_env()
    with caplog.at_level(logging.WARNING, logger="rumbapy._worker.server"):
        liar = env.client(client_pid=os.getpid() + 1, script="mente.py")
        with pytest.raises((EOFError, BrokenPipeError, ConnectionError, OSError)):
            liar.open(expect_welcome=False)
            assert liar.answer is None
            liar.channel.recv_msg(2.0)
        liar.close()
    assert any("mas o pipe diz" in r.getMessage() for r in caplog.records)
    with env.client() as honest:                                       # o lease segue livre
        assert honest.result("ping")["owner_pid"] == os.getpid()


def test_same_process_reopening_takes_the_lease(make_env: Any) -> None:
    env = make_env()
    first = env.client().open()
    second = env.client().open()                                       # mesmo PID (Ctrl+C e reabriu)
    assert second.result("ping")["owner_pid"] == os.getpid()
    first.close()
    second.close()


def test_unconfirmed_pid_never_takes_over_an_existing_lease(make_env: Any) -> None:
    env = make_env()
    first = env.client().open()
    env.peer_pids.append(None)                                         # type: ignore[arg-type]  (PID ilegível)
    second = env.client().open(expect_welcome=False)                   # declara o MESMO PID do dono
    assert second.answer["t"] == "reject" and second.answer["reason"] == "busy"
    second.close()
    assert first.result("ping")["owner_pid"] == os.getpid()           # o dono segue com o lease
    first.close()
    time.sleep(0.2)
    env.peer_pids.append(None)                                         # type: ignore[arg-type]
    with env.client() as alone:                                        # sem dono: entra mesmo sem o PID do pipe
        assert alone.result("ping")["owner_pid"] == os.getpid()


def test_build_mismatch_is_rejected_for_owner_only(make_env: Any) -> None:
    env = make_env()
    owner = WireClient(pipe=env.pipe, pid=os.getpid(), key=env.key, build_id="0" * 16).open(expect_welcome=False)
    assert owner.answer["reason"] == "build"
    owner.close()
    admin = WireClient(pipe=env.pipe, pid=os.getpid(), key=env.key, mode="admin", build_id="0" * 16).open()
    assert admin.answer == {"t": "welcome", "mode": "admin"}
    admin.close()


def test_evict_kicks_the_owner(make_env: Any) -> None:
    env = make_env()
    owner = env.client(script="dono.py").open()
    hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
    with env.client("admin") as admin:
        result = admin.result("evict", {"reason": "close"})
    assert result == {"evicted_owner": {"pid": os.getpid(), "script": "dono.py"}, "was_connected": True}
    event = owner.wait_event()
    assert event == {"event": "evicted", "reason": "close"}
    reply = owner.call("read", {"row": 1, "col": 1, "length": 3}, hwnd)
    assert reply["error"]["type"] == "SessionReplaced"
    reg = _paths.read_json(env.registry)
    assert reg["owner"] is None and reg["connected"] is False and reg["state"] == "listening"
    owner.close()
    with env.client() as again:                                        # um dono novo pode entrar
        assert again.result("connect", {"letter": "D"})["connected"]


def test_shutdown_with_owner_is_terminal_in_use(make_env: Any) -> None:
    env = make_env()
    with env.client(script="dono.py"):
        with env.client("admin") as admin:
            reply = admin.call("shutdown", {"reason": "teste"})
            assert reply["error"]["type"] == "TerminalInUse" and "dono.py" in reply["error"]["message"]
    assert env.exits == []


def test_shutdown_without_owner_exits_clean(make_env: Any) -> None:
    env = make_env()
    with env.client("admin") as admin:
        assert admin.result("shutdown", {"reason": "teste"}, timeout=15) == {}
        assert admin.events == []                                      # quem pediu recebe {}, não o evento
    assert env.wait_exit() == 0
    assert not env.registry.exists()
    assert env.fake.reset_count == 1                                   # ResetSystem antes de sair


def test_stop_event_is_deferred_while_there_is_an_owner(make_env: Any) -> None:
    env = make_env()
    owner = env.client().open()
    handle = _win32.open_event(env.stop_event, create=False)
    _win32.set_event(handle)
    time.sleep(0.6)
    assert env.exits == [] and owner.result("ping")["state"] == "owned"
    owner.close()
    assert env.wait_exit() == 0
    _win32.close_handle(handle)


def test_eof_cancels_pending_jobs(make_env: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rumbapy")
    env = make_env(scenarios.hang("WD_CopyPS"))
    owner = env.client().open()
    hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
    owner.send("snapshot", {"fields": False}, hwnd)                    # em voo (travado)
    sent = owner.send("send", {"chunks": ["@E"], "secret": False}, hwnd)
    read = owner.send("read", {"row": 1, "col": 1, "length": 3}, hwnd)
    time.sleep(0.2)
    owner.close()
    time.sleep(0.8)
    env.fake.hang_event.set()
    time.sleep(0.5)
    assert not any(c[0] == "WD_SendKey" for c in env.fake.calls)
    assert env.fake.sessions["D"].aid_log == []
    text = caplog.text
    assert f"dropped id={sent} op=send reason=owner_gone" in text
    assert f"dropped id={read} op=read reason=owner_gone" in text


def test_lease_end_with_new_build_exits_0(make_env: Any) -> None:
    env = make_env()
    owner = env.client().open()
    (env.pkgdir / "mod.py").write_text("x = 2\n", encoding="utf-8")   # "update" em disco
    owner.close()
    assert env.wait_exit() == 0
    assert not env.registry.exists()


def test_idle_exit_5(make_env: Any) -> None:
    env = make_env(idle=0.5)
    assert env.wait_exit(10) == 5
    assert not env.registry.exists()
    from rumbapy import _locks
    lock = _locks.file_lock(_paths.spawn_lock_name("D"), timeout=2)     # o worker soltou a trava
    lock.release()


def test_silent_client_does_not_block_a_second_client(make_env: Any) -> None:
    env = make_env()
    mute = _ipc.connect(env.pipe, os.getpid(), letter="D")              # conecta e não autentica
    t0 = time.monotonic()
    with env.client("admin") as admin:
        assert admin.result("ping")["state"] == "listening"
    assert time.monotonic() - t0 < 1.5
    mute.close()


def test_connection_limit(make_env: Any) -> None:
    env = make_env()
    clients = [env.client("admin").open() for _ in range(_protocol.MAX_CONNECTIONS)]
    with pytest.raises((_ipc.AuthRejected, _ipc.PipeTimeout, EOFError, OSError)):
        extra = env.client("admin")
        try:
            extra.open()
        finally:
            extra.close()
    for c in clients:
        c.close()
    time.sleep(0.3)
    with env.client("admin") as again:
        assert again.result("ping")


def test_queue_full(make_env: Any) -> None:
    env = make_env(scenarios.hang("WD_CopyPSToString"))
    with env.client() as owner:
        hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
        ids = [owner.send("read", {"row": 1, "col": 1, "length": 3}, hwnd) for _ in range(_protocol.JOB_QUEUE_MAX + 5)]
        replies = []
        deadline = time.monotonic() + 3
        while len(replies) < 4 and time.monotonic() < deadline:
            msg = owner.channel.recv_msg(0.1)
            if msg is not None and "id" in msg:
                replies.append(msg)
        assert replies and all(r["error"]["message"] == "muitas requisições pendentes" for r in replies)
        assert {r["id"] for r in replies} <= set(ids[-6:])
        env.fake.hang_event.set()
