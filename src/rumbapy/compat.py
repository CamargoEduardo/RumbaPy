"""Compatibilidade com a API da v0.1.5 (B.9.1): ``RumbaClient`` sobre a ``Session`` da v2.

Scripts antigos continuam funcionando sem mudanças: ``RumbaClient("D").logon_cics(uid, pwd)``, ``send_key``,
``copy_ps_to_string`` etc. Diferenças assumidas (CHANGELOG, Q3): toda falha levanta (antes algumas eram silenciosas),
sempre como a exceção v2 original (tipo preservado; todas são ``RuntimeError``) com a mensagem prefixada pelo texto da
v0.1.5 ``Comando '<acao>' falhou: ``. Não há servidor TCP: ``HOST``/``PORT`` existem só por compatibilidade.

Nada de lógica nova aqui (A.3): cada método é uma chamada à ``Session`` (ou a ``rumbapy.open/attach/close``).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Literal, get_args
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import (InvalidPassword, ParameterError, PasswordExpired, RecipeNotAvailable, RumbaError,
                     SessionReplaced, TerminalInUse, NotConnected)
from . import session as _session
from .keys import AIDS, Key
from . import keys as _keys
from . import profiles
from . import waits

import logging
import os

logger = logging.getLogger(__name__)

Mnemonic = Literal[
    "ENTER", "TAB", "BACKSPACE", "BACKTAB", "CLEAR", "DOWN",
    "LEFT", "RIGHT", "F1", "F2", "F3", "F4", "F5", "F6", "F7",
    "F8", "F9", "F10", "F11", "F12", "UP", "DELETE", "ERASEEOF",
    "HOME", "PAGEUP", "PAGEDOWN", "RESET", "HELP", "INSERT"
]
MNEMONIC_NAMES: frozenset[str] = frozenset(get_args(Mnemonic))

PORTS = {"D": 8500, "A": 8600, "Z": 8700}           # sem uso na v2 (não há servidor TCP); outras letras: 8800
LEGACY_PREFIX = "Comando '{acao}' falhou: "         # texto exato da v0.1.5
LEGACY_LOGON: tuple[tuple[type[RumbaError], str], ...] = (
    (PasswordExpired, "SENHA DO CICS ESTÁ EXPIRADA"),
    (InvalidPassword, "SENHA DO CICS ESTÁ INCORRETA"),
)
SEND_KEY_CUT = "send_key: ignorado o trecho após a 1ª tecla AID (comportamento da v0.1.5)"
LAZY_ATTACH_TIMEOUT = 10.0                          # qualquer op sem logon_*/connect anexa sozinha
CONNECT_TIMEOUT = 60.0
COMPAT_AID_TIMEOUT = 120.0                          # send_key/copy_ps_to_string: como o @timeout()/screen_load da v0.1.5
# trechos de mensagem/dica da v2 que citam nomes que o RumbaClient não tem (t.reconnect(), t.acknowledge()...)
COMPAT_PHRASES: tuple[tuple[str, str], ...] = (
    ("Use a Session nova.", "Chame connect() deste RumbaClient (anexa ao terminal aberto agora) ou faça o logon "
                            "de novo."),
    ("confirme a tela com wait_text/wait_any antes de digitar (ou t.acknowledge()).",
     "confirme a tela com copy_ps_to_string e reancore com screen_load(...) antes de digitar."),
    ("reancore com t.wait_text/t.wait_any (ou t.acknowledge()).", "reancore com screen_load(...)."),
    ("print(t.screen)", "copy_ps_to_string"),
    ("t.press(Key.RESET)", "send_key('RESET')"),
    (" (t.wait_ready())", ""),
)
NOT_CONNECTED_HINT = "chame connect() deste RumbaClient (ou logon_cics/logon_rhelp) de novo."

SessionFactory = Callable[..., "_session.Session"]


# ------------------------------------------------------------------------------------------- funções legadas (B.9.2)

def update_python_64bit() -> None:
    """Força a checagem do auto-update (H.1): se houver versão nova, instala e levanta ``UpdateRequired``.

    Nunca reexecuta o script (H.5): rode-o de novo depois da atualização.
    """
    from . import _update                            # tardio: o import do pacote já atualizou (H.1)
    _update.ensure_latest(context="legacy", force=True)


def _runtime(path: Path | str | os.PathLike[str] | None) -> Any:
    from . import _runtime32                         # tardio: P-D (Onda 2)
    return _runtime32.find(path)


def find_python_32bit() -> Path:
    """Python 32-bit para o processo auxiliar (``_runtime32.find``); nenhum -> ``Python32NotFound`` (``ValueError``)."""
    return Path(_runtime(None).path)


def ensure_python_32bit(python_32bit: Path | str) -> None:
    """Valida o Python 32-bit (I.1). Na v2 nada é instalado nele: o worker roda a partir do pacote 64-bit."""
    runtime = _runtime(python_32bit)
    logger.info("Python 32-bit válido (%s, %s); nada a instalar na v2", runtime.real,
                ".".join(str(n) for n in runtime.version))


def update_python_32bit(python_32bit: Path | str) -> None:
    """Igual a ``ensure_python_32bit`` (v2: o Python 32-bit não recebe pacotes)."""
    ensure_python_32bit(python_32bit)


def _find_python32(explicit: Path | str | None) -> Path:
    """``python_32bit`` do ``RumbaClient`` (ponto de troca dos testes)."""
    return Path(_runtime(explicit).path)


def _default_session_factory(action: str, terminal: str, *, python32: Any = None,
                             timeout: float | None = None) -> _session.Session:
    if action == "open":
        return _session.open(terminal, python32=python32)
    if action == "attach":
        return _session.attach(terminal, timeout=LAZY_ATTACH_TIMEOUT if timeout is None else timeout,
                               python32=python32)
    raise ParameterError.make(detalhe=f"uso interno: ação de sessão desconhecida {action!r}.")


def _prefix(exc: RumbaError, action: str) -> None:
    """Prefixa ``exc.message`` com o texto da v0.1.5 (uma vez só), preservando o tipo e os demais atributos."""
    if getattr(exc, "_compat_action", None) is not None:
        return
    rest = exc.message
    for cls, legacy in LEGACY_LOGON:
        if isinstance(exc, cls):
            rest = f"{legacy}. {rest}"
            break
    exc.message = LEGACY_PREFIX.format(acao=action) + _compat_text(rest)
    exc.args = (exc.message,)
    if isinstance(exc, NotConnected):
        exc.hint = NOT_CONNECTED_HINT                # "t.reconnect()" não existe no RumbaClient
    elif exc.hint:
        exc.hint = _compat_text(exc.hint)
    exc._compat_action = action                      # type: ignore[attr-defined]


def _compat_text(text: str) -> str:
    """Troca as referências à API v2 (``t.acknowledge()``, ``Session``...) pelos equivalentes do ``RumbaClient``."""
    for old, new in COMPAT_PHRASES:
        text = text.replace(old, new)
    return text


def _compat_timeout(session: _session.Session) -> float:
    """Prazo das esperas de ``send_key``/``copy_ps_to_string``: 120 s, ou ``Session.timeout`` se maior (B.11)."""
    return max(session.timeout, COMPAT_AID_TIMEOUT)


def _send_key_tokens(key: object) -> list[_keys.Token]:
    """Nome ``Mnemonic`` (sem diferenciar maiúsculas, como a v0.1.5) ou mnemônicos ``@``; corta após a 1ª AID."""
    if not isinstance(key, str):
        raise ParameterError.make(detalhe=f"send_key espera str (recebi {type(key).__name__}).")
    if key.upper() in MNEMONIC_NAMES:
        tokens: list[_keys.Token] = [Key.parse(key.upper())]
    else:
        tokens = _keys.tokenize_mnemonic(key)
    for index, token in enumerate(tokens):
        if isinstance(token, Key) and token in AIDS:
            rest: Sequence[_keys.Token] = tokens[index + 1:]
            if any(isinstance(t, Key) or t for t in rest):
                logger.warning(SEND_KEY_CUT)
            return tokens[:index + 1]
    return tokens


# ------------------------------------------------------------------------------------------- RumbaClient (B.9.1)

class RumbaClient:
    """Cliente da v0.1.5, agora sobre ``rumbapy.Session`` (sem servidor TCP; o processo auxiliar sobe na 1ª op).

    ``auto_update=False`` só vale com ``RUMBAPY_DEV=1`` (o usuário roda sempre a última versão). ``connected`` diz
    se este cliente tem uma sessão ativa. Toda falha levanta a exceção original da v2 com a mensagem
    ``Comando '<acao>' falhou: ...``.
    """

    HOST = "localhost"

    def __init__(self, terminal_type: str, python32bit_path: Path | str | None = None, auto_update: bool = True,
                 *, _session_factory: SessionFactory | None = None) -> None:
        _session._require_user_role()
        self._profile = profiles.resolve(terminal_type)       # letra inválida falha na hora (UnknownTerminal)
        self.terminal_type = terminal_type
        # como na v0.1.5 (`Path(p) if p else find_python_32bit()`): qualquer valor falso ("" do config.ini) = descobrir
        self.python_32bit: Path = _find_python32(python32bit_path or None)
        if not auto_update and os.environ.get("RUMBAPY_DEV") != "1":
            logger.warning("RumbaClient: auto_update=False ignorado (o RumbaPy sempre roda a última versão; "
                           "só RUMBAPY_DEV=1 desliga a atualização)")
            auto_update = True
        if auto_update:
            _session._ensure_latest()
        else:
            logger.info("RumbaClient: auto_update=False (RUMBAPY_DEV=1)")
        letter = self._profile.letter
        self.session_id = f"{letter}_{uuid4().hex}"
        self.PORT = PORTS.get(letter, 8800)
        self._connected = False
        self._session: _session.Session | None = None
        self._factory: SessionFactory = _session_factory or _default_session_factory

    def __repr__(self) -> str:
        return f"<RumbaClient {self._profile.letter} connected={self.connected}>"

    update_python_64bit = staticmethod(update_python_64bit)

    @property
    def connected(self) -> bool:
        """``True`` depois de ``logon_*``/``connect`` (ou do anexo automático) bem-sucedido, enquanto a sessão estiver
        conectada; ``False`` após falha de logon, ``disconnect``/``close_terminal`` ou sessão ``replaced``/``closed``."""
        session = self._session
        return self._connected and session is not None and session.connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = bool(value)

    # ------------------------------------------------------------------ apoio
    @contextmanager
    def _legacy(self, action: str) -> Iterator[None]:
        try:
            yield
        except RumbaError as exc:
            _prefix(exc, action)
            raise

    def _new(self, action: str, **kw: Any) -> _session.Session:
        self._connected = False                      # só vira True depois do sucesso de quem chamou (logon/connect)
        session = self._factory(action, self._profile.letter, python32=self.python_32bit, **kw)
        self._session = session
        return session

    def _live(self) -> _session.Session:
        """Sessão em uso; sem nenhuma (ou depois de ``close_terminal``), anexa ao terminal aberto (10 s)."""
        session = self._session
        if session is None or session.state == "closed":
            session = self._new("attach", timeout=LAZY_ATTACH_TIMEOUT)
            self._connected = True
        return session

    # ------------------------------------------------------------------ logon
    def logon_cics(self, uid: str, pwd: str) -> bool:
        """Abre o terminal (fechando as instâncias do perfil) e faz o logon no CICS (só perfis D/Z)."""
        with self._legacy("logon_cics"):
            self._require_kind("cics")
            self._new("open").cics.logon(uid, pwd)
        self._connected = True
        return True

    def logon_rhelp(self, uid: str, pwd: str) -> bool:
        """Abre o terminal e faz o logon no IMS/RHELP (só perfis IMS: ``RumbaClient("A")``)."""
        with self._legacy("logon_rhelp"):
            self._require_kind("ims")
            self._new("open").ims.logon(uid, pwd)
        self._connected = True
        return True

    def _require_kind(self, kind: str) -> None:
        if self._profile.kind != kind:
            letters = ", ".join(p.letter for p in profiles.PROFILES.values() if p.kind == kind)
            raise RecipeNotAvailable.make(receita=kind, tipo=kind.upper(), letras=letters,
                                          terminal=self._profile.letter)

    # ------------------------------------------------------------------ teclas e tela
    def send_key(self, key: Mnemonic | str) -> bool:
        """Nome (``"ENTER"``, ``"F3"``...) ou mnemônicos (``"SPRE@E"``); tudo após a 1ª AID é ignorado (WARNING)."""
        with self._legacy("send_key"):
            tokens = _send_key_tokens(key)
            session = self._live()
            with session._op():
                session._send_tokens(tokens, timeout=_compat_timeout(session))
        return True

    def copy_ps_to_string(self, y: int, x: int, length: int) -> str:
        """``length`` caracteres a partir de ``(y, x)``; como na v0.1.5, um NUL no meio corta o texto ali.

        ``length == 0`` devolve ``""`` sem tocar no terminal (v0.1.5: célula vazia de planilha não interrompe o lote).
        Com o host ocupado (X SYSTEM) espera até 120 s."""
        if isinstance(length, int) and not isinstance(length, bool) and length == 0:
            return ""
        with self._legacy("copy_ps_to_string"):
            session = self._live()
            with session._op():
                result = session._read_raw(y, x, length, timeout=_compat_timeout(session))
        return result.text[:result.nul_at] if result.nul_at >= 0 else result.text

    def copy_string_to_ps(self, y: int, x: int, text: str) -> bool:
        """Escreve ``text`` em ``(y, x)`` no PS local; campo protegido ou texto maior que o campo levantam antes.

        Texto vazio não faz nada e devolve ``True`` (como a v0.1.5: célula vazia de planilha não interrompe o lote).
        """
        if isinstance(text, str) and not text:
            logger.debug("copy_string_to_ps: texto vazio em (%s,%s), nada a escrever", y, x)
            return True
        with self._legacy("copy_string_to_ps"):
            self._live().write(y, x, text)
        return True

    def copy_field(self, y: int, x: int) -> str:
        """Texto do campo que contém ``(y, x)`` (tamanho real), sem os NUL finais."""
        with self._legacy("copy_field"):
            session = self._live()
            with session._op():
                result = session._read_field_raw(y, x)
        return result.text[:len(result.text) - result.nul_tail] if result.nul_tail > 0 else result.text

    def screen_load(self, y: int, x: int, text: str, text_diff: bool = False) -> bool:
        """Espera ``text`` em ``(y, x)`` por até 120 s (``text_diff=True``: espera o texto sair).

        Como na v0.1.5, a condição é testada antes do detector de erro grave do CICS. ``text == ""`` devolve ``True``
        na hora (v0.1.5: ``"" == ""``); com ``text_diff=True`` levanta ``ParameterError`` (nunca terminaria)."""
        if isinstance(text, str) and not text:
            if text_diff:
                with self._legacy("screen_load"):
                    raise ParameterError.make(detalhe="screen_load com texto vazio e text_diff=True nunca termina "
                                                      "(um texto vazio não tem como sair da tela).")
            logger.debug("screen_load: texto vazio em (%s,%s), nada a esperar", y, x)
            return True
        with self._legacy("screen_load"):
            self._live()._wait_text(text, y, x, timeout=waits.SCREEN_LOAD_TIMEOUT, gone=bool(text_diff),
                                    cond_first=True)
        return True

    # ------------------------------------------------------------------ ciclo de vida
    def close_terminal(self) -> bool:
        """Fecha todas as janelas RUMBA do perfil (de qualquer script), como a v0.1.5 — mesmo com a sessão substituída."""
        with self._legacy("close_terminal"):
            session = self._session
            if session is not None and session.state in ("ok", "uncertain"):
                try:
                    session.close()
                except (SessionReplaced, TerminalInUse):
                    # a instância é de outro script agora: a v0.1.5 fechava as janelas do perfil mesmo assim
                    _session.close(self._profile.letter)
                    self._session = None             # a próxima op anexa ao terminal que estiver aberto
            else:
                _session.close(self._profile.letter)
                if session is not None and session.state == "replaced":
                    self._session = None             # a próxima op anexa ao terminal que estiver aberto
        self._connected = False
        return True

    def disconnect(self) -> bool:
        """``WD_DisconnectPS`` da sessão deste cliente (sem sessão ativa: nada a fazer)."""
        with self._legacy("disconnect"):
            session = self._session
            if session is not None and session.state in ("ok", "uncertain"):
                try:
                    session.disconnect()
                except SessionReplaced:
                    # outro script reabriu a letra (o evict foi lido agora): não há o que desconectar
                    logger.info("disconnect %s: sessão substituída; descartada", self._profile.letter)
                    self._session = None
        self._connected = False
        return True

    def connect(self) -> bool:
        """Sem sessão (ou substituída): anexa ao terminal aberto (até 60 s; sem terminal -> ``TerminalNotOpen``);
        senão reconecta à mesma instância — se ela foi fechada/reaberta (``SessionReplaced``), anexa à atual."""
        with self._legacy("connect"):
            session = self._session
            if session is None or session.state in ("closed", "replaced"):
                self._new("attach", timeout=CONNECT_TIMEOUT)
            else:
                try:
                    session.reconnect()
                except SessionReplaced:
                    logger.info("connect %s: a instância foi reaberta; anexando à atual", self._profile.letter)
                    self._new("attach", timeout=CONNECT_TIMEOUT)
        self._connected = True
        return True
