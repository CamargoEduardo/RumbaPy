"""Handlers das ops do worker (D.5): valida por ``_protocol.OPS``, confere a instância (``hwnd``), chama o ``Engine`` e
traduz o que não for ``RumbaError``.

``ping`` não passa por aqui (a T-conn responde direto, D.3). Exceção Python inesperada num handler vira
``RumbaError("Erro interno no processo auxiliar (<Tipo>).")`` — o log leva só o tipo e o traceback (nunca
``str(exc)``, C21c); se a op for N ou ``connect``, ``engine.resync()`` e ``details.resynced=true`` (R35).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from ..errors import GuardViolation, NotConnected, ParameterError, RumbaError, SessionReplaced
from .engine import Engine, JobCancelled, key_names, text_after_key, text_units
from .. import _codec, _protocol
from . import guard as guardmod

import traceback
import threading
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class JobCtx:
    """Contexto de uma requisição: ``op``, conexão de origem, ``cancel`` (dono foi embora) e ``hwnd`` esperado."""

    op: str
    conn_id: int | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    hwnd: int | None = None
    id: int | None = None


Handler = Callable[[Engine, dict, JobCtx], dict]


def _wire_bytes(text: str) -> bytes:
    try:
        return text.encode(_codec.ENCODING)          # cp1252 estrito (C.3); o _protocol já validou
    except UnicodeEncodeError:
        raise ParameterError.make(detalhe="texto fora do cp1252") from None


def _sessions(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.sessions()


def _connect(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.connect(args["letter"])


def _disconnect(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.disconnect()


def _status(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.status()


def _snapshot(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.snapshot(args["fields"])


def _read(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.read(args["row"], args["col"], args["length"])


def _read_field(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.read_field(args["row"], args["col"])


def _write(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.write(args["row"], args["col"], _wire_bytes(args["text"]), args["secret"])


def _send(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.send([_wire_bytes(c) for c in args["chunks"]], args["secret"], ctx.cancel)


def _set_cursor(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    return engine.set_cursor(args["row"], args["col"])


def _evict(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    result = engine.evict()
    return {"evicted_owner": None, **result}          # o servidor preenche evicted_owner (C.6.2)


def _shutdown(engine: Engine, args: dict, ctx: JobCtx) -> dict:
    engine.stop_calls()
    return {}


def _describe(op: str, args: Mapping[str, Any], *, secret: bool = False, may_hide: bool = False) -> str:
    """Resumo da requisição para o log: coordenadas e tamanhos (omitidos se ``secret``); nunca texto (D.7).

    ``secret`` = o efetivo (o ``Engine`` promove a secreto o ``write`` num campo oculto); ``may_hide`` = a tela pode
    ter campo oculto (``Engine.screen_may_hide``): o ``send`` sem ``secret`` não diz quantas unidades de texto mandou."""
    secret = secret or bool(args.get("secret"))
    parts: list[str] = []
    for key in ("row", "col"):
        if key in args:
            parts.append(f"{key}={args[key]}")
    if "length" in args:
        parts.append(f"len={args['length']}")
    if op == "write" and not secret and isinstance(args.get("text"), str):
        parts.append(f"len={len(args['text'])}")
    if op == "send" and isinstance(args.get("chunks"), list):
        data = [c.encode(_codec.ENCODING, "replace") for c in args["chunks"] if isinstance(c, str)]
        keys = [name for chunk in data for name in key_names(chunk)]
        count = sum(text_units(chunk) for chunk in data)
        if secret:
            units = "oculto"
        elif count and (text_after_key(data) or may_hide):   # depois de <Tab> ou tela com campo oculto: senha?
            units = "omitido"
        else:
            units = str(count)
        parts.append(f"keys=[{','.join(keys)}] text_units={units}")
        if not secret:
            parts.append(f"chunks={len(data)}")
    if op == "connect":
        parts.append(f"letter={args.get('letter')}")
    if secret:
        parts.append("secret")
    return " ".join(parts)


class Dispatcher:
    """``handle(op, args, ctx)``: valida (``OPS``), checa ``hwnd``, chama o handler e traduz exceções."""

    HANDLERS: ClassVar[Mapping[str, Handler]] = MappingProxyType({
        "sessions": _sessions,
        "connect": _connect,
        "disconnect": _disconnect,
        "status": _status,
        "snapshot": _snapshot,
        "read": _read,
        "read_field": _read_field,
        "write": _write,
        "send": _send,
        "set_cursor": _set_cursor,
        "evict": _evict,
        "shutdown": _shutdown,
    })

    def __init__(self, engine: Engine, *, guard: str = "none") -> None:
        self.engine = engine
        self.guard = guard

    def geometry(self) -> tuple[int | None, int | None]:
        """``rows/cols`` da sessão conectada (A.8 e); ``(None, None)`` sem conexão."""
        engine = self.engine
        return (engine.rows, engine.cols) if engine.connected else (None, None)

    def validate(self, op: str, args: dict) -> dict:
        """``dispatch.validate`` (C.4): tipos exatos, argumentos e posições contra a geometria conectada."""
        rows, cols = self.geometry()
        return _protocol.validate_args(op, args, rows=rows, cols=cols)

    def _check_instance(self, spec: _protocol.Op, ctx: JobCtx) -> None:
        engine = self.engine
        if not spec.checks_hwnd:
            return
        if ctx.hwnd is None:
            # sem bind_instance não há como provar que é a mesma instância — salvo se o próprio worker não tem hwnd
            # (GetSessionHWND devolveu 0 -> None, D.5/F.6): aí None == None é a mesma instância
            if engine.connected and engine.hwnd is not None:
                raise ParameterError.make(detalhe=f"a operação {spec.name} exige o hwnd da instância conectada "
                                                  f"(bind_instance depois do connect, C.6.5)", terminal=engine.letter)
            return
        if engine.connected:
            if engine.hwnd != ctx.hwnd:
                raise SessionReplaced.make(L=engine.letter or "?", terminal=engine.letter)
        elif engine.last_disconnect in ("evict", "resync", "stop"):
            raise SessionReplaced.make(L=engine.letter or engine.bound_letter or "?", terminal=engine.letter)

    def _may_reconnect(self, spec: _protocol.Op, exc: NotConnected) -> bool:
        """Repetir depois de reconectar? Só com o worker conectado (a queda veio da DLL, não do cliente) e só se nada
        pode ter chegado ao host: leitura (idempotente) ou entrada cuja 1ª chamada à DLL já foi recusada (rc 1 =
        sem conexão, ``delivery`` ``none``). Nunca reenvia entrada parcial (B.1)."""
        if not self.engine.connected or not spec.needs_connection or spec.name in ("connect", "disconnect"):
            return False
        return spec.idempotent or exc.delivery in (None, "none")

    def handle(self, op: str, args: dict, ctx: JobCtx) -> dict:
        engine = self.engine
        t0 = time.perf_counter()
        spec = _protocol.OPS.get(op)
        engine.last_rc = None
        engine.last_sha = None
        engine.last_secret = False
        outcome = "ok"
        valid: dict | None = None
        try:
            if spec is None or op not in self.HANDLERS:
                raise ParameterError.make(detalhe=f"operação desconhecida: {op!r}")
            args = valid = self.validate(op, args)
            self._check_instance(spec, ctx)
            if spec.needs_connection and not engine.connected:
                raise NotConnected.make(L=engine.letter or engine.bound_letter or "?", terminal=engine.letter)
            try:
                return self.HANDLERS[op](engine, args, ctx)
            except NotConnected as exc:
                if not self._may_reconnect(spec, exc):
                    raise
                engine.reconnect_same()           # SessionReplaced/NotConnected se a instância mudou ou sumiu
                logger.warning("op=%s id=%s: a conexão HLLAPI caiu com a sessão aberta (rc 1) e foi refeita; repetindo "
                               "1x", op, ctx.id)
                return self.HANDLERS[op](engine, args, ctx)
        except RumbaError as exc:
            outcome = f"erro={type(exc).__name__}"
            raise
        except JobCancelled:
            outcome = "cancelado"
            raise
        except guardmod.GuardViolation as exc:
            outcome = "erro=GuardViolation"
            raise GuardViolation.make(politica=self.guard, detalhe=str(exc), terminal=engine.letter) from None
        except Exception as exc:
            outcome = f"erro=interno({type(exc).__name__})"
            logger.error("op=%s id=%s exceção interna %s\n%s", op, ctx.id, type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            details: dict[str, Any] = {}
            if spec is not None and (not spec.idempotent or op == "connect"):
                engine.resync()
                details["resynced"] = True
            raise RumbaError.make("internal", tipo=type(exc).__name__, details=details or None,
                                  terminal=engine.letter) from None
        finally:
            ms = (time.perf_counter() - t0) * 1000
            # resumo DEPOIS do handler: o secret efetivo só se sabe lá (write num campo oculto, D.7)
            summary = "" if valid is None else _describe(op, valid, secret=engine.last_secret,
                                                          may_hide=op == "send" and engine.screen_may_hide())
            rc = "" if engine.last_rc is None else f"rc={engine.last_rc}"
            sha = "" if engine.last_sha is None else f"sha={engine.last_sha}"
            logger.info(" ".join(p for p in (f"op={op}", f"id={ctx.id}", summary, rc, f"ms={ms:.1f}", outcome, sha)
                                 if p))
