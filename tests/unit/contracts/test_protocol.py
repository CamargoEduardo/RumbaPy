"""Aceite P-0: ``_protocol`` (C.1-C.4) — tabela OPS, validação de requisições, codificação e respostas."""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from rumbapy.errors import InvalidPosition, KeyboardLocked, ParameterError, ProtocolError, UndefinedKey
from rumbapy import _protocol as P

import json

import pytest

TABLE = {   # C.4: op -> (mode, args, I, conn, hwnd, prazo)
    "ping": ("both", {}, True, False, False, 5.0),
    "sessions": ("both", {}, True, False, False, 25.0),
    "connect": ("owner", {"letter": str}, True, False, False, 25.0),
    "disconnect": ("owner", {}, True, False, False, 25.0),
    "status": ("owner", {}, True, True, True, 25.0),
    "snapshot": ("owner", {"fields": bool}, True, True, True, 25.0),
    "read": ("owner", {"row": int, "col": int, "length": int}, True, True, True, 25.0),
    "read_field": ("owner", {"row": int, "col": int}, True, True, True, 25.0),
    "write": ("owner", {"row": int, "col": int, "text": str, "secret": bool}, False, True, True, 45.0),
    "send": ("owner", {"chunks": list, "secret": bool}, False, True, True, 25.0),
    "set_cursor": ("owner", {"row": int, "col": int}, False, True, True, 25.0),
    "evict": ("admin", {"reason": str}, False, False, False, 25.0),
    "shutdown": ("both", {"reason": str}, False, False, False, 30.0),
}


def test_ops_table_is_exactly_c4() -> None:
    assert list(P.OPS) == list(TABLE)
    for name, (mode, args, idempotent, conn, hwnd, timeout) in TABLE.items():
        op = P.OPS[name]
        assert op.name == name
        assert (op.mode, dict(op.args), op.idempotent, op.needs_connection, op.checks_hwnd, op.client_timeout) == \
            (mode, args, idempotent, conn, hwnd, timeout)
    assert P.ADMIN_OPS == {"ping", "sessions", "evict", "shutdown"}
    assert P.OWNER_OPS == set(P.OPS) - {"evict"}
    assert set(P.RESULT_KEYS) == set(P.OPS)


def test_ops_are_immutable() -> None:
    with pytest.raises(TypeError):
        P.OPS["novo"] = P.OPS["ping"]  # type: ignore[index]
    with pytest.raises(TypeError):
        P.OPS["read"].args["x"] = int  # type: ignore[index]


def test_client_timeouts() -> None:
    assert P.client_timeout("read", {"row": 1, "col": 1, "length": 1}) == 25.0
    assert P.client_timeout("snapshot", {"fields": False}) == 25.0
    assert P.client_timeout("snapshot", {"fields": True}) == 90.0
    assert P.client_timeout("send", {"chunks": ["a", "b", "@E"], "secret": False}) == 25.0 * 3 + 5
    assert P.client_timeout("send") == 30.0
    assert P.client_timeout("ping") == 5.0


def test_constants() -> None:
    assert P.PROTOCOL == 2 and P.VERSION == "2.0.0"
    assert P.MAX_MESSAGE == 1_048_576 and P.AUTH_STEP_S == 2.0 and P.MAX_CONNECTIONS == 8
    assert P.AUTH_TAG_CLIENT == b"rumbapy2|c|" and P.AUTH_TAG_WORKER == b"rumbapy2|w|"
    assert P.EXIT_CODES[P.EXIT_SANDBOX] and set(P.EXIT_CODES) == {0, 1, 3, 5, 10, 11, 12, 13, 14, 15, 16, 17, 18}


def req(op: str, args: dict[str, Any] | None = None, rid: int = 1, hwnd: int | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"id": rid, "op": op, "args": args or {}}
    if hwnd is not None:
        msg["hwnd"] = hwnd
    return msg


VALID = {
    "ping": {}, "sessions": {}, "connect": {"letter": "D"}, "disconnect": {}, "status": {},
    "snapshot": {"fields": True}, "read": {"row": 23, "col": 7, "length": 74}, "read_field": {"row": 22, "col": 56},
    "write": {"row": 12, "col": 21, "text": "A000000", "secret": False},
    "send": {"chunks": ["A000000@T", "@E"], "secret": True}, "set_cursor": {"row": 1, "col": 1},
    "evict": {"reason": "close"}, "shutdown": {"reason": "build"},
}


@pytest.mark.parametrize("op", list(VALID))
def test_valid_requests(op: str) -> None:
    request = P.validate_request(json.loads(json.dumps(req(op, VALID[op], rid=17, hwnd=1234567))))
    assert (request.id, request.op, request.args, request.hwnd) == (17, op, VALID[op], 1234567)
    assert request.spec is P.OPS[op]


@pytest.mark.parametrize("msg", [
    "nao-e-objeto", {"op": "ping"}, {"id": 0, "op": "ping"}, {"id": True, "op": "ping"}, {"id": "1", "op": "ping"},
    {"id": 1}, {"id": 1, "op": 3}, {"id": 1, "op": "ping", "args": []}, {"id": 1, "op": "ping", "hwnd": "x"},
    {"id": 1, "op": "ping", "hwnd": False}, {"id": 1, "op": "ping", "extra": 1},
])
def test_bad_envelopes_are_protocol_errors(msg: Any) -> None:
    with pytest.raises(ProtocolError):
        P.validate_request(msg, letter="D")


@pytest.mark.parametrize("op, args", [
    ("teleport", {}),
    ("read", {"row": 1, "col": 1}),                                     # ausente
    ("read", {"row": 1, "col": 1, "length": 1, "x": 2}),                # desconhecido
    ("read", {"row": True, "col": 1, "length": 1}),                     # bool não vale como int
    ("read", {"row": 1.0, "col": 1, "length": 1}),
    ("read", {"row": 1, "col": 1, "length": 0}),
    ("snapshot", {"fields": 1}),
    ("connect", {"letter": "d"}), ("connect", {"letter": "DD"}), ("connect", {"letter": "_"}),
    ("write", {"row": 1, "col": 1, "text": "", "secret": False}),
    ("write", {"row": 1, "col": 1, "text": "a\tb", "secret": False}),
    ("write", {"row": 1, "col": 1, "text": "seta→", "secret": False}),   # fora do cp1252
    ("send", {"chunks": [], "secret": False}),
    ("send", {"chunks": ["a"] * 65, "secret": False}),
    ("send", {"chunks": ("a",), "secret": False}),                      # tupla não é lista no wire
    ("send", {"chunks": [""], "secret": False}),
    ("send", {"chunks": [1], "secret": False}),
    ("send", {"chunks": ["a" * 129], "secret": False}),
    ("send", {"chunks": ["@E", "abc"], "secret": False}),               # AID fora do último chunk
    ("send", {"chunks": ["abc@E"], "secret": False}),                   # AID junto com texto
    ("send", {"chunks": ["@E@E"], "secret": False}),
    ("send", {"chunks": ["@T@u"], "secret": False}),                    # PAGE_DOWN/UP também é AID
    ("evict", {"reason": ""}), ("shutdown", {"reason": "x" * 65}),
])
def test_bad_arguments_are_parameter_errors(op: str, args: dict[str, Any]) -> None:
    with pytest.raises(ParameterError) as info:
        P.validate_request(req(op, args))
    assert info.value.delivery == "none"


def test_positions_are_not_checked_without_the_geometry() -> None:
    """Sem ``rows``/``cols`` a posição não é conferida aqui (e nunca vira ``ParameterError``)."""
    assert P.validate_request(req("read", {"row": 0, "col": -1, "length": 1})).args["row"] == 0
    assert P.validate_request(req("set_cursor", {"row": 99, "col": 1})).args["row"] == 99


@pytest.mark.parametrize("op, args, rows", [
    ("read", {"row": 0, "col": 1, "length": 1}, 24),
    ("read", {"row": 1, "col": 0, "length": 1}, 24),
    ("read", {"row": -3, "col": 1, "length": 1}, 24),
    ("read", {"row": 25, "col": 1, "length": 1}, 24),
    ("read", {"row": 24, "col": 80, "length": 2}, 24),               # passa do fim da tela
    ("read", {"row": 1, "col": 1, "length": 1921}, 24),
    ("read_field", {"row": 1, "col": 81}, 24),
    ("write", {"row": 0, "col": 5, "text": "x", "secret": False}, 24),
    ("set_cursor", {"row": 33, "col": 1}, 32),
])
def test_positions_outside_the_screen_are_invalid_position(op: str, args: dict[str, Any], rows: int) -> None:
    with pytest.raises(InvalidPosition) as info:
        P.validate_request(req(op, args), rows=rows, cols=80)
    exc = info.value
    assert isinstance(exc, IndexError) and not isinstance(exc, ValueError)
    assert exc.delivery == "none" and f"{rows}x80" in exc.message


def test_positions_inside_the_screen_pass() -> None:
    P.validate_request(req("read", {"row": 24, "col": 80, "length": 1}), rows=24, cols=80)
    P.validate_request(req("read", {"row": 1, "col": 1, "length": 1920}), rows=24, cols=80)
    P.validate_request(req("read", {"row": 32, "col": 1, "length": 80}), rows=32, cols=80)     # IMS A
    P.validate_request(req("write", {"row": 13, "col": 21, "text": "x", "secret": True}), rows=24, cols=80)
    P.validate_request(req("ping", {}), rows=24, cols=80)                                       # op sem posição
    with pytest.raises(ParameterError):
        P.validate_request(req("read", {"row": 1, "col": 1, "length": 0}), rows=24, cols=80)


def test_secret_send_never_reveals_sizes() -> None:
    with pytest.raises(ParameterError) as info:
        P.validate_request(req("send", {"chunks": ["s" * 130], "secret": True}))
    assert info.value.message == "send: chunk 0 excede 128 bytes" and "130" not in str(info.value)
    with pytest.raises(ParameterError) as info:
        P.validate_request(req("send", {"chunks": ["a"] * 65, "secret": True}))
    assert "65" not in str(info.value)
    with pytest.raises(ParameterError) as info:                         # sem segredo, o tamanho ajuda a depurar
        P.validate_request(req("send", {"chunks": ["s" * 130], "secret": False}))
    assert "130 bytes" in info.value.message


def test_send_chunk_limits_are_in_cp1252_bytes() -> None:
    P.validate_request(req("send", {"chunks": ["ç" * 128], "secret": False}))
    P.validate_request(req("send", {"chunks": ["a@@" * 42 + "bc"], "secret": False}))      # 128 bytes, com escapes
    with pytest.raises(ParameterError):
        P.validate_request(req("send", {"chunks": ["a@@" * 42 + "bcd"], "secret": False}))  # 129 bytes
    P.validate_request(req("send", {"chunks": ["abc", "@A@F", "@E"], "secret": False}))
    P.validate_request(req("send", {"chunks": ["@A@H"], "secret": False}))                  # SYSREQ sozinho


def test_bad_mnemonic_in_send() -> None:
    with pytest.raises(UndefinedKey) as info:
        P.validate_request(req("send", {"chunks": ["ab@!"], "secret": False}))
    assert "@!" in info.value.message
    with pytest.raises(UndefinedKey) as info:
        P.validate_request(req("send", {"chunks": ["QWERTY@!XYZ"], "secret": True}))
    text = str(info.value)
    assert "@!" not in text and "QWERTY" not in text and "XYZ" not in text


def test_mode_rules() -> None:
    for op in P.OPS:
        allowed_admin = op in P.ADMIN_OPS
        allowed_owner = op in P.OWNER_OPS
        for mode, allowed in (("admin", allowed_admin), ("owner", allowed_owner)):
            if allowed:
                P.validate_request(req(op, VALID[op]), mode=mode)
            else:
                with pytest.raises(ParameterError):
                    P.validate_request(req(op, VALID[op]), mode=mode)


def test_encode_decode() -> None:
    msg = {"id": 1, "ok": True, "result": {"text": "Atualização €"}, "ms": 1.3}
    data = P.encode(msg)
    assert data == json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert P.decode(data) == msg
    with pytest.raises(ProtocolError):
        P.decode(b"\xff\xfe")
    with pytest.raises(ProtocolError):
        P.decode(b"[1, 2]")
    with pytest.raises(ProtocolError):
        P.decode(b'{"x": NaN}')
    with pytest.raises(ProtocolError):
        P.decode(b"{" * 10)
    with pytest.raises(ProtocolError):
        P.encode({"x": float("nan")})
    with pytest.raises(ProtocolError):
        P.encode({"x": object()})


def test_message_size_limit() -> None:
    big = {"text": "a" * P.MAX_MESSAGE}
    with pytest.raises(ProtocolError):
        P.encode(big)
    with pytest.raises(ProtocolError):
        P.decode(b'"' + b"a" * P.MAX_MESSAGE + b'"')
    assert len(P.encode({"t": "a" * (P.MAX_MESSAGE - 20)})) <= P.MAX_MESSAGE


def test_builders_and_reply_unwrapping() -> None:
    assert P.make_request(3, "read", {"row": 1, "col": 1, "length": 1}, hwnd=99) == \
        {"id": 3, "op": "read", "args": {"row": 1, "col": 1, "length": 1}, "hwnd": 99}
    assert "hwnd" not in P.make_request(4, "connect", {"letter": "D"}, hwnd=99)      # connect não confere hwnd
    ok = P.make_result(3, {"text": "x"}, ms=1.23456)
    assert ok == {"id": 3, "ok": True, "result": {"text": "x"}, "ms": 1.235}
    assert P.unwrap_reply(ok, 3) == {"text": "x"}
    err = P.make_error(5, KeyboardLocked.make(rc=5, function="WD_SendKey", delivery="partial",
                                              details={"chunks_sent": 1}))
    with pytest.raises(KeyboardLocked) as info:
        P.unwrap_reply(json.loads(json.dumps(err)), 5, letter="D")
    assert info.value.delivery == "partial" and info.value.terminal == "D" and info.value.details == {"chunks_sent": 1}
    event = P.make_event("evicted", "close")
    assert P.is_event(event) and not P.is_event(ok)
    cases = [(event, 3), ({"id": 4, "ok": True, "result": {}}, 3), ({"id": 3, "ok": True}, 3), ({"id": 3}, 3),
             ({"id": True, "ok": True, "result": {}}, 1), ({"id": 3, "ok": True, "result": []}, 3)]
    for bad, expected_id in cases:
        with pytest.raises(ProtocolError):
            P.unwrap_reply(bad, expected_id)
    with pytest.raises(ValueError):
        P.make_event("explodiu", "x")
