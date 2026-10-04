"""API pública da v2 (B.2-B.4): ``rumbapy.open/attach/close`` e a classe ``Session``.

A ``Session`` fala só com um ``Backend`` (C.5): leituras e esperas são locais ao emulador; só ``press``/``enter``/
``clear``/``pf``/``pa``/``send_keys`` com AID transmitem ao host. Regras que o usuário pode confiar (B.4):

* antes de toda tecla espera-se o teclado livre; antes de toda AID, tela estável (2 snapshots iguais e livres);
* a AID vai sozinha no último ``WD_SendKey`` e a espera pós-AID compara com a tela tirada **depois** da digitação;
* exceções de entrada dizem ``delivery``; com ``"partial"``/``"unknown"`` a sessão fica ``uncertain`` e recusa nova
  entrada (``SessionUncertain``) até uma espera de conteúdo bem-sucedida ou ``acknowledge()``;
* nunca há reenvio automático de entrada; texto escrito/digitado nunca vai a log.

Injeção para testes (I2, J.3): ``_set_factories(backend_factory=..., user32_factory=..., launcher_factory=...)``.

* ``backend_factory(profile, python32)`` devolve ``get_backend(letra, modo) -> Backend`` (``modo`` = ``"owner"`` ou
  ``"admin"``; padrão: ``_bridge.get_worker`` através do ``_launcher``, pacote P-D/P-C);
* ``user32_factory()`` devolve o ``User32`` (padrão: ``RealUser32``, que respeita o sandbox);
* ``launcher_factory(user32, get_backend)`` devolve o orquestrador de janelas (padrão: ``_launcher.Launcher``) com
  ``open(...)``/``attach(...)`` (-> ``Opened(backend, info, profile, screen)``), ``close_all(...)`` (-> nº de
  janelas fechadas) e ``window_info(profile, hwnd)``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal

from .errors import (FieldNotFound, HostBusy, NoHostResponse, NotConnected, ParameterError, ProtectedField,
                     RecipeNotAvailable, RumbaError, SessionReplaced, SessionUncertain, TerminalNotOpen, Truncated,
                     WaitTimeout, HostConnectionLost, RecipeError, TerminalInUse, EncodingError)
from ._types import FieldRead, OpenInfo, Profile, ReadResult, WindowInfo
from ._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE, User32
from .screen import Cursor, Screen
from .recipes import Cics, Ims
from ._backend import Backend
from .keys import AIDS, Key
from . import keys as _keys
from . import _protocol
from . import profiles
from . import _codec
from . import waits

import threading
import weakref
import logging
import math
import time
import sys
import os
import re

logger = logging.getLogger(__name__)

WindowMode = Literal["normal", "minimized"]           # "hidden" cortado (I16)
SessionState = Literal["ok", "uncertain", "replaced", "closed"]
GetBackend = Callable[[str, str], Backend]            # (letra, "owner" | "admin") -> Backend

OPEN_TIMEOUT = 90.0                 # RUMBAPY_OPEN_TIMEOUT (B.11)
ATTACH_TIMEOUT = 10.0               # attach / reconnect
CLOSE_TIMEOUT = 15.0
RECONNECT_POLL = 0.5                # rc 1 (ainda conectando) -> nova tentativa
OMITTED = "<conteúdo omitido>"      # texto de tela que não pode ir a mensagens (hidden_unknown, D.6)
NO_RESPONSE_HINT = ("NÃO repita a tecla: confira a tela (print(e.screen)) e reancore com t.wait_text/t.wait_any "
                    "(ou t.acknowledge()).")
_READ_BUSY_HINT = "espere o host liberar (t.wait_ready()) e leia de novo."
INPUT_UNKNOWN = "Não sei se a entrada chegou ao host."   # = sufixo das variantes "input" (WorkerCrashed/WorkerHung)


# ------------------------------------------------------------------------------------------- utilidades

def _seconds(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ParameterError.make(detalhe=f"{name} deve ser um número de segundos finito e >= 0 (recebi {value!r}).")
    return float(value)


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(raw)
    except ValueError:
        logger.warning("%s inválido (%r); uso %g s", name, raw, default)
        return default
    return value


def default_timeout() -> float:
    """Prazo padrão das esperas: ``RUMBAPY_TIMEOUT`` ou 30 s (B.11)."""
    return _env_seconds("RUMBAPY_TIMEOUT", waits.DEFAULT_TIMEOUT)


def _letters(kind: str) -> str:
    return ", ".join(p.letter for p in profiles.PROFILES.values() if p.kind == kind)


def _bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ParameterError.make(detalhe=f"{name} deve ser True ou False (recebi {value!r}).")
    return value


def _int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError.make(detalhe=f"{name} deve ser um número inteiro (recebi {type(value).__name__}).")
    return value


def _shown(screen: Screen, text: str) -> str:
    """Trecho da tela que pode ir a uma mensagem (nunca com a máscara desconhecida)."""
    return OMITTED if screen.hidden_unknown else text


def _line1(screen: Screen) -> str:
    return _shown(screen, screen.line(1).rstrip())


def _oia_tail(screen: Screen) -> str:
    """Fim de toda mensagem de espera de conteúdo (G.4): termina com ``oia.describe()``."""
    return f" Estado do terminal: {screen.oia.describe()}"


def _g8(screen: Screen) -> str:
    return " ".join(f"{b:02X}" for b in screen.oia.inhibit)


def _same(a: Screen, b: Screen) -> bool:
    return a.text == b.text and a.cursor == b.cursor


def _hidden_attr(attr: int) -> bool:
    """Atributo 3270 com intensidade ``11`` (não exibido: senha) — a mesma regra do worker (``_worker.mask``)."""
    return (int(attr) >> 2) & 3 == 3


def _quoted(value: str | re.Pattern[str]) -> str:
    return value.pattern if isinstance(value, re.Pattern) else value


# ------------------------------------------------------------------------------------------- fábricas (I2)

def _default_backend_factory(profile: Profile, python32: str | os.PathLike | None) -> GetBackend:
    """``get_backend(letra, modo)`` sobre o processo auxiliar 32-bit (``_bridge.get_worker``, pacote P-D).

    A ``session`` não importa ``_bridge`` (A.3): usa a mesma ponte que o ``_launcher`` (P-C) já monta.
    """
    from . import _launcher                  # tardio: P-C (Onda 2)
    return _launcher._default_get_backend(python32)


def _default_user32_factory() -> User32:
    from ._user32 import RealUser32          # tardio: P-C (Onda 2)
    return RealUser32()


def _default_launcher_factory(user32: User32, get_backend: GetBackend) -> Any:
    from ._launcher import Launcher          # tardio: P-C (Onda 2)
    return Launcher(user32, get_backend)


_backend_factory: Callable[[Profile, Any], GetBackend] = _default_backend_factory
_user32_factory: Callable[[], User32] = _default_user32_factory
_launcher_factory: Callable[[User32, GetBackend], Any] = _default_launcher_factory
_UNSET: Any = object()


def _set_factories(*, backend_factory: Any = _UNSET, user32_factory: Any = _UNSET,
                   launcher_factory: Any = _UNSET) -> None:
    """Troca as fábricas usadas por ``open``/``attach``/``close`` (testes). ``None`` volta ao padrão."""
    global _backend_factory, _user32_factory, _launcher_factory
    if backend_factory is not _UNSET:
        _backend_factory = backend_factory or _default_backend_factory
    if user32_factory is not _UNSET:
        _user32_factory = user32_factory or _default_user32_factory
    if launcher_factory is not _UNSET:
        _launcher_factory = launcher_factory or _default_launcher_factory


def _require_user_role() -> None:
    """``open/attach/close`` e ``RumbaClient()`` só nos papéis ``user``/``cli`` (A.7)."""
    package = sys.modules.get(__package__ or "rumbapy")
    role = getattr(package, "_ROLE", "user")
    if role not in ("user", "cli"):
        raise RumbaError.make("legacy_shim")


def _ensure_latest() -> None:
    from . import _update                    # H.1: open/attach/RumbaClient reconsultam pelo TTL
    _update.ensure_latest(context="open")


def _launcher_for(profile: Profile, python32: Any) -> tuple[Any, User32]:
    user32 = _user32_factory()
    return _launcher_factory(user32, _backend_factory(profile, python32)), user32


# ------------------------------------------------------------------------------------------- registro no processo
# Quem libera o quê (I8): cada Session conectada segura um "_Hold". ``backend.release()`` (fim do lease) só é
# chamado quando nenhuma outra Session do processo segura o MESMO objeto Backend. O GC (weakref.finalize) e o atexit
# também devolvem. Se o _bridge entregar objetos distintos que dividem a conexão, a contagem fina é dele (C.6.6).

class _Hold:
    __slots__ = ("backend", "letter", "active")

    def __init__(self, backend: Backend, letter: str) -> None:
        self.backend = backend
        self.letter = letter
        self.active = False


_lock = threading.RLock()
_holds: list[_Hold] = []
_sessions: dict[str, weakref.WeakSet[Session]] = {}


def _acquire(hold: _Hold) -> None:
    with _lock:
        if not hold.active:
            hold.active = True
            _holds.append(hold)


def _drop(hold: _Hold) -> None:
    with _lock:
        if not hold.active:
            return
        hold.active = False
        _holds.remove(hold)
        shared = any(other.backend is hold.backend for other in _holds)
    if shared:
        return
    try:
        hold.backend.release()
    except Exception as exc:                 # nunca falha no GC/atexit
        logger.warning("release do terminal %s falhou (%s)", hold.letter, type(exc).__name__)


def _sessions_of(letter: str) -> list[Session]:
    with _lock:
        return list(_sessions.get(letter, ()))


def _replace_others(letter: str, keep: Session | None = None) -> None:
    """Outra ``open()``/``close()`` na letra: as Session anteriores deste processo passam a ``replaced``."""
    for other in _sessions_of(letter):
        if other is not keep and other.state in ("ok", "uncertain"):
            other._set_replaced()


# ------------------------------------------------------------------------------------------- funções de módulo (B.2)

def open(terminal: str = "D", *, reuse: bool = False, window: WindowMode = "normal",
         timeout: float | None = None, close_on_exit: bool = False,
         python32: str | os.PathLike | None = None) -> Session:
    """Abre o terminal e devolve a ``Session`` conectada (nada é enviado ao host).

    ``reuse=False`` (padrão) fecha **todas** as instâncias do perfil — inclusive as de outro script, cujo lease é
    revogado — e abre uma nova pelo Host Access Web Launcher. ``reuse=True`` só conecta se houver exatamente uma
    instância saudável. ``timeout=None`` = ``RUMBAPY_OPEN_TIMEOUT`` ou 90 s. ``window="minimized"`` minimiza sem
    ativar. ``close_on_exit=True`` fecha o terminal ao sair do ``with``.
    """
    _require_user_role()
    profile = profiles.resolve(terminal)
    _bool("reuse", reuse)
    _bool("close_on_exit", close_on_exit)
    if window not in ("normal", "minimized"):
        raise ParameterError.make(detalhe=f"window deve ser 'normal' ou 'minimized' (recebi {window!r}).")
    total = _env_seconds("RUMBAPY_OPEN_TIMEOUT", OPEN_TIMEOUT) if timeout is None else _seconds("timeout", timeout)
    _ensure_latest()
    launcher, user32 = _launcher_for(profile, python32)
    opened = launcher.open(profile, reuse=reuse, window=window, timeout=total)
    _replace_others(profile.letter)          # 2ª open() no processo: as Session anteriores da letra são substituídas
    return Session(opened.backend, profile, opened.info, timeout=default_timeout(), user32=user32,
                   close_on_exit=close_on_exit)


def attach(terminal: str = "D", *, timeout: float = ATTACH_TIMEOUT,
           python32: str | os.PathLike | None = None) -> Session:
    """Conecta a um terminal já aberto, sem abrir nem fechar janelas (não destrutivo).

    Nenhuma instância -> ``TerminalNotOpen``; 2+ instâncias -> ``MultipleInstances``; outro script com o lease ->
    ``TerminalInUse``.
    """
    _require_user_role()
    profile = profiles.resolve(terminal)
    limit = _seconds("timeout", timeout)
    _ensure_latest()
    launcher, user32 = _launcher_for(profile, python32)
    opened = launcher.attach(profile, timeout=limit)
    return Session(opened.backend, profile, opened.info, timeout=default_timeout(), user32=user32)


def close(terminal: str, *, timeout: float = CLOSE_TIMEOUT) -> int:
    """Fecha **todas** as instâncias do perfil (de qualquer script, revogando o lease); devolve quantas fechou.

    Não exige processo auxiliar nem lease. As ``Session`` deste processo naquela letra passam a ``replaced``.
    """
    _require_user_role()
    profile = profiles.resolve(terminal)
    limit = _seconds("timeout", timeout)
    launcher, _ = _launcher_for(profile, None)
    try:
        closed = launcher.close_all(profile, timeout=limit)
    finally:
        _replace_others(profile.letter)
    return int(closed)


# ------------------------------------------------------------------------------------------- Session (B.3)

class Session:
    """Um terminal conectado. Use ``rumbapy.open("D")`` ou ``rumbapy.attach("D")`` para obter uma.

    Estados (``state``): ``ok``; ``uncertain`` (a última entrada pode ter chegado ao host: leituras e esperas
    funcionam, entradas levantam ``SessionUncertain``); ``replaced`` (a instância foi fechada/reaberta: toda op levanta
    ``SessionReplaced``); ``closed`` (depois de ``close()``: toda op levanta ``TerminalNotOpen``).
    """

    def __init__(self, backend: Backend, profile: Profile, info: OpenInfo, *, timeout: float = waits.DEFAULT_TIMEOUT,
                 user32: User32 | None = None, close_on_exit: bool = False) -> None:
        if not isinstance(profile, Profile):
            raise ParameterError.make(detalhe=f"profile deve ser Profile (recebi {type(profile).__name__}).")
        if not isinstance(info, OpenInfo):
            raise ParameterError.make(detalhe=f"info deve ser OpenInfo (recebi {type(info).__name__}).")
        self._backend = backend
        self.profile = profile
        self.terminal = profile.letter
        self.info = info
        self.rows = info.rows
        self.cols = info.cols
        self.timeout = _seconds("timeout", timeout)
        self.state: SessionState = "ok"
        self.close_on_exit = _bool("close_on_exit", close_on_exit)
        self.error_checks: list[Callable[[Screen], None]] = [waits.cics_error_check] if profile.kind == "cics" else []
        self._user32 = user32
        self._hwnd = info.hwnd
        self._connected = True
        self._aid_attempts = 0                  # AIDs que podem ter chegado ao host: as receitas usam (I15)
        self._recipes: dict[str, Any] = {}
        self._clock: Callable[[], float] = time.monotonic
        self._sleep: Callable[[float], object] = time.sleep
        self._hold = _Hold(backend, self.terminal)
        _acquire(self._hold)
        self._finalizer = weakref.finalize(self, _drop, self._hold)   # GC e atexit devolvem o lease (I8)
        with _lock:
            _sessions.setdefault(self.terminal, weakref.WeakSet()).add(self)
        backend.bind_instance(info.hwnd)        # toda op com checks_hwnd leva este hwnd (C.6.5)

    # ------------------------------------------------------------------ estado
    def __repr__(self) -> str:
        pid = getattr(self._backend, "worker_pid", None) or getattr(self._backend, "pid", None)
        worker = f" worker={pid}" if isinstance(pid, int) and not isinstance(pid, bool) else ""
        return f"<Session {self.terminal} {self.profile.name} {self.rows}x{self.cols} {self.state}{worker}>"

    @property
    def connected(self) -> bool:
        """Conectado ao HLLAPI e segurando o lease (``False`` depois de ``release``/``disconnect``/``close``)."""
        return self._connected and self._hold.active and self.state in ("ok", "uncertain")

    @property
    def cics(self) -> Cics:
        """Receitas CICS (perfis D e Z); outros perfis -> ``RecipeNotAvailable``."""
        return self._recipe("cics", Cics)

    @property
    def ims(self) -> Ims:
        """Receitas IMS (perfil A); outros perfis -> ``RecipeNotAvailable``."""
        return self._recipe("ims", Ims)

    def _recipe(self, kind: str, cls: Callable[[Session], Any]) -> Any:
        if self.profile.kind != kind:
            raise RecipeNotAvailable.make(receita=kind, tipo=kind.upper(), letras=_letters(kind),
                                          terminal=self.terminal)
        if kind not in self._recipes:
            self._recipes[kind] = cls(self)
        return self._recipes[kind]

    def _set_replaced(self) -> None:
        if self.state != "closed":
            if self.state != "replaced":
                logger.info("sessão %s substituída (instância fechada/reaberta)", self.terminal)
            self.state = "replaced"
        _drop(self._hold)

    def _mark_uncertain(self) -> None:
        if self.state == "ok":
            self.state = "uncertain"
            logger.info("sessão %s incerta: a última entrada pode ter chegado ao host", self.terminal)

    def _content_confirmed(self) -> None:
        """Espera de conteúdo bem-sucedida: o script reancorou pela tela (sai de ``uncertain``)."""
        if self.state == "uncertain":
            self.state = "ok"
            logger.info("sessão %s confirmada por espera de conteúdo", self.terminal)

    def _timeout(self, value: float | None) -> float:
        return self.timeout if value is None else _seconds("timeout", value)

    def _guard_alive(self) -> None:
        if self.state == "closed":
            raise TerminalNotOpen.make(L=self.terminal, terminal=self.terminal, rc=1)
        if self.state == "replaced":
            raise SessionReplaced.make(L=self.terminal, terminal=self.terminal)

    def _guard_connected(self) -> None:
        self._guard_alive()
        if not self._connected:
            raise NotConnected.make(L=self.terminal, terminal=self.terminal, rc=1)
        _acquire(self._hold)                    # depois de release(): a próxima op retoma o lease

    def _guard_input(self) -> None:
        self._guard_connected()
        if self.state == "uncertain":
            raise SessionUncertain.make(L=self.terminal, terminal=self.terminal)

    def _absorb(self, exc: RumbaError) -> None:
        """Ajusta o estado da Session a partir de uma exceção (B.3) e completa ``exc.terminal``."""
        if exc.terminal is None:
            exc.terminal = self.terminal
        if isinstance(exc, SessionReplaced):
            self._set_replaced()
        elif isinstance(exc, (NoHostResponse, HostConnectionLost)) or exc.delivery in ("partial", "unknown"):
            self._mark_uncertain()
        elif isinstance(exc, NotConnected):
            self._connected = False

    @contextmanager
    def _op(self) -> Iterator[None]:
        """Envolve uma operação pública: toda ``RumbaError`` atualiza o estado e ganha ``terminal``."""
        try:
            yield
        except RumbaError as exc:
            self._absorb(exc)
            raise

    def _call(self, fn: Callable[..., Any], *args: Any, input_op: bool = False, **kwargs: Any) -> Any:
        """Chama o ``Backend``. Ctrl+C durante ``write``/``send``/``set_cursor`` deixa a sessão ``uncertain``."""
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            if input_op:
                self._mark_uncertain()
            raise
        except RumbaError as exc:
            self._absorb(exc)
            raise

    # ------------------------------------------------------------------ ciclo de vida
    def acknowledge(self) -> Session:
        """Sai de ``uncertain``: o script assume a tela atual como ponto de partida."""
        with self._op():
            self._guard_alive()
            if self.state == "uncertain":
                self.state = "ok"
                logger.info("sessão %s confirmada por acknowledge()", self.terminal)
            return self

    def release(self) -> None:
        """Devolve o lease (se nenhuma outra Session do processo usa o mesmo terminal); o HLLAPI continua conectado.

        A próxima operação desta Session retoma o lease sozinha.
        """
        _drop(self._hold)

    def disconnect(self) -> None:
        """``WD_DisconnectPS`` + ``release()``. Depois, só ``reconnect()`` volta a operar."""
        with self._op():
            self._guard_alive()
            try:
                if self._connected:
                    _acquire(self._hold)
                    self._call(self._backend.disconnect)
            finally:
                self._connected = False
                self.release()

    def reconnect(self, timeout: float = ATTACH_TIMEOUT) -> Session:
        """Reconecta ao HLLAPI da MESMA instância (outra instância -> ``SessionReplaced``)."""
        with self._op():
            self._guard_alive()
            limit = _seconds("timeout", timeout)
            _acquire(self._hold)
            start = self._clock()
            while True:
                result = self._call(self._backend.connect)
                if result.connected:
                    break
                if self._clock() - start >= limit:
                    raise NotConnected.make(L=self.terminal, terminal=self.terminal, rc=result.rc,
                                            function="WD_ConnectPS")
                self._sleep(RECONNECT_POLL)
            if self._hwnd is not None and result.hwnd is not None and result.hwnd != self._hwnd:
                self._set_replaced()
                raise SessionReplaced.make(L=self.terminal, terminal=self.terminal)
            if self._hwnd is not None and result.hwnd is None:
                # GetSessionHWND = 0: não dá para provar que é a MESMA instância; não religa ao hwnd antigo (o worker
                # responderia SessionReplaced na próxima op, com a mensagem errada). Falha segura e explicada.
                raise NotConnected.make(L=self.terminal, terminal=self.terminal, function="WD_GetSessionHWND",
                                        hint=f"tente t.reconnect() de novo ou rumbapy.attach('{self.terminal}').",
                                        details={"motivo": "janela da sessão não identificada na reconexão"})
            if self._hwnd is None:
                self._hwnd = result.hwnd
            self._backend.bind_instance(self._hwnd)
            if result.rows and result.cols:
                self.rows, self.cols = result.rows, result.cols
            self._connected = True
            return self

    def close(self) -> None:
        """``rumbapy.close(letra)``: fecha todas as instâncias do perfil; ``state`` vira ``closed``.

        Em ``replaced`` levanta ``SessionReplaced`` sem fechar nada: a instância atual da letra é OUTRA (de outra
        ``Session`` ou de outro script); para fechá-la de propósito use ``rumbapy.close(letra)`` (A.11 c).
        """
        if self.state == "closed":
            return
        if self.state == "replaced":
            raise SessionReplaced.make(L=self.terminal, terminal=self.terminal)
        self._confirm_instance()
        self._close_now()

    def _confirm_instance(self) -> None:
        """Antes de fechar (A.11 c / A.14): confirma com o worker, por uma op barata com ``checks_hwnd``
        (``status``), que esta Session ainda é a dona da MESMA instância.

        Um ``evict`` parado no pipe (outro script reabriu a letra) ou um hwnd diferente -> ``SessionReplaced`` (a
        sessão vira ``replaced``); outro script com o lease -> ``TerminalInUse``. Nos dois casos nada é fechado.
        ``NotConnected``/``TerminalNotOpen`` (instância já ausente) e falhas do processo auxiliar seguem para o
        fechamento (``rumbapy.close`` não depende do worker). Resta uma janela TOCTOU estreita e inevitável entre esta
        sonda e o ``close_all``.
        """
        if not self._connected:
            return
        was_active = self._hold.active
        try:
            _acquire(self._hold)
            self._call(self._backend.status)
        except (SessionReplaced, TerminalInUse):
            if not was_active:
                _drop(self._hold)                    # volta ao estado anterior (lease devolvido)
            raise
        except RumbaError as exc:
            logger.info("close %s: conferência da instância falhou (%s); fechando assim mesmo", self.terminal,
                        type(exc).__name__)

    def _close_now(self) -> None:
        try:
            close(self.terminal)
        finally:
            self.state = "closed"
            self._connected = False
            _drop(self._hold)

    def __enter__(self) -> Session:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """``release()``; com ``close_on_exit``, ``close()``. Nunca mascara a exceção que estiver saindo do ``with``."""
        try:
            try:
                if self.close_on_exit and self.state == "replaced":
                    # outra open() substituiu a instância: fechar agora derrubaria o terminal NOVO (e o lease alheio)
                    logger.info("saída do with no terminal %s: sessão substituída, close_on_exit ignorado",
                                self.terminal)
                elif self.close_on_exit and self.state != "closed":
                    try:
                        self._confirm_instance()         # ANTES do release: o lease ainda é desta Session
                    except (SessionReplaced, TerminalInUse) as stale:
                        # a instância é de outro script agora (evict ainda não lido / lease alheio): não fecha
                        logger.info("saída do with no terminal %s: %s, close_on_exit ignorado", self.terminal,
                                    type(stale).__name__)
                    else:
                        self._close_now()
            finally:
                self.release()                           # sem efeito se o close já devolveu o lease
        except Exception as cleanup:
            if exc_type is None:
                raise
            logger.warning("saída do with no terminal %s falhou (%s)", self.terminal, type(cleanup).__name__)
        return None

    # ------------------------------------------------------------------ leitura (local; repete 1x após reciclagem)
    def _pos(self, row: object, col: object) -> int:
        return _codec.to_pos(_int("row", row), _int("col", col), self.rows, self.cols)

    def _snap(self, fields: bool = False) -> Screen:
        return self._call(self._backend.snapshot, fields=fields)

    @property
    def screen(self) -> Screen:
        """A tela atual (= ``snapshot()``)."""
        return self.snapshot()

    def snapshot(self, *, fields: bool = False) -> Screen:
        """Texto + cursor + OIA (e os campos, com ``fields=True``). Conteúdo oculto vem em branco."""
        with self._op():
            self._guard_connected()
            return self._snap(_bool("fields", fields))

    def _read_raw(self, row: int, col: int, length: int, *, timeout: float | None = None) -> ReadResult:
        """``read`` completo (``nul_at``/``nul_tail`` para a compat); com ``busy`` repete até ``timeout``
        (``None`` = ``Session.timeout``)."""
        self._guard_connected()
        limit = self._timeout(timeout)
        pos = self._pos(row, col)
        _codec.check_span(pos, _int("length", length), self.rows, self.cols)
        start = self._clock()
        while True:
            result: ReadResult = self._call(self._backend.read, row, col, length)
            if not result.busy:
                return result
            elapsed = self._clock() - start
            if elapsed >= limit:
                raise HostBusy.make(rc=4, function="WD_CopyPSToString", terminal=self.terminal,
                                    hint=_READ_BUSY_HINT, details={"waited_s": round(elapsed, 3)})
            self._sleep(min(waits.poll_interval(elapsed), max(0.0, limit - elapsed)))

    def read(self, row: int, col: int, length: int) -> str:
        """Exatamente ``length`` caracteres a partir de ``(row, col)`` (NUL vira espaço; campo oculto em branco)."""
        with self._op():
            return self._read_raw(row, col, length).text

    def _read_field_raw(self, row: int, col: int) -> FieldRead:
        self._guard_connected()
        self._pos(row, col)
        return self._call(self._backend.read_field, row, col)

    def read_field(self, row: int, col: int) -> str:
        """Texto do campo que contém ``(row, col)``, no tamanho real do campo."""
        with self._op():
            return self._read_field_raw(row, col).text

    def find(self, text: str | re.Pattern[str], *, case: bool = True) -> Cursor | None:
        """``self.screen.find(...)``: primeira ocorrência na tela, ou ``None``."""
        with self._op():
            return self.screen.find(text, case=case)

    # ------------------------------------------------------------------ escrita local (vai ao host na próxima AID)
    def _encode(self, text: object, *, secret: bool, errors: str) -> bytes:
        if not isinstance(text, str):
            raise ParameterError.make(detalhe=f"O texto deve ser str (recebi {type(text).__name__}).")
        return _codec.encode(text, host=self.profile.kind, errors=errors, secret=secret)  # type: ignore[arg-type]

    def _write_wire(self, row: int, col: int, data: bytes, secret: bool) -> None:
        self._call(self._backend.write, row, col, data.decode(_codec.ENCODING), secret=secret, input_op=True)

    def _field_room(self, row: int, col: int) -> int:
        """Quantas posições de dados restam no campo desprotegido a partir de ``(row, col)``."""
        return self._field_space(row, col)[0]

    def _field_space(self, row: int, col: int) -> tuple[int, bool]:
        """``(posições de dados restantes no campo desprotegido a partir de (row, col), campo oculto?)``."""
        pos = self._pos(row, col)
        found: FieldRead = self._call(self._backend.read_field, row, col)
        if found.attr & 0x20:
            raise ProtectedField.make(r=row, c=col, terminal=self.terminal)
        start = _codec.to_pos(found.start[0], found.start[1], self.rows, self.cols)
        offset = (pos - start) % (self.rows * self.cols)
        if offset >= found.length:              # byte de atributo (ou fora do campo): não é posição de dados
            if found.length == 0:
                raise FieldNotFound.make(r=row, c=col, terminal=self.terminal)
            raise ProtectedField.make(r=row, c=col, terminal=self.terminal)
        return found.length - offset, _hidden_attr(found.attr)

    def _hidden_at(self, row: int, col: int) -> bool:
        """O campo em ``(row, col)`` é oculto (senha)? Na dúvida (falha ao ler o atributo) responde ``True``."""
        try:
            found: FieldRead = self._call(self._backend.read_field, row, col)
        except FieldNotFound:
            return False                          # tela não formatada: não há campo oculto
        except RumbaError:
            return True
        return _hidden_attr(found.attr)

    def write(self, row: int, col: int, text: str, *, secret: bool = False,
              errors: Literal["strict", "replace"] = "strict") -> Session:
        """Copia ``text`` para o PS local em ``(row, col)`` (``WD_CopyStringToPS``); nada vai ao host agora.

        Campo protegido -> ``ProtectedField``; texto maior que o campo -> ``Truncated`` (antes de copiar).
        ``secret=True`` para senhas: nenhuma mensagem cita o valor nem o tamanho.
        """
        with self._op():
            secret = _bool("secret", secret)
            self._guard_input()
            pos = self._pos(row, col)
            try:
                data = self._encode(text, secret=secret, errors=errors)
            except EncodingError:
                # campo oculto (senha) sem secret=True (ex.: compat copy_string_to_ps): a mensagem não cita o caractere
                if secret or not self._hidden_at(row, col):
                    raise
                data = self._encode(text, secret=True, errors=errors)
            if not data:
                raise ParameterError.make(detalhe="Texto vazio: nada para escrever (para apagar um campo use "
                                                  "t.fill(row, col, '')).")
            _codec.check_span(pos, len(data), self.rows, self.cols)
            self._write_wire(row, col, data, secret)
            return self

    def fill(self, row: int, col: int, text: str, *, align: Literal["left", "right"] = "left",
             secret: bool = False, errors: Literal["strict", "replace"] = "strict") -> Session:
        """Preenche o campo a partir de ``(row, col)`` até o fim, completando com espaços (``fill(r, c, "")`` apaga).

        ``align="right"`` alinha à direita. Texto maior que o espaço do campo -> ``Truncated`` sem escrever nada.
        """
        with self._op():
            secret = _bool("secret", secret)
            if align not in ("left", "right"):
                raise ParameterError.make(detalhe=f"align deve ser 'left' ou 'right' (recebi {align!r}).")
            self._guard_input()
            self._pos(row, col)
            if not isinstance(text, str):
                raise ParameterError.make(detalhe=f"O texto deve ser str (recebi {type(text).__name__}).")
            room, hidden = self._field_space(row, col)
            secret = secret or hidden               # campo oculto é secreto pelo atributo (A.9 g): nem o tamanho
            data = self._encode(text, secret=secret, errors=errors)
            if len(data) > room:
                if secret:
                    raise Truncated.make("secret", r=row, c=col, rc=6, delivery="none", terminal=self.terminal)
                raise Truncated.make(n=len(data), m=room, r=row, c=col, rc=6, delivery="none",
                                     terminal=self.terminal)
            padded = data.ljust(room, b" ") if align == "left" else data.rjust(room, b" ")
            self._write_wire(row, col, padded, secret)
            return self

    def _erase_field(self, row: int, col: int) -> None:
        """Apaga o campo (espaços, ``secret=True``) mesmo com a sessão ``uncertain`` — só o PS local (receitas)."""
        self._guard_connected()
        room = self._field_room(row, col)
        self._write_wire(row, col, b" " * room, True)

    def type(self, text: str, *, secret: bool = False) -> Session:
        """Digita ``text`` na posição do cursor (``WD_SendKey`` sem AID; ``@`` é escapado). Nada vai ao host agora."""
        with self._op():
            secret = _bool("secret", secret)
            if not isinstance(text, str):
                raise ParameterError.make(detalhe=f"O texto deve ser str (recebi {type(text).__name__}).")
            self._guard_input()
            chunks = _keys.encode_body([text], host=self.profile.kind, secret=secret)
            if chunks:
                self._send_body(chunks, secret, self.timeout)
            return self

    def move_to(self, row: int, col: int) -> Session:
        """Põe o cursor em ``(row, col)`` (``WD_SetCursor``, local)."""
        with self._op():
            self._guard_input()
            self._pos(row, col)
            self._call(self._backend.set_cursor, row, col, input_op=True)
            return self

    # ------------------------------------------------------------------ teclas (G.3)
    def _send_body(self, chunks: Sequence[str], secret: bool, limit: float) -> None:
        """Teclado livre e depois ``send`` do corpo (sem AID), em lotes de até ``MAX_CHUNKS`` chunks (C.4)."""
        size = _protocol.MAX_CHUNKS
        for start in range(0, len(chunks), size):
            self._guard_input()
            self._wait_ready(limit)
            self._call(self._backend.send, list(chunks[start:start + size]), secret=secret, input_op=True)

    def _press_aid(self, key: Key, limit: float) -> Screen:
        """AID sozinha num ``send`` de 1 chunk, depois espera a resposta do host (``NoHostResponse`` se não vier)."""
        self._guard_input()
        pre = self._ready_stable(limit)                # tirada DEPOIS da digitação: o eco local não é resposta
        self._guard_input()
        try:
            result = self._call(self._backend.send, [key.value], secret=False, input_op=True)
        except RumbaError as exc:
            if exc.delivery in ("partial", "unknown"):   # a AID pode ter chegado ao RUMBA (None/none: não saiu)
                self._aid_attempts += 1
            raise
        except KeyboardInterrupt:
            self._aid_attempts += 1
            raise
        self._aid_attempts += 1                    # AIDs que podem ter chegado ao host (as receitas usam, I15)
        seen_lock = result.oia_after.keyboard_locked

        def answered(s: Screen) -> bool | None:
            nonlocal seen_lock
            if s.oia.keyboard_locked or s.host_busy:
                seen_lock = True
                return None
            return True if seen_lock or not _same(s, pre) else None

        def describe(_s: Screen) -> str:
            return NoHostResponse.templates["default"].format(tecla=key.name, s=limit)

        try:
            return waits.wait_until(answered, snapshot=self._snap, timeout=limit, describe=describe,
                                    error_checks=self._checks_after_aid(pre), clock=self._clock, sleep=self._sleep,
                                    exc=NoHostResponse, hint=NO_RESPONSE_HINT)
        except KeyboardInterrupt:
            self._mark_uncertain()                     # a AID já saiu: não sei se o host respondeu
            raise
        except RumbaError as exc:
            self._aid_wait_failed(exc)
            raise

    def _checks_after_aid(self, pre: Screen) -> list[Callable[[Screen], None]]:
        """``error_checks`` da espera pós-AID (A.14): se a tela ``pre`` JÁ mostra o erro (ex.: CLEAR/PF3 para sair do
        "E R R O   G R A V E"), telas com o mesmo texto de ``pre`` ainda não são resposta e não levantam.

        Com ``pre`` limpa os checks valem como estão: uma resposta nova com erro é acusada no 1º snapshot. Se o host
        reenviar a MESMA tela de erro, a AID volta sem ``HostError`` e a próxima espera de conteúdo acusa o erro.
        """
        checks = list(self.error_checks)
        if not checks:
            return checks
        try:
            for check in checks:
                check(pre)
        except Exception:
            def skip_pre(check: Callable[[Screen], None]) -> Callable[[Screen], None]:
                def wrapped(s: Screen) -> None:
                    if s.text != pre.text:
                        check(s)
                return wrapped
            return [skip_pre(check) for check in checks]
        return checks

    def _aid_wait_failed(self, exc: RumbaError) -> None:
        """Falha na espera DEPOIS de a AID sair (processo auxiliar travou/caiu, canal inválido, sem conexão...): a
        AID pode ter sido processada, então ``delivery`` vira ``unknown`` e a sessão fica ``uncertain`` (B.3).

        ``NoHostResponse``/``HostConnectionLost`` já são tratados pelo ``_absorb``; ``SessionReplaced`` vira
        ``replaced``; ``HostError`` (e subclasses de ``RecipeError``) = o host respondeu com uma tela de erro."""
        if isinstance(exc, (NoHostResponse, HostConnectionLost, SessionReplaced, RecipeError)):
            return
        if exc.delivery not in ("partial", "unknown"):
            if "input" in (type(exc).__dict__.get("templates") or {}) and INPUT_UNKNOWN not in exc.message:
                exc.message = f"{exc.message.rstrip()} {INPUT_UNKNOWN}"
                exc.args = (exc.message,)
            exc.delivery = "unknown"
            if exc.hint is None:
                exc.hint = NO_RESPONSE_HINT
        self._mark_uncertain()

    def press(self, key: Key | str, *, timeout: float | None = None) -> Session:
        """Pressiona uma tecla. AID (ENTER, CLEAR, PFn, PAn, PAGE_UP/DOWN...) espera a tela estável antes e a resposta
        do host depois; tecla local (TAB, HOME...) só espera o teclado livre."""
        with self._op():
            parsed = Key.parse(key)
            limit = self._timeout(timeout)
            self._guard_input()
            if parsed in AIDS:
                self._press_aid(parsed, limit)
            else:
                self._wait_ready(limit)
                self._call(self._backend.send, [parsed.value], secret=False, input_op=True)
            return self

    def enter(self, **kw: Any) -> Session:
        return self.press(Key.ENTER, **kw)

    def clear(self, **kw: Any) -> Session:
        return self.press(Key.CLEAR, **kw)

    def pf(self, n: int, **kw: Any) -> Session:
        return self.press(Key.pf(n), **kw)

    def pa(self, n: int, **kw: Any) -> Session:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 3:
            raise ParameterError.make(detalhe=f"PA vai de 1 a 3 (recebi {n!r}).")
        return self.press(Key[f"PA{n}"], **kw)

    def _send_tokens(self, tokens: Sequence[_keys.Token], *, secret: bool = False,
                     timeout: float | None = None) -> Session:
        """Envia tokens (texto e ``Key``) cortando em cada AID; tudo é validado e codificado ANTES do 1º envio."""
        limit = self._timeout(timeout)
        plan = [(_keys.encode_body(seg.body, host=self.profile.kind, secret=secret), seg.aid)
                for seg in _keys.split_at_aids(tokens)]
        self._guard_input()
        for chunks, aid in plan:
            if chunks:
                self._send_body(chunks, secret, limit)
            if aid is not None:
                self._press_aid(aid, limit)
        return self

    def send_keys(self, keys: str, *, secret: bool = False, timeout: float | None = None) -> Session:
        """Sintaxe extra: ``"A000000<Tab>senha<Enter>"`` (``<<`` = ``<``). Cada AID espera a resposta do host antes
        do resto. Com ``secret=True`` nenhuma mensagem cita o texto."""
        with self._op():
            secret = _bool("secret", secret)
            tokens = _keys._tokenize_extra(keys, secret=secret)
            return self._send_tokens(tokens, secret=secret, timeout=timeout)

    # ------------------------------------------------------------------ esperas (G)
    def _wait(self, cond: waits.Cond, limit: float, describe: Callable[[Screen], str], *,
              checks: bool = True, hint: str | None = None, **kw: Any) -> Screen:
        return waits.wait_until(cond, snapshot=self._snap, timeout=limit, describe=describe,
                                error_checks=self.error_checks if checks else (), clock=self._clock,
                                sleep=self._sleep, hint=hint, **kw)

    @staticmethod
    def _ready(s: Screen) -> bool:
        return s.oia.ready and not s.host_busy

    def _describe_ready(self, limit: float, *, stable: bool = False) -> Callable[[Screen], str]:
        def describe(s: Screen) -> str:
            if s.oia.keyboard_locked:
                return WaitTimeout.templates["keyboard"].format(s=limit, hex=_g8(s))
            if s.host_busy:
                return f"O host continua ocupado (X SYSTEM) após {limit:g} s." + _oia_tail(s)
            if not s.oia.online:
                return f"O terminal continua sem conexão com o host após {limit:g} s." + _oia_tail(s)
            if stable:
                return f"A tela não ficou estável em {limit:g} s; nada foi enviado." + _oia_tail(s)
            return f"O terminal não ficou pronto em {limit:g} s." + _oia_tail(s)
        return describe

    def _wait_ready(self, limit: float) -> Screen:
        return self._wait(lambda s: True if self._ready(s) else None, limit, self._describe_ready(limit),
                          checks=False)

    def _ready_stable(self, limit: float) -> Screen:
        """Teclado livre e 2 snapshots consecutivos iguais (texto e cursor), a cada 50 ms, no mesmo prazo."""
        prev: list[Screen] = []

        def stable(s: Screen) -> bool | None:
            if not self._ready(s):
                prev.clear()
                return None
            if prev and _same(prev[0], s):
                return True
            prev[:] = [s]
            return None

        gap = waits.READY_STABLE_GAP
        return self._wait(stable, limit, self._describe_ready(limit, stable=True), checks=False,
                          fast_poll=gap, slow_poll=gap)

    def wait_ready(self, timeout: float | None = None) -> Screen:
        """Espera o teclado livre (sem os detectores de erro; não tira a sessão de ``uncertain``)."""
        with self._op():
            limit = self._timeout(timeout)
            self._guard_connected()
            return self._wait_ready(limit)

    def _check_text(self, text: object) -> str | re.Pattern[str]:
        if isinstance(text, re.Pattern):
            if not isinstance(text.pattern, str):
                raise ParameterError.make(detalhe="O padrão deve ser de texto (str), não de bytes.")
            return text
        if not isinstance(text, str):
            raise ParameterError.make(detalhe=f"O texto esperado deve ser str ou re.Pattern (recebi "
                                              f"{type(text).__name__}).")
        if not text:
            raise ParameterError.make(detalhe="O texto esperado não pode ser vazio.")
        return text

    def _text_cond(self, text: str | re.Pattern[str], row: int | None, col: int | None,
                   case: bool) -> Callable[[Screen], Cursor | None]:
        """Condição da G.4 (devolve a posição achada ou ``None``); valida a posição na hora."""
        if row is None:
            if col is not None:
                raise ParameterError.make(detalhe="Informe a linha (row) junto com a coluna (col).")
            return lambda s: s.find(text, case=case)
        if col is None:
            self._pos(row, 1)
            if isinstance(text, re.Pattern):
                pattern = text if case else re.compile(text.pattern, text.flags | re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(text), 0 if case else re.IGNORECASE)

            def in_line(s: Screen) -> Cursor | None:
                found = pattern.search(s.line(row))
                return None if found is None else Cursor(row, found.start() + 1)
            return in_line
        pos = self._pos(row, col)
        at = Cursor(row, col)
        if isinstance(text, re.Pattern):
            pattern = text if case else re.compile(text.pattern, text.flags | re.IGNORECASE)
            return lambda s: at if pattern.match(s.text, pos - 1) else None
        _codec.check_span(pos, len(text), self.rows, self.cols)
        wanted = text if case else text.casefold()

        def fixed(s: Screen) -> Cursor | None:
            got = s.get(row, col, len(text))
            return at if (got if case else got.casefold()) == wanted else None
        return fixed

    def _cited(self, s: Screen, row: int, col: int, size: int) -> str:
        """No máximo ``size`` caracteres da tela a partir de ``(row, col)`` (nunca além do fim da tela)."""
        pos = (row - 1) * s.cols + col
        size = max(1, min(size, s.rows * s.cols - pos + 1))
        return _shown(s, s.get(row, col, size))

    def wait_text(self, text: str | re.Pattern[str], row: int | None = None, col: int | None = None, *,
                  timeout: float | None = None, gone: bool = False, case: bool = True) -> Screen:
        """Espera ``text`` (ou um ``re.Pattern``) em ``(row, col)``, na linha ``row`` ou em qualquer lugar da tela.

        Devolve a tela com ``match`` = posição achada. ``gone=True`` espera o texto SUMIR. Timeout -> ``WaitTimeout``
        com a tela (``e.screen``) e o que havia na posição. Sucesso tira a sessão de ``uncertain``.
        """
        return self._wait_text(text, row, col, timeout=timeout, gone=gone, case=case)

    def _wait_text(self, text: str | re.Pattern[str], row: int | None = None, col: int | None = None, *,
                   timeout: float | None = None, gone: bool = False, case: bool = True,
                   cond_first: bool = False) -> Screen:
        """``wait_text``; ``cond_first=True`` (só a compat ``screen_load``, como a v0.1.5) testa a condição ANTES dos
        ``error_checks`` em cada snapshot."""
        with self._op():
            limit = self._timeout(timeout)
            gone, case = _bool("gone", gone), _bool("case", case)
            wanted = self._check_text(text)
            self._guard_connected()
            found = self._text_cond(wanted, row, col, case)
            shown = _quoted(wanted)
            at = Cursor(row, col) if row is not None and col is not None else None
            cond: Callable[[Screen], Cursor | bool | None]
            if gone:
                def vanished(s: Screen) -> Cursor | bool | None:
                    return (at or True) if found(s) is None else None
                cond = vanished
            else:
                cond = found

            def describe(s: Screen) -> str:
                if gone:
                    where = f"em ({row},{col})" if at else (f"na linha {row}" if row is not None else "na tela")
                    if at:
                        message = WaitTimeout.templates["gone"].format(x=shown, r=row, c=col, s=limit)
                    else:
                        message = f"'{shown}' continua {where} após {limit:g} s."
                elif at is not None and row is not None and col is not None:
                    message = WaitTimeout.templates["at"].format(s=limit, x=shown, r=row, c=col,
                                                                 atual=self._cited(s, row, col, len(shown)))
                elif row is not None:
                    message = (f"Esperei {limit:g} s por '{shown}' na linha {row}. Linha 1 atual: "
                               f"'{_line1(s)}'.")
                else:
                    message = WaitTimeout.templates["anywhere"].format(s=limit, x=shown, linha1=_line1(s))
                return message + _oia_tail(s)

            hint = None if gone else WaitTimeout.hints["at" if at else "anywhere"]
            screen = self._wait(cond, limit, describe, hint=hint, cond_first=cond_first)
            self._content_confirmed()
            return screen

    def _option_cond(self, key: str, value: object) -> tuple[Callable[[Screen], Any], Callable[[Screen], str]]:
        """(condição, descrição para o timeout) de uma opção de ``wait_any``."""
        if callable(value) and not isinstance(value, (str, re.Pattern, tuple)):
            # condição do usuário: vale a verdade do resultado ("", 0, [] e None NÃO satisfazem)
            return (lambda s: True if value(s) else None), (lambda s: f"'{key}' = condição própria (não satisfeita)")
        if isinstance(value, tuple):
            if len(value) != 3:
                raise ParameterError.make(detalhe=f"wait_any: a opção '{key}' deve ser (texto, linha, coluna).")
            text, row, col = value
            wanted = self._check_text(text)
            cond = self._text_cond(wanted, _int("row", row), _int("col", col), True)
            shown = _quoted(wanted)
            return cond, (lambda s: f"'{key}' = '{shown}' em ({row},{col}), ali está "
                                    f"'{self._cited(s, row, col, len(shown))}'")
        wanted = self._check_text(value)
        cond = self._text_cond(wanted, None, None, True)
        return cond, (lambda s: f"'{key}' = '{_quoted(wanted)}' em qualquer lugar (não encontrado)")

    def wait_any(self, options: Mapping[str, Any], *, timeout: float | None = None) -> Screen:
        """Espera a 1ª opção satisfeita, testando NA ORDEM do dicionário; ``match`` = a chave.

        Valores: ``str`` (em qualquer lugar), ``re.Pattern`` (busca na tela), ``(texto, linha, coluna)`` (posição fixa)
        ou ``Callable[[Screen], bool]``. Ponha as opções de erro ANTES da de sucesso.
        """
        with self._op():
            limit = self._timeout(timeout)
            if not isinstance(options, Mapping) or not options:
                raise ParameterError.make(detalhe="wait_any espera um dicionário não vazio {nome: condição}.")
            built: list[tuple[str, Callable[[Screen], Any], Callable[[Screen], str]]] = []
            for key, value in options.items():
                if not isinstance(key, str) or not key:
                    raise ParameterError.make(detalhe="wait_any: as chaves devem ser str não vazias.")
                cond, explain = self._option_cond(key, value)
                built.append((key, cond, explain))
            self._guard_connected()

            def first(s: Screen) -> str | None:
                for key, cond, _ in built:
                    result = cond(s)
                    if result is not None and result is not False:
                        return key
                return None

            def describe(s: Screen) -> str:
                listed = "; ".join(explain(s) for _, _, explain in built)
                return (f"Esperei {limit:g} s por uma destas opções: {listed}. Linha 1 atual: '{_line1(s)}'."
                        + _oia_tail(s))

            screen = self._wait(first, limit, describe, hint=WaitTimeout.hints["anywhere"])
            self._content_confirmed()
            return screen

    def wait_change(self, *, since: Screen | None = None, timeout: float | None = None) -> Screen:
        """Espera o texto ou o cursor mudarem em relação a ``since`` (ou ao 1º snapshot desta espera)."""
        with self._op():
            limit = self._timeout(timeout)
            if since is not None and not isinstance(since, Screen):
                raise ParameterError.make(detalhe=f"since deve ser um Screen (recebi {type(since).__name__}).")
            self._guard_connected()
            base = since if since is not None else self._snap()

            def describe(s: Screen) -> str:
                return f"A tela não mudou em {limit:g} s. Linha 1 atual: '{_line1(s)}'." + _oia_tail(s)

            screen = self._wait(lambda s: None if _same(s, base) else True, limit, describe)
            self._content_confirmed()
            return screen

    def wait_cursor(self, row: int, col: int, *, timeout: float | None = None) -> Screen:
        """Espera o cursor em ``(row, col)``; ``match`` = essa posição."""
        with self._op():
            limit = self._timeout(timeout)
            self._pos(row, col)
            self._guard_connected()
            target = Cursor(row, col)

            def describe(s: Screen) -> str:
                return (f"Esperei {limit:g} s pelo cursor em {target}, mas ele está em {s.cursor}."
                        + _oia_tail(s))

            screen = self._wait(lambda s: target if s.cursor == target else None, limit, describe)
            self._content_confirmed()
            return screen

    # ------------------------------------------------------------------ janela (cliente, user32; nunca ativa)
    def _window_api(self) -> User32:
        if self._user32 is None:
            self._user32 = _user32_factory()
        return self._user32

    def _root_hwnd(self) -> int:
        self._guard_alive()
        if not self._hwnd:
            raise ParameterError.make(detalhe=f"O terminal {self.terminal} não informou a janela (HWND); "
                                              "show/minimize/window não estão disponíveis.")
        user32 = self._window_api()
        root = user32.root_of(self._hwnd) or (self._hwnd if user32.is_window(self._hwnd) else 0)
        if not root:
            raise TerminalNotOpen.make(L=self.terminal, terminal=self.terminal)
        from ._launcher import root_kind         # tardio: P-C (Onda 2)
        kind = root_kind(user32, root)
        if kind is None:
            raise TerminalNotOpen.make(L=self.terminal, terminal=self.terminal)
        if kind != "rumba":
            # aba do Edge no modo IE (E.3): a janela-raiz é a do navegador inteiro do usuário; nunca mexer nela
            dono = "do Microsoft Edge (aba no modo IE)" if kind == "edge" else "de outro programa"
            raise ParameterError.make(detalhe=f"A janela do terminal {self.terminal} é {dono}, não do RUMBA; "
                                              "show/minimize/window não estão disponíveis.")
        return root

    def show(self) -> Session:
        """Mostra a janela sem ativá-la nem roubar o foco (``SW_SHOWNOACTIVATE``)."""
        with self._op():
            self._window_api().show(self._root_hwnd(), SW_SHOWNOACTIVATE)
            return self

    def minimize(self) -> Session:
        """Minimiza a janela sem ativá-la (``SW_SHOWMINNOACTIVE``)."""
        with self._op():
            self._window_api().show(self._root_hwnd(), SW_SHOWMINNOACTIVE)
            return self

    @property
    def window(self) -> WindowInfo:
        """``WindowInfo`` da janela do terminal (título sanitizado)."""
        with self._op():
            root = self._root_hwnd()
            from ._launcher import Launcher      # tardio: P-C (Onda 2)

            def no_backend(letter: str, mode: str) -> Backend:
                raise ParameterError.make(detalhe="uso interno: window não usa o processo auxiliar.")

            info = Launcher(self._window_api(), no_backend).window_info(self.profile, root)
            if info is None:
                raise TerminalNotOpen.make(L=self.terminal, terminal=self.terminal)
            return info
