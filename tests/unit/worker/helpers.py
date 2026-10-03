"""Auxiliares dos testes do worker e do IPC: cliente de protocolo mínimo e spawn do worker real com ``--fake-dll``.

O cliente aqui é só o necessário para exercitar o lado do worker (o cliente de verdade é ``_bridge``, pacote P-D).
O worker em subprocesso roda sempre com ``RUMBAPY_SANDBOX=1``, ``--fake-dll tests.fakes.scenarios:<fábrica>`` e
``--fake-path <repo>``; nunca há ``--dll`` na linha de comando. Ninguém mata o processo: os testes o encerram por
``shutdown``/evento de parada e esperam a saída.
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rumbapy import _buildid, _ipc, _paths, _protocol, _win32

import subprocess
import secrets
import struct
import shutil
import time
import sys
import os

REPO = Path(__file__).resolve().parents[3]
PKGDIR = REPO / "src" / "rumbapy"
BOOT = ('import os,sys,types; d=sys.argv[1]; m=types.ModuleType("rumbapy"); m.__path__=[d]; '
        'm.__file__=os.path.join(d,"__init__.py"); sys.modules["rumbapy"]=m; '
        'from rumbapy._worker.main import main; sys.exit(main(sys.argv[2:]))')
IS_32 = struct.calcsize("P") == 4


def _base_python(venv: str) -> str | None:
    cfg = REPO / venv / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                exe = Path(value.strip()) / "python.exe"
                return str(exe) if exe.is_file() else None
    except OSError:
        return None
    return None


def python32() -> str | None:
    """Python 32-bit real (não o lançador do venv): ``RUMBAPY_TEST_PY32``, o próprio (x86) ou o de ``.venv32``."""
    explicit = os.environ.get("RUMBAPY_TEST_PY32")
    if explicit:
        return explicit
    if IS_32:
        return getattr(sys, "_base_executable", sys.executable)
    return _base_python(".venv32")


def python64() -> str | None:
    if not IS_32:
        return getattr(sys, "_base_executable", sys.executable)
    return _base_python(".venv")


def own_python() -> str:
    return getattr(sys, "_base_executable", sys.executable)


def write_key(path: Path, key: bytes | None = None) -> bytes:
    key = key if key is not None else secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    return key


@dataclass
class Worker:
    proc: subprocess.Popen
    pipe: str
    registry: Path
    key: bytes
    log: Path
    boot: Path
    stop_event: str
    build_id: str
    letter: str = "D"
    reg: dict[str, Any] = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return int(self.reg.get("pid") or self.proc.pid)

    def log_text(self) -> str:
        parts = []
        for p in (self.log, self.boot):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        return "\n".join(parts)

    def wait(self, timeout: float = 20.0) -> int | None:
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> int | None:
        """Encerra sem matar: shutdown admin, depois o evento de parada; espera a saída."""
        if self.proc.poll() is not None:
            return self.proc.returncode
        try:
            with WireClient(self, mode="admin") as admin:
                admin.call("shutdown", {"reason": "teste"}, timeout=15)
        except Exception:
            pass
        code = self.wait(15)
        if code is None:
            try:
                h = _win32.open_event(self.stop_event, create=False)
                _win32.set_event(h)
                _win32.close_handle(h)
            except OSError:
                pass
            code = self.wait(20)
        return code


def worker_argv(python: str, home: Path, *, letter: str = "D", factory: str = "cics_login",
                pkgdir: Path = PKGDIR, expect_build: str | None = None, keyfile: Path | None = None,
                pipe: str | None = None, extra: list[str] | None = None, fake: bool = True,
                idle: float = 120.0) -> tuple[list[str], dict[str, Any]]:
    token = secrets.token_hex(4)
    pipe = pipe or _paths.pipe_name(letter, _paths.new_pipe_rand())
    registry = home / "run" / f"worker-{token}-{letter}.json"
    log = home / "logs" / f"worker-{token}-{letter}.log"
    stop_event = f"Local\\rumbapy2-test-{token}-{letter}-stop"
    keyfile = keyfile or home / "authkey.bin"
    build = expect_build or _buildid.compute(pkgdir)
    argv = [python, "-I", "-S", "-X", "faulthandler", "-c", BOOT, str(pkgdir), "--letter", letter, "--pipe", pipe,
            "--keyfile", str(keyfile), "--stop-event", stop_event, "--registry", str(registry), "--log", str(log),
            "--log-level", "DEBUG", "--idle", str(idle), "--expect-build", build, "--commit", "none"]
    if fake:
        argv += ["--fake-dll", f"tests.fakes.scenarios:{factory}", "--fake-path", str(REPO)]
    argv += extra or []
    meta = {"pipe": pipe, "registry": registry, "log": log, "stop_event": stop_event, "build_id": build,
            "keyfile": keyfile}
    return argv, meta


def worker_env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    env.update({"RUMBAPY_SANDBOX": "1", "RUMBAPY_HOME": str(home)})
    env.update(extra)
    return env


def run_worker(argv: list[str], home: Path, *, env: dict[str, str] | None = None) -> subprocess.Popen:
    boot = home / "logs" / "boot" / f"boot-{secrets.token_hex(4)}.txt"
    boot.parent.mkdir(parents=True, exist_ok=True)
    with open(boot, "wb") as err:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                cwd=str(home), env=env or worker_env(home), close_fds=True,
                                creationflags=_win32.CREATE_NO_WINDOW | _win32.CREATE_NEW_PROCESS_GROUP)
    proc.boot_path = boot  # type: ignore[attr-defined]
    return proc


def spawn_worker(python: str, home: Path, *, key: bytes | None = None, timeout: float = 20.0,
                 env: dict[str, str] | None = None, **kw: Any) -> Worker:
    argv, meta = worker_argv(python, home, **kw)
    key = write_key(meta["keyfile"], key) if not meta["keyfile"].exists() else meta["keyfile"].read_bytes()
    proc = run_worker(argv, home, env=env)
    worker = Worker(proc, meta["pipe"], meta["registry"], key, meta["log"], proc.boot_path,  # type: ignore[attr-defined]
                    meta["stop_event"], meta["build_id"], kw.get("letter", "D"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reg = _paths.read_json(meta["registry"])
        if reg and reg.get("pipe") == meta["pipe"] and reg.get("state") == "listening":
            worker.reg = reg
            return worker
        if proc.poll() is not None:
            raise AssertionError(f"o worker saiu com {proc.returncode} no boot:\n{worker.log_text()}")
        time.sleep(0.05)
    worker.stop()
    raise AssertionError(f"o worker não ficou pronto em {timeout} s:\n{worker.log_text()}")


class WireClient:
    """Cliente mínimo do protocolo 2 (auth + hello/hello_ack + requisições)."""

    def __init__(self, worker: Any = None, *, pipe: str | None = None, pid: int | None = None,
                 key: bytes | None = None, letter: str = "D", mode: str = "owner", build_id: str | None = None,
                 client_pid: int | None = None, script: str = "teste.py") -> None:
        self.pipe = pipe or worker.pipe
        self.pid = pid if pid is not None else worker.pid
        self.key = key if key is not None else worker.key
        self.letter = letter
        self.mode = mode
        self.build_id = build_id if build_id is not None else (worker.build_id if worker is not None else "")
        self.client_pid = client_pid if client_pid is not None else os.getpid()
        self.script = script
        self.channel: _ipc.Channel | None = None
        self.hello: dict[str, Any] | None = None
        self.answer: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.next_id = 1

    def open(self, *, expect_welcome: bool = True) -> WireClient:
        ch = _ipc.connect(self.pipe, self.pid, letter=self.letter)
        self.channel = ch
        _ipc.client_auth(ch, self.key, server_pid=self.pid)
        self.hello = ch.recv_msg(3.0)
        ch.send_msg({"t": "hello_ack", "mode": self.mode, "client_pid": self.client_pid, "client_version": "2.0.0",
                     "build_id": self.build_id, "script": self.script})
        self.answer = ch.recv_msg(3.0)
        if expect_welcome:
            assert self.answer is not None and self.answer.get("t") == "welcome", self.answer
        return self

    def __enter__(self) -> WireClient:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self.channel is not None:
            self.channel.close()

    def send(self, op: str, args: dict[str, Any] | None = None, hwnd: int | None = None) -> int:
        assert self.channel is not None
        rid = self.next_id
        self.next_id += 1
        self.channel.send_msg(_protocol.make_request(rid, op, args, hwnd))
        return rid

    def reply(self, rid: int, timeout: float = 10.0) -> dict[str, Any]:
        assert self.channel is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.channel.recv_msg(0.1)
            if msg is None:
                continue
            if _protocol.is_event(msg):
                self.events.append(msg)
                continue
            assert msg.get("id") == rid, msg
            return msg
        raise TimeoutError(f"sem resposta para id={rid} em {timeout} s")

    def wait_event(self, timeout: float = 5.0) -> dict[str, Any] | None:
        assert self.channel is not None
        deadline = time.monotonic() + timeout
        while not self.events and time.monotonic() < deadline:
            msg = self.channel.recv_msg(0.1)
            if msg is not None and _protocol.is_event(msg):
                self.events.append(msg)
        return self.events[0] if self.events else None

    def call(self, op: str, args: dict[str, Any] | None = None, hwnd: int | None = None,
             timeout: float = 10.0) -> dict[str, Any]:
        return self.reply(self.send(op, args, hwnd), timeout)

    def result(self, op: str, args: dict[str, Any] | None = None, hwnd: int | None = None,
               timeout: float = 10.0) -> dict[str, Any]:
        rid = self.send(op, args, hwnd)
        return _protocol.unwrap_reply(self.reply(rid, timeout), rid, letter=self.letter)


def copy_package(dest: Path) -> Path:
    """Cópia de ``src/rumbapy`` (sem ``__pycache__``) para testar "build novo em disco"."""
    target = dest / "rumbapy"
    shutil.copytree(PKGDIR, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target
