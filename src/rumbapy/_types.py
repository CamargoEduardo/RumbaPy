"""Tipos de dados trocados entre as camadas (I1): perfis, sessões HLLAPI e resultados das ops do worker.

Puro e importável pelo worker (sem ``ctypes``, sem ``screen`` em tempo de import). Cada tipo tem ``to_wire()``
(dict JSON; posições como ``[row, col]``, OIA como hex) e ``from_wire(d)`` (formato inválido -> ``ProtocolError``;
chaves extras são ignoradas). O ``text`` lido da tela nunca aparece no ``repr()`` (tracebacks e logs com ``%r``).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Self
from dataclasses import dataclass, field, fields
from collections.abc import Mapping

from .errors import ProtocolError

if TYPE_CHECKING:
    from .screen import OIA

ProfileKind = Literal["cics", "ims"]
OpenVia = Literal["launcher", "ie", "reuse", "attach"]
InstanceKind = Literal["launcher", "ie", "hllapi", "launched"]

_MISSING = object()


def _bad(detail: str) -> ProtocolError:
    return ProtocolError.make(L="?", detalhe=detail)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _get(d: Mapping[str, Any], cls: type, key: str, kinds: tuple[type, ...], *, optional: bool = False,
         default: Any = _MISSING) -> Any:
    """Lê ``d[key]`` exigindo um dos tipos ``kinds`` (``bool`` nunca vale como ``int``)."""
    if key not in d:
        if default is not _MISSING:
            return default
        raise _bad(f"{cls.__name__}.{key} ausente")
    value = d[key]
    if value is None and optional:
        return None
    ok = any(_is_int(value) if kind is int else
             (isinstance(value, (int, float)) and not isinstance(value, bool)) if kind is float else
             isinstance(value, kind) for kind in kinds)
    if not ok:
        raise _bad(f"{cls.__name__}.{key} com tipo inválido ({type(value).__name__})")
    return float(value) if kinds == (float,) else value


def _require_mapping(d: Any, cls: type) -> Mapping[str, Any]:
    if not isinstance(d, Mapping):
        raise _bad(f"{cls.__name__} sem formato de objeto")
    return d


def _pair(value: Any, cls: type, key: str) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_int(v) for v in value):
        return int(value[0]), int(value[1])
    raise _bad(f"{cls.__name__}.{key} deve ser [linha, coluna]")


def _normalize_eab(obj: Any) -> None:
    """F.3 calcula ``eab = b[13] & 0x80`` (int 0/128): guarda sempre ``bool`` (o wire só aceita ``bool``)."""
    if _is_int(obj.eab):
        object.__setattr__(obj, "eab", bool(obj.eab))


class _Wire:
    """``to_wire`` genérico: campos da dataclass, tuplas viram listas."""

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


@dataclass(frozen=True)
class Profile(_Wire):
    """Perfil do portal com HLLAPI (E.1): ``Profile("BRAZIL_D", "D", "cics", "BRAZIL D")``."""

    name: str
    letter: str
    kind: ProfileKind
    link_text: str           # texto do link em index_pro_api.html (fallback IE)

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        kind = _get(d, cls, "kind", (str,))
        if kind not in ("cics", "ims"):
            raise _bad(f"Profile.kind inválido: {kind!r}")
        return cls(_get(d, cls, "name", (str,)), _get(d, cls, "letter", (str,)), kind, _get(d, cls, "link_text", (str,)))


@dataclass(frozen=True)
class SessionEntry(_Wire):
    """Uma entrada de ``WD_QuerySessions`` (16 B; ao vivo ``44 20x11 48 20 80 07`` -> D, tipo H, 1920)."""

    letter: str
    long_name: str
    type: str
    ps_size: int

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        return cls(_get(d, cls, "letter", (str,)), _get(d, cls, "long_name", (str,)), _get(d, cls, "type", (str,)),
                   _get(d, cls, "ps_size", (int,)))


@dataclass(frozen=True)
class SessionStatus(_Wire):
    """Resultado de ``WD_QuerySessionStatus`` (20 B; ao vivo tipo D, 24x80, code page 0 = não confiável)."""

    letter: str
    long_name: str
    type: str
    eab: bool                # aceita o int de "b[13] & 0x80" e guarda bool
    rows: int
    cols: int
    host_cp: int

    def __post_init__(self) -> None:
        _normalize_eab(self)

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        return cls(_get(d, cls, "letter", (str,)), _get(d, cls, "long_name", (str,)), _get(d, cls, "type", (str,)),
                   _get(d, cls, "eab", (bool,)), _get(d, cls, "rows", (int,)), _get(d, cls, "cols", (int,)),
                   _get(d, cls, "host_cp", (int,)))


@dataclass(frozen=True)
class ConnectResult(_Wire):
    """Resultado da op ``connect`` (uma tentativa). Com ``connected=False`` só ``rc`` vale (rc 1 = ainda conectando)."""

    connected: bool
    rc: int
    already: bool = False
    rows: int | None = None
    cols: int | None = None
    host_cp: int | None = None
    long_name: str | None = None
    type: str | None = None
    eab: bool | None = None  # aceita o int de "b[13] & 0x80" e guarda bool
    hwnd: int | None = None
    hwnd_pid: int | None = None

    def __post_init__(self) -> None:
        _normalize_eab(self)

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        connected = _get(d, cls, "connected", (bool,))
        rc = _get(d, cls, "rc", (int,))
        if not connected:
            return cls(False, rc, already=bool(_get(d, cls, "already", (bool,), default=False)))
        return cls(True, rc,
                   already=_get(d, cls, "already", (bool,), default=False),
                   rows=_get(d, cls, "rows", (int,)),
                   cols=_get(d, cls, "cols", (int,)),
                   host_cp=_get(d, cls, "host_cp", (int,), optional=True, default=None),
                   long_name=_get(d, cls, "long_name", (str,), optional=True, default=None),
                   type=_get(d, cls, "type", (str,), optional=True, default=None),
                   eab=_get(d, cls, "eab", (bool,), optional=True, default=None),
                   hwnd=_get(d, cls, "hwnd", (int,), optional=True, default=None),
                   hwnd_pid=_get(d, cls, "hwnd_pid", (int,), optional=True, default=None))


@dataclass(frozen=True)
class ReadResult(_Wire):
    """Resultado da op ``read``: ``nul_at`` = índice do 1º NUL no bruto (-1 se nenhum); ``nul_tail`` = NULs finais."""

    text: str = field(repr=False)
    busy: bool
    nul_at: int
    nul_tail: int

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        return cls(_get(d, cls, "text", (str,)), _get(d, cls, "busy", (bool,)), _get(d, cls, "nul_at", (int,)),
                   _get(d, cls, "nul_tail", (int,)))


@dataclass(frozen=True)
class FieldRead(_Wire):
    """Resultado da op ``read_field``: texto do campo que contém a posição pedida (tamanho real do campo)."""

    text: str = field(repr=False)
    start: tuple[int, int]   # (linha, coluna) da 1ª posição de dados
    length: int
    attr: int
    nul_tail: int

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        return cls(_get(d, cls, "text", (str,)), _pair(d.get("start"), cls, "start"), _get(d, cls, "length", (int,)),
                   _get(d, cls, "attr", (int,)), _get(d, cls, "nul_tail", (int,)))


@dataclass(frozen=True)
class SendResult:
    """Resultado da op ``send``: quantos chunks a DLL aceitou e o OIA lido logo após o último."""

    chunks_sent: int
    oia_after: OIA

    def to_wire(self) -> dict[str, Any]:
        return {"chunks_sent": self.chunks_sent, "oia_after": self.oia_after.raw.hex()}

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        from .screen import OIA          # import tardio: o worker usa _types sem precisar de screen

        d = _require_mapping(d, cls)
        raw = _get(d, cls, "oia_after", (str,))
        try:
            oia = OIA.from_raw(bytes.fromhex(raw))
        except ValueError:
            raise _bad("SendResult.oia_after inválido") from None
        return cls(_get(d, cls, "chunks_sent", (int,)), oia)


@dataclass(frozen=True)
class OpenInfo(_Wire):
    """Como a ``Session`` foi obtida (E.5): lançada, reaproveitada ou anexada."""

    launched: bool
    closed_instances: int
    hwnd: int | None
    hwnd_pid: int | None
    seconds_to_register: float | None
    seconds_to_connect: float | None
    rows: int
    cols: int
    via: OpenVia

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        via = _get(d, cls, "via", (str,))
        if via not in ("launcher", "ie", "reuse", "attach"):
            raise _bad(f"OpenInfo.via inválido: {via!r}")
        return cls(_get(d, cls, "launched", (bool,)), _get(d, cls, "closed_instances", (int,)),
                   _get(d, cls, "hwnd", (int,), optional=True), _get(d, cls, "hwnd_pid", (int,), optional=True),
                   _get(d, cls, "seconds_to_register", (float,), optional=True),
                   _get(d, cls, "seconds_to_connect", (float,), optional=True),
                   _get(d, cls, "rows", (int,)), _get(d, cls, "cols", (int,)), via)


@dataclass(frozen=True)
class WindowInfo(_Wire):
    """Janela de uma instância do terminal (título já sanitizado, E.6)."""

    hwnd: int
    pid: int
    title: str
    process: str | None      # basename minúsculo da imagem
    kind: InstanceKind
    visible: bool
    minimized: bool

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> Self:
        d = _require_mapping(d, cls)
        kind = _get(d, cls, "kind", (str,))
        if kind not in ("launcher", "ie", "hllapi", "launched"):
            raise _bad(f"WindowInfo.kind inválido: {kind!r}")
        return cls(_get(d, cls, "hwnd", (int,)), _get(d, cls, "pid", (int,)), _get(d, cls, "title", (str,)),
                   _get(d, cls, "process", (str,), optional=True), kind, _get(d, cls, "visible", (bool,)),
                   _get(d, cls, "minimized", (bool,)))
