"""Session: esperas (G.3-G.5) — formas do ``wait_text``, mensagens, ``wait_any``/``wait_change``/``wait_cursor``,
``wait_ready`` e detectores de erro."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import HostError, InvalidPosition, ParameterError, WaitTimeout
from tests.unit.client.session.support import echo_host, make_session, table_screen
from rumbapy.screen import Cursor
from tests.fakes import scenarios

import dataclasses
import re

import pytest


@pytest.fixture
def table():
    t, backend, fake = make_session(table_screen(), timeout=0.3)
    echo_host(fake)
    yield t, backend, fake
    t.release()


def _write_line(fake, row: int, col: int, text: str) -> None:
    s = fake.sessions["D"]
    start = s.pos(row, col) - 1
    s.buffer[start:start + len(text)] = text.encode("cp1252")


# ------------------------------------------------------------------------------------------- wait_text (G.4)

def test_wait_text_forms_and_match(table):
    t, _, _ = table
    assert t.wait_text("VOLVO", 1, 2).match == Cursor(1, 2)
    assert t.wait_text("VOLVO", 1).match == Cursor(1, 2)
    assert t.wait_text("SP0001T").match == Cursor(1, 8)
    assert t.wait_text(re.compile(r"SP\d{4}T"), 1).match == Cursor(1, 8)
    assert t.wait_text(re.compile(r"SP\d{4}"), 1, 8).match == Cursor(1, 8)
    assert t.wait_text(re.compile(r"sp\d{4}"), 1, 8, case=False).match == Cursor(1, 8)
    assert t.wait_text("volvo", 1, 2, case=False).match == Cursor(1, 2)
    assert t.wait_text("volvo", 1, case=False).match == Cursor(1, 2)
    assert t.wait_text("NADA", 1, 2, gone=True).match == Cursor(1, 2)
    assert t.wait_text("NADA", gone=True).match is None


def test_wait_text_validation(table):
    t, backend, _ = table
    before = list(backend.calls)
    with pytest.raises(ParameterError):
        t.wait_text("X", col=3)
    with pytest.raises(ParameterError):
        t.wait_text("")
    with pytest.raises(InvalidPosition):
        t.wait_text("X", 25, 1)
    with pytest.raises(InvalidPosition):
        t.wait_text("XYZ", 24, 79)
    with pytest.raises(ParameterError):
        t.wait_text(b"X")                                   # type: ignore[arg-type]
    assert backend.calls == before


def test_wait_text_timeout_messages(table):
    t, _, _ = table
    with pytest.raises(WaitTimeout) as at:
        t.wait_text("XXXXX", 1, 2, timeout=0.1)
    exc = at.value
    assert exc.message.startswith("Esperei 0.1 s por 'XXXXX' em (1,2), mas ali está 'VOLVO'.")
    assert exc.message.endswith("teclado livre")
    assert exc.hint.startswith("confira a tela (print(e.screen))")
    assert exc.screen is not None and exc.terminal == "D"
    with pytest.raises(WaitTimeout) as anywhere:
        t.wait_text("NUNCA", timeout=0.1)
    assert anywhere.value.message.startswith(
        "Esperei 0.1 s por 'NUNCA' em qualquer lugar da tela. Linha 1 atual: ' VOLVO SP0001T'.")
    with pytest.raises(WaitTimeout) as gone:
        t.wait_text("VOLVO", 1, 2, gone=True, timeout=0.1)
    assert gone.value.message.startswith("'VOLVO' continua em (1,2) após 0.1 s.")


def test_wait_text_sees_late_change_and_clears_uncertain(table):
    t, _, fake = table
    import threading

    timer = threading.Timer(0.1, _write_line, args=(fake, 23, 7, "PRONTO"))
    timer.start()
    t._mark_uncertain()
    assert t.wait_text("PRONTO", 23, 7, timeout=2).match == Cursor(23, 7)
    assert t.state == "ok"


def test_hidden_unknown_omits_content_and_screen(table, monkeypatch):
    t, backend, _ = table
    real = backend.snapshot
    monkeypatch.setattr(backend, "snapshot", lambda **kw: dataclasses.replace(real(**kw), hidden_unknown=True))
    with pytest.raises(WaitTimeout) as info:
        t.wait_text("XXXXX", 1, 2, timeout=0.1)
    assert "VOLVO" not in info.value.message and "omitido" in info.value.message
    assert info.value.screen is None
    with pytest.raises(WaitTimeout) as other:
        t.wait_any({"a": "NUNCA"}, timeout=0.1)
    assert "VOLVO" not in other.value.message


# ------------------------------------------------------------------------------------------- wait_any / change / cursor

def test_wait_any_order_and_kinds(table):
    t, _, _ = table
    s = t.wait_any({"erro": re.compile("ERRO"), "ok": ("VOLVO", 1, 2), "tambem": "SP0001T"})
    assert s.match == "ok"
    s = t.wait_any({"primeiro": "VOLVO", "segundo": lambda scr: True})
    assert s.match == "primeiro"
    s = t.wait_any({"fn": lambda scr: scr.get(1, 2, 5) == "VOLVO"})
    assert s.match == "fn"


def test_wait_any_callable_uses_truthiness(table):
    t, _, _ = table
    blank = {"vazio": lambda scr: scr.get(23, 7, 10).strip(), "zero": lambda scr: 0, "lista": lambda scr: []}
    with pytest.raises(WaitTimeout) as info:
        t.wait_any(blank, timeout=0.1)                  # "", 0 e [] NÃO confirmam a tela
    assert "'vazio' = condição própria" in info.value.message
    assert t.wait_any({"vazio": lambda scr: "", "texto": lambda scr: scr.get(1, 2, 5)}).match == "texto"


def test_wait_any_timeout_lists_options(table):
    t, _, _ = table
    with pytest.raises(WaitTimeout) as info:
        t.wait_any({"ok": ("ATUALIZADO", 23, 7), "erro": re.compile("INVALID|ERRO"), "f": lambda s: False},
                   timeout=0.1)
    message = info.value.message
    assert "'ok' = 'ATUALIZADO' em (23,7)" in message
    assert "'erro' = 'INVALID|ERRO'" in message and "'f' = condição própria" in message
    assert message.endswith("teclado livre")


def test_wait_any_validation(table):
    t, _, _ = table
    for bad in ({}, {"a": ("X", 1)}, {"": "X"}, {"a": ""}, {"a": ("X", 30, 1)}):
        with pytest.raises((ParameterError, InvalidPosition)):
            t.wait_any(bad, timeout=0.1)


def test_wait_change_and_cursor(table):
    t, _, _ = table
    base = t.snapshot()
    with pytest.raises(WaitTimeout) as info:
        t.wait_change(since=base, timeout=0.1)
    assert info.value.message.startswith("A tela não mudou em 0.1 s.")
    t.move_to(7, 30)
    assert t.wait_change(since=base, timeout=0.5).cursor == Cursor(7, 30)
    assert t.wait_cursor(7, 30).match == Cursor(7, 30)
    with pytest.raises(WaitTimeout) as cursor:
        t.wait_cursor(1, 1, timeout=0.1)
    assert "pelo cursor em (1,1), mas ele está em (7,30)" in cursor.value.message


def test_wait_ready_keyboard_message_and_no_error_checks():
    t, _, fake = make_session(table_screen(), timeout=0.2)
    try:
        fake.sessions["D"].inhibited = True
        with pytest.raises(WaitTimeout) as info:
            t.wait_ready()
        assert info.value.message.startswith("O teclado continua bloqueado após 0.2 s (G8=04 00 00 00 00).")
        assert "t.press(Key.RESET)" in info.value.message
        fake.sessions["D"].inhibited = False
        _write_line(fake, 5, 31, "E R R O   G R A V E")
        assert t.wait_ready().oia.ready                       # wait_ready não roda error_checks (C20)
        with pytest.raises(HostError) as host:
            t.wait_text("VOLVO", 1, 2)
        assert host.value.screen is not None
    finally:
        t.release()


def test_ims_has_no_cics_detector():
    t, _, _ = make_session(scenarios.ims_login(), "A")
    try:
        assert t.error_checks == [] and t.rows == 32
        assert t.wait_text("USS010", 2, 1).match == Cursor(2, 1)
    finally:
        t.release()


def test_press_before_aid_needs_ready_keyboard(table):
    t, _, fake = table
    fake.sessions["D"].inhibited = True
    with pytest.raises(WaitTimeout) as info:
        t.enter(timeout=0.1)
    assert "teclado continua bloqueado" in info.value.message
    assert fake.sessions["D"].aid_log == []                 # nunca envia RESET nem a AID sozinho
    assert t.state == "ok"


# ------------------------------------------------------------------------------------------- erro grave e a AID (A.14)

ERRO = "E R R O   G R A V E"


def _error_host(delay: float = 0.3):
    """D na tela de erro grave do CICS; o host leva ``delay`` s para responder (como o real): CLEAR/PF3 limpam a
    tela, ENTER numa tela limpa leva de volta ao erro."""
    fake = table_screen()
    session = fake.sessions["D"]
    session.response_delay = delay
    _write_line(fake, 5, 31, ERRO)

    def host(s, aid, fields):
        if aid in ("CLEAR", "PF3"):
            s.buffer[:] = b" " * len(s.buffer)
        elif aid == "ENTER":
            start = s.pos(5, 31) - 1
            s.buffer[start:start + len(ERRO)] = ERRO.encode("cp1252")

    session.on_aid = host
    return fake


@pytest.mark.parametrize("leave", ["clear", "pf3", "send_keys"])
def test_aid_from_error_screen_does_not_raise_old_screen(leave):
    fake = _error_host()
    t, _, _ = make_session(fake, timeout=3.0)
    try:
        with pytest.raises(HostError):
            t.wait_text("VOLVO", 1, 2)                     # a tela atual É o erro: espera de conteúdo acusa
        if leave == "clear":
            t.clear()
        elif leave == "pf3":
            t.pf(3)
        else:
            t.send_keys("<Clear>")
        assert t.state == "ok"
        assert t.screen.get(5, 31, len(ERRO)) != ERRO
        assert [aid for aid, _ in fake.sessions["D"].aid_log] == ["PF3" if leave == "pf3" else "CLEAR"]
    finally:
        t.release()


def test_aid_from_clean_screen_to_error_raises_with_the_new_screen():
    fake = _error_host()
    _write_line(fake, 5, 31, " " * len(ERRO))              # começa limpa
    t, _, _ = make_session(fake, timeout=3.0)
    try:
        with pytest.raises(HostError) as info:
            t.enter()
        assert info.value.screen is not None and info.value.screen.get(5, 31, len(ERRO)) == ERRO
        assert [aid for aid, _ in fake.sessions["D"].aid_log] == ["ENTER"]
    finally:
        t.release()


def test_aid_from_error_screen_to_another_error_screen_raises():
    fake = _error_host()
    session = fake.sessions["D"]

    def host(s, aid, fields):                              # o host responde com OUTRA tela de erro
        start = s.pos(6, 1) - 1
        s.buffer[start:start + 6] = b"OUTRO!"

    session.on_aid = host
    t, _, _ = make_session(fake, timeout=3.0)
    try:
        with pytest.raises(HostError) as info:
            t.enter()
        assert info.value.screen is not None and info.value.screen.get(6, 1, 6) == "OUTRO!"
    finally:
        t.release()


def test_compat_send_key_clear_from_error_screen():
    from rumbapy import compat

    fake = _error_host()

    def factory(action, terminal, *, python32=None, timeout=None):
        return make_session(fake, terminal, timeout=3.0)[0]

    import rumbapy.compat as compat_mod
    original = compat_mod._find_python32
    compat_mod._find_python32 = lambda explicit: explicit
    try:
        c = compat.RumbaClient("D", "C:/py32/python.exe", auto_update=False, _session_factory=factory)
        assert c.send_key("CLEAR") is True
        assert c.copy_ps_to_string(5, 31, len(ERRO)) != ERRO
    finally:
        compat_mod._find_python32 = original
        if c._session is not None:
            c._session.release()
