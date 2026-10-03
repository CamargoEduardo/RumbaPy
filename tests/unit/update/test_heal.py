"""``_bootstrap.heal`` (H.6) com rede e pip falsos; a versão com pacote copiado e SyntaxError real fica no e2e."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess
import types
import time
import sys
import os

import pytest

from tests.e2e.update.support import SHA_A, SHA_B
from rumbapy import _bootstrap, _locks


class Fake:
    def __init__(self) -> None:
        self.remote: str | BaseException = SHA_B
        self.rc = 0
        self.pips: list[str] = []
        self.lock_held: list[bool] = []
        self.markers: list[dict[str, Any] | None] = []
        self.proc = types.SimpleNamespace(pid=os.getpid())

    def remote_master_sha(self) -> str:
        if isinstance(self.remote, BaseException):
            raise self.remote
        return self.remote

    def pip_install(self, sha: str, on_spawn: Any = None) -> int:
        from rumbapy import _locks

        try:                                                     # a trava de update tem que estar com o heal
            _locks.file_lock("update", timeout=0).release()
            self.lock_held.append(False)
        except _locks.LockTimeout:
            self.lock_held.append(True)
        self.pips.append(sha)
        if on_spawn is not None:
            on_spawn(self.proc)
        self.markers.append(_bootstrap.read_json(_bootstrap.in_progress_path()))
        return self.rc


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Fake:
    f = Fake()
    monkeypatch.delenv("RUMBAPY_DEV", raising=False)
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_bootstrap, "remote_master_sha", f.remote_master_sha)
    monkeypatch.setattr(_bootstrap, "pip_install", f.pip_install)
    monkeypatch.setattr(_bootstrap, "_sleep", lambda s: time.sleep(0.01))
    return f


def broken() -> SyntaxError:
    try:
        compile("def x(:\n", "_update.py", "exec")
    except SyntaxError as exc:
        return exc
    raise AssertionError


def test_dev_reraises_original(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> None:
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    exc = broken()
    with pytest.raises(SyntaxError) as info:
        _bootstrap.heal(exc)
    assert info.value is exc
    assert fake.pips == []


def test_reinstalls_master_under_lock(fake: Fake) -> None:
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert str(info.value) == "O RumbaPy foi reparado para bbbbbbb. Rode de novo."
    assert isinstance(info.value.__cause__, SyntaxError)
    assert fake.pips == [SHA_B] and fake.lock_held == [True]
    state = _bootstrap.read_json(_bootstrap.heal_state_path())
    assert state is not None and state["attempts"][-1]["remote"] == SHA_B
    log = _bootstrap.update_log_path().read_text(encoding="utf-8")          # type: ignore[union-attr]
    assert "heal: o import do RumbaPy falhou (SyntaxError" in log and "Traceback" in log


def test_second_failure_same_sha_within_one_hour(fake: Fake) -> None:
    with pytest.raises(RuntimeError):
        _bootstrap.heal(broken())
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert str(info.value) == ("A versão publicada do RumbaPy (bbbbbbb) está com defeito; avise o responsável. "
                               "Detalhes em update.log")
    assert fake.pips == [SHA_B]


def test_old_attempt_or_other_sha_reinstalls(fake: Fake) -> None:
    _bootstrap.write_json(_bootstrap.heal_state_path(), {"attempts": [
        {"remote": SHA_B, "at": time.time() - 3601, "rc": 0}, {"remote": SHA_A, "at": time.time(), "rc": 0}]})
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "reparado" in str(info.value)
    assert fake.pips == [SHA_B]


def test_failed_pip_does_not_block_the_next_attempt(fake: Fake) -> None:
    fake.rc = 1                                                  # rede/proxy caiu durante o download do pip
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "a reinstalação falhou (pip código 1)" in str(info.value)
    fake.rc = 0
    with pytest.raises(RuntimeError) as info:                    # dentro de 1 h: tenta de novo, sem "com defeito"
        _bootstrap.heal(broken())
    assert str(info.value) == "O RumbaPy foi reparado para bbbbbbb. Rode de novo."
    assert fake.pips == [SHA_B, SHA_B]
    with pytest.raises(RuntimeError) as info:                    # agora sim: reinstalou (rc 0) e continua quebrado
        _bootstrap.heal(broken())
    assert "está com defeito" in str(info.value)
    assert fake.pips == [SHA_B, SHA_B]


def test_marker_with_corrupt_created_does_not_crash(fake: Fake) -> None:
    _bootstrap.write_json(_bootstrap.in_progress_path(), {"pid": 0x7FFFFFF0, "created": "x", "remote": SHA_B})
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "reparado" in str(info.value)


def test_process_alive_tolerates_corrupt_created() -> None:
    assert _bootstrap.process_alive(os.getpid(), "x") is True    # type: ignore[arg-type]
    assert _bootstrap.process_alive(os.getpid(), [1]) is True    # type: ignore[arg-type]
    assert _bootstrap.process_alive(0x7FFFFFF0, "x") is False    # type: ignore[arg-type]


def test_remote_unreachable(fake: Fake) -> None:
    fake.remote = _bootstrap.BootstrapError("network", "info/refs: tempo esgotado")
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    msg = str(info.value)
    assert "não consegui consultar a versão publicada (info/refs: tempo esgotado)" in msg
    assert msg.endswith(_bootstrap.RECOVERY_COMMAND)
    assert fake.pips == []


def test_pip_failure_message(fake: Fake) -> None:
    fake.rc = 2
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "a reinstalação falhou (pip código 2)" in str(info.value)


def test_pip_refused_by_sandbox(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(sha: str, on_spawn: Any = None) -> int:
        raise _bootstrap.BootstrapError("sandbox", "pip install sem RUMBAPY_TEST_PIP")

    monkeypatch.setattr(_bootstrap, "pip_install", refuse)
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "pip install sem RUMBAPY_TEST_PIP" in str(info.value)


def test_other_live_process_updating_waits_and_asks_to_rerun(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        _bootstrap.write_json(_bootstrap.in_progress_path(), {
            "pid": child.pid, "created": _bootstrap.process_created(child.pid), "remote": SHA_B, "at": time.time()})
        monkeypatch.setattr(_bootstrap, "HEAL_WAIT", 0.3)
        started = time.monotonic()
        with _locks.file_lock("update", timeout=0), pytest.raises(RuntimeError) as info:
            _bootstrap.heal(broken())                            # o outro processo segura a trava: atualizando
        assert time.monotonic() - started >= 0.25
        assert str(info.value) == ("O RumbaPy está sendo atualizado por outro processo; rode de novo em alguns "
                                   "segundos.")
        assert fake.pips == []
    finally:
        child.kill()
        child.wait(timeout=30)


def test_marker_with_dead_pid_does_not_wait(fake: Fake) -> None:
    _bootstrap.write_json(_bootstrap.in_progress_path(), {"pid": 0x7FFFFFF0, "created": 1, "remote": SHA_B})
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "reparado" in str(info.value)


def test_own_pid_marker_does_not_wait(fake: Fake) -> None:
    _bootstrap.write_json(_bootstrap.in_progress_path(), {"pid": os.getpid(), "remote": SHA_B})
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert "reparado" in str(info.value)


def test_owner_alive_but_idle_does_not_wait(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:                                                         # ex.: kernel que levou Ctrl+C depois do pip
        _bootstrap.write_json(_bootstrap.in_progress_path(), {
            "pid": child.pid, "created": _bootstrap.process_created(child.pid), "remote": SHA_B, "at": time.time()})
        monkeypatch.setattr(_bootstrap, "HEAL_WAIT", 5.0)
        started = time.monotonic()
        with pytest.raises(RuntimeError) as info:
            _bootstrap.heal(broken())
        assert time.monotonic() - started < 4.0
        assert "reparado" in str(info.value) and fake.pips == [SHA_B]
    finally:
        child.kill()
        child.wait(timeout=30)


def test_orphan_pip_alive_waits_even_with_own_pid(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:                                                         # Ctrl+C duplo: o pip ficou rodando sozinho
        _bootstrap.write_json(_bootstrap.in_progress_path(), {
            "pid": os.getpid(), "created": _bootstrap.process_created(os.getpid()), "remote": SHA_B,
            "pip_pid": child.pid, "pip_created": _bootstrap.process_created(child.pid)})
        monkeypatch.setattr(_bootstrap, "HEAL_WAIT", 0.3)
        with pytest.raises(RuntimeError) as info:
            _bootstrap.heal(broken())
        assert "sendo atualizado por outro processo" in str(info.value)
        assert fake.pips == []
    finally:
        child.kill()
        child.wait(timeout=30)


def test_orphan_pip_born_after_the_wait_is_rechecked_under_the_lock(fake: Fake,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:                                                         # o dono morreu entre a espera e a trava

        def remote_then_orphan() -> str:
            _bootstrap.write_json(_bootstrap.in_progress_path(), {
                "pid": 0x7FFFFFF0, "created": 1, "remote": SHA_B,
                "pip_pid": child.pid, "pip_created": _bootstrap.process_created(child.pid)})
            return SHA_B

        monkeypatch.setattr(_bootstrap, "remote_master_sha", remote_then_orphan)
        with pytest.raises(RuntimeError) as info:
            _bootstrap.heal(broken())
        assert str(info.value) == _bootstrap.HEAL_BUSY
        assert fake.pips == []
        marker = _bootstrap.read_json(_bootstrap.in_progress_path())
        assert marker is not None and marker["pip_pid"] == child.pid          # o marcador do pip órfão fica
        _locks.file_lock("update", timeout=0).release()                      # a trava foi solta
    finally:
        child.kill()
        child.wait(timeout=30)


def test_heal_writes_marker_with_pip_pid_and_removes_it(fake: Fake) -> None:
    with pytest.raises(RuntimeError):
        _bootstrap.heal(broken())
    marker = fake.markers[0]
    assert marker is not None and marker["pid"] == os.getpid() and marker["remote"] == SHA_B
    assert marker["pip_pid"] == os.getpid() and marker["pip_created"] == _bootstrap.process_created(os.getpid())
    assert not _bootstrap.in_progress_path().exists()                       # type: ignore[union-attr]


def test_heal_keyboard_interrupt_keeps_marker(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(sha: str, on_spawn: Any = None) -> int:
        on_spawn(fake.proc)
        raise KeyboardInterrupt

    monkeypatch.setattr(_bootstrap, "pip_install", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _bootstrap.heal(broken())
    marker = _bootstrap.read_json(_bootstrap.in_progress_path())
    assert marker is not None and marker["pip_pid"] == os.getpid()
    _locks.file_lock("update", timeout=0).release()                         # a trava foi solta


def test_heal_state_write_failure_still_reports_repair(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    original = _bootstrap.write_json

    def write_json(path: Any, obj: Any) -> None:
        if path is not None and Path(path).name == "heal-state.json":
            raise PermissionError(13, "antivírus")
        original(path, obj)

    monkeypatch.setattr(_bootstrap, "write_json", write_json)
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert str(info.value) == "O RumbaPy foi reparado para bbbbbbb. Rode de novo."
    log = _bootstrap.update_log_path().read_text(encoding="utf-8")          # type: ignore[union-attr]
    assert "não consegui gravar heal-state.json (PermissionError)" in log


@pytest.mark.parametrize(("module", "ps1", "action"), [
    ("ipykernel", False, "Reinicie o kernel (Kernel → Restart) e rode a célula de novo."),
    ("IPython", False, "Reinicie o kernel (Kernel → Restart) e rode a célula de novo."),
    (None, True, "Feche este Python e abra de novo."),
    ("idlelib", False, "Feche este Python e abra de novo."),
])
def test_repair_message_by_context(fake: Fake, monkeypatch: pytest.MonkeyPatch, module: str | None, ps1: bool,
                                   action: str) -> None:
    for name in ("ipykernel", "IPython", "idlelib"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    if module:
        monkeypatch.setitem(sys.modules, module, types.ModuleType(module))
    if ps1:
        monkeypatch.setattr(sys, "ps1", ">>> ", raising=False)
    with pytest.raises(RuntimeError) as info:
        _bootstrap.heal(broken())
    assert str(info.value) == f"O RumbaPy foi reparado para bbbbbbb. {action}"
