"""Ponte cliente 64-bit -> worker 32-bit (C.3 lado cliente, C.7, C.8, D.1, D.8): spawn, handshake e canais.

* ``BOOT`` + ``spawn``: monta a linha de comando da D.1 (``-I -S``, sem rodar ``rumbapy/__init__.py``) e lança o
  worker com o Python 32-bit **real** de ``_runtime32`` (E14). Com ``RUMBAPY_SANDBOX=1`` só sobe worker com
  ``--fake-dll`` (``RUMBAPY_TEST_FAKE_DLL``); a DLL real nunca é pedida.
* ``handshake``: ordem de decisão da C.3 (o dono vem antes do build, R1/E2).
* ``Bridge.get_handle(letra, modo)``: máquina de estados da D.8 sob ``locks\\spawn-<tag>-<L>.lock``; devolve um
  ``WorkerHandle`` (canal autenticado + lease, se ``owner``). Os handles ``owner`` ficam em cache por letra e são
  compartilhados pelas ``Session`` do processo (C.6.6), com contagem de usuários (o último ``release`` fecha).
* ``get_worker(letra, modo)``: executa a D.8 na hora (``TerminalInUse`` etc. aparecem aqui, E.5/E.6) e devolve o
  ``WorkerBackend`` (``_backend.py``) amarrado ao handle — é o ``Backend`` que o ``_launcher`` usa.
* ``WorkerHandle.request``: uma requisição por vez (trava por handle), prazo do cliente com conferência do PID a cada
  1 s, eventos, Ctrl+C fecha a conexão (C.7) e erros de transporte viram ``WorkerCrashed``/``WorkerHung``/
  ``ProtocolError`` (C.8). A reciclagem (repetir op idempotente 1x) é do ``WorkerBackend``.

As exceções internas do transporte (``_ipc.PipeNotFound``/``PipeTimeout``/``AuthRejected``, A.9 e) nunca saem daqui.
Este módulo não conhece telas, janelas nem receitas (A.3). Nunca mata processo: espera a saída em fatias de 100 ms.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import (ChannelHijacked, ClientTableFull, ParameterError, ProtocolError, Python32NotFound,
                     RumbaError, RumbaNotInstalled, SandboxViolation, SessionReplaced, SessionStopped,
                     TerminalInUse, UpdateRequired, WorkerCrashed, WorkerError, WorkerHung, WorkerStartFailed)
from . import _buildid, _ipc, _locks, _paths, _protocol, _runtime32, _version, _win32

import subprocess
import threading
import logging
import time
import math
import sys
import os

if TYPE_CHECKING:
    from ._backend import WorkerBackend

logger = logging.getLogger(__name__)

# D.1: uma linha, sem barra invertida (E19); não executa rumbapy/__init__.py (I12). Teste de contrato confere o texto.
BOOT = ('import os,sys,types; d=sys.argv[1]; m=types.ModuleType("rumbapy"); m.__path__=[d]; '
        'm.__file__=os.path.join(d,"__init__.py"); sys.modules["rumbapy"]=m; '
        'from rumbapy._worker.main import main; sys.exit(main(sys.argv[2:]))')
PKGDIR = Path(__file__).resolve().parent
DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"   # == _worker.hllapi.DLL_PATH (teste)

SPAWN_LOCK_S = 30.0               # D.8: uma rodada de espera pela trava de spawn
SPAWN_READY_S = 20.0              # espera do registro do worker recém-lançado
EXIT_WAIT_S = 10.0                # stopping / shutdown / evento de parada: espera o PID sair
HUNG_EXIT_WAIT_S = 30.0           # C.7: depois de WorkerHung, espera o PID sair
SLICE_S = 0.1
ALIVE_CHECK_S = 1.0               # C.7: confere se o PID do worker vive a cada 1 s
HELLO_TIMEOUT = _protocol.HELLO_ACK_TIMEOUT
MAX_SPAWNS = 2
MAX_COLLISION_SPAWNS = 3          # +3 só para a saída 15 (colisão de LOWORD(pid))
MAX_STOPS = 1
MAX_STOPPING_WAITS = 3
SELF_OWNER_RETRY_S = 1.0          # pipe mudo com o lease deste mesmo processo: espera e tenta 1x antes de desistir
# Quem segura a trava de spawn não a solta nas esperas (soltar no meio do boot deixaria outro cliente subir um 2º
# worker da letra); o pior caso de quem segura é limitado: spawns (20 s cada) + esperas de saída (10 s cada).
# Quem espera a trava espera em rodadas de SPAWN_LOCK_S até esse pior caso + folga (A.12 d).
SPAWN_LOCK_MAX_S = (SPAWN_READY_S * (MAX_SPAWNS + MAX_COLLISION_SPAWNS)
                    + EXIT_WAIT_S * (MAX_STOPPING_WAITS + MAX_STOPS) + 10.0)
BOOT_TAIL_LINES = 20
IDLE_S = 1800.0
SCRIPT_MAX = 200
CREATE_NO_WINDOW = _win32.CREATE_NO_WINDOW
CREATE_NEW_PROCESS_GROUP = _win32.CREATE_NEW_PROCESS_GROUP
CREATE_BREAKAWAY_FROM_JOB = _win32.CREATE_BREAKAWAY_FROM_JOB
ERROR_ACCESS_DENIED = 5
HUNG_FUNCTION = "uma chamada da DLL"   # WorkerHung sem a função exata (exit_reason lido do registro)
WATCHDOG_LIMIT_S = 20.0           # D.4: limite das chamadas comuns
CONNECT_LIMIT_S = 15.0            # D.4: WD_ConnectPS
STOP_LIMIT_S = 5.0                # D.4: WD_DisconnectPS/WD_ResetSystem no encerramento (stop_while_hung)
STOP_FUNCTION = "o encerramento (WD_DisconnectPS/WD_ResetSystem)"
POLL_S = 0.1                      # C.7: espera de resposta em laço poll(0.1)
RECONNECT_S = 15.0                # C.8: connect em laço depois de reciclar (tolera rc 1 e rc 11)
RECONNECT_POLL_S = 0.5
INPUT_OPS = frozenset({"write", "send", "set_cursor"})   # ops N que mexem na entrada (variante "input" das mensagens)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

_INTERNAL_IPC = (_ipc.PipeNotFound, _ipc.PipeTimeout, _ipc.AuthRejected)


@contextmanager
def held(lock: Any) -> Iterator[None]:
    """Adquire ``lock`` em fatias de 100 ms: no Windows um ``acquire`` bloqueante não é interrompido por Ctrl+C
    (D.8, E27). Serve para ``Lock`` e ``RLock``."""
    while not lock.acquire(timeout=SLICE_S):
        pass
    try:
        yield
    finally:
        lock.release()


# ------------------------------------------------------------------------------------------- identidade do cliente

_build_cache: dict[str, str] = {}


def build_id() -> str:
    """``BUILD_ID`` calculado pelo ``import rumbapy`` (A.6); sem ele (uso interno), calculado uma vez agora."""
    pkg = sys.modules.get(__package__ or "rumbapy")
    value = getattr(pkg, "BUILD_ID", None)
    if isinstance(value, str) and value:
        return value
    cached = _build_cache.get("id")
    if cached is None:
        cached = _build_cache["id"] = disk_build(PKGDIR)
    return cached


def disk_build(pkgdir: str | os.PathLike[str], letter: str | None = None) -> str:
    """``_buildid.compute(pkgdir)`` com a falha de disco traduzida (C.5: só ``RumbaError`` sai daqui). Um ``OSError``
    aqui (arquivo do pacote trocado no meio de um update, antivírus) nunca é confundido com falha do canal (C.8)."""
    try:
        return _buildid.compute(pkgdir)
    except OSError as exc:
        raise WorkerError(f"Não consegui ler os arquivos do pacote rumbapy para conferir a versão "
                          f"({type(exc).__name__}).",
                          hint="se uma atualização estiver em curso, espere ela terminar e rode de novo",
                          terminal=letter, details={"error": type(exc).__name__,
                                                    "winerror": getattr(exc, "winerror", None)}) from None


def script_name() -> str:
    """Nome do script do usuário (vai no ``hello_ack`` e nas mensagens de ``TerminalInUse``)."""
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    name = os.path.basename(argv0) if argv0 and argv0 != "-c" else ""
    return (name or "<interativo>")[:SCRIPT_MAX]


def installed_commit() -> str | None:
    """Commit instalado segundo o último ``update-state.json`` (só informativo: vai para o log do worker)."""
    try:
        state = _paths.read_json(_paths.update_state_path())
    except Exception:
        return None
    local = state.get("local") if state else None
    if isinstance(local, str) and len(local) == 40 and all(c in "0123456789abcdef" for c in local):
        return local
    return None


def _hour(since: Any) -> str:
    try:
        return datetime.fromisoformat(str(since)).strftime("%H:%M:%S")
    except ValueError:
        return str(since or "?")


def in_use(letter: str, owner: Mapping[str, Any] | None) -> TerminalInUse:
    """``TerminalInUse`` (lease) com o PID/script/hora do dono (B.7)."""
    owner = owner or {}
    return TerminalInUse.make("lease", L=letter, pid=owner.get("pid", "?"), script=owner.get("script") or "?",
                              hora=_hour(owner.get("since")), terminal=letter,
                              details={"owner_pid": owner.get("pid")})


# ------------------------------------------------------------------------------------------- configuração do spawn

@dataclass(frozen=True)
class SpawnConfig:
    """Como lançar o worker. ``from_env`` lê ``RUMBAPY_GUARD`` (J.6) e, só com ``RUMBAPY_SANDBOX=1``, o dublê
    ``RUMBAPY_TEST_FAKE_DLL`` (``<modulo>:<fabrica>``), ``RUMBAPY_TEST_FAKE_PATH`` e ``RUMBAPY_TEST_ALLOW_64``."""

    python32: str | None = None
    guard: str = "none"
    fake_dll: str | None = None
    fake_path: str | None = None
    allow_64: bool = False
    log_level: str = "INFO"
    idle: float = IDLE_S
    dll: str = DLL_PATH

    @classmethod
    def from_env(cls, python32: str | os.PathLike[str] | None = None) -> SpawnConfig:
        guard = (os.environ.get("RUMBAPY_GUARD") or "none").strip().lower()
        if guard not in _protocol.GUARD_MODES:
            raise ParameterError.make(detalhe=f"RUMBAPY_GUARD inválido: {guard!r} "
                                              f"(use {', '.join(_protocol.GUARD_MODES)}).")
        fake = fake_path = None
        allow_64 = False
        if _paths.sandboxed():
            fake = os.environ.get("RUMBAPY_TEST_FAKE_DLL") or None
            fake_path = os.environ.get("RUMBAPY_TEST_FAKE_PATH") or None
            allow_64 = os.environ.get("RUMBAPY_TEST_ALLOW_64") == "1"
            if fake:
                logger.info("spawn: sandbox com dublê da DLL %s", fake)
        level = log_level_from_env()
        return cls(python32=os.fspath(python32) if python32 is not None else None, guard=guard, fake_dll=fake,
                   fake_path=fake_path, allow_64=allow_64, log_level=level)


def log_level_from_env() -> str:
    """``RUMBAPY_LOG_LEVEL`` (diagnóstico; fora da especificação, registrado como desvio): nível do log do worker.
    Só ``DEBUG``/``INFO``/``WARNING``/``ERROR``; outro valor gera WARNING e volta a ``INFO``."""
    raw = os.environ.get("RUMBAPY_LOG_LEVEL")
    level = (raw or "INFO").strip().upper()
    if level not in LOG_LEVELS:
        logger.warning("RUMBAPY_LOG_LEVEL inválido (%r); usando INFO (valores: %s)", raw, ", ".join(LOG_LEVELS))
        return "INFO"
    return level


def worker_env() -> dict[str, str]:
    """D.1: ``os.environ`` sem ``PYTHON*``/``RUMBAPY_*``, mais ``RUMBAPY_SANDBOX``/``LIVE``/``HOME`` (absoluto)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    for name in ("RUMBAPY_SANDBOX", "RUMBAPY_LIVE"):
        if name in os.environ:
            env[name] = os.environ[name]
    if os.environ.get("RUMBAPY_HOME"):
        env["RUMBAPY_HOME"] = str(_paths.home())
    return env


def worker_argv(python: str, letter: str, pipe: str, config: SpawnConfig, *, pkgdir: Path = PKGDIR,
                expect_build: str | None = None, commit: str | None = None) -> list[str]:
    """Linha de comando da D.1 (``--dll`` só sem dublê: no modo ``--fake-dll`` a DLL real nem é citada)."""
    argv = [python, "-I", "-S", "-X", "faulthandler", "-c", BOOT, str(pkgdir),
            "--letter", letter, "--pipe", pipe, "--keyfile", str(_paths.authkey_path()),
            "--stop-event", _paths.stop_event_name(letter), "--registry", str(_paths.worker_registry(letter)),
            "--log", str(_paths.worker_log_path(letter)), "--log-level", config.log_level,
            "--idle", f"{config.idle:g}", "--expect-build", expect_build or build_id(),
            "--commit", commit or "none"]
    if config.fake_dll:
        argv += ["--fake-dll", config.fake_dll]
        if config.fake_path:
            argv += ["--fake-path", config.fake_path]
        if config.allow_64:
            argv += ["--allow-64"]
    else:
        argv += ["--dll", config.dll]
    if config.guard != "none":
        argv += ["--guard", config.guard]
    return argv


@dataclass
class Spawned:
    proc: Any                     # subprocess.Popen (ou dublê com .pid/.poll())
    pipe: str
    boot: Path
    python: str


def spawn(letter: str, config: SpawnConfig, *, pkgdir: Path = PKGDIR, expect_build: str | None = None,
          popen: Callable[..., Any] = subprocess.Popen) -> Spawned:
    """Lança um worker (D.1). Não espera: quem chama acompanha o registro (D.8)."""
    if _paths.sandboxed() and not config.fake_dll:
        raise SandboxViolation.make(acao="processo auxiliar com a DLL real (defina RUMBAPY_TEST_FAKE_DLL)")
    runtime = _runtime32.find(config.python32)
    pipe = _paths.pipe_name(letter, _paths.new_pipe_rand())
    argv = worker_argv(runtime.real, letter, pipe, config, pkgdir=pkgdir, expect_build=expect_build,
                       commit=installed_commit())
    home = _paths.home()
    boot = _paths.boot_log_path(letter)
    try:
        _paths.ensure_dir(boot.parent)
        _paths.ensure_dir(_paths.run_dir())
        _paths.ensure_dir(_paths.logs_dir())
        err = open(boot, "wb")
    except OSError as exc:                       # pasta de dados, não o Python: mensagem própria (C.5)
        raise WorkerError(f"Não consegui preparar a pasta de dados do processo auxiliar do terminal {letter} "
                          f"({type(exc).__name__}).", terminal=letter,
                          details={"error": type(exc).__name__, "winerror": getattr(exc, "winerror", None),
                                   "path": str(boot.parent)}) from None
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    with err:
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": err,
                                  "cwd": str(home), "env": worker_env(), "close_fds": True}
        try:
            try:
                proc = popen(argv, creationflags=flags, **kwargs)
            except OSError as exc:
                if getattr(exc, "winerror", None) != ERROR_ACCESS_DENIED:
                    raise
                logger.warning("spawn: breakaway negado; o processo auxiliar pode ser morto à força quando a IDE "
                               "fechar")
                proc = popen(argv, creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError as exc:                   # só o Popen chega aqui
            code = getattr(exc, "winerror", None) or exc.errno
            raise start_failed(letter, f"não consegui executar o Python 32-bit ({type(exc).__name__}"
                                       f"{f', erro {code}' if code else ''}).",
                               details={"error": type(exc).__name__, "winerror": code}) from None
    try:
        _paths.prune_boot_logs(letter)
    except OSError as exc:                       # limpeza: nunca derruba um spawn que já deu certo
        logger.warning("spawn: não consegui apagar logs de boot antigos (%s)", type(exc).__name__)
    logger.info("spawn letter=%s pid=%s python=%s fake=%s guard=%s", letter, getattr(proc, "pid", "?"),
                runtime.version, bool(config.fake_dll), config.guard)
    return Spawned(proc, pipe, boot, runtime.real)


def start_failed(letter: str, motivo: str, *, tail: str = "",
                 details: dict[str, Any] | None = None) -> WorkerStartFailed:
    """``WorkerStartFailed`` que **não** vem de um código de saída (desistência, ``Popen`` falhou, não ficou pronto).

    O modelo ``default`` da B.7 diz "(código {n})", que aqui induziria a erro: mesmo começo de frase, motivo no lugar
    do código (A.12 f; variantes próprias pendentes no ``errors.py``)."""
    message = f"O processo auxiliar 32-bit do terminal {letter} não iniciou: {motivo}"
    if tail:
        message = f"{message} {tail}"
    return WorkerStartFailed(message, terminal=letter, details=details)


def boot_tail(path: Path | None, lines: int = BOOT_TAIL_LINES) -> str:
    """Últimas linhas do stderr do worker (vão na mensagem de ``WorkerStartFailed``)."""
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-lines:]
    return ("Fim do log de boot:\n" + "\n".join(tail)) if tail else ""


def hung_limit(reason: str, op: str | None, reg: Mapping[str, Any] | None = None) -> tuple[str, float]:
    """``(função, limite)`` da mensagem de ``WorkerHung`` por ``exit_reason`` (D.4): o registro pode trazer
    ``hung_function``/``hung_limit``; senão ``stop_while_hung`` = encerramento (5 s), ``connect`` = ``WD_ConnectPS``
    (15 s) e o resto = limite comum (20 s)."""
    reg = reg or {}
    func, limit = reg.get("hung_function"), reg.get("hung_limit")
    if isinstance(func, str) and func and isinstance(limit, (int, float)) and not isinstance(limit, bool):
        return func[:80], float(limit)
    if reason == "stop_while_hung":
        return STOP_FUNCTION, STOP_LIMIT_S
    if op == "connect":
        return "WD_ConnectPS", CONNECT_LIMIT_S
    return HUNG_FUNCTION, WATCHDOG_LIMIT_S


def reason_error(reason: str | None, letter: str, *, op: str | None = None, delivery: Any = None,
                 variant: str = "default", reg: Mapping[str, Any] | None = None,
                 details: dict[str, Any] | None = None) -> RumbaError | None:
    """``exit_reason`` do registro (saída 3, D.2/D.4) -> exceção; ``None`` se o motivo não for conhecido."""
    if reason == "window_gone":
        return SessionStopped.make(L=letter, delivery=delivery, terminal=letter, details=details)
    if reason == "client_table_full":
        return ClientTableFull.make(delivery=delivery, terminal=letter, details=details)
    if reason in ("hung", "stop_while_hung"):
        func, limit = hung_limit(reason, op, reg)
        return WorkerHung.make(variant, funcao=func, s=limit, delivery=delivery, terminal=letter, details=details)
    return None


def exit_error(code: int, letter: str, *, boot: Path | None = None, dll: str = DLL_PATH,
               reason: str | None = None, reg: Mapping[str, Any] | None = None) -> RumbaError:
    """Código de saída do worker no boot (D.2) -> exceção para o usuário. 11/13/15 são tratados antes (D.8).
    Saída 3 usa o ``exit_reason`` (``reason``) do registro, como a tabela da D.2."""
    if code == _protocol.EXIT_WATCHDOG:
        err = reason_error(reason, letter, reg=reg, details={"exit_code": code, "exit_reason": reason})
        if err is not None:
            return err
    if code == _protocol.EXIT_DLL:
        return RumbaNotInstalled.make(o_que="ehlapi32.Dll", caminho=dll, terminal=letter)
    if code == _protocol.EXIT_PYTHON:
        return Python32NotFound.make(motivo="o processo auxiliar recusou o Python escolhido (precisa ser 32-bit "
                                            ">= 3.12)", terminal=letter)
    if code == _protocol.EXIT_BUILD:
        return UpdateRequired.make("other_process", terminal=letter)
    if code == _protocol.EXIT_LIVE_WITHOUT_GUARD:
        return WorkerStartFailed.make("live_without_guard", terminal=letter)
    if code == _protocol.EXIT_SANDBOX:
        return SandboxViolation.make(acao="processo auxiliar com a DLL real", terminal=letter)
    return WorkerStartFailed.make(L=letter, n=code, fim_do_boot=boot_tail(boot), terminal=letter,
                                  details={"exit_code": code})


# ------------------------------------------------------------------------------------------- processos

def process_alive(pid: Any, created: Any = None) -> bool:
    """PID vivo e, se ``created`` vier, o mesmo processo (PID reutilizado com outro ``created`` = morto, R4f)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not isinstance(created, int) or isinstance(created, bool):
        created = None
    try:
        return bool(_win32.process_alive(pid, created))
    except OSError:
        return False


def wait_exit(pid: Any, created: Any, timeout: float, *, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], object] = time.sleep) -> bool:
    """Espera o processo sair em fatias de 100 ms (Ctrl+C funciona, E27). ``True`` = saiu. Nunca mata."""
    deadline = clock() + max(0.0, timeout)
    while process_alive(pid, created):
        if clock() >= deadline:
            return False
        sleep(SLICE_S)
    return True


def registry_alive(reg: Mapping[str, Any] | None) -> bool:
    return bool(reg) and isinstance(reg.get("pipe"), str) and process_alive(reg.get("pid"), reg.get("created"))


def _is_transport(exc: BaseException) -> bool:
    """C.8: só erros do transporte (EOF, pipe quebrado, ``OSError`` que não é ``RumbaError``)."""
    return isinstance(exc, (EOFError, BrokenPipeError, ConnectionError, OSError)) and not isinstance(exc, RumbaError)


# ------------------------------------------------------------------------------------------- handshake (C.3)

def handshake(channel: Any, *, letter: str, mode: str, build: str, pkgdir: str | os.PathLike[str] = PKGDIR,
              expected_pid: int | None = None) -> tuple[str, dict[str, Any]]:
    """Ordem de decisão da C.3 (o dono vem antes do build). Devolve ``(resultado, hello)``:

    ``"ok"`` (``welcome``: o canal está pronto), ``"stopping"`` (o worker está parando: espere o PID sair) ou
    ``"old_build"`` (worker de build antigo **sem dono**: pare-o por ``shutdown`` e suba outro). Dono vivo diferente ->
    ``TerminalInUse`` (o worker nunca é parado); protocolo novo ou build novo em disco -> ``UpdateRequired``.
    """
    hello = channel.recv_msg(HELLO_TIMEOUT)
    if not isinstance(hello, dict) or hello.get("t") != "hello":
        raise ProtocolError.make(L=letter, detalhe="o processo auxiliar não mandou o hello", terminal=letter)
    proto = hello.get("protocol")
    if proto != _protocol.PROTOCOL:                                              # 1.
        if isinstance(proto, int) and not isinstance(proto, bool) and proto > _protocol.PROTOCOL:
            raise UpdateRequired.make("other_process", terminal=letter)
        raise ProtocolError.make(L=letter, detalhe=f"protocolo {proto!r} desconhecido", terminal=letter)
    if expected_pid is not None and hello.get("pid") != expected_pid:
        raise ChannelHijacked.make(pid=hello.get("pid"), L=letter, terminal=letter)
    if hello.get("state") == "stopping":                                         # 2.
        return "stopping", hello
    if mode == "owner":
        owner = hello.get("owner")
        if isinstance(owner, dict) and owner.get("pid") != os.getpid():         # 3.
            raise in_use(letter, owner)
        if hello.get("build_id") != build:                                       # 4.
            if disk_build(pkgdir, letter) != build:          # OSError do disco -> WorkerError (não é canal)
                raise UpdateRequired.make("other_process", terminal=letter)
            return "old_build", hello
    channel.send_msg({"t": "hello_ack", "mode": mode, "client_pid": os.getpid(),        # 5.
                      "client_version": _version.__version__, "build_id": build, "script": script_name()})
    answer = channel.recv_msg(HELLO_TIMEOUT)
    kind = answer.get("t") if isinstance(answer, dict) else None
    if kind == "welcome":
        return "ok", hello
    if kind == "reject":
        reason = answer.get("reason") if isinstance(answer, dict) else None
        if reason == "busy":
            raise in_use(letter, answer.get("owner") if isinstance(answer.get("owner"), dict) else None)
        if reason == "build":
            return "old_build", hello
        if reason == "stopping":
            return "stopping", hello
    raise ProtocolError.make(L=letter, detalhe="resposta inválida ao hello_ack", terminal=letter)


# ------------------------------------------------------------------------------------------- handle (C.6.6, C.7, C.8)

class WorkerHandle:
    """Canal autenticado com um worker (``owner`` = segura o lease). Uma requisição por vez (trava por handle)."""

    def __init__(self, channel: Any, *, letter: str, mode: str, pid: int, created: int | None,
                 hello: Mapping[str, Any], registry: Path | None = None) -> None:
        self.channel = channel
        self.letter = letter
        self.mode = mode
        self.pid = pid
        self.created = created
        self.hello = dict(hello)
        self.registry = registry
        self.lock = threading.Lock()
        self.next_id = 1
        self.evicted = False
        self.dead = False
        self.exiting = False                # o worker anunciou que vai sair (shutting_down, WorkerHung, exit_reason)
        self.users = 0                      # WorkerBackend que seguram este handle (o último release fecha)

    def __repr__(self) -> str:
        state = "morto" if self.dead else ("despejado" if self.evicted else "vivo")
        return f"<WorkerHandle {self.letter} {self.mode} pid={self.pid} {state}>"

    @property
    def alive(self) -> bool:
        return not self.dead and not getattr(self.channel, "closed", False) and process_alive(self.pid, self.created)

    def close(self) -> None:
        """Fecha a conexão (fim do lease se ``owner``, C.6.4). Idempotente."""
        self.dead = True
        try:
            self.channel.close()
        except Exception as exc:            # fechar nunca falha para quem chama
            logger.debug("handle %s: erro ao fechar (%s)", self.letter, type(exc).__name__)

    def wait_gone(self, timeout: float = HUNG_EXIT_WAIT_S, *, clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], object] = time.sleep) -> None:
        """Se o worker anunciou a saída, espera o PID sumir antes de reciclar (senão o "novo" seria o moribundo).
        Não saiu no prazo -> ``WorkerError`` (C.7); nunca ``TerminateProcess``."""
        if not self.exiting:
            return
        if not wait_exit(self.pid, self.created, timeout, clock=clock, sleep=sleep):
            raise WorkerError.make("not_exited", L=self.letter, pid=self.pid, terminal=self.letter)

    # ----------------------------------------------------------------- erros de transporte
    def _exit_info(self) -> tuple[str | None, dict[str, Any] | None]:
        """``(exit_reason, registro)`` do registro deste worker (outro PID no registro -> ``(None, None)``)."""
        path = self.registry if self.registry is not None else _paths.worker_registry(self.letter)
        reg = _paths.read_json(path)
        if reg and reg.get("pid") == self.pid:
            reason = reg.get("exit_reason")
            return (reason if isinstance(reason, str) else None), reg
        return None, None

    def _exit_reason(self) -> str | None:
        return self._exit_info()[0]

    def crash_error(self, op: str, *, sent: bool = True) -> RumbaError:
        """O worker sumiu durante ``op`` (C.8): tipo pelo ``exit_reason`` do registro; op N de entrada já enviada ->
        ``delivery=unknown``. ``sent=False`` (o handle já estava morto antes do envio): ``delivery=none``."""
        uncertain = sent and op in INPUT_OPS                # op N de entrada enviada: não sei se chegou ao host
        delivery = "unknown" if uncertain else ("none" if op in INPUT_OPS else None)
        variant = "input" if uncertain else "default"
        reason, reg = self._exit_info()
        if reason:
            self.exiting = True
        L = self.letter
        details = {"op": op, "exit_reason": reason, "worker_pid": self.pid}
        err = reason_error(reason, L, op=op, delivery=delivery, variant=variant, reg=reg, details=details)
        if err is not None:
            return err
        return WorkerCrashed.make(variant, L=L, delivery=delivery, terminal=L, details=details)

    def _crashed(self, op: str, exc: BaseException | None = None) -> RumbaError:
        self.close()
        err = self.crash_error(op)
        logger.warning("handle %s: o processo auxiliar (PID %d) caiu durante op=%s (%s) -> %s", self.letter,
                       self.pid, op, type(exc).__name__ if exc is not None else "PID morto", type(err).__name__)
        return err

    def _hung(self, op: str, limit: float, *, clock: Callable[[], float],
              sleep: Callable[[float], object]) -> RumbaError:
        """Estourou o prazo do cliente (C.7): fecha, espera o PID sair até 30 s; nunca ``TerminateProcess``."""
        self.close()
        logger.warning("handle %s: sem resposta para op=%s em %.0f s; esperando o PID %d sair", self.letter, op,
                       limit, self.pid)
        delivery = "unknown" if op in INPUT_OPS else None    # a requisição já foi enviada (C.8, B.7)
        details = {"op": op, "client_timeout": True, "worker_pid": self.pid}
        if not wait_exit(self.pid, self.created, HUNG_EXIT_WAIT_S, clock=clock, sleep=sleep):
            return WorkerError.make("not_exited", L=self.letter, pid=self.pid, terminal=self.letter,
                                    delivery=delivery, details=details)
        return WorkerHung.make("input" if op in INPUT_OPS else "default", funcao=f"'{op}'", s=limit,
                               delivery=delivery, terminal=self.letter, details=details)

    def _bad_message(self, op: str, exc: ProtocolError) -> ProtocolError:
        """``ProtocolError`` local (JSON inválido, > 1 MiB, id inesperado) depois do envio: fecha; numa op N de
        entrada a requisição pode ter sido executada -> ``delivery=unknown`` (C.8). Quem chama não repete op N."""
        self.close()
        if op in INPUT_OPS and exc.delivery is None:
            exc.delivery = "unknown"
            exc.details = {**(exc.details or {}), "op": op}
        return exc

    def _on_event(self, msg: Mapping[str, Any]) -> None:
        event, reason = msg.get("event"), msg.get("reason")
        logger.info("handle %s: evento %s reason=%s", self.letter, event, reason)
        if event == "evicted":
            self.evicted = True
        elif event == "shutting_down":
            self.dead = True
            self.exiting = True

    # ----------------------------------------------------------------- requisição
    def request(self, op: str, args: Mapping[str, Any] | None = None, *, hwnd: int | None = None,
                timeout: float | None = None, clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], object] = time.sleep) -> dict[str, Any]:
        """Uma requisição e sua resposta (C.3/C.7/C.8). Erros do worker sobem como ele mandou (``from_wire``)."""
        if op not in _protocol.OPS:
            raise ParameterError.make(detalhe=f"Operação desconhecida: {op!r}.")
        while not self.lock.acquire(timeout=POLL_S):        # outra thread do processo está usando este handle
            if self.dead:
                raise self.crash_error(op, sent=False)      # nada foi enviado: delivery=none
        try:
            if self.evicted and op not in _protocol.ADMIN_OPS:
                raise SessionReplaced.make(L=self.letter, terminal=self.letter)
            if self.dead:
                raise self.crash_error(op, sent=False)
            rid = self.next_id
            self.next_id += 1
            limit = timeout if timeout is not None else _protocol.client_timeout(op, args)
            msg = _protocol.make_request(rid, op, args, hwnd)
            try:
                self.channel.send_msg(msg)
            except BaseException as exc:
                if _is_transport(exc):
                    raise self._crashed(op, exc) from None
                raise
            return self._await(op, rid, limit, clock=clock, sleep=sleep)
        except KeyboardInterrupt:
            # C.7: fecha a conexão (o worker cancela os jobs dela); a próxima op reabre e retoma o lease
            logger.info("handle %s: Ctrl+C durante op=%s; conexão fechada", self.letter, op)
            self.close()
            raise
        finally:
            self.lock.release()

    def _await(self, op: str, rid: int, limit: float, *, clock: Callable[[], float],
               sleep: Callable[[float], object]) -> dict[str, Any]:
        start = clock()
        deadline = start + limit
        next_alive = start + ALIVE_CHECK_S
        while True:
            try:
                reply = self.channel.recv_msg(POLL_S)
            except ProtocolError as exc:                     # JSON inválido / > 1 MiB: fecha; quem chama recicla
                raise self._bad_message(op, exc) from None
            except BaseException as exc:
                if _is_transport(exc):
                    raise self._crashed(op, exc) from None
                raise
            if reply is None:
                now = clock()
                if now >= deadline:
                    raise self._hung(op, limit, clock=clock, sleep=sleep)
                if now >= next_alive:
                    next_alive = now + ALIVE_CHECK_S
                    if not process_alive(self.pid, self.created):
                        raise self._crashed(op)
                continue
            if _protocol.is_event(reply):
                self._on_event(reply)
                if self.dead:                                # shutting_down: a requisição em curso não terá resposta
                    raise self._crashed(op)
                continue
            try:
                result = _protocol.unwrap_reply(reply, rid, letter=self.letter)
            except ProtocolError as exc:
                if isinstance(reply, Mapping) and reply.get("ok") is False and reply.get("id") == rid:
                    self.close()                             # erro mandado pelo próprio worker: sobe como veio
                    raise
                raise self._bad_message(op, exc) from None
            except (WorkerHung, ClientTableFull):
                self.exiting = True                          # watchdog: o worker vai sair (os._exit(3))
                self.close()
                raise
            except SessionStopped as exc:
                if exc.rc is None:                           # do watchdog (janela sumiu); rc 12 é da DLL
                    self.exiting = True
                    self.close()
                raise
            return result


# ------------------------------------------------------------------------------------------- get_worker (D.8)

@dataclass
class _Attempts:
    """Contadores de uma chamada de ``get_handle`` (D.8)."""

    spawns: int = 0
    collisions: int = 0
    stops: int = 0
    stopping_waits: int = 0
    key_retry: bool = False
    pipe_exists_retry: bool = False
    self_owner_retry: bool = False                # pipe mudo com o lease deste processo: 1 nova tentativa
    last_code: int | None = None                  # último código de saída no boot (mensagem de desistência)


class Bridge:
    """Ciclo de vida dos workers de um processo cliente: cache de handles ``owner``, chave, spawn e reciclagem.

    ``pkgdir``/``build`` existem para os testes (cópia do pacote = "build novo em disco"); em produção valem o
    pacote importado e o ``BUILD_ID`` do ``import rumbapy``.
    """

    def __init__(self, *, pkgdir: str | os.PathLike[str] = PKGDIR, build: str | None = None,
                 config: SpawnConfig | None = None, popen: Callable[..., Any] = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], object] = time.sleep) -> None:
        self.pkgdir = Path(pkgdir)
        self._build = build
        self._config = config
        self.popen = popen
        self.clock = clock
        self.sleep = sleep
        self.handles: dict[str, WorkerHandle] = {}
        self._mutex = threading.Lock()
        self._letter_locks: dict[str, threading.Lock] = {}
        self._key: bytes | None = None

    # ----------------------------------------------------------------- identidade, chave, configuração
    def build_id(self) -> str:
        if self._build is None:
            same = os.path.normcase(str(self.pkgdir.resolve())) == os.path.normcase(str(PKGDIR))
            self._build = build_id() if same else disk_build(self.pkgdir)
        return self._build

    def config(self, python32: str | os.PathLike[str] | None = None) -> SpawnConfig:
        if self._config is None:
            return SpawnConfig.from_env(python32)
        if python32 is None:
            return self._config
        import dataclasses

        return dataclasses.replace(self._config, python32=os.fspath(python32))

    def key(self, *, reload: bool = False) -> bytes:
        """``authkey.bin`` (C.2): criada se não existir; tamanho errado só é recriado sem worker vivo."""
        if self._key is None or reload:
            path = _paths.authkey_path()
            try:
                self._key = _ipc.load_or_create_key(path, workers_alive=self._workers_alive)
            except OSError as exc:            # C.5: só RumbaError sai; e nunca vira "transporte" (C.8)
                raise WorkerError(f"Não consegui ler/criar authkey.bin ({type(exc).__name__}).",
                                  hint="confira as permissões da pasta de dados do rumbapy (antivírus?) e rode de novo",
                                  details={"error": type(exc).__name__, "winerror": getattr(exc, "winerror", None),
                                           "path": str(path)}) from None
        return self._key

    def _workers_alive(self) -> bool:
        try:
            files = list(_paths.run_dir().glob("worker-*.json"))
        except OSError:
            return False
        return any(registry_alive(_paths.read_json(path)) for path in files)

    # ----------------------------------------------------------------- cache e contagem de usuários
    def _letter_lock(self, letter: str) -> threading.Lock:
        with self._mutex:
            return self._letter_locks.setdefault(letter, threading.Lock())

    def _take_cached(self, letter: str) -> WorkerHandle | None:
        with self._mutex:
            handle = self.handles.get(letter)
            if handle is None:
                return None
            if handle.evicted or not handle.alive:
                # despejado: as Session antigas continuam com ele (SessionReplaced); as novas pegam um lease novo
                del self.handles[letter]
                return None
            handle.users += 1
            return handle

    def release(self, handle: WorkerHandle) -> None:
        """Um usuário a menos; o último fecha a conexão (fim do lease, C.6.6)."""
        with self._mutex:
            handle.users = max(0, handle.users - 1)
            if handle.users > 0:
                return
            if self.handles.get(handle.letter) is handle:
                del self.handles[handle.letter]
        handle.close()

    def forget(self, handle: WorkerHandle) -> None:
        """Tira um handle morto do cache (sem mexer na contagem dos outros)."""
        with self._mutex:
            if self.handles.get(handle.letter) is handle:
                del self.handles[handle.letter]

    # ----------------------------------------------------------------- API
    def get_worker(self, letter: str, mode: str = "owner", *,
                   python32: str | os.PathLike[str] | None = None) -> WorkerBackend:
        """D.8 executada na hora; devolve o ``WorkerBackend`` amarrado ao handle (E.5 passos 1 e 6)."""
        from ._backend import WorkerBackend      # tardio: _backend importa este módulo

        handle = self.get_handle(letter, mode, python32=python32)
        return WorkerBackend(letter, mode=mode, python32=python32, bridge=self, handle=handle)

    def get_handle(self, letter: str, mode: str = "owner", *,
                   python32: str | os.PathLike[str] | None = None) -> WorkerHandle:
        """Handle vivo (D.8), já contado como um usuário (devolva com ``release``)."""
        handle = self._get_handle(letter, mode, python32, spawn=True)
        assert handle is not None
        return handle

    def find_handle(self, letter: str, mode: str = "admin", *,
                    python32: str | os.PathLike[str] | None = None) -> WorkerHandle | None:
        """Como ``get_handle``, mas **nunca sobe worker** (E.4 passo 1: "se houver worker vivo"): sem worker vivo
        da letra devolve ``None``. Para ``shutdown``/``evict``: subir um worker só para pará-lo carregaria a DLL."""
        return self._get_handle(letter, mode, python32, spawn=False)

    def _get_handle(self, letter: str, mode: str, python32: str | os.PathLike[str] | None, *,
                    spawn: bool) -> WorkerHandle | None:
        if mode not in _protocol.MODES:
            raise ParameterError.make(detalhe=f"Modo inválido: {mode!r} (use owner ou admin).")
        registry = _paths.worker_registry(letter)            # valida a letra (ParameterError)
        if mode == "owner":
            cached = self._take_cached(letter)
            if cached is not None:
                return cached
        with held(self._letter_lock(letter)):               # em fatias: Ctrl+C funciona durante o spawn de outra thread
            if mode == "owner":
                cached = self._take_cached(letter)
                if cached is not None:
                    return cached
            if not spawn and not registry_alive(_paths.read_json(registry)):
                return None                                  # sem worker vivo: nem pega a trava de spawn
            try:
                with self._spawn_lock(letter):
                    handle = self._locate(letter, mode, python32, registry, spawn=spawn)
            except (_ipc.PipeNotFound, _ipc.PipeTimeout, _ipc.AuthRejected) as exc:   # internas (A.9 e)
                raise WorkerError(exc.message, terminal=letter, details={"transport": type(exc).__name__}) from None
            if handle is None:
                return None
            handle.users = 1
            if mode == "owner":
                with self._mutex:
                    self.handles[letter] = handle
            logger.info("get_worker letter=%s mode=%s pid=%d", letter, mode, handle.pid)
            return handle

    @contextmanager
    def _spawn_lock(self, letter: str) -> Iterator[_locks.FileLock]:
        rounds = max(1, math.ceil(SPAWN_LOCK_MAX_S / SPAWN_LOCK_S))
        try:
            for attempt in range(1, rounds + 1):
                try:
                    lock = _locks.file_lock(_paths.spawn_lock_name(letter), SPAWN_LOCK_S)
                    break
                except _locks.LockTimeout:
                    if attempt >= rounds:
                        raise
                    # quem segura não solta nas esperas (A.12 d): o pior caso dele é limitado, então seguimos
                    logger.info("get_worker letter=%s: outro processo ainda inicia o processo auxiliar (%.0f s); "
                                "esperando a trava de spawn", letter, attempt * SPAWN_LOCK_S)
        except _locks.LockTimeout:
            raise WorkerError(f"Outro processo está iniciando o processo auxiliar do terminal {letter} há mais de "
                              f"{rounds * SPAWN_LOCK_S:.0f} s; rode de novo.", terminal=letter) from None
        except OSError as exc:                                # C.5: só RumbaError sai do Backend
            raise WorkerError(f"Não consegui criar a trava de início do processo auxiliar do terminal {letter} "
                              f"({type(exc).__name__}).", terminal=letter,
                              details={"error": type(exc).__name__, "winerror": getattr(exc, "winerror", None)}
                              ) from None
        try:
            yield lock
        finally:
            lock.release()

    # ----------------------------------------------------------------- máquina de estados (D.8)
    def _locate(self, letter: str, mode: str, python32: Any, registry: Path, *,
                spawn: bool = True) -> WorkerHandle | None:
        st = _Attempts()
        while True:
            reg = _paths.read_json(registry)
            if reg is not None and registry_alive(reg):
                handle = self._use_existing(letter, mode, reg, registry, st)
            elif not spawn:
                return None                                  # find_handle: não há worker vivo (ou ele saiu)
            else:
                handle = self._spawn_new(letter, mode, python32, registry, st)
            if handle is not None:
                return handle

    def _open(self, pipe: str, pid: int, letter: str) -> Any:
        """``ipc.connect`` (confere o PID do servidor do pipe: ``ChannelHijacked``) + autenticação (C.2)."""
        channel = _ipc.connect(pipe, pid, _protocol.CONNECT_TIMEOUT, letter=letter)
        try:
            _ipc.client_auth(channel, self.key(), server_pid=pid)
        except BaseException:
            channel.close()
            raise
        return channel

    def _session(self, letter: str, mode: str, reg: Mapping[str, Any], registry: Path,
                 st: _Attempts) -> tuple[str, WorkerHandle | None]:
        """Conecta, autentica e faz o handshake. ``("ok", handle)`` ou ``(resultado, None)``."""
        pid, pipe = int(reg["pid"]), str(reg["pipe"])
        created = reg.get("created") if isinstance(reg.get("created"), int) else None
        try:
            channel = self._open(pipe, pid, letter)
        except _ipc.AuthRejected:
            if not st.key_retry:                       # a chave mudou desde o boot do worker: relê 1x
                st.key_retry = True
                self.key(reload=True)
                return "retry", None
            raise
        except BaseException as exc:
            if _is_transport(exc):
                return "gone", None
            raise
        try:
            outcome, hello = handshake(channel, letter=letter, mode=mode, build=self.build_id(), pkgdir=self.pkgdir,
                                       expected_pid=pid)
        except BaseException as exc:
            channel.close()
            if _is_transport(exc):
                return "gone", None
            raise
        if outcome == "ok":
            return "ok", WorkerHandle(channel, letter=letter, mode=mode, pid=pid, created=created, hello=hello,
                                      registry=registry)
        channel.close()
        return outcome, None

    def _use_existing(self, letter: str, mode: str, reg: Mapping[str, Any], registry: Path,
                      st: _Attempts) -> WorkerHandle | None:
        pid = int(reg["pid"])
        created = reg.get("created") if isinstance(reg.get("created"), int) else None
        # watchdog/parada presa (exit_reason) ou parada limpa em curso (state=stopping: o pipe já não aceita, D.4)
        if reg.get("exit_reason") or reg.get("state") == "stopping":
            st.stopping_waits += 1
            if st.stopping_waits > MAX_STOPPING_WAITS:
                raise WorkerError.make("not_exited", L=letter, pid=pid, terminal=letter)
            logger.info("get_worker letter=%s: o processo auxiliar (PID %d) está saindo (%s); esperando", letter,
                        pid, reg.get("exit_reason") or "stopping")
            wait_exit(pid, created, EXIT_WAIT_S, clock=self.clock, sleep=self.sleep)
            return None
        try:
            outcome, handle = self._session(letter, mode, reg, registry, st)
        except (_ipc.PipeNotFound, _ipc.PipeTimeout, _ipc.AuthRejected) as exc:
            return self._unresponsive(letter, reg, st, exc)
        if handle is not None:
            return handle
        if outcome == "retry":
            return None
        if outcome in ("stopping", "gone"):
            st.stopping_waits += 1
            if st.stopping_waits > MAX_STOPPING_WAITS:
                raise WorkerError.make("not_responding", terminal=letter)
            logger.info("get_worker letter=%s: o processo auxiliar (PID %d) está saindo; esperando", letter, pid)
            wait_exit(pid, created, EXIT_WAIT_S, clock=self.clock, sleep=self.sleep)
            return None
        # old_build: worker de build antigo e SEM dono -> shutdown{"reason":"build"} -> espera sair -> spawn
        st.stops += 1
        if st.stops > MAX_STOPS:
            raise WorkerError.make("not_responding", terminal=letter)
        logger.info("get_worker letter=%s: processo auxiliar de build antigo (PID %d) sem dono; parando", letter, pid)
        self._shutdown_old(letter, reg, registry, st)
        if not wait_exit(pid, created, EXIT_WAIT_S, clock=self.clock, sleep=self.sleep):
            raise WorkerError.make("not_exited", L=letter, pid=pid, terminal=letter)
        return None

    def _shutdown_old(self, letter: str, reg: Mapping[str, Any], registry: Path, st: _Attempts) -> None:
        try:
            outcome, handle = self._session(letter, "admin", reg, registry, st)
        except (_ipc.PipeNotFound, _ipc.PipeTimeout, _ipc.AuthRejected):
            return
        if handle is None:
            return
        try:
            handle.request("shutdown", {"reason": "build"})
        except (WorkerCrashed, WorkerHung):
            pass                                       # já saiu
        finally:
            handle.close()

    def _unresponsive(self, letter: str, reg: Mapping[str, Any], st: _Attempts, exc: BaseException) -> None:
        """Pipe ausente/mudo (D.8): sem dono -> evento de parada e espera; com dono vivo -> ``TerminalInUse``."""
        pid = int(reg["pid"])
        created = reg.get("created") if isinstance(reg.get("created"), int) else None
        owner = reg.get("owner")
        if isinstance(owner, dict) and owner.get("pid") != os.getpid() and process_alive(owner.get("pid")):
            raise in_use(letter, owner)
        if isinstance(owner, dict) and owner.get("pid") == os.getpid():
            # o lease é DESTE processo (handle anterior ainda aberto): o worker adiaria o evento de parada (D.4 d),
            # então não gasto a parada; espero um pouco, tento de novo 1x e desisto com a dica certa
            if not st.self_owner_retry:
                st.self_owner_retry = True
                logger.info("get_worker letter=%s: o processo auxiliar (PID %d) não aceitou (%s) e o lease é deste "
                            "processo; tentando de novo", letter, pid, type(exc).__name__)
                self.sleep(SELF_OWNER_RETRY_S)
                return None
            raise WorkerError(f"O processo auxiliar do terminal {letter} (PID {pid}) não aceita conexões e o lease é "
                              f"deste mesmo processo (PID {os.getpid()}); não vou pará-lo.",
                              hint="feche as Session abertas deste terminal (ou reinicie o kernel/script) e rode de "
                                   "novo", terminal=letter,
                              details={"worker_pid": pid, "owner_pid": os.getpid(),
                                       "transport": type(exc).__name__})
        st.stops += 1
        if st.stops > MAX_STOPS:
            raise WorkerError.make("not_responding", terminal=letter)
        logger.warning("get_worker letter=%s: o processo auxiliar (PID %d) não responde (%s); sinalizando parada",
                       letter, pid, type(exc).__name__)
        self._signal_stop(letter)
        if not wait_exit(pid, created, EXIT_WAIT_S, clock=self.clock, sleep=self.sleep):
            raise WorkerError.make("not_responding", terminal=letter)
        return None

    def _signal_stop(self, letter: str) -> None:
        try:
            handle = _win32.open_event(_paths.stop_event_name(letter), create=False)
        except OSError:
            return                                     # sem evento: o worker não chegou a criá-lo
        try:
            _win32.set_event(handle)
        finally:
            _win32.close_handle(handle)

    def _spawn_new(self, letter: str, mode: str, python32: Any, registry: Path, st: _Attempts) -> WorkerHandle | None:
        st.spawns += 1
        if st.spawns > MAX_SPAWNS + st.collisions:
            last = (f"último código de saída {st.last_code}" if st.last_code is not None
                    else "nenhum código de saída")
            raise start_failed(letter, f"desisti depois de {st.spawns - 1} tentativas de iniciar ({last}).",
                               details={"exit_code": st.last_code, "attempts": st.spawns - 1})
        if disk_build(self.pkgdir, letter) != self.build_id():                  # R11b
            raise UpdateRequired.make("other_process", terminal=letter)
        self.key()                                                              # a chave existe antes do boot
        config = self.config(python32)
        # erros do Popen viram WorkerStartFailed e os da pasta de dados WorkerError dentro do próprio spawn
        spawned = spawn(letter, config, pkgdir=self.pkgdir, expect_build=self.build_id(), popen=self.popen)
        deadline = self.clock() + SPAWN_READY_S
        while True:
            reg = _paths.read_json(registry)
            if reg is not None and reg.get("pipe") == spawned.pipe and isinstance(reg.get("pid"), int):
                try:
                    _outcome, handle = self._session(letter, mode, reg, registry, st)
                except (_ipc.PipeNotFound, _ipc.PipeTimeout) as exc:
                    logger.info("get_worker letter=%s: canal novo ainda não aceitou (%s)", letter, type(exc).__name__)
                    handle = None
                if handle is not None:
                    return handle                      # não exige Popen.pid (E14): vale o PID do registro
            code = spawned.proc.poll()
            if code is not None:
                return self._boot_exit(int(code), letter, spawned, st, config)
            if self.clock() >= deadline:
                raise start_failed(letter, f"não ficou pronto em {SPAWN_READY_S:.0f} s.",
                                   tail=boot_tail(spawned.boot),
                                   details={"exit_code": None, "ready_timeout": SPAWN_READY_S})
            self.sleep(SLICE_S)

    def _boot_exit(self, code: int, letter: str, spawned: Spawned, st: _Attempts,
                   config: SpawnConfig) -> WorkerHandle | None:
        """Saída do worker antes de ficar pronto (D.2/D.8)."""
        st.last_code = code
        logger.warning("get_worker letter=%s: o processo auxiliar saiu no boot com código %d (%s)", letter, code,
                       _protocol.EXIT_CODES.get(code, "?"))
        if code == _protocol.EXIT_PIPE_EXISTS and not st.pipe_exists_retry:
            st.pipe_exists_retry = True
            return None
        if code == _protocol.EXIT_PID_COLLISION and st.collisions < MAX_COLLISION_SPAWNS:
            st.collisions += 1
            return None
        if code == _protocol.EXIT_KEY and not st.key_retry:
            st.key_retry = True
            self.key(reload=True)
            return None
        if code in (_protocol.EXIT_CLEAN, _protocol.EXIT_IDLE):
            return None
        reason, reg = None, None
        if code == _protocol.EXIT_WATCHDOG:                  # D.2: 3 -> WorkerHung/SessionStopped por exit_reason
            found = _paths.read_json(_paths.worker_registry(letter))
            if found and found.get("pipe") == spawned.pipe:
                reg = found
                reason = found.get("exit_reason") if isinstance(found.get("exit_reason"), str) else None
        raise exit_error(code, letter, boot=spawned.boot, dll=config.dll, reason=reason, reg=reg)

    # ----------------------------------------------------------------- diagnóstico (doctor; nunca sobe worker)
    def probe(self, letter: str) -> dict[str, Any]:
        """Registro + ``ping`` por conexão admin, sem spawn e sem criar a chave. Só leitura."""
        registry = _paths.worker_registry(letter)
        reg = _paths.read_json(registry)
        info: dict[str, Any] = {"letter": letter, "registered": reg is not None, "alive": False}
        if reg is None:
            return info
        owner = reg.get("owner") if isinstance(reg.get("owner"), dict) else None
        info.update({"pid": reg.get("pid"), "state": reg.get("state"), "version": reg.get("version"),
                     "build_id": reg.get("build_id"), "connected": reg.get("connected"),
                     "owner_pid": owner.get("pid") if owner else None,
                     "owner_script": owner.get("script") if owner else None,
                     "exit_reason": reg.get("exit_reason"), "alive": registry_alive(reg)})
        if not info["alive"]:
            return info
        try:
            key = _ipc.read_key(_paths.authkey_path())
        except (OSError, ValueError) as exc:
            info["error"] = f"chave ilegível ({type(exc).__name__})"
            return info
        handle: WorkerHandle | None = None
        try:
            pid = int(reg["pid"])
            channel = _ipc.connect(str(reg["pipe"]), pid, _protocol.CONNECT_TIMEOUT, letter=letter)
            try:
                _ipc.client_auth(channel, key, server_pid=pid)
                outcome, hello = handshake(channel, letter=letter, mode="admin", build=self.build_id(),
                                           pkgdir=self.pkgdir, expected_pid=pid)
            except BaseException:
                channel.close()
                raise
            if outcome != "ok":
                channel.close()
                info["error"] = f"handshake: {outcome}"
                return info
            handle = WorkerHandle(channel, letter=letter, mode="admin", pid=pid, created=reg.get("created"),
                                  hello=hello, registry=registry)
            info["ping"] = handle.request("ping")
        except RumbaError as exc:
            info["error"] = type(exc).__name__
        except Exception as exc:                        # diagnóstico nunca falha
            info["error"] = type(exc).__name__
        finally:
            if handle is not None:
                handle.close()
        return info


# ------------------------------------------------------------------------------------------- instância do processo

_default_bridge: Bridge | None = None
_default_lock = threading.Lock()


def default_bridge() -> Bridge:
    """A ``Bridge`` do processo (C.6.6: ``dict[letra, WorkerHandle]`` compartilhado pelas ``Session``)."""
    global _default_bridge
    with _default_lock:
        if _default_bridge is None:
            _default_bridge = Bridge()
        return _default_bridge


def get_worker(letter: str, mode: str = "owner", *,
               python32: str | os.PathLike[str] | None = None) -> WorkerBackend:
    """D.8: worker vivo da letra (sobe se preciso) -> ``WorkerBackend`` (``owner`` com lease; ``admin`` sem)."""
    return default_bridge().get_worker(letter, mode, python32=python32)


def get_handle(letter: str, mode: str = "owner", *,
               python32: str | os.PathLike[str] | None = None) -> WorkerHandle:
    return default_bridge().get_handle(letter, mode, python32=python32)
