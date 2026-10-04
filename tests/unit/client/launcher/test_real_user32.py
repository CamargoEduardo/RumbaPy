"""``RealUser32``: só leituras de verdade (EnumWindows/GetWindowText/GetClassName/GetWindowThreadProcessId) e a
recusa do sandbox. Nenhuma janela real recebe mensagem (as funções de ação são trocadas por espiões que falham)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE, RawWindow, RealUser32, User32
from rumbapy.errors import SandboxViolation
from rumbapy import _user32

import pytest
import os


@pytest.fixture
def real(monkeypatch: pytest.MonkeyPatch) -> RealUser32:
    assert os.environ.get("RUMBAPY_SANDBOX") == "1"
    u = RealUser32()

    def forbidden(*args):  # type: ignore[no-untyped-def]
        raise AssertionError(f"API de ação chamada no sandbox: {args!r}")

    monkeypatch.setattr(u._api, "PostMessageW", forbidden)
    monkeypatch.setattr(u._api, "ShowWindowAsync", forbidden)
    return u


def test_real_user32_follows_the_protocol(real: RealUser32) -> None:
    assert isinstance(real, User32)


def test_top_windows_reads_the_desktop(real: RealUser32) -> None:
    windows = real.top_windows()
    assert isinstance(windows, list)
    for win in windows[:50]:
        assert isinstance(win, RawWindow)
        assert isinstance(win.hwnd, int) and win.hwnd > 0
        assert isinstance(win.title, str) and isinstance(win.cls, str)
        assert isinstance(win.pid, int) and isinstance(win.owner, int)
        assert isinstance(win.visible, bool) and isinstance(win.iconic, bool)


def test_own_process_image(real: RealUser32) -> None:
    image = real.process_image(os.getpid())
    assert image is not None and image.endswith(".exe") and image == image.lower()
    assert real.process_image(0) is None
    assert real.parent_image(0) is None


def test_invalid_handles(real: RealUser32) -> None:
    assert real.is_window(0) is False
    assert real.root_of(0) == 0


@pytest.mark.parametrize("action", ["post_close", "click", "show"])
def test_sandbox_refuses_actions_before_the_api(real: RealUser32, action: str) -> None:
    hwnd = 0x7FFF_FFF0                                      # nunca chega à API (o espião falharia)
    with pytest.raises(SandboxViolation) as info:
        if action == "show":
            real.show(hwnd, SW_SHOWNOACTIVATE)
        else:
            getattr(real, action)(hwnd)
    assert "sandbox" in str(info.value)


def test_sandbox_refuses_even_minimize(real: RealUser32) -> None:
    with pytest.raises(SandboxViolation):
        real.show(1, SW_SHOWMINNOACTIVE)


def test_refusal_does_not_depend_on_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(_user32, "_sandboxed", lambda: True)
    with pytest.raises(SandboxViolation):
        _user32._refuse_in_sandbox("teste")
    assert calls == []
