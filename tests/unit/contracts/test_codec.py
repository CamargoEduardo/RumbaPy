"""Aceite P-0: ``_codec`` (F.4) — decode/encode cp1252, conjunto seguro por host e posições em 24x80 e 32x80."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import EncodingError, InvalidPosition, ParameterError
from rumbapy import _codec

import logging

import pytest

UNDEFINED_CP1252 = bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])


# ------------------------------------------------------------------------------------------- decode

def test_decode_turns_control_bytes_into_spaces_and_keeps_the_size() -> None:
    raw = bytes(range(0x20)) + b"ABC\x00\x00"
    text = _codec.decode(raw)
    assert len(text) == len(raw)
    assert text == " " * 0x20 + "ABC  "


def test_decode_cp1252_accents_round_trip() -> None:
    data = _codec.encode("ç ã É €", host="cics")
    assert data == b"\xe7 \xe3 \xc9 \x80"
    assert _codec.decode(data) == "ç ã É €"


def test_decode_undefined_bytes_become_replacement_char() -> None:
    text = _codec.decode(b"A" + UNDEFINED_CP1252 + b"B")
    assert text == "A" + "�" * 5 + "B"
    assert len(text) == 7


def test_decode_keeps_everything_else() -> None:
    raw = bytes(range(0x20, 0x100))
    text = _codec.decode(raw)
    assert len(text) == len(raw)
    assert text[0] == " " and text[-1] == "ÿ" and text[0x7F - 0x20] == "\x7f"


def test_decode_refuses_text() -> None:
    with pytest.raises(ParameterError):
        _codec.decode("abc")  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- encode

def test_encode_normalizes_to_nfc() -> None:
    decomposed = "ção"     # c + cedilha combinante, a + til combinante
    assert _codec.encode(decomposed, host="cics") == "ção".encode("cp1252")


@pytest.mark.parametrize("ch", ["\t", "\n", "\r", "\x00", "\x1b", "\x7f"])
@pytest.mark.parametrize("errors", ["strict", "replace"])
def test_encode_controls_always_raise(ch: str, errors: str) -> None:
    with pytest.raises(EncodingError) as info:
        _codec.encode(f"ab{ch}cd", host="cics", errors=errors)  # type: ignore[arg-type]
    exc = info.value
    assert exc.delivery == "none"
    assert exc.hint == "use Key.TAB / escreva por linha"
    assert "(U+%04X) na posição 2" % ord(ch) in exc.message


def test_euro_is_accepted_in_cics_and_refused_in_ims() -> None:
    assert _codec.encode("€ 10", host="cics") == b"\x80 10"
    with pytest.raises(EncodingError) as info:
        _codec.encode("€ 10", host="ims")
    assert info.value.message == "O caractere '€' (U+20AC) na posição 0 não existe no terminal."


def test_currency_sign_is_refused_in_cics_and_accepted_in_ims() -> None:
    with pytest.raises(EncodingError):
        _codec.encode("¤", host="cics")
    assert _codec.encode("¤", host="ims") == b"\xa4"


@pytest.mark.parametrize("host", ["cics", "ims"])
def test_safe_set(host: str) -> None:
    printable = "".join(chr(c) for c in range(0x20, 0x7F))
    assert _codec.encode(printable, host=host) == printable.encode("ascii")  # type: ignore[arg-type]
    latin = "".join(chr(c) for c in range(0xA0, 0x100) if not (host == "cics" and c == 0xA4))
    assert _codec.decode(_codec.encode(latin, host=host)) == latin  # type: ignore[arg-type]
    for outside in ("→", "Ā", "“", "\u0080", "\u009f", "\U0001f600"):
        with pytest.raises(EncodingError):
            _codec.encode(outside, host=host)  # type: ignore[arg-type]


def test_replace_map_and_warning_without_text(caplog: pytest.LogCaptureFixture) -> None:
    source = "“aspas” ‘x’ a–b—c fim… seta→ emoji\U0001f600"
    with caplog.at_level(logging.WARNING, logger="rumbapy._codec"):
        data = _codec.encode(source, host="cics", errors="replace")
    assert data == b"\"aspas\" 'x' a-b-c fim... seta? emoji?"
    assert len(caplog.records) == 1
    assert "aspas" not in caplog.text and "seta" not in caplog.text
    assert _codec.encode("€", host="ims", errors="replace") == b"?"
    assert _codec.encode("¤", host="cics", errors="replace") == b"?"


def test_secret_messages_never_show_the_character() -> None:
    for value in ("senha→1", "senha\t1", "€uro"):
        host = "ims" if value.startswith("€") else "cics"
        with pytest.raises(EncodingError) as info:
            _codec.encode(value, host=host, secret=True)  # type: ignore[arg-type]
        exc = info.value
        assert exc.message == "O valor secreto contém um caractere não suportado."
        assert exc.details is None
        offending = value[5] if value.startswith("senha") else value[0]
        assert offending not in str(exc) and repr(offending)[1:-1] not in str(exc)
        assert "senha" not in str(exc) and "uro" not in str(exc) and "U+" not in str(exc)


def test_encode_argument_validation() -> None:
    with pytest.raises(ParameterError):
        _codec.encode(b"abc", host="cics")  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        _codec.encode("abc", host="tso")  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        _codec.encode("abc", host="cics", errors="ignore")  # type: ignore[arg-type]
    assert _codec.encode("", host="ims") == b""


# ------------------------------------------------------------------------------------------- posições

@pytest.mark.parametrize("rows, cols", [(24, 80), (32, 80)])
def test_to_pos_and_back(rows: int, cols: int) -> None:
    assert _codec.to_pos(1, 1, rows, cols) == 1
    assert _codec.to_pos(22, 56, rows, cols) == 21 * 80 + 56 == 1736
    assert _codec.to_pos(rows, cols, rows, cols) == rows * cols
    for pos in (1, 80, 81, 1736, rows * cols):
        row, col = _codec.to_rowcol(pos, rows, cols)
        assert _codec.to_pos(row, col, rows, cols) == pos
    assert _codec.to_rowcol(1736, rows, cols) == (22, 56)


@pytest.mark.parametrize("rows, cols", [(24, 80), (32, 80)])
@pytest.mark.parametrize("row, col", [(0, 1), (1, 0), (1, 81), (-1, 5)])
def test_to_pos_outside(rows: int, cols: int, row: int, col: int) -> None:
    with pytest.raises(InvalidPosition) as info:
        _codec.to_pos(row, col, rows, cols)
    assert info.value.message == f"({row},{col}) está fora da tela {rows}x{cols}."
    assert isinstance(info.value, IndexError)


def test_rows_differ_between_profiles() -> None:
    assert _codec.to_pos(32, 80, 32, 80) == 2560
    with pytest.raises(InvalidPosition) as info:
        _codec.to_pos(25, 1, 24, 80)
    assert info.value.message == "(25,1) está fora da tela 24x80."
    with pytest.raises(InvalidPosition):
        _codec.to_rowcol(1921, 24, 80)
    assert _codec.to_rowcol(1921, 32, 80) == (25, 1)


@pytest.mark.parametrize("rows, cols", [(24, 80), (32, 80)])
def test_check_span(rows: int, cols: int) -> None:
    size = rows * cols
    _codec.check_span(1, size, rows, cols)
    _codec.check_span(size, 1, rows, cols)
    _codec.check_span(1771, 74, rows, cols)          # (23,11) + 74 cabe
    with pytest.raises(ParameterError):
        _codec.check_span(1, 0, rows, cols)
    with pytest.raises(InvalidPosition) as info:
        _codec.check_span(size - 9, 20, rows, cols)  # passa 10 posições do fim
    assert info.value.message == f"({rows + 1},10) está fora da tela {rows}x{cols}."
    with pytest.raises(InvalidPosition):
        _codec.check_span(0, 1, rows, cols)
    with pytest.raises(InvalidPosition):
        _codec.check_span(size + 1, 1, rows, cols)


@pytest.mark.parametrize("bad", [True, 1.0, "1", None])
def test_positions_refuse_non_int(bad: object) -> None:
    with pytest.raises(ParameterError):
        _codec.to_pos(bad, 1, 24, 80)  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        _codec.check_span(1, bad, 24, 80)  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        _codec.to_rowcol(bad, 24, 80)  # type: ignore[arg-type]
