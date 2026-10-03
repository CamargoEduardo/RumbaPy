"""Aceite P-0: ``Cursor``, ``Field``, ``OIA`` (bytes ao vivo) e ``Screen.from_wire`` (B.5)."""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from rumbapy.screen import OIA, Cursor, Field, Screen
from rumbapy.errors import ProtocolError

import dataclasses
import inspect
import pickle

import pytest

# Validado ao vivo (2026-10-03, BRAZIL_D): formato 1, imagem com códigos internos, grupos 10 00 00 00 40 40 00…
LIVE_IMAGE = bytes([0x10] * 20 + [0xC1, 0x05, 0x00, 0x7F] * 15)
LIVE_OIA = bytes([1]) + LIVE_IMAGE + bytes([0x10, 0x00, 0x00, 0x00, 0x40, 0x40] + [0x00] * 16)


def oia_bytes(group1: int = 0x10, g8: bytes = b"\0" * 5) -> bytes:
    raw = bytearray(LIVE_OIA)
    raw[81] = group1
    raw[88:93] = g8
    return bytes(raw)


def test_oia_from_live_bytes_is_ready() -> None:
    assert len(LIVE_OIA) == 103
    oia = OIA.from_raw(LIVE_OIA)
    assert oia.format == 1
    assert oia.group1 == 0x10
    assert oia.inhibit == b"\0" * 5
    assert oia.online and not oia.keyboard_locked and oia.ready
    assert oia.describe() == "teclado livre"


def test_oia_keyboard_locked_and_offline() -> None:
    locked = OIA.from_raw(oia_bytes(g8=b"\x00\x20\x00\x00\x00"))
    assert locked.keyboard_locked and not locked.ready and locked.online
    assert locked.describe() == "teclado bloqueado (G8=00 20 00 00 00)"
    sscp = OIA.from_raw(oia_bytes(group1=0x20))
    assert sscp.online and sscp.ready
    offline = OIA.from_raw(oia_bytes(group1=0x00))
    assert not offline.online and not offline.ready
    assert offline.describe() == "sem conexão com o host"
    other_bits = OIA.from_raw(oia_bytes(group1=0xCF))
    assert not other_bits.online


@pytest.mark.parametrize("size", [0, 102, 104, 110])
def test_oia_wrong_size(size: int) -> None:
    with pytest.raises(ValueError):
        OIA.from_raw(b"\0" * size)


def test_oia_is_frozen_bytes() -> None:
    oia = OIA.from_raw(bytearray(LIVE_OIA))
    assert type(oia.raw) is bytes
    with pytest.raises(dataclasses.FrozenInstanceError):
        oia.raw = b""  # type: ignore[misc]
    with pytest.raises(TypeError):
        OIA("x" * 103)  # type: ignore[arg-type]
    assert pickle.loads(pickle.dumps(oia)) == oia


def test_cursor() -> None:
    cur = Cursor(22, 56)
    assert str(cur) == "(22,56)"
    assert cur == (22, 56) and cur.row == 22 and cur.col == 56


@pytest.mark.parametrize("attr, protected, numeric, hidden, intensified, modified", [
    (0xC5, False, False, False, False, True),    # ao vivo em (22,56): desprotegido, normal detectável, MDT
    (0xC0, False, False, False, False, False),
    (0xE0, True, False, False, False, False),
    (0xF0, True, True, False, False, False),
    (0xCC, False, False, True, False, False),    # não exibido (senha)
    (0xC8, False, False, False, True, False),    # alta intensidade
    (0xED, True, False, True, False, True),
])
def test_field_attribute_bits(attr: int, protected: bool, numeric: bool, hidden: bool, intensified: bool,
                              modified: bool) -> None:
    field = Field(Cursor(22, 56), 1, attr, " ")
    assert (field.protected, field.numeric, field.hidden, field.intensified, field.modified) == \
        (protected, numeric, hidden, intensified, modified)
    assert (field.row, field.col) == (22, 56)


def snapshot_result(**overrides: Any) -> dict[str, Any]:
    text = ("VOLVO" + " " * 75) + " " * (23 * 80)
    base = {"text": text, "cursor": [22, 56], "oia": LIVE_OIA.hex(), "busy": False, "hidden_unknown": False}
    base.update(overrides)
    return base


def test_screen_from_wire_basic() -> None:
    screen = Screen.from_wire(snapshot_result(), "D")
    assert (screen.terminal, screen.rows, screen.cols) == ("D", 24, 80)
    assert len(screen.text) == 1920 and screen.text.startswith("VOLVO")
    assert screen.cursor == Cursor(22, 56)
    assert screen.oia.ready and not screen.host_busy and not screen.hidden_unknown
    assert screen.fields is None and screen.match is None
    assert isinstance(screen.taken_at, float)


def test_screen_from_wire_geometry() -> None:
    ims = snapshot_result(text="X" * 2560)
    screen = Screen.from_wire(ims, "A")
    assert (screen.rows, screen.cols) == (32, 80)
    assert Screen.from_wire(ims, "A", rows=32, cols=80).rows == 32
    assert Screen.from_wire({**ims, "rows": 32, "cols": 80}, "A").rows == 32
    assert Screen.from_wire(snapshot_result(text="Y" * 1320, cursor=1), "?", cols=132).rows == 10
    with pytest.raises(ProtocolError):
        Screen.from_wire(ims, "A", rows=24, cols=80)
    with pytest.raises(ProtocolError):
        Screen.from_wire(snapshot_result(text="Z" * 1000), "D")


def test_screen_from_wire_fields_and_masking() -> None:
    text = list(" " * 1920)
    text[(12 - 1) * 80 + 20:(12 - 1) * 80 + 28] = "A000000 "
    text[(13 - 1) * 80 + 20:(13 - 1) * 80 + 28] = "segredo!"          # o worker já deveria ter mascarado
    result = snapshot_result(text="".join(text), fields=[
        {"start": [12, 21], "length": 8, "attr": 0xC1},
        {"start": [13, 21], "length": 8, "attr": 0xCD},                 # oculto + MDT
        {"start": [1, 1], "length": 0, "attr": 0xE0},
        {"start": 1911, "length": 20, "attr": 0xE0},                    # dá a volta no fim da tela
    ])
    screen = Screen.from_wire(result, "D")
    assert screen.fields is not None and len(screen.fields) == 4
    user, password, empty, wrap = screen.fields
    assert user == Field(Cursor(12, 21), 8, 0xC1, "A000000 ")
    assert password.hidden and password.text == " " * 8
    assert "segredo" not in screen.text                              # defesa em profundidade
    assert empty.length == 0 and empty.text == ""
    assert wrap.start == Cursor(24, 71) and len(wrap.text) == 20


@pytest.mark.parametrize("bad", [
    {"text": ""}, {"text": None}, {"cursor": [0]}, {"cursor": "22,56"}, {"cursor": [True, 1]}, {"oia": "zz"},
    {"oia": "00" * 102}, {"busy": 0}, {"hidden_unknown": None}, {"fields": {}}, {"fields": [{"start": [1, 1]}]},
    {"fields": [{"start": [1, 1], "length": 1, "attr": 300}]}, {"fields": [{"start": [99, 1], "length": 1,
                                                                             "attr": 0xC0}]},
])
def test_screen_from_wire_rejects_garbage(bad: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError):
        Screen.from_wire(snapshot_result(**bad), "D")
    with pytest.raises(ProtocolError):
        Screen.from_wire(["nao", "e", "dict"], "D")  # type: ignore[arg-type]


def test_screen_is_frozen_and_replaceable() -> None:
    screen = Screen.from_wire(snapshot_result(), "D")
    with pytest.raises(dataclasses.FrozenInstanceError):
        screen.text = ""  # type: ignore[misc]
    matched = dataclasses.replace(screen, match=Cursor(1, 1))
    assert matched.match == Cursor(1, 1) and screen.match is None


def test_from_wire_requires_busy_and_hidden_unknown() -> None:
    """``hidden_unknown`` ausente nunca vira "máscara conhecida" (False é o lado inseguro, B.7/D.6)."""
    for key in ("busy", "hidden_unknown"):
        result = {k: v for k, v in snapshot_result().items() if k != key}
        with pytest.raises(ProtocolError):
            Screen.from_wire(result, "D")
    assert Screen.from_wire(snapshot_result(hidden_unknown=True), "D").hidden_unknown is True


@pytest.mark.parametrize("raw", [103, "x" * 103, [0] * 103, None, 1.5])
def test_oia_from_raw_refuses_non_bytes(raw: Any) -> None:
    with pytest.raises(TypeError):
        OIA.from_raw(raw)                                             # bytes(103) seria um OIA de zeros


def test_oia_from_raw_accepts_buffers() -> None:
    assert OIA.from_raw(memoryview(LIVE_OIA)) == OIA.from_raw(bytearray(LIVE_OIA)) == OIA.from_raw(LIVE_OIA)


def test_reprs_never_show_screen_content() -> None:
    """``repr`` vai para tracebacks, saídas do pytest e logs com ``%r``; uma tela com ``hidden_unknown=True``
    pode ter conteúdo oculto não mascarado (B.4.5, D.6, T-SEC-1)."""
    from rumbapy._types import FieldRead, ReadResult

    text = list(" " * 1920)
    text[(13 - 1) * 80 + 20:(13 - 1) * 80 + 28] = "SENHA123"
    result = snapshot_result(text="".join(text), hidden_unknown=True, busy=True,
                             fields=[{"start": [13, 21], "length": 8, "attr": 0xC1}])    # máscara falhou
    screen = Screen.from_wire(result, "D")
    assert "SENHA123" in screen.text                                  # o conteúdo existe...
    shown = repr(screen)
    assert "SENHA123" not in shown                                    # ...mas nunca no repr
    assert shown == "<Screen D 24x80 · cursor (22,56) · teclado livre · host ocupado · máscara desconhecida · " \
                    "1 campo>"
    assert "SENHA123" not in repr(screen.fields) and "SENHA123" not in repr(dataclasses.replace(screen, match="ok"))
    assert "SENHA123" not in repr(ReadResult("SENHA123", False, -1, 0))
    assert "SENHA123" not in repr(FieldRead("SENHA123", (13, 21), 8, 0xC1, 0))
    assert repr(Screen.from_wire(snapshot_result(), "D")) == "<Screen D 24x80 · cursor (22,56) · teclado livre>"


def test_field_end_uses_the_screen_geometry() -> None:
    result = snapshot_result(fields=[
        {"start": [12, 21], "length": 8, "attr": 0xC1},
        {"start": [1, 80], "length": 3, "attr": 0xC0},                  # quebra de linha
        {"start": 1911, "length": 20, "attr": 0xE0},                    # dá a volta no fim da tela
        {"start": [5, 5], "length": 0, "attr": 0xE0},                   # sem dados: end == start
    ])
    user, wrapped_line, wrapped_screen, empty = Screen.from_wire(result, "D").fields or ()
    assert user.end == Cursor(12, 28)
    assert wrapped_line.end == Cursor(2, 2)
    assert wrapped_screen.end == Cursor(1, 10)
    assert empty.end == Cursor(5, 5)
    assert (user.rows, user.cols) == (24, 80)
    ims = Screen.from_wire(snapshot_result(text=" " * 2560, fields=[{"start": [32, 75], "length": 10, "attr": 0xC0}]),
                           "A")
    assert ims.fields is not None and ims.fields[0].end == Cursor(1, 4)  # 32x80: volta só depois da linha 32
    by_hand = Field(Cursor(24, 75), 10, 0xC0, " " * 10)                # sem rows: não dá a volta
    assert by_hand.end == Cursor(25, 4)
    assert by_hand == Field(Cursor(24, 75), 10, 0xC0, " " * 10, rows=24, cols=80)   # geometria fora da comparação


# B.5: assinaturas dos métodos do P-A — (nome, tipo de parâmetro, padrão); as anotações não são comparadas
POK, KW, EMPTY = inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty
SCREEN_METHODS: dict[str, list[tuple[str, Any, Any]]] = {
    "line": [("row", POK, EMPTY)],
    "get": [("row", POK, EMPTY), ("col", POK, EMPTY), ("length", POK, EMPTY)],
    "find": [("text", POK, EMPTY), ("case", KW, True)],
    "contains": [("text", POK, EMPTY), ("row", POK, None), ("col", POK, None)],
    "__contains__": [("text", POK, EMPTY)],
    "field_at": [("row", POK, EMPTY), ("col", POK, EMPTY)],
    "table": [("first_row", POK, EMPTY), ("last_row", POK, EMPTY), ("columns", POK, EMPTY), ("strip", KW, True),
              ("skip_blank", KW, True)],
    "to_text": [("ruler", KW, True)],
    "__str__": [],
    "_repr_html_": [],
}


def _params(fn: Any) -> list[tuple[str, Any, Any]]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values() if p.name != "self"]


def test_p_a_signatures_follow_b5() -> None:
    for name, expected in SCREEN_METHODS.items():
        assert _params(getattr(Screen, name)) == expected, name
    for name in ("lines", "input_fields"):
        assert isinstance(inspect.getattr_static(Screen, name), property), name
    assert inspect.signature(Screen.from_wire).parameters["terminal"].kind is POK


P_A_CALLS: dict[str, Any] = {
    "lines": lambda s: s.lines, "line": lambda s: s.line(1), "get": lambda s: s.get(1, 1, 5),
    "find": lambda s: s.find("V"), "contains": lambda s: s.contains("V"), "__contains__": lambda s: "V" in s,
    "field_at": lambda s: s.field_at(1, 1), "input_fields": lambda s: s.input_fields,
    "table": lambda s: s.table(1, 2, {"a": (1, 2)}), "to_text": lambda s: s.to_text(), "__str__": str,
    "_repr_html_": lambda s: s._repr_html_(),
}


def test_p_a_methods_are_implemented() -> None:
    """O P-A entregou os métodos de ``Screen`` (comportamento testado em ``tests/unit/core/test_screen.py``):
    nenhum deles é mais stub, e todos funcionam com argumentos válidos."""
    screen = Screen.from_wire(snapshot_result(fields=[{"start": [1, 2], "length": 5, "attr": 0xC0}]), "D")
    for name, call in P_A_CALLS.items():
        try:
            call(screen)
        except NotImplementedError as exc:                            # pragma: no cover - regressão
            pytest.fail(f"Screen.{name} voltou a ser stub: {exc}")
    assert screen.get(1, 1, 5) == "VOLVO" and "VOLVO" in screen and screen.find("V") == Cursor(1, 1)
    assert screen.field_at(1, 1) is not None and screen.input_fields
