"""``main`` em subprocesso (D.1, D.2) e ponta a ponta com o worker real (32-bit) e ``--fake-dll``.

Nenhum processo é morto: todo worker termina por ``shutdown``, evento de parada, idle, watchdog ou erro de boot.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.unit.worker.helpers import (IS_32, WireClient, copy_package, own_python, python32, python64, run_worker,
                                       spawn_worker, worker_argv, worker_env, write_key)
from rumbapy import _buildid, _paths, _protocol
from tests.fakes import scenarios

import time

import pytest

PY32 = python32()
needs32 = pytest.mark.skipif(PY32 is None, reason="Python 32-bit não encontrado (.venv32 ou RUMBAPY_TEST_PY32)")
ANY_PY = own_python()
ALLOW = ["--allow-64"]


def exit_code(argv: list[str], home: Path, *, env: dict[str, str] | None = None, timeout: float = 25) -> int:
    proc = run_worker(argv, home, env=env)
    try:
        return proc.wait(timeout)
    except Exception:
        raise AssertionError("o worker não saiu (deveria falhar no boot)") from None


@pytest.fixture
def home(tmp_path: Path) -> Path:
    write_key(tmp_path / "authkey.bin")
    return tmp_path


# ------------------------------------------------------------------------------------------- códigos de saída
def test_exit_12_python_64_without_allow(home: Path) -> None:
    py64 = python64()
    if py64 is None:
        pytest.skip("Python 64-bit não encontrado")
    argv, _ = worker_argv(py64, home)
    assert exit_code(argv, home) == _protocol.EXIT_PYTHON


def test_exit_13_bad_key(home: Path) -> None:
    bad = home / "curta.bin"
    bad.write_bytes(b"12345")
    argv, _ = worker_argv(ANY_PY, home, keyfile=bad, extra=ALLOW)
    assert exit_code(argv, home) == _protocol.EXIT_KEY


def test_exit_14_build_mismatch(home: Path) -> None:
    argv, _ = worker_argv(ANY_PY, home, expect_build="0" * 16, extra=ALLOW)
    assert exit_code(argv, home) == _protocol.EXIT_BUILD


def test_exit_16_live_without_guard(home: Path) -> None:
    argv, _ = worker_argv(ANY_PY, home, extra=ALLOW)
    assert exit_code(argv, home, env=worker_env(home, RUMBAPY_LIVE="1")) == _protocol.EXIT_LIVE_WITHOUT_GUARD


@needs32
def test_exit_17_sandbox_without_fake_dll(home: Path) -> None:
    argv, meta = worker_argv(PY32, home, fake=False)                  # sem --fake-dll e sem --dll na linha
    assert "--dll" not in argv and not any("ehlapi" in a.lower() for a in argv)
    assert exit_code(argv, home) == _protocol.EXIT_SANDBOX
    assert not meta["registry"].exists()


@pytest.mark.parametrize("letter", ["%", "DD", "d", "", "1"])
def test_exit_1_invalid_letter(letter: str, capsys: pytest.CaptureFixture[str]) -> None:
    from rumbapy._worker import main as worker_main

    argv = ["--letter", letter, "--pipe", "p", "--keyfile", "k", "--registry", "r", "--expect-build", "b"]
    assert worker_main.main(argv) == _protocol.EXIT_UNHANDLED          # falha no argparse, antes de qualquer efeito
    assert "letra de sessão inválida" in capsys.readouterr().err
    assert worker_main.letter_arg("D") == "D"


def test_exit_1_fake_dll_without_sandbox(home: Path) -> None:
    # 17 no cliente = "sandbox com a DLL real" (SandboxViolation): o caso oposto sai com 1 (linha de comando inválida)
    argv, _ = worker_argv(ANY_PY, home, extra=ALLOW)
    env = worker_env(home)
    env["RUMBAPY_SANDBOX"] = "0"
    assert exit_code(argv, home, env=env) == _protocol.EXIT_UNHANDLED


def test_exit_10_dll_does_not_load(home: Path) -> None:
    argv, _ = worker_argv(ANY_PY, home, factory="nao_existe", extra=ALLOW)
    assert exit_code(argv, home) == _protocol.EXIT_DLL


def test_exit_11_second_worker_same_pipe(home: Path) -> None:
    first = spawn_worker(ANY_PY, home, extra=ALLOW)
    try:
        argv, _ = worker_argv(ANY_PY, home, pipe=first.pipe, extra=ALLOW)
        assert exit_code(argv, home) == _protocol.EXIT_PIPE_EXISTS
    finally:
        assert first.stop() == 0


# ------------------------------------------------------------------------------------------- ponta a ponta (32-bit)
@needs32
@pytest.mark.e2e
def test_e2e_logon_flow_and_clean_shutdown(home: Path) -> None:
    worker = spawn_worker(PY32, home)
    try:
        assert worker.reg["state"] == "listening" and worker.reg["protocol"] == 2
        assert set(worker.reg) == set(_protocol.REGISTRY_KEYS)
        with WireClient(worker, script="lote.py") as c:
            assert c.hello["python"].startswith("3.") and c.hello["pid"] == worker.pid
            conn = c.result("connect", {"letter": "D"})
            hwnd = conn["hwnd"]
            assert conn["connected"] and (conn["rows"], conn["cols"]) == (24, 80) and conn["hwnd_pid"] == 4242
            assert c.result("snapshot", {"fields": True}, hwnd)["cursor"] == [22, 56]
            c.result("write", {"row": 22, "col": 56, "text": "C", "secret": False}, hwnd)
            assert c.result("send", {"chunks": ["@E"], "secret": False}, hwnd)["chunks_sent"] == 1
            c.result("write", {"row": 12, "col": 21, "text": scenarios.VALID_USER, "secret": False}, hwnd)
            c.result("write", {"row": 13, "col": 21, "text": scenarios.VALID_PASSWORD, "secret": True}, hwnd)
            shown = c.result("read", {"row": 13, "col": 21, "length": 8}, hwnd)["text"]
            assert scenarios.VALID_PASSWORD not in shown
            c.result("send", {"chunks": ["@E"], "secret": False}, hwnd)
            text = c.result("snapshot", {"fields": False}, hwnd)["text"]
            assert "Signon is complete" in text
            err = c.call("send", {"chunks": ["@E", "x"], "secret": False}, hwnd)["error"]
            assert err["type"] == "ParameterError" and err["delivery"] == "none"
            replaced = c.call("status", {}, hwnd + 1)["error"]
            assert replaced["type"] == "SessionReplaced"
        assert worker.stop() == 0
        assert not worker.registry.exists()
        log = worker.log_text()
        assert scenarios.VALID_PASSWORD not in log and scenarios.VALID_USER not in log
        assert "L=D op=connect" in log and "keys=[ENTER] text_units=0" in log
    finally:
        worker.stop()


@needs32
@pytest.mark.e2e
def test_e2e_lease_end_with_new_build_exits_0(home: Path, tmp_path: Path) -> None:
    pkg = copy_package(tmp_path / "copia")
    worker = spawn_worker(PY32, home, pkgdir=pkg, expect_build=_buildid.compute(pkg))
    try:
        owner = WireClient(worker).open()
        (pkg / "_version.py").write_text((pkg / "_version.py").read_text(encoding="utf-8") + "\n# novo\n",
                                         encoding="utf-8")
        owner.close()
        assert worker.wait(15) == 0
        assert not worker.registry.exists()
        assert "build novo" in worker.log_text()
    finally:
        worker.stop()


@needs32
@pytest.mark.e2e
def test_e2e_idle_exit_5(home: Path) -> None:
    worker = spawn_worker(PY32, home, idle=1.0)
    try:
        assert worker.wait(15) == _protocol.EXIT_IDLE
        assert not worker.registry.exists()
    finally:
        worker.stop()


@needs32
@pytest.mark.e2e
def test_e2e_stop_event_without_owner(home: Path) -> None:
    from rumbapy import _win32

    worker = spawn_worker(PY32, home)
    try:
        handle = _win32.open_event(worker.stop_event, create=False)
        _win32.set_event(handle)
        _win32.close_handle(handle)
        assert worker.wait(15) == 0
    finally:
        worker.stop()


@needs32
@pytest.mark.e2e
def test_e2e_stop_while_dll_is_hung_exits_3(home: Path) -> None:
    worker = spawn_worker(PY32, home, factory="hang__WD_CopyPS")
    try:
        owner = WireClient(worker).open()
        hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
        owner.send("snapshot", {"fields": False}, hwnd)
        time.sleep(0.3)
        owner.close()                                                  # fim do lease; T-dll continua presa
        with WireClient(worker, mode="admin") as admin, WireClient(worker, mode="admin") as other:
            admin.send("shutdown", {"reason": "teste"})
            assert other.wait_event(3) == {"event": "shutting_down", "reason": "shutdown"}   # os demais são avisados
            assert worker.wait(15) == _protocol.EXIT_WATCHDOG
        reg = _paths.read_json(worker.registry)
        assert reg is not None and reg["exit_reason"] == "stop_while_hung"
    finally:
        worker.stop()


@needs32
@pytest.mark.e2e
@pytest.mark.timeout(60)
def test_e2e_watchdog_hung_call(home: Path) -> None:
    """Chamada presa > 20 s: resposta ``WorkerHung``, evento ``shutting_down`` e saída 3 (sem ``TerminateProcess``)."""
    worker = spawn_worker(PY32, home, factory="hang__WD_SendKey")
    try:
        owner = WireClient(worker).open()
        hwnd = owner.result("connect", {"letter": "D"})["hwnd"]
        reply = owner.call("send", {"chunks": ["@T"], "secret": False}, hwnd, timeout=30)
        assert reply["error"]["type"] == "WorkerHung" and reply["error"]["delivery"] == "unknown"
        assert worker.wait(10) == _protocol.EXIT_WATCHDOG
        reg = _paths.read_json(worker.registry)
        assert reg is not None and reg["exit_reason"] == "hung"
        assert "watchdog" in worker.log_text()
    finally:
        worker.stop()


@pytest.mark.skipif(not IS_32, reason="só no job x86")
def test_running_under_32_bit() -> None:
    import struct

    assert struct.calcsize("P") == 4
