"""Bindings ctypes da ``ehlapi32.Dll`` (F.1/F.2) — contrato congelado no P-0; a implementação é do pacote P-B.

Regras para a implementação:

* a DLL é carregada **dentro de T-dll** a partir do caminho instalado pelo RUMBA (nunca copiar: ``.SHARDAT``);
* ``RUMBAPY_SANDBOX=1`` sem ``fake`` -> ``SandboxViolation`` (saída 17) ANTES de qualquer outra checagem (inclusive
  de existência do arquivo); ``fake="mod:fábrica"`` (só com sandbox) -> ``importlib.import_module(mod).<fábrica>()``;
  ``guard != "none"`` -> ``GuardedDll(dll, new_policy(guard))`` — sempre uma CÓPIA de ``POLICIES[guard]`` (os
  ``GuardPolicy`` são mutáveis: ``audit`` e ``allowed_aids`` não podem vazar entre cargas nem entre testes);
* só as funções de ``BOUND_FUNCTIONS`` são vinculadas (teste estático), sempre por ``getattr(dll, "WD_x")``
  (compatível com ``FakeEhllapi`` e ``GuardedDll``), com ``restype`` W (``WD_GetSessionHWND`` -> ``c_void_p``);
* buffers sempre ``create_string_buffer(n + 16)`` lidos com ``.raw[:n]`` (nunca ``.value``).
"""
# ruff: noqa: I001
from __future__ import annotations

from contextlib import AbstractContextManager
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Literal

from .._types import SessionEntry, SessionStatus
from .guard import GuardPolicy

import ctypes
import copy

DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
H_APP = ctypes.c_void_p(1)                 # 1º argumento de toda WD_*

GuardMode = Literal["none", "readonly", "text", "login"]

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

_P_B = "pendente: implementação do pacote P-B (worker + IPC)"


def load_dll(path: str, *, fake: str | None, guard: GuardMode) -> object:
    """Carrega a DLL real (em T-dll) ou o dublê de ``fake``; embrulha em ``GuardedDll`` se ``guard != "none"``."""
    raise NotImplementedError(f"load_dll: {_P_B}")


class Hllapi:
    """Fachada fina sobre a DLL: só devolve rc/bytes (quem converte rc em exceção é ``rc.check``, F.6).

    ``track(func)`` é o ``watchdog.inflight``: um context manager em volta de cada chamada ``WD_*``.
    """

    def __init__(self, dll: object, *, track: Callable[[str], AbstractContextManager[None]]) -> None:
        raise NotImplementedError(f"Hllapi: {_P_B}")

    def connect(self, letter: str) -> int:
        raise NotImplementedError(f"Hllapi.connect: {_P_B}")

    def disconnect(self) -> int:
        raise NotImplementedError(f"Hllapi.disconnect: {_P_B}")

    def reset_system(self) -> int:
        raise NotImplementedError(f"Hllapi.reset_system: {_P_B}")

    def set_param(self, text: bytes) -> int:
        raise NotImplementedError(f"Hllapi.set_param: {_P_B}")

    def query_sessions(self) -> tuple[int, list[SessionEntry]]:
        raise NotImplementedError(f"Hllapi.query_sessions: {_P_B}")

    def query_status(self, letter: str) -> tuple[int, SessionStatus | None]:
        raise NotImplementedError(f"Hllapi.query_status: {_P_B}")

    def copy_ps(self, size: int) -> tuple[int, bytes]:
        raise NotImplementedError(f"Hllapi.copy_ps: {_P_B}")

    def copy_ps_to_string(self, pos: int, n: int) -> tuple[int, bytes]:
        raise NotImplementedError(f"Hllapi.copy_ps_to_string: {_P_B}")

    def copy_string_to_ps(self, pos: int, data: bytes) -> int:
        raise NotImplementedError(f"Hllapi.copy_string_to_ps: {_P_B}")

    def copy_field(self, pos: int, n: int) -> tuple[int, bytes]:
        raise NotImplementedError(f"Hllapi.copy_field: {_P_B}")

    def send_key(self, chunk: bytes) -> int:
        raise NotImplementedError(f"Hllapi.send_key: {_P_B}")

    def cursor(self) -> tuple[int, int]:
        """(rc, pos)."""
        raise NotImplementedError(f"Hllapi.cursor: {_P_B}")

    def set_cursor(self, pos: int) -> int:
        raise NotImplementedError(f"Hllapi.set_cursor: {_P_B}")

    def oia(self) -> tuple[int, bytes]:
        """(rc, 103 bytes)."""
        raise NotImplementedError(f"Hllapi.oia: {_P_B}")

    def field_attr(self, pos: int) -> tuple[int, int]:
        raise NotImplementedError(f"Hllapi.field_attr: {_P_B}")

    def find_field_pos(self, pos: int, code: bytes) -> tuple[int, int]:
        """``code`` = ``b"T "`` | ``b"N "``."""
        raise NotImplementedError(f"Hllapi.find_field_pos: {_P_B}")

    def find_field_len(self, pos: int, code: bytes) -> tuple[int, int]:
        raise NotImplementedError(f"Hllapi.find_field_len: {_P_B}")

    def hwnd(self) -> int | None:
        raise NotImplementedError(f"Hllapi.hwnd: {_P_B}")
