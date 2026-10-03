"""Ponto de entrada do worker 32-bit (D.1, D.2): linha de comando, ordem de boot e códigos de saída.

Executado pelo ``BOOT`` de ``_bridge`` (``-I -S``, sem rodar ``rumbapy/__init__.py``)::

    <python32> -I -S -X faulthandler -c <BOOT> <pkgdir> --letter D --pipe <nome> --keyfile <...>\\authkey.bin
        --stop-event Local\\rumbapy2-<tag>-D-stop --registry <...> --log <...> --log-level INFO --idle 1800
        --expect-build <BUILD_ID> --commit <sha|none> [--dll <caminho>] [--guard none|readonly|text|login]
        [--fake-dll <modulo>:<fabrica> --fake-path <dir>] [--allow-64]

Ordem (D.2): Python (12) -> logging -> build (14) -> chave (13) -> pipe (11/18) -> colisão de LOWORD(pid) (15) ->
sandbox/live (17/16) -> T-dll carrega a DLL (10) -> evento de parada + registro -> T-watch e T-accept.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import _buildid, _ipc, _log, _protocol, _win32

import importlib
import threading
import argparse
import platform
import logging
import struct
import sys
import os

logger = logging.getLogger(__name__)

PKGDIR = Path(__file__).resolve().parents[1]
WORKER_MODULES = ("guard", "structs", "rc", "mask", "hllapi", "engine", "dispatch", "watchdog", "server", "main")
LOG_FORMAT = _log.FORMAT.replace("%(message)s", "L={letter} %(message)s")


def collides(own_pid: int, pids: Iterable[int]) -> bool:
    """Colisão de identidade HLLAPI (R10): outro processo com a DLL e o mesmo ``LOWORD(pid)``."""
    return any(p != own_pid and (p & 0xFFFF) == (own_pid & 0xFFFF) for p in pids)


def letter_arg(value: str) -> str:
    """``--letter``: uma letra A-Z (entra no formato do log e em ``QuerySessionStatus``)."""
    if len(value) != 1 or not ("A" <= value <= "Z"):
        raise argparse.ArgumentTypeError(f"letra de sessão inválida: {value!r} (use uma letra de A a Z)")
    return value


def build_parser() -> argparse.ArgumentParser:
    from .hllapi import DLL_PATH, GUARD_MODES

    p = argparse.ArgumentParser(prog="rumbapy-worker", add_help=False)
    p.add_argument("--letter", required=True, type=letter_arg)
    p.add_argument("--pipe", required=True)
    p.add_argument("--keyfile", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--expect-build", required=True)
    p.add_argument("--stop-event", default=None)
    p.add_argument("--log", default=None)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--idle", type=float, default=1800.0)
    p.add_argument("--commit", default="none")
    p.add_argument("--dll", default=DLL_PATH)
    p.add_argument("--guard", default="none", choices=GUARD_MODES)
    p.add_argument("--fake-dll", default=None)
    p.add_argument("--fake-path", default=None)
    p.add_argument("--allow-64", action="store_true")
    return p


def _stderr(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        stream.write(message + "\n")
        stream.flush()
    except (OSError, ValueError):
        pass


def setup_logging(args: argparse.Namespace) -> None:
    root = logging.getLogger("rumbapy")
    root.handlers.clear()
    level = logging.getLevelName(str(args.log_level).upper())
    root.setLevel(level if isinstance(level, int) else logging.INFO)
    root.propagate = False
    fmt = logging.Formatter(LOG_FORMAT.format(letter=args.letter), _log.DATEFMT)
    if args.log:
        handler: logging.Handler = _log.SafeRotatingFileHandler(args.log)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    if sys.stderr is not None:                       # boot log (stderr = logs\boot\...txt): avisos e erros
        err = logging.StreamHandler(sys.stderr)
        err.setLevel(logging.WARNING)
        err.setFormatter(fmt)
        root.addHandler(err)
    threading.current_thread().name = "main"


def _fake_window_pid(dll: Any, hwnd: int) -> int:
    """No modo ``--fake-dll`` o HWND é falso: o "dono" é o PID da ``FakeSession`` (nunca uma janela real)."""
    inner = getattr(dll, "_dll", dll)
    for session in getattr(inner, "sessions", {}).values():
        if getattr(session, "hwnd", None) == hwnd:
            return int(getattr(session, "pid", 0) or 0)
    return 0


def main(argv: list[str]) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return _protocol.EXIT_UNHANDLED if exc.code else _protocol.EXIT_CLEAN
    # 1) Python adequado
    is64 = struct.calcsize("P") != 4
    if (is64 and not (args.allow_64 and args.fake_dll)) or sys.version_info < (3, 12):
        _stderr(f"Python inadequado: {platform.python_version()} {'64' if is64 else '32'}-bit "
                "(o worker exige 32-bit >= 3.12)")
        return _protocol.EXIT_PYTHON
    # 2) logging
    setup_logging(args)
    sandbox = os.environ.get("RUMBAPY_SANDBOX") == "1"
    logger.info("start pid=%d python=%s letter=%s guard=%s commit=%s fake=%s", os.getpid(),
                platform.python_version(), args.letter, args.guard, args.commit, bool(args.fake_dll))
    if args.fake_path and not args.fake_dll:
        logger.error("--fake-path só vale junto com --fake-dll")
        return _protocol.EXIT_UNHANDLED
    if args.fake_dll and not sandbox:
        # 1 (não 17): no cliente 17 significa "sandbox com a DLL real" (SandboxViolation), o caso oposto (D.2)
        logger.error("--fake-dll só é aceito com RUMBAPY_SANDBOX=1 (linha de comando inválida)")
        _stderr("--fake-dll só é aceito com RUMBAPY_SANDBOX=1")
        return _protocol.EXIT_UNHANDLED
    try:
        return _boot(args, sandbox)
    except Exception as exc:
        logger.critical("exceção não tratada no boot (%s)", type(exc).__name__, exc_info=True)
        _log.flush_all()
        return _protocol.EXIT_UNHANDLED


def _boot(args: argparse.Namespace, sandbox: bool) -> int:
    # 3) todos os módulos do worker + build_id
    for name in WORKER_MODULES:
        importlib.import_module(f"{__package__}.{name}")
    own = _buildid.compute(PKGDIR)
    if own != args.expect_build:
        logger.error("build divergente: em disco %s, esperado %s", own, args.expect_build)
        return _protocol.EXIT_BUILD
    # 4) chave
    try:
        key = _ipc.read_key(args.keyfile)
    except (OSError, ValueError) as exc:
        logger.error("chave inválida (%s)", type(exc).__name__)
        return _protocol.EXIT_KEY
    # 5) pipe reservado ANTES da DLL (E20/I37)
    try:
        security = _win32.pipe_security_attributes()
    except OSError as exc:
        logger.error("não consegui montar a DACL do pipe (%s)", exc)
        return _protocol.EXIT_DACL
    try:
        first = _ipc.create_pipe(args.pipe, first=True, security=security)
    except _ipc.PipeExists:
        logger.error("o pipe já existe (outro processo auxiliar)")
        return _protocol.EXIT_PIPE_EXISTS
    except OSError as exc:
        logger.error("CreateNamedPipe falhou (%s)", exc)
        return _protocol.EXIT_DACL
    # 6) colisão de identidade HLLAPI (R10)
    try:
        pids = _win32.hllapi_client_pids()
    except OSError as exc:
        logger.warning("não consegui listar os clientes HLLAPI (%s)", type(exc).__name__)
        pids = []
    if collides(os.getpid(), pids):
        logger.error("colisão de LOWORD(pid) com outro cliente HLLAPI")
        return _protocol.EXIT_PID_COLLISION
    # 7) sandbox / ao vivo
    if sandbox and not args.fake_dll:
        logger.error("RUMBAPY_SANDBOX=1 sem --fake-dll: a DLL real não é carregada")
        return _protocol.EXIT_SANDBOX
    if os.environ.get("RUMBAPY_LIVE") == "1" and args.guard == "none":
        logger.error("RUMBAPY_LIVE=1 exige --guard")
        return _protocol.EXIT_LIVE_WITHOUT_GUARD
    if args.fake_path:
        sys.path.insert(0, args.fake_path)
    from .hllapi import load_dll
    from .server import Config, WorkerServer

    config = Config(letter=args.letter, pipe=args.pipe, key=key, registry=Path(args.registry), build_id=own,
                    pkgdir=str(PKGDIR), stop_event=args.stop_event, log_path=args.log, idle=args.idle,
                    guard=args.guard, commit=None if args.commit == "none" else args.commit,
                    python=sys.executable)
    holder: dict[str, WorkerServer] = {}
    fake = bool(args.fake_dll)
    server = WorkerServer(
        config, dll_loader=lambda: load_dll(args.dll, fake=args.fake_dll, guard=args.guard), first_pipe=first,
        security=security,
        is_window=(lambda hwnd: True) if fake else _win32.is_window,            # HWNDs falsos no modo fake
        dialog_visible=(lambda pid: False) if fake else _win32.hllapi_error_dialog_visible,
        window_pid=(lambda hwnd: _fake_window_pid(holder["s"].dll, hwnd)) if fake else _win32.window_thread_pid)
    holder["s"] = server
    # 8) T-dll carrega a DLL
    error = server.start_dll()
    if error is not None:
        logger.error("a DLL não carregou (%s)", type(error).__name__)
        return _protocol.EXIT_DLL
    # 9) evento de parada + registro; 10) T-watch e T-accept
    if args.stop_event:
        handle = _win32.open_event(args.stop_event, create=True)
        if _win32.event_is_set(handle):
            _win32.reset_event(handle)
        server.stop_handle = handle
    server.start_service()
    return server.wait()
