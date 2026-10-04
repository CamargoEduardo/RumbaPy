"""Neutralização de servidores RumbaPy v0.1.5 ainda vivos na sessão do usuário (E.8).

O servidor 0.1.5 (TCP em ``127.0.0.1``: D 8500, A 8600, Z 8700, outras 8800) segura a conexão HLLAPI do processo
dele; enquanto isso, o ``ConnectPS`` do worker novo dá rc 11. Antes do 1º ``connect`` de ``open``/``attach`` (uma vez
por processo e letra) mandamos ao servidor velho ``ping`` e, se for um 0.1.5 de verdade, ``disconnect`` (libera o
HLLAPI daquele processo). **Nunca** ``close_terminal`` e nunca encerrar o processo: ele fica ocioso até o logoff.
O stub da v2 (``"shim": true``) sai sozinho. Servidor de outra sessão do Windows nunca é tocado (E33).
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable

import threading
import logging
import socket
import json
import os

logger = logging.getLogger(__name__)

PORTS = {"D": 8500, "A": 8600, "Z": 8700}
OTHER_PORT = 8800
SESSION_ID = "rumbapy2-migracao"
CONNECT_TIMEOUT = 0.5
READ_TIMEOUT = 2.0
MAX_ROUNDS = 3
MAX_REPLY = 1 << 20


def port_for(letter: str) -> int:
    return PORTS.get(letter.upper(), OTHER_PORT)


@dataclass
class LegacyStatus:
    """O que a neutralização viu para a letra (``busy`` entra na mensagem do rc 11, R27)."""

    letter: str
    port: int
    present: bool = False
    busy: bool = False
    shim: bool = False
    disconnected: int = 0
    other_session: bool = False
    pids: list[int] = field(default_factory=list)     # ouvintes da porta vistos na nossa sessão do Windows


_DONE: dict[str, LegacyStatus] = {}
_LOCK = threading.Lock()


def status(letter: str) -> LegacyStatus | None:
    """Resultado da neutralização já feita neste processo para a letra (ou ``None``)."""
    with _LOCK:
        return _DONE.get(letter.upper())


def busy_extra(letter: str) -> str:
    """Complemento da mensagem de ``TerminalInUse`` (rc 11) quando um servidor legado ocupado foi visto."""
    st = status(letter)
    if st is not None and st.busy:
        return (f" (um servidor RumbaPy v0.1.5 na porta {st.port} está ocupado e pode estar segurando a conexão; "
                "feche o script antigo ou faça logoff)")
    return ""


def _reset_for_tests() -> None:
    with _LOCK:
        _DONE.clear()


def _process_session_id(pid: int) -> int | None:
    """Sessão do Windows do processo ``pid`` (``ProcessIdToSessionId``); ``None`` se não der para ler."""
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = _kernel32.ProcessIdToSessionId
    fn.restype = wintypes.BOOL
    fn.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    out = wintypes.DWORD(0)
    if not fn(pid, ctypes.byref(out)):
        return None
    return int(out.value)


def _listener_pid(port: int) -> int | None:
    from . import _win32

    return _win32.tcp_listener_pid(port)


def _listener_pids(port: int) -> list[int]:
    """TODOS os PIDs que escutam em ``127.0.0.1:<port>``/``0.0.0.0:<port>`` (com ``SO_REUSEADDR`` pode haver mais de
    um; ``_win32.tcp_listener_pids``, só leitura)."""
    from . import _win32

    return _win32.tcp_listener_pids(port)


def _our_session() -> int:
    from . import _win32

    return _win32.session_id()


class _Busy(Exception):
    """O servidor aceitou a conexão mas não respondeu a tempo (ou nem aceitou)."""


def _request(port: int, payload: dict, *, connect: Callable[..., socket.socket]) -> dict | None:
    """Uma requisição no formato 0.1.5 (JSON, ``shutdown(SHUT_WR)``, lê até EOF). ``None`` = recusado/sem resposta."""
    try:
        sock = connect(("127.0.0.1", port), timeout=CONNECT_TIMEOUT)
    except ConnectionRefusedError:
        return None
    except (TimeoutError, socket.timeout):
        raise _Busy() from None
    except OSError as exc:
        logger.warning("Servidor legado na porta %d: não consegui conectar (%s)", port, type(exc).__name__)
        return None
    try:
        sock.settimeout(READ_TIMEOUT)
        sock.sendall(json.dumps(payload).encode("utf-8"))
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                raise _Busy() from None
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_REPLY:
                break
    except _Busy:
        raise
    except OSError as exc:
        logger.warning("Servidor legado na porta %d: erro de comunicação (%s)", port, type(exc).__name__)
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass
    try:
        reply = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return reply if isinstance(reply, dict) else {}


def _single_to_many(listener_pid: Callable[[int], int | None]) -> Callable[[int], list[int]]:
    def many(port: int) -> list[int]:
        pid = listener_pid(port)
        return [] if pid is None else [pid]

    return many


def neutralize(letter: str, *, port: int | None = None,
               listener_pid: Callable[[int], int | None] = _listener_pid,
               listener_pids: Callable[[int], list[int]] | None = None,
               session_of: Callable[[int], int | None] = _process_session_id,
               our_session: Callable[[], int] = _our_session,
               connect: Callable[..., socket.socket] = socket.create_connection,
               force: bool = False) -> LegacyStatus:
    """Desconecta do HLLAPI um servidor 0.1.5 que escute na porta da letra (E.8). Uma vez por processo e letra
    (``force=True`` refaz). Nunca levanta por causa do servidor legado: problemas viram WARNING.

    ``listener_pids`` (todos os ouvintes da porta) tem precedência; ``listener_pid`` (um só) é aceito para testes."""
    letter = letter.upper()
    if listener_pids is None:
        listener_pids = _listener_pids if listener_pid is _listener_pid else _single_to_many(listener_pid)
    with _LOCK:
        done = _DONE.get(letter)
        if done is not None and not force:
            return done
    st = LegacyStatus(letter, port if port is not None else port_for(letter))
    if port is None and os.environ.get("RUMBAPY_SANDBOX") == "1":
        # no sandbox, as portas reais (8500/8600/8700/8800) podem ser de um servidor 0.1.5 de PRODUÇÃO: não tocar
        logger.warning("Sandbox: neutralização do servidor legado na porta %d pulada", st.port)
        with _LOCK:
            _DONE[letter] = st
        return st
    try:
        _neutralize(st, listener_pids=listener_pids, session_of=session_of, our_session=our_session, connect=connect)
    except Exception as exc:                     # melhor esforço: o legado nunca impede o open/attach
        logger.warning("Neutralização do servidor legado (porta %d) falhou: %s", st.port, type(exc).__name__)
    with _LOCK:
        _DONE[letter] = st
    return st


def _neutralize(st: LegacyStatus, *, listener_pids: Callable[[int], list[int]],
                session_of: Callable[[int], int | None], our_session: Callable[[], int],
                connect: Callable[..., socket.socket]) -> None:
    """Passos 1-4 da E.8. Com um só ouvinte, uma rodada. Com mais de um (``SO_REUSEADDR``), até ``MAX_ROUNDS``
    rodadas de ``ping``/``disconnect`` enquanto um servidor não-shim responder: não dá para saber qual processo
    atendeu a conexão (o ping do 0.1.5 não diz o PID), e o ``disconnect`` é idempotente."""
    mine: int | None = None
    rounds = 1
    done = 0
    while done < rounds:
        done += 1
        pids = sorted({int(p) for p in listener_pids(st.port)})
        if not pids:
            return
        if mine is None:
            mine = our_session()
        for pid in pids:                          # todos da nossa sessão, senão nenhum é tocado (E33)
            theirs = session_of(pid)
            if theirs != mine:
                st.other_session = True
                logger.warning("Servidor na porta %d (PID %d) é de outra sessão do Windows (%s); não vou tocá-lo.",
                               st.port, pid, "desconhecida" if theirs is None else theirs)
                return
        for pid in pids:
            if pid not in st.pids:
                st.pids.append(pid)
        if len(st.pids) > 1:
            rounds = MAX_ROUNDS
        label = ", ".join(str(p) for p in pids)
        ping = {"session_id": SESSION_ID, "action": "ping", "params": {"terminal_type": st.letter}}
        try:
            reply = _request(st.port, ping, connect=connect)
        except _Busy:
            st.present = st.busy = True
            logger.warning("Servidor legado na porta %d (PID %s) não respondeu ao ping: presente e ocupado.",
                           st.port, label)
            return
        if reply is None:
            return                                # ninguém aceitou a conexão
        st.present = True
        if reply.get("success") is not True:
            logger.warning("Servidor na porta %d (PID %s) respondeu ao ping sem success=true; não vou tocá-lo.",
                           st.port, label)
            return
        if reply.get("shim") is True:
            st.shim = True                        # o stub da v2 sai sozinho (outro ouvinte: próxima rodada)
            continue
        disconnect = {"session_id": SESSION_ID, "action": "disconnect", "params": {"terminal_type": st.letter}}
        try:
            answer = _request(st.port, disconnect, connect=connect)
        except _Busy:
            st.busy = True
            logger.warning("Servidor legado na porta %d (PID %s) não respondeu ao disconnect (ocupado).", st.port,
                           label)
            return
        if answer is None or answer.get("success") is not True:
            logger.warning("Servidor legado na porta %d (PID %s) não confirmou o disconnect.", st.port, label)
            return
        st.disconnected += 1
        logger.warning("Servidor legado v0.1.5 (porta %d) desconectado do terminal %s; ficará ocioso até o logoff.",
                       st.port, st.letter)
