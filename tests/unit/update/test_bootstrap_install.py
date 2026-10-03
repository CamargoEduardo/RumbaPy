"""Primitivas de instalação do ``_bootstrap`` (H.4, H.6): argv exato do pip, pip falso, sandbox, ``--user``,
breakaway, sonda em interpretador novo, processos, trava e ``say``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess
import json
import sys
import io
import os

import pytest

from tests.e2e.update.support import FAKE_PIP, SHA_A, SHA_B
from rumbapy import _bootstrap, _locks


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_bootstrap, "_needs_user", lambda: False)
    return tmp_path / "home"


# ------------------------------------------------------------------------------------------- argv e pip falso

def test_pip_argv_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_TEST_GITHUB", raising=False)
    monkeypatch.setattr(_bootstrap, "_needs_user", lambda: False)
    assert _bootstrap.pip_argv(SHA_B) == [
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--force-reinstall",
        "--no-deps", f"https://github.com/CamargoEduardo/RumbaPy/archive/{SHA_B}.zip"]
    monkeypatch.setattr(_bootstrap, "_needs_user", lambda: True)
    assert _bootstrap.pip_argv(SHA_B)[-1] == "--user"


@pytest.mark.parametrize(("venv", "user_site", "writable", "expected"), [
    (True, True, False, False),
    (False, False, False, False),
    (False, True, True, False),
    (False, True, False, True),
])
def test_needs_user(monkeypatch: pytest.MonkeyPatch, venv: bool, user_site: bool, writable: bool,
                    expected: bool) -> None:
    monkeypatch.setattr(sys, "prefix", "C:/venv" if venv else sys.base_prefix)
    monkeypatch.setattr(_bootstrap.site, "ENABLE_USER_SITE", user_site)
    monkeypatch.setattr(_bootstrap, "_writable", lambda d: writable)
    assert _bootstrap._needs_user() is expected


def test_writable_probe(tmp_path: Path) -> None:
    assert _bootstrap._writable(str(tmp_path)) is True
    assert list(tmp_path.iterdir()) == []
    assert _bootstrap._writable(str(tmp_path / "nao-existe")) is False


def test_pip_install_runs_substitute_with_exact_args(home: Path, monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path: Path) -> None:
    log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.setenv("FAKE_PIP_LOG", str(log))
    monkeypatch.setenv("FAKE_PIP_MODE", "noop")
    assert _bootstrap.pip_install(SHA_B) == 0
    recorded = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert recorded == [_bootstrap.pip_argv(SHA_B)[3:]]
    assert recorded[0][0] == "install"
    pip_logs = list((home / "logs").glob("pip-*.log"))
    assert len(pip_logs) == 1
    update_log = (home / "logs" / "update.log").read_text(encoding="utf-8")
    assert "RUMBAPY_TEST_PIP" in update_log and "código 0" in update_log


def test_pip_install_returns_rc(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.setenv("FAKE_PIP_RC", "3")
    assert _bootstrap.pip_install(SHA_A) == 3


def test_pip_missing_hint(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                          capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "nopip.py"
    script.write_text("import sys\nprint('No module named pip', file=sys.stderr)\nsys.exit(1)\n", encoding="utf-8")
    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(script))
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    assert _bootstrap.pip_install(SHA_A) == 1
    err = capsys.readouterr().err
    assert "RumbaPy: atualizando para aaaaaaa…" in err
    assert "python -m ensurepip --upgrade" in err


def test_pip_sandbox_refusals(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_TEST_PIP", raising=False)
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.pip_install(SHA_A)
    assert info.value.kind == "sandbox"
    monkeypatch.setenv("RUMBAPY_TEST_PIP", "pip")
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)          # "fora de venv"
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.pip_install(SHA_A)
    assert info.value.kind == "sandbox" and "venv" in info.value.reason


def test_pip_install_rejects_bad_sha(home: Path) -> None:
    with pytest.raises(ValueError):
        _bootstrap.pip_install("HEAD")


def test_spawn_detached_flags_and_breakaway_fallback(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    seen: list[int] = []

    class Proc:
        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(argv: list[str], **kwargs: Any) -> Proc:
        seen.append(kwargs["creationflags"])
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["close_fds"] is True
        if kwargs["creationflags"] & _bootstrap.CREATE_BREAKAWAY_FROM_JOB:
            exc = OSError("acesso negado")
            exc.winerror = 5  # type: ignore[attr-defined]
            raise exc
        return Proc()

    monkeypatch.setattr(_bootstrap.subprocess, "Popen", fake_popen)
    _bootstrap._spawn_detached(["x"], None, None)
    base = _bootstrap.CREATE_NO_WINDOW | _bootstrap.CREATE_NEW_PROCESS_GROUP
    assert seen == [base | _bootstrap.CREATE_BREAKAWAY_FROM_JOB, base]


def test_pip_slow_notice_and_no_kill(home: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    waits = iter([subprocess.TimeoutExpired("pip", 1), subprocess.TimeoutExpired("pip", 1), 0])

    class Proc:
        killed = False

        def wait(self, timeout: float | None = None) -> int:
            item = next(waits)
            if isinstance(item, BaseException):
                raise item
            return item

        def kill(self) -> None:
            Proc.killed = True

    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    monkeypatch.setattr(_bootstrap, "_spawn_detached", lambda argv, out, cwd: Proc())
    monkeypatch.setattr(_bootstrap, "PIP_SLOW_NOTICE", 0.0)
    assert _bootstrap.pip_install(SHA_A) == 0
    assert capsys.readouterr().err.count("a atualização está demorando") == 1
    assert Proc.killed is False


class ScriptedProc:
    """``Popen`` falso: cada ``wait`` consome um item (exceção = levanta; int = código de saída)."""

    def __init__(self, *items: Any) -> None:
        self.items = list(items)
        self.waits = 0
        self.killed = False
        self.pid = os.getpid()

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def kill(self) -> None:
        self.killed = True


def test_ctrl_c_waits_for_pip_then_reraises(home: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    proc = ScriptedProc(KeyboardInterrupt(), subprocess.TimeoutExpired("pip", 1), 0)
    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    monkeypatch.setattr(_bootstrap, "_spawn_detached", lambda argv, out, cwd: proc)
    with pytest.raises(KeyboardInterrupt):
        _bootstrap.pip_install(SHA_A)
    assert proc.waits == 3 and proc.items == [] and proc.killed is False    # esperou o pip acabar
    assert "aguarde: o pip está terminando a atualização" in capsys.readouterr().err
    log = (home / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Ctrl+C durante a instalação" in log and "pip: código 0" in log


def test_second_ctrl_c_leaves_pip_running(home: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    proc = ScriptedProc(KeyboardInterrupt(), KeyboardInterrupt(), 0)
    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    monkeypatch.setattr(_bootstrap, "_spawn_detached", lambda argv, out, cwd: proc)
    with pytest.raises(KeyboardInterrupt):
        _bootstrap.pip_install(SHA_A)
    assert proc.waits == 2 and proc.killed is False
    assert "o pip continua rodando em segundo plano" in capsys.readouterr().err


def test_on_spawn_receives_proc_and_its_errors_are_only_logged(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = ScriptedProc(0)
    seen: list[Any] = []

    def on_spawn(p: Any) -> None:
        seen.append(p)
        raise PermissionError(13, "antivírus")

    monkeypatch.setenv("RUMBAPY_TEST_PIP", str(FAKE_PIP))
    monkeypatch.setattr(_bootstrap, "_spawn_detached", lambda argv, out, cwd: proc)
    assert _bootstrap.pip_install(SHA_A, on_spawn=on_spawn) == 0
    assert seen == [proc]
    assert "on_spawn falhou (PermissionError" in (home / "logs" / "update.log").read_text(encoding="utf-8")


def test_marker_record_and_busy(home: Path) -> None:
    me = os.getpid()
    record = _bootstrap.marker_record(SHA_B)
    assert record["pid"] == me and record["created"] == _bootstrap.process_created(me) and "pip_pid" not in record
    with_pip = _bootstrap.marker_record(SHA_B, ScriptedProc())
    assert with_pip["pip_pid"] == me and with_pip["pip_created"] == _bootstrap.process_created(me)
    assert _bootstrap.update_busy(None) is False
    assert _bootstrap.update_busy(record) is False                   # dono = este processo, sem pip
    assert _bootstrap.update_busy(with_pip) is True                  # pip vivo, mesmo com o dono = este processo
    assert _bootstrap.pip_alive(with_pip) == me
    assert _bootstrap.update_busy({"pid": 0x7FFFFFF0, "pip_pid": 0x7FFFFFF1}) is False


def test_live_owner_counts_only_while_holding_the_lock(home: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        marker = {"pid": child.pid, "created": _bootstrap.process_created(child.pid), "remote": SHA_B}
        assert _bootstrap.update_busy(marker) is False               # dono vivo e ocioso: marcador velho
        with _locks.file_lock("update", timeout=0):
            assert _bootstrap.update_busy(marker) is True            # dono vivo e com a trava: atualizando
    finally:
        child.kill()
        child.wait(timeout=30)


# ------------------------------------------------------------------------------------------- sonda e direct_url

def test_fresh_installed_runs_new_interpreter_without_importing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(_bootstrap.subprocess, "run", spy)
    info = _bootstrap.fresh_installed()
    assert calls[0][:3] == [sys.executable, "-E", "-c"]
    assert "import rumbapy" not in calls[0][3] and "find_spec('rumbapy')" in calls[0][3]
    assert set(info) >= {"file", "url", "commit", "version"}
    assert info["commit"] is None                                # este .venv não tem o rumbapy instalado


def test_fresh_installed_handles_garbage(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr(_bootstrap.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"nada de json", b""))
    assert _bootstrap.fresh_installed()["error"].startswith("sonda")


@pytest.mark.parametrize(("direct_url", "commit"), [
    ({"url": f"https://github.com/o/r/archive/{SHA_A}.zip"}, SHA_A),
    ({"url": f"https://codeload.github.com/o/r/zip/{SHA_B}"}, SHA_B),
    ({"url": "https://x/r.git", "vcs_info": {"vcs": "git", "commit_id": SHA_B}}, SHA_B),
    ({"url": "https://x/r.git", "vcs_info": {"vcs": "hg", "commit_id": SHA_B}}, None),
    ({"url": "file:///c:/x", "dir_info": {"editable": True}}, None),
    ({"url": "https://github.com/o/r/archive/refs/heads/master.zip"}, None),
    (None, None),
    ("texto", None),
])
def test_commit_from_direct_url(direct_url: Any, commit: str | None) -> None:
    assert _bootstrap.commit_from_direct_url(direct_url) == commit


# ------------------------------------------------------------------------------------------- processos e trava

def test_process_alive() -> None:
    me = os.getpid()
    assert _bootstrap.process_alive(me) is True
    created = _bootstrap.process_created(me)
    assert isinstance(created, int) and created > 0
    assert _bootstrap.process_alive(me, created) is True
    assert _bootstrap.process_alive(me, created + 1) is False   # PID reaproveitado
    assert _bootstrap.process_alive(0x7FFFFFF0) is False
    assert _bootstrap.process_alive(None) is False  # type: ignore[arg-type]
    assert _bootstrap.process_alive(-1) is False


def test_dead_child_is_not_alive() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert _bootstrap.process_alive(child.pid) is False


def test_update_lock_is_the_same_as_locks(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _bootstrap.update_lock(timeout=0):
        with pytest.raises(_locks.LockTimeout):
            _locks.file_lock("update", timeout=0)
    with _locks.file_lock("update", timeout=0):
        with pytest.raises(_bootstrap.BootstrapError) as info:
            with _bootstrap.update_lock(timeout=0.2):
                pass
    assert info.value.kind == "lock"


# ------------------------------------------------------------------------------------------- say e log

def test_say_tolerates_missing_or_broken_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    monkeypatch.setattr(sys, "stderr", None)
    _bootstrap.say("nada acontece")

    class Broken(io.StringIO):
        def write(self, s: str) -> int:
            raise OSError("fechado")

    monkeypatch.setattr(sys, "stderr", Broken())
    _bootstrap.say("nada acontece")
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    _bootstrap.say("nada acontece")


def test_say_respects_quiet(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("RUMBAPY_QUIET", "1")
    _bootstrap.say("silêncio")
    monkeypatch.delenv("RUMBAPY_QUIET")
    _bootstrap.say("olá")
    assert capsys.readouterr().err == "olá\n"


def test_log_line_format(home: Path) -> None:
    _bootstrap.log_line("update context=import result=up-to-date")
    line = (home / "logs" / "update.log").read_text(encoding="utf-8").splitlines()[-1]
    assert line[23:] == " INFO  update context=import result=up-to-date"
    assert line[4] == "-" and line[19] == "."


def test_log_line_without_home_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_HOME")
    assert _bootstrap.update_log_path() is None
    _bootstrap.log_line("nada")


def test_version_read_without_import() -> None:
    from rumbapy._version import __version__

    assert _bootstrap.version() == __version__


def test_constants_match_version_module() -> None:
    from rumbapy import _version

    assert _bootstrap.RECOVERY_COMMAND == _version.RECOVERY_COMMAND
    assert f"{_bootstrap.GITHUB_ROOT}/{_bootstrap.REPO_OWNER}/{_bootstrap.REPO_NAME}" == _version.REPO_URL
    assert _bootstrap.MASTER_REF == _version.MASTER_REF


def test_paths_match_the_paths_module(home: Path) -> None:
    from rumbapy import _paths

    assert _bootstrap.home() == _paths.home()
    assert _bootstrap.update_log_path() == _paths.update_log_path()
    assert _bootstrap.heal_state_path() == _paths.heal_state_path()
    assert _bootstrap.in_progress_path() == _paths.update_in_progress_path()
    assert _bootstrap._home_file("locks", "update.lock") == _paths.lock_path(_paths.UPDATE_LOCK)


def test_home_relative_and_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUMBAPY_HOME", "rel")
    assert _bootstrap.home() == tmp_path / "rel"
    monkeypatch.delenv("RUMBAPY_HOME")
    assert _bootstrap.home() is None                             # sandbox sem HOME: nada é gravado
