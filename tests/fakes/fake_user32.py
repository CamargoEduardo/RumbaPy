"""Dublê de ``rumbapy._user32.User32`` para testes do launcher/close_all sem tocar em janelas reais (E.2, J.3).

Modela janelas de nível superior (título, classe, PID, dono, visível, minimizada), janelas filhas (textos e botões),
processos (imagem e pai) e diálogos. ``post_close`` fecha a janela depois de ``close_delay`` segundos (no relógio
injetado), salvo se houver um diálogo de dono bloqueando; fechar uma janela fecha os diálogos dela. ``click`` num botão
fecha o diálogo do botão. Toda chamada fica em ``calls``. Não existe método de matar processo (de propósito).

O relógio é preguiçoso: fechamentos pendentes e eventos agendados (``schedule``) acontecem na próxima chamada.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from rumbapy._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE, RawWindow

import itertools
import time

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWNA = 8
SW_RESTORE = 9

DIALOG_CLASS = "#32770"
BUTTON_CLASS = "Button"
STATIC_CLASS = "Static"


@dataclass
class FakeWindow:
    hwnd: int
    title: str
    cls: str
    pid: int
    owner: int = 0
    visible: bool = True
    iconic: bool = False
    parent: int = 0                     # != 0 => janela filha (não aparece em top_windows)
    blocks_close: bool = False          # diálogo modal que impede o WM_CLOSE do dono
    close_delay: float | None = None    # None = usa o padrão do FakeUser32
    closable: bool = True               # False = ignora WM_CLOSE (janela "teimosa")
    alive: bool = True
    closing_at: float | None = None
    texts: list[str] = field(default_factory=list)

    def raw(self) -> RawWindow:
        return RawWindow(self.hwnd, self.title, self.cls, self.pid, self.owner, self.visible, self.iconic)


@dataclass
class FakeProcess:
    pid: int
    image: str
    parent_pid: int | None = None


class FakeUser32:
    """Implementa o ``Protocol`` ``User32`` sobre um mundo de janelas em memória."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, close_delay: float = 0.0) -> None:
        self.clock = clock
        self.close_delay = close_delay
        self.windows: dict[int, FakeWindow] = {}
        self.processes: dict[int, FakeProcess] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.closed: list[int] = []                      # ordem em que as janelas foram destruídas
        self.clicked: list[tuple[int, str]] = []         # (hwnd do diálogo, texto do botão)
        self._hwnds = itertools.count(0x00A1_0000, 0x10)
        self._scheduled: list[tuple[float, int, Callable[[FakeUser32], None]]] = []
        self._seq = itertools.count()

    # ------------------------------------------------------------------ montagem do cenário
    def add_process(self, pid: int, image: str, *, parent_pid: int | None = None) -> FakeProcess:
        proc = FakeProcess(pid, image.lower(), parent_pid)
        self.processes[pid] = proc
        return proc

    def add_window(self, title: str, *, cls: str = "WindowsForms10.Window.8.app.0.1", pid: int, owner: int = 0,
                   visible: bool = True, iconic: bool = False, texts: Iterable[str] = (),
                   buttons: Iterable[str] = (), blocks_close: bool = False, close_delay: float | None = None,
                   closable: bool = True, hwnd: int | None = None) -> FakeWindow:
        win = FakeWindow(hwnd or next(self._hwnds), title, cls, pid, owner=owner, visible=visible, iconic=iconic,
                         blocks_close=blocks_close, close_delay=close_delay, closable=closable)
        self.windows[win.hwnd] = win
        for text in texts:
            self.add_child(win.hwnd, STATIC_CLASS, text)
        for text in buttons:
            self.add_child(win.hwnd, BUTTON_CLASS, text)
        return win

    def add_dialog(self, title: str, *, owner: int = 0, pid: int | None = None, texts: Iterable[str] = (),
                   buttons: Iterable[str] = ("OK",), blocks_close: bool = True, visible: bool = True) -> FakeWindow:
        """Diálogo ``#32770``; sem ``pid``, herda o PID do dono."""
        if pid is None:
            if not owner or owner not in self.windows:
                raise ValueError("informe pid ou um dono existente")
            pid = self.windows[owner].pid
        return self.add_window(title, cls=DIALOG_CLASS, pid=pid, owner=owner, texts=texts, buttons=buttons,
                               blocks_close=blocks_close, visible=visible)

    def add_child(self, parent: int, cls: str, text: str) -> FakeWindow:
        owner_pid = self.windows[parent].pid
        child = FakeWindow(next(self._hwnds), text, cls, owner_pid, parent=parent)
        self.windows[child.hwnd] = child
        return child

    def schedule(self, delay: float, action: Callable[[FakeUser32], None]) -> None:
        """Executa ``action(self)`` quando o relógio passar de ``agora + delay`` (na próxima chamada)."""
        self._scheduled.append((self.clock() + delay, next(self._seq), action))
        self._scheduled.sort(key=lambda item: (item[0], item[1]))

    def destroy(self, hwnd: int) -> None:
        """Some com a janela (e com as filhas e os diálogos de que ela é dona), como o fechamento real."""
        win = self.windows.get(hwnd)
        if win is None or not win.alive:
            return
        win.alive = False
        self.closed.append(hwnd)
        for other in list(self.windows.values()):
            if other.alive and (other.parent == hwnd or other.owner == hwnd):
                self.destroy(other.hwnd)

    # ------------------------------------------------------------------ relógio preguiçoso
    def _tick(self) -> None:
        now = self.clock()
        while self._scheduled and self._scheduled[0][0] <= now:
            _, _, action = self._scheduled.pop(0)
            action(self)
        for win in list(self.windows.values()):
            if win.alive and win.closing_at is not None and win.closing_at <= now:
                self.destroy(win.hwnd)

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))
        self._tick()

    def _alive(self, hwnd: int) -> FakeWindow | None:
        win = self.windows.get(hwnd)
        return win if win is not None and win.alive else None

    def _blocking_dialog(self, hwnd: int) -> FakeWindow | None:
        for other in self.windows.values():
            if other.alive and other.owner == hwnd and other.parent == 0 and other.blocks_close:
                return other
        return None

    # ------------------------------------------------------------------ Protocol User32
    def top_windows(self) -> list[RawWindow]:
        self._record("top_windows")
        return [w.raw() for w in self.windows.values() if w.alive and w.parent == 0]

    def process_image(self, pid: int) -> str | None:
        self._record("process_image", pid)
        proc = self.processes.get(pid)
        return proc.image if proc else None

    def parent_image(self, pid: int) -> str | None:
        self._record("parent_image", pid)
        proc = self.processes.get(pid)
        if proc is None or proc.parent_pid is None:
            return None
        parent = self.processes.get(proc.parent_pid)
        return parent.image if parent else None

    def static_texts(self, hwnd: int) -> list[str]:
        self._record("static_texts", hwnd)
        return [w.title for w in self.windows.values() if w.alive and w.parent == hwnd and w.cls != BUTTON_CLASS]

    def buttons(self, hwnd: int) -> list[tuple[int, str]]:
        self._record("buttons", hwnd)
        return [(w.hwnd, w.title) for w in self.windows.values() if w.alive and w.parent == hwnd and w.cls == BUTTON_CLASS]

    def post_close(self, hwnd: int) -> None:
        self._record("post_close", hwnd)
        win = self._alive(hwnd)
        if win is None or not win.closable or win.parent != 0:
            return
        if self._blocking_dialog(hwnd) is not None:
            return                                   # o diálogo modal "segura" a janela; é preciso tratá-lo antes
        delay = self.close_delay if win.close_delay is None else win.close_delay
        if win.closing_at is None:
            win.closing_at = self.clock() + delay
        self._tick()

    def click(self, button_hwnd: int) -> None:
        self._record("click", button_hwnd)
        button = self._alive(button_hwnd)
        if button is None or button.cls != BUTTON_CLASS:
            return
        dialog = self._alive(button.parent)
        if dialog is None:
            return
        self.clicked.append((dialog.hwnd, button.title))
        self.destroy(dialog.hwnd)

    def show(self, hwnd: int, cmd: int) -> None:
        self._record("show", hwnd, cmd)
        win = self._alive(hwnd)
        if win is None:
            return
        if cmd == SW_HIDE:
            win.visible = False
        elif cmd in (SW_SHOWMINNOACTIVE, SW_MINIMIZE, SW_SHOWMINIMIZED):
            win.visible, win.iconic = True, True
        elif cmd in (SW_SHOWNOACTIVATE, SW_SHOWNORMAL, SW_RESTORE, SW_SHOW, SW_SHOWNA, SW_MAXIMIZE):
            win.visible = True
            if cmd != SW_SHOW and cmd != SW_SHOWNA:
                win.iconic = False

    def is_window(self, hwnd: int) -> bool:
        self._record("is_window", hwnd)
        return self._alive(hwnd) is not None

    def root_of(self, hwnd: int) -> int:
        self._record("root_of", hwnd)
        win = self._alive(hwnd)
        if win is None:
            return 0
        while win.parent:
            parent = self._alive(win.parent)
            if parent is None:
                return 0
            win = parent
        return win.hwnd

    # ------------------------------------------------------------------ consultas para os testes
    def calls_to(self, name: str) -> list[tuple]:
        return [args for called, args in self.calls if called == name]

    def alive_titles(self) -> list[str]:
        return [w.title for w in self.windows.values() if w.alive and w.parent == 0]
