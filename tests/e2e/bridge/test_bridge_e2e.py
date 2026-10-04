"""Ponte ponta a ponta (P-D): spawn + handshake + ops pelo ``WorkerBackend``, corrida, sequestro de canal, PID reusado.

Worker real 32-bit com ``--fake-dll tests.e2e.bridge.scenarios_bridge:<fábrica>``; nada aqui carrega a DLL real.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from tests.e2e.bridge.conftest import Env
from rumbapy.errors import ChannelHijacked, KeyboardLocked, RumbaError, UpdateRequired
from rumbapy import _bridge, _paths, _protocol, _win32
from rumbapy.screen import Screen

import subprocess
import threading
import time
import os

import pytest

pytestmark = pytest.mark.e2e


def test_spawn_handshake_and_ops(env: Env) -> None:
    b = env.bridge()
    be = b.get_worker("D", "owner")
    env.remember(be.worker_pid)
    reg = env.wait_registry(lambda r: r.get("state") == "owned")
    assert reg["pid"] == be.worker_pid
    assert reg["owner"]["pid"] == os.getpid()                       # hello_ack com o PID real (A.9)
    assert reg["build_id"] == b.build_id()
    result = be.connect()
    assert result.connected and result.rows == 24 and result.cols == 80
    assert be.hwnd == result.hwnd                                   # bind_instance logo após o connect (A.9)
    screen = be.snapshot()
    assert isinstance(screen, Screen) and "CICS" in screen.text
    read = be.read(1, 1, 80)
    assert len(read.text) == 80 and read.busy is False
    assert be.ping()["owner_pid"] == os.getpid()
    # cache: o 2º get_worker do mesmo processo reaproveita o handle (C.6.6)
    again = b.get_worker("D", "owner")
    assert again.handle is be.handle and be.handle.users == 2
    again.release()
    assert be.handle is not None and be.handle.alive
    be.release()
    assert b.handles == {}
    # depois do release a próxima op retoma o lease no MESMO worker
    assert be.read(1, 1, 5).text is not None and be.worker_pid == reg["pid"]
    be.release()


def test_sessions_and_admin(env: Env) -> None:
    b = env.bridge()
    admin = b.get_worker("D", "admin")
    env.remember(admin.worker_pid)
    letters = [s.letter for s in admin.sessions()]
    assert "D" in letters
    assert env.registry()["owner"] is None                          # admin não toma o lease
    admin.release()


def test_two_clients_race_one_worker(env: Env) -> None:
    results: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            be = env.bridge().get_worker("D", "admin")
            results.append(be.worker_pid)
            be.release()
        except BaseException as exc:                                # noqa: BLE001 - devolvido ao teste
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert len(results) == 2 and results[0] == results[1]          # corrida de 2 clientes -> 1 worker
    env.remember(results[0])
    assert len(env.boot_logs()) == 1


def test_channel_hijacked_without_loop(env: Env) -> None:
    b = env.bridge()
    admin = b.get_worker("D", "admin")
    env.remember(admin.worker_pid)
    admin.release()
    reg = env.registry()
    fake = dict(reg, pid=os.getpid(), created=_win32.process_created(os.getpid()))   # PID vivo, mas não é o servidor
    _paths.write_json(_paths.worker_registry("D"), fake)
    boots = len(env.boot_logs())
    t0 = time.monotonic()
    with pytest.raises(ChannelHijacked) as info:
        env.bridge().get_worker("D", "owner")
    assert time.monotonic() - t0 < 10
    assert str(reg["pid"]) in info.value.message                    # o PID que de fato serve o pipe
    assert len(env.boot_logs()) == boots                            # sem laço, sem spawn
    _paths.write_json(_paths.worker_registry("D"), reg)


def test_pid_reused_is_dead(env: Env) -> None:
    created = _win32.process_created(os.getpid())
    stale = {"protocol": 2, "pid": os.getpid(), "created": (created or 0) + 12345, "pipe": r"\\.\pipe\nao-existe",
             "letter": "D", "owner": {"pid": 1, "script": "velho.py", "since": "x"}, "state": "owned"}
    _paths.ensure_dir(_paths.run_dir())
    _paths.write_json(_paths.worker_registry("D"), stale)
    assert not _bridge.registry_alive(stale)
    be = env.bridge().get_worker("D", "owner")                      # PID reutilizado (created diferente) = morto
    env.remember(be.worker_pid)
    assert be.worker_pid != os.getpid()
    be.release()


def test_ctrl_c_closes_and_next_op_reopens(env: Env) -> None:
    be = env.bridge().get_worker("D", "owner")
    env.remember(be.worker_pid)
    be.connect()
    handle = be.handle
    original = handle.channel.recv_msg
    calls = {"n": 0}

    def interrupted(timeout: float) -> Any:
        calls["n"] += 1
        raise KeyboardInterrupt

    handle.channel.recv_msg = interrupted
    with pytest.raises(KeyboardInterrupt):
        be.read(1, 1, 10)
    handle.channel.recv_msg = original
    assert handle.dead and handle.channel.closed                     # C.7: a conexão foi fechada
    pid = be.worker_pid
    assert be.read(1, 1, 10).text is not None                        # reabre (handshake) e retoma o lease
    assert be.handle is not handle and be.worker_pid == pid
    assert env.wait_registry(lambda r: r.get("owner") is not None)["owner"]["pid"] == os.getpid()
    be.release()


def test_exit_14_build_mismatch(env: Env) -> None:
    def popen(argv: list[str], **kw: Any) -> subprocess.Popen:
        argv = list(argv)
        argv[argv.index("--expect-build") + 1] = "0" * 16
        return subprocess.Popen(argv, **kw)

    with pytest.raises(UpdateRequired):
        env.bridge(popen=popen).get_worker("D", "owner")


def test_exit_13_rereads_key_once(env: Env) -> None:
    bad = env.home / "curta.bin"
    bad.write_bytes(b"123")
    calls: list[list[str]] = []

    def popen(argv: list[str], **kw: Any) -> subprocess.Popen:
        argv = list(argv)
        if not calls:
            argv[argv.index("--keyfile") + 1] = str(bad)
        calls.append(argv)
        return subprocess.Popen(argv, **kw)

    be = env.bridge(popen=popen).get_worker("D", "owner")
    env.remember(be.worker_pid)
    assert len(calls) == 2
    be.release()


def test_exit_11_pipe_exists_retries_once(env: Env) -> None:
    first = env.bridge().get_worker("D", "admin")
    env.remember(first.worker_pid)
    pipe = env.registry()["pipe"]
    first.release()
    os.unlink(_paths.worker_registry("D"))                          # registro sumiu, mas o pipe continua dele
    calls: list[str] = []

    def popen(argv: list[str], **kw: Any) -> subprocess.Popen:
        argv = list(argv)
        if not calls:
            argv[argv.index("--pipe") + 1] = pipe
        calls.append(argv[argv.index("--pipe") + 1])
        return subprocess.Popen(argv, **kw)

    be = env.bridge(popen=popen).get_worker("D", "admin")
    env.remember(be.worker_pid)
    assert len(calls) == 2 and calls[0] == pipe and calls[1] != pipe
    assert be.worker_pid != first.worker_pid
    be.release()


def test_refused_send_is_not_retried(env: Env) -> None:
    be = env.bridge().get_worker("D", "owner")
    env.remember(be.worker_pid)
    be.connect()
    pid = be.worker_pid
    with pytest.raises(RumbaError) as info:
        be.send(["@E" * 70], secret=False)                         # chunk > 128 bytes: recusado antes da DLL
    assert not isinstance(info.value, KeyboardLocked)
    assert be.worker_pid == pid and be.handle.alive                 # nada de reciclar por erro do worker
    be.release()


def test_doctor_probe_pings_live_worker_without_lease(env: Env) -> None:
    from rumbapy import _doctor

    be = env.bridge().get_worker("D", "owner")
    env.remember(be.worker_pid)
    env.wait_registry(lambda r: r.get("owner") is not None)
    info = _bridge.Bridge().probe("D")
    assert info["alive"] and info["owner_pid"] == os.getpid() and info["ping"]["owner_pid"] == os.getpid()
    assert _bridge.Bridge().probe("Z") == {"letter": "Z", "registered": False, "alive": False}
    report = _doctor.collect(probes=_doctor.Probes(letters=("D",), remote_sha=lambda: "a" * 40,
                                                   find_runtime=lambda: None, is_file=lambda p: True))
    assert "D: PID" in report["Processos auxiliares"].detail and "ping ok" in report["Processos auxiliares"].detail
    assert len(env.boot_logs()) == 1                                    # o doctor nunca sobe worker
    be.release()


def test_protocol_constant_sanity() -> None:
    assert _protocol.PROTOCOL == 2 and _bridge.HELLO_TIMEOUT == _protocol.HELLO_ACK_TIMEOUT
