"""Aceite P-A: motor ``waits.wait_until`` com relógio falso (G.2) e constantes (G.6)."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rumbapy.errors import HostConnectionLost, HostError, NoHostResponse, ParameterError, WaitTimeout
from rumbapy.screen import Cursor, Screen
from tests.unit.core.screens import make_screen
from rumbapy import waits

import logging

import pytest


class FakeClock:
    """Relógio + sleep falsos: ``sleep`` só avança o tempo (e registra os intervalos pedidos)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class Snapshots:
    """``snapshot()`` que devolve as telas de ``script(t)`` (t = segundos desde o início) e conta as chamadas."""

    def __init__(self, clock: FakeClock, script: Callable[[float], Screen]) -> None:
        self.clock, self.script, self.start, self.calls = clock, script, clock.now, 0
        self.log: list[str] = []

    def __call__(self) -> Screen:
        self.calls += 1
        self.log.append("snapshot")
        return self.script(self.clock.now - self.start)


READY = make_screen({1: "TELA INICIAL"})
NEXT = make_screen({1: " VOLVO"}, cursor=(22, 56))
OFFLINE = make_screen({1: "TELA INICIAL"}, online=False)


def run(cond: Callable[[Screen], Any], script: Callable[[float], Screen], *, timeout: float = 5.0,
        describe: Callable[[Screen], str] | None = None, error_checks: Sequence[Callable[[Screen], None]] = (),
        clock: FakeClock | None = None, **kwargs: Any) -> tuple[Screen, FakeClock, Snapshots]:
    clock = clock or FakeClock()
    snaps = Snapshots(clock, script)
    result = waits.wait_until(cond, snapshot=snaps, timeout=timeout,
                              describe=describe or (lambda s: f"Esperei {timeout:g} s pela tela."),
                              error_checks=error_checks, clock=clock.clock, sleep=clock.sleep, **kwargs)
    return result, clock, snaps


# ------------------------------------------------------------------ sucesso

def test_success_immediately_without_sleeping() -> None:
    result, clock, snaps = run(lambda s: s.find("TELA"), lambda t: READY)
    assert result.match == Cursor(1, 1) and snaps.calls == 1 and clock.sleeps == []
    assert result.text == READY.text and READY.match is None                 # original intacta


def test_success_after_the_screen_changes() -> None:
    result, clock, snaps = run(lambda s: s.find("VOLVO"), lambda t: NEXT if t >= 0.5 else READY)
    assert result.match == Cursor(1, 2)
    assert 0.5 <= clock.now - 1000.0 < 0.56
    assert set(clock.sleeps) == {0.05}


@pytest.mark.parametrize("value, match", [
    (True, None), (Cursor(3, 4), Cursor(3, 4)), ("chave", "chave"), ("", ""), (READY, None), (1, None),
])
def test_match_values(value: Any, match: Any) -> None:
    result, _, _ = run(lambda s: value, lambda t: READY)
    assert result.match == match and (result.match is None) == (match is None)


@pytest.mark.parametrize("value", [None, False, 0, []])
def test_falsy_results_keep_waiting(value: Any) -> None:
    with pytest.raises(WaitTimeout):
        run(lambda s: value, lambda t: READY, timeout=0.2)


# ------------------------------------------------------------------ polling

def test_polling_is_fast_then_slow() -> None:
    assert waits.poll_interval(0.0) == 0.05 and waits.poll_interval(0.99) == 0.05 and waits.poll_interval(1.0) == 0.1
    clock = FakeClock()                      # intervalos binários exatos: sem erro de arredondamento no relógio falso
    with pytest.raises(WaitTimeout):
        run(lambda s: None, lambda t: READY, timeout=3.0, clock=clock, fast_poll=0.0625, slow_poll=0.125)
    assert clock.sleeps == [0.0625] * 16 + [0.125] * 16
    assert clock.now - 1000.0 == 3.0


def test_last_sleep_is_cut_at_the_deadline() -> None:
    clock = FakeClock()
    with pytest.raises(WaitTimeout):
        run(lambda s: None, lambda t: READY, timeout=0.12, clock=clock)
    assert clock.sleeps == pytest.approx([0.05, 0.05, 0.02])
    assert clock.now - 1000.0 == pytest.approx(0.12)


def test_custom_polling() -> None:
    clock = FakeClock()
    with pytest.raises(WaitTimeout):
        run(lambda s: None, lambda t: READY, timeout=1.0, clock=clock, fast_phase=0.0, slow_poll=0.25)
    assert clock.sleeps == pytest.approx([0.25] * 4)


# ------------------------------------------------------------------ timeout

def test_timeout_message_and_screen() -> None:
    def describe(s: Screen) -> str:
        return (f"Esperei 2 s por 'VOLVO' em qualquer lugar da tela. Linha 1 atual: '{s.line(1).rstrip()}'. "
                f"Estado do terminal: {s.oia.describe()}.")

    with pytest.raises(WaitTimeout) as info:
        run(lambda s: s.find("VOLVO"), lambda t: READY, timeout=2.0, describe=describe)
    exc = info.value
    assert exc.message == ("Esperei 2 s por 'VOLVO' em qualquer lugar da tela. Linha 1 atual: 'TELA INICIAL'. "
                           "Estado do terminal: teclado livre.")
    assert exc.screen is not None and exc.screen.text == READY.text
    assert exc.terminal == "D" and isinstance(exc, TimeoutError)
    assert str(exc).startswith("[D] Esperei 2 s")


def test_timeout_message_is_exactly_what_describe_returns() -> None:
    """G.2: o motor não acrescenta nada (o G8 da variante 'keyboard' do wait_ready não se repete)."""
    locked = make_screen(locked=True)
    message = ("O teclado continua bloqueado após 0.1 s (G8=00 20 00 00 00). Se for erro de digitação ('X -f'), "
               "use t.press(Key.RESET).")
    with pytest.raises(WaitTimeout) as info:
        run(lambda s: None, lambda t: locked, timeout=0.1, describe=lambda s: message)
    assert info.value.message == message


def test_timeout_uses_the_exc_class() -> None:
    text = "A tecla ENTER foi enviada, mas o host não respondeu em 0 s."
    with pytest.raises(NoHostResponse) as info:
        run(lambda s: None, lambda t: READY, timeout=0.1, exc=NoHostResponse, describe=lambda s: text)
    assert info.value.delivery == "unknown"
    assert info.value.message == text                     # texto exato da B.7, sem acréscimo do motor


def test_timeout_zero_still_takes_one_snapshot() -> None:
    _, clock, snaps = run(lambda s: True, lambda t: READY, timeout=0)
    assert snaps.calls == 1 and clock.sleeps == []
    with pytest.raises(WaitTimeout):
        run(lambda s: None, lambda t: READY, timeout=0)


def test_timeout_never_attaches_a_screen_with_unknown_mask() -> None:
    unknown = make_screen({1: "SEGREDO"}, hidden_unknown=True)
    with pytest.raises(WaitTimeout) as info:
        run(lambda s: None, lambda t: unknown, timeout=0.1, describe=lambda s: "Esperei.")
    assert info.value.screen is None


def test_describe_failure_falls_back_to_a_generic_message(caplog: pytest.LogCaptureFixture) -> None:
    def broken(s: Screen) -> str:
        raise RuntimeError("bug: SEGREDO-NA-TELA")

    with caplog.at_level(logging.DEBUG, logger="rumbapy.waits"):
        with pytest.raises(WaitTimeout) as info:
            run(lambda s: None, lambda t: READY, timeout=0.5, describe=broken)
    assert info.value.message == "A condição de espera não aconteceu em 0.5 s. Estado do terminal: teclado livre."
    records = [r for r in caplog.records if r.name == "rumbapy.waits"]
    assert records and all(r.exc_info is None for r in records)          # sem traceback no log
    assert "RuntimeError" in caplog.text and "SEGREDO" not in caplog.text  # só o tipo, nunca a mensagem


# ------------------------------------------------------------------ error_checks

def test_error_checks_run_before_the_condition() -> None:
    order: list[str] = []

    def check(s: Screen) -> None:
        order.append("check")

    def cond(s: Screen) -> bool:
        order.append("cond")
        return len(order) >= 4

    run(cond, lambda t: READY, error_checks=[check])
    assert order == ["check", "cond", "check", "cond"]


def test_error_check_wins_over_a_satisfied_condition() -> None:
    def fail(s: Screen) -> None:
        raise HostError.make(screen=s)

    with pytest.raises(HostError):
        run(lambda s: True, lambda t: READY, error_checks=[fail])


def test_cics_error_check() -> None:
    grave = make_screen(at={(5, 31): waits.CICS_ERROR_TEXT})
    with pytest.raises(HostError) as info:
        run(lambda s: None, lambda t: READY if t < 0.3 else grave, error_checks=[waits.cics_error_check])
    assert info.value.screen is not None and info.value.terminal == "D"
    waits.cics_error_check(READY)                                           # tela normal: nada
    waits.cics_error_check(make_screen(rows=4))                             # tela pequena: nada


# ------------------------------------------------------------------ host perdido

def test_host_connection_lost_after_3_s_offline() -> None:
    clock = FakeClock()
    with pytest.raises(HostConnectionLost) as info:
        run(lambda s: None, lambda t: OFFLINE, timeout=30.0, clock=clock)
    assert 3.0 <= clock.now - 1000.0 < 3.11
    assert info.value.terminal == "D" and "A conexão com o mainframe caiu (D)." in str(info.value)


def test_offline_must_be_continuous() -> None:
    def flapping(t: float) -> Screen:
        return READY if int(t) % 2 else OFFLINE                             # 1 s offline, 1 s online...

    clock = FakeClock()
    with pytest.raises(WaitTimeout):
        run(lambda s: None, flapping, timeout=8.0, clock=clock)
    assert clock.now - 1000.0 == pytest.approx(8.0)


def test_offline_but_condition_met_returns() -> None:
    result, _, _ = run(lambda s: s.find("TELA"), lambda t: OFFLINE)
    assert result.match == Cursor(1, 1)


def test_host_lost_after_is_configurable() -> None:
    with pytest.raises(HostConnectionLost):
        run(lambda s: None, lambda t: OFFLINE, host_lost_after=0)


def test_host_lost_while_waiting_for_an_aid_answer_has_unknown_delivery() -> None:
    with pytest.raises(HostConnectionLost) as info:
        run(lambda s: None, lambda t: OFFLINE, host_lost_after=0, exc=NoHostResponse)
    assert info.value.delivery == "unknown"                                 # a AID já tinha saído (B.4.3)
    with pytest.raises(HostConnectionLost) as plain:
        run(lambda s: None, lambda t: OFFLINE, host_lost_after=0)
    assert plain.value.delivery is None                                     # espera sem entrada


def test_timeout_carries_the_callers_hint() -> None:
    with pytest.raises(WaitTimeout) as info:
        run(lambda s: None, lambda t: READY, timeout=0.5, hint="confira a tela (print(e.screen)).")
    assert info.value.hint == "confira a tela (print(e.screen))."
    with pytest.raises(WaitTimeout) as bare:
        run(lambda s: None, lambda t: READY, timeout=0.5)
    assert bare.value.hint is None


# ------------------------------------------------------------------ Ctrl+C

def test_keyboard_interrupt_in_sleep_stops_at_once() -> None:
    clock = FakeClock()
    snaps = Snapshots(clock, lambda t: READY)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        waits.wait_until(lambda s: None, snapshot=snaps, timeout=30.0, describe=lambda s: "x", clock=clock.clock,
                         sleep=sleep)
    assert snaps.calls == 1


def test_keyboard_interrupt_in_snapshot_is_not_swallowed() -> None:
    def snapshot() -> Screen:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        waits.wait_until(lambda s: True, snapshot=snapshot, timeout=1.0, describe=lambda s: "x")


# ------------------------------------------------------------------ validação e constantes

@pytest.mark.parametrize("kwargs", [
    {"timeout": -1}, {"timeout": float("nan")}, {"timeout": float("inf")}, {"timeout": "5"}, {"timeout": True},
    {"timeout": 1, "host_lost_after": -1}, {"timeout": 1, "fast_poll": None},
])
def test_validates_arguments(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ParameterError):
        waits.wait_until(lambda s: True, snapshot=lambda: READY, describe=lambda s: "x", **kwargs)


def test_validates_callables() -> None:
    with pytest.raises(ParameterError):
        waits.wait_until(None, snapshot=lambda: READY, timeout=1, describe=lambda s: "x")  # type: ignore[arg-type]


def test_real_clock_default_works() -> None:
    result = waits.wait_until(lambda s: True, snapshot=lambda: READY, timeout=1.0, describe=lambda s: "x")
    assert result.text == READY.text


def test_constants_follow_g6() -> None:
    assert waits.DEFAULT_TIMEOUT == 30.0
    assert (waits.FAST_PHASE, waits.FAST_POLL, waits.SLOW_POLL) == (1.0, 0.05, 0.1)
    assert waits.READY_STABLE_GAP == 0.05 and waits.FIRST_SCREEN_STABLE == 0.3
    assert waits.HOST_LOST_AFTER == 3.0 and waits.SCREEN_LOAD_TIMEOUT == 120.0
    assert waits.CICS_ERROR_TEXT == "E R R O   G R A V E" and len(waits.CICS_ERROR_TEXT) == 19
    assert waits.CICS_ERROR_AT == (5, 31)
