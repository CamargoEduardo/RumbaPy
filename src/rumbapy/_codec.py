"""Posições e texto do terminal (F.4) — puro, usado por ``keys``, ``session`` e pelo worker.

* Posição linear 1-based: ``pos = (row - 1) * cols + col`` com ``rows/cols`` reais da sessão (IMS A = 32x80).
* Leitura (``decode``): bytes ``0x00-0x1F`` viram espaço e o resto é cp1252 com ``replace``; o tamanho é preservado.
* Escrita (``encode``): NFC, recusa caracteres de controle e aceita só o "conjunto seguro" do host
  (ASCII imprimível + U+00A0-U+00FF; CICS/CCSID 1143 troca ``¤`` por ``€``; IMS/CCSID 037 não tem ``€``).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Literal

from .errors import EncodingError, InvalidPosition, ParameterError

import unicodedata
import logging

logger = logging.getLogger(__name__)

ENCODING = "cp1252"
Host = Literal["cics", "ims"]
HOSTS: tuple[str, ...] = ("cics", "ims")
ERRORS: tuple[str, ...] = ("strict", "replace")

# errors="replace": aproximações ASCII; qualquer outro caractere fora do conjunto seguro vira "?"
REPLACEMENTS: dict[str, str] = {
    "\u201c": '"', "\u201d": '"',      # aspas curvas duplas
    "\u2018": "'", "\u2019": "'",      # aspas curvas simples
    "\u2013": "-", "\u2014": "-",      # meia-risca e travessão
    "\u2026": "...",                   # reticências
}
CONTROL_HINT = "use Key.TAB / escreva por linha"

_EURO = 0x20AC
_CURRENCY_SIGN = 0x00A4
_CONTROL_TO_SPACE = bytes.maketrans(bytes(range(0x20)), b" " * 0x20)


# ------------------------------------------------------------------------------------------- posições

def _check_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError.make(detalhe=f"{name} deve ser um número inteiro (recebi {type(value).__name__}).")
    return value


def _check_geometry(rows: object, cols: object) -> tuple[int, int]:
    rows, cols = _check_int("rows", rows), _check_int("cols", cols)
    if rows < 1 or cols < 1:
        raise ParameterError.make(detalhe=f"Tamanho de tela inválido: {rows}x{cols}.")
    return rows, cols


def _split(pos: int, cols: int) -> tuple[int, int]:
    return (pos - 1) // cols + 1, (pos - 1) % cols + 1


def to_pos(row: int, col: int, rows: int, cols: int) -> int:
    """(linha, coluna) 1-based -> posição linear 1-based; ``InvalidPosition`` fora de 1..rows / 1..cols."""
    row, col = _check_int("row", row), _check_int("col", col)
    rows, cols = _check_geometry(rows, cols)
    if not (1 <= row <= rows and 1 <= col <= cols):
        raise InvalidPosition.make(r=row, c=col, rows=rows, cols=cols)
    return (row - 1) * cols + col


def to_rowcol(pos: int, rows: int, cols: int) -> tuple[int, int]:
    """Posição linear 1-based -> (linha, coluna); ``InvalidPosition`` fora de 1..rows*cols."""
    pos = _check_int("pos", pos)
    rows, cols = _check_geometry(rows, cols)
    row, col = _split(pos, cols)
    if not 1 <= pos <= rows * cols:
        raise InvalidPosition.make(r=row, c=col, rows=rows, cols=cols, details={"pos": pos})
    return row, col


def check_span(pos: int, length: int, rows: int, cols: int) -> None:
    """Exige ``length >= 1`` (senão ``ParameterError``) e ``pos .. pos+length-1`` dentro da tela.

    Fora da tela -> ``InvalidPosition`` citando a posição (linha, coluna) que ficou de fora.
    """
    pos, length = _check_int("pos", pos), _check_int("length", length)
    rows, cols = _check_geometry(rows, cols)
    if length < 1:
        raise ParameterError.make(detalhe=f"O tamanho deve ser pelo menos 1 (recebi {length}).")
    size = rows * cols
    if not 1 <= pos <= size:
        row, col = _split(pos, cols)
        raise InvalidPosition.make(r=row, c=col, rows=rows, cols=cols, details={"pos": pos, "length": length})
    end = pos + length - 1
    if end > size:
        row, col = _split(end, cols)
        raise InvalidPosition.make(r=row, c=col, rows=rows, cols=cols, details={"pos": pos, "length": length})


# ------------------------------------------------------------------------------------------- texto

def decode(raw: bytes) -> str:
    """Bytes do PS -> texto: ``0x00-0x1F`` viram espaço; cp1252 com ``replace``; ``len(resultado) == len(raw)``."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ParameterError.make(detalhe=f"decode espera bytes (recebi {type(raw).__name__}).")
    return bytes(raw).translate(_CONTROL_TO_SPACE).decode(ENCODING, "replace")


def is_safe(ch: str, host: Host) -> bool:
    """O caractere existe no terminal do ``host`` (conjunto seguro, F.4)?"""
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E:
        return True
    if 0xA0 <= cp <= 0xFF:
        return not (host == "cics" and cp == _CURRENCY_SIGN)
    return cp == _EURO and host == "cics"


def _shown(ch: str) -> str:
    """Forma legível de um caractere numa mensagem (controles aparecem escapados, ex.: ``\\t``)."""
    cp = ord(ch)
    if cp < 0x20 or cp == 0x7F or 0x80 <= cp <= 0x9F:
        return ch.encode("unicode_escape").decode("ascii")
    return ch


def _char_error(ch: str, index: int, *, secret: bool, control: bool) -> EncodingError:
    hint = CONTROL_HINT if control else None
    if secret:
        return EncodingError.make("secret", hint=hint)
    return EncodingError.make(ch=_shown(ch), cp=ord(ch), i=index, hint=hint,
                              details={"index": index, "codepoint": ord(ch)})


def encode(text: str, *, host: Host, errors: Literal["strict", "replace"] = "strict", secret: bool = False) -> bytes:
    """Texto do usuário -> bytes cp1252 prontos para o terminal (NFC + conjunto seguro do ``host``).

    Controles (``< 0x20`` e ``0x7F``) sempre levantam ``EncodingError``. Fora do conjunto seguro: ``strict`` levanta
    ``EncodingError`` (sem citar o caractere se ``secret``); ``replace`` usa ``REPLACEMENTS`` ou ``?`` e registra um
    WARNING só com a contagem (nunca o texto).
    """
    if not isinstance(text, str):
        raise ParameterError.make(detalhe=f"O texto deve ser str (recebi {type(text).__name__}).")
    if host not in HOSTS:
        raise ParameterError.make(detalhe=f"host deve ser 'cics' ou 'ims' (recebi {host!r}).")
    if errors not in ERRORS:
        raise ParameterError.make(detalhe=f"errors deve ser 'strict' ou 'replace' (recebi {errors!r}).")
    text = unicodedata.normalize("NFC", text)
    out: list[str] = []
    replaced = 0
    for index, ch in enumerate(text):
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7F:
            raise _char_error(ch, index, secret=secret, control=True)
        if is_safe(ch, host):
            out.append(ch)
        elif errors == "strict":
            raise _char_error(ch, index, secret=secret, control=False)
        else:
            out.append(REPLACEMENTS.get(ch, "?"))
            replaced += 1
    if replaced:
        logger.warning("encode: %d caractere(s) sem equivalente no terminal foram substituídos", replaced)
    return "".join(out).encode(ENCODING)
