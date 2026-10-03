"""Estado da sessão HLLAPI do worker e as regras de ``delivery`` (D.5). Roda só em T-dll (a única thread da DLL).

Cada método corresponde a uma op de ``_protocol.OPS`` e devolve o ``result`` (dict de wire). Posições recebidas já
foram validadas pelo ``Dispatcher`` contra ``rows``/``cols`` da sessão conectada (A.8 e). O texto lido passa pela
máscara (D.6) antes do ``decode``; nada lido ou escrito vai para log (D.7).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..errors import FieldNotFound, HllapiError, ParameterError, ProtectedField, Truncated
from .mask import MaskState, hidden_in_span, is_hidden, is_protected
from ..keys import AIDS, Key, tokenize_mnemonic
from .. import _codec, _win32
from .hllapi import Hllapi
from . import rc as rcmod

import threading
import hashlib
import logging

logger = logging.getLogger(__name__)

NORESET = b"NORESET"
THIS = b"T "
DEFAULT_ROWS, DEFAULT_COLS = 24, 80


class JobCancelled(Exception):
    """O dono foi embora no meio de um ``send`` (C.6.4): o worker para antes do próximo chunk e não responde."""

    def __init__(self, chunks_sent: int) -> None:
        super().__init__(f"send cancelado após {chunks_sent} chunk(s)")
        self.chunks_sent = chunks_sent


def _units(chunk: bytes) -> int:
    """Quantas "unidades" (teclas ou caracteres) há no chunk — para a regra de ``delivery`` de rc 4/5."""
    try:
        tokens = tokenize_mnemonic(chunk.decode(_codec.ENCODING))
    except Exception:
        return 2
    return sum(1 if isinstance(t, Key) else len(t) for t in tokens)


def _is_aid_chunk(chunk: bytes) -> bool:
    try:
        tokens = tokenize_mnemonic(chunk.decode(_codec.ENCODING))
    except Exception:
        return False
    return len(tokens) == 1 and isinstance(tokens[0], Key) and tokens[0] in AIDS


def key_names(chunk: bytes) -> list[str]:
    """Nomes das teclas do chunk (para log: ``keys=[TAB,ENTER]``); nunca o texto."""
    try:
        tokens = tokenize_mnemonic(chunk.decode(_codec.ENCODING))
    except Exception:
        return ["?"]
    return [t.name for t in tokens if isinstance(t, Key)]


def text_units(chunk: bytes) -> int:
    try:
        tokens = tokenize_mnemonic(chunk.decode(_codec.ENCODING))
    except Exception:
        return 0
    return sum(len(t) for t in tokens if not isinstance(t, Key))


def text_after_key(chunks: list[bytes]) -> bool:
    """Há texto depois de alguma tecla (``"A000000@Tsenha@E"``)? Aí o tamanho do texto pode ser o de um campo
    oculto: o log omite ``text_units`` (D.7)."""
    seen_key = False
    for chunk in chunks:
        try:
            tokens = tokenize_mnemonic(chunk.decode(_codec.ENCODING))
        except Exception:
            return True
        for t in tokens:
            if isinstance(t, Key):
                seen_key = True
            elif t and seen_key:
                return True
    return False


class Engine:
    """Estado HLLAPI: ``letter, connected, rows, cols, hwnd, hwnd_pid, aid_seq`` (+ ``last_rc`` para o log)."""

    def __init__(self, hll: Hllapi, mask: MaskState, *, letter: str | None = None,
                 window_pid: Callable[[int], int] = _win32.window_thread_pid) -> None:
        self.hll = hll
        self.mask = mask
        self.bound_letter = letter               # o worker atende uma letra só (None = qualquer, testes)
        self._window_pid_fn = window_pid         # injetável: no modo --fake-dll os HWNDs são falsos
        self.letter: str | None = None
        self.connected = False
        self.rows = DEFAULT_ROWS
        self.cols = DEFAULT_COLS
        self.hwnd: int | None = None
        self.hwnd_pid: int | None = None
        self.aid_seq = 0
        self.last_rc: int | None = None
        self.last_sha: str | None = None
        self.last_secret = False                 # write num campo oculto vira secreto: o log omite os tamanhos (D.7)
        self.status_info: dict[str, Any] = {}
        self.last_disconnect: str | None = None  # "client" | "evict" | "resync" | "stop"
        self.lock = threading.RLock()            # leitura atômica do estado por T-conn (ping/hello)

    # ----------------------------------------------------------------- utilidades
    @property
    def size(self) -> int:
        return self.rows * self.cols

    def ctx(self, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"L": self.letter or self.bound_letter, "rows": self.rows, "cols": self.cols}
        out.update(extra)
        return out

    def pos(self, row: int, col: int) -> int:
        return _codec.to_pos(row, col, self.rows, self.cols)

    def rowcol(self, pos: int) -> list[int]:
        return [(pos - 1) // self.cols + 1, (pos - 1) % self.cols + 1]

    def _check(self, func: str, rc: int, **extra: Any) -> None:
        self.last_rc = rc
        rcmod.check(func, rc, self.ctx(**extra))

    def _note_sha(self, masked: bytes) -> None:
        """DEBUG do log (D.7): ``sha=<sha1[:6]>`` do que foi devolvido (já mascarado: nunca um hash de senha)."""
        self.last_sha = hashlib.sha1(masked).hexdigest()[:6] if logger.isEnabledFor(logging.DEBUG) else None

    def _keyboard_free(self) -> bool:
        rc, raw = self.hll.oia()
        if rc not in (0, 4, 5):
            return False
        return rc == 0 and not any(raw[88:93])

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {"connected": self.connected, "hwnd": self.hwnd, "hwnd_pid": self.hwnd_pid, "letter": self.letter}

    def screen_may_hide(self) -> bool:
        """A tela pode ter campo oculto (máscara desconhecida ou com spans ocultos)? O log do ``send`` sem ``secret``
        omite então o tamanho do texto: pode ser uma senha digitada num campo oculto (D.7)."""
        return self.mask.hidden is None or bool(self.mask.hidden)

    def _set_disconnected(self, why: str) -> None:
        with self.lock:
            self.connected = False
            self.last_disconnect = why
        self.mask.invalidate()

    # ----------------------------------------------------------------- sessões e conexão
    def sessions(self) -> dict[str, Any]:
        rc, entries = self.hll.query_sessions()
        self._check("WD_QuerySessions", rc)
        return {"sessions": [e.to_wire() for e in entries]}

    def _connected_result(self, rc: int, *, already: bool) -> dict[str, Any]:
        info = self.status_info
        return {"connected": True, "rc": rc, "already": already, "rows": self.rows, "cols": self.cols,
                "host_cp": info.get("host_cp"), "long_name": info.get("long_name"), "type": info.get("type"),
                "eab": info.get("eab"), "hwnd": self.hwnd, "hwnd_pid": self.hwnd_pid}

    def connect(self, letter: str) -> dict[str, Any]:
        if self.bound_letter is not None and letter != self.bound_letter:
            raise ParameterError.make(detalhe=f"este processo auxiliar atende só a letra {self.bound_letter} "
                                              f"(recebi {letter})")
        if self.connected and self.letter == letter:
            return self._connected_result(0, already=True)
        if self.connected:
            self.disconnect()
        rc = self.hll.connect(letter)
        self.last_rc = rc
        if rcmod.classify("WD_ConnectPS", rc) == "tolerated":     # rc 1: a sessão ainda está conectando
            return {"connected": False, "rc": rc, "already": False}
        rcmod.check("WD_ConnectPS", rc, self.ctx(L=letter))
        details: dict[str, Any] = {}
        prc = self.hll.set_param(NORESET)
        if prc in rcmod.GENERIC:                 # 9/12/36 (F.6): a sessão/o host caiu — não é "NORESET recusado"
            self.last_rc = prc
            self.hll.disconnect()
            rcmod.check("WD_SetSessionParamEx", prc, self.ctx(L=letter))
        if prc != 0:                             # D.5: demais rc ≠ 0 -> WARNING e details.noreset=false
            logger.warning("SetSessionParamEx(NORESET) rc=%d (tolerado)", prc)
            details["noreset"] = False
        try:
            src, status = self.hll.query_status(letter)
            rcmod.check("WD_QuerySessionStatus", src, self.ctx(L=letter))
            assert status is not None
            hwnd = self.hll.hwnd()
        except BaseException:
            self.hll.disconnect()
            raise
        with self.lock:
            self.letter = letter
            self.rows, self.cols = status.rows or DEFAULT_ROWS, status.cols or DEFAULT_COLS
            self.status_info = status.to_wire()
            self.hwnd = hwnd
            self.hwnd_pid = (self._window_pid(hwnd) or None) if hwnd else None
            self.connected = True
            self.last_disconnect = None
        self.mask.invalidate(self.size)
        result = self._connected_result(rc, already=False)
        if details:
            result["details"] = details
        return result

    def _window_pid(self, hwnd: int) -> int:
        try:
            return int(self._window_pid_fn(hwnd))
        except Exception:
            return 0

    def disconnect(self) -> dict[str, Any]:
        rc = self.hll.disconnect() if self.connected else None
        self._set_disconnected("client")         # antes do _check: rc genérico (9/12/36) também encerra a conexão
        if rc is not None:
            self._check("WD_DisconnectPS", rc)
        return {}

    def status(self) -> dict[str, Any]:
        rc, status = self.hll.query_status(self.letter or "")
        self._check("WD_QuerySessionStatus", rc)
        assert status is not None
        return status.to_wire()

    def evict(self) -> dict[str, Any]:
        was = self.connected
        if was:
            rc = self.hll.disconnect()
            self.last_rc = rc
            if rcmod.classify("WD_DisconnectPS", rc) == "error":
                logger.warning("evict: DisconnectPS rc=%d", rc)
        self._set_disconnected("evict")
        return {"was_connected": was}

    def stop_calls(self) -> None:
        """``DisconnectPS`` (se conectado) + ``ResetSystem`` — antes de encerrar o processo."""
        if self.connected:
            rc = self.hll.disconnect()
            logger.info("stop: DisconnectPS rc=%d", rc)
        rc = self.hll.reset_system()
        logger.info("stop: ResetSystem rc=%d", rc)
        self._set_disconnected("stop")

    def resync(self) -> None:
        """``QuerySessions`` + ``QuerySessionStatus`` + ``GetSessionHWND``; qualquer falha => ``connected=False``."""
        if not self.connected or not self.letter:
            return
        try:
            rc, entries = self.hll.query_sessions()
            ok = rc == 0 and any(e.letter == self.letter for e in entries)
            if ok:
                rc, status = self.hll.query_status(self.letter)
                ok = rc == 0 and status is not None
            if ok:
                hwnd = self.hll.hwnd()
                with self.lock:
                    if hwnd != self.hwnd:
                        logger.warning("resync: HWND mudou (%s -> %s)", self.hwnd, hwnd)
                        self.hwnd = hwnd
                        self.hwnd_pid = (self._window_pid(hwnd) or None) if hwnd else None
                        self.mask.invalidate()
        except Exception as exc:                 # nunca str(exc): pode citar dados (C21c)
            logger.warning("resync falhou (%s)", type(exc).__name__)
            ok = False
        if not ok:
            self._set_disconnected("resync")

    # ----------------------------------------------------------------- leitura
    def _copy_ps(self) -> tuple[bool, bytes]:
        rc, raw = self.hll.copy_ps(self.size)
        self._check("WD_CopyPS", rc)
        return rc in (4, 5), raw

    def snapshot(self, fields: bool) -> dict[str, Any]:
        busy, raw = self._copy_ps()
        digest = hashlib.sha1(raw).digest()
        seen: list[tuple[int, int]] = []         # spans ocultos de todas as enumerações desta chamada
        for attempt in range(2):                 # a tela mudou durante a enumeração: enumera de novo (1x)
            if not self.mask.refresh(self.hll, aid_seq=self.aid_seq, keyboard_free=not busy, ps_hash=digest):
                break
            if self.mask.hidden is not None:
                seen.extend(self.mask.hidden)
            busy, again = self._copy_ps()
            changed = again != raw
            raw, digest = again, hashlib.sha1(again).digest()
            if not changed:
                break
            self.mask.valid = False
            if attempt == 1:                     # instável nas duas: máscara desconhecida -> conferência campo a campo
                self.mask.mark_unknown(seen)
        self.mask.note_snapshot(digest)
        rc, cpos = self.hll.cursor()
        self._check("WD_QueryCursorLocation", rc)
        orc, oia = self.hll.oia()
        self._check("WD_CopyOIA", orc)
        self.last_rc = 4 if busy else 0
        cursor = self.rowcol(cpos) if 1 <= cpos <= self.size else [1, 1]
        extra = self._unknown_mask_spans(1, self.size, 1, 1)
        masked = self.mask.apply(raw, 1, extra)
        self._note_sha(masked)
        result: dict[str, Any] = {"text": _codec.decode(masked), "cursor": cursor,
                                  "oia": oia.hex(), "busy": busy, "hidden_unknown": self.mask.hidden_unknown}
        if fields:
            specs = self.mask.fields if self.mask.valid else None     # máscara desconhecida: sem campos
            if specs is not None:
                result["fields"] = [{"start": self.rowcol(start), "length": length, "attr": attr}
                                    for start, length, attr in specs]
        return result

    def read(self, row: int, col: int, length: int) -> dict[str, Any]:
        pos = self.pos(row, col)
        _codec.check_span(pos, length, self.rows, self.cols)
        rc, raw = self.hll.copy_ps_to_string(pos, length)
        self._check("WD_CopyPSToString", rc, r=row, c=col)
        if self.mask.refresh(self.hll, aid_seq=self.aid_seq, keyboard_free=rc not in (4, 5)):
            rc, raw = self.hll.copy_ps_to_string(pos, length)
            self._check("WD_CopyPSToString", rc, r=row, c=col)
        extra = self._unknown_mask_spans(pos, length, row, col)
        masked = self.mask.apply(raw, pos, extra)
        self._note_sha(masked)
        # nul_at/nul_tail sobre o bruto JÁ mascarado: NULs dentro de um campo oculto revelariam o tamanho da senha
        return {"text": _codec.decode(masked), "busy": rc in (4, 5), "nul_at": masked.find(b"\0"),
                "nul_tail": len(masked) - len(masked.rstrip(b"\0"))}

    def _unknown_mask_spans(self, pos: int, length: int, row: int, col: int) -> list[tuple[int, int]]:
        """Máscara desconhecida (a enumeração falhou): confere campo a campo só a área pedida (D.6).

        Devolve os spans ocultos achados (``[]`` com a máscara conhecida); se nem a conferência der, recusa a leitura.
        """
        if not self.mask.hidden_unknown:
            return []
        func, frc, extra = hidden_in_span(self.hll, pos, length, self.size)
        if frc != 0:
            self.last_rc = frc
            self._refuse_unmasked(func, frc, row, col)
        return extra

    def _refuse_unmasked(self, func: str, rc: int, row: int, col: int) -> None:
        """Sem como saber se a área tem campo oculto: recusa a leitura (o bruto nunca atravessa o pipe, D.6)."""
        logger.warning("leitura recusada: máscara desconhecida e %s rc=%d na conferência dos campos", func, rc)
        if rc == 1 or rc in rcmod.GENERIC:       # sessão desconectada/caída: essa é a causa, não a máscara
            exc = rcmod.error_for(func, rc, self.ctx(r=row, c=col))
            if exc is not None:
                raise exc
        raise HllapiError(f"Não consegui conferir se a área ({row},{col}) tem campo oculto ({func} rc {rc}); a "
                          f"leitura foi recusada para não expor conteúdo oculto.", rc=rc, function=func,
                          terminal=self.letter)

    def read_field(self, row: int, col: int) -> dict[str, Any]:
        pos = self.pos(row, col)
        rc, start = self.hll.find_field_pos(pos, THIS)
        if rc == 24:
            self.last_rc = rc
            raise FieldNotFound.make(r=row, c=col, rc=rc, function="WD_FindFieldPosition", terminal=self.letter)
        self._check("WD_FindFieldPosition", rc, r=row, c=col)
        zero = rc == 28
        lrc, length = self.hll.find_field_len(pos, THIS)
        if lrc == 24:
            raise FieldNotFound.make(r=row, c=col, rc=lrc, function="WD_FindFieldLength", terminal=self.letter)
        self._check("WD_FindFieldLength", lrc, r=row, c=col)
        if zero or lrc == 28:
            length = 0
        if zero or not 1 <= start <= self.size:
            start = pos
        arc, attr = self.hll.field_attr(start)
        self._check("WD_QueryFieldAttribute", arc, r=row, c=col)
        self.mask.refresh(self.hll, aid_seq=self.aid_seq, keyboard_free=self._keyboard_free)
        raw = b""
        if length > 0:
            crc, raw = self.hll.copy_field(start, length)
            self._check("WD_CopyFieldToString", crc, r=row, c=col)
        if raw and is_hidden(attr):
            raw = b" " * len(raw)                # campo oculto -> espaços
        else:
            raw = self.mask.apply(raw, start)
        self.last_rc = 0
        self._note_sha(raw)
        return {"text": _codec.decode(raw), "start": self.rowcol(start), "length": length, "attr": attr,
                "nul_tail": len(raw) - len(raw.rstrip(b"\0"))}

    # ----------------------------------------------------------------- entrada
    def _room(self, row: int, col: int, pos: int) -> tuple[int, bool]:
        """(bytes que cabem a partir de ``pos``, campo oculto?) — valida ANTES de copiar (C9)."""
        size = self.size
        rc, attr = self.hll.field_attr(pos)
        if rc == 24:                             # PS não formatado: o limite é o fim da tela
            return size - pos + 1, False
        self._check("WD_QueryFieldAttribute", rc, r=row, c=col)
        hidden = is_hidden(attr)
        if hidden:                               # já secreto: se algo abaixo falhar, o log também omite o tamanho
            self.last_secret = True
        if is_protected(attr):
            raise ProtectedField.make(r=row, c=col, terminal=self.letter)
        prc, start = self.hll.find_field_pos(pos, THIS)
        if prc == 24:
            return size - pos + 1, hidden
        self._check("WD_FindFieldPosition", prc, r=row, c=col)
        lrc, length = self.hll.find_field_len(pos, THIS)
        self._check("WD_FindFieldLength", lrc, r=row, c=col)
        if prc == 28 or lrc == 28:
            return 0, hidden
        attr_pos = start - 1 if start > 1 else size
        if pos == attr_pos:                      # o byte de atributo não é dado
            raise ProtectedField.make(r=row, c=col, terminal=self.letter)
        end = start + length - 1                 # pode passar do fim da tela (campo que dá a volta)
        here = pos if pos >= start else pos + size
        return max(0, end - here + 1), hidden

    def write(self, row: int, col: int, data: bytes, secret: bool) -> dict[str, Any]:
        pos = self.pos(row, col)
        self.last_secret = secret
        room, hidden = self._room(row, col, pos)
        secret = secret or hidden
        self.last_secret = secret
        if len(data) > room:
            if secret:
                raise Truncated.make("secret", r=row, c=col, delivery="none", terminal=self.letter)
            raise Truncated.make(n=len(data), m=room, r=row, c=col, delivery="none", terminal=self.letter)
        rc = self.hll.copy_string_to_ps(pos, data)
        self.last_rc = rc
        if rc in (0, 6):
            if secret:
                self.mask.add_secret(pos, len(data))
            self.mask.own_mutation()
        self._check("WD_CopyStringToPS", rc, r=row, c=col, n=len(data), m=room, secret=secret)
        return {}

    def send(self, chunks: list[bytes], secret: bool, cancel: threading.Event) -> dict[str, Any]:
        n = len(chunks)
        last_is_aid = bool(chunks) and _is_aid_chunk(chunks[-1])
        for i, chunk in enumerate(chunks):
            if cancel.is_set():
                if i:
                    self.mask.own_mutation()
                raise JobCancelled(i)
            rc = self.hll.send_key(chunk)
            self.last_rc = rc
            if rc == 0:
                continue
            if rc in (2, 20):
                delivery = "none" if i == 0 else "partial"
            elif rc in (4, 5):
                delivery = "none" if i == 0 and _units(chunk) == 1 else "partial"
            else:
                delivery = "unknown"
            if delivery != "none":
                self.mask.own_mutation()
                if last_is_aid and i == n - 1 and delivery == "unknown":
                    self._aid_sent()
            names = "" if secret else ",".join(key_names(chunk))
            exc = rcmod.error_for("WD_SendKey", rc, self.ctx(delivery=delivery, details={"chunks_sent": i},
                                                             x=names or "?"))
            if exc is not None:
                raise exc
        if last_is_aid:
            self._aid_sent()
        else:
            self.mask.own_mutation()
        orc, oia = self.hll.oia()
        if rcmod.classify("WD_CopyOIA", orc) == "error":
            rcmod.check("WD_CopyOIA", orc, self.ctx(delivery="unknown", details={"chunks_sent": n}))
        return {"chunks_sent": n, "oia_after": oia.hex()}

    def _aid_sent(self) -> None:
        self.aid_seq += 1
        self.mask.on_aid()

    def set_cursor(self, row: int, col: int) -> dict[str, Any]:
        pos = self.pos(row, col)
        rc = self.hll.set_cursor(pos)
        self._check("WD_SetCursor", rc, r=row, c=col)
        return {}
