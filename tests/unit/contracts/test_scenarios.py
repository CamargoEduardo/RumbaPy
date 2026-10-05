"""Aceite P-0: fábricas de ``tests/fakes/scenarios.py`` (J.3), inclusive pelo caminho do ``--fake-dll mod:fábrica``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.fakes.fake_ehllapi import FakeEhllapi

import importlib
import threading
import ctypes
import time
import ast

import pytest

H = None
MODULE = "tests.fakes.scenarios"


def load(spec: str) -> FakeEhllapi:
    """Exatamente o que o worker faz com ``--fake-dll mod:fábrica`` (F.1)."""
    module, _, factory = spec.partition(":")
    fake = getattr(importlib.import_module(module), factory)()
    assert isinstance(fake, FakeEhllapi)
    return fake


def screen_text(fake: FakeEhllapi, size: int = 1920) -> str:
    buf = ctypes.create_string_buffer(size + 64)
    rc = fake.WD_CopyPS(H, buf, size)
    assert rc in (0, 4)
    return buf.raw[:size].decode("cp1252")


def test_scenarios_do_not_import_rumbapy() -> None:
    source = (Path(__file__).resolve().parents[2] / "fakes" / "scenarios.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("rumbapy")
        elif isinstance(node, ast.Import):
            assert not any(alias.name.startswith("rumbapy") for alias in node.names)


def test_cics_login_full_flow() -> None:
    fake = load(f"{MODULE}:cics_login")
    assert sorted(fake.sessions) == ["D"]
    assert fake.WD_ConnectPS(H, b"D") == 0
    assert "Select CICS Environment" in screen_text(fake)
    assert fake.WD_SendKey(H, b"C@E") == 0
    assert "TERMINAL" in screen_text(fake)
    assert fake.WD_SendKey(H, b"A000000@Tsenha@E") == 0
    assert "Signon is complete" in screen_text(fake)


def test_two_letters() -> None:
    fake = load(f"{MODULE}:two_letters")
    assert sorted(fake.sessions) == ["D", "Z"]
    assert fake.sessions["D"].hwnd != fake.sessions["Z"].hwnd
    count = ctypes.c_ushort(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    assert fake.WD_QuerySessions(H, ctypes.byref(count), buf) == 0 and count.value == 2


def test_ims_login_32x80() -> None:
    fake = load(f"{MODULE}:ims_login")
    session = fake.sessions["A"]
    assert (session.rows, session.cols) == (32, 80)
    fake.WD_ConnectPS(H, b"A")
    assert screen_text(fake, 2560)[80:86] == "USS010"
    fake.WD_SendKey(H, b"IMSRS@E")
    assert screen_text(fake, 2560)[3 * 80 + 43:3 * 80 + 50] == "Welcome"
    fake.WD_SendKey(H, b"A000000@Tsenha@E")
    assert screen_text(fake, 2560)[15 * 80 + 19:15 * 80 + 24] == "Start"
    fake.WD_SendKey(H, b"RHELP@E")
    assert "RHELP MAIN MENU" in screen_text(fake, 2560)


def test_blank_first_screen() -> None:
    fake = load(f"{MODULE}:blank_first_screen__1")
    assert fake.WD_ConnectPS(H, b"D") == 4                          # conectou, mas o PS está vazio/ocupado
    started = time.monotonic()
    assert screen_text(fake).strip() == ""
    deadline = started + 3
    while time.monotonic() < deadline and "Select CICS" not in screen_text(fake):
        time.sleep(0.02)
    assert time.monotonic() - started >= 0.9
    assert "Select CICS Environment" in screen_text(fake)
    assert fake.WD_SendKey(H, b"C@E") == 0 and "TERMINAL" in screen_text(fake)


def test_slow_host_and_defaults() -> None:
    assert load(f"{MODULE}:slow_host").sessions["D"].response_delay == 0.5
    fake = load(f"{MODULE}:slow_host__0.2")
    assert fake.sessions["D"].response_delay == 0.2
    assert load(f"{MODULE}:blank_first_screen").first_screen_seconds == 2.0  # type: ignore[attr-defined]


@pytest.mark.parametrize("spec, func", [
    (f"{MODULE}:hang", "WD_CopyPS"),
    (f"{MODULE}:hang__WD_CopyPSToString", "WD_CopyPSToString"),
    (f"{MODULE}:hang__SendKey", "WD_SendKey"),
])
def test_hang(spec: str, func: str) -> None:
    fake = load(spec)
    assert fake.hang_on == {func}


def test_hang_really_blocks_until_released() -> None:
    fake = load(f"{MODULE}:hang")
    fake.WD_ConnectPS(H, b"D")
    done = threading.Event()
    threading.Thread(target=lambda: (screen_text(fake), done.set()), daemon=True).start()
    assert not done.wait(0.2)
    fake.hang_event.set()
    assert done.wait(3)


def test_unknown_factory() -> None:
    module = importlib.import_module(MODULE)
    for name in ("nao_existe", "hang_", "slow_host__", "nao_existe__1"):
        with pytest.raises(AttributeError):
            getattr(module, name)
