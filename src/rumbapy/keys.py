"""Teclas do 3270 (B.6): ``Key``, o conjunto ``AIDS`` e a tokenização de mnemônicos EHLLAPI.

* ``Key`` é um ``Enum`` sem mixin ``str`` (E24): ``Token = str | Key`` nunca é ambíguo. Teste sempre
  ``isinstance(t, Key)`` **antes** de ``isinstance(t, str)``.
* Teclas AID (``AIDS``) transmitem ao host: ENTER, CLEAR, PF1..PF24, PA1..PA3, SYSREQ, ATTN e PAGE_UP/PAGE_DOWN
  (roll). Tudo depois da 1ª AID numa chamada de ``WD_SendKey`` é descartado pela DLL.

P-0 entrega ``Key``, ``AIDS``, ``Token``, ``Segment`` e ``tokenize_mnemonic`` (usado pelo worker e pela compat).
``Key.parse``, ``Key.pf``, ``tokenize_extra``, ``split_at_aids`` e ``encode_body`` são do pacote P-A.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from enum import Enum

from .errors import ParameterError, UndefinedKey

ESCAPE = "@"                 # caractere de escape dos mnemônicos (padrão da DLL)
_P_A = "pendente: implementação do pacote P-A (núcleo puro)"


class Key(Enum):
    """Teclas do terminal; o valor é o mnemônico de ``WD_SendKey`` (``Key.ENTER.value == "@E"``)."""

    ENTER = "@E"
    CLEAR = "@C"
    PA1 = "@x"
    PA2 = "@y"
    PA3 = "@z"
    SYSREQ = "@A@H"
    ATTN = "@A@Q"
    PF1 = "@1"
    PF2 = "@2"
    PF3 = "@3"
    PF4 = "@4"
    PF5 = "@5"
    PF6 = "@6"
    PF7 = "@7"
    PF8 = "@8"
    PF9 = "@9"
    PF10 = "@a"
    PF11 = "@b"
    PF12 = "@c"
    PF13 = "@d"
    PF14 = "@e"
    PF15 = "@f"
    PF16 = "@g"
    PF17 = "@h"
    PF18 = "@i"
    PF19 = "@j"
    PF20 = "@k"
    PF21 = "@l"
    PF22 = "@m"
    PF23 = "@n"
    PF24 = "@o"
    PAGE_UP = "@u"           # roll: é AID (transmite ao host; C10, I14)
    PAGE_DOWN = "@v"
    RESET = "@R"
    TAB = "@T"
    BACKTAB = "@B"
    HOME = "@0"
    END = "@q"
    NEWLINE = "@N"
    UP = "@U"
    DOWN = "@V"
    LEFT = "@L"
    RIGHT = "@Z"
    BACKSPACE = "@<"
    DELETE = "@D"
    INSERT = "@I"
    ERASE_EOF = "@F"
    ERASE_INPUT = "@A@F"
    FIELD_EXIT = "@A@E"
    CURSOR_SELECT = "@A@J"
    DUP = "@S@x"
    FIELD_MARK = "@S@y"
    PRINT = "@P"
    HELP = "@H"
    # aliases (mesmo valor => mesmo membro)
    F1 = "@1"
    F2 = "@2"
    F3 = "@3"
    F4 = "@4"
    F5 = "@5"
    F6 = "@6"
    F7 = "@7"
    F8 = "@8"
    F9 = "@9"
    F10 = "@a"
    F11 = "@b"
    F12 = "@c"
    F13 = "@d"
    F14 = "@e"
    F15 = "@f"
    F16 = "@g"
    F17 = "@h"
    F18 = "@i"
    F19 = "@j"
    F20 = "@k"
    F21 = "@l"
    F22 = "@m"
    F23 = "@n"
    F24 = "@o"
    PAGEUP = "@u"
    PAGEDOWN = "@v"
    ERASEEOF = "@F"

    @property
    def is_aid(self) -> bool:
        """A tecla transmite ao host (está em ``AIDS``)?"""
        return self in AIDS

    @classmethod
    def parse(cls, name: Key | str) -> Key:
        """``"ENTER"``, ``"PF3"``, ``"F3"``, ``"PAGE_UP"``, ``"PAGEUP"``, ``"<Pf3>"``, ``"@3"`` -> ``Key`` (P-A)."""
        raise NotImplementedError(f"Key.parse: {_P_A}")

    @classmethod
    def pf(cls, n: int) -> Key:
        """``Key.pf(3) is Key.PF3``; fora de 1..24 -> ``ParameterError`` (P-A)."""
        raise NotImplementedError(f"Key.pf: {_P_A}")


AIDS: frozenset[Key] = frozenset({
    Key.ENTER, Key.CLEAR, Key.PA1, Key.PA2, Key.PA3, Key.SYSREQ, Key.ATTN, Key.PAGE_UP, Key.PAGE_DOWN,
    *(Key[f"PF{n}"] for n in range(1, 25)),
})

Token = str | Key            # str = texto literal


@dataclass(frozen=True)
class Segment:
    """Trecho de entrada: corpo (texto e teclas locais, sem AID) seguido de no máximo uma AID."""

    body: tuple[Token, ...]
    aid: Key | None


# mnemônico -> Key: "@E" -> ("E",) ; "@A@H" -> ("A", "H")
_ONE_PART: dict[str, Key] = {k.value[1]: k for k in Key if len(k.value) == 2}
_TWO_PART: dict[tuple[str, str], Key] = {(k.value[1], k.value[3]): k for k in Key if len(k.value) == 4}
_PREFIXES = frozenset(prefix for prefix, _ in _TWO_PART)
_AT_HINT = "Para digitar '@' use '@@'."


def tokenize_mnemonic(s: str) -> list[Token]:
    """Texto no formato de ``WD_SendKey`` -> tokens: ``"SPRE@E"`` -> ``["SPRE", Key.ENTER]``.

    ``"@@"`` é um ``@`` literal (junta-se ao texto vizinho); ``"@"`` final ou código inválido -> ``UndefinedKey``.
    Não valida AIDs nem tamanho (isso é de quem chama).
    """
    if not isinstance(s, str):
        raise ParameterError.make(detalhe=f"Mnemônicos devem ser str (recebi {type(s).__name__}).")
    tokens: list[Token] = []
    text: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch != ESCAPE:
            text.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise UndefinedKey.make(x=ESCAPE, sugestao=_AT_HINT)
        code = s[i + 1]
        if code == ESCAPE:
            text.append(ESCAPE)
            i += 2
            continue
        if code in _PREFIXES:
            if i + 3 < n and s[i + 2] == ESCAPE and (code, s[i + 3]) in _TWO_PART:
                key = _TWO_PART[(code, s[i + 3])]
                width = 4
            else:
                raise UndefinedKey.make(x=s[i:i + 4], sugestao=_AT_HINT)
        elif code in _ONE_PART:
            key = _ONE_PART[code]
            width = 2
        else:
            raise UndefinedKey.make(x=s[i:i + 2], sugestao=_AT_HINT)
        if text:
            tokens.append("".join(text))
            text.clear()
        tokens.append(key)
        i += width
    if text:
        tokens.append("".join(text))
    return tokens


def tokenize_extra(s: str) -> list[Token]:
    """``"abc<Tab>1<Enter>"`` -> ``["abc", Key.TAB, "1", Key.ENTER]``; ``"<<"`` = ``"<"`` literal (P-A)."""
    raise NotImplementedError(f"tokenize_extra: {_P_A}")


def split_at_aids(tokens: Sequence[Token]) -> list[Segment]:
    """Corta a sequência em segmentos que terminam em no máximo uma AID (P-A)."""
    raise NotImplementedError(f"split_at_aids: {_P_A}")


def encode_body(body: Sequence[Token], *, host: Literal["cics", "ims"], errors: Literal["strict", "replace"] = "strict",
                secret: bool = False) -> list[str]:
    """Corpo sem AID -> chunks de ``WD_SendKey`` (<= 128 bytes cp1252, ``@`` -> ``@@``) no formato de wire (P-A)."""
    raise NotImplementedError(f"encode_body: {_P_A}")
