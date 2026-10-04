"""Máquina de estados do ``get_worker`` (D.8) com spawn, registro e handshake roteirizados (sem processo real)."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit.client.bridge.fakes import FakeChannel
from rumbapy.errors import (RumbaNotInstalled, TerminalInUse, UpdateRequired, WorkerError, WorkerStartFailed)
from rumbapy import _bridge, _ipc, _paths

import pytest


class Proc:
    def __init__(self, code: int | None) -> None:
        self.code = code
        self.pid = 1

    def poll(self) -> int | None:
        return self.code


class Harness:
    """Registro em disco (``RUMBAPY_HOME`` do teste) + vivos + spawn/handshake falsos."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
        self.alive: set[int] = set()
        self.spawn_codes: list[int | None] = []          # None = sobe e registra
        self.spawned: list[str] = []
        self.sessions: list[Any] = []                    # resultados de _session na ordem
        self.stops: list[str] = []
        self.next_pid = 5000
        monkeypatch.setattr(_bridge, "process_alive", lambda pid, created=None: pid in self.alive)
        monkeypatch.setattr(_bridge, "spawn", self._spawn)
        self.bridge = _bridge.Bridge(build="x" * 16, config=_bridge.SpawnConfig(python32="py", fake_dll="m:f"),
                                     clock=self._clock, sleep=self._sleep)
        self.now = 0.0
        monkeypatch.setattr(_bridge, "_buildid", type("B", (), {"compute": staticmethod(lambda p: "x" * 16)}))
        monkeypatch.setattr(self.bridge, "key", lambda reload=False: b"k" * 32)
        monkeypatch.setattr(self.bridge, "_session", self._session)
        monkeypatch.setattr(self.bridge, "_signal_stop", lambda letter: self.stops.append(letter))
        monkeypatch.setattr(self.bridge, "_shutdown_old", lambda *a: self._shutdown())
        self.shutdowns = 0

    def _clock(self) -> float:
        return self.now

    def _sleep(self, s: float) -> None:
        self.now += s

    def _shutdown(self) -> None:
        self.shutdowns += 1
        self.alive.clear()

    def register(self, pid: int, *, owner: dict | None = None, pipe: str = "p", **kw: Any) -> None:
        self.alive.add(pid)
        _paths.ensure_dir(_paths.run_dir())
        _paths.write_json(_paths.worker_registry("D"), {"pid": pid, "pipe": pipe, "owner": owner, **kw})

    def _spawn(self, letter: str, config: Any, **kw: Any) -> _bridge.Spawned:
        code = self.spawn_codes.pop(0) if self.spawn_codes else None
        pipe = f"pipe-{len(self.spawned)}"
        self.spawned.append(pipe)
        if code is None:
            self.next_pid += 1
            self.register(self.next_pid, pipe=pipe)
        return _bridge.Spawned(Proc(code), pipe, Path("boot.txt"), "py")

    def _session(self, letter: str, mode: str, reg: dict, registry: Path, st: Any) -> tuple[str, Any]:
        item = self.sessions.pop(0) if self.sessions else "ok"
        if isinstance(item, BaseException):
            raise item
        if item == "ok":
            return "ok", _bridge.WorkerHandle(FakeChannel(), letter=letter, mode=mode, pid=reg["pid"], created=None,
                                              hello={}, registry=registry)
        if item == "stopping":
            self.alive.discard(reg["pid"])
        return item, None


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    return Harness(monkeypatch, tmp_path)


def test_spawns_when_no_registry_and_caches_owner(h: Harness) -> None:
    handle = h.bridge.get_handle("D", "owner")
    assert handle.pid == 5001 and len(h.spawned) == 1 and handle.users == 1
    again = h.bridge.get_handle("D", "owner")
    assert again is handle and handle.users == 2 and len(h.spawned) == 1
    h.bridge.release(again)
    h.bridge.release(handle)
    assert h.bridge.handles == {} and handle.channel.closed


def test_admin_is_not_cached(h: Harness) -> None:
    a = h.bridge.get_handle("D", "admin")
    b = h.bridge.get_handle("D", "admin")
    assert a is not b and h.bridge.handles == {} and len(h.spawned) == 1


def test_existing_worker_is_reused(h: Harness) -> None:
    h.register(4000)
    assert h.bridge.get_handle("D", "owner").pid == 4000 and h.spawned == []


def test_stopping_waits_and_counts(h: Harness) -> None:
    h.register(4000)
    h.sessions = ["stopping"]
    handle = h.bridge.get_handle("D", "owner")
    assert handle.pid == 5001                                          # esperou sair e subiu outro


def test_old_build_without_owner_is_shut_down_once(h: Harness) -> None:
    h.register(4000)
    h.sessions = ["old_build"]
    assert h.bridge.get_handle("D", "owner").pid == 5001 and h.shutdowns == 1


def test_dying_worker_with_exit_reason_is_awaited(h: Harness) -> None:
    h.register(4000, exit_reason="hung")
    h.alive.discard(4000)                                              # sai durante a espera
    h.alive.add(4000)
    calls: list[Any] = []
    original = _bridge.wait_exit

    def fake_wait(pid: Any, created: Any, timeout: float, **kw: Any) -> bool:
        calls.append(pid)
        h.alive.discard(pid)
        return original(pid, created, timeout, **kw)

    import rumbapy._bridge as mod

    mod.wait_exit, saved = fake_wait, mod.wait_exit
    try:
        assert h.bridge.get_handle("D", "owner").pid == 5001
    finally:
        mod.wait_exit = saved
    assert calls == [4000] and h.sessions == []                        # nem tentou falar com o moribundo


def test_unresponsive_with_owner_is_terminal_in_use(h: Harness) -> None:
    h.register(4000, owner={"pid": 4444, "script": "outro.py", "since": "s"})
    h.alive.add(4444)
    h.sessions = [_ipc.PipeTimeout("mudo")]
    with pytest.raises(TerminalInUse) as info:
        h.bridge.get_handle("D", "owner")
    assert "4444" in info.value.message and h.stops == []


def test_unresponsive_without_owner_signals_stop(h: Harness) -> None:
    h.register(4000)
    h.sessions = [_ipc.PipeNotFound("sumiu")]

    def stop(letter: str) -> None:
        h.stops.append(letter)
        h.alive.discard(4000)

    h.bridge._signal_stop = stop  # type: ignore[method-assign]
    assert h.bridge.get_handle("D", "owner").pid == 5001 and h.stops == ["D"]


def test_unresponsive_never_exits_is_worker_error(h: Harness) -> None:
    h.register(4000)
    h.sessions = [_ipc.PipeTimeout("mudo")]
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    assert info.value.message == "o processo auxiliar não responde"
    assert h.now >= _bridge.EXIT_WAIT_S


def test_exit_15_gets_three_extra_spawns(h: Harness) -> None:
    h.spawn_codes = [15, 15, 15]
    assert h.bridge.get_handle("D", "owner").pid == 5001 and len(h.spawned) == 4


def test_exit_15_four_times_fails(h: Harness) -> None:
    h.spawn_codes = [15, 15, 15, 15]
    with pytest.raises(WorkerStartFailed):
        h.bridge.get_handle("D", "owner")


def test_exit_11_once_then_ok_twice_fails(h: Harness) -> None:
    h.spawn_codes = [11]
    assert h.bridge.get_handle("D", "admin").pid == 5001
    h.alive.clear()
    h.spawn_codes = [11, 11]
    with pytest.raises(WorkerStartFailed):
        h.bridge.get_handle("D", "admin")


def test_exit_13_rereads_key_once(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    reloads: list[bool] = []
    monkeypatch.setattr(h.bridge, "key", lambda reload=False: reloads.append(reload) or b"k" * 32)
    h.spawn_codes = [13]
    assert h.bridge.get_handle("D", "owner").pid == 5001 and True in reloads
    h.alive.clear()
    h.spawn_codes = [13, 13]
    with pytest.raises(WorkerStartFailed):
        h.bridge.get_handle("D", "admin")


def test_exit_10_is_rumba_not_installed(h: Harness) -> None:
    h.spawn_codes = [10]
    with pytest.raises(RumbaNotInstalled):
        h.bridge.get_handle("D", "owner")


def test_never_registers_is_start_failed(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def spawn(letter: str, config: Any, **kw: Any) -> _bridge.Spawned:
        return _bridge.Spawned(Proc(None), "nunca", Path("boot.txt"), "py")

    monkeypatch.setattr(_bridge, "spawn", spawn)
    with pytest.raises(WorkerStartFailed) as info:
        h.bridge.get_handle("D", "owner")
    assert info.value.message == "O processo auxiliar 32-bit do terminal D não iniciou: não ficou pronto em 20 s."
    assert "código" not in info.value.message                          # não é um código de saída (A.12 f)
    assert h.now >= _bridge.SPAWN_READY_S


def test_disk_build_changed_before_spawn(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_bridge, "_buildid", type("B", (), {"compute": staticmethod(lambda p: "novo")}))
    with pytest.raises(UpdateRequired):
        h.bridge.get_handle("D", "owner")
    assert h.spawned == []


def test_internal_ipc_errors_are_translated(h: Harness) -> None:
    h.sessions = [_ipc.AuthRejected("chave diferente")] * 50
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    assert type(info.value) is WorkerError                              # nunca AuthRejected/PipeTimeout (A.9 e)
    assert not isinstance(info.value, (_ipc.AuthRejected, _ipc.PipeTimeout, _ipc.PipeNotFound))


def test_evicted_cached_handle_is_not_reused(h: Harness) -> None:
    first = h.bridge.get_handle("D", "owner")
    first.evicted = True
    second = h.bridge.get_handle("D", "owner")
    assert second is not first and not first.channel.closed           # as Session antigas seguem com o despejado


def test_invalid_mode_and_letter(h: Harness) -> None:
    from rumbapy.errors import ParameterError

    with pytest.raises(ParameterError):
        h.bridge.get_handle("D", "dono")
    with pytest.raises(ParameterError):
        h.bridge.get_handle("dd", "owner")


def test_pid_reused_with_other_created_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from rumbapy import _win32

    created = _win32.process_created(os.getpid())
    assert _bridge.process_alive(os.getpid(), created)
    assert not _bridge.process_alive(os.getpid(), (created or 0) + 1)
    assert not _bridge.registry_alive({"pid": os.getpid(), "created": (created or 0) + 1, "pipe": "p"})
    assert not _bridge.process_alive(True) and not _bridge.process_alive(-1) and not _bridge.process_alive("1")


def test_find_handle_never_spawns(h: Harness) -> None:
    assert h.bridge.find_handle("D", "admin") is None and h.spawned == []
    h.register(4000)
    found = h.bridge.find_handle("D", "admin")
    assert found is not None and found.pid == 4000 and h.spawned == []


def test_find_handle_worker_leaving_returns_none(h: Harness) -> None:
    h.register(4000)
    h.sessions = ["stopping"]                                          # sai durante a espera: não sobe outro
    assert h.bridge.find_handle("D", "owner") is None and h.spawned == []


def test_spawn_lock_oserror_is_worker_error(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(name: str, timeout: float) -> Any:
        raise PermissionError(13, "acesso negado")

    monkeypatch.setattr(_bridge._locks, "file_lock", denied)
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    assert type(info.value) is WorkerError and info.value.details["error"] == "PermissionError"


def test_give_up_message_has_numeric_code(h: Harness) -> None:
    h.spawn_codes = [0, 0, 0]                                          # saída 0 no boot: repete o spawn
    with pytest.raises(WorkerStartFailed) as info:
        h.bridge.get_handle("D", "owner")
    assert "não iniciou: desisti depois de 2 tentativas de iniciar (último código de saída 0)." in info.value.message
    assert info.value.details == {"exit_code": 0, "attempts": 2}


def test_start_failed_from_spawn_propagates(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def spawn(letter: str, config: Any, **kw: Any) -> _bridge.Spawned:
        raise _bridge.start_failed(letter, "não consegui executar o Python 32-bit (FileNotFoundError, erro 2).")

    monkeypatch.setattr(_bridge, "spawn", spawn)
    with pytest.raises(WorkerStartFailed) as info:
        h.bridge.get_handle("D", "owner")
    assert "não iniciou: não consegui executar o Python 32-bit (FileNotFoundError, erro 2)." in info.value.message


@pytest.mark.parametrize("raw, level", [(None, "INFO"), ("debug", "DEBUG"), (" warning ", "WARNING"),
                                        ("verboso", "INFO"), ("", "INFO")])
def test_log_level_from_env(monkeypatch: pytest.MonkeyPatch, raw: str | None, level: str) -> None:
    if raw is None:
        monkeypatch.delenv("RUMBAPY_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("RUMBAPY_LOG_LEVEL", raw)
    assert _bridge.log_level_from_env() == level


def test_registry_stopping_waits_without_connecting(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parada limpa em curso (``state=stopping`` no registro, o pipe já não aceita): espera o PID sair, sem gastar
    a única parada (``stops``) nem sinalizar o evento, e sobe outro."""
    h.register(4000, state="stopping")
    seen: list[int] = []
    inner = h._session

    def session(letter: str, mode: str, reg: dict, registry: Path, st: Any) -> tuple[str, Any]:
        seen.append(reg["pid"])
        return inner(letter, mode, reg, registry, st)

    def sleep(s: float) -> None:
        h.now += s
        h.alive.discard(4000)                                          # sai durante a espera

    monkeypatch.setattr(h.bridge, "_session", session)
    h.bridge.sleep = sleep
    handle = h.bridge.get_handle("D", "owner")
    assert handle.pid == 5001 and seen == [5001] and h.stops == []


def test_registry_stopping_forever_is_not_exited(h: Harness) -> None:
    h.register(4000, state="stopping")
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    assert "(PID 4000) não encerrou" in info.value.message and h.spawned == []


# ------------------------------------------------------------------------------------- correções da verificação

def test_unresponsive_with_own_lease_retries_once_then_specific_error(h: Harness) -> None:
    import os

    h.register(4000, owner={"pid": os.getpid(), "script": "eu.py", "since": "2026-10-04T10:00:00"})
    h.sessions = [_ipc.PipeTimeout("mudo"), _ipc.PipeTimeout("mudo")]
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    err = info.value
    assert type(err) is WorkerError and "lease é deste mesmo processo" in err.message
    assert err.hint and "Session" in err.hint
    assert h.stops == [] and h.spawned == []                           # a parada não foi gasta (o worker adiaria)
    assert h.now >= _bridge.SELF_OWNER_RETRY_S and h.now < _bridge.EXIT_WAIT_S


def test_unresponsive_with_own_lease_recovers_on_retry(h: Harness) -> None:
    import os

    h.register(4000, owner={"pid": os.getpid()})
    h.sessions = [_ipc.PipeTimeout("mudo"), "ok"]
    assert h.bridge.get_handle("D", "owner").pid == 4000 and h.stops == []


def test_key_oserror_is_worker_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))

    def denied(path: Any, **kw: Any) -> bytes:
        raise PermissionError(13, "acesso negado")

    monkeypatch.setattr(_bridge._ipc, "load_or_create_key", denied)
    with pytest.raises(WorkerError) as info:
        _bridge.Bridge(build="x" * 16).key()
    assert type(info.value) is WorkerError and "authkey.bin" in info.value.message
    assert info.value.details["error"] == "PermissionError"


def test_key_oserror_in_open_is_not_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``OSError`` da chave dentro de ``_open`` não vira "gone" (3 esperas de 10 s e um "não responde" enganoso)."""
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    bridge = _bridge.Bridge(build="x" * 16)
    channel = FakeChannel()
    monkeypatch.setattr(_bridge._ipc, "connect", lambda *a, **kw: channel)

    def denied(path: Any, **kw: Any) -> bytes:
        raise PermissionError(13, "acesso negado")

    monkeypatch.setattr(_bridge._ipc, "load_or_create_key", denied)
    with pytest.raises(WorkerError) as info:
        bridge._session("D", "owner", {"pid": 4000, "pipe": "p"}, tmp_path / "w.json", _bridge._Attempts())
    assert "authkey.bin" in info.value.message and channel.closed


def test_disk_build_oserror_in_handshake_is_not_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.unit.client.bridge.fakes import hello

    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    bridge = _bridge.Bridge(build="x" * 16)
    channel = FakeChannel([hello(pid=4000, build_id="velho")])
    monkeypatch.setattr(_bridge._ipc, "connect", lambda *a, **kw: channel)
    monkeypatch.setattr(_bridge._ipc, "client_auth", lambda *a, **kw: None)
    monkeypatch.setattr(bridge, "key", lambda reload=False: b"k" * 32)

    def broken(pkgdir: Any) -> str:
        raise PermissionError(13, "arquivo em uso pelo update")

    monkeypatch.setattr(_bridge._buildid, "compute", broken)
    with pytest.raises(WorkerError) as info:
        bridge._session("D", "owner", {"pid": 4000, "pipe": "p"}, tmp_path / "w.json", _bridge._Attempts())
    assert "conferir a versão" in info.value.message and channel.closed
    assert info.value.details["error"] == "PermissionError"


def test_disk_build_oserror_before_spawn_is_worker_error(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(pkgdir: Any) -> str:
        raise OSError(5, "negado")

    monkeypatch.setattr(_bridge, "_buildid", type("B", (), {"compute": staticmethod(broken)}))
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "owner")
    assert type(info.value) is WorkerError and h.spawned == []


def test_exit_3_at_boot_uses_exit_reason(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from rumbapy.errors import SessionStopped

    def spawn(letter: str, config: Any, **kw: Any) -> _bridge.Spawned:
        _paths.ensure_dir(_paths.run_dir())
        _paths.write_json(_paths.worker_registry("D"), {"pid": 7, "pipe": "meu", "exit_reason": "window_gone"})
        return _bridge.Spawned(Proc(3), "meu", Path("boot.txt"), "py")

    monkeypatch.setattr(_bridge, "spawn", spawn)
    h.sessions = ["gone"]                                              # o pipe do worker já sumiu
    with pytest.raises(SessionStopped):
        h.bridge.get_handle("D", "owner")


def test_spawn_lock_waits_more_rounds_before_giving_up(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from rumbapy import _locks

    rounds = -(-int(_bridge.SPAWN_LOCK_MAX_S) // int(_bridge.SPAWN_LOCK_S))
    calls: list[float] = []

    class Lock:
        def release(self) -> None:
            pass

    def busy(name: str, timeout: float) -> Any:
        calls.append(timeout)
        if len(calls) < rounds:
            raise _locks.LockTimeout(name, timeout)
        return Lock()

    monkeypatch.setattr(_bridge._locks, "file_lock", busy)
    assert h.bridge.get_handle("D", "admin").pid == 5001                # o último round consegue
    assert calls == [_bridge.SPAWN_LOCK_S] * rounds
    calls.clear()

    def never(name: str, timeout: float) -> Any:
        calls.append(timeout)
        raise _locks.LockTimeout(name, timeout)

    monkeypatch.setattr(_bridge._locks, "file_lock", never)
    with pytest.raises(WorkerError) as info:
        h.bridge.get_handle("D", "admin")
    assert len(calls) == rounds and f"há mais de {rounds * _bridge.SPAWN_LOCK_S:.0f} s" in info.value.message
