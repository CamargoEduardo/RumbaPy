"""Receita IMS (B.8): USS -> "Welcome" (usuário/senha) -> "Start" (aplicação).

Âncoras da v0.1.5, **a validar no roteiro L7** (exige autorização): "USS010" em (2,1), comando USS em (5,1),
"Welcome" em (4,44), usuário em (10,47), senha em (11,47) — **oculta** —, "Start" em (16,20) e aplicação em (16,47).
"""
# ruff: noqa: I001
from __future__ import annotations

from .cics import check_credentials, input_field, hidden_input, submit_credentials, unexpected
from ..errors import LogonFailed, ParameterError, WaitTimeout
from typing import TYPE_CHECKING
from ..screen import Screen

import logging

if TYPE_CHECKING:
    from ..session import Session

logger = logging.getLogger(__name__)


class Ims:
    """Receitas dos perfis IMS (``t.ims``)."""

    USS = ("USS010", 2, 1)
    USS_COMMAND = (5, 1)
    WELCOME = ("Welcome", 4, 44)
    USER = (10, 47)
    PASSWORD = (11, 47)
    START = ("Start", 16, 20)
    APP = (16, 47)

    def __init__(self, session: Session) -> None:
        self._t = session

    def __repr__(self) -> str:
        return f"<Ims {self._t.terminal}>"

    def logon(self, user: str, password: str, *, app: str = "RHELP", uss_command: str = "IMSRS",
              timeout: float | None = None) -> Screen:
        """Logon no IMS a partir da tela USS; devolve a tela depois de abrir ``app``.

        A tela "Start" não apareceu no prazo -> ``LogonFailed`` (com a tela); campo da senha não oculto ->
        ``UnexpectedScreen`` sem escrever a senha.
        """
        t = self._t
        check_credentials(user, password)
        for name, value in (("app", app), ("uss_command", uss_command)):
            if not isinstance(value, str) or not value.strip():
                raise ParameterError.make(detalhe=f"{name} deve ser uma str não vazia.")
        limit = t._timeout(timeout)
        text, row, col = self.USS
        t.wait_text(text, row, col, timeout=limit)
        t.write(*self.USS_COMMAND, uss_command)
        t.enter(timeout=limit)
        text, row, col = self.WELCOME
        t.wait_text(text, row, col, timeout=limit)
        screen = t.snapshot(fields=True)
        if input_field(screen, *self.USER) is None or not hidden_input(screen, *self.PASSWORD):
            raise unexpected(t, screen, "a tela 'Welcome' do IMS com o campo da senha oculto")
        submit_credentials(t, self.USER, self.PASSWORD, user, password, limit)
        text, row, col = self.START
        try:
            t.wait_text(text, row, col, timeout=limit)
        except WaitTimeout as exc:
            raise LogonFailed.make(motivo=f"a tela '{text}' não apareceu em {limit:g} s", terminal=t.terminal,
                                   screen=exc.screen) from None
        t.fill(*self.APP, app)
        t.enter(timeout=limit)
        logger.info("logon IMS concluído no terminal %s", t.terminal)
        return t.snapshot()


__all__ = ["Ims"]
