"""T-watch (D.4): tick de 100 ms que vigia a chamada em voo à DLL, a janela da sessão, o evento de parada e o idle.

``Hllapi`` envolve cada chamada com ``inflight(func)`` (limites: ``WD_ConnectPS`` 15 s; ``WD_QuerySessions`` 8 s;
``WD_DisconnectPS``/``WD_ResetSystem`` no encerramento 5 s; demais 20 s). A cada tick:

a) suspensão (R21): intervalo desde o tick anterior > 5 s -> rearma ``inflight.t0`` e a base do idle;
b) estouro do ``inflight`` -> ``fire("hung")``;
c) com ``inflight`` e ``hwnd``, ``not is_window(hwnd)`` em 2 ticks seguidos -> ``fire("window_gone")`` (R31);
d) evento de parada sinalizado: sem dono -> parada limpa; com dono -> WARNING uma vez e adia;
e) idle: sem dono, fila vazia e sem ``inflight`` por ``idle`` s -> ``host.try_idle_stop()`` (próximo tick se não deu).

``fire(kind)`` (R9), nesta ordem: arma ``sleep(2); exit(3)`` numa thread daemon ANTES de tudo; escolhe o tipo
(``ClientTableFull`` se o diálogo "HLLAPI ERROR" do próprio processo estiver visível; senão ``SessionStopped`` ou
``WorkerHung``; ``delivery="unknown"`` se o job em voo for N); responde o job e avisa ``shutting_down``; grava
``exit_reason``; ``flush()`` dos logs; ``exit(3)``. Nunca ``ResetSystem`` aqui; nunca ``TerminateProcess``.

Injeção para testes (J.3): ``Watchdog(host, exit_fn=..., clock=..., is_window=..., dialog_visible=...,
start_timer=...)``. No modo ``--fake-dll`` os HWNDs são falsos: o ``main`` passa um ``is_window`` neutro.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import ClientTableFull, RumbaError, SessionStopped, WorkerHung
from .. import _log, _protocol, _win32
from .engine import JobCancelled

import threading
import logging
import time
import os

logger = logging.getLogger(__name__)

# WD_QuerySessions responde em < 1 s (ao vivo: 0,2–0,8 s no registro); logo depois de um emulador sair já travou para
# sempre dentro da DLL (2026-10-04): 8 s em vez de 20 s encurtam o custo do travamento (o worker é reciclado)
LIMITS: dict[str, float] = {"WD_ConnectPS": 15.0, "WD_QuerySessions": 8.0}
DEFAULT_LIMIT = 20.0
STOP_LIMIT = 5.0                         # DisconnectPS/ResetSystem durante o encerramento
STOP_FUNCS = frozenset({"WD_DisconnectPS", "WD_ResetSystem"})
TICK = 0.1
SUSPEND_GAP = 5.0
EXIT_DELAY = 2.0
WINDOW_TICKS = 2

EXIT_REASONS = {"hung": "hung", "window_gone": "window_gone", "table_full": "client_table_full"}


@dataclass
class Inflight:
    func: str
    job_id: int | None
    t0: float
    limit: float
    job: Any = None


class WatchHost(Protocol):
    """O que o watchdog precisa do servidor (``WorkerServer`` implementa)."""

    letter: str

    def current_job(self) -> Any: ...

    def engine_hwnd(self) -> int | None: ...

    def fire_respond(self, job: Any, exc: RumbaError) -> None: ...

    def fire_record(self, exit_reason: str) -> None: ...

    def stop_requested(self) -> bool: ...

    def has_owner(self) -> bool: ...

    def busy(self) -> bool: ...

    def stop_clean(self, reason: str) -> None: ...

    def try_idle_stop(self) -> bool: ...


def _start_daemon_timer(seconds: float, fn: Callable[[], Any]) -> threading.Thread:
    def run() -> None:
        time.sleep(seconds)
        fn()

    thread = threading.Thread(target=run, name="watch-exit", daemon=True)
    thread.start()
    return thread


class Watchdog:
    def __init__(self, host: WatchHost | None = None, *, exit_fn: Callable[[int], Any] = os._exit,
                 clock: Callable[[], float] = time.monotonic, idle: float = 1800.0,
                 is_window: Callable[[int], bool] = _win32.is_window,
                 dialog_visible: Callable[[int | None], bool] = _win32.hllapi_error_dialog_visible,
                 start_timer: Callable[[float, Callable[[], Any]], Any] = _start_daemon_timer,
                 sleep: Callable[[float], Any] = time.sleep) -> None:
        self.host = host
        self.exit_fn = exit_fn
        self.clock = clock
        self.idle = float(idle)
        self.is_window = is_window
        self.dialog_visible = dialog_visible
        self.start_timer = start_timer
        self.sleep = sleep
        self.stopping = False                # encerramento: DisconnectPS/ResetSystem com limite de 5 s
        self.fired: str | None = None
        self._inflight: Inflight | None = None
        self._last_tick: float | None = None
        self._idle_base = clock()
        self._window_misses = 0
        self._stop_warned = False
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- chamadas à DLL
    def limit_for(self, func: str) -> float:
        if self.stopping and func in STOP_FUNCS:
            return STOP_LIMIT
        return LIMITS.get(func, DEFAULT_LIMIT)

    @contextmanager
    def inflight(self, func: str) -> Iterator[None]:
        if self.fired:                       # disparou: nenhuma chamada nova à DLL até o os._exit (entrada, nunca)
            raise JobCancelled(0)
        job = self.host.current_job() if self.host is not None else None
        with self._lock:
            self._inflight = Inflight(func, getattr(job, "id", None), self.clock(), self.limit_for(func), job)
        try:
            yield
        finally:
            with self._lock:
                self._inflight = None
                self._idle_base = self.clock()

    @property
    def current(self) -> Inflight | None:
        return self._inflight

    def touch(self) -> None:
        """Atividade (requisição, conexão): reinicia a contagem do idle."""
        self._idle_base = self.clock()

    # ----------------------------------------------------------------- tick
    def tick(self) -> None:
        if self.fired:
            return
        now = self.clock()
        last, self._last_tick = self._last_tick, now
        inflight = self._inflight
        if last is not None and now - last > SUSPEND_GAP:              # (a) suspensão/hibernação
            logger.warning("watchdog: intervalo de %.1f s entre ticks (suspensão?); prazos rearmados", now - last)
            with self._lock:
                if self._inflight is not None:
                    self._inflight.t0 = now
            self._idle_base = now
            self._window_misses = 0
            return
        host = self.host
        if inflight is not None and now - inflight.t0 > inflight.limit:  # (b)
            self.fire("hung")
            return
        hwnd = host.engine_hwnd() if host is not None else None
        if inflight is not None and hwnd:                                # (c)
            if not self.is_window(hwnd):
                self._window_misses += 1
                if self._window_misses >= WINDOW_TICKS:
                    self.fire("window_gone")
                    return
            else:
                self._window_misses = 0
        else:
            self._window_misses = 0
        if host is None:
            return
        if host.stop_requested():                                        # (d)
            if not host.has_owner():
                host.stop_clean("stop_event")
                return
            if not self._stop_warned:
                logger.warning("evento de parada recebido com dono ativo; adiado até o fim do lease")
                self._stop_warned = True
        if host.has_owner() or host.busy() or inflight is not None:      # (e)
            self._idle_base = now
        elif now - self._idle_base >= self.idle:
            if not host.try_idle_stop():
                return

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set() and not self.fired:
            try:
                self.tick()
            except Exception as exc:                 # o watchdog nunca morre por causa de um tick
                logger.error("watchdog: erro no tick (%s)", type(exc).__name__)
            self.sleep(TICK)

    # ----------------------------------------------------------------- disparo
    def make_error(self, kind: str, inflight: Inflight | None) -> tuple[str, RumbaError]:
        letter = self.host.letter if self.host is not None else "?"
        job = inflight.job if inflight is not None else None
        op = getattr(job, "op", None)
        input_op = isinstance(op, str) and op in _protocol.OPS and not _protocol.OPS[op].idempotent
        delivery = "unknown" if input_op else None
        try:
            table_full = bool(self.dialog_visible(os.getpid()))
        except Exception:
            table_full = False
        func = inflight.func if inflight is not None else "?"
        if table_full:
            return "table_full", ClientTableFull.make(function=func, delivery=delivery, terminal=letter)
        if kind == "window_gone":
            return kind, SessionStopped.make(L=letter, function=func, delivery=delivery, terminal=letter)
        limit = inflight.limit if inflight is not None else DEFAULT_LIMIT
        return kind, WorkerHung.make("input" if input_op else "default", funcao=func, s=limit, function=func,
                                     delivery=delivery, terminal=letter)

    def fire(self, kind: str) -> None:
        if self.fired:
            return
        self.fired = kind
        self.start_timer(EXIT_DELAY, lambda: self.exit_fn(_protocol.EXIT_WATCHDOG))   # 1) antes de tudo
        inflight = self._inflight
        cancel = getattr(inflight.job, "cancel", None) if inflight is not None else None
        if cancel is not None:
            # o cliente vai receber WorkerHung/delivery=unknown: se a chamada travada voltar antes do os._exit, o
            # send em curso para antes do próximo chunk (nenhuma AID depois da resposta)
            cancel.set()
        try:
            reason, exc = self.make_error(kind, inflight)                               # 2)
            logger.critical("watchdog: %s em %s (job %s, limite %.0f s); encerrando com código 3", reason,
                            inflight.func if inflight else "-", inflight.job_id if inflight else "-",
                            inflight.limit if inflight else 0)
            if self.host is not None:
                try:
                    self.host.fire_respond(inflight.job if inflight else None, exc)     # 3)
                except Exception as err:
                    logger.error("watchdog: não consegui responder (%s)", type(err).__name__)
                try:
                    self.host.fire_record(EXIT_REASONS.get(reason, reason))             # 4)
                except Exception as err:
                    logger.error("watchdog: não consegui gravar exit_reason (%s)", type(err).__name__)
        finally:
            _log.flush_all()                                                            # 5)
            self.exit_fn(_protocol.EXIT_WATCHDOG)                                       # 6)
