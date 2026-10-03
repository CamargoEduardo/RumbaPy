"""Aceite P-0: ``Key``, ``AIDS``, ``Segment`` e ``tokenize_mnemonic`` (B.6); assinaturas e stubs do P-A."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rumbapy.keys import AIDS, Key, Segment, tokenize_mnemonic
from rumbapy.errors import ParameterError, UndefinedKey
from rumbapy import keys

import dataclasses
import inspect

import pytest

AID_NAMES = {"ENTER", "CLEAR", "PA1", "PA2", "PA3", "SYSREQ", "ATTN", "PAGE_UP", "PAGE_DOWN",
             *(f"PF{n}" for n in range(1, 25))}


def test_key_values_follow_the_mnemonic_table() -> None:
    assert Key.ENTER.value == "@E" and Key.CLEAR.value == "@C"
    assert [Key[f"PF{n}"].value for n in range(1, 13)] == [f"@{c}" for c in "123456789abc"]
    assert [Key[f"PF{n}"].value for n in range(13, 25)] == [f"@{c}" for c in "defghijklmno"]
    assert (Key.PA1.value, Key.PA2.value, Key.PA3.value) == ("@x", "@y", "@z")
    assert (Key.SYSREQ.value, Key.ATTN.value) == ("@A@H", "@A@Q")
    assert (Key.PAGE_UP.value, Key.PAGE_DOWN.value) == ("@u", "@v")
    assert (Key.ERASE_INPUT.value, Key.FIELD_EXIT.value, Key.CURSOR_SELECT.value) == ("@A@F", "@A@E", "@A@J")
    assert (Key.DUP.value, Key.FIELD_MARK.value) == ("@S@x", "@S@y")
    assert (Key.RESET.value, Key.TAB.value, Key.BACKTAB.value, Key.HOME.value, Key.END.value) == \
        ("@R", "@T", "@B", "@0", "@q")
    assert len({k.value for k in Key}) == len(list(Key))       # sem valores repetidos entre membros canônicos


def test_aliases_are_the_same_members() -> None:
    for n in range(1, 25):
        assert Key[f"F{n}"] is Key[f"PF{n}"]
    assert Key.PAGEUP is Key.PAGE_UP and Key.PAGEDOWN is Key.PAGE_DOWN and Key.ERASEEOF is Key.ERASE_EOF
    assert "F3" not in [k.name for k in Key]                   # aliases não aparecem na iteração


def test_key_is_not_a_str() -> None:
    assert not isinstance(Key.ENTER, str)
    assert Key.ENTER != "@E"


def test_aids() -> None:
    assert {k.name for k in AIDS} == AID_NAMES
    assert len(AIDS) == 33
    assert isinstance(AIDS, frozenset)
    for key in Key:
        assert key.is_aid == (key.name in AID_NAMES)
    assert Key.PAGE_DOWN.is_aid and not Key.TAB.is_aid and not Key.HELP.is_aid


def test_segment_is_frozen() -> None:
    seg = Segment(("abc", Key.TAB), Key.ENTER)
    with pytest.raises(dataclasses.FrozenInstanceError):
        seg.aid = None  # type: ignore[misc]
    assert seg == Segment(("abc", Key.TAB), Key.ENTER)


@pytest.mark.parametrize("text, expected", [
    ("SPRE@E", ["SPRE", Key.ENTER]),
    ("", []),
    ("abc", ["abc"]),
    ("@@", ["@"]),
    ("a@@b", ["a@b"]),
    ("@@@E", ["@", Key.ENTER]),
    ("@A@F", [Key.ERASE_INPUT]),
    ("x@A@Hy", ["x", Key.SYSREQ, "y"]),
    ("@S@x@S@y", [Key.DUP, Key.FIELD_MARK]),
    ("@T@B@0@q@N@U@V@L@Z@<@D@I@F@R@P@H", [Key.TAB, Key.BACKTAB, Key.HOME, Key.END, Key.NEWLINE, Key.UP, Key.DOWN,
                                          Key.LEFT, Key.RIGHT, Key.BACKSPACE, Key.DELETE, Key.INSERT, Key.ERASE_EOF,
                                          Key.RESET, Key.PRINT, Key.HELP]),
    ("@u@v@x@y@z@C", [Key.PAGE_UP, Key.PAGE_DOWN, Key.PA1, Key.PA2, Key.PA3, Key.CLEAR]),
    ("A000000@Tsenha@E", ["A000000", Key.TAB, "senha", Key.ENTER]),
    ("çã€", ["çã€"]),
])
def test_tokenize_mnemonic(text: str, expected: list) -> None:
    assert tokenize_mnemonic(text) == expected


def test_tokenize_every_key_value() -> None:
    for key in Key:
        assert tokenize_mnemonic(key.value) == [key]
        assert tokenize_mnemonic("a" + key.value + "b") == ["a", key, "b"]


@pytest.mark.parametrize("text, shown", [
    ("@", "@"), ("abc@", "@"), ("@!", "@!"), ("@A", "@A"), ("@A@", "@A@"), ("@A@Z", "@A@Z"), ("@S@z", "@S@z"),
    ("@A@@", "@A@@"), ("ok@W", "@W"),
])
def test_tokenize_invalid(text: str, shown: str) -> None:
    with pytest.raises(UndefinedKey) as info:
        tokenize_mnemonic(text)
    exc = info.value
    assert exc.message == f"Tecla '{shown}' desconhecida. Para digitar '@' use '@@'."
    assert exc.delivery == "none"
    assert isinstance(exc, ValueError)


def test_tokenize_refuses_non_str() -> None:
    with pytest.raises(ParameterError):
        tokenize_mnemonic(b"@E")  # type: ignore[arg-type]
    with pytest.raises(ParameterError):
        tokenize_mnemonic(Key.ENTER)  # type: ignore[arg-type]


# B.6: assinaturas — (nome, tipo de parâmetro, padrão); as anotações não são comparadas
POK, KW, EMPTY = inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty
SIGNATURES: dict[str, list[tuple[str, Any, Any]]] = {
    "tokenize_extra": [("s", POK, EMPTY)],
    "tokenize_mnemonic": [("s", POK, EMPTY)],
    "split_at_aids": [("tokens", POK, EMPTY)],
    "encode_body": [("body", POK, EMPTY), ("host", KW, EMPTY), ("errors", KW, "strict"), ("secret", KW, False)],
}


def _params(fn: Any) -> list[tuple[str, Any, Any]]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]


def test_signatures_follow_b6() -> None:
    for name, expected in SIGNATURES.items():
        assert _params(getattr(keys, name)) == expected, name
    assert isinstance(inspect.getattr_static(Key, "parse"), classmethod)
    assert isinstance(inspect.getattr_static(Key, "pf"), classmethod)
    assert isinstance(inspect.getattr_static(Key, "is_aid"), property)
    assert _params(Key.parse) == [("name", POK, EMPTY)]
    assert _params(Key.pf) == [("n", POK, EMPTY)]


P_A_CALLS: dict[str, Callable[[], Any]] = {
    "Key.parse": lambda: Key.parse("ENTER"), "Key.pf": lambda: Key.pf(3),
    "tokenize_extra": lambda: keys.tokenize_extra("a<Enter>"),
    "split_at_aids": lambda: keys.split_at_aids(["a", Key.ENTER]),
    "encode_body": lambda: keys.encode_body(["a"], host="cics"),
}


def test_p_a_pending_stubs_say_so() -> None:
    """Enquanto for stub, a função diz que é do P-A; implementada, sai da conta (o comportamento é testado pelo P-A
    em ``tests/unit/core``) — este arquivo do P-0 nunca precisa ser editado quando o P-A entregar."""
    pending = []
    for name, call in P_A_CALLS.items():
        try:
            call()
        except NotImplementedError as exc:
            assert "P-A" in str(exc), name
            pending.append(name)
        except Exception:                                             # implementada (pode recusar estes argumentos)
            continue
    if not pending:
        pytest.skip("o P-A já implementou todas as funções de keys")
