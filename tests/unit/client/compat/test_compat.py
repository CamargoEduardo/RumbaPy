"""Compatibilidade v0.1.5 (B.9.1/B.9.2): cada método do ``RumbaClient`` sobre a ``Session`` com o fake."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import (InvalidPassword, PasswordExpired, ProtectedField, RecipeNotAvailable, TerminalNotOpen,
                            UnknownTerminal, UpdateRequired, KeyboardLocked)
from tests.unit.client.session.support import FakeLauncher, aids, echo_host, make_session
from tests.unit.client.session.support import table_screen as _table_screen
from tests.fakes.fake_ehllapi import FakeEhllapi
from rumbapy import session as session_mod
from tests.fakes import scenarios
from rumbapy import compat

from pathlib import Path

import logging

import pytest

USER, PASSWORD = scenarios.VALID_USER, "Zq7pw9k"
PY32 = Path("C:/py32/python.exe")


class Factory:
    """``_session_factory`` dos testes: cada chamada cria uma ``Session`` sobre o MESMO ``FakeEhllapi``."""

    def __init__(self, fake: FakeEhllapi, letter: str = "D") -> None:
        self.fake, self.letter = fake, letter
        self.calls: list[tuple] = []
        self.sessions: list = []

    def __call__(self, action: str, terminal: str, *, python32=None, timeout=None):
        self.calls.append((action, terminal, timeout, python32))
        if terminal not in self.fake.sessions:
            raise TerminalNotOpen.make(L=terminal, terminal=terminal, rc=1)
        t, _, _ = make_session(self.fake, terminal, timeout=1.0)
        self.sessions.append(t)
        return t


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    updates: list[str] = []
    monkeypatch.setattr(compat, "_find_python32", lambda explicit: Path(explicit) if explicit else PY32)
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: updates.append("open"))
    launcher = FakeLauncher(scenarios.cics_login())
    session_mod._set_factories(backend_factory=lambda p, py: None, user32_factory=lambda: None,
                               launcher_factory=lambda u, g: launcher)
    yield {"updates": updates, "launcher": launcher}
    session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


def table_screen() -> FakeEhllapi:
    fake = _table_screen()
    echo_host(fake)                          # cada AID muda a tela (o host "responde")
    return fake


def client(fake=None, letter="D", **kw):
    factory = Factory(fake or (scenarios.ims_login(USER, PASSWORD) if letter == "A"
                               else scenarios.cics_login(USER, PASSWORD)), letter)
    return compat.RumbaClient(letter, _session_factory=factory, **kw), factory


# ------------------------------------------------------------------------------------------- construtor

def test_constructor_attributes_and_nothing_started(isolated):
    c, factory = client()
    assert c.terminal_type == "D" and c.python_32bit == PY32 and c.HOST == "localhost" and c.PORT == 8500
    assert c.session_id.startswith("D_") and len(c.session_id) == 34 and c.connected is False
    assert factory.calls == [] and isolated["updates"] == ["open"]
    assert compat.RumbaClient("A", "C:/x/python.exe", _session_factory=factory).PORT == 8600
    assert compat.RumbaClient("Z", _session_factory=factory).PORT == 8700
    assert compat.RumbaClient("A", "C:/x/python.exe", _session_factory=factory).python_32bit == Path("C:/x/python.exe")


def test_empty_python32_path_means_discovery(monkeypatch):
    seen = []
    monkeypatch.setattr(compat, "_find_python32", lambda explicit: seen.append(explicit) or PY32)
    _, factory = client()
    for empty in ("", None):                                 # v0.1.5: `Path(p) if p else find_python_32bit()`
        assert compat.RumbaClient("D", empty, _session_factory=factory).python_32bit == PY32
    assert seen == [None, None, None]


def test_invalid_letter_fails_immediately():
    with pytest.raises(UnknownTerminal) as info:
        compat.RumbaClient("Q")
    assert isinstance(info.value, ValueError) and isinstance(info.value, RuntimeError)


def test_auto_update_false_needs_dev(isolated, monkeypatch, caplog):
    monkeypatch.delenv("RUMBAPY_DEV", raising=False)
    with caplog.at_level(logging.WARNING, logger="rumbapy.compat"):
        client(auto_update=False)
    assert "auto_update=False ignorado" in caplog.text
    assert isolated["updates"] == ["open"]
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    client(auto_update=False)
    assert isolated["updates"] == ["open"]                  # com RUMBAPY_DEV=1 não consulta


# ------------------------------------------------------------------------------------------- logon

def test_logon_cics(isolated):
    c, factory = client()
    assert c.logon_cics(USER, PASSWORD) is True
    assert c.connected and factory.calls[0][:2] == ("open", "D") and factory.calls[0][3] == PY32
    assert aids(factory.fake) == ["ENTER", "ENTER"]


@pytest.mark.parametrize(("expired", "password", "cls", "legacy"), [
    (True, PASSWORD, PasswordExpired, "SENHA DO CICS ESTÁ EXPIRADA"),
    (False, "errada1", InvalidPassword, "SENHA DO CICS ESTÁ INCORRETA"),
])
def test_logon_cics_legacy_texts(expired, password, cls, legacy):
    c, _ = client(scenarios.cics_login(USER, PASSWORD, expired=expired))
    with pytest.raises(cls) as info:
        c.logon_cics(USER, password)
    assert c.connected is False                              # como a v0.1.5: só fica True depois do logon
    assert info.value.message.startswith(f"Comando 'logon_cics' falhou: {legacy}. ")
    assert str(info.value).startswith(f"[D] Comando 'logon_cics' falhou: {legacy}")
    assert info.value.args == (info.value.message,)
    assert isinstance(info.value, RuntimeError)


def test_logon_cics_only_cics_profiles():
    c, factory = client(letter="A")
    with pytest.raises(RecipeNotAvailable) as info:
        c.logon_cics(USER, PASSWORD)
    assert info.value.message == "Comando 'logon_cics' falhou: t.cics só existe em terminais CICS (D, Z)."
    assert factory.calls == []                               # não abriu (nem fechou) nada


def test_logon_rhelp_only_ims():
    c, factory = client(letter="A")
    assert c.logon_rhelp(USER, PASSWORD) is True
    assert aids(factory.fake, "A") == ["ENTER", "ENTER", "ENTER"]
    d, dfactory = client()
    with pytest.raises(RecipeNotAvailable):
        d.logon_rhelp(USER, PASSWORD)
    assert dfactory.calls == []


# ------------------------------------------------------------------------------------------- teclas

def test_send_key_cuts_after_first_aid(caplog):
    c, factory = client(table_screen())
    with caplog.at_level(logging.WARNING, logger="rumbapy.compat"):
        assert c.send_key("A@EB@E") is True
    assert compat.SEND_KEY_CUT in caplog.text
    assert len(factory.fake.sessions["D"].aid_log) == 1        # T-SAFE-2: 1 AID
    assert factory.calls[0][:3] == ("attach", "D", 10.0)       # anexou sozinho (uso preguiçoso)


@pytest.mark.parametrize(("key", "aid"), [("ENTER", "ENTER"), ("enter", "ENTER"), ("F3", "PF3"),
                                          ("PAGEDOWN", "PAGEDOWN"), ("@8", "PF8"), ("CLEAR", "CLEAR")])
def test_send_key_names_and_mnemonics(key, aid):
    c, factory = client(table_screen())
    c.send_key(key)
    assert [a for a, _ in factory.fake.sessions["D"].aid_log] == [aid]


def test_send_key_local_and_text():
    c, factory = client(table_screen())
    c.send_key("TAB")
    c.send_key("PN9")
    assert factory.fake.sessions["D"].aid_log == []
    assert c.copy_ps_to_string(7, 30, 3) == "PN9"


def test_send_key_errors_raise_with_prefix():
    fake = table_screen()
    fake.rc_override["WD_SendKey"] = [5]
    c, _ = client(fake)
    with pytest.raises(KeyboardLocked) as info:
        c.send_key("TAB")
    assert info.value.message.startswith("Comando 'send_key' falhou: O RUMBA recusou as teclas")


# ------------------------------------------------------------------------------------------- tela

def test_copy_ps_to_string_cuts_at_nul():
    fake = table_screen()
    s = fake.sessions["D"]
    s.buffer[s.pos(1, 4) - 1] = 0
    c, _ = client(fake)
    assert c.copy_ps_to_string(1, 2, 5) == "VO"
    assert c.copy_ps_to_string(1, 8, 7) == "SP0001T"


def test_copy_field_strips_only_trailing_nul():
    fake = table_screen()
    s = fake.sessions["D"]
    start = s.pos(3, 14) - 1
    s.buffer[start:start + 8] = b"A B" + b"\0" * 5
    c, _ = client(fake)
    assert c.copy_field(3, 14) == "A B"
    assert c.copy_field(5, 12) == "TITULO".ljust(20)


def test_copy_string_to_ps():
    c, _ = client(table_screen())
    assert c.copy_string_to_ps(3, 14, "PN1") is True
    assert c.copy_ps_to_string(3, 14, 3) == "PN1"
    with pytest.raises(ProtectedField) as info:
        c.copy_string_to_ps(5, 12, "X")
    assert info.value.message.startswith("Comando 'copy_string_to_ps' falhou: (5,12)")


def test_copy_string_to_ps_empty_is_noop():
    c, factory = client(table_screen())
    assert c.copy_string_to_ps(3, 14, "") is True            # v0.1.5: célula vazia não interrompe o lote
    assert factory.calls == []                               # nem anexou
    c.copy_ps_to_string(1, 1, 5)
    before = [name for name, _ in factory.fake.calls].count("WD_CopyStringToPS")
    assert c.copy_string_to_ps(3, 14, "") is True
    assert [name for name, _ in factory.fake.calls].count("WD_CopyStringToPS") == before


def test_screen_load(monkeypatch):
    c, factory = client(table_screen())
    assert c.screen_load(1, 2, "VOLVO") is True
    assert c.screen_load(1, 2, "NADA", text_diff=True) is True
    seen = {}
    t = factory.sessions[0]
    monkeypatch.setattr(t, "_wait_text", lambda *a, **kw: seen.update(args=a, kw=kw))
    c.screen_load(4, 6, "TERMINAL", True)
    assert seen == {"args": ("TERMINAL", 4, 6), "kw": {"timeout": 120.0, "gone": True, "cond_first": True}}


def test_screen_load_error_grave_only_in_cics():
    fake = table_screen()
    s = fake.sessions["D"]
    s.buffer[s.pos(5, 31) - 1:s.pos(5, 31) + 18] = b"E R R O   G R A V E"
    c, _ = client(fake)
    from rumbapy.errors import HostError

    assert c.screen_load(1, 2, "VOLVO") is True             # v0.1.5: a condição vale antes do erro grave
    assert c.screen_load(5, 31, "E R R O   G R A V E") is True
    with pytest.raises(HostError) as info:                  # condição falsa: o detector acusa na hora
        c.screen_load(1, 2, "NADA!")
    assert info.value.message.startswith("Comando 'screen_load' falhou: O CICS mostrou")
    with pytest.raises(HostError):
        c.screen_load(1, 2, "VOLVO", text_diff=True)


# ------------------------------------------------------------------------------------------- ciclo de vida

def test_close_terminal(isolated):
    c, factory = client()
    assert c.close_terminal() is True                       # sem sessão: rumbapy.close(L)
    assert isolated["launcher"].closed == 1 and c.connected is False
    c.copy_ps_to_string(1, 1, 5)
    assert c.close_terminal() is True
    assert factory.sessions[0].state == "closed" and isolated["launcher"].closed == 2


def test_close_terminal_with_replaced_session(isolated):
    c, factory = client()
    c.copy_ps_to_string(1, 1, 5)
    assert c.connected
    factory.sessions[0]._set_replaced()                      # outra open() reabriu o terminal
    assert c.connected is False
    assert c.close_terminal() is True                        # v0.1.5: fecha as janelas do perfil mesmo assim
    assert isolated["launcher"].closed == 1 and c.connected is False
    c.copy_ps_to_string(1, 1, 5)                             # a próxima op anexa de novo
    assert [call[0] for call in factory.calls] == ["attach", "attach"] and c.connected


def test_disconnect_and_connect():
    c, factory = client(table_screen())
    assert c.disconnect() is True                           # sem sessão: nada a fazer
    assert factory.calls == []
    assert c.connect() is True
    assert factory.calls[-1][:3] == ("attach", "D", 60.0) and c.connected
    assert c.disconnect() is True and c.connected is False
    assert factory.fake.connected is None
    assert c.connect() is True and factory.fake.connected == "D"
    assert len(factory.calls) == 1                          # reconectou a mesma sessão


def test_connect_without_terminal():
    fake = table_screen()
    fake.remove_session("D")
    c, _ = client(fake)
    with pytest.raises(TerminalNotOpen) as info:
        c.connect()
    assert info.value.message == "Comando 'connect' falhou: O terminal D não está aberto."
    assert c.connected is False


# ------------------------------------------------------------------------------------------- funções legadas

def test_update_python_64bit_forces_and_never_reexecutes(monkeypatch):
    from rumbapy import _update, client as legacy

    calls = []

    def ensure_latest(context="import", force=False):
        calls.append((context, force))
        raise UpdateRequired.make(versao="2.0.1", sha7="abcdef0", acao="Este processo ainda usa a versão antiga; "
                                                                      "reinicie-o.")

    monkeypatch.setattr(_update, "ensure_latest", ensure_latest)
    for fn in (compat.update_python_64bit, legacy.update_python_64bit, compat.RumbaClient.update_python_64bit):
        with pytest.raises(UpdateRequired):
            fn()
    assert calls == [("legacy", True)] * 3


def test_python32_helpers_only_validate(monkeypatch, caplog):
    from rumbapy import _runtime32, client as legacy

    seen = []

    def find(explicit=None):
        seen.append(explicit)
        return _runtime32.Runtime32("C:/py32/python.exe", "C:/py32/python.exe", (3, 13, 2))

    monkeypatch.setattr(_runtime32, "find", find)
    assert legacy.find_python_32bit() == Path("C:/py32/python.exe")
    with caplog.at_level(logging.INFO, logger="rumbapy.compat"):
        assert legacy.ensure_python_32bit("C:/py32/python.exe") is None
        assert legacy.update_python_32bit("C:/py32/python.exe") is None
    assert seen == [None, "C:/py32/python.exe", "C:/py32/python.exe"]
    assert "nada a instalar" in caplog.text


def test_legacy_module_exports():
    import rumbapy
    from rumbapy import client as legacy

    assert legacy.RumbaClient is compat.RumbaClient is rumbapy.RumbaClient
    assert legacy.RUMBAPY_REPO == "https://github.com/CamargoEduardo/RumbaPy.git"
    assert "ENTER" in legacy.Mnemonic.__args__ and "INSERT" in legacy.Mnemonic.__args__
    assert rumbapy.client is legacy


# ------------------------------------------------------------------------------------------- integração (correções)

def test_send_key_and_read_use_the_compat_deadline_not_session_timeout(monkeypatch):
    """v0.1.5 esperava 60-120 s por uma transação lenta: o prazo da compat não é o Session.timeout (1 s aqui)."""
    monkeypatch.setattr(compat, "COMPAT_AID_TIMEOUT", 6.0)
    c, factory = client(scenarios.slow_host(1.6))
    assert c.copy_string_to_ps(22, 56, "C") is True
    assert factory.sessions[0].timeout == 1.0
    assert c.send_key("ENTER") is True
    assert c.screen_load(4, 6, "TERMINAL") is True
    assert aids(factory.fake) == ["ENTER"]
    factory.fake.sessions["D"].lock_for(1.6)               # X SYSTEM mais longo que o Session.timeout
    assert c.copy_ps_to_string(4, 6, 8) == "TERMINAL"


def test_empty_read_and_empty_screen_load_like_v015():
    from rumbapy.errors import ParameterError

    c, factory = client(table_screen())
    assert c.copy_ps_to_string(1, 1, 0) == "" and factory.calls == []
    assert c.screen_load(1, 2, "") is True and factory.calls == []
    with pytest.raises(ParameterError) as info:
        c.screen_load(1, 2, "", text_diff=True)
    assert info.value.message.startswith("Comando 'screen_load' falhou: screen_load com texto vazio")


def _evicted(session, op: str):
    from rumbapy.errors import SessionReplaced

    def fail(*a, **kw):
        raise SessionReplaced.make(L="D", terminal="D")

    setattr(session._backend, op, fail)


def test_connect_after_evict_attaches_in_one_call():
    c, factory = client(table_screen())
    c.copy_ps_to_string(1, 1, 5)
    _evicted(factory.sessions[0], "connect")                # outro script reabriu: o worker responde SessionReplaced
    assert c.connect() is True
    assert [call[:3] for call in factory.calls] == [("attach", "D", 10.0), ("attach", "D", 60.0)]
    assert factory.sessions[0].state == "replaced" and c.connected


def test_connect_with_other_hwnd_attaches():
    import dataclasses

    c, factory = client(table_screen())
    c.copy_ps_to_string(1, 1, 5)
    backend = factory.sessions[0]._backend
    real = backend.connect
    backend.connect = lambda: dataclasses.replace(real(), hwnd=0x0DEAD0)
    assert c.connect() is True
    assert factory.calls[-1][:3] == ("attach", "D", 60.0)


def test_disconnect_after_evict_returns_true():
    c, factory = client(table_screen())
    c.copy_ps_to_string(1, 1, 5)
    _evicted(factory.sessions[0], "disconnect")
    assert c.disconnect() is True and c.connected is False
    assert c.copy_ps_to_string(1, 2, 5) == "VOLVO"          # sem sessão: a próxima op anexa de novo
    assert [call[0] for call in factory.calls] == ["attach", "attach"]


def test_close_terminal_when_the_close_probe_finds_a_new_instance(isolated):
    c, factory = client(table_screen())
    c.copy_ps_to_string(1, 1, 5)
    _evicted(factory.sessions[0], "status")                 # a sonda do close descobre a substituição
    assert c.close_terminal() is True                       # v0.1.5: fecha as janelas do perfil mesmo assim
    assert isolated["launcher"].closed == 1 and c.connected is False


def test_compat_hints_never_point_to_the_v2_api():
    from rumbapy.errors import NotConnected, SessionReplaced, SessionUncertain

    c, factory = client(table_screen())
    c.copy_ps_to_string(1, 1, 5)
    c.disconnect()
    with pytest.raises(NotConnected) as nc:
        c.copy_ps_to_string(1, 1, 5)
    assert nc.value.hint == compat.NOT_CONNECTED_HINT and "t." not in str(nc.value)
    c.connect()
    factory.sessions[0]._mark_uncertain()
    with pytest.raises(SessionUncertain) as unc:
        c.send_key("ENTER")
    assert "t.acknowledge" not in str(unc.value) and "screen_load" in str(unc.value)
    factory.sessions[0]._set_replaced()
    with pytest.raises(SessionReplaced) as rep:
        c.copy_ps_to_string(1, 1, 5)
    assert "Session nova" not in str(rep.value) and "connect()" in str(rep.value)


def test_no_host_response_hint_is_compat(monkeypatch):
    from rumbapy.errors import NoHostResponse

    monkeypatch.setattr(compat, "COMPAT_AID_TIMEOUT", 0.3)
    fake = _table_screen()                                  # host mudo
    c, factory = client(fake)
    factory.sessions.clear()
    c.copy_ps_to_string(1, 1, 5)
    factory.sessions[0].timeout = 0.2
    with pytest.raises(NoHostResponse) as info:
        c.send_key("ENTER")
    assert "t.wait_text" not in str(info.value) and "screen_load" in str(info.value)


def test_copy_string_to_ps_hidden_field_never_cites_the_char():
    from rumbapy.errors import EncodingError

    c, _ = client(table_screen())
    with pytest.raises(EncodingError) as info:
        c.copy_string_to_ps(9, 20, "ałb")                   # (9,20) é o campo oculto (senha)
    assert "ł" not in str(info.value) and "U+0142" not in str(info.value)


def test_lazy_attach_on_blank_cics_screen_through_the_real_launcher():
    """CICS logado e com a tela limpa (CLEAR): o anexo automático da compat funciona (E.6 sem exigir texto)."""
    from tests.unit.client.launcher.world import World
    from tests.fakes.fake_ehllapi import FakeScreen

    world = World()
    world.add_launcher_window(screen=FakeScreen(lines=[""] * 24, fields=[{"row": 1, "col": 2, "length": 10}]))
    session_mod._set_factories(backend_factory=lambda p, py: world.get_backend, user32_factory=lambda: world.user32,
                               launcher_factory=lambda u32, get: world.launcher())
    c = compat.RumbaClient("D")
    assert c.copy_string_to_ps(1, 2, "SCOM") is True
    assert c.copy_ps_to_string(1, 2, 4) == "SCOM"
    assert world.user32_calls("post_close") == [] and world.spawned == []
    c.disconnect()
