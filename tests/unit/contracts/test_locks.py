"""Aceite P-0: ``file_lock`` exclusivo entre processos e liberado na morte do dono (I43, R24)."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from rumbapy._locks import FileLock, LockTimeout, file_lock

import subprocess
import time
import sys

import pytest

SRC = Path(__file__).resolve().parents[3] / "src"

# Processo filho que segura a trava e depois MORRE sem soltá-la (os._exit): não é matado por nós.
# Carrega o pacote como o BOOT do worker (D.1), sem executar rumbapy/__init__.py.
HOLDER = (
    "import os, sys, types\n"
    "pkg = types.ModuleType('rumbapy'); pkg.__path__ = [os.path.join(sys.argv[1], 'rumbapy')]\n"
    "sys.modules['rumbapy'] = pkg\n"
    "from rumbapy._locks import file_lock\n"
    "lock = file_lock(sys.argv[2], timeout=10)\n"
    "print('locked', flush=True)\n"
    "sys.stdin.readline()\n"
    "os._exit(0)\n"
)


@pytest.fixture
def lock_name() -> Iterator[str]:
    yield f"teste-{time.monotonic_ns()}"


def test_acquire_release_and_context_manager(lock_name: str) -> None:
    with file_lock(lock_name, timeout=1) as lock:
        assert lock.locked
        assert lock.path.name == f"{lock_name}.lock" and lock.path.exists()
    assert not lock.locked
    lock.release()                                     # idempotente
    with file_lock(lock_name, timeout=0):
        pass


def test_exclusive_inside_the_same_process(lock_name: str) -> None:
    first = file_lock(lock_name, timeout=0)
    try:
        other = FileLock(lock_name)
        started = time.monotonic()
        with pytest.raises(LockTimeout) as info:
            other.acquire(timeout=0.35)
        assert 0.3 <= time.monotonic() - started < 3.0
        assert isinstance(info.value, TimeoutError) and info.value.name == lock_name
        with pytest.raises(LockTimeout):
            other.acquire(timeout=0)                    # tentativa única
        with pytest.raises(RuntimeError):
            first.acquire(timeout=0)                    # o mesmo objeto não "reentra"
    finally:
        first.release()
    other.acquire(timeout=0)
    other.release()


def test_with_requires_an_acquired_lock(lock_name: str) -> None:
    with pytest.raises(RuntimeError):
        with FileLock(lock_name):
            pass


def test_exclusive_between_processes_and_released_when_the_owner_dies(lock_name: str) -> None:
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", HOLDER, str(SRC), lock_name],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdin is not None
        line = child.stdout.readline().strip()
        assert line == "locked", child.stderr.read() if child.stderr else ""
        with pytest.raises(LockTimeout):
            file_lock(lock_name, timeout=0.3)
        child.stdin.write("pode morrer\n")
        child.stdin.flush()
        assert child.wait(timeout=10) == 0              # saiu por conta própria, SEM soltar a trava
        with file_lock(lock_name, timeout=5) as lock:   # o Windows soltou a trava com o processo
            assert lock.locked
    finally:
        if child.stdin and not child.stdin.closed:
            child.stdin.close()
        child.wait(timeout=10)
        for stream in (child.stdout, child.stderr):
            if stream:
                stream.close()
