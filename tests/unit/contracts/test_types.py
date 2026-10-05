"""Aceite P-0: tipos de ``_types`` (I1) — ida e volta ``to_wire``/``from_wire`` e recusa de formatos inválidos."""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from rumbapy._types import (ConnectResult, FieldRead, OpenInfo, Profile, ReadResult, SendResult, SessionEntry,
                            SessionStatus, WindowInfo)
from rumbapy.errors import ProtocolError
from rumbapy.screen import OIA

import dataclasses
import json

import pytest

OIA_RAW = bytes([1]) + bytes([0x10] * 80) + bytes([0x10] + [0] * 21)

SAMPLES = [
    Profile("BRAZIL_D", "D", "cics", "BRAZIL D"),
    SessionEntry("D", "", "H", 1920),
    SessionStatus("D", "", "D", False, 24, 80, 0),
    ConnectResult(True, 0, already=False, rows=24, cols=80, host_cp=0, long_name="", type="D", eab=False,
                  hwnd=1234567, hwnd_pid=5678),
    ConnectResult(False, 1),
    ReadResult("VOLVO ", False, -1, 0),
    FieldRead("A000000 ", (12, 21), 8, 0xC1, 0),
    SendResult(1, OIA.from_raw(OIA_RAW)),
    OpenInfo(True, 2, 1234567, 5678, 9.4, 11.3, 24, 80, "launcher"),
    OpenInfo(False, 0, None, None, None, 0.2, 32, 80, "attach"),
    WindowInfo(1234567, 5678, "BRAZIL_D - Pro Client", "host access web launcher.exe", "launcher", True, False),
]


@pytest.mark.parametrize("value", SAMPLES, ids=lambda v: type(v).__name__)
def test_round_trip_through_json(value: Any) -> None:
    wire = value.to_wire()
    back = type(value).from_wire(json.loads(json.dumps(wire)))
    assert back == value


@pytest.mark.parametrize("value", SAMPLES, ids=lambda v: type(v).__name__)
def test_types_are_frozen(value: Any) -> None:
    first = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, first, None)


def test_wire_shapes() -> None:
    assert FieldRead("x", (12, 21), 1, 0xC0, 0).to_wire()["start"] == [12, 21]
    assert SendResult(2, OIA.from_raw(OIA_RAW)).to_wire() == {"chunks_sent": 2, "oia_after": OIA_RAW.hex()}
    extra = {**SessionEntry("D", "", "H", 1920).to_wire(), "novo_campo": 1}
    assert SessionEntry.from_wire(extra) == SessionEntry("D", "", "H", 1920)


def test_eab_from_the_raw_bit_is_stored_as_bool() -> None:
    """F.3 calcula ``eab = b[13] & 0x80`` (int 0/128); o tipo guarda ``bool`` e o wire continua só com ``bool``."""
    status = SessionStatus("D", "", "D", 0x80, 24, 80, 0)          # type: ignore[arg-type]
    assert status.eab is True and status.to_wire()["eab"] is True
    assert SessionStatus.from_wire(json.loads(json.dumps(status.to_wire()))) == status
    assert SessionStatus("D", "", "D", 0, 24, 80, 0).eab is False  # type: ignore[arg-type]
    connect = ConnectResult(True, 0, rows=24, cols=80, eab=0x80)    # type: ignore[arg-type]
    assert connect.eab is True and ConnectResult.from_wire(json.loads(json.dumps(connect.to_wire()))) == connect
    assert ConnectResult(False, 1).eab is None


def test_connect_result_not_connected_only_needs_rc() -> None:
    assert ConnectResult.from_wire({"connected": False, "rc": 1}) == ConnectResult(False, 1)
    with pytest.raises(ProtocolError):
        ConnectResult.from_wire({"connected": True, "rc": 0})          # conectado exige rows/cols


@pytest.mark.parametrize("cls, payload", [
    (SessionEntry, {"letter": "D", "long_name": "", "type": "H", "ps_size": True}),
    (SessionEntry, {"letter": "D", "long_name": "", "type": "H"}),
    (SessionStatus, {"letter": "D", "long_name": "", "type": "D", "eab": 0, "rows": 24, "cols": 80, "host_cp": 0}),
    (ReadResult, {"text": 1, "busy": False, "nul_at": -1, "nul_tail": 0}),
    (FieldRead, {"text": "", "start": [12], "length": 1, "attr": 0, "nul_tail": 0}),
    (SendResult, {"chunks_sent": 1, "oia_after": "00"}),
    (OpenInfo, {"launched": True, "closed_instances": 0, "hwnd": None, "hwnd_pid": None, "seconds_to_register": None,
                "seconds_to_connect": 1.0, "rows": 24, "cols": 80, "via": "teleporte"}),
    (WindowInfo, {"hwnd": 1, "pid": 2, "title": "x", "process": None, "kind": "edge", "visible": True,
                  "minimized": False}),
    (Profile, {"name": "X", "letter": "X", "kind": "tso", "link_text": "X"}),
    (ConnectResult, ["nao", "objeto"]),
])
def test_invalid_payloads(cls: type, payload: Any) -> None:
    with pytest.raises(ProtocolError):
        cls.from_wire(payload)


def test_send_result_from_wire_imports_screen_lazily() -> None:
    import ast
    import inspect

    import rumbapy._types as types_module

    tree = ast.parse(inspect.getsource(types_module))
    top_level = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all("screen" not in (getattr(node, "module", "") or "") for node in top_level)
