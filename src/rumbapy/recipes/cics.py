"""Receita CICS (B.8): logon pela tela "Select CICS Environment" -> signon -> "Signon is complete".

Âncoras (todas validadas ao vivo no BRAZIL_D: MENU/MENU_FIELD no L1; signon, senha oculta e "Signon" no L6, 2026-10-04):
o texto do menu na linha 14, o campo do ambiente em (22,56), "TERMINAL" em (4,6), usuário em (12,21), senha em
(13,21) — **oculta** —, mensagens em (19,19) e "Signon" em (5,20) depois do logon.
"""
# ruff: noqa: I001
from __future__ import annotations

from ..errors import InvalidPassword, ParameterError, PasswordExpired, RumbaError, UnexpectedScreen
from collections.abc import Callable
from ..screen import Field, Screen
from typing import TYPE_CHECKING
from .. import waits

import logging

if TYPE_CHECKING:
    from ..session import Session

logger = logging.getLogger(__name__)

OMITTED = "<conteúdo omitido>"


# ------------------------------------------------------------------------------------------- utilidades comuns

def input_field(screen: Screen, row: int, col: int) -> Field | None:
    """Campo desprotegido cujo DADO contém ``(row, col)`` (o byte de atributo não conta), ou ``None``."""
    if screen.fields is None:
        return None
    found = screen.field_at(row, col)
    if found is None or found.protected or found.length < 1:
        return None
    size = screen.rows * screen.cols
    start = (found.start.row - 1) * screen.cols + found.start.col - 1
    index = (row - 1) * screen.cols + col - 1
    return found if (index - start) % size < found.length else None


def hidden_input(screen: Screen, row: int, col: int) -> bool:
    found = input_field(screen, row, col)
    return found is not None and found.hidden


def unexpected(session: Session, screen: Screen | None, what: str) -> UnexpectedScreen:
    """``UnexpectedScreen`` com a linha 1 (omitida se a máscara for desconhecida) e a tela anexada se permitido."""
    if screen is None:
        line1 = OMITTED
    else:
        line1 = OMITTED if screen.hidden_unknown else screen.line(1).rstrip()
    attach = screen if screen is not None and not screen.hidden_unknown else None
    return UnexpectedScreen.make(tela=what, linha1=line1, L=session.terminal, terminal=session.terminal,
                                 screen=attach)


def check_credentials(user: object, password: object) -> None:
    """Tipos e vazios conferidos antes de qualquer escrita (a mensagem nunca cita os valores)."""
    if not isinstance(user, str) or not user.strip():
        raise ParameterError.make(detalhe="Informe o usuário (str não vazia).")
    if not isinstance(password, str) or not password:
        raise ParameterError.make(detalhe="Informe a senha (str não vazia).")


def submit_credentials(session: Session, user_at: tuple[int, int], password_at: tuple[int, int], user: str,
                       password: str, timeout: float) -> None:
    """Escreve usuário e senha (``secret=True``) e tecla ENTER.

    Falha antes de o ENTER sair: apaga a senha (melhor esforço; só no PS local) e relança com ``screen=None``.
    Entre a escrita da senha e a resposta do host, nenhuma exceção leva a tela (I15).
    """
    aids_before = session._aid_attempts
    try:
        session.fill(*user_at, user)
        session.fill(*password_at, password, secret=True)
        session.enter(timeout=timeout)
    except BaseException as exc:
        if session._aid_attempts == aids_before:          # o ENTER não chegou a ser enviado
            try:
                session._erase_field(*password_at)
            except Exception as erase:                     # melhor esforço; nunca mascara a falha original
                logger.warning("logon: não consegui apagar o campo da senha (%s)", type(erase).__name__)
        if isinstance(exc, RumbaError):
            exc.screen = None
        raise


# ------------------------------------------------------------------------------------------- CICS

class Cics:
    """Receitas dos perfis CICS (``t.cics``)."""

    MENU = ("Select CICS Environment", 14)        # texto na linha 14 (validado ao vivo: começa em (14,26))
    MENU_FIELD = (22, 56)                         # desprotegido (validado ao vivo)
    SIGNON = ("TERMINAL", 4, 6)
    USER = (12, 21)
    PASSWORD = (13, 21)
    OK = ("Signon", 5, 20)
    MSG = (19, 19)
    EXPIRED = "SENHA EXPIRADA"
    INVALID = "SENHA INVALIDA"
    WHAT = "a tela 'Select CICS Environment' ou a tela de signon do CICS"

    def __init__(self, session: Session) -> None:
        self._t = session

    def __repr__(self) -> str:
        return f"<Cics {self._t.terminal}>"

    # ------------------------------------------------------------------ reconhecimento
    def is_menu(self, screen: Screen) -> bool:
        text, row = self.MENU
        return text in screen.line(row) and input_field(screen, *self.MENU_FIELD) is not None

    def is_signon(self, screen: Screen) -> bool:
        text, row, col = self.SIGNON
        return (screen.get(row, col, len(text)) == text and input_field(screen, *self.USER) is not None
                and hidden_input(screen, *self.PASSWORD))

    def _logged_on(self, screen: Screen) -> bool:
        text, row, col = self.OK
        signon, srow, scol = self.SIGNON
        return screen.get(row, col, len(text)) == text and screen.get(srow, scol, len(signon)) != signon

    # ------------------------------------------------------------------ receitas
    def logon(self, user: str, password: str, *, environment: str = "C", timeout: float | None = None) -> Screen:
        """Logon no CICS a partir do menu de ambientes ou da tela de signon; devolve a tela "Signon is complete".

        Senha expirada -> ``PasswordExpired``; usuário/senha incorretos -> ``InvalidPassword``; tela desconhecida
        (ou campo da senha que não é oculto) -> ``UnexpectedScreen`` sem escrever nada.
        """
        t = self._t
        check_credentials(user, password)
        if not isinstance(environment, str) or not environment.strip():
            raise ParameterError.make(detalhe="environment deve ser uma str não vazia (ex.: 'C').")
        limit = t._timeout(timeout)
        screen = t.snapshot(fields=True)
        if self.is_menu(screen):
            t.fill(*self.MENU_FIELD, environment)
            t.enter(timeout=limit)
            text, row, col = self.SIGNON
            t.wait_text(text, row, col, timeout=limit)
            screen = t.snapshot(fields=True)
            if not self.is_signon(screen):
                raise unexpected(t, screen, "a tela de signon do CICS com o campo da senha oculto")
        elif not self.is_signon(screen):
            raise unexpected(t, screen, self.WHAT)
        submit_credentials(t, self.USER, self.PASSWORD, user, password, limit)
        options: dict[str, tuple[str, int, int] | Callable[[Screen], bool]] = {
            "expirada": (self.EXPIRED, *self.MSG),       # erros testados ANTES de "ok" (C11)
            "invalida": (self.INVALID, *self.MSG),
            "ok": self._logged_on,
        }
        result = t.wait_any(options, timeout=limit)
        if result.match == "expirada":
            raise PasswordExpired.make(terminal=t.terminal)
        if result.match == "invalida":
            raise InvalidPassword.make(terminal=t.terminal)
        logger.info("logon CICS concluído no terminal %s", t.terminal)
        return result

    def check(self, screen: Screen) -> None:
        """``HostError`` se a tela for a do "E R R O   G R A V E" (G.6)."""
        waits.cics_error_check(screen)

    def start_transaction(self, code: str, *, row: int = 1, col: int = 2,
                          wait_for: tuple[str, int, int] | None = None) -> Screen:
        """Escreve o código da transação em ``(row, col)``, tecla ENTER e (opcional) espera ``wait_for``."""
        t = self._t
        if not isinstance(code, str) or not code.strip():
            raise ParameterError.make(detalhe="Informe o código da transação (str não vazia).")
        if wait_for is not None and not (isinstance(wait_for, tuple) and len(wait_for) == 3):
            raise ParameterError.make(detalhe="wait_for deve ser (texto, linha, coluna).")
        t.write(row, col, code)
        t.enter()
        if wait_for is None:
            return t.snapshot()
        text, wrow, wcol = wait_for
        return t.wait_text(text, wrow, wcol)


__all__ = ["Cics", "check_credentials", "hidden_input", "input_field", "submit_credentials", "unexpected"]
