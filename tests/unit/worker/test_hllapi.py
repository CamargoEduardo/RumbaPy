"""F.1/F.2: carga da DLL (só dublê no sandbox), ``GuardedDll``, lista fechada de funções e buffers ``.raw``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen
from rumbapy._worker.guard import GuardedDll
from rumbapy._worker import hllapi
from rumbapy import errors

import ctypes
import ast

import pytest

FAKE = "tests.fakes.scenarios:cics_login"


def test_load_fake_factory_and_guard() -> None:
    dll = hllapi.load_dll("nao-usado", fake=FAKE, guard="none")
    assert isinstance(dll, FakeEhllapi) and "D" in dll.sessions
    guarded = hllapi.load_dll("nao-usado", fake=FAKE, guard="login")
    assert isinstance(guarded, GuardedDll)
    assert guarded.policy is not hllapi.POLICIES["login"] and guarded.policy.allowed_aids == {"ENTER"}
    parametric = hllapi.load_dll("nao-usado", fake="tests.fakes.scenarios:hang__WD_CopyPSToString", guard="none")
    assert parametric.hang_on == {"WD_CopyPSToString"}


def test_load_rejections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(errors.ParameterError):
        hllapi.load_dll("x", fake=FAKE, guard="bogus")  # type: ignore[arg-type]
    with pytest.raises(errors.ParameterError):
        hllapi.load_dll("x", fake="sem-dois-pontos", guard="none")
    monkeypatch.setenv("RUMBAPY_SANDBOX", "0")
    with pytest.raises(errors.SetupError):                          # dublê só com sandbox
        hllapi.load_dll(str(tmp_path / "nao-existe.dll"), fake=FAKE, guard="none")


def test_only_bound_functions_are_referenced() -> None:
    """Teste estático (F.2): nenhum nome ``WD_*`` fora da lista fechada aparece no módulo."""
    source = Path(hllapi.__file__).read_text(encoding="utf-8")
    names = {node.value for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("WD_")}
    assert names == set(hllapi.BOUND_FUNCTIONS)


def test_bindings_set_argtypes_and_restype() -> None:
    fake = FakeEhllapi()
    hll = hllapi.Hllapi(fake)
    assert fake.WD_CopyPSToString.argtypes == [ctypes.c_void_p, ctypes.c_ushort, ctypes.c_char_p, ctypes.c_ushort]
    assert fake.WD_CopyPSToString.restype is ctypes.c_ushort
    assert fake.WD_GetSessionHWND.restype is ctypes.c_void_p
    assert fake.WD_QueryCursorLocation.argtypes[1] is ctypes.POINTER(ctypes.c_ushort)
    assert hll.hwnd() is None                                       # 0 -> None


def test_buffers_are_read_raw_with_nuls() -> None:
    fake = FakeEhllapi()
    session = fake.add_session("D")
    session.load(FakeScreen(lines=["AB"] + [""] * 23))
    session.buffer[2:4] = b"\0\0"
    hll = hllapi.Hllapi(fake)
    assert hll.connect("D") == 0
    rc, raw = hll.copy_ps(1920)
    assert rc == 0 and len(raw) == 1920 and raw[:5] == b"AB\0\0 "
    rc, part = hll.copy_ps_to_string(1, 5)
    assert part == b"AB\0\0 "
    rc, status = hll.query_status("D")
    assert rc == 0 and (status.rows, status.cols) == (24, 80)
    rc, entries = hll.query_sessions()
    assert rc == 0 and [e.letter for e in entries] == ["D"]
    rc, oia = hll.oia()
    assert rc == 0 and len(oia) == 103 and oia[81] == 0x10
    assert hll.cursor() == (0, 1)
    assert hll.find_field_pos(1, b"T ")[0] == 24                     # tela não formatada
    assert hll.send_key(b"@T") == 0 and hll.set_param(b"NORESET") == 0
    assert hll.disconnect() == 0 and hll.reset_system() == 0


def test_track_wraps_every_call() -> None:
    from contextlib import contextmanager

    seen: list[str] = []

    @contextmanager
    def track(func: str):  # type: ignore[no-untyped-def]
        seen.append(func)
        yield

    fake = FakeEhllapi()
    fake.add_session("D")
    hll = hllapi.Hllapi(fake, track=track)
    hll.connect("D")
    hll.copy_ps(1920)
    hll.hwnd()
    assert seen == ["WD_ConnectPS", "WD_CopyPS", "WD_GetSessionHWND"]
