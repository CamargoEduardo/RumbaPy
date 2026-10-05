"""Aceite P-0: ``_paths`` (A.5) — raiz, nomes, ``user_tag``, ``atomic_write`` sob ``PermissionError`` e ``read_json``."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from rumbapy.errors import ParameterError, SandboxViolation
from rumbapy import _paths, _win32

import hashlib
import logging
import json
import os

import pytest

TAG = "0123456789ab"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_home_follows_rumbapy_home(home: Path) -> None:
    assert _paths.home() == home
    assert not home.exists()                       # nada é criado só por perguntar
    assert _paths.run_dir() == home / "run"
    assert _paths.logs_dir() == home / "logs"
    assert _paths.boot_logs_dir() == home / "logs" / "boot"
    assert _paths.locks_dir() == home / "locks"


def test_relative_rumbapy_home_becomes_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O worker roda com cwd=HOME (D.1): um HOME relativo apontaria para outra pasta (registro, logs, travas)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUMBAPY_HOME", os.path.join("rel", "..", "dados", "home"))
    assert _paths.home().is_absolute()
    assert _paths.home() == tmp_path / "dados" / "home"
    assert _paths.lock_path("update") == tmp_path / "dados" / "home" / "locks" / "update.lock"
    monkeypatch.chdir(tmp_path / "..")                                # o valor já resolvido não muda com o cwd
    assert _paths.home() != tmp_path / "dados" / "home"               # (por isso o filho recebe str(home()))


def test_sandbox_requires_rumbapy_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_HOME", raising=False)
    with pytest.raises(SandboxViolation):
        _paths.home()


def test_default_home_is_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RUMBAPY_HOME", raising=False)
    monkeypatch.delenv("RUMBAPY_SANDBOX", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert _paths.home() == tmp_path / "AppData" / "Local" / "rumbapy"
    assert not _paths.sandboxed()


def test_names_and_paths(home: Path) -> None:
    assert _paths.worker_registry("D", tag=TAG) == home / "run" / f"worker-{TAG}-D.json"
    assert _paths.launched_registry("Z", tag=TAG) == home / "run" / f"launched-{TAG}-Z.json"
    assert _paths.pipe_name("D", "00ff00ff00ff00ff", tag=TAG) == rf"\\.\pipe\rumbapy2-{TAG}-D-00ff00ff00ff00ff"
    assert _paths.stop_event_name("A", tag=TAG) == rf"Local\rumbapy2-{TAG}-A-stop"
    assert _paths.spawn_lock_name("D", tag=TAG) == f"spawn-{TAG}-D"
    assert _paths.lock_path(_paths.spawn_lock_name("D", tag=TAG)) == home / "locks" / f"spawn-{TAG}-D.lock"
    for name in (_paths.UPDATE_LOCK, _paths.KEY_LOCK, _paths.PY32_LOCK):
        assert _paths.lock_path(name) == home / "locks" / f"{name}.lock"
    assert _paths.authkey_path() == home / "authkey.bin"
    assert _paths.update_state_path() == home / "update-state.json"
    assert _paths.heal_state_path() == home / "heal-state.json"
    assert _paths.update_in_progress_path() == home / "update-in-progress.json"
    assert _paths.runtime_cache_path() == home / "runtime.json"
    assert _paths.client_log_path(date(2026, 10, 3)) == home / "logs" / "client-20261003.log"
    assert _paths.worker_log_path("D", tag=TAG) == home / "logs" / f"worker-{TAG}-D.log"
    assert _paths.update_log_path() == home / "logs" / "update.log"
    when = datetime(2026, 10, 3, 14, 5, 12, 345678)
    assert _paths.pip_log_path(when) == home / "logs" / "pip-20261003-140512.log"
    assert _paths.boot_log_path("D", tag=TAG, when=when) == \
        home / "logs" / "boot" / f"worker-{TAG}-D-20261003-140512-345.txt"
    assert _paths.RESERVED_LETTER == "_"
    assert _paths.worker_registry("_", tag=TAG).name == f"worker-{TAG}-_.json"


def test_pipe_rand() -> None:
    rand = _paths.new_pipe_rand()
    assert len(rand) == 16 and int(rand, 16) >= 0
    assert rand != _paths.new_pipe_rand()
    for bad in ("", "XYZ", "ab-cd", None):
        with pytest.raises(ParameterError):
            _paths.pipe_name("D", bad, tag=TAG)  # type: ignore[arg-type]


@pytest.mark.parametrize("letter", ["d", "DD", "", "1", ".", "..\\x", None, "Ç"])
def test_bad_letters(letter: object, home: Path) -> None:
    with pytest.raises(ParameterError):
        _paths.worker_registry(letter, tag=TAG)  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        _paths.stop_event_name(letter, tag=TAG)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "../x", "a b", "a/b", "a\\b"])
def test_bad_lock_names(name: str, home: Path) -> None:
    with pytest.raises(ParameterError):
        _paths.lock_path(name)


def test_user_tag(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr(_win32, "user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(_win32, "session_id", lambda: 3)
    monkeypatch.setattr(_paths, "_tag_cache", {})
    tag = _paths.user_tag()
    assert len(tag) == 12 and int(tag, 16) >= 0
    assert tag == _paths.user_tag()                                 # cache
    expected_sandbox = hashlib.sha256(f"S-1-5-21-1-2-3-1001|3|sandbox|{home}".encode()).hexdigest()[:12]
    assert tag == expected_sandbox
    monkeypatch.delenv("RUMBAPY_SANDBOX")
    real = _paths.user_tag()
    assert real == hashlib.sha256(b"S-1-5-21-1-2-3-1001|3").hexdigest()[:12]   # fórmula A.5
    assert real != tag                                              # o sandbox nunca usa os nomes reais
    assert _paths.worker_registry("D").name == f"worker-{real}-D.json"


def test_atomic_write_creates_parents_and_leaves_no_temp(home: Path) -> None:
    target = home / "run" / "worker-x-D.json"
    _paths.atomic_write(target, b'{"a":1}')
    assert target.read_bytes() == b'{"a":1}'
    _paths.atomic_write(target, b'{"a":2}')
    assert target.read_bytes() == b'{"a":2}'
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_atomic_write_retries_on_permission_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = home / "registry.json"
    real_replace = os.replace
    attempts: list[str] = []
    sleeps: list[float] = []

    def flaky_replace(src: str, dst: str) -> None:
        attempts.append(str(dst))
        if len(attempts) < 4:
            raise PermissionError(13, "Acesso negado (simulado)")
        real_replace(src, dst)

    monkeypatch.setattr(_paths.os, "replace", flaky_replace)
    monkeypatch.setattr(_paths.time, "sleep", sleeps.append)
    _paths.atomic_write(target, b"ok")
    assert target.read_bytes() == b"ok"
    assert len(attempts) == 4 and sleeps == [0.05] * 3
    assert [p.name for p in home.iterdir()] == ["registry.json"]


def test_atomic_write_gives_up_after_20_attempts(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = home / "registry.json"
    attempts: list[int] = []

    def always_denied(src: str, dst: str) -> None:
        attempts.append(1)
        raise PermissionError(13, "Acesso negado (simulado)")

    monkeypatch.setattr(_paths.os, "replace", always_denied)
    monkeypatch.setattr(_paths.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        _paths.atomic_write(target, b"nunca")
    assert len(attempts) == 20
    assert not target.exists()
    assert list(home.iterdir()) == []                               # o temporário foi apagado


def test_atomic_write_refuses_text(home: Path) -> None:
    with pytest.raises(ParameterError):
        _paths.atomic_write(home / "x", "texto")  # type: ignore[arg-type]


def test_read_json(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = home / "state.json"
    with caplog.at_level(logging.DEBUG, logger="rumbapy._paths"):
        assert _paths.read_json(target) is None                     # ausente: só DEBUG (caso normal), sem aviso
        assert [r.levelno for r in caplog.records if r.name == "rumbapy._paths"] == [logging.DEBUG]
        caplog.clear()
    with caplog.at_level(logging.WARNING, logger="rumbapy._paths"):
        _paths.write_json(target, {"pid": 4321, "acento": "çã€"})
        assert _paths.read_json(target) == {"pid": 4321, "acento": "çã€"}
        assert json.loads(target.read_bytes().decode("utf-8")) == {"pid": 4321, "acento": "çã€"}
        for content in (b"{quebrado", b"[1, 2]", b"\xff\xfe\x00", b""):
            target.write_bytes(content)
            assert _paths.read_json(target) is None
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 4


def test_read_json_while_file_is_locked(home: Path, monkeypatch: pytest.MonkeyPatch,
                                        caplog: pytest.LogCaptureFixture) -> None:
    target = home / "state.json"
    _paths.write_json(target, {"ok": True})
    calls: list[int] = []
    real_read = Path.read_bytes

    def locked_twice(self: Path) -> bytes:
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "em uso (simulado)")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", locked_twice)
    monkeypatch.setattr(_paths.time, "sleep", lambda s: None)
    assert _paths.read_json(target) == {"ok": True}
    calls.clear()
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(13, "sempre")))
    with caplog.at_level(logging.WARNING, logger="rumbapy._paths"):
        assert _paths.read_json(target) is None
    assert "bloqueado" in caplog.text


def test_prune_boot_logs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = home / "logs" / "boot"
    boot.mkdir(parents=True)
    names = [f"worker-{TAG}-D-20261003-1400{i:02d}-000.txt" for i in range(7)]
    for name in names:
        (boot / name).write_text("x")
    (boot / f"worker-{TAG}-Z-20261003-140000-000.txt").write_text("outra letra")
    assert _paths.prune_boot_logs("D", tag=TAG) == 2
    assert sorted(p.name for p in boot.iterdir() if "-D-" in p.name) == names[2:]
    assert (boot / f"worker-{TAG}-Z-20261003-140000-000.txt").exists()

    real_unlink = Path.unlink

    def busy_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == names[2]:
            raise PermissionError(13, "em uso por um worker vivo (simulado)")
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy_unlink)
    assert _paths.prune_boot_logs("D", tag=TAG, keep=1) == 3       # o arquivo em uso fica
    assert sorted(p.name for p in boot.iterdir() if "-D-" in p.name) == [names[2], names[6]]
