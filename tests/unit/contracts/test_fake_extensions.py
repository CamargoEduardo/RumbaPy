"""Aceite P-0: extensões do ``FakeEhllapi`` (J.3) — desligadas, o comportamento é idêntico ao anterior.

O "comportamento anterior" está congelado em ``golden/fake_ehllapi_golden.json`` (gerado com o simulador antes das
extensões, commit 929d8e7; ver ``fake_golden_scenario.py``). A pasta NÃO pode se chamar ``data/``: esse padrão está no
``.gitignore`` e o arquivo ficaria fora do commit (``test_golden_master_is_versioned`` vigia isso).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, FakeSession
from tests.unit.contracts import fake_golden_scenario
from tests.fakes import fake_ehllapi

import subprocess
import threading
import ctypes
import shutil
import time

import pytest

GOLDEN = Path(__file__).with_name("golden") / "fake_ehllapi_golden.json"
H = None


def test_extensions_off_reproduce_the_golden_master() -> None:
    result = fake_golden_scenario.run(fake_ehllapi)
    assert fake_golden_scenario.dumps(result) + "\n" == GOLDEN.read_text(encoding="ascii")


def test_golden_master_is_versioned() -> None:
    """O golden precisa entrar no commit: ``git check-ignore`` não pode casar com ele (padrões ``data/``, ``*.txt``…)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git não está no PATH")
    proc = subprocess.run([git, "check-ignore", "-q", str(GOLDEN)], cwd=GOLDEN.parent, capture_output=True,
                          timeout=20)
    if proc.returncode not in (0, 1):
        pytest.skip("fora de um checkout git (ex.: sdist)")
    assert proc.returncode == 1, f"{GOLDEN.name} está ignorado pelo .gitignore e não entraria no commit"


def test_extensions_are_off_by_default() -> None:
    fake = FakeEhllapi()
    session = fake.add_session("D")
    assert fake.rc_override == {}
    assert (session.response_delay, session.lock_visible_after, session.pending_responses) == (0.0, 0.0, 0)
    assert "AUTORESET" in fake.params and "NORESET" not in fake.params


# ------------------------------------------------------------------------------------------- utilidades

def two_screens(fake: FakeEhllapi) -> FakeSession:
    """Sessão D: tela "ANTIGA" com um campo; qualquer AID carrega a tela "NOVA"."""
    session = fake.add_session("D")
    session.load(FakeScreen(name="old", lines=["ANTIGA"], fields=[{"row": 2, "col": 1, "length": 5}]))
    new = FakeScreen(name="new", lines=["NOVA"], fields=[{"row": 3, "col": 1, "length": 5}])
    session.on_aid = lambda s, aid, fields: s.load(new)
    assert fake.WD_ConnectPS(H, b"D") == 0
    return session


def line1(fake: FakeEhllapi) -> tuple[int, str]:
    buf = ctypes.create_string_buffer(80 + 16)
    rc = fake.WD_CopyPSToString(H, 1, buf, 80)
    return rc, buf.raw[:80].decode("cp1252").strip()


def g8_locked(fake: FakeEhllapi) -> tuple[int, bool]:
    buf = ctypes.create_string_buffer(110)
    rc = fake.WD_CopyOIA(H, buf, 103)
    return rc, any(buf.raw[88:93])


def wait_until(cond: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ------------------------------------------------------------------------------------------- response_delay

def test_response_delay_keeps_old_screen_and_lock_until_the_host_answers() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    session.response_delay = 0.8
    started = time.monotonic()
    assert fake.WD_SendKey(H, b"@E") == 0
    assert [aid for aid, _ in session.aid_log] == ["ENTER"]          # a AID foi registrada na hora
    assert session.pending_responses == 1
    assert line1(fake) == (4, "ANTIGA")                               # tela antiga + ocupado
    assert g8_locked(fake) == (4, True)
    assert fake.WD_SendKey(H, b"X") == 4                              # teclado travado
    assert wait_until(lambda: line1(fake) == (0, "NOVA"))
    assert time.monotonic() - started >= 0.75
    assert g8_locked(fake) == (0, False)
    assert session.pending_responses == 0
    assert not session.busy


def test_response_delay_releases_keyboard_only_after_the_new_screen() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    session.response_delay = 0.2
    fake.WD_SendKey(H, b"@E")
    seen: list[tuple[int, str]] = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (not seen or seen[-1] != (0, "NOVA")):
        seen.append(line1(fake))
    assert (0, "ANTIGA") not in seen                                  # nunca "livre com a tela antiga"
    assert seen[-1] == (0, "NOVA")


# ------------------------------------------------------------------------------------------- lock_visible_after

def test_lock_visible_after_hides_the_lock_at_first() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    session.response_delay = 1.2
    session.lock_visible_after = 0.6
    fake.WD_SendKey(H, b"@E")
    assert line1(fake) == (0, "ANTIGA")                               # bloqueio ainda não observável
    assert g8_locked(fake) == (0, False)
    assert wait_until(lambda: g8_locked(fake) == (4, True), timeout=2.0)
    assert line1(fake) == (4, "ANTIGA")
    assert wait_until(lambda: line1(fake) == (0, "NOVA"), timeout=3.0)


def test_lock_visible_after_without_response_delay() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    session.host_delay = 1.2
    session.lock_visible_after = 0.6
    fake.WD_SendKey(H, b"@E")
    assert line1(fake) == (0, "NOVA")                                 # resposta imediata, bloqueio invisível
    assert wait_until(lambda: g8_locked(fake)[1], timeout=2.0)
    assert wait_until(lambda: not g8_locked(fake)[1], timeout=2.0)


# ------------------------------------------------------------------------------------------- rc_override

def test_rc_override_queue() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    fake.rc_override = {"WD_CopyPSToString": [4, 9], "SendKey": [5]}
    assert line1(fake)[0] == 4
    assert line1(fake)[0] == 9
    assert line1(fake) == (0, "ANTIGA")                               # fila esgotada: volta ao normal
    assert fake.WD_SendKey(H, b"@E") == 5                             # forçado: nenhum efeito colateral
    assert session.aid_log == [] and session.screen_name == "old"
    assert ("WD_SendKey", ("rc_override", 5)) in fake.calls
    assert fake.WD_SendKey(H, b"@E") == 0 and session.screen_name == "new"
    fake.rc_override["WD_GetSessionHWND"] = [0]
    assert fake.WD_GetSessionHWND(H) == 0 and fake.WD_GetSessionHWND(H) == session.hwnd


# ------------------------------------------------------------------------------------------- SetSessionParamEx

def test_noreset_removes_autoreset() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    assert fake.WD_SetSessionParamEx(H, b"NORESET") == 0
    assert "NORESET" in fake.params and "AUTORESET" not in fake.params
    session.inhibited = True                                          # "X -f"
    assert fake.WD_SendKey(H, b"@0A") == 5                            # sem AUTORESET o RESET não é implícito
    assert fake.WD_SendKey(H, b"@R") == 0 and not session.inhibited   # Key.RESET destrava (dica do KeyboardLocked)
    assert fake.WD_SendKey(H, b"@0A") == 0
    session.inhibited = True
    assert fake.WD_SetSessionParamEx(H, b"AUTORESET") == 0
    assert "AUTORESET" in fake.params and "NORESET" not in fake.params
    assert fake.WD_SendKey(H, b"@0A") == 0


@pytest.mark.parametrize("option, removed", [
    ("ATTRB", {"NOATTRB"}), ("EAB", {"NOEAB"}), ("NWAIT", {"TWAIT"}), ("LWAIT", {"TWAIT"}),
    ("SRCHFROM", {"SRCHALL"}), ("SRCHBKWD", {"SRCHFRWD"}), ("IPAUSE", {"FPAUSE"}), ("CONPHYS", {"CONLOG"}),
])
def test_opposite_options(option: str, removed: set[str]) -> None:
    fake = FakeEhllapi()
    before = set(fake.params)
    assert fake.WD_SetSessionParamEx(H, option.lower().encode()) == 0
    assert fake.params == (before - removed) | {option}


def test_write_access_group_and_invalid_names() -> None:
    fake = FakeEhllapi()
    fake.WD_SetSessionParamEx(H, b"WRITE_SUPER")
    assert fake.WD_SetSessionParamEx(H, b"WRITE_READ,BOGUS") == 2     # rc 2, mas a opção válida vale
    assert "WRITE_READ" in fake.params and "WRITE_SUPER" not in fake.params and "BOGUS" not in fake.params


# ------------------------------------------------------------------------------------------- @H (Key.HELP)

def test_help_is_a_local_key() -> None:
    fake = FakeEhllapi()
    session = two_screens(fake)
    assert fake.WD_SendKey(H, b"@H") == 0                             # antes: rc 2 (ParameterError enganoso)
    assert session.aid_log == [] and session.screen_name == "old"
    assert fake.WD_SendKey(H, b"@A@H") == 0                           # SYSREQ continua sendo AID
    assert [aid for aid, _ in session.aid_log] == ["SYSREQ"]


# ------------------------------------------------------------------------------------------- trava e hang_on

def test_hang_releases_the_simulator_lock() -> None:
    fake = FakeEhllapi()
    two_screens(fake)
    fake.hang_on = {"WD_CopyPS"}
    results: list[int] = []
    worker = threading.Thread(target=lambda: results.append(fake.WD_CopyPS(H, ctypes.create_string_buffer(2000),
                                                                           1920)), daemon=True)
    worker.start()
    assert wait_until(lambda: any(name == "WD_CopyPS" for name, _ in fake.calls))
    cursor = ctypes.c_ushort()
    assert fake.WD_QueryCursorLocation(H, ctypes.byref(cursor)) == 0   # não ficou preso atrás da chamada travada
    assert results == []
    fake.hang_event.set()
    worker.join(timeout=3)
    assert results == [0]
