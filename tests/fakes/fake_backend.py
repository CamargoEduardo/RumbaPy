"""``InProcessBackend`` (J.3, C.5): o ``Dispatcher`` do worker rodando no próprio processo sobre o ``FakeEhllapi``.

``InProcessBackend(fake, letter)`` = ``Dispatcher(Engine(Hllapi(fake), MaskState()))``. Cada requisição e cada
resposta passam por ``_protocol.encode``/``decode`` (JSON de verdade, como no pipe) e ficam em ``messages`` (espião do
"pipe" para os testes de segurança: nenhum segredo pode aparecer ali). Implementa o ``Backend`` inteiro e só levanta
``RumbaError``.

``lease=True`` (e ``share=`` para outros backends do MESMO "worker") modela o que o ``WorkerServer`` faz com as
conexões: um só ``Engine`` (a conexão HLLAPI é do processo), um dono por vez (o 1º backend ``owner`` que fizer uma op
fica com o lease até ``release``; outro dono -> ``TerminalInUse``) e o ``evict`` de qualquer backend despeja o dono
(as ops dele passam a ``SessionReplaced`` até ele devolver o handle com ``release``; ``evicted_owner`` preenchido).
Sem ``lease`` (padrão) nada disso acontece: cada backend é independente, como antes.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rumbapy._types import ConnectResult, FieldRead, ReadResult, SendResult, SessionEntry, SessionStatus
from rumbapy._worker.dispatch import Dispatcher, JobCtx
from rumbapy._worker.hllapi import Hllapi, new_policy
from rumbapy.errors import ProtocolError, RumbaError, SessionReplaced, TerminalInUse
from rumbapy._worker.guard import GuardedDll
from rumbapy._worker.mask import MaskState
from rumbapy._worker.engine import Engine
from rumbapy.screen import Screen
from rumbapy import _protocol

import time
import os


class _Lease:
    """Estado do "servidor" compartilhado pelos backends de um mesmo worker simulado (``lease=True``)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.holder: InProcessBackend | None = None


class InProcessBackend:
    """``Backend`` sem processo auxiliar: mesmas validações, máscara, rc e erros do worker real."""

    def __init__(self, fake: Any, letter: str, *, guard: str = "none", share: InProcessBackend | None = None,
                 mode: str = "owner", lease: bool = False) -> None:
        self.letter = letter
        self.fake = fake
        self.guard = guard
        self.mode = mode
        self.evicted = False                        # despejado (lease=True): ops de dono -> SessionReplaced
        if share is not None:                       # mesmo worker: mesmo Engine/Dispatcher (conexão por processo)
            self.fake, self.guard, self.policy = share.fake, share.guard, share.policy
            self.hll, self.mask, self.engine, self.dispatcher = share.hll, share.mask, share.engine, share.dispatcher
            self._lease = share._lease
        else:
            dll = GuardedDll(fake, new_policy(guard)) if guard != "none" else fake
            self.policy = getattr(dll, "policy", None)
            self.hll = Hllapi(dll)
            self.mask = MaskState()
            self.engine = Engine(self.hll, self.mask, letter=letter, window_pid=self._window_pid)
            self.dispatcher = Dispatcher(self.engine, guard=guard)
            self._lease = _Lease(lease)
        self.messages: list[bytes] = []             # tudo que "trafegou" (requisições e respostas)
        self.replies: list[bytes] = []              # só o que veio do "worker" (respostas)
        self.calls: list[str] = []                  # ops pedidas, em ordem
        self.hwnd: int | None = None
        self.released = 0
        self._next_id = 1
        self._rows: int | None = None
        self._cols: int | None = None
        self._started = time.monotonic()

    # ----------------------------------------------------------------- transporte simulado
    def _window_pid(self, hwnd: int) -> int:
        for session in getattr(self.fake, "sessions", {}).values():
            if getattr(session, "hwnd", None) == hwnd:
                return int(getattr(session, "pid", 0) or 0)
        return 0

    def _call(self, op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self.calls.append(op)
        data = _protocol.encode(_protocol.make_request(rid, op, args, self.hwnd), letter=self.letter)
        self.messages.append(data)
        msg = _protocol.decode(data, letter=self.letter)
        mode = "admin" if _protocol.OPS[op].mode == "admin" else "owner"
        try:
            self._check_lease(op)
            rows, cols = self.dispatcher.geometry()
            request = _protocol.validate_request(msg, mode=mode, letter=self.letter, rows=rows, cols=cols)
            ctx = JobCtx(op, conn_id=0, hwnd=request.hwnd, id=rid)
            reply = _protocol.make_result(rid, self.dispatcher.handle(op, request.args, ctx))
        except RumbaError as exc:
            reply = _protocol.make_error(rid, exc)
        out = _protocol.encode(reply, letter=self.letter)
        self.messages.append(out)
        self.replies.append(out)
        return _protocol.unwrap_reply(_protocol.decode(out, letter=self.letter), rid, letter=self.letter)

    def _check_lease(self, op: str) -> None:
        """Como o ``WorkerServer`` (C.6.2): dono despejado -> ``SessionReplaced``; outro dono -> ``TerminalInUse``."""
        lease = self._lease
        if not lease.enabled or self.mode != "owner" or op in _protocol.ADMIN_OPS:
            return
        if self.evicted:
            raise SessionReplaced.make(L=self.letter, terminal=self.letter)
        if lease.holder is None:
            lease.holder = self
        elif lease.holder is not self:
            raise TerminalInUse.make("lease", L=self.letter, pid=os.getpid(), script="outro dono (dublê)",
                                     hora="--:--", terminal=self.letter)

    def traffic(self, *, replies_only: bool = False) -> str:
        """JSON trafegado, como texto (para procurar segredos); ``replies_only``: só o sentido worker -> cliente."""
        return "\n".join(m.decode("utf-8") for m in (self.replies if replies_only else self.messages))

    # ----------------------------------------------------------------- Backend
    def ping(self) -> dict:
        engine = self.engine
        return {"uptime_s": round(time.monotonic() - self._started, 3), "letter": self.letter, "state": "owned",
                "connected": engine.connected, "owner_pid": os.getpid(), "hwnd": engine.hwnd}

    def sessions(self) -> list[SessionEntry]:
        result = self._call("sessions")
        entries = result.get("sessions")
        if not isinstance(entries, list):
            raise ProtocolError.make(L=self.letter, detalhe="sessions sem lista", terminal=self.letter)
        return [SessionEntry.from_wire(e) for e in entries]

    def connect(self) -> ConnectResult:
        result = ConnectResult.from_wire(self._call("connect", {"letter": self.letter}))
        if result.connected:
            self._rows, self._cols = result.rows, result.cols
        return result

    def disconnect(self) -> None:
        self._call("disconnect")

    def status(self) -> SessionStatus:
        return SessionStatus.from_wire(self._call("status"))

    def snapshot(self, *, fields: bool = False) -> Screen:
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

    def evict(self, reason: str) -> dict:
        result = self._call("evict", {"reason": reason})
        lease = self._lease
        if lease.enabled and lease.holder is not None:
            lease.holder.evicted = True                 # o aviso "evicted" do pipe: a próxima op do dono vê
            lease.holder = None
            result["evicted_owner"] = {"pid": os.getpid(), "script": "dono (dublê)"}
        return result

    def shutdown(self, reason: str) -> None:
        self._call("shutdown", {"reason": reason})

    def bind_instance(self, hwnd: int | None) -> None:
        self.hwnd = hwnd

    def release(self) -> None:
        self.released += 1
        lease = self._lease
        if lease.enabled:
            if lease.holder is self:
                lease.holder = None
            self.evicted = False                        # o próximo get_backend é uma conexão nova
