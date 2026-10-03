"""Teclas do 3270 (B.6): ``Key``, o conjunto ``AIDS`` e a tokenização de mnemônicos EHLLAPI.

* ``Key`` é um ``Enum`` sem mixin ``str`` (E24): ``Token = str | Key`` nunca é ambíguo. Teste sempre
  ``isinstance(t, Key)`` **antes** de ``isinstance(t, str)``.
* Teclas AID (``AIDS``) transmitem ao host: ENTER, CLEAR, PF1..PF24, PA1..PA3, SYSREQ, ATTN e PAGE_UP/PAGE_DOWN
  (roll). Tudo depois da 1ª AID numa chamada de ``WD_SendKey`` é descartado pela DLL.

Duas sintaxes de entrada:

* **mnemônicos** (formato de ``WD_SendKey``, ``tokenize_mnemonic``): ``"SPRE@E"``; ``"@@"`` = ``@`` literal;
* **extra** (amigável, ``tokenize_extra``): ``"abc<Tab>1<Enter>"``; ``"<<"`` = ``<`` literal; ``@`` é texto comum.

``split_at_aids`` corta os tokens em ``Segment`` (corpo + no máximo uma AID) e ``encode_body`` transforma um corpo
em chunks de wire (<= 128 bytes cp1252, ``@`` -> ``@@``, unidades nunca partidas).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from enum import Enum

from .errors import EncodingError, ParameterError, UndefinedKey
from . import _codec

import unicodedata
import difflib

ESCAPE = "@"                 # caractere de escape dos mnemônicos (padrão da DLL)
CHUNK_MAX_BYTES = 128        # limite de projeto por WD_SendKey (= _protocol.CHUNK_MAX_BYTES; a DLL recusa >= 256)
EXTRA_OPEN = "<"             # "<Tab>" na sintaxe extra; "<<" = "<" literal
EXTRA_CLOSE = ">"
_NAME_MAX = 20               # nomes de tecla maiores que isto não são citados em mensagens (podem ser dados)


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
        """``"ENTER"``, ``"PF3"``, ``"F3"``, ``"PAGE_UP"``, ``"PAGEUP"``, ``"<Pf3>"``, ``"@3"`` -> ``Key``.

        Sem diferença de maiúsculas; espaços e hífens valem como ``_`` (``"page down"``, ``"erase-eof"``). Nome
        desconhecido -> ``UndefinedKey`` com sugestão ("Você quis dizer PF3?"); tipo errado -> ``ParameterError``.
        """
        if isinstance(name, Key):
            return name
        if not isinstance(name, str):
            raise ParameterError.make(detalhe=f"A tecla deve ser Key ou str (recebi {type(name).__name__}).")
        text = name.strip()
        if text.startswith(EXTRA_OPEN) and text.endswith(EXTRA_CLOSE) and len(text) > 2:
            text = text[1:-1].strip()
        key = _lookup(text)
        if key is None:
            raise UndefinedKey.make(x=_quoted(name), sugestao=_suggest(text, "{}"))
        return key

    @classmethod
    def pf(cls, n: int) -> Key:
        """``Key.pf(3) is Key.PF3``; fora de 1..24 (ou não inteiro) -> ``ParameterError``."""
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 24:
            raise ParameterError.make(detalhe=f"PF vai de 1 a 24 (recebi {n!r}).")
        return cls[f"PF{n}"]


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
_LT_HINT = "Para digitar '<' use '<<'."

# nome -> Key (inclui aliases: F3, PAGEUP...) e a forma sem "_" (PAGEDOWN, ERASEINPUT, FIELDEXIT...)
_BY_NAME: dict[str, Key] = dict(Key.__members__)
_BY_COMPACT: dict[str, Key] = {name.replace("_", ""): key for name, key in Key.__members__.items()}


def _normalize(text: str) -> str:
    return text.strip().upper().replace(" ", "_").replace("-", "_")


def _lookup(text: str) -> Key | None:
    """Nome de tecla (sem ``<>``) ou um único mnemônico (``"@3"``) -> ``Key`` ou ``None``.

    Nomes não diferenciam maiúsculas; mnemônicos sim (``"@a"`` = PF10, ``"@A@F"`` = ERASE_INPUT).
    """
    text = text.strip()
    if text.startswith(ESCAPE):
        try:
            tokens = tokenize_mnemonic(text)
        except UndefinedKey:
            return None
        return tokens[0] if len(tokens) == 1 and isinstance(tokens[0], Key) else None
    norm = _normalize(text)
    if not norm:
        return None
    key = _BY_NAME.get(norm)
    if key is not None or "_" in norm:
        # a forma compacta (PAGEDOWN, ERASEEOF) só vale escrita SEM separadores: "<P F 1>"/"<E-N-T-E-R>" no meio de um
        # texto nunca viram uma AID que o usuário não pediu
        return key
    return _BY_COMPACT.get(norm)


def _close_name(text: str) -> str | None:
    """O nome de tecla mais parecido com ``text`` (``difflib``, corte 0,6) ou ``None``."""
    norm = _normalize(text)
    close = difflib.get_close_matches(norm, list(_BY_NAME), n=1, cutoff=0.6) if norm else []
    return close[0] if close else None


def _suggest(text: str, form: str) -> str:
    """Frase de sugestão para um nome desconhecido (``form`` = ``"{}"`` ou ``"<{}>"``)."""
    if text.strip().startswith(ESCAPE):
        return "Informe um único mnemônico (ex.: '@3' = PF3); " + _AT_HINT[0].lower() + _AT_HINT[1:]
    close = _close_name(text)
    if close is not None:
        return f"Você quis dizer {form.format(close)}?"
    enter, pf3, tab, page_down = (form.format(n) for n in ("ENTER", "PF3", "TAB", "PAGE_DOWN"))
    return f"Use nomes como {enter}, {pf3}, {tab} ou {page_down}."


def _quoted(name: str) -> str:
    """Nome como aparece na mensagem (nomes compridos são cortados: podem ser dados digitados por engano)."""
    return name if len(name) <= _NAME_MAX else name[:_NAME_MAX] + "…"


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
            else:                                # só cita 4 caracteres se o 3º for '@' (nunca 2 caracteres de dado)
                shown = s[i:i + 4] if i + 2 < n and s[i + 2] == ESCAPE else s[i:i + 2]
                raise UndefinedKey.make(x=shown, sugestao=_AT_HINT)
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


def _is_name(name: str) -> bool:
    """O trecho entre ``<`` e ``>`` tem cara de nome de tecla (curto, só letras/dígitos/``_ -@<``)?"""
    return 0 < len(name) <= _NAME_MAX and all(ch.isascii() and (ch.isalnum() or ch in "_ -@<") for ch in name)


def tokenize_extra(s: str) -> list[Token]:
    """Sintaxe extra -> tokens: ``"abc<Tab>1<Enter>"`` -> ``["abc", Key.TAB, "1", Key.ENTER]``.

    ``"<<"`` é um ``<`` literal; ``>`` sozinho e ``@`` são texto comum (``encode_body`` escapa o ``@``). Dentro de
    ``<...>`` valem os nomes de ``Key.parse`` (``<PF3>``, ``<F3>``, ``<page down>``, ``<@3>``). Nome desconhecido ou
    ``<`` sem ``>`` -> ``UndefinedKey``; a mensagem só cita o trecho se ele parecer um nome de tecla (curto), para
    não expor dados digitados por engano.
    """
    return _tokenize_extra(s, secret=False)


def _tokenize_extra(s: str, *, secret: bool) -> list[Token]:
    """``tokenize_extra`` com ``secret``: com ``secret=True`` a ``UndefinedKey`` nunca cita o trecho (B.4.5, B.7).

    Para ``Session.send_keys(..., secret=True)`` (P-E): a assinatura pública da B.6 não tem ``secret``.
    """
    if not isinstance(s, str):
        raise ParameterError.make(detalhe=f"As teclas devem ser str (recebi {type(s).__name__}).")
    tokens: list[Token] = []
    text: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch != EXTRA_OPEN:
            text.append(ch)
            i += 1
            continue
        if i + 1 < n and s[i + 1] == EXTRA_OPEN:
            text.append(EXTRA_OPEN)
            i += 2
            continue
        close = s.find(EXTRA_CLOSE, i + 1)
        name = s[i + 1:close] if close != -1 else ""
        key = _lookup(name) if _is_name(name) else None
        if key is None:
            if secret:
                raise UndefinedKey.make(x="<...>", sugestao="Confira os nomes entre '<' e '>' (ex.: <Enter>). "
                                        + _LT_HINT)
            if _is_name(name):
                # só cita o trecho se ele parece um nome de tecla digitado errado (<Pf33>); senão pode ser um dado
                # (uma senha com '<' passada sem secret=True)
                shown = f"<{name}>" if _close_name(name) is not None else "<...>"
                raise UndefinedKey.make(x=shown, sugestao=_suggest(name, "<{}>") + " " + _LT_HINT)
            raise UndefinedKey.make(x=EXTRA_OPEN, sugestao="Feche o nome da tecla com '>' (ex.: <Enter>). " + _LT_HINT)
        if text:
            tokens.append("".join(text))
            text.clear()
        tokens.append(key)
        i = close + 1
    if text:
        tokens.append("".join(text))
    return tokens


def _check_token(token: object, index: int) -> None:
    if not isinstance(token, (Key, str)):
        raise ParameterError.make(detalhe=f"Token {index} deve ser str ou Key (recebi {type(token).__name__}).")


def split_at_aids(tokens: Sequence[Token]) -> list[Segment]:
    """Corta a sequência em segmentos ``Segment(body, aid)``: cada AID fecha um segmento.

    ``["abc", Key.TAB, "1", Key.ENTER, "x"]`` -> ``[Segment(("abc", Key.TAB, "1"), Key.ENTER), Segment(("x",), None)]``.
    Textos vazios são descartados; um segmento vazio só existe se tiver AID (``[Key.ENTER]`` ->
    ``[Segment((), Key.ENTER)]``).
    """
    if isinstance(tokens, (str, Key)) or not isinstance(tokens, Sequence):
        raise ParameterError.make(detalhe=f"split_at_aids espera uma lista de tokens (recebi {type(tokens).__name__}).")
    segments: list[Segment] = []
    body: list[Token] = []
    for index, token in enumerate(tokens):
        _check_token(token, index)
        if isinstance(token, Key):
            if token in AIDS:
                segments.append(Segment(tuple(body), token))
                body = []
            else:
                body.append(token)
        elif token:
            body.append(token)
    if body:
        segments.append(Segment(tuple(body), None))
    return segments


def _units(body: Sequence[Token], host: _codec.Host, errors: Literal["strict", "replace"], secret: bool) -> list[str]:
    """Corpo -> unidades atômicas de wire: 1 caractere (``@`` -> ``@@``) ou 1 mnemônico de tecla local."""
    units: list[str] = []
    offset = 0                                       # posição do texto no corpo (para a mensagem de EncodingError)
    for index, token in enumerate(body):
        _check_token(token, index)
        if isinstance(token, Key):                   # Key antes de str (B.6)
            if token in AIDS:
                raise ParameterError.make(detalhe=f"A tecla AID {token.name} não pode ir no corpo; use "
                                                  "split_at_aids e envie a AID sozinha.")
            units.append(token.value)
            continue
        try:
            data = _codec.encode(token, host=host, errors=errors, secret=secret)
        except EncodingError as exc:
            raise _shifted(exc, offset, secret) from None
        offset += len(unicodedata.normalize("NFC", token))   # _codec.encode indexa o texto já em NFC (F.4)
        units.extend(ESCAPE * 2 if ch == ESCAPE else ch for ch in data.decode(_codec.ENCODING))
    return units


def _shifted(exc: EncodingError, offset: int, secret: bool) -> EncodingError:
    """Reescreve a posição do caractere inválido para contar desde o início do corpo (nunca com ``secret``)."""
    details = exc.details or {}
    if secret or not offset or "index" not in details or "codepoint" not in details:
        return exc
    index, cp = details["index"] + offset, details["codepoint"]
    return EncodingError.make(ch=_codec._shown(chr(cp)), cp=cp, i=index, hint=exc.hint,
                              details={**details, "index": index})


def encode_body(body: Sequence[Token], *, host: Literal["cics", "ims"], errors: Literal["strict", "replace"] = "strict",
                secret: bool = False) -> list[str]:
    """Corpo sem AID -> chunks de ``WD_SendKey`` no formato de wire (``str`` = ``bytes.decode("cp1252")``, C.3).

    Texto passa por ``_codec.encode`` (NFC, conjunto seguro do ``host``; ``EncodingError`` sem citar o caractere se
    ``secret``) e cada ``@`` vira ``@@``; teclas locais entram como mnemônico. As unidades (1 caractere, ``@@`` ou 1
    mnemônico) são agrupadas em chunks de até ``CHUNK_MAX_BYTES`` (128) bytes sem nunca partir uma unidade. AID no
    corpo -> ``ParameterError``. Corpo vazio -> ``[]``.
    """
    if isinstance(body, Key) or not isinstance(body, Sequence):
        raise ParameterError.make(detalhe=f"encode_body espera uma lista de tokens (recebi {type(body).__name__}).")
    if isinstance(body, str):
        body = [body]
    if host not in _codec.HOSTS:
        raise ParameterError.make(detalhe=f"host deve ser 'cics' ou 'ims' (recebi {host!r}).")
    if errors not in _codec.ERRORS:
        raise ParameterError.make(detalhe=f"errors deve ser 'strict' ou 'replace' (recebi {errors!r}).")
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for unit in _units(body, host, errors, secret):
        if size + len(unit) > CHUNK_MAX_BYTES:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(unit)
        size += len(unit)                            # cp1252: 1 byte por caractere
    if current:
        chunks.append("".join(current))
    return chunks
