# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator

from tests.unit.client.launcher.world import World
from rumbapy import _legacy, _log

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> Iterator[None]:
    """Cada teste com o próprio ``RUMBAPY_HOME`` (o ``launched-*.json`` não vaza entre testes) e sem
    ``RUMBAPY_LAUNCH`` herdado."""
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RUMBAPY_LAUNCH", raising=False)
    monkeypatch.delenv("RUMBAPY_OPEN_TIMEOUT", raising=False)
    _legacy._reset_for_tests()
    yield
    _legacy._reset_for_tests()


@pytest.fixture
def notices(monkeypatch) -> list[str]:
    """Mensagens de ``_log.notice`` (o aviso visível ao usuário)."""
    seen: list[str] = []
    monkeypatch.setattr(_log, "notice", seen.append)
    return seen


@pytest.fixture
def world() -> World:
    return World()
