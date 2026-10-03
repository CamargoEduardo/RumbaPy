"""Watchdog (D.4) com relógio, ``exit_fn``, ``is_window`` e temporizador injetados: nunca mata o pytest."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rumbapy._worker.watchdog import DEFAULT_LIMIT, Watchdog
from rumbapy._worker.mask import MaskState
from rumbapy._worker.main import collides
from rumbapy._worker.hllapi import Hllapi
from rumbapy._worker.engine import Engine, JobCancelled
from tests.fakes import scenarios
from rumbapy import errors

import threading
import time

import pytest


@dataclass
class Job:
    id: int
    op: str


@dataclass
class Host:
    letter: str = "D"
    job: Any = None
    hwnd: int | None = None
    stop: bool = False
    owner: bool = False
    is_busy: bool = False
    events: list[Any] = field(default_factory=list)
    idle_ok: bool = True

    def current_job(self) -> Any:
        return self.job

    def engine_hwnd(self) -> int | None:
        return self.hwnd

    def fire_respond(self, job: Any, exc: errors.RumbaError) -> None:
        self.events.append(("respond", job, exc))

    def fire_record(self, exit_reason: str) -> None:
        self.events.append(("record", exit_reason))

    def stop_requested(self) -> bool:
        return self.stop

    def has_owner(self) -> bool:
        return self.owner

    def busy(self) -> bool:
        return self.is_busy

    def stop_clean(self, reason: str) -> None:
        self.events.append(("stop", reason))

    def try_idle_stop(self) -> bool:
        self.events.append(("idle",))
        return self.idle_ok


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(host: Host, **kw: Any) -> tuple[Watchdog, Clock]:
    clock = Clock()

    def exit_fn(code: int) -> None:
        host.events.append(("exit", code))

    def start_timer(seconds: float, fn: Any) -> None:
        host.events.append(("timer", seconds))

    wd = Watchdog(host, exit_fn=exit_fn, clock=clock, start_timer=start_timer,
                  dialog_visible=kw.pop("dialog_visible", lambda pid: False), **kw)
    return wd, clock


def advance(wd: Watchdog, clock: Clock, seconds: float, step: float = 0.1) -> None:
    for _ in range(int(round(seconds / step))):
        clock.t += step
        wd.tick()


@pytest.mark.parametrize("op, delivery, variant_text", [
    ("snapshot", None, "o processo auxiliar foi reiniciado."),
    ("send", "unknown", "Não sei se a entrada chegou ao host."),
])
def test_hung_dll_call_fires_with_the_timer_armed_first(op: str, delivery: str | None, variant_text: str) -> None:
    fake = scenarios.hang("WD_CopyPS")
    host = Host(job=Job(7, op))
    wd, clock = make(host)
    # window_pid injetado: o HWND do FakeEhllapi é falso e nunca vai ao user32 real (J.2/J.3)
    engine = Engine(Hllapi(fake, track=wd.inflight), MaskState(), letter="D", window_pid=lambda h: 0)
    engine.connect("D")
    worker = threading.Thread(target=lambda: _swallow(engine.snapshot, False), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while wd.current is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert wd.current is not None and wd.current.func == "WD_CopyPS" and wd.current.job_id == 7
        wd.tick()
        advance(wd, clock, DEFAULT_LIMIT - 0.5)
        assert host.events == []                                     # ainda dentro do prazo
        advance(wd, clock, 1.0)
        kinds = [e[0] for e in host.events]
        assert kinds == ["timer", "respond", "record", "exit"]        # temporizador ANTES da resposta
        assert host.events[0] == ("timer", 2.0)
        _, job, exc = host.events[1]
        assert job.id == 7 and type(exc) is errors.WorkerHung and exc.delivery == delivery
        assert exc.message.endswith(variant_text) and "WD_CopyPS" in exc.message and "20 s" in exc.message
        assert host.events[2] == ("record", "hung") and host.events[3] == ("exit", 3)
        assert wd.fired == "hung"
        n = len(host.events)
        advance(wd, clock, 1.0)
        assert len(host.events) == n                                  # dispara uma vez só
    finally:
        fake.hang_event.set()
        worker.join(5)


@dataclass
class CancellableJob:
    id: int
    op: str
    cancel: threading.Event = field(default_factory=threading.Event)


def test_fire_cancels_the_send_in_flight_and_refuses_new_dll_calls() -> None:
    fake = scenarios.hang("WD_SendKey")
    host = Host(job=CancellableJob(9, "send"))
    wd, _clock = make(host)
    engine = Engine(Hllapi(fake, track=wd.inflight), MaskState(), letter="D", window_pid=lambda h: 0)
    engine.connect("D")
    outcome: list[BaseException] = []

    def run() -> None:
        try:
            engine.send([b"ABC", b"@E"], False, host.job.cancel)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while wd.current is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert wd.current is not None and wd.current.func == "WD_SendKey"
        wd.fire("hung")
        assert host.job.cancel.is_set()                             # antes de a chamada travada voltar
    finally:
        fake.hang_event.set()
        worker.join(5)
    assert len(outcome) == 1 and isinstance(outcome[0], JobCancelled)
    assert sum(1 for c in fake.calls if c[0] == "WD_SendKey") == 1   # o @E (AID) nunca saiu depois do disparo
    with pytest.raises(JobCancelled):
        engine.hll.cursor()                                          # nenhuma chamada nova à DLL depois do disparo


def _swallow(fn: Any, *args: Any) -> None:
    try:
        fn(*args)
    except Exception:
        pass


def test_connect_has_a_15_second_limit_and_stop_calls_5() -> None:
    wd, _clock = make(Host())
    assert wd.limit_for("WD_ConnectPS") == 15.0 and wd.limit_for("WD_CopyPS") == 20.0
    assert wd.limit_for("WD_DisconnectPS") == 20.0
    wd.stopping = True
    assert wd.limit_for("WD_DisconnectPS") == 5.0 and wd.limit_for("WD_ResetSystem") == 5.0


def test_clock_jump_over_5_seconds_does_not_fire() -> None:
    host = Host(job=Job(1, "read"))
    wd, clock = make(host)
    with wd.inflight("WD_CopyPSToString"):
        wd.tick()
        advance(wd, clock, 15.0)
        clock.t += 25.0                                               # suspensão/hibernação
        wd.tick()
        advance(wd, clock, 15.0)
        assert host.events == [] and not wd.fired
        advance(wd, clock, 6.0)
        assert wd.fired == "hung"


def test_window_gone_two_ticks_in_a_row() -> None:
    state = {"alive": True}
    host = Host(job=Job(3, "send"), hwnd=0x1000)
    wd, clock = make(host, is_window=lambda hwnd: state["alive"])
    with wd.inflight("WD_SendKey"):
        wd.tick()
        state["alive"] = False
        advance(wd, clock, 0.1)
        assert host.events == []                                      # 1 tick: ainda não
        state["alive"] = True
        advance(wd, clock, 0.1)
        state["alive"] = False
        advance(wd, clock, 0.1)
        assert host.events == []                                      # a contagem recomeçou
        advance(wd, clock, 0.1)
    _, _job, exc = host.events[1]
    assert type(exc) is errors.SessionStopped and exc.delivery == "unknown"
    assert host.events[2] == ("record", "window_gone")


def test_window_check_only_with_inflight() -> None:
    host = Host(hwnd=0x1000)
    wd, clock = make(host, is_window=lambda hwnd: False)
    wd.tick()
    advance(wd, clock, 1.0)
    assert not wd.fired


def test_hllapi_error_dialog_means_client_table_full() -> None:
    host = Host(job=Job(1, "connect"))
    wd, clock = make(host, dialog_visible=lambda pid: True)
    with wd.inflight("WD_ConnectPS"):
        wd.tick()
        advance(wd, clock, 16.0)
    _, _job, exc = host.events[1]
    assert type(exc) is errors.ClientTableFull and host.events[2] == ("record", "client_table_full")


def test_stop_event_deferred_while_there_is_an_owner(caplog: pytest.LogCaptureFixture) -> None:
    host = Host(stop=True, owner=True)
    wd, clock = make(host)
    wd.tick()
    advance(wd, clock, 1.0)
    assert host.events == []
    assert sum("adiado" in r.getMessage() for r in caplog.records) == 1      # WARNING uma vez
    host.owner = False
    advance(wd, clock, 0.1)
    assert host.events == [("stop", "stop_event")]


def test_idle_stop_after_idle_seconds_without_activity() -> None:
    host = Host(idle_ok=False)
    wd, clock = make(host, idle=3.0)
    wd.tick()
    advance(wd, clock, 2.0)
    assert host.events == []
    host.owner = True
    advance(wd, clock, 2.0)                                           # com dono não conta
    host.owner = False
    advance(wd, clock, 2.0)
    assert host.events == []
    advance(wd, clock, 1.5)
    assert ("idle",) in host.events                                   # trava ocupada -> tenta no próximo tick
    count = host.events.count(("idle",))
    advance(wd, clock, 0.2)
    assert host.events.count(("idle",)) > count


def test_collides_on_loword() -> None:
    assert collides(0x10005, [0x20005, 1234])
    assert not collides(0x10005, [0x10005, 0x10006])
    assert not collides(42, [])
