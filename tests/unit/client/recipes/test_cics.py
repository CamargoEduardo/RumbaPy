"""Receita CICS (B.8): logon feliz/expirada/inválida, tela estranha, senha não oculta, falha antes do ENTER."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import (HostError, InvalidPassword, PasswordExpired, RecipeNotAvailable, Truncated,
                            UnexpectedScreen, WaitTimeout)
from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, make_cics_login_host
from tests.unit.client.session.support import aids, make_session, send_calls, table_screen
from tests.fakes import scenarios

import pytest

USER, PASSWORD = scenarios.VALID_USER, "Zq7pw9k"      # senha distinta de qualquer palavra das mensagens


def _copies(fake: FakeEhllapi) -> int:
    return [name for name, _ in fake.calls].count("WD_CopyStringToPS")


def _pwd_buffer(fake: FakeEhllapi) -> bytes:
    s = fake.sessions["D"]
    field = next(f for f in s.fields if f.name == "pwd")
    return bytes(s.buffer[field.start - 1:field.end])


def at_signon(*, hidden: bool = True, expired: bool = False) -> FakeEhllapi:
    """D já na tela de signon (sem passar pelo menu de ambientes)."""
    fake = FakeEhllapi()
    session = fake.add_session("D", hwnd=scenarios.HWND_D, pid=scenarios.PID_D)
    env, host = make_cics_login_host(USER, PASSWORD, expired)
    session.load(env)
    session.screen_name = "env"
    host(session, "ENTER", {"env": "C"})
    session.on_aid = host
    if not hidden:
        next(f for f in session.fields if f.name == "pwd").hidden = False
    return fake


@pytest.fixture
def cics():
    created = []

    def build(fake=None, **kw):
        t, backend, fake = make_session(fake or scenarios.cics_login(USER, PASSWORD),
                                        timeout=kw.pop("timeout", 2.0))
        created.append(t)
        return t, backend, fake
    yield build
    for t in created:
        t.release()


def test_logon_from_menu(cics):
    t, backend, fake = cics()
    screen = t.cics.logon(USER, PASSWORD)
    assert screen.match == "ok" and screen.get(5, 20, 6) == "Signon"
    assert aids(fake) == ["ENTER", "ENTER"]                     # T-SAFE-2: só o necessário ao logon
    log = fake.sessions["D"].aid_log
    assert log[0][1] == {"env": "C"}
    assert log[1][1]["uid"] == USER and log[1][1]["pwd"] == PASSWORD
    assert PASSWORD not in backend.traffic(replies_only=True)
    assert all(PASSWORD.encode() not in sent for sent in send_calls(fake))
    assert t.state == "ok"


def test_logon_from_signon(cics):
    t, _, fake = cics(at_signon())
    t.cics.logon(USER, PASSWORD)
    assert aids(fake) == ["ENTER"]


def test_password_expired(cics):
    t, _, fake = cics(at_signon(expired=True))
    with pytest.raises(PasswordExpired) as info:
        t.cics.logon(USER, PASSWORD)
    assert info.value.message == "A senha do CICS está expirada. Troque-a manualmente no terminal."
    assert PASSWORD not in str(info.value)


def test_invalid_password(cics):
    t, _, fake = cics()
    with pytest.raises(InvalidPassword):
        t.cics.logon(USER, "errada")
    assert aids(fake) == ["ENTER", "ENTER"]


def test_errors_are_tested_before_ok(cics):
    fake = at_signon()
    session = fake.sessions["D"]
    both = FakeScreen(name="both", lines=["", "", "", "", " " * 19 + "Signon is complete"] + [""] * 13
                      + [" " * 18 + "SENHA EXPIRADA"] + [""] * 5)
    session.on_aid = lambda s, aid, fields: s.load(both)
    t, _, _ = cics(fake)
    with pytest.raises(PasswordExpired):
        t.cics.logon(USER, PASSWORD)


def test_unexpected_screen_writes_nothing(cics):
    t, _, fake = cics(table_screen())
    with pytest.raises(UnexpectedScreen) as info:
        t.cics.logon(USER, PASSWORD)
    assert "linha 1 atual é: ' VOLVO SP0001T'" in info.value.message
    assert info.value.hint == "use rumbapy.open('D') para começar de uma tela limpa."
    assert _copies(fake) == 0 and send_calls(fake) == [] and aids(fake) == []


def test_password_field_not_hidden_writes_nothing(cics):
    t, _, fake = cics(at_signon(hidden=False))
    with pytest.raises(UnexpectedScreen):
        t.cics.logon(USER, PASSWORD)
    assert _copies(fake) == 0 and aids(fake) == []


def test_menu_without_signon_after_enter(cics):
    fake = scenarios.cics_login(USER, PASSWORD)
    session = fake.sessions["D"]
    real = session.on_aid

    def host(s, aid, fields):
        real(s, aid, fields)
        next(f for f in s.fields if f.name == "pwd").hidden = False

    session.on_aid = host
    t, _, _ = cics(fake)
    with pytest.raises(UnexpectedScreen) as info:
        t.cics.logon(USER, PASSWORD)
    assert "oculto" in info.value.message
    assert aids(fake) == ["ENTER"]                              # só o ENTER do ambiente


def test_failure_before_enter_erases_password(cics, monkeypatch):
    t, _, fake = cics(at_signon())

    def stuck(limit):
        raise WaitTimeout("O teclado continua bloqueado.", screen=t.snapshot(), terminal="D")

    monkeypatch.setattr(t, "_ready_stable", stuck)
    with pytest.raises(WaitTimeout) as info:
        t.cics.logon(USER, PASSWORD)
    assert info.value.screen is None                            # I15
    assert aids(fake) == []
    assert _pwd_buffer(fake) == b" " * 8                        # a senha foi apagada do PS local


def test_password_too_long_never_written(cics):
    t, _, fake = cics(at_signon())
    with pytest.raises(Truncated) as info:
        t.cics.logon(USER, PASSWORD * 3)
    assert PASSWORD not in str(info.value) and info.value.screen is None
    assert aids(fake) == [] and _pwd_buffer(fake) == b" " * 8


def test_no_answer_after_password_has_no_screen(cics):
    fake = at_signon()
    fake.sessions["D"].on_aid = None                            # host mudo
    t, _, _ = cics(fake, timeout=0.3)
    with pytest.raises(WaitTimeout) as info:
        t.cics.logon(USER, PASSWORD)
    assert info.value.screen is None and t.state == "uncertain"
    assert aids(fake) == ["ENTER"]


def test_start_transaction_and_check(cics):
    fake = scenarios.cics_login(USER, PASSWORD)
    session = fake.sessions["D"]
    real = session.on_aid

    def host(s, aid, fields):
        if s.screen_name == "menu" and fields.get("cmd") == "SPRE":
            s.load(FakeScreen(name="spre", lines=[" VOLVO SPRE"] + [""] * 23))
        else:
            real(s, aid, fields)

    session.on_aid = host
    t, _, _ = cics(fake)
    t.cics.logon(USER, PASSWORD)
    screen = t.cics.start_transaction("SPRE", wait_for=("VOLVO", 1, 2))
    assert screen.match == (1, 2)
    assert aids(fake) == ["ENTER", "ENTER", "ENTER"]
    t.cics.check(screen)
    s = fake.sessions["D"]
    s.buffer[s.pos(5, 31) - 1:s.pos(5, 31) - 1 + 19] = b"E R R O   G R A V E"
    with pytest.raises(HostError):
        t.cics.check(t.snapshot())


def test_recipes_by_kind(cics):
    t, _, _ = cics()
    with pytest.raises(RecipeNotAvailable) as info:
        t.ims
    assert info.value.message == "t.ims só existe em terminais IMS (A)."
    assert t.cics is t.cics
