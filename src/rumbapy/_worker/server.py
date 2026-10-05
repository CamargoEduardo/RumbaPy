"""Servidor do worker (D.3, C.1-C.3, C.6 lado worker): pipe, threads, lease, fila e eventos.

| Thread | Faz | NUNCA |
|---|---|---|
| T-accept | ``ConnectNamedPipe`` e entrega da conexão a uma T-conn | chamar a DLL; autenticar |
| T-conn-n | auth, handshake, ``recv`` -> validação -> fila; responde ``ping`` direto; EOF -> fim do lease | chamar a DLL |
| T-dll | carrega a DLL; executa os jobs em FIFO; toda chamada ``WD_*`` | segurar ``conn.lock`` durante a DLL |
| T-watch | tick de 100 ms (``watchdog``) | chamar a DLL |

Fila global ``queue.Queue(maxsize=64)`` (cheia -> ``ParameterError``); resposta com ``conn.lock`` por até 1 s (não deu ->
fecha a conexão). ``evict`` é um job (após o atual); ``shutdown`` e o evento de parada só valem sem dono.

Só a T-conn dona fecha o próprio canal: as outras threads apenas marcam ``conn.alive = False`` (a T-conn vê isso em no
máximo ``RECV_SLICE_S`` e fecha em ``_end_conn``). Assim nenhum handle é fechado enquanto outra thread o usa em
``poll``/``recv_bytes`` (o valor do handle poderia ser reutilizado por uma instância nova do pipe).

O PID do cliente vem do pipe (``GetNamedPipeClientProcessId``); o ``client_pid`` declarado no ``hello_ack`` tem de
conferir com ele (divergência -> WARNING e a conexão é fechada). Lease, ``busy`` e o reaper usam o PID do pipe.
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..errors import ParameterError, ProtocolError, RumbaError, SessionReplaced, TerminalInUse
from .. import _buildid, _ipc, _locks, _log, _paths, _protocol, _win32
from .dispatch import Dispatcher, JobCtx
from .engine import Engine, JobCancelled
from .watchdog import Watchdog
from .mask import MaskState
from .hllapi import Hllapi

import threading
import platform
import logging
import _winapi
import queue
import time
import os

logger = logging.getLogger(__name__)

RESPONSE_LOCK_S = 1.0
EVENT_LOCK_S = 0.5
RECV_SLICE_S = 0.5
STOP_FREE_S = 5.0
STOP_TOTAL_S = 12.0


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hour(since: str | None) -> str:
    try:
        return datetime.fromisoformat(str(since)).strftime("%H:%M:%S")
    except ValueError:
        return str(since or "?")


@dataclass
class Config:
    letter: str
    pipe: str
    key: bytes
    registry: Path
    build_id: str
    pkgdir: str
    stop_event: str | None = None
    log_path: str | None = None
    idle: float = 1800.0
    guard: str = "none"
    commit: str | None = None
    python: str = ""


class Conn:
    """Uma conexão de cliente (lado do worker)."""

    _ids = 0

    def __init__(self, channel: _ipc.Channel, peer_pid: int | None = None) -> None:
        Conn._ids += 1
        self.id = Conn._ids
        self.channel = channel
        self.mode: str | None = None
        self.peer_pid = peer_pid                         # GetNamedPipeClientProcessId (None = não deu para ler)
        self.client_pid: int | None = peer_pid
        self.thread: threading.Thread | None = None
        self.script: str | None = None
        self.evicted = False
        self.alive = True
        self.jobs: set[Job] = set()


@dataclass(eq=False)
class Job:
    id: int | None
    op: str
    args: dict[str, Any] = field(default_factory=dict)
    hwnd: int | None = None
    conn: Conn | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    fn: Callable[[Job], Any] | None = None           # job interno (parada, evict)
    started: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


class WorkerServer:
    """Estado do worker + as quatro threads. ``exit_fn`` (padrão ``os._exit``) é injetável nos testes."""

    def __init__(self, config: Config, *, dll_loader: Callable[[], object], first_pipe: int,
                 security: _win32.SecurityAttributes, exit_fn: Callable[[int], Any] = os._exit,
                 is_window: Callable[[int], bool] = _win32.is_window,
                 dialog_visible: Callable[[int | None], bool] = _win32.hllapi_error_dialog_visible,
                 window_pid: Callable[[int], int] = _win32.window_thread_pid,
                 peer_pid: Callable[[_ipc.Channel], int | None] | None = None,
                 stop_event_handle: int = 0) -> None:
        self.config = config
        self.letter = config.letter
        self.dll_loader = dll_loader
        self.security = security
        self._pipe = first_pipe
        self._exit_fn = exit_fn
        self.window_pid = window_pid
        self.peer_pid = peer_pid or _pipe_client_pid    # injetável: nos testes um "outro processo" é simulado
        self.stop_handle = stop_event_handle
        self.state = "listening"
        self.owner: Conn | None = None
        self.owner_info: dict[str, Any] | None = None
        self.conns: dict[int, Conn] = {}
        self.queue: queue.Queue[Job] = queue.Queue(maxsize=_protocol.JOB_QUEUE_MAX)
        self.lock = threading.RLock()
        self.engine: Engine | None = None
        self.dispatcher: Dispatcher | None = None
        self.dll: object | None = None
        self.dll_ready = threading.Event()
        self.dll_error: BaseException | None = None
        self.current: Job | None = None
        self.started_at = now_iso()
        self.t0 = time.monotonic()
        self.created = _win32.process_created(os.getpid())
        self.stop_reason: str | None = None
        self.exit_code: int | None = None
        self.finished = threading.Event()
        self.threads_stop = threading.Event()
        self._idle_lock: _locks.FileLock | None = None
        self._stop_waiters: list[tuple[Conn, int]] = []
        self.watchdog = Watchdog(self, exit_fn=self._watchdog_exit, idle=config.idle, is_window=is_window,
                                 dialog_visible=dialog_visible)

    # ================================================================= ciclo de vida
    def start_dll(self, timeout: float = 60.0) -> BaseException | None:
        """Sobe T-dll, que carrega a DLL (D.2 passo 8). Devolve a exceção da carga (ou ``None``)."""
        threading.Thread(target=self._dll_main, name="dll", daemon=True).start()
        if not self.dll_ready.wait(timeout):
            return TimeoutError("a DLL não carregou no prazo")
        return self.dll_error

    def start_service(self) -> None:
        """Grava o registro (``listening``) e sobe T-watch e T-accept (D.2 passos 9 e 10)."""
        self.write_registry()
        threading.Thread(target=self.watchdog.run, args=(self.threads_stop,), name="watch", daemon=True).start()
        threading.Thread(target=self._accept_main, name="accept", daemon=True).start()
        logger.info("listening pipe=%s idle=%.0f", self.config.pipe.rsplit("-", 1)[-1], self.config.idle)

    def wait(self) -> int:
        self.finished.wait()
        return int(self.exit_code or 0)

    def _exit(self, code: int) -> None:
        self.exit_code = code
        self.threads_stop.set()
        _log.flush_all()
        self.finished.set()
        self._exit_fn(code)

    def _watchdog_exit(self, code: int) -> None:
        if self.exit_code is None:
            self.exit_code = code
        self.threads_stop.set()
        self.finished.set()
        self._exit_fn(code)

    # ================================================================= registro (D.7)
    def registry_data(self, *, exit_reason: str | None = None) -> dict[str, Any]:
        engine = self.engine
        state = engine.state() if engine is not None else {"connected": False, "hwnd": None, "hwnd_pid": None}
        owner = dict(self.owner_info) if self.owner_info else None
        return {"protocol": _protocol.PROTOCOL, "version": _protocol.VERSION, "build_id": self.config.build_id,
                "pid": os.getpid(), "created": self.created, "letter": self.letter, "pipe": self.config.pipe,
                "python": self.config.python, "started_at": self.started_at, "log": self.config.log_path,
                "state": self.state, "owner": owner, "connected": state["connected"], "hwnd": state["hwnd"],
                "hwnd_pid": state["hwnd_pid"], "exit_reason": exit_reason}

    def write_registry(self, *, exit_reason: str | None = None) -> None:
        try:
            with self.lock:
                data = self.registry_data(exit_reason=exit_reason)
            _paths.write_json(self.config.registry, data)
        except Exception as exc:
            logger.warning("não consegui gravar o registro (%s)", type(exc).__name__)

    def delete_registry(self) -> None:
        reg = _paths.read_json(self.config.registry)
        if reg is not None and reg.get("pid") == os.getpid():
            try:
                os.unlink(self.config.registry)
            except OSError as exc:
                logger.warning("não consegui apagar o registro (%s)", type(exc).__name__)

    # ================================================================= estado (WatchHost)
    def current_job(self) -> Job | None:
        return self.current

    def engine_hwnd(self) -> int | None:
        engine = self.engine
        return engine.hwnd if engine is not None and engine.connected else None

    def stop_requested(self) -> bool:
        if not self.stop_handle or self.state == "stopping":
            return False
        try:
            return _win32.event_is_set(self.stop_handle)
        except OSError:
            return False

    def has_owner(self) -> bool:
        return self.owner is not None

    def busy(self) -> bool:
        return self.current is not None or not self.queue.empty() or self.state == "stopping"

    def hello(self) -> dict[str, Any]:
        engine = self.engine
        state = engine.state() if engine is not None else {"connected": False, "hwnd": None}
        with self.lock:
            owner = dict(self.owner_info) if self.owner_info else None
            st = self.state
        return {"t": "hello", "protocol": _protocol.PROTOCOL, "version": _protocol.VERSION,
                "build_id": self.config.build_id, "pid": os.getpid(), "letter": self.letter,
                "python": platform.python_version(), "started_at": self.started_at, "state": st, "owner": owner,
                "connected": state["connected"], "hwnd": state["hwnd"], "guard": self.config.guard}

    def ping_result(self) -> dict[str, Any]:
        engine = self.engine
        state = engine.state() if engine is not None else {"connected": False, "hwnd": None}
        with self.lock:
            owner_pid = self.owner_info["pid"] if self.owner_info else None
            st = self.state
        return {"uptime_s": round(time.monotonic() - self.t0, 3), "letter": self.letter, "state": st,
                "connected": state["connected"], "owner_pid": owner_pid, "hwnd": state["hwnd"]}

    # ================================================================= envio
    def send_to(self, conn: Conn | None, msg: dict[str, Any], *, lock_timeout: float = RESPONSE_LOCK_S) -> bool:
        if conn is None or not conn.alive:
            return False
        try:
            if conn.channel.send_msg(msg, lock_timeout=lock_timeout):
                return True
            logger.warning("conn=%d: trava de escrita ocupada; fechando a conexão", conn.id)
        except RumbaError as exc:
            logger.warning("conn=%d: resposta inválida (%s); fechando", conn.id, type(exc).__name__)
        except (OSError, EOFError, ValueError):
            pass
        conn.alive = False                                # a T-conn dona fecha o canal (nunca outra thread)
        return False

    def reply(self, conn: Conn | None, rid: int | None, *, result: dict[str, Any] | None = None,
              error: RumbaError | None = None, ms: float | None = None) -> None:
        if error is not None:
            msg = _protocol.make_error(rid, error)
        else:
            msg = _protocol.make_result(rid or 0, result or {}, ms)
        self.send_to(conn, msg)

    def broadcast(self, event: str, reason: str, *, exclude: frozenset[int] = frozenset()) -> None:
        with self.lock:
            conns = [c for c in self.conns.values() if c.id not in exclude]
        for conn in conns:
            self.send_to(conn, _protocol.make_event(event, reason), lock_timeout=EVENT_LOCK_S)

    # ================================================================= T-dll
    def _dll_main(self) -> None:
        try:
            self.dll = self.dll_loader()
            hll = Hllapi(self.dll, track=self.watchdog.inflight)
            self.engine = Engine(hll, MaskState(), letter=self.letter, window_pid=self.window_pid)
            self.dispatcher = Dispatcher(self.engine, guard=self.config.guard)
        except BaseException as exc:                      # saída 10 (o main decide)
            self.dll_error = exc
            self.dll_ready.set()
            return
        self.dll_ready.set()
        while not self.threads_stop.is_set():
            try:
                job = self.queue.get(timeout=RECV_SLICE_S)
            except queue.Empty:
                continue
            try:
                self._run_job(job)
            except Exception as exc:                      # nunca derruba T-dll
                logger.error("job %s falhou fora do handler (%s)", job.op, type(exc).__name__)
            finally:
                job.done.set()
                with self.lock:
                    if job.conn is not None:
                        job.conn.jobs.discard(job)

    def _run_job(self, job: Job) -> None:
        job.started.set()
        if job.fn is not None:
            self.current = job
            try:
                job.fn(job)
            finally:
                self.current = None
            return
        conn = job.conn
        if job.cancel.is_set() or (conn is not None and not conn.alive):
            logger.info("dropped id=%s op=%s reason=owner_gone", job.id, job.op)
            return
        if conn is not None and conn.evicted:
            self.reply(conn, job.id, error=SessionReplaced.make(L=self.letter, terminal=self.letter))
            return
        assert self.dispatcher is not None
        ctx = JobCtx(job.op, conn_id=conn.id if conn else None, cancel=job.cancel, hwnd=job.hwnd, id=job.id)
        t0 = time.perf_counter()
        self.current = job
        try:
            result = self.dispatcher.handle(job.op, job.args, ctx)
        except JobCancelled as exc:
            logger.info("dropped id=%s op=%s reason=owner_gone chunks_sent=%d", job.id, job.op, exc.chunks_sent)
            return
        except RumbaError as exc:
            self.current = None
            if job.op in ("connect", "disconnect"):
                self.write_registry()
            if not self.watchdog.fired:
                self.reply(conn, job.id, error=exc)
            return
        finally:
            self.current = None
        if self.watchdog.fired:                           # o watchdog já respondeu este job
            return
        if job.op in ("connect", "disconnect"):          # registro antes da resposta: quem lê depois já vê
            self.write_registry()
        self.reply(conn, job.id, result=result, ms=(time.perf_counter() - t0) * 1000)

    def enqueue(self, job: Job) -> None:
        if job.conn is not None:
            with self.lock:
                job.conn.jobs.add(job)
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            if job.conn is not None:
                with self.lock:
                    job.conn.jobs.discard(job)
            raise ParameterError.make(detalhe="muitas requisições pendentes") from None
        self.watchdog.touch()

    # ================================================================= T-accept
    def _accepting(self) -> bool:
        return self.state != "stopping" and not self.threads_stop.is_set()

    def _accept_main(self) -> None:
        handle = self._pipe
        while handle and self._accepting():
            try:
                connected = _ipc.accept(handle, lambda: not self._accepting())
            except OSError as exc:
                logger.warning("ConnectNamedPipe falhou (%s); recriando a instância", type(exc).__name__)
                connected = False
            if not self._accepting():
                break
            nxt = 0
            while not nxt and self._accepting():
                try:
                    nxt = _ipc.create_pipe(self.config.pipe, first=False, security=self.security)
                except OSError as exc:
                    logger.warning("não consegui criar a próxima instância do pipe (%s)", type(exc).__name__)
                    time.sleep(RECV_SLICE_S)
            if connected:
                self._hand_off(handle)
            else:
                _close_handle(handle)
            handle = nxt
            self._pipe = nxt
        _close_handle(handle)
        self._pipe = 0

    def _hand_off(self, handle: int) -> None:
        with self.lock:
            full = len(self.conns) >= _protocol.MAX_CONNECTIONS
        if full:
            logger.warning("mais de %d conexões vivas; a nova foi fechada", _protocol.MAX_CONNECTIONS)
            _close_handle(handle)
            return
        channel = _ipc.wrap(handle, letter=self.letter)
        try:
            peer = self.peer_pid(channel)
        except Exception:
            peer = None
        conn = Conn(channel, peer)
        with self.lock:
            self.conns[conn.id] = conn
        self.watchdog.touch()
        conn.thread = threading.Thread(target=self._conn_main, args=(conn,), name=f"conn-{conn.id}", daemon=True)
        conn.thread.start()

    # ================================================================= T-conn
    def _conn_main(self, conn: Conn) -> None:
        try:
            try:
                _ipc.server_auth(conn.channel, self.config.key)
            except _ipc.AuthFailed as exc:
                logger.warning("conn=%d: autenticação falhou (cliente PID %s): %s", conn.id, conn.client_pid, exc)
                return
            if not self._handshake(conn):
                return
            while conn.alive and not self.threads_stop.is_set():
                msg = conn.channel.recv_msg(RECV_SLICE_S)
                if msg is None:
                    continue
                if not self._on_request(conn, msg):
                    break
        except (EOFError, BrokenPipeError, ConnectionError):
            pass
        except RumbaError as exc:                         # ProtocolError: JSON inválido, > 1 MiB...
            logger.warning("conn=%d: %s; fechando", conn.id, type(exc).__name__)
        except (OSError, RuntimeError, ValueError) as exc:
            if conn.alive:
                logger.info("conn=%d: canal encerrado (%s)", conn.id, type(exc).__name__)
        finally:
            self._end_conn(conn)

    def _reap_dead_owner(self) -> None:
        """O processo dono morreu e a T-conn dele ainda não viu o EOF: encerra o lease já (não rejeita à toa)."""
        with self.lock:
            owner, info = self.owner, self.owner_info
        if owner is None or info is None or _win32.process_alive(int(info["pid"])):
            return
        logger.info("conn=%d: o processo dono (PID %s) não existe mais; fim do lease", owner.id, info["pid"])
        owner.alive = False                               # o canal fica para a T-conn dele fechar
        self._end_conn(owner, close=False)

    def _handshake(self, conn: Conn) -> bool:
        self._reap_dead_owner()
        conn.channel.send_msg(self.hello())
        ack = conn.channel.recv_msg(_protocol.HELLO_ACK_TIMEOUT)
        if not isinstance(ack, dict) or ack.get("t") != "hello_ack" or ack.get("mode") not in _protocol.MODES:
            logger.warning("conn=%d: hello_ack ausente ou inválido; fechando", conn.id)
            return False
        pid, script, build = ack.get("client_pid"), ack.get("script"), ack.get("build_id")
        if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(script, str) \
                or not isinstance(build, str):
            logger.warning("conn=%d: hello_ack malformado; fechando", conn.id)
            return False
        if conn.peer_pid is not None and pid != conn.peer_pid:
            logger.warning("conn=%d: hello_ack declara o PID %s, mas o pipe diz %s; fechando", conn.id, pid,
                           conn.peer_pid)
            return False
        conn.mode, conn.script, conn.client_pid = ack["mode"], script[:200], pid
        old: Conn | None = None
        with self.lock:
            if self.state == "stopping":
                reject: str | None = "stopping"
            elif conn.mode == "admin":
                reject = None
            elif self.owner is not None and self.owner_info is not None and self.owner_info.get("pid") != pid:
                reject = "busy"
            elif self.owner is not None and self.owner is not conn and conn.peer_pid is None:
                reject = "busy"                           # PID não confirmado pelo pipe: não toma o lease de ninguém
            elif build != self.config.build_id:
                reject = "build"
            else:
                reject = None
                if self.owner is not None and self.owner is not conn:
                    old = self.owner                      # o mesmo processo reabriu o canal (Ctrl+C, C.7)
                self.owner = conn
                self.owner_info = {"pid": pid, "script": conn.script, "since": now_iso()}
                self.state = "owned"
            owner = dict(self.owner_info) if self.owner_info else None
        if reject is not None:
            logger.info("conn=%d: reject %s (mode=%s pid=%s)", conn.id, reject, conn.mode, pid)
            conn.channel.send_msg({"t": "reject", "reason": reject, "owner": owner})
            return False
        if old is not None:
            logger.info("conn=%d: o PID %s reabriu o canal; o lease passa para a conexão nova", conn.id, pid)
            self._cancel_jobs(old)
            old.alive = False                             # a T-conn antiga vê e fecha o próprio canal
        conn.channel.send_msg({"t": "welcome", "mode": conn.mode})
        logger.info("conn=%d: welcome mode=%s pid=%s", conn.id, conn.mode, pid)
        if conn.mode == "owner":
            self.write_registry()
        return True

    def _on_request(self, conn: Conn, msg: dict[str, Any]) -> bool:
        """Uma requisição; ``False`` = fechar a conexão."""
        self.watchdog.touch()
        if self.state == "stopping":
            return False
        rows, cols = self.dispatcher.geometry() if self.dispatcher is not None else (None, None)
        rid = msg.get("id") if isinstance(msg.get("id"), int) and not isinstance(msg.get("id"), bool) else None
        try:
            req = _protocol.validate_request(msg, mode=conn.mode, letter=self.letter, rows=rows, cols=cols)
        except RumbaError as exc:
            if isinstance(exc, ProtocolError):
                logger.warning("conn=%d: requisição inválida; fechando", conn.id)
                return False
            self.reply(conn, rid, error=exc)
            return True
        if req.op == "ping":
            self.reply(conn, req.id, result=self.ping_result())
            return True
        if conn.evicted and req.op not in _protocol.ADMIN_OPS:
            self.reply(conn, req.id, error=SessionReplaced.make(L=self.letter, terminal=self.letter))
            return True
        try:
            if req.op == "shutdown":
                self._on_shutdown(conn, req)
            elif req.op == "evict":
                self.enqueue(Job(req.id, "evict", req.args, conn=conn, fn=self._evict_job))
            else:
                self.enqueue(Job(req.id, req.op, req.args, req.hwnd, conn))
        except RumbaError as exc:
            self.reply(conn, req.id, error=exc)
        return True

    def _cancel_jobs(self, conn: Conn) -> None:
        with self.lock:
            jobs = list(conn.jobs)
        for job in jobs:
            job.cancel.set()

    def _end_conn(self, conn: Conn, *, close: bool = True) -> None:
        """EOF/erro na conexão (C.6.4): cancela os jobs dela; se era o dono, o lease termina.

        ``close=False``: chamado por outra thread (reaper) — o canal fica para a T-conn dona fechar."""
        conn.alive = False
        if close:
            conn.channel.close()
        self._cancel_jobs(conn)
        with self.lock:
            self.conns.pop(conn.id, None)
            was_owner = self.owner is conn
            if was_owner:
                self.owner = None
                self.owner_info = None
                if self.state != "stopping":
                    self.state = "listening"
        if not was_owner or self.state == "stopping":
            return
        logger.info("conn=%d: fim do lease", conn.id)
        self.write_registry()
        try:
            changed = _buildid.compute(self.config.pkgdir) != self.config.build_id
        except OSError:
            changed = False
        if changed:
            logger.info("o código em disco mudou (build novo); encerrando")
            self.stop_clean("build")

    # ================================================================= evict / shutdown
    def _evict_job(self, job: Job) -> None:
        assert self.dispatcher is not None
        ctx = JobCtx("evict", conn_id=job.conn.id if job.conn else None, id=job.id)
        try:
            result = self.dispatcher.handle("evict", job.args, ctx)
        except RumbaError as exc:
            self.reply(job.conn, job.id, error=exc)
            return
        with self.lock:
            owner, info = self.owner, self.owner_info
            if owner is not None:
                owner.evicted = True
                self.owner = None
                self.owner_info = None
                if self.state != "stopping":
                    self.state = "listening"
        if owner is not None:
            logger.info("evict: dono conn=%d despejado (reason=%s)", owner.id, job.args.get("reason"))
            self.send_to(owner, _protocol.make_event("evicted", str(job.args.get("reason", ""))),
                         lock_timeout=EVENT_LOCK_S)
        result["evicted_owner"] = {"pid": info["pid"], "script": info["script"]} if info else None
        self.write_registry()
        self.reply(job.conn, job.id, result=result)

    def _on_shutdown(self, conn: Conn, req: _protocol.Request) -> None:
        with self.lock:
            owner, info = self.owner, self.owner_info
        if owner is not None and owner is not conn and info is not None:
            raise TerminalInUse.make("lease", L=self.letter, pid=info["pid"], script=info["script"],
                                     hora=_hour(info.get("since")), terminal=self.letter)
        reason = "build" if req.args.get("reason") == "build" else "shutdown"
        self.stop_clean(reason, requester=(conn, req.id))

    # ================================================================= parada limpa (D.4)
    def stop_clean(self, reason: str, *, requester: tuple[Conn, int] | None = None) -> None:
        """``STOPPING``: para de aceitar, avisa (``shutting_down``), ``DisconnectPS``+``ResetSystem`` como job de
        T-dll (T-dll presa por 5 s -> ``exit_reason="stop_while_hung"`` e saída 3), apaga o registro, sai 0 (5 = idle)."""
        with self.lock:
            if requester is not None:
                self._stop_waiters.append(requester)
            if self.state == "stopping":
                return
            self.state = "stopping"
            self.stop_reason = reason
        threading.Thread(target=self._stop_main, args=(reason,), name="stop", daemon=True).start()

    def _stop_calls_job(self, job: Job) -> None:
        engine = self.engine
        if engine is None:
            return
        try:
            engine.stop_calls()
        except Exception as exc:
            logger.warning("parada: DisconnectPS/ResetSystem falhou (%s)", type(exc).__name__)

    def _stop_main(self, reason: str) -> None:
        logger.info("parada limpa reason=%s", reason)
        self.write_registry()
        with self.lock:                                   # quem pediu o shutdown recebe {} (não o evento)
            requesters = frozenset(conn.id for conn, _rid in self._stop_waiters)
        self.broadcast("shutting_down", reason, exclude=requesters)
        with self.lock:
            conns = list(self.conns.values())
        for conn in conns:
            self._cancel_jobs(conn)
        self.watchdog.stopping = True
        job = Job(None, "stop", fn=self._stop_calls_job)
        deadline = time.monotonic() + STOP_FREE_S         # um prazo só para "T-dll livre" (D.4): fila + início
        try:
            self.queue.put(job, timeout=STOP_FREE_S)
            started = job.started.wait(max(0.0, deadline - time.monotonic()))
        except queue.Full:
            started = False
        if not started:
            logger.critical("parada: a thread da DLL não ficou livre em %.0f s; encerrando com código 3", STOP_FREE_S)
            self.fire_record("stop_while_hung")
            self._exit(_protocol.EXIT_WATCHDOG)
            return
        job.done.wait(STOP_TOTAL_S)
        if self.watchdog.fired:
            return
        with self.lock:
            waiters, self._stop_waiters = self._stop_waiters, []
        for conn, rid in waiters:
            self.reply(conn, rid, result={})
        self.delete_registry()
        self.threads_stop.set()
        accept = [t for t in threading.enumerate() if t.name == "accept"]
        for thread in accept:
            thread.join(2 * _ipc.ACCEPT_SLICE_S + 0.5)
        with self.lock:
            conns = list(self.conns.values())
        for conn in conns:                                # cada T-conn fecha o próprio canal (vê threads_stop)
            conn.alive = False
        for conn in conns:
            thread = conn.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(RECV_SLICE_S + 1.0)
        if self._idle_lock is not None:                   # solta só depois de apagar o registro e fechar o pipe (R4)
            self._idle_lock.release()
            self._idle_lock = None
        self._exit(_protocol.EXIT_IDLE if reason == "idle" else _protocol.EXIT_CLEAN)

    def try_idle_stop(self) -> bool:
        lock = _locks.FileLock(_paths.spawn_lock_name(self.letter))
        try:
            lock.acquire(timeout=0)
        except _locks.LockTimeout:
            return False
        except Exception as exc:
            logger.warning("idle: não consegui a trava de spawn (%s)", type(exc).__name__)
            return False
        with self.lock:
            if self.owner is not None or self.state == "stopping":
                lock.release()
                return False
            self._idle_lock = lock
        logger.info("ocioso há %.0f s sem dono; encerrando", self.config.idle)
        self.stop_clean("idle")
        return True

    # ================================================================= watchdog (D.4 fire)
    def fire_respond(self, job: Job | None, exc: RumbaError) -> None:
        if job is not None and job.fn is None and job.conn is not None:
            self.send_to(job.conn, _protocol.make_error(job.id, exc), lock_timeout=EVENT_LOCK_S)
        self.broadcast("shutting_down", "watchdog")

    def fire_record(self, exit_reason: str) -> None:
        """Grava ``exit_reason`` no registro com orçamento de 0,5 s."""
        thread = threading.Thread(target=self.write_registry, kwargs={"exit_reason": exit_reason},
                                  name="registry", daemon=True)
        thread.start()
        thread.join(0.5)


def _pipe_client_pid(channel: _ipc.Channel) -> int | None:
    return channel.peer_pid(server=True)


def _close_handle(handle: int) -> None:
    if handle:
        try:
            _winapi.CloseHandle(handle)
        except OSError:
            pass
