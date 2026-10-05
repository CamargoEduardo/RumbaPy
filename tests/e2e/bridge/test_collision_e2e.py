"""Saída 15 (colisão de ``LOWORD(pid)``, D.2 passo 6) com o worker REAL 32-bit e ``--fake-dll`` (aceite da L, D.8).

O PID do worker não é controlável, então a colisão é simulada numa **cópia** do pacote: o ``_win32.py`` da cópia
devolve, nas N primeiras subidas, um "cliente HLLAPI" com o mesmo LOWORD do próprio worker (contador em
``RUMBAPY_HOME``). ``Bridge(pkgdir=cópia)`` calcula o build da cópia, então ``--expect-build`` continua coerente.
Só leitura de processos (Toolhelp32) no caminho normal; nada é morto.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.unit.worker.helpers import copy_package
from tests.e2e.bridge.conftest import Env
from rumbapy.errors import WorkerStartFailed
from rumbapy import _bridge

import pytest

pytestmark = pytest.mark.e2e

PATCH = '''

# --- e2e (só nesta cópia): colisão simulada de LOWORD(pid) nas N primeiras subidas
_e2e_original_client_pids = hllapi_client_pids


def hllapi_client_pids(dll_name: str = "ehlapi32.dll") -> list[int]:
    path = os.path.join(os.environ["RUMBAPY_HOME"], "colisoes.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            left = int(fh.read().strip() or "0")
    except (OSError, ValueError):
        left = 0
    if left > 0:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(left - 1))
        return [os.getpid() + 0x10000]          # mesmo LOWORD, outro PID
    return _e2e_original_client_pids(dll_name)
'''


def _colliding_package(env: Env, tmp_path: Path, collisions: int) -> Path:
    pkg = copy_package(tmp_path / "site")
    with open(pkg / "_win32.py", "a", encoding="utf-8") as fh:
        fh.write(PATCH)
    (env.home / "colisoes.txt").write_text(str(collisions), encoding="utf-8")
    return pkg


@pytest.mark.timeout(120)
def test_three_collisions_then_worker_starts(env: Env, tmp_path: Path) -> None:
    pkg = _colliding_package(env, tmp_path, 3)
    be = env.bridge(pkgdir=pkg).get_worker("D", "owner")
    env.remember(be.worker_pid)
    assert (env.home / "colisoes.txt").read_text(encoding="utf-8") == "0"      # as 3 colisões aconteceram
    assert len(env.boot_logs()) == 4                                            # 3 saídas 15 + o que subiu
    assert be.connect().connected
    be.release()


@pytest.mark.timeout(120)
def test_four_collisions_give_up_with_start_failed(env: Env, tmp_path: Path) -> None:
    pkg = _colliding_package(env, tmp_path, 4)
    with pytest.raises(WorkerStartFailed) as info:
        env.bridge(pkgdir=pkg).get_worker("D", "owner")
    assert "(código 15)" in info.value.message                                 # 4ª colisão: sem mais extras
    assert info.value.details["exit_code"] == 15
    assert (env.home / "colisoes.txt").read_text(encoding="utf-8") == "0"
    assert len(env.boot_logs()) == 4                                            # 1 + 3 extras, nenhum a mais
    assert env.registry() is None or not _bridge.registry_alive(env.registry())
