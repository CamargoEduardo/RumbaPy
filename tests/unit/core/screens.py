"""Fábrica de telas para os testes do núcleo puro (P-A): monta ``Screen`` pelo caminho de wire (``from_wire``)."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rumbapy.screen import Screen

# OIA visto ao vivo (BRAZIL_D, 2026-10-03): formato 1, G1=0x10 (LU-LU), G8 zerado => teclado livre
LIVE_IMAGE = bytes([0x10] * 20 + [0xC1, 0x05, 0x00, 0x7F] * 15)
LIVE_OIA = bytes([1]) + LIVE_IMAGE + bytes([0x10, 0x00, 0x00, 0x00, 0x40, 0x40] + [0x00] * 16)
LOCK = b"\x00\x20\x00\x00\x00"


def oia_hex(*, online: bool = True, locked: bool = False) -> str:
    raw = bytearray(LIVE_OIA)
    raw[81] = 0x10 if online else 0x00
    raw[88:93] = LOCK if locked else b"\0" * 5
    return bytes(raw).hex()


def make_text(lines: Mapping[int, str] | Iterable[str] = (), *, rows: int = 24, cols: int = 80,
              at: Mapping[tuple[int, int], str] | None = None) -> str:
    """Texto ``rows*cols``: ``lines`` = ``{linha: texto}`` (ou lista a partir da linha 1); ``at`` = texto em (r, c)."""
    grid = [" " * cols for _ in range(rows)]
    items = lines.items() if isinstance(lines, Mapping) else enumerate(lines, 1)
    for row, value in items:
        grid[row - 1] = value[:cols].ljust(cols)
    text = list("".join(grid))
    for (row, col), value in (at or {}).items():
        start = (row - 1) * cols + col - 1
        text[start:start + len(value)] = value
    return "".join(text)[:rows * cols]


def make_screen(lines: Mapping[int, str] | Iterable[str] = (), *, rows: int = 24, cols: int = 80,
                at: Mapping[tuple[int, int], str] | None = None, cursor: tuple[int, int] = (1, 1),
                online: bool = True, locked: bool = False, busy: bool = False, hidden_unknown: bool = False,
                fields: list[dict[str, Any]] | None = None, terminal: str = "D", taken_at: float = 1_700_000_000.0,
                text: str | None = None) -> Screen:
    result: dict[str, Any] = {
        "text": text if text is not None else make_text(lines, rows=rows, cols=cols, at=at),
        "cursor": list(cursor), "oia": oia_hex(online=online, locked=locked), "busy": busy,
        "hidden_unknown": hidden_unknown, "taken_at": taken_at,
    }
    if fields is not None:
        result["fields"] = fields
    return Screen.from_wire(result, terminal, rows=rows, cols=cols)
