"""Fotografia da tela (B.5): ``Cursor``, ``Field``, ``OIA`` e ``Screen`` — dados puros, sem I/O.

O texto de ``Screen`` já chega mascarado pelo worker (campos não exibidos viram espaços, D.6). A imagem da linha de
status do OIA (``raw[1:81]``) usa códigos internos do emulador: **nunca** a decodifique como texto.

``repr()`` de ``Screen``/``Field`` nunca mostra o conteúdo da tela (vai para tracebacks, saídas do pytest e logs com
``%r``; uma tela com ``hidden_unknown=True`` pode ter conteúdo oculto não mascarado): para ver a tela use ``print``.

P-0 entrega as dataclasses (inclusive ``Field.end``, que depende da geometria guardada em ``Field.rows/cols``),
``OIA.from_raw`` e ``Screen.from_wire``; os métodos de consulta e formatação de ``Screen`` (``line``, ``get``,
``find``, ``table``, ``to_text``…) são do pacote P-A.
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING, Any, NamedTuple
from collections.abc import Mapping

from .errors import ProtocolError

import time

if TYPE_CHECKING:
    import re

OIA_SIZE = 103
_P_A = "pendente: implementação do pacote P-A (núcleo puro)"


class Cursor(NamedTuple):
    """Posição (linha, coluna), 1-based."""

    row: int
    col: int

    def __str__(self) -> str:
        return f"({self.row},{self.col})"


@dataclass(frozen=True, slots=True)
class Field:
    """Campo 3270 da tela (só em ``snapshot(fields=True)``).

    ``rows``/``cols`` (só por nome, fora do ``repr`` e da comparação) guardam a geometria da tela para ``end``;
    ``Screen.from_wire`` sempre os preenche. Num ``Field`` criado à mão sem ``rows``, ``end`` não dá a volta na tela.
    """

    start: Cursor        # 1ª posição de dados (logo após o byte de atributo)
    length: int          # WD_FindFieldLength (0 se rc 28)
    attribute: int       # byte bruto 0xC0..0xFF
    text: str = dc_field(repr=False)   # do snapshot; campo oculto => espaços (nunca no repr)
    rows: int | None = dc_field(default=None, kw_only=True, repr=False, compare=False)
    cols: int = dc_field(default=80, kw_only=True, repr=False, compare=False)

    @property
    def row(self) -> int:
        return self.start.row

    @property
    def col(self) -> int:
        return self.start.col

    @property
    def end(self) -> Cursor:
        """Última posição de dados do campo (inclusive). Como no 3270, o campo dá a volta do fim da tela para o
        início; campo de tamanho 0 -> ``start``."""
        cols = self.cols
        last = (self.start.row - 1) * cols + (self.start.col - 1) + max(self.length, 1) - 1   # 0-based
        if self.rows:
            last %= self.rows * cols
        return Cursor(last // cols + 1, last % cols + 1)

    @property
    def protected(self) -> bool:
        return bool(self.attribute & 0x20)

    @property
    def numeric(self) -> bool:
        return bool(self.attribute & 0x10)

    @property
    def hidden(self) -> bool:
        """Campo não exibido (senha): intensidade ``11``."""
        return (self.attribute >> 2) & 3 == 3

    @property
    def intensified(self) -> bool:
        return (self.attribute >> 2) & 3 == 2

    @property
    def modified(self) -> bool:
        """MDT ligado (o campo será enviado ao host na próxima AID)."""
        return bool(self.attribute & 0x01)


@dataclass(frozen=True, slots=True)
class OIA:
    """Operator Information Area (103 bytes de ``WD_CopyOIA``).

    Só "G8 != 0 = teclado bloqueado" e "G1 & 0x30 = online" decidem algo; os bits finos de G8, G7 (insert) e G12
    (erro de comunicação) ficam fora da v2.0 até serem validados ao vivo.
    """

    raw: bytes

    def __post_init__(self) -> None:
        if isinstance(self.raw, (bytearray, memoryview)):
            object.__setattr__(self, "raw", bytes(self.raw))
        if not isinstance(self.raw, bytes):
            raise TypeError(f"OIA.raw deve ser bytes (recebi {type(self.raw).__name__})")
        if len(self.raw) != OIA_SIZE:
            raise ValueError(f"o OIA tem {OIA_SIZE} bytes (recebi {len(self.raw)})")

    @classmethod
    def from_raw(cls, raw: bytes) -> OIA:
        """``ValueError`` se ``len(raw) != 103``; ``TypeError`` se não for ``bytes``/``bytearray``/``memoryview``."""
        if not isinstance(raw, (bytes, bytearray, memoryview)):    # bytes(103) seria um OIA de zeros
            raise TypeError(f"OIA.from_raw espera bytes (recebi {type(raw).__name__})")
        return cls(bytes(raw))

    @property
    def format(self) -> int:
        """1 = 3270."""
        return self.raw[0]

    @property
    def group1(self) -> int:
        """G1: posse da tela / online (0x10 = LU-LU, visto ao vivo; 0x20 = SSCP-LU)."""
        return self.raw[81]

    @property
    def inhibit(self) -> bytes:
        """G8 (5 bytes): entrada inibida."""
        return self.raw[88:93]

    @property
    def online(self) -> bool:
        return (self.group1 & 0x30) != 0

    @property
    def keyboard_locked(self) -> bool:
        return any(self.inhibit)

    @property
    def ready(self) -> bool:
        return self.online and not self.keyboard_locked

    def describe(self) -> str:
        """``"teclado livre"`` | ``"teclado bloqueado (G8=00 20 00 00 00)"`` | ``"sem conexão com o host"``."""
        if not self.online:
            return "sem conexão com o host"
        if self.keyboard_locked:
            return "teclado bloqueado (G8=" + " ".join(f"{b:02X}" for b in self.inhibit) + ")"
        return "teclado livre"


def _bad(detail: str, terminal: str) -> ProtocolError:
    return ProtocolError.make(L=terminal or "?", detalhe=detail, terminal=terminal or None)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _position(value: Any, rows: int, cols: int, terminal: str, what: str) -> Cursor:
    """``[row, col]`` (formato de wire) ou posição linear 1-based -> ``Cursor`` dentro da tela."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(_int(v) for v in value):
        row, col = int(value[0]), int(value[1])
        if 1 <= row <= rows and 1 <= col <= cols:
            return Cursor(row, col)
    elif _int(value) and 1 <= value <= rows * cols:
        return Cursor((value - 1) // cols + 1, (value - 1) % cols + 1)
    raise _bad(f"{what} inválido no snapshot", terminal)


@dataclass(frozen=True, slots=True)
class Screen:
    """Uma fotografia da tela (texto + cursor + OIA), tirada pelo worker numa única op ``snapshot``.

    ``repr()`` é um resumo sem o texto (o ``__repr__`` abaixo substitui o gerado pelo ``dataclass``).
    """

    terminal: str
    rows: int
    cols: int
    text: str                         # exatamente rows*cols caracteres, já mascarado pelo worker (D.6)
    cursor: Cursor
    oia: OIA                          # sempre presente (CopyOIA falhou => exceção)
    host_busy: bool                   # CopyPS devolveu rc 4/5
    hidden_unknown: bool              # a máscara não pôde ser calculada: não registre/anexe esta tela
    fields: tuple[Field, ...] | None  # só com snapshot(fields=True)
    taken_at: float                   # time.time()
    match: Cursor | str | None = None  # preenchido pelas esperas

    @classmethod
    def from_wire(cls, d: Mapping[str, Any], terminal: str, *, rows: int | None = None,
                  cols: int | None = None) -> Screen:
        """Resultado da op ``snapshot`` (``{text, cursor, oia, busy, hidden_unknown, fields?}``) -> ``Screen``.

        A geometria vem de ``rows``/``cols`` (do ``ConnectResult``), senão de ``d["rows"]``/``d["cols"]``, senão é
        inferida com 80 colunas (todos os perfis conhecidos têm 80). Campos ocultos são apagados também aqui
        (defesa em profundidade: o worker já mascara). Formato inválido -> ``ProtocolError``.
        """
        if not isinstance(d, Mapping):
            raise _bad("snapshot sem formato de objeto", terminal)
        text = d.get("text")
        if not isinstance(text, str) or not text:
            raise _bad("texto ausente no snapshot", terminal)
        rows = rows if rows is not None else d.get("rows")
        cols = cols if cols is not None else d.get("cols")
        if rows is None and cols is None and len(text) % 80 == 0:
            rows, cols = len(text) // 80, 80
        elif rows is None and _int(cols) and cols > 0 and len(text) % cols == 0:
            rows = len(text) // cols
        elif cols is None and _int(rows) and rows > 0 and len(text) % rows == 0:
            cols = len(text) // rows
        if not (_int(rows) and _int(cols) and rows > 0 and cols > 0 and rows * cols == len(text)):
            raise _bad(f"geometria {rows}x{cols} não confere com {len(text)} caracteres", terminal)
        size = rows * cols
        cursor = _position(d.get("cursor"), rows, cols, terminal, "cursor")
        oia_hex = d.get("oia")
        try:
            oia = OIA.from_raw(bytes.fromhex(oia_hex)) if isinstance(oia_hex, str) else None
        except ValueError:
            oia = None
        if oia is None:
            raise _bad("OIA inválido no snapshot", terminal)
        busy = d.get("busy")
        hidden_unknown = d.get("hidden_unknown")      # obrigatório (C.4): ausente NUNCA vira "máscara conhecida"
        if not isinstance(busy, bool) or not isinstance(hidden_unknown, bool):
            raise _bad("busy/hidden_unknown ausentes ou inválidos no snapshot", terminal)
        fields: tuple[Field, ...] | None = None
        raw_fields = d.get("fields")
        if raw_fields is not None:
            if not isinstance(raw_fields, list):
                raise _bad("fields inválido no snapshot", terminal)
            chars = list(text)
            specs = []
            for item in raw_fields:
                if not isinstance(item, Mapping):
                    raise _bad("campo inválido no snapshot", terminal)
                start = _position(item.get("start"), rows, cols, terminal, "início de campo")
                length, attr = item.get("length"), item.get("attr")
                if not (_int(length) and length >= 0 and _int(attr) and 0 <= attr <= 0xFF):
                    raise _bad("campo inválido no snapshot", terminal)
                first = (start.row - 1) * cols + start.col          # posição linear 1-based
                span = [(first - 1 + k) % size for k in range(min(length, size))]
                specs.append((start, length, attr, span))
                if (attr >> 2) & 3 == 3:                             # campo oculto: apaga o conteúdo
                    for k in span:
                        chars[k] = " "
            text = "".join(chars)
            fields = tuple(Field(start, length, attr, "".join(text[k] for k in span), rows=rows, cols=cols)
                           for start, length, attr, span in specs)
        taken_at = d.get("taken_at")
        if not isinstance(taken_at, (int, float)) or isinstance(taken_at, bool):
            taken_at = time.time()
        return cls(terminal=terminal, rows=rows, cols=cols, text=text, cursor=cursor, oia=oia, host_busy=busy,
                   hidden_unknown=hidden_unknown, fields=fields, taken_at=float(taken_at))

    def __repr__(self) -> str:
        """Resumo SEM o conteúdo da tela: ``<Screen D 24x80 · cursor (22,56) · teclado livre>``."""
        try:
            parts = [f"<Screen {self.terminal} {self.rows}x{self.cols}", f"cursor {self.cursor}",
                     self.oia.describe()]
            if self.host_busy:
                parts.append("host ocupado")
            if self.hidden_unknown:
                parts.append("máscara desconhecida")
            if self.fields is not None:
                parts.append(f"{len(self.fields)} campo" + ("" if len(self.fields) == 1 else "s"))
            if self.match is not None:
                parts.append(f"match={self.match!r}")
            return " · ".join(parts) + ">"
        except Exception:                             # repr nunca pode falhar (tracebacks, depuradores)
            return "<Screen>"

    # ------------------------------------------------------------------ consulta e formatação (P-A)

    @property
    def lines(self) -> list[str]:
        raise NotImplementedError(f"Screen.lines: {_P_A}")

    def line(self, row: int) -> str:
        raise NotImplementedError(f"Screen.line: {_P_A}")

    def get(self, row: int, col: int, length: int) -> str:
        raise NotImplementedError(f"Screen.get: {_P_A}")

    def find(self, text: str | re.Pattern[str], *, case: bool = True) -> Cursor | None:
        raise NotImplementedError(f"Screen.find: {_P_A}")

    def contains(self, text: str, row: int | None = None, col: int | None = None) -> bool:
        raise NotImplementedError(f"Screen.contains: {_P_A}")

    def __contains__(self, text: str) -> bool:
        raise NotImplementedError(f"Screen.__contains__: {_P_A}")

    def field_at(self, row: int, col: int) -> Field | None:
        raise NotImplementedError(f"Screen.field_at: {_P_A}")

    @property
    def input_fields(self) -> list[Field]:
        raise NotImplementedError(f"Screen.input_fields: {_P_A}")

    def table(self, first_row: int, last_row: int, columns: Mapping[str, tuple[int, int]], *, strip: bool = True,
              skip_blank: bool = True) -> list[dict[str, str]]:
        raise NotImplementedError(f"Screen.table: {_P_A}")

    def to_text(self, *, ruler: bool = True) -> str:
        raise NotImplementedError(f"Screen.to_text: {_P_A}")

    def __str__(self) -> str:
        raise NotImplementedError(f"Screen.__str__: {_P_A}")

    def _repr_html_(self) -> str:
        raise NotImplementedError(f"Screen._repr_html_: {_P_A}")
