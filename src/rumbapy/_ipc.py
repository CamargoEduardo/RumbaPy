"""Transporte do protocolo 2 (C.1, C.2): named pipe com DACL, autenticação HMAC mútua com prazos e framing JSON.

Usado pelos dois lados:

* worker: ``create_pipe`` (1ª instância com ``FILE_FLAG_FIRST_PIPE_INSTANCE``), ``accept`` em fatias de 0,5 s,
  ``server_auth``;
* cliente: ``connect(name, expected_pid)`` (confere o PID do servidor do pipe: ``ChannelHijacked``), ``client_auth``.

Mensagens só por ``send_bytes``/``recv_bytes`` de ``multiprocessing.connection.PipeConnection`` (nunca pickle, nunca
``Client``/``Listener``, R2); toda leitura é ``poll(prazo)`` + ``recv_bytes(maxlength=MAX_MESSAGE)``.
"""
# ruff: noqa: I001
from __future__ import annotations

from multiprocessing.connection import PipeConnection
from collections.abc import Callable
from typing import Any

from .errors import ChannelHijacked, ProtocolError, WorkerError
from . import _protocol, _locks, _win32

import threading
import secrets
import logging
import _winapi
import hashlib
import hmac
import time
import os

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------------------- constantes Win32
PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008          # só clientes locais (C.1): SMB com a mesma credencial fica de fora
PIPE_UNLIMITED_INSTANCES = 255
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_SEM_TIMEOUT = 121
ERROR_PIPE_BUSY = 231
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535
WAIT_TIMEOUT = 0x102
ACCEPT_SLICE_S = 0.5
CLOSE_LOCK_S = 1.0                               # close() espera a escrita em curso terminar (R9a)
KEY_LOCK_S = 10.0                                # locks\key.lock ao recriar uma chave corrompida (C.2)


# PipeNotFound/PipeTimeout/AuthRejected são internos do cliente (C.8): o ``_bridge`` os trata (relê a chave, recicla o
# worker) e eles nunca atravessam o pipe — por isso não estão em ``errors.__all__`` nem em ``errors.from_wire``.
class PipeNotFound(WorkerError):
    """O pipe do registro não existe (o worker morreu ou ainda não criou o pipe)."""


class PipeTimeout(WorkerError, TimeoutError):
    """O pipe existe, mas não aceitou a conexão / não respondeu no prazo."""


class AuthRejected(WorkerError):
    """O worker fechou a conexão durante a autenticação (o ``mc`` do cliente não conferiu: chaves diferentes)."""


class PipeExists(OSError):
    """Já existe um pipe com esse nome (outro worker): saída 11 (D.2)."""


class AuthFailed(Exception):
    """O cliente não provou conhecer a chave (lado do worker; a conexão é fechada sem resposta)."""


# ------------------------------------------------------------------------------------------- chave (C.2)

def read_key(path: str | os.PathLike[str]) -> bytes:
    """Lê ``authkey.bin``: exatamente 32 bytes, senão ``ValueError`` (o worker sai com 13)."""
    with open(path, "rb") as fh:
        key = fh.read(_protocol.AUTH_KEY_BYTES + 1)
    if len(key) != _protocol.AUTH_KEY_BYTES:
        raise ValueError(f"authkey.bin com {len(key)} bytes (esperado {_protocol.AUTH_KEY_BYTES})")
    return key


def load_or_create_key(path: str | os.PathLike[str], *, workers_alive: Callable[[], bool],
                       lock: Callable[[], Any] | None = None) -> bytes:
    """Chave do cliente (C.2, R12): cria "se não existir" com ``O_EXCL`` + ``os.rename`` (nunca ``os.replace``).

    Tamanho errado: recria só sob ``lock()`` e sem worker vivo (``workers_alive()``); senão ``WorkerError`` (variante
    ``authkey_corrupt``). Sem ``lock``, usa ``_locks.file_lock("key")`` (``locks\\key.lock``, prazo ``KEY_LOCK_S``):
    nunca há recriação sem trava. ``lock()`` devolve um objeto já travado, com ``release()``.
    """
    path = os.fspath(path)
    for _attempt in range(3):
        try:
            return read_key(path)
        except FileNotFoundError:
            _create_key(path)
        except ValueError:
            guard = _key_lock() if lock is None else lock()
            try:
                try:
                    return read_key(path)                  # outro processo pode ter acabado de recriar
                except ValueError:
                    pass
                if workers_alive():
                    raise WorkerError.make("authkey_corrupt") from None
                logger.warning("authkey.bin com tamanho inválido; recriando (nenhum processo auxiliar vivo)")
                os.unlink(path)
                _create_key(path)
            finally:
                guard.release()
    return read_key(path)


def _key_lock() -> Any:
    try:
        return _locks.file_lock("key", KEY_LOCK_S)
    except _locks.LockTimeout:
        raise WorkerError(f"authkey.bin está corrompido e outro processo está mexendo nele há mais de {KEY_LOCK_S:g} "
                          f"s; rode de novo.") from None


def _create_key(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, secrets.token_bytes(_protocol.AUTH_KEY_BYTES))
    finally:
        os.close(fd)
    try:
        os.rename(tmp, path)
    except FileExistsError:                            # outro processo criou antes: vale a dele
        os.unlink(tmp)


# ------------------------------------------------------------------------------------------- canal

class Channel:
    """``PipeConnection`` + trava de escrita + JSON (C.1). ``recv`` devolve ``None`` no prazo esgotado."""

    def __init__(self, conn: PipeConnection, *, letter: str | None = None) -> None:
        self.conn = conn
        self.letter = letter
        self.lock = threading.Lock()
        self.closed = False
        self._handle_closed = False

    @property
    def handle(self) -> int:
        return int(self.conn.fileno())

    def peer_pid(self, *, server: bool) -> int | None:
        """PID do outro lado (``server=True``: somos o servidor, queremos o cliente)."""
        try:
            return _win32.pipe_client_pid(self.handle) if server else _win32.pipe_server_pid(self.handle)
        except OSError:
            return None

    def send_msg(self, obj: dict[str, Any], *, lock_timeout: float | None = None) -> bool:
        """Envia; com ``lock_timeout`` a trava é tentada só por esse tempo (``False`` se não deu)."""
        data = _protocol.encode(obj, letter=self.letter)
        if lock_timeout is None:
            acquired = self.lock.acquire()
        else:
            acquired = self.lock.acquire(timeout=lock_timeout)
        if not acquired:
            return False
        try:
            if self.closed:                            # conferido COM a trava: nunca escreve num handle já fechado
                raise BrokenPipeError("canal fechado")
            self.conn.send_bytes(data)
        finally:
            try:
                if self.closed:                        # close() desistiu de esperar: quem escrevia fecha
                    self._close_conn()
            finally:
                self.lock.release()
        return True

    def recv_msg(self, timeout: float) -> dict[str, Any] | None:
        """``poll(timeout)`` + ``recv_bytes(MAX_MESSAGE)``; ``EOFError`` no fim; > 1 MiB -> ``ProtocolError``."""
        if not self.conn.poll(timeout):
            return None
        try:
            data = self.conn.recv_bytes(_protocol.MAX_MESSAGE)
        except OSError as exc:
            if "bad message length" in str(exc):       # PipeConnection fecha a conexão sozinha
                self.closed = True
                raise ProtocolError.make(L=self.letter or "?", detalhe="mensagem maior que 1 MiB",
                                         terminal=self.letter) from None
            raise
        return _protocol.decode(data, letter=self.letter)

    def close(self) -> None:
        """Fecha sob a trava de escrita (R9a): um ``send_msg`` em curso termina antes de o handle ser liberado (o
        valor do handle poderia ser reaproveitado pela próxima instância do pipe e a resposta ir para outra conexão).

        Se a escrita não terminar em ``CLOSE_LOCK_S``, quem está escrevendo fecha ao soltar a trava."""
        self.closed = True
        if not self.lock.acquire(timeout=CLOSE_LOCK_S):
            logger.warning("canal: escrita em curso há mais de %g s; o fechamento fica com quem escreve", CLOSE_LOCK_S)
            return
        try:
            self._close_conn()
        finally:
            self.lock.release()

    def _close_conn(self) -> None:
        """Sempre com a trava de escrita na mão; idempotente."""
        if self._handle_closed:
            return
        self._handle_closed = True
        try:
            self.conn.close()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------- lado do worker

def create_pipe(name: str, *, first: bool, security: _win32.SecurityAttributes) -> int:
    """Uma instância do pipe (C.1). ``first`` usa ``FILE_FLAG_FIRST_PIPE_INSTANCE``: já existe -> ``PipeExists``."""
    open_mode = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
    pipe_mode = PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS
    try:
        return int(_winapi.CreateNamedPipe(name, open_mode, pipe_mode, PIPE_UNLIMITED_INSTANCES,
                                           _protocol.PIPE_BUFFER, _protocol.PIPE_BUFFER, 0, security.address()))
    except OSError as exc:
        if first and getattr(exc, "winerror", None) in (ERROR_ACCESS_DENIED, ERROR_PIPE_BUSY):
            raise PipeExists(exc.errno, f"o pipe {name} já existe", name) from None
        raise


def accept(handle: int, should_stop: Callable[[], bool], *, slice_s: float = ACCEPT_SLICE_S) -> bool:
    """``ConnectNamedPipe`` sobreposto, esperando em fatias (checa ``should_stop``). ``True`` = cliente conectado."""
    try:
        ov = _winapi.ConnectNamedPipe(handle, overlapped=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == ERROR_NO_DATA:   # conectou e já desconectou (Issue 14725)
            return True
        raise
    try:
        while True:
            rc = _winapi.WaitForMultipleObjects([ov.event], False, int(slice_s * 1000))
            if rc != WAIT_TIMEOUT:
                break
            if should_stop():
                ov.cancel()
                return False
    except BaseException:
        ov.cancel()
        raise
    finally:
        _, err = ov.GetOverlappedResult(True)
    return err == 0


def wrap(handle: int, *, letter: str | None = None) -> Channel:
    return Channel(PipeConnection(handle), letter=letter)


def _mac(key: bytes, tag: bytes, a: bytes, b: bytes) -> str:
    return hmac.new(key, tag + a + b, hashlib.sha256).hexdigest()


def _hex32(value: Any) -> bytes | None:
    if not isinstance(value, str) or len(value) != 2 * _protocol.AUTH_NONCE_BYTES:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def server_auth(channel: Channel, key: bytes, *, timeout: float = _protocol.AUTH_STEP_S) -> None:
    """Lado do worker (C.2). Cliente mudo, mensagem inválida ou ``mc`` errado -> ``AuthFailed``."""
    nw = secrets.token_bytes(_protocol.AUTH_NONCE_BYTES)
    channel.send_msg({"t": "auth", "v": _protocol.AUTH_VERSION, "nw": nw.hex()})
    try:
        reply = channel.recv_msg(timeout)
    except ProtocolError as exc:
        raise AuthFailed("mensagem inválida na autenticação") from exc
    if reply is None:
        raise AuthFailed(f"o cliente não respondeu à autenticação em {timeout:g} s")
    nc = _hex32(reply.get("nc"))
    mc = reply.get("mc")
    if reply.get("t") != "auth" or nc is None or not isinstance(mc, str):
        raise AuthFailed("resposta de autenticação malformada")
    if not hmac.compare_digest(mc, _mac(key, _protocol.AUTH_TAG_CLIENT, nw, nc)):
        raise AuthFailed("o cliente não conhece a chave")
    channel.send_msg({"t": "auth_ok", "mw": _mac(key, _protocol.AUTH_TAG_WORKER, nc, nw)})


# ------------------------------------------------------------------------------------------- lado do cliente

def client_auth(channel: Channel, key: bytes, *, server_pid: int | None = None,
                timeout: float = _protocol.AUTH_STEP_S) -> None:
    """Lado do cliente (C.2). ``mw`` inválido -> ``ChannelHijacked``; worker mudo -> ``PipeTimeout``."""
    letter = channel.letter or "?"
    hello = channel.recv_msg(timeout)
    if hello is None:
        raise PipeTimeout(f"O processo auxiliar do terminal {letter} não iniciou a autenticação em {timeout:g} s.",
                          terminal=channel.letter)
    nw = _hex32(hello.get("nw"))
    if hello.get("t") != "auth" or hello.get("v") != _protocol.AUTH_VERSION or nw is None:
        raise ProtocolError.make(L=letter, detalhe="autenticação malformada", terminal=channel.letter)
    nc = secrets.token_bytes(_protocol.AUTH_NONCE_BYTES)
    channel.send_msg({"t": "auth", "nc": nc.hex(), "mc": _mac(key, _protocol.AUTH_TAG_CLIENT, nw, nc)})
    try:
        ok = channel.recv_msg(timeout)
    except (EOFError, BrokenPipeError, ConnectionError):
        # o worker fecha sem responder quando o mc não confere (chaves diferentes dos dois lados)
        raise AuthRejected(f"O processo auxiliar do terminal {letter} recusou a autenticação (chave diferente).",
                           terminal=channel.letter) from None
    if ok is None:
        raise PipeTimeout(f"O processo auxiliar do terminal {letter} não concluiu a autenticação em {timeout:g} s.",
                          terminal=channel.letter)
    mw = ok.get("mw")
    if ok.get("t") != "auth_ok" or not isinstance(mw, str) or \
            not hmac.compare_digest(mw, _mac(key, _protocol.AUTH_TAG_WORKER, nc, nw)):
        raise ChannelHijacked.make(pid=server_pid if server_pid is not None else "?", L=letter,
                                   terminal=channel.letter)


def connect(name: str, expected_pid: int, timeout: float = _protocol.CONNECT_TIMEOUT, *,
            letter: str | None = None) -> Channel:
    """Abre o pipe ``name`` (C.1): ``PipeNotFound`` (erro 2), ``PipeTimeout`` (ocupado até o prazo) ou
    ``ChannelHijacked`` (``GetNamedPipeServerProcessId != expected_pid``)."""
    deadline = time.monotonic() + timeout
    shown = letter or "?"
    while True:
        try:
            _winapi.WaitNamedPipe(name, _protocol.WAIT_NAMED_PIPE_MS)
        except OSError as exc:
            code = getattr(exc, "winerror", None)
            if code == ERROR_FILE_NOT_FOUND:
                raise PipeNotFound(f"O canal do terminal {shown} não existe.", terminal=letter) from None
            if code != ERROR_SEM_TIMEOUT:
                raise
        else:
            try:
                handle = _winapi.CreateFile(name, GENERIC_READ | GENERIC_WRITE, 0, _winapi.NULL, OPEN_EXISTING,
                                            FILE_FLAG_OVERLAPPED, _winapi.NULL)
            except OSError as exc:
                code = getattr(exc, "winerror", None)
                if code == ERROR_FILE_NOT_FOUND:
                    raise PipeNotFound(f"O canal do terminal {shown} não existe.", terminal=letter) from None
                if code != ERROR_PIPE_BUSY:
                    raise
            else:
                return _finish_connect(int(handle), expected_pid, letter)
        if time.monotonic() >= deadline:
            raise PipeTimeout(f"O canal do terminal {shown} não aceitou a conexão em {timeout:g} s.", terminal=letter)


def _finish_connect(handle: int, expected_pid: int, letter: str | None) -> Channel:
    try:
        _winapi.SetNamedPipeHandleState(handle, PIPE_READMODE_MESSAGE, None, None)
        server = _win32.pipe_server_pid(handle)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise
    if server != expected_pid:
        _winapi.CloseHandle(handle)
        raise ChannelHijacked.make(pid=server, L=letter or "?", terminal=letter)
    return wrap(handle, letter=letter)
