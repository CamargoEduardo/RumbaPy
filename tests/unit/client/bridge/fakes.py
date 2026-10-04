"""Dublês da ponte para os testes de unidade: canal roteirizado, relógio falso e ``Bridge`` falsa."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rumbapy import _protocol

import os

BUILD = "b" * 16


def hello(**kw: Any) -> dict[str, Any]:
    msg = {"t": "hello", "protocol": 2, "version": "2.0.0", "build_id": BUILD, "pid": 4321, "letter": "D",
           "python": "3.13.2", "started_at": "2026-10-04T10:00:00-03:00", "state": "listening", "owner": None,
           "connected": False, "hwnd": None, "guard": "none"}
    msg.update(kw)
    return msg


class FakeChannel:
    """``recv_msg`` devolve a fila ``incoming`` (itens chamáveis são executados: podem levantar); ``sent`` guarda tudo.

    ``responder(msg)`` (opcional) gera respostas para cada requisição enviada.
    """

    def __init__(self, incoming: list[Any] | None = None,
                 responder: Callable[[dict[str, Any]], list[Any]] | None = None) -> None:
        self.incoming: list[Any] = list(incoming or [])
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.responder = responder
        self.recv_calls = 0

    def send_msg(self, obj: dict[str, Any], *, lock_timeout: float | None = None) -> bool:
        if self.closed:
            raise BrokenPipeError("canal fechado")
        self.sent.append(obj)
        if self.responder is not None:
            self.incoming.extend(self.responder(obj))
        return True

    def recv_msg(self, timeout: float) -> dict[str, Any] | None:
        self.recv_calls += 1
        if not self.incoming:
            return None
        item = self.incoming.pop(0)
        if callable(item):
            return item()
        return item

    def close(self) -> None:
        self.closed = True


def ok(result: dict[str, Any]) -> Callable[[dict[str, Any]], list[Any]]:
    """Responder que devolve ``result`` para qualquer requisição."""
    return lambda msg: [_protocol.make_result(msg["id"], result)]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeHandle:
    """``WorkerHandle`` mínimo para os testes do ``WorkerBackend``: ``script`` = lista de respostas por op."""

    def __init__(self, pid: int, script: dict[str, list[Any]] | None = None) -> None:
        self.pid = pid
        self.created = None
        self.letter = "D"
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, dict[str, Any] | None, int | None]] = []
        self.dead = False
        self.evicted = False
        self.exiting = False
        self.users = 1
        self.waited = 0

    @property
    def alive(self) -> bool:
        return not self.dead

    def wait_gone(self, timeout: float = 30.0, **kw: Any) -> None:
        self.waited += 1

    def close(self) -> None:
        self.dead = True

    def request(self, op: str, args: dict[str, Any] | None = None, *, hwnd: int | None = None) -> dict[str, Any]:
        self.calls.append((op, args, hwnd))
        queue = self.script.get(op) or [{}]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            if getattr(item, "_kills", False):
                self.dead = True
            raise item
        if callable(item):
            return item()
        return dict(item)


def kills(exc: BaseException) -> BaseException:
    exc._kills = True        # type: ignore[attr-defined]
    return exc


class FakeBridge:
    """Entrega ``FakeHandle`` em sequência; registra ``get_handle``/``release``."""

    def __init__(self, handles: list[FakeHandle]) -> None:
        self.queue = list(handles)
        self.got: list[tuple[str, str]] = []
        self.found: list[tuple[str, str]] = []
        self.released: list[FakeHandle] = []

    def get_handle(self, letter: str, mode: str = "owner", *, python32: Any = None) -> FakeHandle:
        self.got.append((letter, mode))
        return self.queue.pop(0)

    def find_handle(self, letter: str, mode: str = "admin", *, python32: Any = None) -> FakeHandle | None:
        """Sem spawn: fila vazia = "nenhum worker vivo"."""
        self.found.append((letter, mode))
        return self.queue.pop(0) if self.queue else None

    def release(self, handle: FakeHandle) -> None:
        self.released.append(handle)
        handle.users -= 1


CONNECTED = {"connected": True, "rc": 0, "already": False, "rows": 24, "cols": 80, "host_cp": 1140,
             "long_name": "BRAZIL_D", "type": "3270", "eab": False, "hwnd": 0x1000, "hwnd_pid": 4242}
NOT_YET = {"connected": False, "rc": 1, "already": False}
OWN_PID = os.getpid()
