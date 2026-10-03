"""Fotografia da tela (B.5): ``Cursor``, ``Field``, ``OIA`` e ``Screen`` — dados puros, sem I/O.

O texto de ``Screen`` já chega mascarado pelo worker (campos não exibidos viram espaços, D.6). A imagem da linha de
status do OIA (``raw[1:81]``) usa códigos internos do emulador: **nunca** a decodifique como texto.

``repr()`` de ``Screen``/``Field`` nunca mostra o conteúdo da tela (vai para tracebacks, saídas do pytest e logs com
``%r``; uma tela com ``hidden_unknown=True`` pode ter conteúdo oculto não mascarado): para ver a tela use ``print``.

Consultas de ``Screen`` usam coordenadas ``(row, col)`` 1-based e validam a posição contra a geometria da própria tela
(fora da tela -> ``InvalidPosition``; argumento de tipo errado -> ``ParameterError``). ``get`` e ``find`` trabalham
no texto linear (um trecho pode continuar na linha seguinte, como no 3270).
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from collections.abc import Mapping
from typing import Any, NamedTuple

from .errors import InvalidPosition, ParameterError, ProtocolError
from . import _codec

import time
import html
import re

OIA_SIZE = 103
_CURSOR_STYLE = "background:#ffd54f;color:#000"


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

    # ------------------------------------------------------------------ consulta

    def _offset(self, row: int, col: int) -> int:
        """(linha, coluna) validadas -> índice 0-based em ``text``."""
        return _codec.to_pos(row, col, self.rows, self.cols) - 1

    def _cursor_at(self, index: int) -> Cursor:
        return Cursor(index // self.cols + 1, index % self.cols + 1)

    @property
    def lines(self) -> list[str]:
        """As ``rows`` linhas da tela, cada uma com exatamente ``cols`` caracteres."""
        cols = self.cols
        return [self.text[i * cols:(i + 1) * cols] for i in range(self.rows)]

    def line(self, row: int) -> str:
        """Linha ``row`` (1-based) com ``cols`` caracteres; fora da tela -> ``InvalidPosition``."""
        start = self._offset(row, 1)
        return self.text[start:start + self.cols]

    def get(self, row: int, col: int, length: int) -> str:
        """Exatamente ``length`` caracteres a partir de ``(row, col)`` (continua na linha seguinte, como no 3270).

        Trecho que passa do fim da tela -> ``InvalidPosition``; ``length < 1`` -> ``ParameterError``.
        """
        pos = _codec.to_pos(row, col, self.rows, self.cols)
        _codec.check_span(pos, length, self.rows, self.cols)
        return self.text[pos - 1:pos - 1 + length]

    def find(self, text: str | re.Pattern[str], *, case: bool = True) -> Cursor | None:
        """Primeira ocorrência de ``text`` (texto ou ``re.Pattern``) no texto linear da tela, ou ``None``.

        ``case=False`` ignora maiúsculas/minúsculas (também para padrões). Texto vazio -> ``ParameterError``.
        """
        if isinstance(text, re.Pattern):
            if not isinstance(text.pattern, str):
                raise ParameterError.make(detalhe="O padrão deve ser de texto (str), não de bytes.")
            pattern = text if case else re.compile(text.pattern, text.flags | re.IGNORECASE)
        else:
            _check_text(text)
            if case:
                index = self.text.find(text)
                return None if index < 0 else self._cursor_at(index)
            pattern = re.compile(re.escape(text), re.IGNORECASE)
        found = pattern.search(self.text)
        return None if found is None else self._cursor_at(found.start())

    def contains(self, text: str, row: int | None = None, col: int | None = None) -> bool:
        """``text`` está na tela (sem posição), na linha ``row`` ou exatamente em ``(row, col)``?

        ``col`` sem ``row`` e texto vazio -> ``ParameterError``; posição fora da tela -> ``InvalidPosition``.
        """
        _check_text(text)
        if row is None:
            if col is not None:
                raise ParameterError.make(detalhe="Informe a linha (row) junto com a coluna (col).")
            return text in self.text
        if col is None:
            return text in self.line(row)
        start = self._offset(row, col)
        return self.text[start:start + len(text)] == text      # passa do fim da tela => não está lá

    def __contains__(self, text: str) -> bool:
        return self.contains(text)

    # ------------------------------------------------------------------ campos

    def _require_fields(self, what: str) -> tuple[Field, ...]:
        if self.fields is None:
            raise ParameterError.make(detalhe=f"{what} precisa dos campos da tela: use snapshot(fields=True).")
        return self.fields

    def field_at(self, row: int, col: int) -> Field | None:
        """Campo que contém ``(row, col)`` ou ``None`` (tela não formatada).

        Um campo vai do seu byte de atributo (a posição antes de ``start``) até o último dado, dando a volta no fim
        da tela. Sem campos no snapshot -> ``ParameterError``.
        """
        fields = self._require_fields("field_at")
        index = self._offset(row, col)
        size = self.rows * self.cols
        for item in fields:
            start = (item.start.row - 1) * self.cols + item.start.col - 1      # 1º dado, 0-based
            attribute = (start - 1) % size
            if (index - attribute) % size <= min(item.length, size - 1):
                return item
        return None

    @property
    def input_fields(self) -> list[Field]:
        """Campos desprotegidos (onde se digita), na ordem da tela. Sem campos no snapshot -> ``ParameterError``."""
        fields = self._require_fields("input_fields")
        return sorted((f for f in fields if not f.protected), key=lambda f: (f.start.row, f.start.col))

    # ------------------------------------------------------------------ tabelas

    def table(self, first_row: int, last_row: int, columns: Mapping[str, tuple[int, int]], *, strip: bool = True,
              skip_blank: bool = True) -> list[dict[str, str]]:
        """Linhas ``first_row..last_row`` (inclusive) como dicionários ``{nome: texto}``.

        ``columns`` = ``{"nome": (coluna, tamanho)}`` (coluna 1-based; o trecho precisa caber na linha).
        ``strip`` tira espaços das pontas; ``skip_blank`` pula linhas em que todas as colunas estão em branco.
        Para pandas: ``pd.DataFrame(tela.table(...))``.
        """
        self._offset(first_row, 1)                                      # valida as duas linhas
        self._offset(last_row, 1)
        if first_row > last_row:
            raise ParameterError.make(detalhe=f"first_row ({first_row}) é maior que last_row ({last_row}).")
        if not isinstance(columns, Mapping) or not columns:
            raise ParameterError.make(detalhe="columns deve ser um dicionário {nome: (coluna, tamanho)} não vazio.")
        spans: list[tuple[str, int, int]] = []
        for name, spec in columns.items():
            if not isinstance(name, str):
                raise ParameterError.make(detalhe=f"Nome de coluna deve ser str (recebi {type(name).__name__}).")
            if not (isinstance(spec, (tuple, list)) and len(spec) == 2):
                raise ParameterError.make(detalhe=f"Coluna '{name}': use (coluna, tamanho).")
            col, length = spec
            self._offset(first_row, col)                                # valida a coluna
            if isinstance(length, bool) or not isinstance(length, int) or length < 1:
                raise ParameterError.make(detalhe=f"Coluna '{name}': o tamanho deve ser um inteiro >= 1.")
            if col + length - 1 > self.cols:
                raise InvalidPosition.make(r=first_row, c=col + length - 1, rows=self.rows, cols=self.cols,
                                           details={"column": name})
            spans.append((name, col - 1, length))
        result: list[dict[str, str]] = []
        for line in self.lines[first_row - 1:last_row]:
            row = {name: line[start:start + length] for name, start, length in spans}
            if skip_blank and not any(value.strip() for value in row.values()):
                continue
            if strip:
                row = {name: value.strip() for name, value in row.items()}
            result.append(row)
        return result

    # ------------------------------------------------------------------ formatação

    def _footer(self) -> str:
        """``cursor (22,56) · teclado livre · 24x80 · D BRAZIL_D · 14:05:12``."""
        from .profiles import PROFILES                 # tardio: screen não puxa profiles ao ser importado (A.4.1)
        profile = PROFILES.get(self.terminal)
        parts = [f"cursor {self.cursor}", self.oia.describe()]
        if self.host_busy:
            parts.append("host ocupado")
        if self.hidden_unknown:
            parts.append("máscara desconhecida")
        parts += [f"{self.rows}x{self.cols}", f"{self.terminal} {profile.name}" if profile else self.terminal]
        try:
            parts.append(time.strftime("%H:%M:%S", time.localtime(self.taken_at)))
        except (OverflowError, OSError, ValueError):
            pass
        return " · ".join(parts)

    def to_text(self, *, ruler: bool = True) -> str:
        """A tela como texto: régua de colunas + número da linha (``ruler=True``) e um rodapé com cursor/OIA."""
        lines = self.lines
        if ruler:
            width = max(3, len(str(self.rows)))
            pad = " " * (width + 1)
            cols = range(1, self.cols + 1)
            tens = "".join(str(c // 10 % 10) if c % 10 == 0 else " " for c in cols).rstrip()
            units = "".join(str(c % 10) for c in cols)
            lines = [pad + tens, pad + units] + [f"{n:>{width}} {text}" for n, text in enumerate(lines, 1)]
        return "\n".join([*lines, self._footer()])

    def __str__(self) -> str:
        return self.to_text()

    def _repr_html_(self) -> str:
        """``<pre>`` para Jupyter: texto com ``html.escape`` e a posição do cursor destacada."""
        row, col = self.cursor.row - 1, self.cursor.col - 1
        out = []
        for n, text in enumerate(self.lines):
            if n == row and 0 <= col < len(text):
                out.append(html.escape(text[:col]) + f'<span style="{_CURSOR_STYLE}">{html.escape(text[col])}</span>'
                           + html.escape(text[col + 1:]))
            else:
                out.append(html.escape(text))
        out.append(html.escape(self._footer()))
        return '<pre style="font-family:monospace;line-height:1.15">' + "\n".join(out) + "</pre>"


def _check_text(text: object) -> None:
    if not isinstance(text, str):
        kind = type(text).__name__
        raise ParameterError.make(detalhe=f"O texto procurado deve ser str ou re.Pattern (recebi {kind}).")
    if not text:
        raise ParameterError.make(detalhe="O texto procurado não pode ser vazio.")
