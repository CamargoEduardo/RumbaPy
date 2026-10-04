"""Mundo simulado para os testes do launcher (P-C): relógio falso, ``FakeUser32``, ``FakeEhllapi`` e
``InProcessBackend`` (J.3). Nada aqui toca janela, processo ou DLL reais.

* ``Clock``: relógio injetável (``clock=`` / ``sleep=`` do ``Launcher``); ``sleep`` avança o tempo e dispara os eventos
  agendados com ``at``.
* ``World``: o "Windows" (``user32``), o "RUMBA" (``fake``) e os dois ``Backend`` (admin e dono) sobre o mesmo fake,
  mais um ``spawn`` falso que agenda o aparecimento da janela do Web Launcher e o registro da letra.
* ``timeline``: chamadas de ``user32`` e de ``Backend`` na ordem em que aconteceram (para "evict antes do WM_CLOSE").
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, _in_bytes, make_cics_login_host
from tests.fakes.fake_user32 import FakeUser32, FakeWindow
from tests.fakes.fake_backend import InProcessBackend
from rumbapy._launcher import Launcher
from rumbapy import profiles

import itertools
import heapq

LAUNCHER_EXE = r"C:\Fake\Host Access Web Launcher.exe"
LAUNCHER_IMAGE = "host access web launcher.exe"
LAUNCHER_CLASS = "WindowsForms10.Window.8.app.0.1"
REGISTER_AFTER = 9.4          # ao vivo: registro da letra ≈ 9,4 s depois do Popen
CONNECT_AFTER = 11.3          # ao vivo: ConnectPS rc 1 até ≈ 11,3 s


class Clock:
    """Relógio falso: ``sleep`` avança o tempo e roda os eventos vencidos (em ordem)."""

    def __init__(self, start: float = 1000.0, *, max_sleeps: int = 200_000) -> None:
        self.now = start
        self.sleeps = 0
        self.max_sleeps = max_sleeps
        self._events: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def __call__(self) -> float:
        return self.now

    def at(self, delay: float, action: Callable[[], None]) -> None:
        heapq.heappush(self._events, (self.now + delay, next(self._seq), action))

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, seconds
        self.sleeps += 1
        if self.sleeps > self.max_sleeps:
            raise AssertionError("laço sem fim no relógio falso")
        target = self.now + seconds
        while self._events and self._events[0][0] <= target:
            when, _seq, action = heapq.heappop(self._events)
            self.now = max(self.now, when)
            action()
        self.now = target


class TimelineUser32(FakeUser32):
    """``FakeUser32`` que também anota cada chamada numa linha do tempo compartilhada e avisa quem fecha."""

    def __init__(self, timeline: list[tuple], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.timeline = timeline
        self.on_destroy: list[Callable[[int], None]] = []

    def _record(self, name: str, *args: object) -> None:
        self.timeline.append(("user32", name, *args))
        super()._record(name, *args)

    def destroy(self, hwnd: int) -> None:
        win = self.windows.get(hwnd)
        was_alive = win is not None and win.alive
        super().destroy(hwnd)
        if was_alive:
            for callback in list(self.on_destroy):
                callback(hwnd)


class ClockedEhllapi(FakeEhllapi):
    """``FakeEhllapi`` cujo ``ConnectPS`` devolve rc 1 até ``connect_ready_at[letra]`` no relógio falso."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.connect_ready_at: dict[str, float] = {}
        super().__init__()

    def WD_ConnectPS(self, h: Any, short: Any) -> int:
        letter = _in_bytes(short)[:1].decode("latin-1").upper()
        if letter in self.sessions and self.clock() < self.connect_ready_at.get(letter, float("-inf")):
            self._log("WD_ConnectPS", short)
            return 1
        return super().WD_ConnectPS(h, short)


class RecordingBackend:
    """Repassa ao ``InProcessBackend`` e anota cada op na linha do tempo; ``fail[op]`` = exceções a levantar."""

    def __init__(self, inner: InProcessBackend, mode: str, timeline: list[tuple]) -> None:
        self.inner = inner
        self.mode = mode
        self.timeline = timeline
        self.fail: dict[str, list[BaseException]] = {}
        self.released = 0
        self.bound: list[int | None] = []

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self.timeline.append(("backend", self.mode, name))
            queue = self.fail.get(name)
            if queue:
                raise queue.pop(0)
            return target(*args, **kwargs)

        return call

    def bind_instance(self, hwnd: int | None) -> None:
        self.timeline.append(("backend", self.mode, "bind_instance"))
        self.bound.append(hwnd)
        self.inner.bind_instance(hwnd)

    def release(self) -> None:
        self.timeline.append(("backend", self.mode, "release"))
        self.released += 1


@dataclass
class FakeProc:
    pid: int
    code: int | None = None
    polls: int = 0

    def poll(self) -> int | None:
        self.polls += 1
        return self.code


@dataclass
class World:
    """Tudo o que o ``Launcher`` enxerga, em memória."""

    close_delay: float = 0.5
    clock: Clock = field(default_factory=Clock)
    timeline: list[tuple] = field(default_factory=list)
    spawned: list[list[str]] = field(default_factory=list)
    procs: list[FakeProc] = field(default_factory=list)
    scripts: list[tuple[list[str], float]] = field(default_factory=list)
    neutralized: list[str] = field(default_factory=list)
    backend_requests: list[tuple[str, str]] = field(default_factory=list)
    alive: dict[int, int] = field(default_factory=dict)        # pid -> created
    worker: dict[str, Any] | None = None                       # registro do worker vivo (find_worker)
    launcher_path: str | None = LAUNCHER_EXE
    script_output: tuple[int | None, bytes] = (0, b'{"ok":true,"step":"clicked"}')
    launch_plan: Callable[[World, FakeProc], None] | None = None
    on_script: Callable[[World], None] | None = None
    owner_error: BaseException | None = None                   # get_backend(L, "owner") levanta isto
    hllapi_pids: list[int] = field(default_factory=list)       # processos com a ehlapi32 carregada (Toolhelp32)

    def __post_init__(self) -> None:
        self.user32 = TimelineUser32(self.timeline, clock=self.clock, close_delay=self.close_delay)
        self.user32.on_destroy.append(self._unregister)
        self.fake = ClockedEhllapi(self.clock)
        self.admin = RecordingBackend(InProcessBackend(self.fake, "D"), "admin", self.timeline)
        self.owner = RecordingBackend(InProcessBackend(self.fake, "D"), "owner", self.timeline)
        self._pids = itertools.count(5000)
        self._hwnds = itertools.count(0x00B1_0000, 0x10)
        self.session_hwnd: dict[str, int] = {}
        if self.launch_plan is None:
            self.launch_plan = standard_plan()

    # ----------------------------------------------------------------- dependências do Launcher
    def get_backend(self, letter: str, mode: str) -> RecordingBackend:
        self.backend_requests.append((letter, mode))
        self.timeline.append(("get_backend", letter, mode))
        if mode == "owner" and self.owner_error is not None:
            raise self.owner_error
        return self.admin if mode == "admin" else self.owner

    def spawn(self, argv: list[str]) -> FakeProc:
        self.spawned.append(list(argv))
        proc = FakeProc(next(self._pids))
        self.alive[proc.pid] = 1000 + proc.pid
        self.procs.append(proc)
        self.timeline.append(("spawn", proc.pid))
        assert self.launch_plan is not None
        self.launch_plan(self, proc)
        return proc

    def run_script(self, argv: list[str], timeout: float) -> tuple[int | None, bytes]:
        self.scripts.append((list(argv), timeout))
        if self.on_script is not None:
            self.on_script(self)
        return self.script_output

    def launcher(self, **overrides: Any) -> Launcher:
        kwargs: dict[str, Any] = dict(
            clock=self.clock, sleep=self.clock.sleep, find_worker=lambda letter: self.worker,
            find_launcher=lambda: self.launcher_path, run_script=self.run_script, neutralize=self.neutralized.append,
            legacy_busy_extra=lambda letter: "", process_alive=self._process_alive,
            process_created=lambda pid: self.alive.get(pid), hllapi_client_pids=lambda: list(self.hllapi_pids))
        kwargs.update(overrides)
        return Launcher(self.user32, self.get_backend, self.spawn, **kwargs)

    def _process_alive(self, pid: int, created: int | None) -> bool:
        return pid in self.alive and (created is None or self.alive[pid] == created)

    # ----------------------------------------------------------------- montagem do cenário
    def new_pid(self) -> int:
        return next(self._pids)

    def add_launcher_window(self, title: str = "BRAZIL_D - Pro Client", *, pid: int | None = None,
                            register: str | None = "D", visible: bool = True, cls: str = LAUNCHER_CLASS,
                            image: str = LAUNCHER_IMAGE, closable: bool = True,
                            close_delay: float | None = None, screen: FakeScreen | None = None) -> FakeWindow:
        """Janela de nível superior do Web Launcher; ``register`` = letra registrada no HLLAPI por ela."""
        pid = pid if pid is not None else self.new_pid()
        if pid not in self.user32.processes:
            self.user32.add_process(pid, image)
        win = self.user32.add_window(title, cls=cls, pid=pid, visible=visible, closable=closable,
                                     close_delay=close_delay, hwnd=next(self._hwnds))
        if register:
            self.register(register, win.hwnd, pid, screen=screen)
        return win

    def register(self, letter: str, hwnd: int, pid: int, *, screen: FakeScreen | None = None) -> None:
        session = self.fake.add_session(letter, hwnd=hwnd, pid=pid)
        env, host = make_cics_login_host()
        session.load(screen or env)
        session.on_aid = host
        self.session_hwnd[letter] = hwnd

    def _unregister(self, hwnd: int) -> None:
        for letter, owner_hwnd in list(self.session_hwnd.items()):
            if owner_hwnd == hwnd:
                del self.session_hwnd[letter]
                self.fake.remove_session(letter)

    def registered(self) -> list[str]:
        return sorted(self.fake.sessions)

    # ----------------------------------------------------------------- consultas
    def user32_calls(self, name: str) -> list[tuple]:
        return self.user32.calls_to(name)

    def index(self, *event: object) -> int:
        """Posição da 1ª ocorrência de ``event`` (prefixo) na linha do tempo."""
        for i, item in enumerate(self.timeline):
            if item[:len(event)] == event:
                return i
        raise AssertionError(f"{event!r} não aconteceu")


def standard_plan(*, register_after: float = REGISTER_AFTER, connect_after: float = CONNECT_AFTER,
                  title: str = "BRAZIL_D - Pro Client", letter: str = "D",
                  screen: FakeScreen | None = None) -> Callable[[World, FakeProc], None]:
    """Web Launcher como ao vivo: janela + registro da letra em ``register_after`` s; ConnectPS rc 0 em
    ``connect_after`` s (contados a partir do Popen)."""

    def plan(world: World, proc: FakeProc) -> None:
        world.fake.connect_ready_at[letter] = world.clock() + connect_after
        world.clock.at(register_after, lambda: world.add_launcher_window(title, pid=proc.pid, register=letter,
                                                                         screen=screen))

    return plan


def silent_plan() -> Callable[[World, FakeProc], None]:
    """O launcher nunca registra a letra (nem abre janela)."""

    def plan(world: World, proc: FakeProc) -> None:
        return None

    return plan


def profile_d() -> Any:
    return profiles.resolve("D")
