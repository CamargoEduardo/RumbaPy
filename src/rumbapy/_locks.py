"""Travas de arquivo entre processos (``locks\\<nome>.lock`` com ``msvcrt.locking``; I43, R24).

A trava é do *handle*: o Windows a solta sozinho quando o processo dono morre, por qualquer motivo. Não é reentrante
(o mesmo processo também espera por ela se a pedir de novo) e vale entre threads. Esperas em fatias de 0,1 s
(Ctrl+C funciona). Uso::

    with file_lock("update", timeout=600):       # LockTimeout (TimeoutError) se não conseguir no prazo
        ...
    lock = FileLock(_paths.spawn_lock_name("D"))
    lock.acquire(timeout=0)                      # tentativa única, sem espera
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Self

from .errors import UnsupportedPlatform
from . import _paths

import threading
import errno
import time
import os

try:
    import msvcrt
except ImportError:                       # pragma: no cover - o RumbaPy só existe no Windows
    msvcrt = None  # type: ignore[assignment]

POLL = 0.1
_BUSY_ERRNOS = {errno.EACCES, getattr(errno, "EDEADLOCK", None), getattr(errno, "EDEADLK", None)} - {None}


class LockTimeout(TimeoutError):
    """A trava não foi obtida no prazo (quem chama traduz para o erro do seu contexto)."""

    def __init__(self, name: str, timeout: float | None) -> None:
        super().__init__(f"não consegui a trava {name!r} em {timeout} s")
        self.name = name
        self.timeout = timeout


class FileLock:
    """Trava exclusiva de 1 byte em ``locks\\<name>.lock``."""

    def __init__(self, name: str, *, path: str | os.PathLike[str] | None = None) -> None:
        self.name = name
        self.path = Path(path) if path is not None else _paths.lock_path(name)
        self._fd: int | None = None
        self._guard = threading.Lock()

    @property
    def locked(self) -> bool:
        return self._fd is not None

    def _try_once(self, fd: int) -> bool:
        assert msvcrt is not None              # acquire() já recusou a plataforma sem msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in _BUSY_ERRNOS:
                return False
            raise

    def acquire(self, timeout: float | None, *, poll: float = POLL) -> None:
        """Obtém a trava em até ``timeout`` s (``0`` = uma tentativa; ``None`` = sem prazo)."""
        if msvcrt is None:
            raise UnsupportedPlatform.make()
        with self._guard:
            if self._fd is not None:
                raise RuntimeError(f"a trava {self.name!r} já está com este objeto")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            fd = os.open(self.path, flags, 0o600)
            deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
            try:
                while not self._try_once(fd):
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise LockTimeout(self.name, timeout)
                        time.sleep(min(poll, remaining))
                    else:
                        time.sleep(poll)
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd

    def release(self) -> None:
        """Solta a trava (idempotente)."""
        with self._guard:
            fd, self._fd = self._fd, None
        if fd is None:
            return
        assert msvcrt is not None              # só há fd depois de um acquire() bem-sucedido
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass                           # fechar o handle solta de qualquer jeito
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        if self._fd is None:
            raise RuntimeError("use file_lock(...) ou chame acquire() antes do with")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def __del__(self) -> None:
        if getattr(self, "_fd", None) is not None:
            try:
                self.release()
            except Exception:
                pass


def file_lock(name: str, timeout: float | None, *, poll: float = POLL) -> FileLock:
    """Devolve a trava ``name`` já obtida (use com ``with``); ``LockTimeout`` se o prazo estourar."""
    lock = FileLock(name)
    lock.acquire(timeout, poll=poll)
    return lock
