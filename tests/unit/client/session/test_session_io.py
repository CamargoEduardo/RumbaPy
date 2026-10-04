"""Session: leitura, escrita local, teclas e a sequência pré/pós-AID (B.3, B.4, G.3) sobre o ``InProcessBackend``."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import (EncodingError, HostBusy, InvalidPosition, NoHostResponse, ParameterError,
                            ProtectedField, Truncated, UndefinedKey, WaitTimeout)
from tests.unit.client.session.support import aids, echo_host, make_session, send_calls, table_screen
from rumbapy.keys import Key
from tests.fakes import scenarios
from rumbapy import _protocol

import time

import pytest


@pytest.fixture
def table():
    session, backend, fake = make_session(table_screen())
    echo_host(fake)
    yield session, backend, fake
    session.release()


# ------------------------------------------------------------------------------------------- leitura

def test_read_returns_exact_length_and_field(table):
    t, _, _ = table
    assert t.read(1, 2, 5) == "VOLVO"
    assert len(t.read(1, 1, 200)) == 200
    assert t.read_field(5, 12) == "TITULO".ljust(20)
    assert t.find("SP0001T") == (1, 8)
    assert t.screen.get(1, 2, 5) == "VOLVO"


def test_read_validates_position_before_the_dll(table):
    t, backend, _ = table
    before = list(backend.calls)
    with pytest.raises(InvalidPosition):
        t.read(25, 1, 1)
    with pytest.raises(InvalidPosition):
        t.read(24, 80, 2)
    with pytest.raises(ParameterError):
        t.read(1, 1, 0)
    assert backend.calls == before


def test_read_busy_retries_until_deadline_then_host_busy(table):
    t, backend, fake = table
    t.timeout = 0.3
    fake.sessions["D"].lock_for(5.0)
    start = time.monotonic()
    with pytest.raises(HostBusy) as info:
        t.read(1, 2, 5)
    assert time.monotonic() - start >= 0.3
    assert backend.calls.count("read") >= 3
    assert info.value.terminal == "D" and info.value.rc == 4
    assert t.state == "ok"


def test_read_busy_then_free_returns_text(table):
    t, backend, fake = table
    fake.sessions["D"].lock_for(0.15)
    assert t.read(1, 2, 5) == "VOLVO"
    assert backend.calls.count("read") >= 2


# ------------------------------------------------------------------------------------------- escrita local

def test_write_and_fill(table):
    t, _, fake = table
    t.write(3, 14, "PN1")
    assert t.read(3, 14, 8) == "PN1     "
    t.fill(3, 14, "AB")
    assert t.read(3, 14, 8) == "AB      "
    t.fill(7, 30, "12", align="right")
    assert t.read(7, 30, 5) == "   12"
    t.fill(3, 16, "XY")                       # do meio do campo até o fim
    assert t.read(3, 14, 8) == "ABXY    "
    t.fill(3, 14, "")
    assert t.read(3, 14, 8) == " " * 8
    assert aids(fake) == []                   # nada foi ao host


def test_fill_too_long_is_truncated_none_without_copy(table):
    t, _, fake = table
    before = [c for c, _ in fake.calls].count("WD_CopyStringToPS")
    with pytest.raises(Truncated) as info:
        t.fill(3, 14, "123456789")
    assert info.value.delivery == "none"
    assert "(9)" in info.value.message and "(8)" in info.value.message
    with pytest.raises(Truncated) as secret:
        t.fill(9, 20, "senha-grande", secret=True)
    assert "senha" not in str(secret.value) and "12" not in secret.value.message
    assert [c for c, _ in fake.calls].count("WD_CopyStringToPS") == before
    assert t.state == "ok"


def test_write_protected_and_encoding(table):
    t, _, _ = table
    with pytest.raises(ProtectedField):
        t.fill(5, 12, "X")
    with pytest.raises(ProtectedField):
        t.write(5, 12, "X")
    with pytest.raises(EncodingError):
        t.write(3, 14, "a\tb")
    with pytest.raises(EncodingError) as info:
        t.write(3, 14, "s\u0001", secret=True)
    assert "U+" not in info.value.message
    with pytest.raises(ParameterError):
        t.write(3, 14, "")
    assert t.state == "ok"


def test_type_and_move_to(table):
    t, _, fake = table
    t.move_to(3, 14).type("a@b")
    assert t.read(3, 14, 3) == "a@b"
    assert b"a@@b" in send_calls(fake)
    t.move_to(3, 14).press(Key.ERASE_EOF)
    assert t.read(3, 14, 8) == " " * 8
    assert aids(fake) == []


# ------------------------------------------------------------------------------------------- teclas e AID

def test_press_enter_stable_before_aid_alone(table):
    t, backend, fake = table
    t.fill(3, 14, "PN1")
    calls_before = len(backend.calls)
    t.enter()
    ops = backend.calls[calls_before:]
    first_send = ops.index("send")
    assert ops[:first_send].count("snapshot") >= 2            # tela estável (2 snapshots) antes da AID
    assert send_calls(fake)[-1] == b"@E"                      # AID sozinha num send de 1 chunk
    assert aids(fake) == ["ENTER"]
    assert t.read(23, 7, 12) == "ATUALIZADO 1"


def test_press_without_response_is_no_host_response_and_uncertain():
    t, _, fake = make_session(table_screen(), timeout=0.4)     # host mudo: a tela não muda com o ENTER
    try:
        with pytest.raises(NoHostResponse) as info:
            t.enter()
        exc = info.value
        assert exc.delivery == "unknown" and exc.terminal == "D"
        assert exc.message == "A tecla ENTER foi enviada, mas o host não respondeu em 0 s."
        assert exc.hint and "NÃO repita" in exc.hint
        assert exc.screen is not None
        assert isinstance(exc, WaitTimeout) and isinstance(exc, TimeoutError)
        assert t.state == "uncertain"
        assert aids(fake) == ["ENTER"]
    finally:
        t.release()


def test_response_delay_read_after_enter_sees_new_screen():
    fake = scenarios.slow_host(0.5)
    t, _, _ = make_session(fake, timeout=3.0)
    try:
        t.fill(22, 56, "C").enter()
        assert t.read(4, 6, 8) == "TERMINAL"
    finally:
        t.release()


def test_lock_visible_after_uses_oia_after():
    fake = scenarios.slow_host(0.5, lock_visible_after=0.3)
    t, _, _ = make_session(fake, timeout=3.0)
    try:
        t.fill(22, 56, "X").enter()               # host ignora 'X': a tela não muda, só o teclado trava e libera
        assert t.state == "ok"
        assert aids(fake) == ["ENTER"]
    finally:
        t.release()


def test_send_keys_waits_screen_between_aids_t_safe_3():
    fake = scenarios.slow_host(0.5, lock_visible_after=0.3)
    session = fake.sessions["D"]
    stamps: list[float] = []
    original = fake._aid

    def spy(s, aid):
        stamps.append(time.monotonic())
        original(s, aid)

    fake._aid = spy
    t, _, _ = make_session(fake, timeout=5.0)
    try:
        t.send_keys("a<Enter>b<Enter>")
        assert aids(fake) == ["ENTER", "ENTER"]
        assert stamps[1] - stamps[0] >= 0.5       # a 2ª AID só depois da resposta da 1ª
        assert session.pending_responses == 0
    finally:
        t.release()


def test_send_keys_validates_everything_before_sending(table):
    t, _, fake = table
    with pytest.raises(UndefinedKey):
        t.send_keys("abc<Enter>def<Bogus>")
    assert send_calls(fake) == [] and aids(fake) == []
    with pytest.raises(UndefinedKey) as info:
        t.send_keys("x<segredo>", secret=True)
    assert "segredo" not in str(info.value)


def test_send_keys_long_body_is_split_in_several_sends(table):
    t, backend, fake = table
    t.move_to(3, 14)
    chunk_count = _protocol.MAX_CHUNKS + 6
    text = "<Home>" * (chunk_count * 64)              # 2 bytes por tecla local -> 64 por chunk
    t.send_keys(text)
    sends = [c for c in backend.calls if c == "send"]
    assert len(sends) == 2
    assert aids(fake) == []


def test_send_keys_tab_and_enter_counts_one_aid(table):
    t, _, fake = table
    t.move_to(3, 14).send_keys("PN2<Tab>99<Enter>")
    assert aids(fake) == ["ENTER"]
    assert fake.sessions["D"].aid_log[0][1].get("pn") == "PN2"


def test_pf_pa_and_page_down_are_aids(table):
    t, _, fake = table
    t.pf(8)
    t.press("PAGE_DOWN")
    t.pa(1)
    assert aids(fake) == ["PF8", "PAGEDOWN", "PA1"]
    with pytest.raises(ParameterError):
        t.pa(4)
    with pytest.raises(ParameterError):
        t.pf(25)


def test_pre_snapshot_is_taken_after_typing():
    t, _, fake = make_session(table_screen(), timeout=0.4)     # host mudo: só o eco local muda a tela
    try:
        with pytest.raises(NoHostResponse):
            t.move_to(3, 14).send_keys("PN1<Enter>")             # o eco de "PN1" não conta como resposta
        assert aids(fake) == ["ENTER"]
    finally:
        t.release()


def test_lock_seen_only_in_oia_after_counts_as_answer(monkeypatch):
    import dataclasses
    from rumbapy.screen import OIA

    t, backend, fake = make_session(table_screen(), timeout=0.4)  # host mudo e sem bloqueio visível
    locked = OIA.from_raw(bytes([1]) + b"\x10" * 80 + bytes([0x10] + [0] * 7 + [0x20] + [0] * 13))
    assert locked.keyboard_locked
    real_send = backend.send

    def send(chunks, *, secret):
        result = real_send(chunks, secret=secret)
        return dataclasses.replace(result, oia_after=locked) if "@E" in chunks else result

    monkeypatch.setattr(backend, "send", send)
    try:
        t.enter()                                            # o bloqueio visto no oia_after = o host respondeu
        assert t.state == "ok" and aids(fake) == ["ENTER"]
    finally:
        t.release()


def test_client_logs_never_contain_typed_text(table, caplog):
    import logging

    t, _, _ = table
    with caplog.at_level(logging.DEBUG, logger="rumbapy"):
        t.move_to(3, 14).send_keys("Zq7pw9k<Tab>Wx5<Enter>")
        t.fill(9, 20, "Kp3sec", secret=True)
        t.write(3, 14, "Zq7pw9k")
    for secret in ("Zq7pw9k", "Kp3sec", "Wx5"):
        assert secret not in caplog.text
    assert "Kp3sec" not in t.read(9, 20, 8) and "Kp3sec" not in str(t.screen)
