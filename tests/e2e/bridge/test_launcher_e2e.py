"""``close``/``open(reuse=True)`` de ponta a ponta (P-G, §L "Arquivos": ``close`` com dono vivo em outro processo e
``reuse`` com worker real e cenário pré-registrado).

Pilha inteira: ``rumbapy.close``/``rumbapy.open`` -> ``Launcher`` REAL (só ``spawn``/``run_script`` proibidos e os
PIDs das janelas falsas respondidos pelo teste) -> ``_launcher._default_get_backend`` (``ExistingAdmin.find`` no
``close``, ``_bridge.get_worker`` no ``open``) -> ``_bridge`` real -> worker REAL 32-bit com ``--fake-dll``
(``scenarios_bridge:cics``: a letra D já registrada no ``QuerySessions``). Janelas só no ``FakeUser32``; o dono
"de outro script" é um subprocesso cliente de verdade (como em ``test_concurrency_e2e``). Nada aqui carrega a DLL
real, abre o RUMBA ou toca janelas reais.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from tests.unit.client.session.support import user32_with_window
from tests.e2e.bridge.conftest import PY32, SCENARIOS, Env
from tests.unit.worker.helpers import REPO, own_python
from tests.fakes.fake_user32 import FakeUser32
from tests.fakes import scenarios
from rumbapy.errors import InstanceNotClosable, TerminalInUse
from rumbapy import session as session_mod
from rumbapy import _backend, _bridge, _launcher

import subprocess
import logging
import json
import os

import rumbapy
import pytest

pytestmark = pytest.mark.e2e

# Dono "de outro script": conecta (bind_instance no worker), avisa, espera a largada e tenta uma op de dono. Imprime
# 2 linhas JSON: {"pid", "worker", "hwnd", "connected"} e depois {"outcome": "ok" | nome da exceção}.
OWNER_SCRIPT = '''
import os, sys, types, json, time
d = sys.argv[1]
m = types.ModuleType("rumbapy"); m.__path__ = [d]; m.__file__ = os.path.join(d, "__init__.py")
sys.modules["rumbapy"] = m
sys.path.insert(0, sys.argv[2])
from rumbapy import _bridge
cfg = _bridge.SpawnConfig(python32=sys.argv[3], fake_dll=sys.argv[4], fake_path=sys.argv[2], idle=120.0)
be = _bridge.Bridge(pkgdir=d, config=cfg).get_worker("D", "owner")
res = be.connect()
print(json.dumps({"pid": os.getpid(), "worker": be.worker_pid, "hwnd": res.hwnd, "connected": res.connected}),
      flush=True)
go = sys.argv[5]
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.02)
try:
    be.read(1, 1, 5)
    outcome = "ok"
except Exception as exc:
    outcome = type(exc).__name__
print(json.dumps({"outcome": outcome}), flush=True)
be.release()
'''


def _child_env(env: Env) -> dict[str, str]:
    child = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    child.update({"RUMBAPY_SANDBOX": "1", "RUMBAPY_HOME": str(env.home), "RUMBAPY_QUIET": "1"})
    return child


class Owner:
    """Subprocesso cliente com o lease de dono da letra D (outro script, outro PID)."""

    def __init__(self, env: Env) -> None:
        script = env.home / "dono.py"
        script.write_text(OWNER_SCRIPT, encoding="utf-8")
        self.go = env.home / "dono-go.flag"
        self.proc = subprocess.Popen([own_python(), "-I", "-S", str(script), str(_bridge.PKGDIR), str(REPO),
                                      str(PY32), f"{SCENARIOS}:cics", str(self.go)], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=_child_env(env), cwd=str(env.home))
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            _out, err = self.proc.communicate(timeout=30)
            raise AssertionError(f"o dono não conectou: {err.decode('utf-8', 'replace')}")
        self.info: dict[str, Any] = json.loads(line.decode("utf-8"))
        env.remember(self.info["worker"])
        assert self.info["connected"] and self.info["pid"] != os.getpid()

    def finish(self) -> str:
        """Dá a largada, espera o subprocesso sair e devolve o resultado da op de dono dele."""
        self.go.write_text("go", encoding="utf-8")
        out, err = self.proc.communicate(timeout=60)
        assert self.proc.returncode == 0, err.decode("utf-8", "replace")
        return json.loads(out.decode("utf-8").strip().splitlines()[-1])["outcome"]

    def kill_switch(self) -> None:
        """Limpeza: só dá a largada (o subprocesso sai sozinho); nunca mata processo."""
        if self.proc.poll() is None:
            self.go.write_text("go", encoding="utf-8")
            try:
                self.proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                pass


@dataclass
class Stack:
    env: Env
    user32: FakeUser32
    spawned: list[list[str]] = field(default_factory=list)
    released: list[Any] = field(default_factory=list)       # handles das ExistingAdmin devolvidas pelo close
    exits_with_window: bool = True                          # o "emulador" sai quando a janela dele fecha

    def process_alive(self, pid: int, created: int | None) -> bool:
        if pid != scenarios.PID_D:
            return False
        if not self.exits_with_window:
            return True
        return any(w.alive and w.pid == pid for w in self.user32.windows.values())

    def window_alive(self) -> bool:
        return self.user32.windows[scenarios.HWND_D].alive


def _forbidden(what: str) -> Callable[..., Any]:
    def call(*args: Any, **kw: Any) -> Any:
        raise AssertionError(f"o e2e não pode {what}")
    return call


@pytest.fixture
def stack(env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    monkeypatch.setenv("RUMBAPY_TEST_FAKE_DLL", f"{SCENARIOS}:cics")      # SpawnConfig.from_env (open)
    monkeypatch.setenv("RUMBAPY_TEST_FAKE_PATH", str(REPO))
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    bridge = _bridge.Bridge()                                              # chave/handles desta pasta de dados
    env.bridges.append(bridge)
    monkeypatch.setattr(_bridge, "_default_bridge", bridge)
    st = Stack(env, user32_with_window())

    def spawn(argv: list[str]) -> Any:
        st.spawned.append(list(argv))
        raise AssertionError("o e2e não pode abrir o Web Launcher")

    def launcher(u32: Any, get_backend: Any) -> _launcher.Launcher:
        # get_backend = _launcher._default_get_backend (com .find_admin -> ExistingAdmin.find): a fiação de produção
        return _launcher.Launcher(u32, get_backend, spawn=spawn, run_script=_forbidden("rodar o cscript"),
                                  neutralize=lambda letter: None, hllapi_client_pids=lambda: [],
                                  process_alive=st.process_alive, process_created=lambda pid: 1)

    original_release = _backend.ExistingAdmin.release

    def release(self: _backend.ExistingAdmin) -> None:
        st.released.append(self._handle)
        original_release(self)

    monkeypatch.setattr(_backend.ExistingAdmin, "release", release)
    session_mod._set_factories(user32_factory=lambda: st.user32, launcher_factory=launcher)
    try:
        yield st
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)


def _admin_released(st: Stack) -> None:
    """O close abriu exatamente uma conexão admin (ExistingAdmin) e a devolveu (canal fechado)."""
    assert len(st.released) == 1 and st.released[0] is not None
    assert st.released[0].channel.closed


# ------------------------------------------------------------------------------------------- close (E.4)

@pytest.mark.timeout(120)
def test_close_evicts_owner_in_other_process(stack: Stack, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rumbapy._launcher")
    env = stack.env
    owner = Owner(env)
    try:
        reg = env.wait_registry(lambda r: (r.get("owner") or {}).get("pid") == owner.info["pid"]
                                and r.get("hwnd") == scenarios.HWND_D)
        assert reg["hwnd_pid"] == scenarios.PID_D
        assert rumbapy.close("D", timeout=10) == 1
        # passo 1: evict pela conexão admin real; o worker devolve o dono despejado (o PID do subprocesso)
        assert f"lease revogado (PID {owner.info['pid']})" in caplog.text
        # passo 4: WM_CLOSE só na janela do perfil
        assert (scenarios.HWND_D,) in stack.user32.calls_to("post_close") and not stack.window_alive()
        # passo 5b: o processo da instância saiu -> conferência pela DLL dispensada
        assert "conferência pela DLL dispensada" in caplog.text
        _admin_released(stack)
        after = env.registry()
        assert after is not None and after["owner"] is None and after["pid"] == owner.info["worker"]
        assert len(env.boot_logs()) == 1                                   # close nunca sobe worker
        assert owner.finish() == "SessionReplaced"                         # o aviso chegou ao OUTRO processo
    finally:
        owner.kill_switch()
    assert stack.spawned == []


@pytest.mark.timeout(120)
def test_close_instance_still_registered_raises_not_closable(stack: Stack) -> None:
    """Janela fechada, mas o processo do emulador segue vivo e a letra continua no ``QuerySessions`` (passos 5b/6):
    a conferência vai ao worker pela conexão admin e termina em ``InstanceNotClosable``; a admin é devolvida."""
    stack.exits_with_window = False
    env = stack.env
    owner = Owner(env)
    try:
        env.wait_registry(lambda r: (r.get("owner") or {}).get("pid") == owner.info["pid"]
                          and r.get("hwnd") == scenarios.HWND_D)
        with pytest.raises(InstanceNotClosable) as info:
            rumbapy.close("D", timeout=2)
        assert str(scenarios.PID_D) in info.value.message and _launcher.LAUNCHER_IMAGE in info.value.message
        assert not stack.window_alive()
        _admin_released(stack)
        assert (env.registry() or {}).get("owner", "?") is None
        assert owner.finish() == "SessionReplaced"
    finally:
        owner.kill_switch()
    assert stack.spawned == [] and len(env.boot_logs()) == 1


@pytest.mark.timeout(60)
def test_close_without_worker_never_spawns_one(stack: Stack) -> None:
    assert rumbapy.close("D", timeout=5) == 1
    assert not stack.window_alive() and stack.released == []
    assert stack.env.boot_logs() == [] and stack.env.registry() is None   # nem worker nem DLL (E.4 passo 1)


# ------------------------------------------------------------------------------------------- open(reuse=True) (E.5)

@pytest.mark.timeout(120)
def test_open_reuse_connects_to_the_single_instance(stack: Stack) -> None:
    env = stack.env
    pre = env.bridge().get_worker("D", "admin")                           # worker já vivo, letra pré-registrada
    worker = pre.worker_pid
    env.remember(worker)
    assert "D" in [s.letter for s in pre.sessions()]
    pre.release()
    t = rumbapy.open("D", reuse=True, timeout=30, python32=PY32)
    try:
        assert t.info.via == "reuse" and t.info.launched is False and t.info.closed_instances == 0
        assert t.info.hwnd == scenarios.HWND_D and t.info.seconds_to_register is None
        assert len(t.read(1, 1, 10)) == 10
        reg = env.wait_registry(lambda r: r.get("owner") is not None)
        assert reg["owner"]["pid"] == os.getpid() and reg["pid"] == worker
    finally:
        t.release()
    assert stack.spawned == [] and stack.user32.calls_to("post_close") == [] and stack.window_alive()
    assert len(env.boot_logs()) == 1                                       # o mesmo worker; nada lançado


@pytest.mark.timeout(120)
def test_open_reuse_with_lease_of_other_process_raises_in_use(stack: Stack) -> None:
    """A.14 f: a única instância sob o lease de OUTRO script -> ``TerminalInUse`` sem fechar nem lançar."""
    env = stack.env
    owner = Owner(env)
    try:
        env.wait_registry(lambda r: (r.get("owner") or {}).get("pid") == owner.info["pid"])
        with pytest.raises(TerminalInUse) as info:
            rumbapy.open("D", reuse=True, timeout=30, python32=PY32)
        assert str(owner.info["pid"]) in info.value.message
        assert stack.spawned == [] and stack.user32.calls_to("post_close") == [] and stack.window_alive()
        assert ((env.registry() or {}).get("owner") or {}).get("pid") == owner.info["pid"]
        assert owner.finish() == "ok"                                      # o lease do outro script continua
    finally:
        owner.kill_switch()
