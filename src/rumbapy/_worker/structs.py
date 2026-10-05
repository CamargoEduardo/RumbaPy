"""Estruturas da ``ehlapi32.Dll`` no formato 32-bit (F.3; ``HllapiStandard=false``; validadas ao vivo). Puro.

* ``QuerySessions``: 16 B por sessão — ``letter = e[0:1]``, ``long_name = e[4:12]``, ``type = e[12:13]``,
  ``ps_size = <H @14`` (ao vivo ``44 20x11 48 20 80 07`` -> D, ``H``, 1920).
* ``QuerySessionStatus``: 20 B — ``letter = b[0]``, ``long_name = b[4:12]``, ``type = b[12]``, ``eab = b[13] & 0x80``,
  ``rows = <H @14``, ``cols = <H @16``, ``host_cp = <H @18`` (ao vivo 0; não confiável).
"""
# ruff: noqa: I001
from __future__ import annotations

from .._types import SessionEntry, SessionStatus

import struct

SESSION_ENTRY_SIZE = 16
SESSION_STATUS_SIZE = 20
MAX_SESSIONS = 26
ENCODING = "cp1252"


def _text(raw: bytes) -> str:
    return raw.decode(ENCODING, "replace").strip(" \0")


def _char(raw: bytes) -> str:
    return raw.decode(ENCODING, "replace").strip("\0")


def parse_session_entry(entry: bytes) -> SessionEntry:
    """Uma entrada de 16 bytes de ``WD_QuerySessions``."""
    if len(entry) < SESSION_ENTRY_SIZE:
        raise ValueError(f"entrada de QuerySessions com {len(entry)} bytes (esperado {SESSION_ENTRY_SIZE})")
    (ps_size,) = struct.unpack_from("<H", entry, 14)
    return SessionEntry(letter=_char(entry[0:1]), long_name=_text(entry[4:12]), type=_char(entry[12:13]),
                        ps_size=ps_size)


def parse_sessions(buf: bytes, count: int) -> list[SessionEntry]:
    """``count`` entradas de 16 bytes (``count`` limitado ao que cabe no buffer e a 26)."""
    count = max(0, min(int(count), MAX_SESSIONS, len(buf) // SESSION_ENTRY_SIZE))
    return [parse_session_entry(buf[i * SESSION_ENTRY_SIZE:(i + 1) * SESSION_ENTRY_SIZE]) for i in range(count)]


def parse_status(buf: bytes) -> SessionStatus:
    """Os 20 bytes de ``WD_QuerySessionStatus``."""
    if len(buf) < SESSION_STATUS_SIZE:
        raise ValueError(f"QuerySessionStatus com {len(buf)} bytes (esperado {SESSION_STATUS_SIZE})")
    rows, cols, host_cp = struct.unpack_from("<HHH", buf, 14)
    return SessionStatus(letter=_char(buf[0:1]), long_name=_text(buf[4:12]), type=_char(buf[12:13]),
                         eab=bool(buf[13] & 0x80), rows=rows, cols=cols, host_cp=host_cp)
