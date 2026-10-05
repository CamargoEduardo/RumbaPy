"""Contratos entre pacotes (C.5, D.9, E.2, F.2, A.4.7): o que um pacote expõe é exatamente o que os outros consomem.

As partes que dependem de pacotes ainda não entregues (``Dispatcher.HANDLERS`` do P-B, ``WorkerBackend`` do P-D,
``InProcessBackend`` do P-B, ``RealUser32`` do P-C) são puladas até o módulo existir e passam a valer sozinhas.
Nenhum teste daqui depende de um stub continuar stub: quando o pacote dono implementar, tudo continua verde sem
editar este arquivo (que é do P-0).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.fakes import fake_ehllapi, guard as fakes_guard
from rumbapy._backend import CLIENT_ONLY_METHODS, Backend
from rumbapy._worker import guard as worker_guard
from tests.fakes.fake_user32 import FakeUser32
from rumbapy._user32 import RawWindow, User32
from rumbapy import _types, _win32, screen
from rumbapy._worker import hllapi
from rumbapy.keys import AIDS, Key
from rumbapy._protocol import OPS

import dataclasses
import importlib
import inspect

import pytest

IMPLICIT_ARGS = {"connect": {"letter"}}          # o letter de connect vem de Backend.letter
WIRE_TYPE = {"int": int, "str": str, "bool": bool, "Sequence[str]": list}


def canon(fn: Callable[..., Any]) -> str:
    """Assinatura canônica: ``(a: int, *, b: bool = False) -> str`` (anotações como texto)."""
    sig = inspect.signature(fn)
    parts: list[str] = []
    star = False
    for p in sig.parameters.values():
        if p.name == "self":
            continue
        if p.kind is p.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        text = p.name
        if p.annotation is not p.empty:
            text += f": {p.annotation}"
        if p.default is not p.empty:
            text += f" = {p.default!r}"
        parts.append(text)
    ret = sig.return_annotation
    return f"({', '.join(parts)}) -> {'' if ret is sig.empty else ret}"


def protocol_methods(proto: type) -> dict[str, Callable[..., Any]]:
    return {name: fn for name, fn in vars(proto).items() if inspect.isfunction(fn) and not name.startswith("_")}


# ------------------------------------------------------------------------------------------- Backend x OPS (C.5)

BACKEND = {
    "ping": "() -> dict",
    "sessions": "() -> list[SessionEntry]",
    "connect": "() -> ConnectResult",
    "disconnect": "() -> None",
    "status": "() -> SessionStatus",
    "snapshot": "(*, fields: bool = False) -> Screen",
    "read": "(row: int, col: int, length: int) -> ReadResult",
    "read_field": "(row: int, col: int) -> FieldRead",
    "write": "(row: int, col: int, text: str, *, secret: bool) -> None",
    "send": "(chunks: Sequence[str], *, secret: bool) -> SendResult",
    "set_cursor": "(row: int, col: int) -> None",
    "evict": "(reason: str) -> dict",
    "shutdown": "(reason: str) -> None",
    "bind_instance": "(hwnd: int | None) -> None",
    "release": "() -> None",
}


def test_backend_methods_are_ops_plus_client_only() -> None:
    methods = protocol_methods(Backend)
    assert CLIENT_ONLY_METHODS == {"bind_instance", "release"}
    assert set(methods) - CLIENT_ONLY_METHODS == set(OPS)
    assert {name: canon(fn) for name, fn in methods.items()} == BACKEND
    assert "letter" in Backend.__annotations__


@pytest.mark.parametrize("op", list(OPS))
def test_backend_arguments_match_ops(op: str) -> None:
    params = {name: p for name, p in inspect.signature(getattr(Backend, op)).parameters.items() if name != "self"}
    assert set(params) | IMPLICIT_ARGS.get(op, set()) == set(OPS[op].args)
    for name, param in params.items():
        assert WIRE_TYPE[param.annotation] is OPS[op].args[name], (op, name)


def test_ops_arg_types_are_exact_wire_types() -> None:
    for op in OPS.values():
        for kind in op.args.values():
            assert kind in (int, str, bool, list), (op.name, kind)


def _implements_backend(cls: type) -> None:
    for name, fn in protocol_methods(Backend).items():
        impl = getattr(cls, name, None)
        assert callable(impl), f"{cls.__name__} não implementa {name}"
        expected = [(p.name, p.kind) for p in inspect.signature(fn).parameters.values() if p.name != "self"]
        got = [(p.name, p.kind) for p in inspect.signature(impl).parameters.values() if p.name != "self"]
        assert got == expected, (cls.__name__, name)


def test_worker_backend_implements_backend() -> None:
    module = importlib.import_module("rumbapy._backend")
    cls = getattr(module, "WorkerBackend", None)
    if cls is None:
        pytest.skip("WorkerBackend ainda não existe (pacote P-D)")
    _implements_backend(cls)


def test_in_process_backend_implements_backend() -> None:
    module = pytest.importorskip("tests.fakes.fake_backend", reason="InProcessBackend ainda não existe (pacote P-B)")
    _implements_backend(module.InProcessBackend)


def test_dispatcher_handlers_match_ops() -> None:
    dispatch = pytest.importorskip("rumbapy._worker.dispatch", reason="Dispatcher ainda não existe (pacote P-B)")
    handlers = set(dispatch.Dispatcher.HANDLERS)
    assert set(OPS) - {"ping"} <= handlers <= set(OPS)       # ping é respondido pela T-conn (D.5)


# ------------------------------------------------------------------------------------------- AIDS (A.4.7, C10)

def _code(key: Key) -> str | tuple[str, str]:
    value = key.value
    return (value[1], value[3]) if len(value) == 4 else value[1]


def test_aids_match_fake_and_guard() -> None:
    keys_codes = {_code(k) for k in AIDS}
    fake_codes = set(fake_ehllapi.AID_KEYS) | {("A", c) for c in fake_ehllapi.AID_A_KEYS}
    guard_codes = set(worker_guard.AID_BY_CODE) | {("A", c) for c in worker_guard.AID_BY_A_CODE}
    assert keys_codes == fake_codes == guard_codes
    for code, name in {**fake_ehllapi.AID_KEYS, **worker_guard.AID_BY_CODE}.items():
        assert Key[name].value == "@" + code and Key[name] in AIDS
    for code, name in {**fake_ehllapi.AID_A_KEYS, **worker_guard.AID_BY_A_CODE}.items():
        assert Key[name].value == "@A@" + code and Key[name] in AIDS


def test_local_keys_of_the_fake_are_not_aids() -> None:
    for code in fake_ehllapi.LOCAL_KEYS:
        assert not any(k.value == "@" + code for k in AIDS)


def test_every_key_is_understood_by_the_fake() -> None:
    """``tokenize_mnemonic``/``validate_request`` aceitam toda ``Key``; o simulador também (sem rc 2 enganoso)."""
    for key in Key:
        value = key.value
        if len(value) == 2:
            assert value[1] in fake_ehllapi.AID_KEYS or value[1] in fake_ehllapi.LOCAL_KEYS, key
        elif value[1] == "A":
            assert value[3] in fake_ehllapi.AID_A_KEYS or value[3] in fake_ehllapi.LOCAL_A_KEYS, key
        else:
            assert value[1] == "S", key                               # DUP/FIELD MARK: @S@x/@S@y sem efeito


# ------------------------------------------------------------------------------------------- guard movido

def test_guard_was_moved_and_is_reexported() -> None:
    names = [n for n in vars(worker_guard) if not n.startswith("__") and n not in ("annotations", "ctypes",
                                                                                  "dataclass", "field")]
    assert {"GuardedDll", "GuardPolicy", "GuardViolation", "classify_keys", "AID_BY_CODE"} <= set(names)
    for name in names:
        assert getattr(fakes_guard, name) is getattr(worker_guard, name), name
    assert worker_guard.__file__.replace("\\", "/").endswith("src/rumbapy/_worker/guard.py")


# ------------------------------------------------------------------------------------------- _win32 (D.9)

WIN32 = {
    "user_sid": "() -> str",
    "session_id": "() -> int",
    "process_alive": "(pid: int, created: int | None = None) -> bool",
    "process_created": "(pid: int) -> int | None",
    "process_image": "(pid: int) -> str | None",
    "parent_pid": "(pid: int) -> int | None",
    "hllapi_client_pids": "(dll_name: str = 'ehlapi32.dll') -> list[int]",
    "pipe_security_attributes": "() -> SecurityAttributes",
    "pipe_server_pid": "(handle: int) -> int",
    "pipe_client_pid": "(handle: int) -> int",
    "open_event": "(name: str, *, create: bool) -> int",
    "set_event": "(h: int) -> None",
    "reset_event": "(h: int) -> None",
    "event_is_set": "(h: int) -> bool",
    "wait_handle": "(h: int, timeout: float) -> bool",
    "window_thread_pid": "(hwnd: int) -> int",
    "is_window": "(hwnd: int) -> bool",
    "show_window_async": "(hwnd: int, cmd: int) -> None",
    "hllapi_error_dialog_visible": "(pid: int | None = None) -> bool",
    "tcp_listener_pid": "(port: int) -> int | None",
}


def test_win32_contract() -> None:
    for name, signature in WIN32.items():
        assert canon(getattr(_win32, name)) == signature, name
    assert canon(_win32.SecurityAttributes.address) == "() -> int"
    assert _win32.CREATE_BREAKAWAY_FROM_JOB == 0x01000000
    assert _win32.CREATE_NO_WINDOW == 0x08000000
    assert _win32.CREATE_NEW_PROCESS_GROUP == 0x00000200


def test_win32_show_window_async_refuses_in_sandbox() -> None:
    from rumbapy.errors import SandboxViolation

    with pytest.raises(SandboxViolation):
        _win32.show_window_async(0, 7)


# ------------------------------------------------------------------------------------------- Hllapi (F.1, F.2)

HLLAPI = {
    "connect": "(letter: str) -> int",
    "disconnect": "() -> int",
    "reset_system": "() -> int",
    "set_param": "(text: bytes) -> int",
    "query_sessions": "() -> tuple[int, list[SessionEntry]]",
    "query_status": "(letter: str) -> tuple[int, SessionStatus | None]",
    "copy_ps": "(size: int) -> tuple[int, bytes]",
    "copy_ps_to_string": "(pos: int, n: int) -> tuple[int, bytes]",
    "copy_string_to_ps": "(pos: int, data: bytes) -> int",
    "copy_field": "(pos: int, n: int) -> tuple[int, bytes]",
    "send_key": "(chunk: bytes) -> int",
    "cursor": "() -> tuple[int, int]",
    "set_cursor": "(pos: int) -> int",
    "oia": "() -> tuple[int, bytes]",
    "field_attr": "(pos: int) -> tuple[int, int]",
    "find_field_pos": "(pos: int, code: bytes) -> tuple[int, int]",
    "find_field_len": "(pos: int, code: bytes) -> tuple[int, int]",
    "hwnd": "() -> int | None",
}
BOUND = ["WD_ConnectPS", "WD_DisconnectPS", "WD_ResetSystem", "WD_SetSessionParamEx", "WD_SendKey", "WD_CopyPS",
         "WD_CopyPSToString", "WD_CopyStringToPS", "WD_CopyFieldToString", "WD_QueryCursorLocation", "WD_SetCursor",
         "WD_QueryFieldAttribute", "WD_FindFieldPosition", "WD_FindFieldLength", "WD_CopyOIA", "WD_QuerySessions",
         "WD_QuerySessionStatus", "WD_GetSessionHWND"]


def test_hllapi_contract() -> None:
    methods = {n: f for n, f in vars(hllapi.Hllapi).items() if inspect.isfunction(f) and not n.startswith("_")}
    assert {name: canon(fn) for name, fn in methods.items()} == HLLAPI
    init = inspect.signature(hllapi.Hllapi.__init__)
    assert [(p.name, p.kind) for p in init.parameters.values()] == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD), ("dll", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("track", inspect.Parameter.KEYWORD_ONLY)]
    load = inspect.signature(hllapi.load_dll)
    assert [(p.name, p.kind) for p in load.parameters.values()] == [
        ("path", inspect.Parameter.POSITIONAL_OR_KEYWORD), ("fake", inspect.Parameter.KEYWORD_ONLY),
        ("guard", inspect.Parameter.KEYWORD_ONLY)]
    assert hllapi.DLL_PATH == "C:\\Program Files (x86)\\Micro Focus\\RUMBA\\System\\ehlapi32.Dll"
    assert hllapi.H_APP.value == 1
    assert list(hllapi.BOUND_FUNCTIONS) == BOUND
    assert hllapi.BOUND_FUNCTIONS["WD_CopyPSToString"] == ("H", "W", "S", "W")
    assert all(set(v) <= {"H", "W", "PW", "S"} and v[0] == "H" for v in hllapi.BOUND_FUNCTIONS.values())


def test_guard_policies() -> None:
    policies = hllapi.POLICIES
    assert list(policies) == ["readonly", "text", "login"]
    assert all(isinstance(p, worker_guard.GuardPolicy) for p in policies.values())
    assert (policies["readonly"].allowed_aids, policies["readonly"].allow_text, policies["readonly"].allow_local_keys) \
        == (set(), False, False)
    assert (policies["text"].allowed_aids, policies["text"].allow_text, policies["text"].allow_local_keys) == \
        (set(), True, False)
    assert (policies["login"].allowed_aids, policies["login"].allow_text, policies["login"].allow_local_keys) == \
        ({"ENTER"}, True, False)


def test_load_dll_without_fake_never_loads_in_the_sandbox(tmp_path: Path) -> None:
    """No sandbox e sem ``fake``, ``load_dll`` recusa ANTES de tudo (F.1, saída 17) — implementado pelo P-B.

    NUNCA passe ``hllapi.DLL_PATH`` aqui: o caminho inexistente garante que nada seria carregado nem se a recusa
    do sandbox e a sentinela do ``conftest`` falhassem ao mesmo tempo.
    """
    from rumbapy.errors import SandboxViolation

    missing = tmp_path / "nao-existe" / "ehlapi32.Dll"
    for guard in ("none", "readonly", "bogus"):
        with pytest.raises(SandboxViolation):
            hllapi.load_dll(str(missing), fake=None, guard=guard)   # type: ignore[arg-type]


def test_new_policy_is_an_independent_copy() -> None:
    """``audit``/``allowed_aids`` mutáveis: cada carga da DLL recebe uma cópia nova de ``POLICIES[guard]``."""
    first, second = hllapi.new_policy("login"), hllapi.new_policy("login")
    assert first is not hllapi.POLICIES["login"] and first is not second
    assert first == hllapi.POLICIES["login"]
    first.audit.append(("WD_SendKey", "x"))
    first.allowed_aids.add("PF3")
    assert second.audit == [] and second.allowed_aids == {"ENTER"}
    assert hllapi.POLICIES["login"].audit == [] and hllapi.POLICIES["login"].allowed_aids == {"ENTER"}
    with pytest.raises(KeyError):
        hllapi.new_policy("none")


# ------------------------------------------------------------------------------------------- User32 (E.2)

USER32 = {
    "top_windows": "() -> list[RawWindow]",
    "process_image": "(pid: int) -> str | None",
    "parent_image": "(pid: int) -> str | None",
    "static_texts": "(hwnd: int) -> list[str]",
    "buttons": "(hwnd: int) -> list[tuple[int, str]]",
    "post_close": "(hwnd: int) -> None",
    "click": "(button_hwnd: int) -> None",
    "show": "(hwnd: int, cmd: int) -> None",
    "is_window": "(hwnd: int) -> bool",
    "root_of": "(hwnd: int) -> int",
}


def test_user32_contract() -> None:
    assert {name: canon(fn) for name, fn in protocol_methods(User32).items()} == USER32
    assert [f.name for f in dataclasses.fields(RawWindow)] == ["hwnd", "title", "cls", "pid", "owner", "visible",
                                                               "iconic"]


@pytest.mark.parametrize("module_name, class_name", [("tests.fakes.fake_user32", "FakeUser32"),
                                                     ("rumbapy._user32", "RealUser32")])
def test_user32_implementations(module_name: str, class_name: str) -> None:
    cls = getattr(importlib.import_module(module_name), class_name, None)
    if cls is None:
        pytest.skip(f"{class_name} ainda não existe (pacote P-C)")
    for name, fn in protocol_methods(User32).items():
        expected = [p.name for p in inspect.signature(fn).parameters.values()]
        assert [p.name for p in inspect.signature(getattr(cls, name)).parameters.values()] == expected, name
    assert cls is not FakeUser32 or isinstance(FakeUser32(), User32)


# ------------------------------------------------------------------------------------------- dados (B.5, C.4, E.5)

@pytest.mark.parametrize("cls, names", [
    (screen.Field, ["start", "length", "attribute", "text", "rows", "cols"]),
    (screen.OIA, ["raw"]),
    (screen.Screen, ["terminal", "rows", "cols", "text", "cursor", "oia", "host_busy", "hidden_unknown", "fields",
                     "taken_at", "match"]),
    (_types.Profile, ["name", "letter", "kind", "link_text"]),
    (_types.SessionEntry, ["letter", "long_name", "type", "ps_size"]),
    (_types.SessionStatus, ["letter", "long_name", "type", "eab", "rows", "cols", "host_cp"]),
    (_types.ConnectResult, ["connected", "rc", "already", "rows", "cols", "host_cp", "long_name", "type", "eab", "hwnd",
                            "hwnd_pid"]),
    (_types.ReadResult, ["text", "busy", "nul_at", "nul_tail"]),
    (_types.FieldRead, ["text", "start", "length", "attr", "nul_tail"]),
    (_types.SendResult, ["chunks_sent", "oia_after"]),
    (_types.OpenInfo, ["launched", "closed_instances", "hwnd", "hwnd_pid", "seconds_to_register", "seconds_to_connect",
                       "rows", "cols", "via"]),
    (_types.WindowInfo, ["hwnd", "pid", "title", "process", "kind", "visible", "minimized"]),
])
def test_dataclass_fields(cls: type, names: list[str]) -> None:
    assert [f.name for f in dataclasses.fields(cls)] == names


def test_field_geometry_is_hidden_and_keyword_only() -> None:
    """``Field.rows/cols`` (para ``end``) não mudam a construção posicional, a igualdade nem o ``repr`` (B.5)."""
    geometry = {f.name: f for f in dataclasses.fields(screen.Field) if f.name in ("rows", "cols")}
    assert all(f.kw_only and not f.repr and not f.compare for f in geometry.values())
    assert (geometry["rows"].default, geometry["cols"].default) == (None, 80)
    hidden_text = [f for f in dataclasses.fields(screen.Field) if f.name == "text"][0]
    assert not hidden_text.repr
    for cls in (_types.ReadResult, _types.FieldRead):
        assert not [f for f in dataclasses.fields(cls) if f.name == "text"][0].repr, cls.__name__


def test_result_keys_match_the_types() -> None:
    from rumbapy._protocol import RESULT_KEYS

    assert list(RESULT_KEYS["connect"]) == [f.name for f in dataclasses.fields(_types.ConnectResult)]
    assert list(RESULT_KEYS["read"]) == [f.name for f in dataclasses.fields(_types.ReadResult)]
    assert list(RESULT_KEYS["read_field"]) == [f.name for f in dataclasses.fields(_types.FieldRead)]
    assert list(RESULT_KEYS["send"]) == [f.name for f in dataclasses.fields(_types.SendResult)]
    assert list(RESULT_KEYS["status"]) == [f.name for f in dataclasses.fields(_types.SessionStatus)]
