"""T-SAFE-2 consolidado (J.5): nenhuma AID a mais. Para cada API de entrada, o ``aid_log`` do ``FakeEhllapi`` tem
EXATAMENTE as AIDs esperadas, e cada ``WD_SendKey`` leva no máximo uma AID, sempre no fim (tudo depois da 1ª AID seria
descartado pelo RUMBA, e uma AID escondida no meio do texto iria ao host sem a espera da tela).

Cobre ``press``/``enter``/``clear``/``pf``/``pa``, ``send_keys``, ``type``/``write``/``fill``/``move_to``, as receitas
(``cics.logon``, ``cics.start_transaction``, ``ims.logon``) e a compat (``send_key``, ``screen_load``, ``logon_cics``,
``copy_string_to_ps``, ``copy_ps_to_string``, ``copy_field``). ``send_key("A@EB@E")`` = 1 AID; ``PAGE_DOWN`` conta.
Tudo em processo (``InProcessBackend`` + ``FakeEhllapi``); nada toca o RUMBA.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from tests.unit.client.session.support import aids, echo_host, make_session, send_calls, table_screen
from tests.fakes.fake_ehllapi import AID_A_KEYS, AID_KEYS, ENCODING, FakeEhllapi
from rumbapy.errors import RumbaError, UndefinedKey
from rumbapy import session as session_mod
from tests.fakes import scenarios
from rumbapy import compat
from rumbapy.keys import Key

import pytest

USER, PASSWORD = scenarios.VALID_USER, "Zq7pw9k"


def aids_in(raw: bytes) -> list[tuple[int, str]]:
    """AIDs de um corpo de ``WD_SendKey`` como ``(índice do token, nome)``, com o total de tokens no fim."""
    text = raw.decode(ENCODING)
    found: list[tuple[int, str]] = []
    i = token = 0
    while i < len(text):
        if text[i] == "@" and i + 1 < len(text):
            code = text[i + 1]
            if code == "A" and i + 3 < len(text) and text[i + 2] == "@":
                if text[i + 3] in AID_A_KEYS:
                    found.append((token, AID_A_KEYS[text[i + 3]]))
                i += 4
            else:
                if code in AID_KEYS:
                    found.append((token, AID_KEYS[code]))
                i += 2
        else:
            i += 1
        token += 1
    found.append((token, ""))
    return found


def assert_one_aid_per_call_at_the_end(fake: FakeEhllapi) -> None:
    for raw in send_calls(fake):
        *found, (total, _) = aids_in(raw)
        assert len(found) <= 1, "WD_SendKey com mais de uma AID"
        if found:
            assert found[0][0] == total - 1, "AID no meio de um WD_SendKey (o resto seria descartado)"


def test_aid_parser_used_by_the_invariant() -> None:
    assert aids_in(b"abc@E") == [(3, "ENTER"), (4, "")]
    assert aids_in(b"A@EB@E") == [(1, "ENTER"), (3, "ENTER"), (4, "")]      # seria recusado pelo invariante
    assert aids_in(b"a@@b") == [(3, "")]                                     # '@@' é texto
    assert aids_in(b"@A@Hx@T") == [(0, "SYSREQ"), (3, "")]
    assert aids_in(b"@v") == [(0, "PAGEDOWN"), (1, "")]


@pytest.fixture
def table() -> Iterator[tuple[Any, FakeEhllapi]]:
    """D com campos variados; o "host" muda a tela a cada AID (resposta imediata)."""
    fake = table_screen()
    echo_host(fake)
    t, _, _ = make_session(fake, timeout=2.0)
    yield t, fake
    assert_one_aid_per_call_at_the_end(fake)
    t.release()


SESSION_CASES: list[tuple[str, Callable[[Any], Any], list[str]]] = [
    ("press_enter", lambda t: t.press("ENTER"), ["ENTER"]),
    ("press_key_enter", lambda t: t.press(Key.ENTER), ["ENTER"]),
    ("press_mnemonic_pf8", lambda t: t.press("@8"), ["PF8"]),
    ("press_page_down", lambda t: t.press(Key.PAGE_DOWN), ["PAGEDOWN"]),
    ("press_page_up", lambda t: t.press("PAGE_UP"), ["PAGEUP"]),
    ("press_tab_is_local", lambda t: t.press(Key.TAB), []),
    ("press_home_is_local", lambda t: t.press("HOME"), []),
    ("enter", lambda t: t.enter(), ["ENTER"]),
    ("clear", lambda t: t.clear(), ["CLEAR"]),
    ("pf3", lambda t: t.pf(3), ["PF3"]),
    ("pf24", lambda t: t.pf(24), ["PF24"]),
    ("pa1", lambda t: t.pa(1), ["PA1"]),
    ("pa3", lambda t: t.pa(3), ["PA3"]),
    ("send_keys_text_only", lambda t: t.send_keys("<Home>abc"), []),
    ("send_keys_two_enters", lambda t: t.send_keys("<Home>a<Enter>b<Enter>"), ["ENTER", "ENTER"]),
    ("send_keys_page_down", lambda t: t.send_keys("<Home>A<PageDown>"), ["PAGEDOWN"]),
    ("send_keys_tab_then_pf3", lambda t: t.send_keys("<Home>x<Tab>y<PF3>"), ["PF3"]),
    ("send_keys_secret", lambda t: t.send_keys("<Home>Zq7<Enter>", secret=True), ["ENTER"]),
    ("send_keys_literal_lt", lambda t: t.send_keys("<Home>a<<b"), []),
    ("send_keys_at_is_text", lambda t: t.send_keys("<Home>a@Eb"), []),
    ("type", lambda t: t.move_to(3, 14).type("abc"), []),
    ("write", lambda t: t.write(3, 14, "abc"), []),
    ("fill", lambda t: t.fill(3, 14, "abc"), []),
    ("move_to", lambda t: t.move_to(7, 30), []),
]


@pytest.mark.parametrize(("action", "expected"), [(c[1], c[2]) for c in SESSION_CASES],
                         ids=[c[0] for c in SESSION_CASES])
def test_session_input_sends_exactly_the_expected_aids(table: tuple[Any, FakeEhllapi],
                                                       action: Callable[[Any], Any], expected: list[str]) -> None:
    t, fake = table
    action(t)
    assert aids(fake) == expected


def test_invalid_key_after_an_aid_sends_nothing(table: tuple[Any, FakeEhllapi]) -> None:
    """``send_keys`` valida e codifica TUDO antes do 1º envio: uma tecla inválida depois de ``<Enter>`` não deixa
    nenhuma AID (nem texto) sair."""
    t, fake = table
    with pytest.raises(UndefinedKey):
        t.send_keys("<Home>a<Enter>b<Entr>")
    assert aids(fake) == [] and send_calls(fake) == []


def test_invalid_pa_and_pf_send_nothing(table: tuple[Any, FakeEhllapi]) -> None:
    t, fake = table
    for call in (lambda: t.pa(4), lambda: t.pf(25), lambda: t.pf(0), lambda: t.press("NAO_EXISTE")):
        with pytest.raises(RumbaError):
            call()
    assert aids(fake) == [] and send_calls(fake) == []


# ------------------------------------------------------------------------------------------- receitas

def test_cics_logon_from_the_menu_is_two_enters() -> None:
    t, _, fake = make_session(scenarios.cics_login(USER, PASSWORD), timeout=2.0)
    try:
        t.cics.logon(USER, PASSWORD)
        assert aids(fake) == ["ENTER", "ENTER"]
        echo_host(fake)                                            # o "host" responde à transação
        t.cics.start_transaction("SP01")
        assert aids(fake) == ["ENTER", "ENTER", "ENTER"]
        assert_one_aid_per_call_at_the_end(fake)
    finally:
        t.release()


def test_cics_logon_with_wrong_password_stops_after_the_signon_enter() -> None:
    t, _, fake = make_session(scenarios.cics_login(USER, PASSWORD), timeout=2.0)
    try:
        with pytest.raises(RumbaError):
            t.cics.logon(USER, "errada1")
        assert aids(fake) == ["ENTER", "ENTER"]                   # nenhuma nova tentativa automática
    finally:
        t.release()


def test_ims_logon_is_three_enters() -> None:
    t, _, fake = make_session(scenarios.ims_login(USER, PASSWORD), "A", timeout=2.0)
    try:
        t.ims.logon(USER, PASSWORD)
        assert aids(fake, "A") == ["ENTER", "ENTER", "ENTER"]      # USS, Welcome (credenciais), Start (aplicação)
        assert_one_aid_per_call_at_the_end(fake)
    finally:
        t.release()


# ------------------------------------------------------------------------------------------- compat (v0.1.5)

class Factory:
    """``_session_factory`` da compat: cada chamada cria uma ``Session`` sobre o MESMO ``FakeEhllapi``."""

    def __init__(self, fake: FakeEhllapi) -> None:
        self.fake = fake
        self.sessions: list[Any] = []

    def __call__(self, action: str, terminal: str, *, python32: Any = None, timeout: Any = None) -> Any:
        t, _, _ = make_session(self.fake, terminal, timeout=1.0)
        self.sessions.append(t)
        return t


@pytest.fixture
def legacy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[FakeEhllapi], Any]]:
    monkeypatch.setattr(compat, "_find_python32", lambda explicit: Path("C:/py32/python.exe"))
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    factories: list[Factory] = []

    def build(fake: FakeEhllapi) -> Any:
        factory = Factory(fake)
        factories.append(factory)
        return compat.RumbaClient("D", _session_factory=factory)

    yield build
    for factory in factories:
        assert_one_aid_per_call_at_the_end(factory.fake)
        for t in factory.sessions:
            t.release()


def echo_table() -> FakeEhllapi:
    fake = table_screen()
    echo_host(fake)
    return fake


COMPAT_CASES: list[tuple[str, Callable[[Any], Any], list[str]]] = [
    ("send_key_cut_after_first_aid", lambda c: c.send_key("A@EB@E"), ["ENTER"]),
    ("send_key_name_enter", lambda c: c.send_key("ENTER"), ["ENTER"]),
    ("send_key_name_f3", lambda c: c.send_key("F3"), ["PF3"]),
    ("send_key_page_down", lambda c: c.send_key("PAGEDOWN"), ["PAGEDOWN"]),
    ("send_key_mnemonic_page_down", lambda c: c.send_key("@v"), ["PAGEDOWN"]),
    ("send_key_mnemonic_pa2", lambda c: c.send_key("@y"), ["PA2"]),
    ("send_key_text_only", lambda c: c.send_key("ABC"), []),
    ("send_key_tab_only", lambda c: c.send_key("@T"), []),
    ("send_key_two_aids", lambda c: c.send_key("@E@E"), ["ENTER"]),
    ("screen_load", lambda c: c.screen_load(1, 2, "VOLVO"), []),
    ("screen_load_gone", lambda c: c.screen_load(1, 2, "NADA", text_diff=True), []),
    ("copy_string_to_ps", lambda c: c.copy_string_to_ps(3, 14, "abc"), []),
    ("copy_ps_to_string", lambda c: c.copy_ps_to_string(1, 2, 13), []),
    ("copy_field", lambda c: c.copy_field(5, 10), []),
]


@pytest.mark.parametrize(("action", "expected"), [(c[1], c[2]) for c in COMPAT_CASES],
                         ids=[c[0] for c in COMPAT_CASES])
def test_compat_sends_exactly_the_expected_aids(legacy: Callable[[FakeEhllapi], Any],
                                                action: Callable[[Any], Any], expected: list[str]) -> None:
    fake = echo_table()
    client = legacy(fake)
    action(client)
    assert aids(fake) == expected


def test_compat_logon_cics_is_two_enters(legacy: Callable[[FakeEhllapi], Any]) -> None:
    fake = scenarios.cics_login(USER, PASSWORD)
    client = legacy(fake)
    assert client.logon_cics(USER, PASSWORD) is True
    assert client.screen_load(5, 20, "Signon") is True
    assert aids(fake) == ["ENTER", "ENTER"]


# ------------------------------------------------------------------------------------------- RESET só a pedido (G.3)

def _has_reset(raw: bytes) -> bool:
    return b"@R" in raw.replace(b"@@", b"")                # '@@' é '@' literal (texto)


@pytest.mark.parametrize("action", [c[1] for c in SESSION_CASES], ids=[c[0] for c in SESSION_CASES])
def test_no_input_api_sends_reset_on_its_own(action: Callable[[Any], Any]) -> None:
    """Teclado bloqueado ("X -f"): nenhuma API de entrada manda RESET sozinha (Q4/K-R9) — falha e espera o script."""
    fake = table_screen()
    echo_host(fake)
    t, _, _ = make_session(fake, timeout=0.1)
    try:
        fake.sessions["D"].inhibited = True
        try:
            action(t)
        except RumbaError:
            pass
        assert not any(_has_reset(raw) for raw in send_calls(fake))
        assert aids(fake) == [] and fake.sessions["D"].inhibited is True
    finally:
        t.release()


@pytest.mark.parametrize("action", [lambda t: t.press(Key.RESET), lambda t: t.send_keys("<Reset>"),
                                    lambda t: t.send_keys("<Reset><Reset>")],
                         ids=["press", "send_keys", "send_keys_twice"])
def test_explicit_reset_is_exactly_one_reset_and_no_aid(action: Callable[[Any], Any]) -> None:
    fake = table_screen()
    t, _, _ = make_session(fake, timeout=0.5)
    try:
        fake.sessions["D"].inhibited = True
        action(t)
        assert send_calls(fake) == [b"@R"] and aids(fake) == []
        assert fake.sessions["D"].inhibited is False
        assert_one_aid_per_call_at_the_end(fake)
    finally:
        t.release()


def test_compat_send_key_reset_unlocks_without_the_120_s_wait(legacy: Callable[[FakeEhllapi], Any]) -> None:
    fake = echo_table()
    client = legacy(fake)
    client.copy_ps_to_string(1, 2, 5)                        # anexa (antes do "X -f")
    fake.sessions["D"].inhibited = True
    assert client.send_key("RESET") is True
    assert send_calls(fake) == [b"@R"] and aids(fake) == [] and fake.sessions["D"].inhibited is False
    fake.sessions["D"].inhibited = True
    assert client.send_key("@R") is True
    assert fake.sessions["D"].inhibited is False and aids(fake) == []
