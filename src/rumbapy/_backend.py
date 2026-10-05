"""Fronteira única entre a ``Session`` e o worker (C.5): o ``Backend`` espelha 1:1 a tabela ``_protocol.OPS``.

Implementações: ``WorkerBackend`` (este módulo, pacote P-D; pipe até o worker 32-bit) e ``InProcessBackend``
(``tests/fakes/fake_backend.py``, pacote P-B; ``Dispatcher`` do worker no próprio processo sobre o ``FakeEhllapi``).
Toda implementação levanta apenas subclasses de ``rumbapy.RumbaError``.

``WorkerBackend`` (C.8): cada op vai pelo ``WorkerHandle`` da ``_bridge``; se o transporte cair (``WorkerCrashed``,
``WorkerHung``, ``ProtocolError``), uma op **idempotente** é repetida 1x depois de reciclar o worker (``get_worker`` ->
handshake -> ``connect`` em laço por até 15 s tolerando rc 1 e rc 11 -> mesmo ``hwnd``, senão ``SessionReplaced``).
Nenhuma op N (``write``/``send``/``set_cursor``/``evict``/``shutdown``) é repetida: sobe com ``delivery="unknown"``.
Recusas do worker (ex.: leitura recusada para não expor conteúdo oculto, A.9 h) sobem na hora, sem laço.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from .errors import (ChannelHijacked, NotConnected, ParameterError, ProtocolError, SessionReplaced, TerminalInUse,
                     WorkerCrashed, WorkerHung)
from ._types import ConnectResult, FieldRead, ReadResult, SendResult, SessionEntry, SessionStatus
from . import _protocol

import threading
import logging
import time
import os

if TYPE_CHECKING:
    from ._bridge import Bridge, WorkerHandle
    from .screen import Screen

logger = logging.getLogger(__name__)


@runtime_checkable
class Backend(Protocol):
    """Operações do worker vistas pelo cliente. ``connect`` usa ``letter``; o laço de tentativas é do chamador."""

    letter: str

    def ping(self) -> dict: ...

    def sessions(self) -> list[SessionEntry]: ...

    def connect(self) -> ConnectResult: ...                          # UMA tentativa (rc 1 => connected=False)

    def disconnect(self) -> None: ...

    def status(self) -> SessionStatus: ...

    def snapshot(self, *, fields: bool = False) -> Screen: ...

    def read(self, row: int, col: int, length: int) -> ReadResult: ...        # (text, busy, nul_at, nul_tail)

    def read_field(self, row: int, col: int) -> FieldRead: ...                # (text, start, length, attr, nul_tail)

    def write(self, row: int, col: int, text: str, *, secret: bool) -> None: ...   # text já no formato de wire

    def send(self, chunks: Sequence[str], *, secret: bool) -> SendResult: ...      # (chunks_sent, oia_after)

    def set_cursor(self, row: int, col: int) -> None: ...

    def evict(self, reason: str) -> dict: ...                        # conexão admin própria

    def shutdown(self, reason: str) -> None: ...

    # só do cliente (fora de OPS)
    def bind_instance(self, hwnd: int | None) -> None: ...           # hwnd enviado em toda op com checks_hwnd

    def release(self) -> None: ...                                   # fecha a conexão de lease (não é op)


CLIENT_ONLY_METHODS: frozenset[str] = frozenset({"bind_instance", "release"})

RECONNECT_S = 15.0          # C.8: connect em laço depois de reciclar
RECONNECT_POLL_S = 0.5
_RECYCLE = (WorkerCrashed, WorkerHung, ProtocolError)
SLICE_S = 0.1
NOTHING_EVICTED: dict[str, Any] = {"evicted_owner": None, "was_connected": False}   # evict sem worker vivo


@contextmanager
def _held(lock: Any) -> Iterator[None]:
    """Adquire em fatias de 100 ms: no Windows um ``acquire`` bloqueante ignora o Ctrl+C (D.8, E27)."""
    while not lock.acquire(timeout=SLICE_S):
        pass
    try:
        yield
    finally:
        lock.release()


class WorkerBackend:
    """``Backend`` sobre o processo auxiliar 32-bit (pacote P-D).

    ``WorkerBackend(letra, mode="owner"|"admin", python32=None)`` é preguiçoso: o worker só é procurado/lançado na 1ª
    op. ``_bridge.get_worker`` devolve um já amarrado ao handle (D.8 executada na hora). ``release()`` devolve o
    handle; a próxima op pega outro (e retoma o lease, C.7).
    """

    def __init__(self, letter: str, *, mode: str = "owner", python32: str | os.PathLike[str] | None = None,
                 bridge: Bridge | None = None, handle: WorkerHandle | None = None,
                 clock: Any = time.monotonic, sleep: Any = time.sleep) -> None:
        if mode not in _protocol.MODES:
            raise ParameterError.make(detalhe=f"Modo inválido: {mode!r} (use owner ou admin).")
        self.letter = letter
        self.mode = mode
        self.python32 = python32
        self._bridge = bridge
        self._handle = handle
        self._hwnd: int | None = None
        self._bound = False
        self._conn_pid: int | None = None        # PID do worker em que o último connect deu certo
        self._rows: int | None = None
        self._cols: int | None = None
        self._lock = threading.RLock()
        self._clock = clock
        self._sleep = sleep

    def __repr__(self) -> str:
        pid = self.worker_pid
        return f"<WorkerBackend {self.letter} {self.mode}{f' worker={pid}' if pid else ''}>"

    # ----------------------------------------------------------------- handle
    @property
    def bridge(self) -> Bridge:
        if self._bridge is None:
            from . import _bridge                # tardio: _bridge importa este módulo em get_worker

            self._bridge = _bridge.default_bridge()
        return self._bridge

    @property
    def handle(self) -> WorkerHandle | None:
        return self._handle

    @property
    def worker_pid(self) -> int | None:
        handle = self._handle
        return handle.pid if handle is not None else None

    @property
    def hwnd(self) -> int | None:
        return self._hwnd

    def _current(self, op: str | None = None) -> WorkerHandle:
        """Handle vivo. Se o worker mudou desde o ``connect`` (reciclagem, worker que caiu numa op N), uma op que
        precisa de conexão primeiro reconecta e confere o hwnd (C.8) — nunca repete entrada nenhuma."""
        handle = self._handle
        if handle is not None and handle.evicted:
            return handle                        # despejado: cada op levanta SessionReplaced (C.6.2)
        if handle is None or not handle.alive:
            if handle is not None:
                self._drop(handle)
            handle = self._handle = self.bridge.get_handle(self.letter, self.mode, python32=self.python32)
        # também com o handle vivo: uma reciclagem por op sem conexão (ping/sessions) troca o worker sem connect
        if (op is not None and self.mode == "owner" and _protocol.OPS[op].needs_connection
                and self._conn_pid is not None and handle.pid != self._conn_pid):
            self._ensure_instance(handle)
        return handle

    def _ensure_instance(self, handle: WorkerHandle) -> None:
        result = self._reconnect(handle)
        self._conn_pid = handle.pid
        if self._bound and result.hwnd != self._hwnd:
            logger.warning("reciclagem letter=%s: hwnd mudou (%s -> %s)", self.letter, self._hwnd, result.hwnd)
            raise SessionReplaced.make(L=self.letter, terminal=self.letter,
                                       details={"old_hwnd": self._hwnd, "new_hwnd": result.hwnd})

    def _drop(self, handle: WorkerHandle) -> None:
        """Devolve um handle morto; se o worker dele anunciou a saída, espera o PID sumir (senão o próximo
        ``get_worker`` encontraria o moribundo)."""
        if self._handle is handle:
            self._handle = None
        self.bridge.release(handle)
        handle.wait_gone(clock=self._clock, sleep=self._sleep)

    def _request(self, handle: WorkerHandle, op: str, args: dict[str, Any] | None) -> dict[str, Any]:
        hwnd = self._hwnd if _protocol.OPS[op].checks_hwnd else None
        return handle.request(op, args, hwnd=hwnd)

    def _call(self, op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = _protocol.OPS[op]
        with _held(self._lock):
            handle = self._current(op)
            try:
                return self._request(handle, op, args)
            except ChannelHijacked:
                raise                            # erro final: não recicla (C.8)
            except _RECYCLE as exc:
                if not spec.idempotent:
                    raise                        # op N nunca é repetida (B.1)
                logger.warning("retry op=%s after=%s letter=%s", op, type(exc).__name__, self.letter)
                handle = self._recycle(op)
                return self._request(handle, op, args)

    def _recycle(self, op: str) -> WorkerHandle:
        """C.8: worker novo (D.8) -> handshake -> (se a op precisa de conexão) connect em laço -> mesmo hwnd."""
        old = self._handle
        if old is not None:
            self._drop(old)
        handle = self._current()
        if self.mode == "owner" and _protocol.OPS[op].needs_connection:
            self._ensure_instance(handle)
        return handle

    def _reconnect(self, handle: WorkerHandle) -> ConnectResult:
        deadline = self._clock() + RECONNECT_S
        stuck = False
        while True:
            try:
                result = ConnectResult.from_wire(handle.request("connect", {"letter": self.letter}))
            except TerminalInUse as exc:
                if exc.rc != 11:
                    raise
                stuck = True                     # conexão do worker anterior presa após os._exit (R8)
            else:
                if result.connected:
                    self._geometry(result)
                    return result
                stuck = False
            if self._clock() >= deadline:
                if stuck:
                    raise TerminalInUse.make("rc_stuck", L=self.letter, rc=11, function="WD_ConnectPS",
                                             terminal=self.letter)
                raise NotConnected.make(L=self.letter, rc=1, function="WD_ConnectPS", terminal=self.letter)
            self._sleep(RECONNECT_POLL_S)

    def _geometry(self, result: ConnectResult) -> None:
        if result.connected:
            self._rows, self._cols = result.rows, result.cols

    # ----------------------------------------------------------------- Backend (C.5)
    def ping(self) -> dict:
        return self._call("ping")

    def sessions(self) -> list[SessionEntry]:
        entries = self._call("sessions").get("sessions")
        if not isinstance(entries, list):
            raise ProtocolError.make(L=self.letter, detalhe="sessions sem lista", terminal=self.letter)
        return [SessionEntry.from_wire(e) for e in entries]

    def connect(self) -> ConnectResult:
        with _held(self._lock):
            result = ConnectResult.from_wire(self._call("connect", {"letter": self.letter}))
            if result.connected:
                self._geometry(result)
                self._conn_pid = self.worker_pid
                if not self._bound:              # A.9 i: toda op com hwnd já sai amarrada à instância conectada
                    self.bind_instance(result.hwnd)
            return result

    def disconnect(self) -> None:
        with _held(self._lock):
            self._call("disconnect")
            self._conn_pid = None

    def status(self) -> SessionStatus:
        return SessionStatus.from_wire(self._call("status"))

    def snapshot(self, *, fields: bool = False) -> Screen:
        from .screen import Screen

        result = self._call("snapshot", {"fields": fields})
        return Screen.from_wire(result, self.letter, rows=self._rows, cols=self._cols)

    def read(self, row: int, col: int, length: int) -> ReadResult:
        return ReadResult.from_wire(self._call("read", {"row": row, "col": col, "length": length}))

    def read_field(self, row: int, col: int) -> FieldRead:
        return FieldRead.from_wire(self._call("read_field", {"row": row, "col": col}))

    def write(self, row: int, col: int, text: str, *, secret: bool) -> None:
        self._call("write", {"row": row, "col": col, "text": text, "secret": secret})

    def send(self, chunks: Sequence[str], *, secret: bool) -> SendResult:
        return SendResult.from_wire(self._call("send", {"chunks": list(chunks), "secret": secret}))

    def set_cursor(self, row: int, col: int) -> None:
        self._call("set_cursor", {"row": row, "col": col})

    def _existing(self) -> WorkerHandle | None:
        """Handle atual se vivo; senão um worker JÁ vivo da letra (``find_handle``), nunca um recém-lançado (E.4
        passo 1, A.1.7): subir um worker — que carrega a DLL — só para pará-lo/despejar ninguém não faz sentido."""
        handle = self._handle
        if handle is not None and (handle.evicted or handle.alive):
            return handle
        if handle is not None:
            self._drop(handle)
        handle = self._handle = self.bridge.find_handle(self.letter, self.mode, python32=self.python32)
        return handle

    def evict(self, reason: str) -> dict:
        """Sempre por uma conexão admin própria (C.5), mesmo num backend ``owner``. Sem worker vivo: ninguém a
        despejar (``{"evicted_owner": None, "was_connected": False}``), sem subir worker."""
        if self.mode == "admin":
            with _held(self._lock):
                handle = self._existing()
                if handle is None:
                    return dict(NOTHING_EVICTED)
                return self._request(handle, "evict", {"reason": reason})
        admin = self.bridge.find_handle(self.letter, "admin", python32=self.python32)
        if admin is None:
            return dict(NOTHING_EVICTED)
        try:
            return admin.request("evict", {"reason": reason})
        finally:
            self.bridge.release(admin)

    def shutdown(self, reason: str) -> None:
        """Para o worker da letra, se houver um vivo (sem worker vivo: nada a fazer, nunca sobe um)."""
        with _held(self._lock):
            handle = self._existing()
            if handle is None:
                logger.info("shutdown letter=%s: nenhum processo auxiliar vivo", self.letter)
                return
            try:
                handle.request("shutdown", {"reason": reason})
            except WorkerCrashed:
                pass                             # saiu antes de responder: era o pedido
            finally:
                self._handle = None
                self.bridge.release(handle)

    # ----------------------------------------------------------------- só do cliente
    def bind_instance(self, hwnd: int | None) -> None:
        self._hwnd = hwnd
        self._bound = True

    def release(self) -> None:
        with _held(self._lock):
            handle, self._handle = self._handle, None
        if handle is not None:
            self.bridge.release(handle)


class ExistingAdmin:
    """Conexão admin com um worker JÁ vivo da letra, para o ``close`` (E.4 passo 1: "não exige lease nem DLL").

    Só ``evict``/``sessions``/``release``, direto no ``WorkerHandle``: nunca recicla nem sobe outro worker (um
    ``WorkerBackend`` repetiria ``sessions`` depois de reciclar, o que carregaria a DLL só para conferir um close).
    Use ``ExistingAdmin.find(letra)``: ``None`` se não houver worker vivo."""

    def __init__(self, letter: str, bridge: Bridge, handle: WorkerHandle) -> None:
        self.letter = letter
        self._bridge = bridge
        self._handle: WorkerHandle | None = handle

    @classmethod
    def find(cls, letter: str, *, python32: str | os.PathLike[str] | None = None,
             bridge: Bridge | None = None) -> ExistingAdmin | None:
        if bridge is None:
            from . import _bridge                # tardio: _bridge importa este módulo

            bridge = _bridge.default_bridge()
        handle = bridge.find_handle(letter, "admin", python32=python32)
        return None if handle is None else cls(letter, bridge, handle)

    def __repr__(self) -> str:
        handle = self._handle
        return f"<ExistingAdmin {self.letter}{f' worker={handle.pid}' if handle is not None else ''}>"

    def _live(self) -> WorkerHandle:
        handle = self._handle
        if handle is None or not handle.alive:
            raise WorkerCrashed.make(L=self.letter, terminal=self.letter)
        return handle

    def evict(self, reason: str) -> dict:
        return self._live().request("evict", {"reason": reason})

    def sessions(self) -> list[SessionEntry]:
        entries = self._live().request("sessions").get("sessions")
        if not isinstance(entries, list):
            raise ProtocolError.make(L=self.letter, detalhe="sessions sem lista", terminal=self.letter)
        return [SessionEntry.from_wire(e) for e in entries]

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._bridge.release(handle)
