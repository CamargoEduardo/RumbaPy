"""Protocolo 2 entre o cliente 64-bit e o worker 32-bit (C.1-C.4): tabela ``OPS``, limites e formato das mensagens.

Puro (sem I/O e sem ``ctypes``): usado pelos dois lados. ``validate_request`` faz as validações de C.4; linha/coluna
só são conferidas quando a geometria da sessão é informada (``rows``/``cols``, que o worker conhece depois do
``connect``): fora de ``1..rows``/``1..cols`` (inclusive 0) -> ``InvalidPosition`` (``IndexError``), nunca
``ParameterError``.

Mensagens (só ``send_bytes``/``recv_bytes``; JSON UTF-8 compacto; pickle proibido)::

    {"id":17,"op":"read","args":{"row":23,"col":7,"length":74},"hwnd":1234567}
    {"id":17,"ok":true,"result":{...},"ms":1.3}
    {"id":17,"ok":false,"error":{"type":"KeyboardLocked","message":"…","rc":5,...}}
    {"event":"evicted","reason":"close"}
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from dataclasses import dataclass
from typing import Any, Literal

from .errors import ParameterError, ProtocolError, RumbaError, UndefinedKey, from_wire, to_wire
from ._version import PROTOCOL as PROTOCOL, __version__
from .keys import AIDS, Key, tokenize_mnemonic
from . import _codec

import json

VERSION = __version__

# ------------------------------------------------------------------------------------------- transporte (C.1, C.2)
MAX_MESSAGE = 1_048_576            # bytes por mensagem; excesso -> ProtocolError e fecha
PIPE_BUFFER = 65_536
MAX_CONNECTIONS = 8                # conexões vivas por worker; o excedente é fechado na hora
JOB_QUEUE_MAX = 64                 # fila global de T-dll; cheia -> ParameterError
CONNECT_TIMEOUT = 5.0              # ipc.connect(name, expected_pid, timeout=5.0)
WAIT_NAMED_PIPE_MS = 200
AUTH_VERSION = 1
AUTH_STEP_S = 2.0                  # prazo de cada passo da autenticação, nos dois lados
AUTH_NONCE_BYTES = 32
AUTH_KEY_BYTES = 32                # authkey.bin
AUTH_TAG_CLIENT = b"rumbapy2|c|"   # mc = hmac(K, AUTH_TAG_CLIENT + nw + nc)
AUTH_TAG_WORKER = b"rumbapy2|w|"   # mw = hmac(K, AUTH_TAG_WORKER + nc + nw)
HELLO_ACK_TIMEOUT = 3.0

# ------------------------------------------------------------------------------------------- handshake (C.3)
MODES: tuple[str, ...] = ("owner", "admin")
WORKER_STATES: tuple[str, ...] = ("listening", "owned", "stopping")
REJECT_REASONS: tuple[str, ...] = ("busy", "build", "stopping")
HELLO_KEYS: tuple[str, ...] = ("t", "protocol", "version", "build_id", "pid", "letter", "python", "started_at",
                               "state", "owner", "connected", "hwnd", "guard")
HELLO_ACK_KEYS: tuple[str, ...] = ("t", "mode", "client_pid", "client_version", "build_id", "script")
OWNER_KEYS: tuple[str, ...] = ("pid", "script", "since")
GUARD_MODES: tuple[str, ...] = ("none", "readonly", "text", "login")

# ------------------------------------------------------------------------------------------- eventos
EVENTS: tuple[str, ...] = ("evicted", "shutting_down")
SHUTDOWN_REASONS: tuple[str, ...] = ("idle", "stop_event", "watchdog", "shutdown", "build")

# ------------------------------------------------------------------------------------------- registro do worker (D.7)
REGISTRY_KEYS: tuple[str, ...] = ("protocol", "version", "build_id", "pid", "created", "letter", "pipe", "python",
                                  "started_at", "log", "state", "owner", "connected", "hwnd", "hwnd_pid",
                                  "exit_reason")

# ------------------------------------------------------------------------------------------- códigos de saída (D.2)
EXIT_CLEAN = 0
EXIT_UNHANDLED = 1
EXIT_WATCHDOG = 3
EXIT_IDLE = 5
EXIT_DLL = 10
EXIT_PIPE_EXISTS = 11
EXIT_PYTHON = 12
EXIT_KEY = 13
EXIT_BUILD = 14
EXIT_PID_COLLISION = 15
EXIT_LIVE_WITHOUT_GUARD = 16
EXIT_SANDBOX = 17
EXIT_DACL = 18
EXIT_CODES: Mapping[int, str] = MappingProxyType({
    EXIT_CLEAN: "parada limpa",
    EXIT_UNHANDLED: "exceção não tratada",
    EXIT_WATCHDOG: "watchdog, janela fechada durante chamada ou parada com a DLL presa",
    EXIT_IDLE: "ocioso",
    EXIT_DLL: "a DLL não carregou",
    EXIT_PIPE_EXISTS: "o pipe já existe",
    EXIT_PYTHON: "Python inadequado",
    EXIT_KEY: "chave inválida",
    EXIT_BUILD: "build divergente",
    EXIT_PID_COLLISION: "colisão de LOWORD(pid)",
    EXIT_LIVE_WITHOUT_GUARD: "RUMBAPY_LIVE=1 sem --guard",
    EXIT_SANDBOX: "sandbox com a DLL real",
    EXIT_DACL: "DACL do pipe",
})

# ------------------------------------------------------------------------------------------- limites das ops (C.4, F.5)
MAX_CHUNKS = 64
CHUNK_MAX_BYTES = 128              # limite de projeto por WD_SendKey (a DLL recusa >= 256 com rc 2)
SNAPSHOT_FIELDS_TIMEOUT = 90.0     # snapshot com fields=True
SEND_TIMEOUT_EXTRA = 5.0           # send: client_timeout * n + 5
REASON_MAX = 64
WIRE_ENCODING = "cp1252"           # texto no wire = bytes_cp1252.decode("cp1252")

OpMode = Literal["owner", "admin", "both"]
ArgType = type | tuple[type, ...]


@dataclass(frozen=True)
class Op:
    """Uma operação do worker. ``client_timeout`` é o prazo do cliente (> watchdog da op + 5 s); para ``send`` é o
    prazo POR chunk (total = ``client_timeout * n + 5``) e ``snapshot`` com ``fields=True`` usa 90 s
    (ver :func:`client_timeout`)."""

    name: str
    args: Mapping[str, ArgType]    # nomes e tipos exatos (bool não vale como int)
    mode: OpMode
    idempotent: bool               # repetível 1x após reciclar o worker
    needs_connection: bool         # NotConnected se o worker não estiver conectado
    checks_hwnd: bool              # compara req["hwnd"] com o HWND conectado (SessionReplaced)
    client_timeout: float


def _op(name: str, args: Mapping[str, ArgType], mode: OpMode, idempotent: bool, needs_connection: bool,
        checks_hwnd: bool, client_timeout: float) -> Op:
    return Op(name, MappingProxyType(dict(args)), mode, idempotent, needs_connection, checks_hwnd, client_timeout)


OPS: Mapping[str, Op] = MappingProxyType({op.name: op for op in (
    _op("ping", {}, "both", True, False, False, 5.0),
    _op("sessions", {}, "both", True, False, False, 25.0),
    _op("connect", {"letter": str}, "owner", True, False, False, 25.0),
    _op("disconnect", {}, "owner", True, False, False, 25.0),
    _op("status", {}, "owner", True, True, True, 25.0),
    _op("snapshot", {"fields": bool}, "owner", True, True, True, 25.0),
    _op("read", {"row": int, "col": int, "length": int}, "owner", True, True, True, 25.0),
    _op("read_field", {"row": int, "col": int}, "owner", True, True, True, 25.0),
    _op("write", {"row": int, "col": int, "text": str, "secret": bool}, "owner", False, True, True, 45.0),
    _op("send", {"chunks": list, "secret": bool}, "owner", False, True, True, 25.0),
    _op("set_cursor", {"row": int, "col": int}, "owner", False, True, True, 25.0),
    _op("evict", {"reason": str}, "admin", False, False, False, 25.0),
    _op("shutdown", {"reason": str}, "both", False, False, False, 30.0),
)})

# ops aceitas numa conexão admin: o contrato estável entre versões (C.3 passo 4)
ADMIN_OPS: frozenset[str] = frozenset(name for name, op in OPS.items() if op.mode in ("admin", "both"))
OWNER_OPS: frozenset[str] = frozenset(name for name, op in OPS.items() if op.mode in ("owner", "both"))

# chaves do resultado de cada op (C.4); "fields" do snapshot só vem com fields=True
RESULT_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "ping": ("uptime_s", "letter", "state", "connected", "owner_pid", "hwnd"),
    "sessions": ("sessions",),
    "connect": ("connected", "rc", "already", "rows", "cols", "host_cp", "long_name", "type", "eab", "hwnd",
                "hwnd_pid"),
    "disconnect": (),
    "status": ("letter", "long_name", "type", "eab", "rows", "cols", "host_cp"),
    "snapshot": ("text", "cursor", "oia", "busy", "hidden_unknown"),
    "read": ("text", "busy", "nul_at", "nul_tail"),
    "read_field": ("text", "start", "length", "attr", "nul_tail"),
    "write": (),
    "send": ("chunks_sent", "oia_after"),
    "set_cursor": (),
    "evict": ("evicted_owner", "was_connected"),
    "shutdown": (),
})


def client_timeout(op: str, args: Mapping[str, Any] | None = None) -> float:
    """Prazo do cliente para a requisição (C.4): ``send`` = 25·n + 5; ``snapshot`` com campos = 90."""
    spec = OPS[op]
    args = args or {}
    if op == "snapshot" and args.get("fields") is True:
        return SNAPSHOT_FIELDS_TIMEOUT
    if op == "send":
        chunks = args.get("chunks")
        n = len(chunks) if isinstance(chunks, (list, tuple)) else 1
        return spec.client_timeout * max(n, 1) + SEND_TIMEOUT_EXTRA
    return spec.client_timeout


# ------------------------------------------------------------------------------------------- validação

@dataclass(frozen=True)
class Request:
    id: int
    op: str
    args: dict[str, Any]
    hwnd: int | None

    @property
    def spec(self) -> Op:
        return OPS[self.op]


_ENVELOPE_KEYS = frozenset({"id", "op", "args", "hwnd"})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _type_ok(value: Any, kind: ArgType) -> bool:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    for k in kinds:
        if k is int and _is_int(value):
            return True
        if k is not int and type(value) is k:
            return True
    return False


def _type_name(kind: ArgType) -> str:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return " | ".join(k.__name__ for k in kinds)


def _param(detail: str) -> ParameterError:
    return ParameterError.make(detalhe=detail)


def _proto(detail: str, letter: str | None) -> ProtocolError:
    return ProtocolError.make(L=letter or "?", detalhe=detail, terminal=letter)


def _has_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _validate_text(text: str, what: str, secret: bool) -> bytes:
    """Texto de wire -> bytes cp1252 (estrito), sem caracteres de controle."""
    try:
        data = text.encode(WIRE_ENCODING)
    except UnicodeEncodeError:
        raise _param(f"{what}: texto fora do cp1252" + ("" if secret else " (use _codec.encode no cliente)")) from None
    if _has_control(text):
        raise _param(f"{what}: caractere de controle não é permitido")
    return data


def _validate_send(args: Mapping[str, Any]) -> None:
    """``secret=True``: nenhuma mensagem cita trechos, tamanhos em bytes nem o total de chunks (D.7, I15)."""
    chunks, secret = args["chunks"], args["secret"]
    if not 1 <= len(chunks) <= MAX_CHUNKS:
        raise _param(f"send: de 1 a {MAX_CHUNKS} chunks por requisição" + ("" if secret else f" (recebi {len(chunks)})"))
    last = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        if not isinstance(chunk, str):
            raise _param(f"send: chunk {i} deve ser str (recebi {type(chunk).__name__})")
        if not chunk:
            raise _param(f"send: chunk {i} vazio")
        data = _validate_text(chunk, f"send: chunk {i}", secret)
        if len(data) > CHUNK_MAX_BYTES:
            if secret:
                raise _param(f"send: chunk {i} excede {CHUNK_MAX_BYTES} bytes")
            raise _param(f"send: chunk {i} tem {len(data)} bytes (máximo {CHUNK_MAX_BYTES})")
        try:
            tokens = tokenize_mnemonic(chunk)
        except UndefinedKey:
            if secret:                     # nunca cite trechos de um valor secreto
                raise UndefinedKey(f"send: chunk {i} com mnemônico inválido.") from None
            raise
        aids = [t for t in tokens if isinstance(t, Key) and t in AIDS]
        if aids and (i != last or len(tokens) != 1):
            raise _param("send: uma tecla AID só pode vir sozinha, como o último chunk")


def _check_position(op: str, args: Mapping[str, Any], rows: Any, cols: Any) -> None:
    """C.4: ``1 <= row <= rows``, ``1 <= col <= cols`` e, em ``read``, ``pos+length-1 <= rows*cols``."""
    if "row" not in args:
        return
    pos = _codec.to_pos(args["row"], args["col"], rows, cols)          # InvalidPosition (inclusive 0 e negativos)
    if op == "read":
        _codec.check_span(pos, args["length"], rows, cols)


def validate_args(op: str, args: Mapping[str, Any], *, rows: int | None = None,
                  cols: int | None = None) -> dict[str, Any]:
    """Nomes e tipos exatos dos argumentos de ``op`` + valores (C.4).

    Com a geometria (``rows`` e ``cols``), posições fora da tela -> ``InvalidPosition``; sem ela, linha/coluna não
    são conferidas aqui (quem tem a geometria confere antes da DLL; nunca ``ParameterError`` para posição).
    """
    spec = OPS[op]
    given, expected = set(args), set(spec.args)
    if given - expected:
        raise _param(f"{op}: argumento(s) desconhecido(s): {', '.join(sorted(given - expected))}")
    if expected - given:
        raise _param(f"{op}: argumento(s) ausente(s): {', '.join(sorted(expected - given))}")
    for name, kind in spec.args.items():
        if not _type_ok(args[name], kind):
            raise _param(f"{op}: {name} deve ser {_type_name(kind)} (recebi {type(args[name]).__name__})")
    out = dict(args)
    if op == "read" and out["length"] < 1:
        raise _param(f"read: length deve ser >= 1 (recebi {out['length']})")
    if rows is not None or cols is not None:
        _check_position(op, out, rows, cols)
    if op == "write":
        if not out["text"]:
            raise _param("write: texto vazio")
        _validate_text(out["text"], "write", out["secret"])
    if op == "send":
        out["chunks"] = list(out["chunks"])
        _validate_send(out)
    if op == "connect":
        letter = out["letter"]
        if not (len(letter) == 1 and "A" <= letter <= "Z"):
            raise _param(f"connect: letra inválida {letter!r} (A-Z maiúscula)")
    if op in ("evict", "shutdown"):
        reason = out["reason"]
        if not reason or len(reason) > REASON_MAX or _has_control(reason):
            raise _param(f"{op}: reason inválido")
    return out


def validate_request(msg: Any, *, mode: str | None = None, letter: str | None = None, rows: int | None = None,
                     cols: int | None = None) -> Request:
    """Valida o envelope ``{"id","op","args","hwnd"}`` e os argumentos (C.4).

    Envelope inválido -> ``ProtocolError`` (o worker fecha a conexão). Op desconhecida, op proibida no ``mode``
    da conexão, argumento ausente/desconhecido/tipo errado ou valor inválido -> ``ParameterError``. Com ``rows`` e
    ``cols`` (geometria da sessão conectada), posição fora da tela -> ``InvalidPosition``.
    """
    if not isinstance(msg, Mapping):
        raise _proto("requisição sem formato de objeto", letter)
    extra = set(msg) - _ENVELOPE_KEYS
    if extra:
        raise _proto(f"campos desconhecidos na requisição: {', '.join(sorted(map(str, extra)))}", letter)
    rid = msg.get("id")
    if not _is_int(rid) or rid < 1:
        raise _proto("id ausente ou inválido", letter)
    op = msg.get("op")
    if not isinstance(op, str):
        raise _proto("op ausente", letter)
    hwnd = msg.get("hwnd")
    if hwnd is not None and not _is_int(hwnd):
        raise _proto("hwnd inválido", letter)
    args = msg.get("args", {})
    if not isinstance(args, Mapping):
        raise _proto("args deve ser um objeto", letter)
    if op not in OPS:
        raise _param(f"operação desconhecida: {op!r}")
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"modo de conexão inválido: {mode!r}")
        allowed = ADMIN_OPS if mode == "admin" else OWNER_OPS
        if op not in allowed:
            raise _param(f"a operação {op!r} não é permitida numa conexão {mode}")
    return Request(rid, op, validate_args(op, args, rows=rows, cols=cols), hwnd)


# ------------------------------------------------------------------------------------------- mensagens

def encode(obj: Mapping[str, Any], *, letter: str | None = None) -> bytes:
    """Objeto -> bytes do wire (JSON UTF-8 compacto, ``ensure_ascii=False``); > 1 MiB -> ``ProtocolError``."""
    try:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _proto(f"mensagem não serializável ({type(exc).__name__})", letter) from None
    if len(data) > MAX_MESSAGE:
        raise _proto(f"mensagem de {len(data)} bytes excede {MAX_MESSAGE}", letter)
    return data


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON não permitida: {name}")


def decode(data: bytes, *, letter: str | None = None) -> dict[str, Any]:
    """Bytes do wire -> objeto JSON; > 1 MiB, UTF-8/JSON inválido ou não-objeto -> ``ProtocolError``."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise _proto("mensagem não é bytes", letter)
    if len(data) > MAX_MESSAGE:
        raise _proto(f"mensagem de {len(data)} bytes excede {MAX_MESSAGE}", letter)
    try:
        obj = json.loads(bytes(data).decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise _proto("JSON inválido", letter) from None
    if not isinstance(obj, dict):
        raise _proto("a mensagem não é um objeto JSON", letter)
    return obj


def make_request(rid: int, op: str, args: Mapping[str, Any] | None = None, hwnd: int | None = None) -> dict[str, Any]:
    """Requisição do cliente; ``hwnd`` só vai nas ops com ``checks_hwnd`` (identidade da instância, C.6.5)."""
    msg: dict[str, Any] = {"id": rid, "op": op, "args": dict(args or {})}
    if hwnd is not None and OPS[op].checks_hwnd:
        msg["hwnd"] = hwnd
    return msg


def make_result(rid: int, result: Mapping[str, Any], ms: float | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"id": rid, "ok": True, "result": dict(result)}
    if ms is not None:
        msg["ms"] = round(ms, 3)
    return msg


def make_error(rid: int | None, exc: RumbaError) -> dict[str, Any]:
    return {"id": rid, "ok": False, "error": to_wire(exc)}


def make_event(event: str, reason: str) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError(f"evento desconhecido: {event!r}")
    return {"event": event, "reason": reason}


def is_event(msg: Mapping[str, Any]) -> bool:
    """Eventos não têm ``id`` (podem chegar a qualquer momento)."""
    return "event" in msg and "id" not in msg


def unwrap_reply(msg: Mapping[str, Any], expected_id: int, *, letter: str | None = None) -> dict[str, Any]:
    """Resposta do worker -> ``result`` (dict) ou levanta a exceção que ele mandou (``from_wire``).

    ``id`` diferente do esperado ou formato inválido -> ``ProtocolError``. Não aceita eventos (trate antes).
    """
    if not isinstance(msg, Mapping) or is_event(msg):
        raise _proto("esperava uma resposta e veio um evento ou algo inválido", letter)
    if msg.get("id") != expected_id or not _is_int(msg.get("id")):
        raise _proto(f"id inesperado na resposta ({msg.get('id')!r} != {expected_id})", letter)
    ok = msg.get("ok")
    if ok is True:
        result = msg.get("result")
        if not isinstance(result, Mapping):
            raise _proto("resposta sem result", letter)
        return dict(result)
    if ok is False:
        raise from_wire(msg.get("error") or {}, terminal=letter)
    raise _proto("resposta sem ok", letter)
