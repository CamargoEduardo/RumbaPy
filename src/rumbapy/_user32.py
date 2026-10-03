"""Acesso às janelas do Windows pelo cliente 64-bit (E.2).

P-0 congela o contrato (``RawWindow`` e o ``Protocol`` ``User32``); ``RealUser32`` (ctypes, respeitando
``RUMBAPY_SANDBOX=1``: ``post_close``, ``click`` e ``show`` levantam ``SandboxViolation``) é do pacote P-C.
Nenhuma ação ativa a janela nem a traz para a frente (nunca ``SetForegroundWindow``).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Protocol, runtime_checkable
from dataclasses import dataclass

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
