"""Contrato do BOOT (D.1) e da linha de comando/ambiente do worker montados por ``_bridge.spawn``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit.worker import helpers
from rumbapy.errors import SandboxViolation
from rumbapy import _bridge, _paths, _runtime32, _win32
from rumbapy._worker import hllapi

import subprocess
import re

import pytest

SPEC = Path(__file__).resolve().parents[4] / "docs" / "ESPECIFICACAO_V2.md"


def _boot_from_spec() -> str:
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### D.1", 1)[1].split("### D.2", 1)[0]
    match = re.search(r"`BOOT`.*?```python\n(.*?)\n```", section, re.DOTALL)
    assert match, "bloco do BOOT não encontrado na D.1"
    return match.group(1)


def test_boot_is_exactly_the_d1_text() -> None:
    assert _bridge.BOOT == _boot_from_spec()
    assert "\\" not in _bridge.BOOT and "\n" not in _bridge.BOOT          # uma linha, sem barra invertida (E19)


def test_worker_helpers_use_the_bridge_boot() -> None:
    assert helpers.BOOT is _bridge.BOOT


def test_dll_path_matches_worker() -> None:
    assert _bridge.DLL_PATH == hllapi.DLL_PATH


def _config(**kw: Any) -> _bridge.SpawnConfig:
    base = {"python32": "py32.exe", "fake_dll": "tests.fakes.scenarios:cics_login", "fake_path": "C:/repo"}
    base.update(kw)
    return _bridge.SpawnConfig(**base)


def test_worker_argv_with_fake_dll_never_names_the_real_dll() -> None:
    pipe = _paths.pipe_name("D", _paths.new_pipe_rand())
    argv = _bridge.worker_argv("C:/py32/python.exe", "D", pipe, _config(), expect_build="b" * 16, commit=None)
    assert argv[:7] == ["C:/py32/python.exe", "-I", "-S", "-X", "faulthandler", "-c", _bridge.BOOT]
    assert argv[7] == str(_bridge.PKGDIR)
    pairs = dict(zip(argv[8::2], argv[9::2]))
    assert pairs["--letter"] == "D" and pairs["--pipe"] == pipe and pairs["--expect-build"] == "b" * 16
    assert pairs["--keyfile"] == str(_paths.authkey_path())
    assert pairs["--registry"] == str(_paths.worker_registry("D"))
    assert pairs["--stop-event"] == _paths.stop_event_name("D") and pairs["--commit"] == "none"
    assert pairs["--fake-dll"] == "tests.fakes.scenarios:cics_login" and pairs["--fake-path"] == "C:/repo"
    assert "--dll" not in argv and not any("ehlapi" in a.lower() for a in argv)
    assert "--guard" not in argv


def test_worker_argv_real_dll_and_guard() -> None:
    pipe = _paths.pipe_name("Z", _paths.new_pipe_rand())
    argv = _bridge.worker_argv("py.exe", "Z", pipe, _config(fake_dll=None, fake_path=None, guard="readonly"),
                               expect_build="c" * 16, commit="a" * 40)
    pairs = dict(zip(argv[8::2], argv[9::2]))
    assert pairs["--dll"] == _bridge.DLL_PATH and pairs["--guard"] == "readonly" and pairs["--commit"] == "a" * 40
    assert "--fake-dll" not in argv


def test_worker_env_filters_python_and_rumbapy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "x")
    monkeypatch.setenv("PYTHONHOME", "y")
    monkeypatch.setenv("RUMBAPY_GUARD", "readonly")
    monkeypatch.setenv("RUMBAPY_TEST_FAKE_DLL", "a:b")
    monkeypatch.setenv("RUMBAPY_LIVE", "0")
    env = _bridge.worker_env()
    assert not any(k.upper().startswith("PYTHON") for k in env)
    assert {k for k in env if k.startswith("RUMBAPY_")} == {"RUMBAPY_SANDBOX", "RUMBAPY_LIVE", "RUMBAPY_HOME"}
    assert env["RUMBAPY_HOME"] == str(_paths.home())


class _Proc:
    pid = 4321

    def poll(self) -> None:
        return None


def test_spawn_refuses_real_dll_in_sandbox() -> None:
    with pytest.raises(SandboxViolation):
        _bridge.spawn("D", _config(fake_dll=None), popen=lambda *a, **k: pytest.fail("Popen chamado"))


def test_spawn_popen_arguments_and_breakaway_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    rt = _runtime32.Runtime32("C:/venv/python.exe", "C:/real/python.exe", (3, 13, 2))
    monkeypatch.setattr(_runtime32, "find", lambda explicit=None: rt)
    calls: list[dict[str, Any]] = []

    def popen(argv: list[str], **kw: Any) -> _Proc:
        calls.append({"argv": argv, **kw})
        if len(calls) == 1:
            err = OSError(13, "acesso negado")
            err.winerror = 5          # type: ignore[attr-defined]
            raise err
        return _Proc()

    spawned = _bridge.spawn("D", _config(), popen=popen)
    assert len(calls) == 2
    first, second = calls
    full = _win32.CREATE_NO_WINDOW | _win32.CREATE_NEW_PROCESS_GROUP | _win32.CREATE_BREAKAWAY_FROM_JOB
    assert first["creationflags"] == full
    assert second["creationflags"] == full & ~_win32.CREATE_BREAKAWAY_FROM_JOB
    assert second["argv"][0] == "C:/real/python.exe"                    # sempre o executável real (E14)
    assert second["stdin"] is subprocess.DEVNULL and second["stdout"] is subprocess.DEVNULL
    assert second["cwd"] == str(tmp_path) and second["close_fds"] is True
    assert spawned.boot.parent == _paths.boot_logs_dir() and spawned.boot.exists()
    assert spawned.pipe in second["argv"]


def test_exit_codes_mapping(tmp_path: Path) -> None:
    from rumbapy import errors

    boot = tmp_path / "boot.txt"
    boot.write_text("linha 1\nTraceback qualquer\n", encoding="utf-8")
    expect = {10: errors.RumbaNotInstalled, 12: errors.Python32NotFound, 14: errors.UpdateRequired,
              16: errors.WorkerStartFailed, 17: errors.SandboxViolation, 18: errors.WorkerStartFailed,
              1: errors.WorkerStartFailed}
    for code, cls in expect.items():
        exc = _bridge.exit_error(code, "D", boot=boot)
        assert type(exc) is cls, code
    assert "RUMBAPY_LIVE exige --guard" in _bridge.exit_error(16, "D").message
    failed = _bridge.exit_error(1, "D", boot=boot)
    assert "(código 1)" in failed.message and "Traceback qualquer" in failed.message


def _fake_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    rt = _runtime32.Runtime32("C:/venv/python.exe", "C:/real/python.exe", (3, 13, 2))
    monkeypatch.setattr(_runtime32, "find", lambda explicit=None: rt)


def test_spawn_popen_oserror_is_start_failed_without_exit_code(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    from rumbapy.errors import WorkerStartFailed

    _fake_runtime(monkeypatch, tmp_path)

    def popen(argv: list[str], **kw: Any) -> Any:
        raise FileNotFoundError(2, "não achei")

    with pytest.raises(WorkerStartFailed) as info:
        _bridge.spawn("D", _config(), popen=popen)
    msg = info.value.message
    assert msg == ("O processo auxiliar 32-bit do terminal D não iniciou: não consegui executar o Python 32-bit "
                   "(FileNotFoundError, erro 2).")
    assert info.value.details == {"error": "FileNotFoundError", "winerror": 2}


def test_spawn_data_dir_oserror_is_worker_error_not_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from rumbapy.errors import WorkerError

    _fake_runtime(monkeypatch, tmp_path)

    def denied(path: Any) -> Any:
        raise PermissionError(13, "acesso negado")

    monkeypatch.setattr(_bridge._paths, "ensure_dir", denied)
    with pytest.raises(WorkerError) as info:
        _bridge.spawn("D", _config(), popen=lambda *a, **k: pytest.fail("Popen chamado"))
    assert type(info.value) is WorkerError and "pasta de dados" in info.value.message
    assert "Python" not in info.value.message


def test_spawn_prune_failure_does_not_fail_spawn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_runtime(monkeypatch, tmp_path)

    def denied(letter: str) -> Any:
        raise PermissionError(13, "em uso")

    monkeypatch.setattr(_bridge._paths, "prune_boot_logs", denied)
    spawned = _bridge.spawn("D", _config(), popen=lambda *a, **k: _Proc())
    assert spawned.proc.pid == 4321


def test_exit_3_at_boot_follows_exit_reason() -> None:
    from rumbapy import errors

    assert type(_bridge.exit_error(3, "D", reason="window_gone")) is errors.SessionStopped
    assert type(_bridge.exit_error(3, "D", reason="client_table_full")) is errors.ClientTableFull
    hung = _bridge.exit_error(3, "D", reason="stop_while_hung")
    assert type(hung) is errors.WorkerHung and "em 5 s" in hung.message
    assert type(_bridge.exit_error(3, "D")) is errors.WorkerStartFailed          # sem motivo: genérico


def test_hung_limit_follows_d4() -> None:
    assert _bridge.hung_limit("hung", "connect") == ("WD_ConnectPS", 15.0)
    assert _bridge.hung_limit("hung", "read") == (_bridge.HUNG_FUNCTION, 20.0)
    assert _bridge.hung_limit("stop_while_hung", "read") == (_bridge.STOP_FUNCTION, 5.0)
    reg = {"hung_function": "WD_CopyPSToString", "hung_limit": 20}
    assert _bridge.hung_limit("hung", "connect", reg) == ("WD_CopyPSToString", 20.0)
