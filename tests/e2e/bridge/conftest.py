"""Fixtures dos e2e da ponte: worker REAL 32-bit em subprocesso, sempre com ``--fake-dll`` e ``RUMBAPY_SANDBOX=1``.

Cada teste usa a própria pasta de dados (``RUMBAPY_HOME`` = ``tmp_path``): nomes de pipe/evento/registro nunca colidem
com outros testes nem com produção (``user_tag`` inclui ``HOME`` no sandbox). Nenhum processo é morto: no fim, os
handles são fechados (fim do lease), o evento de parada da letra é sinalizado e o teste espera os workers saírem.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.unit.worker.helpers import REPO, python32
from rumbapy import _bridge, _paths

import time

import pytest

PY32 = python32()
SCENARIOS = "tests.e2e.bridge.scenarios_bridge"


class Env:
    """Pasta de dados do teste + fábrica de ``Bridge`` + limpeza."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.bridges: list[_bridge.Bridge] = []
        self.pids: set[int] = set()

    def config(self, factory: str = "cics", **kw: Any) -> _bridge.SpawnConfig:
        return _bridge.SpawnConfig(python32=PY32, fake_dll=f"{SCENARIOS}:{factory}", fake_path=str(REPO),
                                   log_level="DEBUG", idle=kw.pop("idle", 120.0), **kw)

    def bridge(self, factory: str = "cics", **kw: Any) -> _bridge.Bridge:
        config = kw.pop("config", None) or self.config(factory)
        b = _bridge.Bridge(config=config, **kw)
        self.bridges.append(b)
        return b

    def registry(self, letter: str = "D") -> dict[str, Any] | None:
        return _paths.read_json(_paths.worker_registry(letter))

    def wait_registry(self, pred: Any, letter: str = "D", timeout: float = 5.0) -> dict[str, Any]:
        """O registro é regravado logo depois do ``welcome``: espera a condição em vez de ler uma vez só."""
        deadline = time.monotonic() + timeout
        while True:
            reg = self.registry(letter)
            if reg is not None and pred(reg):
                return reg
            assert time.monotonic() < deadline, f"registro não chegou ao estado esperado: {reg}"
            time.sleep(0.05)

    def boot_logs(self) -> list[Path]:
        return sorted(_paths.boot_logs_dir().glob("*.txt")) if _paths.boot_logs_dir().exists() else []

    def remember(self, pid: Any) -> None:
        if isinstance(pid, int):
            self.pids.add(pid)

    def cleanup(self) -> None:
        for b in self.bridges:
            for handle in list(b.handles.values()):
                handle.close()
        for path in _paths.run_dir().glob("worker-*.json") if _paths.run_dir().exists() else []:
            reg = _paths.read_json(path)
            if reg:
                self.remember(reg.get("pid"))
        for letter in ("D", "Z", "A", "_"):
            _bridge.Bridge()._signal_stop(letter)
        stuck = [pid for pid in self.pids if not _bridge.wait_exit(pid, None, 20.0)]
        assert not stuck, f"processos auxiliares não saíram: {stuck}"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    if PY32 is None:
        pytest.skip("Python 32-bit não encontrado (.venv32 ou RUMBAPY_TEST_PY32)")
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    monkeypatch.setenv("RUMBAPY_SANDBOX", "1")
    e = Env(tmp_path)
    try:
        yield e
    finally:
        e.cleanup()
