"""Máscara de conteúdo oculto (D.6; R14, C4, I15). Puro: recebe o ``Hllapi`` por parâmetro e não importa ``ctypes``.

O conteúdo de campos não exibidos (atributo com bits de intensidade ``11``) e os spans ``secret`` escritos pelo
cliente **não atravessam o pipe**: viram ``0x20`` antes do ``decode``.

Quando reenumerar (antes de responder ``snapshot``, ``read``, ``read_field``): cache vazio (nunca enumerado, falhou ou
``connect`` desde a última); ``aid_seq != enum_aid_seq`` **e** teclado livre agora (o host respondeu à AID; os spans
``secret`` caem aí); em ``snapshot``, ``sha1(PS bruto) != last_hash`` **e** ``own_mut == False`` (escrita espontânea
do host). Depois de ``write``/``send`` sem AID, ``own_mut=True``: o próximo ``snapshot`` só atualiza ``last_hash``.

Enumeração que falha (rc inesperado) deixa ``hidden=None`` (``hidden_unknown``), mas os spans ocultos da última
enumeração boa continuam sendo aplicados (``last_hidden``: mascarar demais é seguro, de menos não). Para ``read`` com a
máscara desconhecida, o ``Engine`` confere os campos da área pedida com :func:`hidden_in_span` e, se nem isso der,
recusa a leitura em vez de devolver o bruto.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import logging

logger = logging.getLogger(__name__)

THIS_FIELD = b"T "
NEXT_FIELD = b"N "
MAX_FIELDS = 1000
RC_OK, RC_NO_FIELD, RC_ZERO_LENGTH = 0, 24, 28
SPACE = 0x20

FieldSpec = tuple[int, int, int]           # (início dos dados, tamanho, atributo); posições lineares 1-based
Span = tuple[int, int]                     # (posição, tamanho)


class FieldSource(Protocol):
    def find_field_pos(self, pos: int, code: bytes) -> tuple[int, int]: ...

    def find_field_len(self, pos: int, code: bytes) -> tuple[int, int]: ...

    def field_attr(self, pos: int) -> tuple[int, int]: ...


def is_hidden(attr: int) -> bool:
    """Atributo 3270 com intensidade ``11`` (não exibido — campos de senha)."""
    return (int(attr) >> 2) & 3 == 3


def is_protected(attr: int) -> bool:
    return bool(int(attr) & 0x20)


def hidden_in_span(hll: FieldSource, pos: int, length: int, size: int) -> tuple[str, int, list[Span]]:
    """Spans ocultos que tocam ``[pos, pos+length)`` (posições lineares 1-based, com volta na tela), conferidos campo a
    campo (``QueryFieldAttribute`` + ``FindFieldPosition``/``FindFieldLength`` com ``"T "``; ``"N "`` para pular o que
    sobra entre os dados de um campo e o próximo atributo — mascarado se o atributo do dono for oculto).

    Devolve ``(função, rc, spans)``: ``rc == 0`` = conferido (``[]`` também para PS não formatado); senão a função e o
    rc que impediram a conferência (o chamador recusa a leitura).
    """
    out: list[Span] = []
    k = 0
    while k < length:
        p = (pos - 1 + k) % size + 1
        rc, attr = hll.field_attr(p)
        if rc == RC_NO_FIELD:
            return "WD_QueryFieldAttribute", RC_OK, []            # PS não formatado: nada oculto
        if rc != RC_OK:
            return "WD_QueryFieldAttribute", rc, out
        step = 1
        prc, start = hll.find_field_pos(p, THIS_FIELD)
        lrc, flen = hll.find_field_len(p, THIS_FIELD)
        if prc not in (RC_OK, RC_ZERO_LENGTH):
            return "WD_FindFieldPosition", prc, out
        if lrc not in (RC_OK, RC_ZERO_LENGTH):
            return "WD_FindFieldLength", lrc, out
        if prc == RC_OK and lrc == RC_OK and flen > 0 and 1 <= start <= size:
            end = start + flen - 1                                 # pode passar do fim da tela (campo que dá a volta)
            here = p if p >= start else p + size
            if here <= end:
                step = end - here + 1
                if is_hidden(attr):
                    out.append((start, flen))
            else:                                                  # byte de atributo / sobra depois dos dados
                nrc, nxt = hll.find_field_pos(p, NEXT_FIELD)
                if nrc == RC_OK and 1 <= nxt <= size:
                    step = max(1, (nxt - 1 - p) % size)            # até o byte de atributo do próximo campo
                if is_hidden(attr):
                    out.append((p, step))
        elif is_hidden(attr):
            out.append((p, 1))
        k += step
    return "", RC_OK, out


def enumerate_fields(hll: FieldSource) -> list[FieldSpec] | None:
    """Campos da tela atual (D.6). ``[]`` = PS não formatado (rc 24); ``None`` = rc inesperado (máscara desconhecida)."""
    rc, start = hll.find_field_pos(1, THIS_FIELD)
    if rc == RC_NO_FIELD:
        return []
    if rc != RC_OK:
        logger.warning("enumeração de campos: FindFieldPosition rc=%d; máscara desconhecida", rc)
        return None
    out: list[FieldSpec] = []
    seen: set[int] = set()
    for _ in range(MAX_FIELDS):
        if start in seen:
            break
        seen.add(start)
        rc, length = hll.find_field_len(start, THIS_FIELD)
        if rc == RC_ZERO_LENGTH:
            length = 0
        elif rc != RC_OK:
            logger.warning("enumeração de campos: FindFieldLength rc=%d; máscara desconhecida", rc)
            return None
        rc, attr = hll.field_attr(start)
        if rc != RC_OK:
            logger.warning("enumeração de campos: QueryFieldAttribute rc=%d; máscara desconhecida", rc)
            return None
        out.append((start, length, attr))
        rc, nxt = hll.find_field_pos(start, NEXT_FIELD)
        if rc == RC_NO_FIELD:
            break
        if rc != RC_OK:
            logger.warning("enumeração de campos: FindFieldPosition(N) rc=%d; máscara desconhecida", rc)
            return None
        start = nxt
    return sorted(out)                     # a busca começa no campo que contém a posição 1 (pode ser o último)


class MaskState:
    """Estado da máscara de uma conexão (``hidden``/``secret``/``enum_aid_seq``/``last_hash``/``own_mut``)."""

    def __init__(self, size: int = 1920) -> None:
        self.size = size
        self.hidden: list[Span] | None = None      # None = desconhecido
        self.last_hidden: list[Span] = []          # spans ocultos da última enumeração boa (aplicados se desconhecido)
        self.secret: list[Span] = []
        self.fields: list[FieldSpec] | None = None
        self.valid = False                         # cache preenchido por uma enumeração bem-sucedida
        self.enum_aid_seq = 0
        self.last_hash: bytes | None = None
        self.own_mut = False
        self.enumerations = 0

    # ----------------------------------------------------------------- eventos
    def invalidate(self, size: int | None = None) -> None:
        """``connect``/troca de instância: tudo desconhecido de novo (spans ``secret`` também caem).

        ``last_hidden`` fica (só cai se a geometria mudar): enquanto a máscara for desconhecida, mascarar demais é seguro.
        """
        if size is not None and size != self.size:
            self.last_hidden = []
        if size is not None:
            self.size = size
        self.hidden = None
        self.fields = None
        self.valid = False
        self.secret = []
        self.last_hash = None
        self.own_mut = False

    def mark_unknown(self, also_hidden: list[Span] | None = None) -> None:
        """A tela mudou durante as enumerações (D.6): máscara desconhecida (``hidden_unknown``), e ``last_hidden``
        passa a ser a união dos spans vistos (mascarar demais é seguro). Os spans ``secret`` ficam."""
        merged = list(self.last_hidden)
        for span in also_hidden or []:
            if span not in merged:
                merged.append(span)
        self.last_hidden = merged
        self.hidden = None
        self.fields = None
        self.valid = False

    def add_secret(self, pos: int, length: int) -> None:
        if length > 0:
            self.secret.append((pos, length))

    def own_mutation(self) -> None:
        self.own_mut = True

    def on_aid(self) -> None:
        """Uma AID foi enviada: a próxima leitura com teclado livre reenumera (quem conta é ``aid_seq``)."""
        self.own_mut = False

    def note_snapshot(self, ps_hash: bytes) -> None:
        self.last_hash = ps_hash
        self.own_mut = False

    @property
    def hidden_unknown(self) -> bool:
        return self.hidden is None

    # ----------------------------------------------------------------- decisão
    def needs_enum(self, *, aid_seq: int, keyboard_free: Callable[[], bool] | bool,
                   ps_hash: bytes | None = None) -> tuple[bool, bool]:
        """(reenumerar?, a AID foi respondida?)."""
        if not self.valid:
            return True, aid_seq != self.enum_aid_seq and _free(keyboard_free)
        if aid_seq != self.enum_aid_seq and _free(keyboard_free):
            return True, True
        if ps_hash is not None and not self.own_mut and self.last_hash is not None and ps_hash != self.last_hash:
            return True, False
        return False, False

    def refresh(self, hll: FieldSource, *, aid_seq: int, keyboard_free: Callable[[], bool] | bool,
                ps_hash: bytes | None = None) -> bool:
        """Reenumera se preciso (D.6). Devolve ``True`` se enumerou."""
        needed, answered = self.needs_enum(aid_seq=aid_seq, keyboard_free=keyboard_free, ps_hash=ps_hash)
        if not needed:
            return False
        if answered:
            self.secret = []                       # o host respondeu à AID: a tela dos spans secretos se foi
        fields = enumerate_fields(hll)
        self.enumerations += 1
        if fields is None:
            self.hidden = None
            self.fields = None
            self.valid = False
        else:
            self.fields = fields
            self.hidden = [(start, length) for start, length, attr in fields if length > 0 and is_hidden(attr)]
            self.last_hidden = list(self.hidden)
            self.valid = True
            if answered:                           # com a AID ainda sem resposta, a próxima leitura livre reenumera
                self.enum_aid_seq = aid_seq
        return True

    # ----------------------------------------------------------------- aplicação
    def spans(self) -> list[Span]:
        """``hidden ∪ secret``; com a máscara desconhecida, ``hidden`` = os spans da última enumeração boa."""
        hidden = self.hidden if self.hidden is not None else self.last_hidden
        return list(hidden) + list(self.secret)

    def apply(self, raw: bytes, pos: int = 1, extra: list[Span] | None = None) -> bytes:
        """Bytes em ``hidden ∪ secret`` (``∪ extra``) viram ``0x20``; ``raw`` começa na posição linear ``pos``."""
        spans = self.spans() + list(extra or [])
        if not spans or not raw:
            return bytes(raw)
        size = self.size
        out = bytearray(raw)
        n = len(out)
        for start, length in spans:
            for k in range(min(length, size)):
                p = (start - 1 + k) % size + 1
                i = (p - pos) % size
                if i < n:
                    out[i] = SPACE
        return bytes(out)

    def describe(self) -> dict[str, Any]:
        """Resumo para log/teste (nunca conteúdo)."""
        return {"hidden": None if self.hidden is None else len(self.hidden), "last_hidden": len(self.last_hidden),
                "secret": len(self.secret),
                "valid": self.valid, "enum_aid_seq": self.enum_aid_seq, "own_mut": self.own_mut}


def _free(keyboard_free: Callable[[], bool] | bool) -> bool:
    return bool(keyboard_free()) if callable(keyboard_free) else bool(keyboard_free)
