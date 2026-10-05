"""Aceite P-A: métodos de ``Screen`` (B.5) — consulta, campos, tabela e formatação."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import InvalidPosition, ParameterError
from rumbapy.screen import Cursor
from tests.unit.core.screens import make_screen
from pathlib import Path

import dataclasses
import time
import ast
import re

import pytest

LISTING = {
    1: " VOLVO DO BRASIL                 CONSULTA DE PECAS".ljust(73) + "SP0042T",
    3: " CODIGO    DESCRICAO                 QTD",
    4: " 1234567   PARAFUSO <M8> & PORCA      10",
    5: "",
    6: " 7654321   ARRUELA                    2",
    7: "           SO DESCRICAO",
    24: "ULTIMA LINHA",
}


@pytest.fixture
def screen():
    return make_screen(LISTING, cursor=(22, 56))


# ------------------------------------------------------------------ lines / line / get

def test_lines_and_line(screen) -> None:
    assert len(screen.lines) == 24 and all(len(line) == 80 for line in screen.lines)
    assert screen.lines[0] == LISTING[1].ljust(80)
    assert screen.line(1) == LISTING[1].ljust(80)
    assert screen.line(24).startswith("ULTIMA LINHA")
    ims = make_screen({32: "FIM"}, rows=32)
    assert len(ims.lines) == 32 and ims.line(32).startswith("FIM")


@pytest.mark.parametrize("row", [0, 25, -1])
def test_line_out_of_screen(screen, row: int) -> None:
    with pytest.raises(InvalidPosition) as info:
        screen.line(row)
    assert info.value.message.endswith("está fora da tela 24x80.")


def test_line_rejects_non_int(screen) -> None:
    for bad in ("1", 1.0, True, None):
        with pytest.raises(ParameterError):
            screen.line(bad)


def test_get(screen) -> None:
    assert screen.get(1, 2, 5) == "VOLVO"
    assert screen.get(1, 74, 7) == "SP0042T"
    assert screen.get(1, 80, 3) == "T  "                     # continua na linha seguinte (texto linear)
    assert screen.get(24, 1, 80) == "ULTIMA LINHA".ljust(80)
    assert len(screen.get(1, 1, 1920)) == 1920


def test_get_validates(screen) -> None:
    with pytest.raises(InvalidPosition):
        screen.get(24, 80, 2)                                # passa do fim da tela
    with pytest.raises(InvalidPosition):
        screen.get(0, 1, 1)
    with pytest.raises(InvalidPosition):
        screen.get(1, 81, 1)
    with pytest.raises(ParameterError):
        screen.get(1, 1, 0)
    with pytest.raises(ParameterError):
        screen.get(1, 1, "5")


# ------------------------------------------------------------------ find / contains

def test_find(screen) -> None:
    assert screen.find("VOLVO") == Cursor(1, 2)
    assert screen.find("SP0042T") == Cursor(1, 74)
    assert screen.find("ARRUELA") == Cursor(6, 12)
    assert screen.find("NAO EXISTE") is None
    assert screen.find("volvo") is None
    assert screen.find("volvo", case=False) == Cursor(1, 2)
    assert screen.find("T ") == Cursor(1, 80)                # atravessa o fim da linha


def test_find_pattern(screen) -> None:
    assert screen.find(re.compile(r"SP\d{4}T")) == Cursor(1, 74)
    assert screen.find(re.compile(r"\d{7}")) == Cursor(4, 2)
    assert screen.find(re.compile(r"parafuso")) is None
    assert screen.find(re.compile(r"parafuso"), case=False) == Cursor(4, 12)
    assert screen.find(re.compile(r"(?i)arruela")) == Cursor(6, 12)


def test_find_validates(screen) -> None:
    for bad in ("", None, 3, b"VOLVO"):
        with pytest.raises(ParameterError):
            screen.find(bad)
    with pytest.raises(ParameterError):
        screen.find(re.compile(rb"VOLVO"))


def test_contains(screen) -> None:
    assert screen.contains("VOLVO")
    assert "VOLVO" in screen and "PECAS" in screen
    assert "volvo" not in screen
    assert screen.contains("VOLVO", 1) and not screen.contains("VOLVO", 2)
    assert screen.contains("VOLVO", 1, 2) and not screen.contains("VOLVO", 1, 3)
    assert screen.contains("ARRUELA", 6, 12)
    assert not screen.contains("ULTIMA LINHA E MAIS", 24, 70)   # passaria do fim da tela: não está lá


def test_contains_validates(screen) -> None:
    with pytest.raises(ParameterError):
        screen.contains("VOLVO", col=2)
    with pytest.raises(ParameterError):
        screen.contains("")
    with pytest.raises(ParameterError):
        screen.contains(3)  # type: ignore[arg-type]
    with pytest.raises(InvalidPosition):
        screen.contains("X", 25)
    with pytest.raises(InvalidPosition):
        screen.contains("X", 1, 81)


# ------------------------------------------------------------------ campos

FIELDS = [
    {"start": [1, 10], "length": 70, "attr": 0xE0},          # protegido: título (atributo em (1,9))
    {"start": [12, 21], "length": 8, "attr": 0xC1},          # usuário (desprotegido, MDT)
    {"start": [12, 30], "length": 50, "attr": 0xE0},         # rótulo protegido
    {"start": [13, 21], "length": 8, "attr": 0xCC},          # senha (oculta)
    {"start": [13, 30], "length": 0, "attr": 0xE0},          # campo sem dados (atributos colados)
    {"start": [24, 75], "length": 10, "attr": 0xC0},         # dá a volta no fim da tela
]


def test_field_at() -> None:
    screen = make_screen({12: " " * 20 + "A000000 "}, fields=FIELDS)
    assert screen.fields is not None
    title, user, label, password, empty, wrap = screen.fields
    assert screen.field_at(1, 10) == title and screen.field_at(1, 79) == title
    assert screen.field_at(1, 9) == title                    # byte de atributo pertence ao campo que ele abre
    assert screen.field_at(1, 80) is None and screen.field_at(1, 5) is None
    assert screen.field_at(12, 21) == user and screen.field_at(12, 28) == user
    assert screen.field_at(12, 29) == label                  # atributo do rótulo
    assert screen.field_at(13, 29) == empty and screen.field_at(13, 30) is None
    assert screen.field_at(13, 21) == password
    assert screen.field_at(24, 80) == wrap and screen.field_at(1, 1) == wrap and screen.field_at(1, 4) == wrap
    assert screen.field_at(24, 74) == wrap                   # atributo
    assert screen.field_at(5, 5) is None                     # fora de qualquer campo
    assert user.text == "A000000 "


def test_field_at_validates() -> None:
    with pytest.raises(ParameterError) as info:
        make_screen().field_at(1, 1)
    assert "snapshot(fields=True)" in info.value.message
    with pytest.raises(InvalidPosition):
        make_screen(fields=FIELDS).field_at(25, 1)
    assert make_screen(fields=[]).field_at(1, 1) is None    # tela não formatada


def test_input_fields() -> None:
    shuffled = [FIELDS[3], FIELDS[0], FIELDS[5], FIELDS[1]]
    screen = make_screen(fields=shuffled)
    starts = [f.start for f in screen.input_fields]
    assert starts == [Cursor(12, 21), Cursor(13, 21), Cursor(24, 75)]
    assert all(not f.protected for f in screen.input_fields)
    assert make_screen(fields=[]).input_fields == []
    with pytest.raises(ParameterError):
        make_screen().input_fields


# ------------------------------------------------------------------ table

COLUMNS = {"codigo": (2, 7), "descricao": (12, 25), "qtd": (38, 3)}


def test_table(screen) -> None:
    assert screen.table(4, 7, COLUMNS) == [
        {"codigo": "1234567", "descricao": "PARAFUSO <M8> & PORCA", "qtd": "10"},
        {"codigo": "7654321", "descricao": "ARRUELA", "qtd": "2"},
        {"codigo": "", "descricao": "SO DESCRICAO", "qtd": ""},
    ]


def test_table_options(screen) -> None:
    raw = screen.table(4, 5, COLUMNS, strip=False, skip_blank=False)
    assert raw == [
        {"codigo": "1234567", "descricao": "PARAFUSO <M8> & PORCA    ", "qtd": " 10"},
        {"codigo": " " * 7, "descricao": " " * 25, "qtd": " " * 3},
    ]
    assert screen.table(5, 5, COLUMNS) == []
    assert screen.table(5, 5, COLUMNS, skip_blank=False) == [{"codigo": "", "descricao": "", "qtd": ""}]
    assert screen.table(24, 24, {"x": (1, 80)}) == [{"x": "ULTIMA LINHA"}]


@pytest.mark.parametrize("args, error", [
    ((5, 4, COLUMNS), ParameterError),
    ((0, 4, COLUMNS), InvalidPosition),
    ((1, 25, COLUMNS), InvalidPosition),
    ((1, 2, {}), ParameterError),
    ((1, 2, [("a", (1, 2))]), ParameterError),
    ((1, 2, {"a": (0, 2)}), InvalidPosition),
    ((1, 2, {"a": (79, 3)}), InvalidPosition),
    ((1, 2, {"a": (1, 0)}), ParameterError),
    ((1, 2, {"a": (1,)}), ParameterError),
    ((1, 2, {"a": ("1", 2)}), ParameterError),
    ((1, 2, {1: (1, 2)}), ParameterError),
])
def test_table_validates(screen, args: tuple, error: type) -> None:
    with pytest.raises(error):
        screen.table(*args)


# ------------------------------------------------------------------ to_text / str / html

def test_to_text(screen) -> None:
    text = screen.to_text()
    lines = text.split("\n")
    assert len(lines) == 2 + 24 + 1
    assert lines[0] == "    " + "".join(f"{' ' * 9}{n % 10}" for n in range(1, 9))
    assert lines[1] == "    " + "1234567890" * 8
    assert lines[2] == "  1 " + LISTING[1].ljust(80)
    assert lines[25] == " 24 " + "ULTIMA LINHA".ljust(80)
    hour = time.strftime("%H:%M:%S", time.localtime(screen.taken_at))
    assert lines[-1] == f"cursor (22,56) · teclado livre · 24x80 · D BRAZIL_D · {hour}"
    assert str(screen) == text


def test_to_text_without_ruler(screen) -> None:
    lines = screen.to_text(ruler=False).split("\n")
    assert lines[:24] == screen.lines
    assert lines[24].startswith("cursor (22,56) · teclado livre · 24x80 · D BRAZIL_D · ")


def test_footer_states() -> None:
    locked = make_screen(cursor=(1, 1), locked=True, busy=True, hidden_unknown=True, terminal="A", rows=32)
    footer = locked.to_text().split("\n")[-1]
    assert footer.startswith("cursor (1,1) · teclado bloqueado (G8=00 20 00 00 00) · host ocupado · "
                             "máscara desconhecida · 32x80 · A IMS_A · ")
    assert len(locked.to_text().split("\n")) == 2 + 32 + 1
    offline = make_screen(online=False, terminal="X")
    assert " · sem conexão com o host · 24x80 · X · " in offline.to_text()
    odd = dataclasses.replace(make_screen(), taken_at=1e300)            # hora impossível: o rodapé não falha
    assert odd.to_text().split("\n")[-1].endswith("D BRAZIL_D")


def test_repr_html_escapes_and_marks_cursor() -> None:
    screen = make_screen({1: "<script>alert('x')</script> & \"q\""}, cursor=(1, 2))
    out = screen._repr_html_()
    assert out.startswith("<pre") and out.endswith("</pre>")
    assert "<script>" not in out and "</script>" not in out and "script&gt;" in out
    assert "&amp;" in out and "&quot;q&quot;" in out and "&#x27;x&#x27;" in out
    assert out.count("<span") == 1
    assert '">s</span>' in out                                          # o caractere em (1,2) é o "s" de "<script>"
    assert "cursor (1,2) · teclado livre" in out


def test_repr_html_cursor_on_last_position() -> None:
    out = make_screen({24: "X" * 80}, cursor=(24, 80))._repr_html_()
    assert '">X</span>\n' in out


def test_str_and_repr_are_safe_with_hidden_fields() -> None:
    screen = make_screen(at={(13, 21): "SEGREDO1"}, fields=[{"start": [13, 21], "length": 8, "attr": 0xCC}])
    for shown in (str(screen), screen.to_text(ruler=False), screen._repr_html_(), repr(screen)):
        assert "SEGREDO1" not in shown


def test_screen_methods_keep_the_dataclass_frozen(screen) -> None:
    screen.to_text()
    with pytest.raises(dataclasses.FrozenInstanceError):
        screen.text = ""  # type: ignore[misc]



def test_screen_does_not_import_profiles_at_module_level() -> None:
    """A.4.1/A.4.3: ``screen`` é puro; o nome do perfil no rodapé vem de um import tardio de ``profiles``."""
    import rumbapy.screen as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = {(node.module or "") for node in top if isinstance(node, ast.ImportFrom)}
    names |= {alias.name for node in top for alias in node.names}
    assert not any("profiles" in name for name in names)
