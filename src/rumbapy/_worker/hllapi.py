"""Bindings ctypes da ``ehlapi32.Dll`` (F.1/F.2): ``load_dll`` e a fachada ``Hllapi``.

* a DLL é carregada **dentro de T-dll** a partir do caminho instalado pelo RUMBA (nunca copiar: ``.SHARDAT``);
* ``RUMBAPY_SANDBOX=1`` sem ``fake`` -> ``SandboxViolation`` (saída 17) ANTES de qualquer outra checagem (inclusive
  de existência do arquivo); ``fake="mod:fábrica"`` (só com sandbox) -> ``importlib.import_module(mod).<fábrica>()``;
  ``guard != "none"`` -> ``GuardedDll(dll, new_policy(guard))`` — sempre uma CÓPIA de ``POLICIES[guard]``;
* só as funções de ``BOUND_FUNCTIONS`` são vinculadas (teste estático), sempre por ``getattr(dll, "WD_x")``
  (compatível com ``FakeEhllapi`` e ``GuardedDll``), com ``restype`` W (``WD_GetSessionHWND`` -> ``c_void_p``);
* buffers sempre ``create_string_buffer(n + 16)`` lidos com ``.raw[:n]`` (nunca ``.value``).

``Hllapi`` só devolve rc/bytes; quem converte rc em exceção é ``rc.check`` (F.6). Cada chamada passa por
``track(func)`` (o ``Watchdog.inflight``).
"""
# ruff: noqa: I001
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Literal

from ..errors import ParameterError, SandboxViolation, SetupError
from .._types import SessionEntry, SessionStatus
from .guard import GuardedDll, GuardPolicy
from . import structs

import importlib
import ctypes
import copy
import os

DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
H_APP = ctypes.c_void_p(1)                 # 1º argumento de toda WD_*

GuardMode = Literal["none", "readonly", "text", "login"]
GUARD_MODES: tuple[str, ...] = ("none", "readonly", "text", "login")

POLICIES: Mapping[str, GuardPolicy] = MappingProxyType({
    "readonly": GuardPolicy(set(), allow_text=False, allow_local_keys=False),
    "text": GuardPolicy(set(), allow_text=True, allow_local_keys=False),
    "login": GuardPolicy({"ENTER"}, allow_text=True, allow_local_keys=False),
})


def new_policy(guard: str) -> GuardPolicy:
    """Cópia nova e independente de ``POLICIES[guard]`` (``KeyError`` se ``guard`` não tiver política, ex. ``"none"``).

    Os modelos de ``POLICIES`` nunca são entregues ao ``GuardedDll``: o ``audit`` de cada carga começa vazio (J.6).
    """
    return copy.deepcopy(POLICIES[guard])


# Lista FECHADA de funções vinculadas e seus argtypes (H = c_void_p, W = c_ushort, PW = POINTER(c_ushort),
# S = c_char_p). restype = W em todas, menos WD_GetSessionHWND (c_void_p).
BOUND_FUNCTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "WD_ConnectPS": ("H", "S"),
    "WD_DisconnectPS": ("H",),
    "WD_ResetSystem": ("H",),
    "WD_SetSessionParamEx": ("H", "S"),
    "WD_SendKey": ("H", "S"),
    "WD_CopyPS": ("H", "S", "W"),
    "WD_CopyPSToString": ("H", "W", "S", "W"),
    "WD_CopyStringToPS": ("H", "W", "S", "W"),
    "WD_CopyFieldToString": ("H", "W", "S", "W"),
    "WD_QueryCursorLocation": ("H", "PW"),
    "WD_SetCursor": ("H", "W"),
    "WD_QueryFieldAttribute": ("H", "PW", "W"),
    "WD_FindFieldPosition": ("H", "PW", "W", "S"),
    "WD_FindFieldLength": ("H", "PW", "W", "S"),
    "WD_CopyOIA": ("H", "S", "W"),
    "WD_QuerySessions": ("H", "PW", "S"),
    "WD_QuerySessionStatus": ("H", "S"),
    "WD_GetSessionHWND": ("H",),
})
CTYPES: Mapping[str, Any] = MappingProxyType({
    "H": ctypes.c_void_p, "W": ctypes.c_ushort, "PW": ctypes.POINTER(ctypes.c_ushort), "S": ctypes.c_char_p,
})
OIA_SIZE = 103
OIA_BUFFER = 110
STATUS_BUFFER = 32
SESSIONS_MAX = 26
EXTRA = 16                                 # folga de todo buffer: create_string_buffer(n + 16)
PS_EXTRA = 64                              # CopyPS: rows*cols + 64


def _sandboxed() -> bool:
    return os.environ.get("RUMBAPY_SANDBOX") == "1"


def load_dll(path: str, *, fake: str | None = None, guard: GuardMode = "none") -> object:
    """Carrega a DLL real (em T-dll) ou o dublê de ``fake``; embrulha em ``GuardedDll`` se ``guard != "none"``."""
    if _sandboxed() and fake is None:      # antes de tudo (nem confere se o arquivo existe)
        raise SandboxViolation.make(acao=f"carregar a DLL do RUMBA ({os.path.basename(str(path))})")
    if guard not in GUARD_MODES:
        raise ParameterError.make(detalhe=f"guard inválido: {guard!r} (use {', '.join(GUARD_MODES)})")
    if fake is not None:
        if not _sandboxed():
            raise SetupError("O dublê da DLL (--fake-dll) só é aceito com RUMBAPY_SANDBOX=1.")
        module_name, sep, factory = fake.partition(":")
        if not sep or not module_name or not factory:
            raise ParameterError.make(detalhe=f"--fake-dll deve ser 'modulo:fabrica' (recebi {fake!r})")
        _dll: object = getattr(importlib.import_module(module_name), factory)()
    else:
        _dll = ctypes.WinDLL(path, use_last_error=True)
    if guard != "none":
        _dll = GuardedDll(_dll, new_policy(guard))
    return _dll


def _null_track(func: str) -> AbstractContextManager[None]:
    return nullcontext()


class Hllapi:
    """Fachada fina sobre a DLL: só devolve rc/bytes (quem converte rc em exceção é ``rc.check``, F.6).

    ``track(func)`` é o ``watchdog.inflight``: um context manager em volta de cada chamada ``WD_*``.
    """

    def __init__(self, dll: object, *, track: Callable[[str], AbstractContextManager[None]] = _null_track) -> None:
        self.dll = dll
        self._track = track
        self._fn: dict[str, Any] = {}
        for name, kinds in BOUND_FUNCTIONS.items():
            fn = getattr(dll, name)
            fn.argtypes = [CTYPES[k] for k in kinds]
            fn.restype = ctypes.c_void_p if name == "WD_GetSessionHWND" else ctypes.c_ushort
            self._fn[name] = fn

    def _call(self, name: str, *args: Any) -> int:
        with self._track(name):
            result = self._fn[name](H_APP, *args)
        return int(result or 0)

    # ----------------------------------------------------------------- conexão
    def connect(self, letter: str) -> int:
        return self._call("WD_ConnectPS", ctypes.create_string_buffer(letter.encode("ascii"), 1 + EXTRA))

    def disconnect(self) -> int:
        return self._call("WD_DisconnectPS")

    def reset_system(self) -> int:
        return self._call("WD_ResetSystem")

    def set_param(self, text: bytes) -> int:
        return self._call("WD_SetSessionParamEx", ctypes.create_string_buffer(text, len(text) + EXTRA))

    def query_sessions(self) -> tuple[int, list[SessionEntry]]:
        count = ctypes.c_ushort(SESSIONS_MAX)
        buf = ctypes.create_string_buffer(SESSIONS_MAX * structs.SESSION_ENTRY_SIZE + EXTRA)
        rc = self._call("WD_QuerySessions", ctypes.byref(count), buf)
        if rc != 0:
            return rc, []
        return rc, structs.parse_sessions(buf.raw, count.value)

    def query_status(self, letter: str) -> tuple[int, SessionStatus | None]:
        buf = ctypes.create_string_buffer(STATUS_BUFFER)
        buf[0] = letter.encode("ascii")[:1]
        rc = self._call("WD_QuerySessionStatus", buf)
        if rc != 0:
            return rc, None
        return rc, structs.parse_status(buf.raw[:structs.SESSION_STATUS_SIZE])

    def hwnd(self) -> int | None:
        return self._call("WD_GetSessionHWND") or None

    # ----------------------------------------------------------------- tela
    def copy_ps(self, size: int) -> tuple[int, bytes]:
        buf = ctypes.create_string_buffer(size + PS_EXTRA)
        rc = self._call("WD_CopyPS", buf, size)
        return rc, buf.raw[:size]

    def copy_ps_to_string(self, pos: int, n: int) -> tuple[int, bytes]:
        buf = ctypes.create_string_buffer(n + EXTRA)
        rc = self._call("WD_CopyPSToString", pos, buf, n)
        return rc, buf.raw[:n]

    def copy_string_to_ps(self, pos: int, data: bytes) -> int:
        return self._call("WD_CopyStringToPS", pos, ctypes.create_string_buffer(data, len(data) + EXTRA), len(data))

    def copy_field(self, pos: int, n: int) -> tuple[int, bytes]:
        buf = ctypes.create_string_buffer(n + EXTRA)
        rc = self._call("WD_CopyFieldToString", pos, buf, n)
        return rc, buf.raw[:n]

    def send_key(self, chunk: bytes) -> int:
        return self._call("WD_SendKey", ctypes.create_string_buffer(chunk, len(chunk) + EXTRA))

    def cursor(self) -> tuple[int, int]:
        """(rc, pos)."""
        pos = ctypes.c_ushort(0)
        rc = self._call("WD_QueryCursorLocation", ctypes.byref(pos))
        return rc, int(pos.value)

    def set_cursor(self, pos: int) -> int:
        return self._call("WD_SetCursor", pos)

    def oia(self) -> tuple[int, bytes]:
        """(rc, 103 bytes)."""
        buf = ctypes.create_string_buffer(OIA_BUFFER)
        rc = self._call("WD_CopyOIA", buf, OIA_SIZE)
        return rc, buf.raw[:OIA_SIZE]

    # ----------------------------------------------------------------- campos
    def field_attr(self, pos: int) -> tuple[int, int]:
        attr = ctypes.c_ushort(0)
        rc = self._call("WD_QueryFieldAttribute", ctypes.byref(attr), pos)
        return rc, int(attr.value)

    def find_field_pos(self, pos: int, code: bytes) -> tuple[int, int]:
        """``code`` = ``b"T "`` | ``b"N "``."""
        out = ctypes.c_ushort(0)
        rc = self._call("WD_FindFieldPosition", ctypes.byref(out), pos, ctypes.create_string_buffer(code))
        return rc, int(out.value)

    def find_field_len(self, pos: int, code: bytes) -> tuple[int, int]:
        out = ctypes.c_ushort(0)
        rc = self._call("WD_FindFieldLength", ctypes.byref(out), pos, ctypes.create_string_buffer(code))
        return rc, int(out.value)
