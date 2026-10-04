"""Concorrência da ponte (P-D, aceite da L) com o worker REAL 32-bit e ``--fake-dll``:

* corrida de 2 **processos** clientes -> 1 worker só (trava de spawn entre processos, D.8);
* ``stopping`` -> espera: um ``shutdown`` admin em curso (``WD_ResetSystem`` lento) enquanto outro cliente entra; o
  cliente novo espera o worker antigo sair e sobe outro;
* thread de cliente abandonada (presa no handle do worker antigo, que travou e saiu pelo watchdog) não prende o
  worker novo; quando solta, o ``read`` dela é repetido 1x no worker novo (C.8).

A saída 15 (colisão de ``LOWORD(pid)``) está em ``test_collision_e2e.py`` (cópia do pacote com o ``_win32`` alterado)
e nos unitários (``tests/unit/client/bridge/test_get_worker.py::test_exit_15_*``).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from tests.unit.worker.helpers import REPO, own_python
from tests.e2e.bridge.conftest import PY32, SCENARIOS, Env
from rumbapy import _bridge

import subprocess
import threading
import logging
import json
import time
import os

import pytest

pytestmark = pytest.mark.e2e

RACE_SCRIPT = '''
import os, sys, types, json, time
d = sys.argv[1]
m = types.ModuleType("rumbapy"); m.__path__ = [d]; m.__file__ = os.path.join(d, "__init__.py")
sys.modules["rumbapy"] = m
sys.path.insert(0, sys.argv[2])
from rumbapy import _bridge
go = sys.argv[5]
deadline = time.monotonic() + 30
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.005)
cfg = _bridge.SpawnConfig(python32=sys.argv[3], fake_dll=sys.argv[4], fake_path=sys.argv[2], idle=120.0)
backend = _bridge.Bridge(pkgdir=d, config=cfg).get_worker("D", "admin")
print(json.dumps({"pid": os.getpid(), "worker": backend.worker_pid}), flush=True)
backend.release()
'''


def _child_env(env: Env) -> dict[str, str]:
    child = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    child.update({"RUMBAPY_SANDBOX": "1", "RUMBAPY_HOME": str(env.home), "RUMBAPY_QUIET": "1"})
    return child


@pytest.mark.timeout(120)
def test_two_client_processes_race_to_one_worker(env: Env) -> None:
    script = env.home / "corrida.py"
    script.write_text(RACE_SCRIPT, encoding="utf-8")
    go = env.home / "go.flag"
    procs = [subprocess.Popen([own_python(), "-I", "-S", str(script), str(_bridge.PKGDIR), str(REPO), str(PY32),
                               f"{SCENARIOS}:cics", str(go)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=_child_env(env), cwd=str(env.home)) for _ in range(2)]
    time.sleep(1.0)                                                     # os dois já importaram e esperam a largada
    go.write_text("go", encoding="utf-8")
    infos: list[dict[str, Any]] = []
    for proc in procs:
        out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        infos.append(json.loads(out.decode("utf-8").strip().splitlines()[-1]))
    for info in infos:
        env.remember(info["worker"])
    assert infos[0]["pid"] != infos[1]["pid"]                           # 2 processos clientes de verdade
    assert infos[0]["worker"] == infos[1]["worker"]                     # -> 1 worker
    assert len(env.boot_logs()) == 1                                    # um spawn só


@pytest.mark.timeout(120)
def test_stopping_worker_is_awaited_then_respawned(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rumbapy._bridge")
    admin = env.bridge("slow_stop").get_worker("D", "admin")
    old = admin.worker_pid
    env.remember(old)
    errors: list[BaseException] = []

    def stop() -> None:
        try:
            admin.shutdown("teste")                                     # bloqueia ~2,5 s (ResetSystem lento)
        except BaseException as exc:                                    # noqa: BLE001 - devolvido ao teste
            errors.append(exc)

    t = threading.Thread(target=stop)
    t.start()
    env.wait_registry(lambda r: r.get("state") == "stopping")
    be = env.bridge("slow_stop").get_worker("D", "owner")               # entra no meio da parada
    env.remember(be.worker_pid)
    t.join(30)
    assert not t.is_alive() and not errors, errors
    assert be.worker_pid != old and not _bridge.process_alive(old)      # esperou o antigo sair e subiu outro
    assert "está saindo (stopping); esperando" in caplog.text
    assert be.connect().connected
    be.release()


@pytest.mark.timeout(120)
def test_abandoned_client_thread_does_not_block_new_worker(env: Env) -> None:
    b = env.bridge("hang_read_once")
    stuck_be = b.get_worker("D", "owner")
    old = stuck_be.worker_pid
    env.remember(old)
    stuck_be.connect()
    old_handle = stuck_be.handle
    release = threading.Event()
    entered = threading.Event()
    original = old_handle.channel.recv_msg

    def blocked(timeout: float) -> Any:
        entered.set()
        release.wait()                                                  # "thread abandonada" presa no canal antigo
        return original(timeout)

    old_handle.channel.recv_msg = blocked
    outcome: list[BaseException | None] = []

    def abandoned() -> None:
        try:
            stuck_be.read(1, 1, 10)
            outcome.append(None)
        except BaseException as exc:                                    # noqa: BLE001 - devolvido ao teste
            outcome.append(exc)

    t = threading.Thread(target=abandoned, daemon=True)
    t.start()
    assert entered.wait(10)
    assert old_handle.lock.locked()                                     # a trava do handle antigo está presa
    # o worker antigo trava no WD_CopyPSToString e o watchdog dele (2 s) o encerra; a thread segue presa no canal
    assert _bridge.wait_exit(old, None, 20.0)
    t0 = time.monotonic()
    fresh = b.get_worker("D", "owner")                                  # mesma Bridge: o cache não serve o morto
    env.remember(fresh.worker_pid)
    assert fresh.handle is not old_handle and fresh.worker_pid != old
    assert fresh.connect().connected and len(fresh.read(1, 1, 10).text) == 10
    assert time.monotonic() - t0 < 30 and t.is_alive()                  # a thread antiga continua presa
    new_pid = fresh.worker_pid
    fresh.release()
    release.set()
    t.join(30)
    # quando solta, vê que o worker antigo morreu (WorkerHung/WorkerCrashed) e o read idempotente é repetido 1x no novo
    assert not t.is_alive() and outcome == [None] and stuck_be.worker_pid == new_pid
    stuck_be.release()
