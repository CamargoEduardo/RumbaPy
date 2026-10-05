"""Acesso às janelas do Windows pelo cliente 64-bit (E.2).

P-0 congela o contrato (``RawWindow`` e o ``Protocol`` ``User32``); ``RealUser32`` (ctypes, respeitando
``RUMBAPY_SANDBOX=1``: ``post_close``, ``click`` e ``show`` levantam ``SandboxViolation`` ANTES de chamar a API) é do
pacote P-C. Os textos de janelas são lidos com ``InternalGetWindowText`` (não manda ``WM_GETTEXT``: nunca trava
numa janela que não processa mensagens).
Nenhuma ação ativa a janela nem a traz para a frente (nunca ``SetForegroundWindow``).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from dataclasses import dataclass

import threading
import logging
import os

logger = logging.getLogger(__name__)

SW_SHOWNOACTIVATE = 4        # Session.show()
SW_SHOWMINNOACTIVE = 7       # Session.minimize(), window="minimized" e janela oculta antes do close (E.4)


@dataclass(frozen=True)
class RawWindow:
    """Janela de nível superior como o ``EnumWindows`` a vê (visíveis e invisíveis)."""

    hwnd: int
    title: str
    cls: str
    pid: int
    owner: int               # GetWindow(GW_OWNER); 0 = sem dono
    visible: bool
    iconic: bool


@runtime_checkable
class User32(Protocol):
    def top_windows(self) -> list[RawWindow]: ...          # EnumWindows + GetWindow(GW_OWNER)

    def process_image(self, pid: int) -> str | None: ...   # basename minúsculo (QueryFullProcessImageNameW)

    def parent_image(self, pid: int) -> str | None: ...

    def static_texts(self, hwnd: int) -> list[str]: ...    # EnumChildWindows + GetWindowTextW

    def buttons(self, hwnd: int) -> list[tuple[int, str]]: ...

    def post_close(self, hwnd: int) -> None: ...           # PostMessageW(WM_CLOSE)

    def click(self, button_hwnd: int) -> None: ...         # PostMessageW(BM_CLICK)

    def show(self, hwnd: int, cmd: int) -> None: ...       # ShowWindowAsync (nunca SetForegroundWindow)

    def is_window(self, hwnd: int) -> bool: ...

    def root_of(self, hwnd: int) -> int: ...               # GetAncestor(GA_ROOT)


# ------------------------------------------------------------------------------------------- RealUser32 (P-C)

WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
GW_OWNER = 4
GA_ROOT = 2
BUTTON_CLASS = "Button"
_TEXT_MAX = 512


def _sandboxed() -> bool:
    return os.environ.get("RUMBAPY_SANDBOX") == "1"


def _refuse_in_sandbox(action: str) -> None:
    """Com ``RUMBAPY_SANDBOX=1`` toda ação com efeito em janela é recusada ANTES de chegar à API (E3, J.2)."""
    if _sandboxed():
        from .errors import SandboxViolation

        raise SandboxViolation.make(acao=action)


class _Api:
    """Funções ctypes com ``argtypes``/``restype`` explícitos (carregadas na 1ª instância de ``RealUser32``)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        _user32_dll = ctypes.WinDLL("user32", use_last_error=True)
        hwnd, lparam, wparam, uint, boolean = (wintypes.HWND, wintypes.LPARAM, wintypes.WPARAM, wintypes.UINT,
                                               wintypes.BOOL)
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(boolean, hwnd, lparam)

        def bind(name: str, restype: object, *argtypes: object) -> Any:
            fn = getattr(_user32_dll, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.EnumWindows = bind("EnumWindows", boolean, self.WNDENUMPROC, lparam)
        self.EnumChildWindows = bind("EnumChildWindows", boolean, hwnd, self.WNDENUMPROC, lparam)
        # InternalGetWindowText lê o texto guardado pelo sistema SEM mandar WM_GETTEXT (GetWindowTextW manda a
        # mensagem às janelas do próprio processo e não lê controles de outros processos)
        self.InternalGetWindowText = bind("InternalGetWindowText", ctypes.c_int, hwnd, wintypes.LPWSTR, ctypes.c_int)
        self.GetClassNameW = bind("GetClassNameW", ctypes.c_int, hwnd, wintypes.LPWSTR, ctypes.c_int)
        self.GetWindowThreadProcessId = bind("GetWindowThreadProcessId", wintypes.DWORD, hwnd,
                                             ctypes.POINTER(wintypes.DWORD))
        self.GetWindow = bind("GetWindow", hwnd, hwnd, uint)
        self.GetAncestor = bind("GetAncestor", hwnd, hwnd, uint)
        self.IsWindow = bind("IsWindow", boolean, hwnd)
        self.IsWindowVisible = bind("IsWindowVisible", boolean, hwnd)
        self.IsIconic = bind("IsIconic", boolean, hwnd)
        self.PostMessageW = bind("PostMessageW", boolean, hwnd, uint, wparam, lparam)
        self.ShowWindowAsync = bind("ShowWindowAsync", boolean, hwnd, ctypes.c_int)

    def text(self, fn: Any, hwnd: int) -> str:
        buf = self.ctypes.create_unicode_buffer(_TEXT_MAX)
        fn(hwnd, buf, _TEXT_MAX)
        return buf.value

    def pid_of(self, hwnd: int) -> int:
        pid = self.wintypes.DWORD(0)
        self.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value)

    def enum(self, parent: int | None) -> list[int]:
        found: list[int] = []

        def collect(hwnd: object, _lparam: object) -> bool:
            if hwnd:
                found.append(int(hwnd))          # type: ignore[call-overload]
            return True

        callback = self.WNDENUMPROC(collect)
        with _ENUM_LOCK:
            if parent is None:
                self.EnumWindows(callback, 0)
            else:
                self.EnumChildWindows(parent, callback, 0)
        return found


_ENUM_LOCK = threading.Lock()
_API: _Api | None = None
_API_LOCK = threading.Lock()


def _api() -> _Api:
    global _API
    with _API_LOCK:
        if _API is None:
            _API = _Api()
        return _API


class RealUser32:
    """``User32`` de verdade (ctypes; funciona em 64 e 32 bits). Leituras são livres; ``post_close``, ``click`` e
    ``show`` recusam no sandbox. Nunca ativa janela, nunca ``SetForegroundWindow``, nunca mata processo."""

    def __init__(self) -> None:
        self._api = _api()

    # ----------------------------------------------------------------- leituras
    def top_windows(self) -> list[RawWindow]:
        api = self._api
        out: list[RawWindow] = []
        for hwnd in api.enum(None):
            out.append(RawWindow(
                hwnd=hwnd,
                title=api.text(api.InternalGetWindowText, hwnd),
                cls=api.text(api.GetClassNameW, hwnd),
                pid=api.pid_of(hwnd),
                owner=int(api.GetWindow(hwnd, GW_OWNER) or 0),
                visible=bool(api.IsWindowVisible(hwnd)),
                iconic=bool(api.IsIconic(hwnd)),
            ))
        return out

    def process_image(self, pid: int) -> str | None:
        from . import _win32

        try:
            return _win32.process_image(pid)
        except OSError:
            return None

    def parent_image(self, pid: int) -> str | None:
        from . import _win32

        try:
            parent = _win32.parent_pid(pid)
        except OSError:
            return None
        if not parent:
            return None
        return self.process_image(parent)

    def _children(self, hwnd: int) -> list[tuple[int, str, str]]:
        api = self._api
        return [(child, api.text(api.GetClassNameW, child), api.text(api.InternalGetWindowText, child))
                for child in api.enum(hwnd)]

    def static_texts(self, hwnd: int) -> list[str]:
        return [text for _child, cls, text in self._children(hwnd) if cls != BUTTON_CLASS and text]

    def buttons(self, hwnd: int) -> list[tuple[int, str]]:
        return [(child, text) for child, cls, text in self._children(hwnd) if cls == BUTTON_CLASS]

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self._api.IsWindow(hwnd))

    def root_of(self, hwnd: int) -> int:
        if not hwnd:
            return 0
        return int(self._api.GetAncestor(hwnd, GA_ROOT) or 0)

    # ----------------------------------------------------------------- ações (recusadas no sandbox)
    def post_close(self, hwnd: int) -> None:
        _refuse_in_sandbox(f"PostMessageW(hwnd={hwnd}, WM_CLOSE)")
        if not self._api.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            logger.warning("PostMessageW(WM_CLOSE) falhou para hwnd=%d (erro %d)", hwnd,
                           self._api.ctypes.get_last_error())

    def click(self, button_hwnd: int) -> None:
        _refuse_in_sandbox(f"PostMessageW(hwnd={button_hwnd}, BM_CLICK)")
        if not self._api.PostMessageW(button_hwnd, BM_CLICK, 0, 0):
            logger.warning("PostMessageW(BM_CLICK) falhou para hwnd=%d (erro %d)", button_hwnd,
                           self._api.ctypes.get_last_error())

    def show(self, hwnd: int, cmd: int) -> None:
        _refuse_in_sandbox(f"ShowWindowAsync(hwnd={hwnd}, cmd={cmd})")
        if cmd not in (SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE):
            raise ValueError(f"comando de janela não permitido: {cmd} (só 4 e 7 não ativam a janela)")
        self._api.ShowWindowAsync(hwnd, cmd)
