"""Motor de espera puro (G.2) e constantes das esperas (G.6) — sem I/O próprio.

Toda espera é polling de ``snapshot`` (texto + cursor + OIA numa op local ao emulador): nada é enviado ao host.
``WD_Wait == 0`` não diz que a próxima tela chegou; por isso as esperas olham o conteúdo da tela.

``wait_until`` recebe tudo por injeção (``snapshot``, ``clock``, ``sleep``) para ser testado com relógio falso.
A cada snapshot, nesta ordem:

1. ``error_checks`` (ex.: detector "E R R O   G R A V E" do CICS) — podem levantar a exceção que quiserem;
2. ``oia.online == False`` contínuo por ``host_lost_after`` segundos -> ``HostConnectionLost``;
3. ``cond(screen)`` — satisfeita se devolver ``True``, um ``Cursor``, uma ``str`` (mesmo vazia: chave de
   ``wait_any``) ou outro objeto verdadeiro; ``None``/``False`` = ainda não;
4. prazo estourado -> ``exc(describe(screen), screen=screen)``; a mensagem é exatamente a de ``describe`` (cabe ao
   chamador terminá-la com ``oia.describe()`` quando a regra pedir, como em G.4).

O primeiro snapshot é tirado na hora (também com ``timeout=0``); o último cai no prazo. ``KeyboardInterrupt``
(Ctrl+C) durante o ``sleep`` ou o ``snapshot`` interrompe a espera na hora (nunca é engolido).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Sequence

from .errors import HostConnectionLost, HostError, NoHostResponse, ParameterError, WaitTimeout
from .screen import Cursor, Screen

import dataclasses
import logging
import math
import time

logger = logging.getLogger(__name__)

Cond = Callable[[Screen], "Cursor | str | bool | None"]      # verdadeiro/não-None = satisfeita

# G.6 (não expostas no pacote)
DEFAULT_TIMEOUT = 30.0          # Session.timeout (RUMBAPY_TIMEOUT)
FAST_PHASE = 1.0                # polling rápido no 1º segundo...
FAST_POLL = 0.05                # ...a cada 50 ms
SLOW_POLL = 0.1                 # depois a cada 100 ms
READY_STABLE_GAP = 0.05         # ready_stable: 2 snapshots prontos e iguais com este intervalo
FIRST_SCREEN_STABLE = 0.3       # open: a 1ª tela precisa ficar estável por este tempo (E.5 passo 7)
HOST_LOST_AFTER = 3.0           # offline contínuo por este tempo -> HostConnectionLost (R29)
SCREEN_LOAD_TIMEOUT = 120.0     # compat RumbaClient.screen_load
CICS_ERROR_TEXT = "E R R O   G R A V E"
CICS_ERROR_AT = (5, 31)         # (linha, coluna) do detector de erro grave do CICS


def cics_error_check(screen: Screen) -> None:
    """Detector do CICS (G.6): ``screen.get(5, 31, 19) == "E R R O   G R A V E"`` -> ``HostError(screen=s)``."""
    (row, col), size = CICS_ERROR_AT, len(CICS_ERROR_TEXT)
    if screen.rows >= row and screen.cols >= col + size - 1 and screen.get(row, col, size) == CICS_ERROR_TEXT:
        raise HostError.make(terminal=screen.terminal or None,
                             screen=None if screen.hidden_unknown else screen)


def poll_interval(elapsed: float, *, fast_phase: float = FAST_PHASE, fast_poll: float = FAST_POLL,
                  slow_poll: float = SLOW_POLL) -> float:
    """Intervalo de polling depois de ``elapsed`` segundos de espera (50 ms no 1º segundo, depois 100 ms)."""
    return fast_poll if elapsed < fast_phase else slow_poll


def _seconds(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ParameterError.make(detalhe=f"{name} deve ser um número de segundos finito e >= 0 (recebi {value!r}).")
    return float(value)


def _satisfied(result: object) -> bool:
    if result is None or result is False:
        return False
    if isinstance(result, (str, Cursor)):
        return True
    return bool(result)


def _timeout_message(describe: Callable[[Screen], str], screen: Screen, timeout: float) -> str:
    """Mensagem do timeout = ``describe(screen)`` sem acréscimos (G.2): o texto exato é do chamador (B.7, G.3, G.4).

    Se ``describe`` falhar (bug do chamador), usa uma mensagem genérica que termina com o estado do OIA; o log só
    registra o tipo da exceção (nunca o texto nem o traceback: poderiam citar conteúdo da tela, B.4.5).
    """
    try:
        message = describe(screen)
        if not isinstance(message, str) or not message.strip():
            raise TypeError("describe deve devolver um texto não vazio")
    except Exception as exc:
        logger.error("wait_until: falha ao descrever o timeout (%s); uso a mensagem genérica", type(exc).__name__)
        message = (f"A condição de espera não aconteceu em {timeout:g} s. "
                   f"Estado do terminal: {screen.oia.describe()}.")
    return message


def wait_until(cond: Cond, *, snapshot: Callable[[], Screen], timeout: float, describe: Callable[[Screen], str],
               error_checks: Sequence[Callable[[Screen], None]] = (), clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], object] = time.sleep, fast_phase: float = FAST_PHASE,
               fast_poll: float = FAST_POLL, slow_poll: float = SLOW_POLL, host_lost_after: float = HOST_LOST_AFTER,
               exc: type[WaitTimeout] = WaitTimeout, hint: str | None = None) -> Screen:
    """Faz polling de ``snapshot()`` até ``cond`` ser satisfeita; devolve a tela com ``match`` preenchido.

    ``match`` = o ``Cursor``/``str`` devolvido por ``cond`` (``None`` para ``True`` ou outros objetos). Prazo
    estourado -> ``exc(mensagem, hint=hint, screen=última tela, terminal=...)`` (``screen=None`` se
    ``hidden_unknown``). Host fora do ar por ``host_lost_after`` -> ``HostConnectionLost``, com ``delivery="unknown"``
    se a espera é a resposta de uma AID já enviada (``exc`` = ``NoHostResponse`` ou subclasse).
    """
    if not callable(cond) or not callable(snapshot) or not callable(describe):
        raise ParameterError.make(detalhe="wait_until: cond, snapshot e describe devem ser chamáveis.")
    timeout = _seconds("timeout", timeout)
    host_lost_after = _seconds("host_lost_after", host_lost_after)
    fast_poll, slow_poll = _seconds("fast_poll", fast_poll), _seconds("slow_poll", slow_poll)
    fast_phase = _seconds("fast_phase", fast_phase)
    checks = tuple(error_checks)
    start = clock()
    deadline = start + timeout
    offline_since: float | None = None
    while True:
        screen = snapshot()
        now = clock()
        for check in checks:
            check(screen)
        if screen.oia.online:
            offline_since = None
        else:
            if offline_since is None:
                offline_since = now
            if now - offline_since >= host_lost_after:
                raise HostConnectionLost.make(L=screen.terminal or "?", terminal=screen.terminal or None,
                                              delivery="unknown" if issubclass(exc, NoHostResponse) else None,
                                              details={"offline_s": round(now - offline_since, 3)})
        result = cond(screen)
        if _satisfied(result):
            match = result if isinstance(result, (str, Cursor)) else None
            return dataclasses.replace(screen, match=match)
        if now >= deadline:
            raise exc(_timeout_message(describe, screen, timeout), hint=hint,
                      screen=None if screen.hidden_unknown else screen, terminal=screen.terminal or None)
        interval = poll_interval(now - start, fast_phase=fast_phase, fast_poll=fast_poll, slow_poll=slow_poll)
        sleep(max(0.0, min(interval, deadline - now)))
