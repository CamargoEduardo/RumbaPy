"""Reciclagem (C.8) e lease entre processos (C.3/D.8, R1/E2) com o worker real 32-bit e ``--fake-dll``.

* ``hang`` no ``WD_CopyPSToString`` -> ``WorkerHung`` -> worker novo -> ``read`` repetido 1x (mesmo hwnd);
* ``hang`` no ``WD_SendKey`` -> ``WorkerHung(delivery=unknown)`` e o ``send`` NÃO é repetido;
* hwnd diferente depois de reciclar -> ``SessionReplaced``;
* dono vivo em outro processo -> ``TerminalInUse`` com PID/script; worker de build antigo com dono vivo não é parado e
  sai sozinho quando o dono sai; build antigo sem dono -> ``shutdown`` + spawn novo.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit.worker.helpers import REPO, copy_package, own_python
from tests.e2e.bridge.conftest import PY32, SCENARIOS, Env
from rumbapy.errors import SessionReplaced, TerminalInUse, WorkerHung
from rumbapy import _bridge

import subprocess
import logging
import json
import os

import pytest

pytestmark = pytest.mark.e2e

OWNER_SCRIPT = '''
import os, sys, types, json
d = sys.argv[1]
m = types.ModuleType("rumbapy"); m.__path__ = [d]; m.__file__ = os.path.join(d, "__init__.py")
sys.modules["rumbapy"] = m
sys.path.insert(0, sys.argv[2])
from rumbapy import _bridge
cfg = _bridge.SpawnConfig(python32=sys.argv[3], fake_dll=sys.argv[4], fake_path=sys.argv[2], idle=120.0)
backend = _bridge.Bridge(pkgdir=d, config=cfg).get_worker("D", "owner")
backend.connect()
print(json.dumps({"pid": os.getpid(), "worker": backend.worker_pid}), flush=True)
sys.stdin.read()
backend.release()
'''


class Owner:
    """Outro processo cliente (``dono.py``) segurando o lease da letra D até o stdin fechar."""

    def __init__(self, env: Env, pkgdir: Path) -> None:
        script = env.home / "dono.py"
        script.write_text(OWNER_SCRIPT, encoding="utf-8")
        child_env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
        child_env.update({"RUMBAPY_SANDBOX": "1", "RUMBAPY_HOME": str(env.home), "RUMBAPY_QUIET": "1"})
        self.proc = subprocess.Popen([own_python(), "-I", "-S", str(script), str(pkgdir), str(REPO), str(PY32),
                                      f"{SCENARIOS}:cics"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=child_env, cwd=str(env.home))
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("o dono não subiu: " + self.proc.stderr.read().decode("utf-8", "replace"))
        info = json.loads(line)
        self.pid, self.worker = info["pid"], info["worker"]
        env.remember(self.worker)

    def leave(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(30)


def _connected(env: Env, factory: str) -> Any:
    be = env.bridge(factory).get_worker("D", "owner")
    env.remember(be.worker_pid)
    result = be.connect()
    assert result.connected
    return be


@pytest.mark.timeout(90)
def test_hung_read_is_retried_once_after_recycle(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="rumbapy._backend")
    be = _connected(env, "hang_read_once")
    old = be.worker_pid
    read = be.read(1, 1, 20)                                    # 1º worker trava; o 2º responde
    assert be.worker_pid != old
    env.remember(be.worker_pid)
    assert len(read.text) == 20
    assert not _bridge.process_alive(old)                       # esperou o worker antigo sair antes de reciclar
    assert "retry op=read after=WorkerHung" in caplog.text
    be.release()


@pytest.mark.timeout(90)
def test_hung_send_is_not_retried(env: Env) -> None:
    be = _connected(env, "hang_send_once")
    old = be.worker_pid
    with pytest.raises(WorkerHung) as info:
        be.send(["abc"], secret=False)
    assert info.value.delivery == "unknown"                    # Session fica uncertain (P-E)
    assert be.worker_pid == old                                 # nada de reciclar/repetir uma op N
    screen = be.read(1, 1, 10)                                  # a próxima op idempotente recicla
    assert screen.text is not None and be.worker_pid != old
    env.remember(be.worker_pid)
    be.release()


@pytest.mark.timeout(90)
def test_recycle_with_other_hwnd_is_session_replaced(env: Env) -> None:
    be = _connected(env, "hang_read_then_new_hwnd")
    with pytest.raises(SessionReplaced):
        be.read(1, 1, 10)
    env.remember(be.worker_pid)
    be.release()


@pytest.mark.timeout(90)
def test_second_process_owner_gets_terminal_in_use(env: Env) -> None:
    owner = Owner(env, _bridge.PKGDIR)
    try:
        with pytest.raises(TerminalInUse) as info:
            env.bridge().get_worker("D", "owner")
        text = str(info.value)
        assert str(owner.pid) in text and "dono.py" in text
        assert _bridge.process_alive(owner.worker)
        admin = env.bridge().get_worker("D", "admin")           # admin entra mesmo com dono
        assert admin.worker_pid == owner.worker
        admin.release()
    finally:
        assert owner.leave() == 0


@pytest.mark.timeout(120)
def test_old_build_with_live_owner_is_not_stopped(env: Env, tmp_path: Path) -> None:
    pkg = copy_package(tmp_path / "site")
    owner = Owner(env, pkg)
    try:
        with open(pkg / "_version.py", "a", encoding="utf-8") as fh:   # "atualização" em disco com o worker vivo
            fh.write("\n# build novo simulado\n")
        newer = env.bridge(pkgdir=pkg)
        with pytest.raises(TerminalInUse):
            newer.get_worker("D", "owner")                      # o dono vem antes do build (R1/E2)
        assert _bridge.process_alive(owner.worker)              # nunca parado com dono vivo
    finally:
        assert owner.leave() == 0
    assert _bridge.wait_exit(owner.worker, None, 20.0)          # fim do lease com build novo -> sai sozinho (E2)
    be = env.bridge(pkgdir=pkg).get_worker("D", "owner")
    env.remember(be.worker_pid)
    assert be.worker_pid != owner.worker
    be.release()


@pytest.mark.timeout(120)
def test_old_build_without_owner_is_shut_down_and_respawned(env: Env, tmp_path: Path) -> None:
    pkg = copy_package(tmp_path / "site")
    old = env.bridge(pkgdir=pkg).get_worker("D", "admin")
    old_pid = old.worker_pid
    env.remember(old_pid)
    old.release()
    with open(pkg / "_version.py", "a", encoding="utf-8") as fh:
        fh.write("\n# build novo simulado\n")
    be = env.bridge(pkgdir=pkg).get_worker("D", "owner")
    env.remember(be.worker_pid)
    assert be.worker_pid != old_pid and not _bridge.process_alive(old_pid)
    be.release()


@pytest.mark.timeout(90)
def test_hung_send_leaves_session_uncertain_and_is_not_resent(env: Env) -> None:
    """Aceite da L com a ``Session`` (P-E) sobre o ``WorkerBackend`` real: ``WD_SendKey`` preso -> ``WorkerHung``,
    ``state == "uncertain"``, nova entrada recusada (``SessionUncertain``) e nenhum reenvio."""
    from rumbapy.errors import SessionUncertain
    from rumbapy.profiles import PROFILES
    from rumbapy._types import OpenInfo
    from rumbapy.session import Session

    be = env.bridge("hang_send_once").get_worker("D", "owner")
    env.remember(be.worker_pid)
    result = be.connect()
    info = OpenInfo(launched=False, closed_instances=0, hwnd=result.hwnd, hwnd_pid=None, seconds_to_register=None,
                    seconds_to_connect=None, rows=result.rows, cols=result.cols, via="attach")
    session = Session(be, PROFILES["D"], info, timeout=20.0)
    old = be.worker_pid
    try:
        with pytest.raises(WorkerHung) as exc:
            session.send_keys("abc")
        assert exc.value.delivery == "unknown"
        assert session.state == "uncertain"
        assert be.worker_pid == old                                 # nada foi repetido num worker novo
        with pytest.raises(SessionUncertain):
            session.send_keys("abc")                                # entrada recusada até reancorar
        assert be.worker_pid == old
    finally:
        session.release()
        be.release()
