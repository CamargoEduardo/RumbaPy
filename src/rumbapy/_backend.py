"""Fronteira única entre a ``Session`` e o worker (C.5): o ``Backend`` espelha 1:1 a tabela ``_protocol.OPS``.

Implementações: ``WorkerBackend`` (este módulo, pacote P-D; pipe até o worker 32-bit) e ``InProcessBackend``
(``tests/fakes/fake_backend.py``, pacote P-B; ``Dispatcher`` do worker no próprio processo sobre o ``FakeEhllapi``).
Toda implementação levanta apenas subclasses de ``rumbapy.RumbaError``.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable
from collections.abc import Sequence

if TYPE_CHECKING:
    from ._types import ConnectResult, FieldRead, ReadResult, SendResult, SessionEntry, SessionStatus
    from .screen import Screen


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
