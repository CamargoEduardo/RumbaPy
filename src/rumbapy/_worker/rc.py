"""rc da ``ehlapi32.Dll`` -> exceção (F.6). As mensagens nascem aqui (B.7: "mensagens nascem onde o erro nasce").

``check(func, rc, ctx)`` não faz nada para sucesso e para os rc tolerados da tabela (quem chama interpreta: ``busy``,
``connected=false``, ``None``...) e levanta a exceção do rc para os demais. ``ctx`` traz os campos das mensagens:
``L`` (letra), ``r``/``c``/``rows``/``cols`` (posição), ``n``/``m`` (tamanhos de ``Truncated``), ``secret``,
``delivery`` e ``details``. Genéricos: 12 ``SessionStopped``, 36 ``HostConnectionLost``, 9 ``HllapiSystemError``;
desconhecido -> ``HllapiError("rc N")``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from ..errors import (FieldNotFound, HllapiError, HllapiSystemError, HostBusy, HostConnectionLost, InvalidPosition,
                      KeyboardLocked, NotConnected, ParameterError, RumbaError, SessionStopped, TerminalInUse,
                      TerminalNotOpen, Truncated, UndefinedKey)

Outcome = Literal["ok", "tolerated", "error"]
ANY = "*"                                   # ResetSystem: qualquer rc é aceito (só log)

# função -> (sucesso, tolerados, {rc: tipo de erro}); rc fora dos três -> genéricos (12/36/9) ou desconhecido
TABLE: Mapping[str, tuple[frozenset[int] | str, frozenset[int], Mapping[int, str]]] = MappingProxyType({
    "WD_ConnectPS": (frozenset({0, 4, 5}), frozenset({1}), {2: "param", 11: "in_use"}),
    "WD_DisconnectPS": (frozenset({0}), frozenset({1}), {}),
    "WD_ResetSystem": (ANY, frozenset(), {}),
    "WD_SetSessionParamEx": (frozenset({0}), frozenset({2}), {}),
    "WD_SendKey": (frozenset({0}), frozenset(), {1: "not_connected", 2: "param", 4: "busy", 5: "locked",
                                                 20: "undefined_key"}),
    "WD_CopyPS": (frozenset({0}), frozenset({4, 5}), {1: "not_connected"}),
    "WD_CopyOIA": (frozenset({0}), frozenset({4, 5}), {1: "not_connected"}),
    "WD_CopyPSToString": (frozenset({0}), frozenset({4, 5}), {1: "not_connected", 2: "param", 7: "position"}),
    "WD_CopyStringToPS": (frozenset({0}), frozenset(), {1: "not_connected", 2: "param", 5: "locked",
                                                        6: "truncated", 7: "position"}),
    "WD_CopyFieldToString": (frozenset({0}), frozenset(), {1: "not_connected", 2: "param", 6: "truncated",
                                                           7: "position", 24: "no_field"}),
    "WD_QueryCursorLocation": (frozenset({0}), frozenset(), {1: "not_connected"}),
    "WD_SetCursor": (frozenset({0}), frozenset(), {1: "not_connected", 4: "busy", 7: "position"}),
    "WD_QueryFieldAttribute": (frozenset({0}), frozenset({24}), {1: "not_connected", 7: "position"}),
    "WD_FindFieldPosition": (frozenset({0}), frozenset({24, 28}), {1: "not_connected", 2: "param", 7: "position"}),
    "WD_FindFieldLength": (frozenset({0}), frozenset({24, 28}), {1: "not_connected", 2: "param", 7: "position"}),
    "WD_QuerySessions": (frozenset({0}), frozenset(), {2: "param", 9: "system"}),
    "WD_QuerySessionStatus": (frozenset({0}), frozenset(), {1: "not_open", 2: "param"}),
})
GENERIC: Mapping[int, str] = MappingProxyType({12: "stopped", 36: "host_lost", 9: "system"})

# delivery padrão quando o contexto não diz nada (D.5): CopyStringToPS rc 5 = nada copiado; rc 6 = parte copiada
_DEFAULT_DELIVERY: Mapping[tuple[str, int], str] = MappingProxyType({
    ("WD_CopyStringToPS", 5): "none",
    ("WD_CopyStringToPS", 6): "partial",
    ("WD_SetCursor", 4): "none",
})


def classify(func: str, rc: int) -> Outcome:
    """``"ok"`` (sucesso), ``"tolerated"`` (o chamador interpreta) ou ``"error"`` (``check`` levanta)."""
    success, tolerated, _errors = TABLE[func]
    if isinstance(success, str) or rc in success:     # ANY: qualquer rc é sucesso
        return "ok"
    return "tolerated" if rc in tolerated else "error"


def _param_detail(func: str, rc: int) -> str:
    return f"O RUMBA recusou os parâmetros de {func} (rc {rc})."


def error_for(func: str, rc: int, ctx: Mapping[str, Any] | None = None) -> RumbaError | None:
    """A exceção do rc (``None`` para sucesso/tolerado)."""
    if classify(func, rc) != "error":
        return None
    ctx = dict(ctx or {})
    letter = ctx.get("L") or "?"
    common: dict[str, Any] = {"rc": rc, "function": func, "terminal": ctx.get("L"), "details": ctx.get("details")}
    delivery = ctx.get("delivery") or _DEFAULT_DELIVERY.get((func, rc))
    if delivery is not None:
        common["delivery"] = delivery
    kind = TABLE[func][2].get(rc) or GENERIC.get(rc)
    r, c = ctx.get("r", "?"), ctx.get("c", "?")
    if kind == "param":
        return ParameterError.make(detalhe=ctx.get("detalhe") or _param_detail(func, rc), **common)
    if kind == "in_use":
        return TerminalInUse.make("rc", L=letter, extra=ctx.get("extra", ""), **common)
    if kind == "not_connected":
        return NotConnected.make(L=letter, **common)
    if kind == "not_open":
        return TerminalNotOpen.make(L=letter, **common)
    if kind == "busy":
        return HostBusy.make(**common)
    if kind == "locked":
        return KeyboardLocked.make(**common)
    if kind == "undefined_key":
        x = ctx.get("x") or "?"
        return UndefinedKey.make(x=x, sugestao="", **common)
    if kind == "position":
        return InvalidPosition.make(r=r, c=c, rows=ctx.get("rows", "?"), cols=ctx.get("cols", "?"), **common)
    if kind == "truncated":
        if ctx.get("secret"):
            return Truncated.make("secret", r=r, c=c, **common)
        return Truncated.make(n=ctx.get("n", "?"), m=ctx.get("m", "?"), r=r, c=c, **common)
    if kind == "no_field":
        return FieldNotFound.make(r=r, c=c, **common)
    if kind == "stopped":
        return SessionStopped.make(L=letter, **common)
    if kind == "host_lost":
        return HostConnectionLost.make(L=letter, **common)
    if kind == "system":
        return HllapiSystemError.make(funcao=func, **common)
    return HllapiError(f"rc {rc}", **common)


def check(func: str, rc: int, ctx: Mapping[str, Any] | None = None) -> None:
    """Levanta a exceção de ``rc`` (F.6); sucesso e tolerados não levantam."""
    exc = error_for(func, rc, ctx)
    if exc is not None:
        raise exc
