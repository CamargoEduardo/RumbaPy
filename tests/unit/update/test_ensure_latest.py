"""Algoritmo ``ensure_latest`` (H.1, H.4) com rede/pip/sonda falsos e ``RUMBAPY_HOME`` próprio por teste."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import subprocess
import types
import json
import time
import sys
import os

import pytest

from rumbapy.errors import SandboxViolation, UpdateCheckFailed, UpdateFailed, UpdateRequired
from rumbapy import _bootstrap, _update, _locks, _paths, _log
from tests.e2e.update.support import SHA_A, SHA_B

DEAD_PID = 0x7FFFFFF0


@dataclass
class World:
    """Estado falso: o que está instalado, o que o GitHub diz e o que o pip faz."""
    mode: str = "archive"
    installed: str | None = SHA_A
    remote: str | BaseException = SHA_A
    pip_rc: int = 0
    pip_installs: bool = True
    calls: list[str] = field(default_factory=list)
    pip_shas: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def install_mode(self) -> tuple[str, str | None]:
        self.calls.append("install_mode")
        return (self.mode, self.installed if self.mode in ("archive", "vcs") else None)

    def remote_master_sha(self) -> str:
        self.calls.append("remote")
        if isinstance(self.remote, BaseException):
            raise self.remote
        return self.remote

    def fresh_installed(self) -> dict[str, Any]:
        self.calls.append("fresh")
        return {"file": "x", "url": None, "commit": self.installed, "version": "2.0.1"}

    def pip_install(self, sha: str, on_spawn: Any = None) -> int:
        self.calls.append("pip")
        self.pip_shas.append(sha)
        if self.pip_rc == 0 and self.pip_installs:
            self.installed = sha
        return self.pip_rc


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    w = World()
    monkeypatch.delenv("RUMBAPY_DEV", raising=False)
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "orig_argv", ["python", "script.py"])
    for name in ("_restart_required", "_restart_message", "_restart_version", "_checked_at", "_seen_local"):
        monkeypatch.setattr(_update, name, None)
    monkeypatch.setattr(_update, "install_mode", w.install_mode)
    monkeypatch.setattr(_bootstrap, "remote_master_sha", w.remote_master_sha)
    monkeypatch.setattr(_bootstrap, "fresh_installed", w.fresh_installed)
    monkeypatch.setattr(_bootstrap, "pip_install", w.pip_install)
    monkeypatch.setattr(_log, "notice", w.notices.append)
    for mod in ("ipykernel", "IPython", "idlelib"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    if hasattr(sys, "ps1"):
        monkeypatch.delattr(sys, "ps1")
    return w


def state() -> dict[str, Any] | None:
    return _paths.read_json(_paths.update_state_path())


# ------------------------------------------------------------------------------------------- quando não roda

def test_legacy_shim_never_updates(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "rumbapy.server", "--terminal_type", "D"])
    world.remote = SHA_B
    _update.ensure_latest("import")
    _update.ensure_latest("open", force=True)
    assert world.calls == []


def test_cli_import_is_deferred_to_main(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "rumbapy", "doctor"])
    world.remote = SHA_B
    _update.ensure_latest("import")
    assert world.calls == []
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("cli")


def test_dev_mode_returns_and_marks(world: World) -> None:
    world.mode = "dev"
    _update.ensure_latest("import")
    _update.ensure_latest("open")
    assert world.calls == ["install_mode"]


def test_unknown_context() -> None:
    with pytest.raises(ValueError):
        _update.ensure_latest("worker")


# ------------------------------------------------------------------------------------------- igual

def test_up_to_date_no_pip(world: World) -> None:
    _update.ensure_latest("import")
    assert world.calls == ["install_mode", "remote"]
    st = state()
    assert st is not None and st["result"] == "up-to-date" and st["remote"] == st["local"] == SHA_A
    _update.ensure_latest("import")                              # já checado neste processo
    _update.ensure_latest("open")                                # TTL ainda não venceu
    assert world.calls == ["install_mode", "remote"]
    log = (_paths.update_log_path()).read_text(encoding="utf-8")
    assert "INFO  update context=import mode=archive local=aaaaaaa remote=aaaaaaa result=up-to-date" in log


def test_cache_dispenses_query_in_a_new_process(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _update.ensure_latest("import")
    monkeypatch.setattr(_update, "_checked_at", None)            # "outro processo"
    world.calls.clear()
    _update.ensure_latest("import")
    assert world.calls == ["install_mode"]


@pytest.mark.parametrize("override", [
    {"result": "failed"},
    {"remote": SHA_B},
    {"local": SHA_B},
    {"checked_at": time.time() - 61},
    {"checked_at": time.time() + 3600},
])
def test_cache_never_dispenses_when_invalid(world: World, monkeypatch: pytest.MonkeyPatch,
                                            override: dict[str, Any]) -> None:
    base = {"checked_at": time.time(), "remote": SHA_A, "local": SHA_A, "result": "up-to-date"}
    _paths.write_json(_paths.update_state_path(), {**base, **override})
    _update.ensure_latest("import")
    assert "remote" in world.calls


def test_force_ignores_cache_and_process_mark(world: World) -> None:
    _update.ensure_latest("import")
    _update.ensure_latest("legacy", force=True)
    assert world.calls.count("remote") == 2


# ------------------------------------------------------------------------------------------- diferente

def test_different_installs_and_requires_restart(world: World) -> None:
    world.remote = SHA_B
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("import")
    assert world.calls == ["install_mode", "remote", "fresh", "pip", "fresh"]
    assert world.pip_shas == [SHA_B]
    assert info.value.message == "O RumbaPy foi atualizado para 2.0.1 (bbbbbbb). Rode o script de novo."
    st = state()
    assert st is not None and st["result"] == "updated" and st["remote"] == st["local"] == SHA_B
    assert not _paths.update_in_progress_path().exists()
    assert _update.restart_info() == (SHA_B, "2.0.1")
    log = _paths.update_log_path().read_text(encoding="utf-8")
    assert "update context=import mode=archive local=aaaaaaa remote=bbbbbbb result=updated pip_s=" in log
    # depois: sempre a mesma mensagem, mesmo com force, sem nova consulta
    world.calls.clear()
    for ctx, force in (("import", False), ("open", False), ("legacy", True)):
        with pytest.raises(UpdateRequired) as again:
            _update.ensure_latest(ctx, force=force)
        assert again.value.message == info.value.message
    assert world.calls == []


@pytest.mark.parametrize(("setup", "action"), [
    ("script", "Rode o script de novo."),
    ("jupyter", "Reinicie o kernel (Kernel → Restart) e rode a célula de novo."),
    ("ipython", "Reinicie o kernel (Kernel → Restart) e rode a célula de novo."),
    ("repl", "Feche este Python e abra de novo."),
    ("idle", "Feche este Python e abra de novo."),
])
def test_message_by_environment(world: World, monkeypatch: pytest.MonkeyPatch, setup: str, action: str) -> None:
    if setup == "jupyter":
        monkeypatch.setitem(sys.modules, "ipykernel", type(sys)("ipykernel"))
    elif setup == "ipython":
        monkeypatch.setitem(sys.modules, "IPython", type(sys)("IPython"))
    elif setup == "repl":
        monkeypatch.setattr(sys, "ps1", ">>> ", raising=False)
    elif setup == "idle":
        monkeypatch.setitem(sys.modules, "idlelib", type(sys)("idlelib"))
    world.remote = SHA_B
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("import")
    assert info.value.message.endswith(action)


@pytest.mark.parametrize("context", ["open", "legacy"])
def test_message_open_and_legacy(world: World, context: str) -> None:
    world.remote = SHA_B
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest(context, force=context == "legacy")
    assert info.value.message.endswith("Este processo ainda usa a versão antiga; reinicie-o.")


def test_other_process_already_installed(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.remote = SHA_B
    calls = iter([SHA_B, SHA_B])
    monkeypatch.setattr(_bootstrap, "fresh_installed",
                        lambda: {"commit": next(calls), "version": "2.0.1", "file": None, "url": None})
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    assert world.pip_shas == []                                  # já estava instalado por outro processo


def test_interrupted_update_forces_reinstall(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.remote = SHA_B
    _paths.write_json(_paths.update_in_progress_path(), {"pid": DEAD_PID, "created": 1, "remote": SHA_B, "at": 1})
    monkeypatch.setattr(_bootstrap, "fresh_installed",
                        lambda: {"commit": SHA_B, "version": "2.0.1", "file": None, "url": None})
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    assert world.pip_shas == [SHA_B]
    assert not _paths.update_in_progress_path().exists()


def test_marker_with_corrupt_created_is_interrupted(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.remote = SHA_B
    _paths.write_json(_paths.update_in_progress_path(), {"pid": DEAD_PID, "created": "x", "remote": SHA_B})
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    assert world.pip_shas == [SHA_B]


@pytest.mark.parametrize("exc", [PermissionError(13, "antivírus"), OSError(28, "disco cheio")])
def test_marker_write_error_is_update_failed(world: World, monkeypatch: pytest.MonkeyPatch, exc: OSError) -> None:
    world.remote = SHA_B
    original = _paths.write_json

    def write_json(path: Any, obj: Any) -> None:
        if Path(path).name == "update-in-progress.json":
            raise exc
        original(path, obj)

    monkeypatch.setattr(_paths, "write_json", write_json)
    with pytest.raises(UpdateFailed) as info:
        _update.ensure_latest("import")
    assert f"não consegui gravar update-in-progress.json ({type(exc).__name__})" in info.value.message
    assert world.pip_shas == []
    assert state()["result"] == "failed"                        # type: ignore[index]


def test_marker_written_during_pip(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any] | None] = []
    original = world.pip_install

    def pip(sha: str, on_spawn: Any = None) -> int:
        seen.append(_paths.read_json(_paths.update_in_progress_path()))
        return original(sha)

    monkeypatch.setattr(_bootstrap, "pip_install", pip)
    world.remote = SHA_B
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    marker = seen[0]
    assert marker is not None and marker["pid"] == os.getpid() and marker["remote"] == SHA_B
    assert marker["created"] == _bootstrap.process_created(os.getpid())


def test_pip_failure(world: World) -> None:
    world.remote = SHA_B
    world.pip_rc = 1
    with pytest.raises(UpdateFailed) as info:
        _update.ensure_latest("import")
    assert info.value.message.startswith("A atualização do RumbaPy falhou (pip código 1). Detalhes em ")
    assert state()["result"] == "failed"                        # type: ignore[index]
    assert not _paths.update_in_progress_path().exists()
    assert _update._restart_required is None
    world.calls.clear()
    with pytest.raises(UpdateFailed):
        _update.ensure_latest("import")                          # nada foi marcado: tenta de novo
    assert "remote" in world.calls


def test_verification_in_fresh_interpreter_fails(world: World) -> None:
    world.remote = SHA_B
    world.pip_installs = False                                   # pip "ok", mas nada mudou (ex.: --user errado)
    with pytest.raises(UpdateFailed) as info:
        _update.ensure_latest("import")
    assert "após instalar, o commit ainda é aaaaaaa" in info.value.message


def test_probe_failure_is_part_of_the_reason(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.remote = SHA_B

    def broken_probe() -> dict[str, Any]:
        world.calls.append("fresh")
        return {"file": None, "url": None, "commit": None, "version": None, "error": "TimeoutExpired"}

    monkeypatch.setattr(_bootstrap, "fresh_installed", broken_probe)
    with pytest.raises(UpdateFailed) as info:
        _update.ensure_latest("import")
    assert "após instalar, o commit ainda é none (sonda: TimeoutExpired)" in info.value.message
    assert "sonda: TimeoutExpired" in state()["reason"]           # type: ignore[index]


@pytest.mark.parametrize("how", ["ps1", "flag", "idlelib"])
def test_python_i_and_idle_use_the_interactive_ttl(world: World, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    if how == "ps1":
        monkeypatch.setattr(sys, "ps1", ">>> ", raising=False)
    elif how == "flag":
        flags = {name: getattr(sys.flags, name) for name in dir(sys.flags) if not name.startswith(("_", "n_"))
                 and not callable(getattr(sys.flags, name))}
        monkeypatch.setattr(sys, "flags", types.SimpleNamespace(**{**flags, "interactive": 1}))   # python -i
    else:
        monkeypatch.setitem(sys.modules, "idlelib", types.ModuleType("idlelib"))
    assert _update._interactive() is True
    assert _update._action("import") == UpdateRequired.ACTIONS["repl"]


def test_unknown_mode_reinstalls_with_notice(world: World) -> None:
    world.mode = "unknown"
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    assert world.pip_shas == [SHA_A] or world.pip_shas == []
    assert any("reinstalando a versão publicada" in n for n in world.notices)


# ------------------------------------------------------------------------------------------- falhas

def test_network_failure_blocks(world: World) -> None:
    world.remote = _bootstrap.BootstrapError("network", "info/refs: tempo esgotado")
    with pytest.raises(UpdateCheckFailed) as info:
        _update.ensure_latest("import")
    assert info.value.message.startswith("Não consegui verificar a versão mais recente no GitHub (info/refs: tempo "
                                         "esgotado). Conecte-se à rede/VPN e rode de novo.")
    assert info.value.details == {"kind": "network"}
    assert state()["result"] == "failed"                        # type: ignore[index]
    world.calls.clear()
    with pytest.raises(UpdateCheckFailed):
        _update.ensure_latest("open")                            # falha não marca: reconsulta
    assert "remote" in world.calls


def test_sandbox_refusal_is_sandbox_violation(world: World) -> None:
    world.remote = _bootstrap.BootstrapError("sandbox", "consulta ao GitHub sem RUMBAPY_TEST_GITHUB")
    with pytest.raises(SandboxViolation):
        _update.ensure_latest("import")


def test_pip_sandbox_refusal(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(sha: str, on_spawn: Any = None) -> int:
        raise _bootstrap.BootstrapError("sandbox", "pip install sem RUMBAPY_TEST_PIP")

    monkeypatch.setattr(_bootstrap, "pip_install", refuse)
    world.remote = SHA_B
    with pytest.raises(SandboxViolation):
        _update.ensure_latest("import")
    assert not _paths.update_in_progress_path().exists()


def test_lock_timeout(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_update, "LOCK_TIMEOUT", 0.2)
    world.remote = SHA_B
    with _locks.file_lock("update", timeout=0):
        with pytest.raises(UpdateFailed) as info:
            _update.ensure_latest("import")
    assert "(outra atualização não terminou)" in info.value.message
    assert world.pip_shas == []
    assert state()["result"] == "failed"                        # type: ignore[index]
    assert "outra atualização não terminou" in (state() or {}).get("reason", "")


# ------------------------------------------------------------------------------------------- TTL do open (H.1)

@pytest.mark.parametrize(("interactive", "age", "queries"), [
    (False, 3601, False),
    (False, 12 * 3600 + 1, True),
    (True, 3599, False),
    (True, 3601, True),
])
def test_open_ttl(world: World, monkeypatch: pytest.MonkeyPatch, interactive: bool, age: float,
                  queries: bool) -> None:
    _update.ensure_latest("import")
    if interactive:
        monkeypatch.setattr(sys, "ps1", ">>> ", raising=False)
    monkeypatch.setattr(_update, "_checked_at", time.monotonic() - age)
    _paths.update_state_path().unlink()                          # sem cache
    world.calls.clear()
    _update.ensure_latest("open")
    assert ("remote" in world.calls) is queries


# ------------------------------------------------------------------------------------------- outro processo atualizou

OTHER = "Outro processo atualizou o RumbaPy. Este processo ainda usa a versão antiga; reinicie-o."


def other_process_updated(world: World, *, with_cache: bool) -> None:
    """Outro processo instalou o master novo (B) depois da 1ª checagem deste (que importou A); o TTL venceu."""
    world.installed = SHA_B
    world.remote = SHA_B
    _update._checked_at = time.monotonic() - 12 * 3600 - 1      # monkeypatch do fixture restaura
    if with_cache:                                               # o que o outro processo gravou há pouco (< 60 s)
        _paths.write_json(_paths.update_state_path(), {"checked_at": time.time(), "context": "import",
                                                       "mode": "archive", "local": SHA_B, "remote": SHA_B,
                                                       "result": "updated", "version": "2.0.1"})
    else:
        _paths.update_state_path().unlink()


@pytest.mark.parametrize("with_cache", [False, True])
def test_reconsult_sees_other_process_update(world: World, with_cache: bool) -> None:
    _update.ensure_latest("import")
    other_process_updated(world, with_cache=with_cache)
    before = state()
    world.calls.clear()
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("open")
    assert info.value.message == OTHER
    assert world.calls == ["install_mode"]                       # nem GitHub, nem pip
    assert state() == before                                     # update-state.json é do disco (certo): intocado
    assert _update.restart_info() == (SHA_B, None)
    log = _paths.update_log_path().read_text(encoding="utf-8")
    assert "WARNING update context=open mode=archive local=bbbbbbb remote=none result=restart-required" in log
    assert "result=up-to-date" not in log.split("context=open", 1)[1]
    # daqui em diante: sempre a mesma mensagem, sem nova consulta (mesmo com force)
    world.calls.clear()
    for ctx, force in (("open", False), ("legacy", True)):
        with pytest.raises(UpdateRequired) as again:
            _update.ensure_latest(ctx, force=force)
        assert again.value.message == OTHER
    assert world.calls == []


def test_reconsult_without_change_still_queries(world: World) -> None:
    _update.ensure_latest("import")
    _update._checked_at = time.monotonic() - 12 * 3600 - 1
    _paths.update_state_path().unlink()
    world.calls.clear()
    _update.ensure_latest("open")
    assert world.calls == ["install_mode", "remote"]
    assert _update.restart_info() == (None, None)


def test_reconsult_other_process_and_newer_master(world: World) -> None:
    """Disco mudou (B) e o master já está em C: reiniciar basta; este processo não instala por cima."""
    _update.ensure_latest("import")
    other_process_updated(world, with_cache=False)
    world.remote = "c" * 40
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("open")
    assert info.value.message == OTHER and world.pip_shas == []


def test_first_check_failed_then_other_process_fixed(world: World) -> None:
    """1ª checagem sem rede (memória = A); outro processo instala B: a reconsulta pede reinício."""
    world.remote = _bootstrap.BootstrapError("network", "info/refs: tempo esgotado")
    with pytest.raises(UpdateCheckFailed):
        _update.ensure_latest("import")
    world.installed = SHA_B
    world.remote = SHA_B
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("open")
    assert info.value.message == OTHER


def test_dev_mode_never_compares_disk(world: World) -> None:
    world.mode = "dev"
    _update.ensure_latest("import")
    world.mode = "archive"                                       # nada foi visto no modo dev
    _update._checked_at = None
    _update.ensure_latest("open")
    assert world.calls == ["install_mode", "install_mode", "remote"]


def test_script_context_action_in_message(world: World) -> None:
    _update.ensure_latest("import")
    other_process_updated(world, with_cache=False)
    with pytest.raises(UpdateRequired) as info:
        _update.ensure_latest("import", force=True)
    assert info.value.message == "Outro processo atualizou o RumbaPy. Rode o script de novo."


# ------------------------------------------------------------------------------------------- isolamento

def test_restart_required_does_not_leak_to_child(world: World, tmp_path: Path) -> None:
    world.remote = SHA_B
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    assert not any("RESTART" in k or "UPDATED" in k for k in os.environ)
    code = ("import json, rumbapy, rumbapy._update as u; "
            "print(json.dumps([u._restart_required, u._restart_message]))")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3] / "src")
    env["RUMBAPY_DEV"] = "1"                                     # o filho roda deste checkout
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60,
                          cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [None, None]


def test_legacy_never_reexecutes(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(os, "execv", lambda *a: spawned.append(a))
    world.remote = SHA_B
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("legacy", force=True)
    assert spawned == []


def test_status_for_cli(world: World) -> None:
    _update.ensure_latest("import")
    info = _update.status()
    assert info["version"] == "2.0.0" and info["mode"] == "archive" and info["commit"] == SHA_A
    assert info["last_check"]["result"] == "up-to-date"


@pytest.mark.parametrize("exc", [PermissionError(13, "antivírus"), OSError(5, "disco")])
def test_lock_os_error_is_update_failed(world: World, monkeypatch: pytest.MonkeyPatch, exc: OSError) -> None:
    def file_lock(name: str, timeout: float | None) -> Any:
        raise exc

    monkeypatch.setattr(_update, "file_lock", file_lock)
    world.remote = SHA_B
    with pytest.raises(UpdateFailed) as info:
        _update.ensure_latest("import")
    assert f"(não consegui a trava de atualização ({type(exc).__name__}))" in info.value.message
    assert world.pip_shas == []
    assert state()["result"] == "failed"                        # type: ignore[index]
    log = _paths.update_log_path().read_text(encoding="utf-8")
    assert "result=failed" in log


# ------------------------------------------------------------------------------------------- pip órfão (E6)

class FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_pip_pid_is_written_to_the_marker(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any] | None] = []
    original = world.pip_install

    def pip(sha: str, on_spawn: Any = None) -> int:
        on_spawn(FakeProc(os.getpid()))
        seen.append(_paths.read_json(_paths.update_in_progress_path()))
        return original(sha)

    monkeypatch.setattr(_bootstrap, "pip_install", pip)
    world.remote = SHA_B
    with pytest.raises(UpdateRequired):
        _update.ensure_latest("import")
    marker = seen[0]
    assert marker is not None and marker["pid"] == os.getpid() and marker["remote"] == SHA_B
    assert marker["pip_pid"] == os.getpid() and marker["pip_created"] == _bootstrap.process_created(os.getpid())
    assert not _paths.update_in_progress_path().exists()


def test_keyboard_interrupt_keeps_marker_and_releases_lock(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def pip(sha: str, on_spawn: Any = None) -> int:
        on_spawn(FakeProc(os.getpid()))
        raise KeyboardInterrupt

    monkeypatch.setattr(_bootstrap, "pip_install", pip)
    world.remote = SHA_B
    with pytest.raises(KeyboardInterrupt):
        _update.ensure_latest("import")
    marker = _paths.read_json(_paths.update_in_progress_path())
    assert marker is not None and marker["pip_pid"] == os.getpid()
    _locks.file_lock("update", timeout=0).release()
    assert _update._restart_required is None


def test_orphan_pip_alive_is_awaited_then_reinstalled(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    try:
        _paths.write_json(_paths.update_in_progress_path(), {
            "pid": DEAD_PID, "created": 1, "remote": SHA_B, "pip_pid": child.pid,
            "pip_created": _bootstrap.process_created(child.pid)})
        world.remote = SHA_B
        monkeypatch.setattr(_update, "_sleep", lambda s: time.sleep(0.05))
        alive_at_pip: list[bool] = []
        original = world.pip_install

        def pip(sha: str, on_spawn: Any = None) -> int:
            alive_at_pip.append(child.poll() is None)
            return original(sha)

        monkeypatch.setattr(_bootstrap, "pip_install", pip)
        monkeypatch.setattr(_bootstrap, "fresh_installed",
                            lambda: {"commit": SHA_B, "version": "2.0.1", "file": None, "url": None})
        with pytest.raises(UpdateRequired):
            _update.ensure_latest("import")
        assert alive_at_pip == [False]                           # esperou o pip órfão; depois reinstalou
        assert any("aguardando o fim de uma atualização" in n for n in world.notices)
    finally:
        child.kill()
        child.wait(timeout=30)


def test_orphan_pip_that_never_ends_is_update_failed(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        _paths.write_json(_paths.update_in_progress_path(), {
            "pid": os.getpid(), "remote": SHA_B, "pip_pid": child.pid,
            "pip_created": _bootstrap.process_created(child.pid)})
        world.remote = SHA_B
        monkeypatch.setattr(_update, "LOCK_TIMEOUT", 0.3)
        monkeypatch.setattr(_update, "_sleep", lambda s: time.sleep(0.05))
        with pytest.raises(UpdateFailed) as info:
            _update.ensure_latest("import")
        assert f"outra atualização não terminou (pip PID {child.pid} ainda rodando)" in info.value.message
        assert world.pip_shas == []
        assert state()["result"] == "failed"                    # type: ignore[index]
        # o marcador do pip ainda vivo NÃO pode ser apagado: ele impede um 2º pip --force-reinstall (E6)
        marker = _paths.read_json(_paths.update_in_progress_path())
        assert marker is not None and marker["pip_pid"] == child.pid
    finally:
        child.kill()
        child.wait(timeout=30)


def test_stale_marker_of_live_idle_owner_is_interrupted(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:                                                         # dono vivo, mas fora da trava: não terminou
        _paths.write_json(_paths.update_in_progress_path(), {
            "pid": child.pid, "created": _bootstrap.process_created(child.pid), "remote": SHA_B})
        world.remote = SHA_B
        monkeypatch.setattr(_bootstrap, "fresh_installed",
                            lambda: {"commit": SHA_B, "version": "2.0.1", "file": None, "url": None})
        with pytest.raises(UpdateRequired):
            _update.ensure_latest("import")
        assert world.pip_shas == [SHA_B]
    finally:
        child.kill()
        child.wait(timeout=30)
