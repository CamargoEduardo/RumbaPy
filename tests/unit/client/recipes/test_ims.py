"""Receita IMS (B.8) com o cenário IMS do fake (``scenarios.ims_login``, 32x80)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import LogonFailed, RecipeNotAvailable, UnexpectedScreen, WaitTimeout
from tests.unit.client.session.support import aids, make_session, send_calls
from tests.fakes import scenarios

import pytest

USER, PASSWORD = scenarios.VALID_USER, "Zq7pw9k"


@pytest.fixture
def ims():
    created = []

    def build(fake=None, timeout: float = 2.0):
        t, backend, fake = make_session(fake or scenarios.ims_login(USER, PASSWORD), "A", timeout=timeout)
        created.append(t)
        return t, backend, fake
    yield build
    for t in created:
        t.release()


def test_logon_rhelp(ims):
    t, backend, fake = ims()
    screen = t.ims.logon(USER, PASSWORD)
    assert "RHELP MAIN MENU" in screen.line(2)
    assert aids(fake, "A") == ["ENTER", "ENTER", "ENTER"]          # USS, credenciais, aplicação
    log = fake.sessions["A"].aid_log
    assert log[0][1]["cmd"] == "IMSRS"
    assert log[1][1]["uid"] == USER and log[1][1]["pwd"] == PASSWORD
    assert log[2][1]["app"] == "RHELP"
    assert PASSWORD not in backend.traffic(replies_only=True)
    assert all(PASSWORD.encode() not in sent for sent in send_calls(fake))


def test_invalid_password_is_logon_failed_with_screen(ims):
    t, _, fake = ims(timeout=0.4)
    with pytest.raises(LogonFailed) as info:
        t.ims.logon(USER, "errada1")
    assert info.value.message == "O logon não terminou: a tela 'Start' não apareceu em 0.4 s."
    assert info.value.screen is not None and "errada1" not in str(info.value.screen)
    assert aids(fake, "A") == ["ENTER", "ENTER"]


def test_password_field_not_hidden_writes_no_password(ims):
    fake = scenarios.ims_login(USER, PASSWORD)
    session = fake.sessions["A"]
    real = session.on_aid

    def host(s, aid, fields):
        real(s, aid, fields)
        for f in s.fields:
            if f.name == "pwd":
                f.hidden = False

    session.on_aid = host
    t, _, _ = ims(fake)
    with pytest.raises(UnexpectedScreen):
        t.ims.logon(USER, PASSWORD)
    assert aids(fake, "A") == ["ENTER"]
    pwd = next(f for f in session.fields if f.name == "pwd")
    assert bytes(session.buffer[pwd.start - 1:pwd.end]) == b" " * 8


def test_not_at_uss_screen_writes_nothing(ims):
    fake = scenarios.ims_login(USER, PASSWORD)
    s = fake.sessions["A"]
    s.buffer[s.pos(2, 1) - 1:s.pos(2, 1) + 5] = b"XXXXXX"
    t, _, _ = ims(fake, timeout=0.2)
    with pytest.raises(WaitTimeout):
        t.ims.logon(USER, PASSWORD)
    assert aids(fake, "A") == [] and send_calls(fake) == []


def test_cics_recipe_not_available_on_ims(ims):
    t, _, _ = ims()
    with pytest.raises(RecipeNotAvailable) as info:
        t.cics
    assert info.value.message == "t.cics só existe em terminais CICS (D, Z)."
