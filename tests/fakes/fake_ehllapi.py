"""Simulador da ehlapi32.Dll do RUMBA no nível das funções WD_* (para testes sem RUMBA e sem mainframe).

O objeto ``FakeEhllapi`` expõe atributos com os mesmos nomes das exportações reais (``WD_ConnectPS``,
``WD_CopyPSToString``, ...). Cada um aceita os MESMOS argumentos que a função ctypes real receberia:
inteiros, ``bytes``, buffers de ``ctypes.create_string_buffer`` e ponteiros ``ctypes.byref(c_ushort())``.
Assim o código de binding pode receber ``FakeEhllapi()`` no lugar de ``ctypes.WinDLL(...)`` sem mudanças.

Semântica reproduzida (ver docs/RUMBA_EHLLAPI.md):
* conexão única por "processo" (o objeto), rc 1 enquanto a sessão não terminou de conectar;
* PS linear 1-based, campos com byte de atributo (exibido como espaço), proteção, campo oculto, MDT;
* ``SendKey`` com mnemônicos ``@x``, rc 2 se >= 256 bytes, tudo após a 1ª tecla AID é descartado;
* teclado travado (X SYSTEM) depois de uma AID até o "host" responder; ``Wait`` devolve 0/4/5;
* ``CopyPSToString``/``CopyStringToPS`` com rc 2/4/5/6/7; ``SearchPS`` rc 24; OIA de 103 bytes;
* estruturas de 16 B (QuerySessions) e 20 B (QuerySessionStatus) no formato 32-bit;
* injeção de travamento por função (``hang_on``) para testar watchdogs.

O "host" é programável: ``FakeSession.on_aid`` recebe ``(sessao, aid, campos_modificados)`` e pode trocar a tela.

Extensões do P-0 (aditivas e DESLIGADAS por padrão; desligadas, o comportamento é idêntico ao anterior):
* ``FakeSession.response_delay`` (s): a AID trava o teclado e o ``on_aid`` só roda depois do atraso (``threading.Timer``);
  até lá a tela antiga continua e o teclado só é liberado depois que a tela nova estiver no lugar;
* ``FakeSession.lock_visible_after`` (s): durante esse tempo após a AID o bloqueio não é observável (OIA sem G8, rc 0,
  entrada aceita) — simula o bloqueio que o cliente não vê;
* ``FakeEhllapi.rc_override``: ``{"WD_CopyPS": [4, 9]}`` = fila de rc forçados por função (a chamada é registrada em
  ``calls`` como ``(nome, ("rc_override", rc))`` e não tem nenhum outro efeito);
* ``WD_SetSessionParamEx`` remove a opção oposta (``NORESET`` tira ``AUTORESET``, ``ATTRB``/``NOATTRB``,
  ``SRCHALL``/``SRCHFROM``, ``NWAIT``/``LWAIT``/``TWAIT``…);
* ``@H`` (``Key.HELP``) é aceito como tecla local sem efeito (antes: rc 2, enganoso para quem testa ``Key.HELP``).
As chamadas são serializadas por uma trava do simulador (o host atrasado roda em outra thread); ``hang_on`` solta a
trava enquanto a chamada está "presa".
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

ENCODING = "cp1252"

# Mnemônicos (tabela do WDApiSvc.ocx / manual WD_API)
AID_KEYS = {
    "E": "ENTER", "C": "CLEAR", "1": "PF1", "2": "PF2", "3": "PF3", "4": "PF4", "5": "PF5", "6": "PF6",
    "7": "PF7", "8": "PF8", "9": "PF9", "a": "PF10", "b": "PF11", "c": "PF12", "d": "PF13", "e": "PF14",
    "f": "PF15", "g": "PF16", "h": "PF17", "i": "PF18", "j": "PF19", "k": "PF20", "l": "PF21", "m": "PF22",
    "n": "PF23", "o": "PF24", "x": "PA1", "y": "PA2", "z": "PA3", "u": "PAGEUP", "v": "PAGEDOWN",
}
AID_A_KEYS = {"H": "SYSREQ", "Q": "ATTN"}
LOCAL_KEYS = {
    "T": "TAB", "B": "BACKTAB", "0": "HOME", "N": "NEWLINE", "U": "UP", "V": "DOWN", "L": "LEFT", "Z": "RIGHT",
    "<": "BACKSPACE", "D": "DELETE", "I": "INSERT", "F": "ERASEEOF", "R": "RESET", "q": "END", "P": "PRINT",
    "H": "HELP",  # extensão P-0: Key.HELP (local no 3270; sem efeito aqui)
}
LOCAL_A_KEYS = {"F": "ERASEINPUT", "E": "FIELDEXIT", "J": "CURSORSELECT", "T": "PRINTPS", "C": "TEST", "R": "DEVCANCEL"}

# Opções mutuamente exclusivas de WD_SetSessionParamEx (RUMBA_EHLLAPI.md §6): ligar uma desliga as outras do grupo.
EXCLUSIVE_PARAMS = (
    frozenset({"ATTRB", "NOATTRB"}), frozenset({"EAB", "NOEAB"}), frozenset({"XLATE", "NOXLATE"}),
    frozenset({"NEWRET", "OLDRET"}), frozenset({"NWAIT", "LWAIT", "TWAIT"}), frozenset({"SRCHALL", "SRCHFROM"}),
    frozenset({"SRCHFRWD", "SRCHBKWD"}), frozenset({"FPAUSE", "IPAUSE"}), frozenset({"AUTORESET", "NORESET"}),
    frozenset({"CONPHYS", "CONLOG"}),
    frozenset({"WRITE_SUPER", "WRITE_WRITE", "WRITE_READ", "WRITE_NONE", "SUPER_WRITE"}),
    frozenset({"QUIET", "NOQUIET"}), frozenset({"CFGSIZE", "NOCFGSIZE"}),
)


# --------------------------------------------------------------------------------------------- helpers ctypes

def _in_bytes(arg) -> bytes:
    """Converte um argumento de entrada char* (bytes, buffer ctypes ou c_char_p) em bytes até o NUL."""
    if arg is None:
        return b""
    if isinstance(arg, (bytes, bytearray)):
        data = bytes(arg)
    elif isinstance(arg, ctypes.Array):
        data = bytes(arg)
    elif isinstance(arg, ctypes.c_char_p):
        data = arg.value or b""
    else:
        raise TypeError(f"argumento char* inesperado: {type(arg)!r}")
    nul = data.find(b"\0")
    return data if nul < 0 else data[:nul]


def _in_raw(arg, length: int) -> bytes:
    """Lê exatamente ``length`` bytes de um char* de entrada (CopyStringToPS usa o tamanho, não o NUL)."""
    if isinstance(arg, (bytes, bytearray)):
        return bytes(arg)[:length]
    if isinstance(arg, ctypes.Array):
        return bytes(arg)[:length]
    if isinstance(arg, ctypes.c_char_p):
        return (arg.value or b"")[:length]
    raise TypeError(f"argumento char* inesperado: {type(arg)!r}")


def _out_write(arg, data: bytes) -> None:
    """Escreve em um buffer de saída char* (create_string_buffer)."""
    if not isinstance(arg, ctypes.Array):
        raise TypeError("buffer de saída deve ser ctypes.create_string_buffer")
    n = min(len(data), ctypes.sizeof(arg))
    ctypes.memmove(arg, data, n)


def _out_word(arg, value: int) -> None:
    """Escreve num WORD* de saída (byref(c_ushort), pointer(c_ushort) ou c_ushort)."""
    obj = getattr(arg, "_obj", None)  # CArgObject de ctypes.byref
    if obj is None and hasattr(arg, "contents"):
        obj = arg.contents
    if obj is None:
        obj = arg
    obj.value = value & 0xFFFF


def _in_word_ptr(arg) -> int:
    obj = getattr(arg, "_obj", None)
    if obj is None and hasattr(arg, "contents"):
        obj = arg.contents
    if obj is None:
        obj = arg
    return int(obj.value)


def _int(arg) -> int:
    if hasattr(arg, "value"):
        return int(arg.value or 0)
    return int(arg or 0)


# --------------------------------------------------------------------------------------------- modelo de tela

@dataclass
class FakeField:
    """Campo 3270: o byte de atributo fica em ``attr_pos`` (1-based) e os dados vêm logo depois."""
    attr_pos: int
    length: int
    protected: bool = False
    numeric: bool = False
    hidden: bool = False
    mdt: bool = False
    name: str = ""

    @property
    def start(self) -> int:
        return self.attr_pos + 1

    @property
    def end(self) -> int:
        return self.attr_pos + self.length

    def attribute_byte(self) -> int:
        value = 0xC0
        if self.protected:
            value |= 0x20
        if self.numeric:
            value |= 0x10
        if self.hidden:
            value |= 0x0C
        if self.mdt:
            value |= 0x01
        return value


@dataclass
class FakeScreen:
    """Descrição declarativa de uma tela: linhas de texto + campos (linha, coluna do 1º caractere, tamanho)."""
    lines: list
    fields: list = field(default_factory=list)  # dicts: row, col, length, protected, numeric, hidden, name, text
    cursor: Optional[tuple] = None  # (row, col); padrão = 1º campo desprotegido
    name: str = ""


class FakeSession:
    def __init__(self, letter: str, rows: int = 24, cols: int = 80, hwnd: int = 0x1000, pid: int = 4242,
                 connect_delay: float = 0.0, host_delay: float = 0.0):
        self.letter = letter
        self.rows, self.cols = rows, cols
        self.size = rows * cols
        self.buffer = bytearray(b" " * self.size)
        self.fields: list = []
        self.cursor = 1
        self.locked_until = 0.0  # X SYSTEM até este instante (monotonic)
        self.inhibited = False   # teclado travado por erro de operador (precisa RESET)
        self.registered_at = time.monotonic()
        self.connect_delay = connect_delay  # ConnectPS devolve 1 até passar esse tempo
        self.host_delay = host_delay        # tempo de X SYSTEM depois de uma AID
        self.hwnd, self.pid = hwnd, pid
        self.on_aid: Optional[Callable] = None  # host programável
        self.screen_name = ""
        self.aid_log: list = []  # (aid, {nome_campo: texto})
        # extensões do P-0 (desligadas com 0)
        self.response_delay = 0.0      # s até o "host" responder a uma AID (on_aid via threading.Timer)
        self.lock_visible_after = 0.0  # s após a AID em que o bloqueio ainda não é observável
        self.last_aid_at: Optional[float] = None
        self.pending_responses = 0     # respostas atrasadas ainda não entregues

    # ----------------------------------------------------------------- geometria
    def pos(self, row: int, col: int) -> int:
        return (row - 1) * self.cols + col

    def rowcol(self, pos: int) -> tuple:
        return (pos - 1) // self.cols + 1, (pos - 1) % self.cols + 1

    # ----------------------------------------------------------------- telas
    def load(self, screen: FakeScreen) -> None:
        self.buffer = bytearray(b" " * self.size)
        for r, line in enumerate(screen.lines[: self.rows]):
            data = line.encode(ENCODING, "replace")[: self.cols]
            start = r * self.cols
            self.buffer[start:start + len(data)] = data
        self.fields = []
        for spec in screen.fields:
            first = self.pos(spec["row"], spec["col"])
            fld = FakeField(attr_pos=first - 1, length=spec["length"], protected=spec.get("protected", False),
                            numeric=spec.get("numeric", False), hidden=spec.get("hidden", False),
                            name=spec.get("name", ""))
            text = spec.get("text", "").encode(ENCODING, "replace")[: fld.length].ljust(fld.length, b" ")
            self.buffer[fld.start - 1:fld.end] = text
            if fld.attr_pos >= 1:
                self.buffer[fld.attr_pos - 1] = 0x20  # atributo aparece como espaço com NOATTRB
            self.fields.append(fld)
        self.fields.sort(key=lambda f: f.attr_pos)
        if screen.cursor:
            self.cursor = self.pos(*screen.cursor)
        else:
            unprot = [f for f in self.fields if not f.protected]
            self.cursor = unprot[0].start if unprot else 1
        self.screen_name = screen.name

    def text(self) -> str:
        return self.buffer.decode(ENCODING)

    def line(self, row: int) -> str:
        start = (row - 1) * self.cols
        return self.buffer[start:start + self.cols].decode(ENCODING)

    def field_at(self, pos: int) -> Optional[FakeField]:
        """Campo que contém ``pos`` (o atributo pertence ao próprio campo)."""
        if not self.fields:
            return None
        owner = None
        for fld in self.fields:
            if fld.attr_pos <= pos:
                owner = fld
        if owner is None:  # posição antes do 1º atributo pertence ao último campo (envolve a tela)
            owner = self.fields[-1]
        if owner.attr_pos <= pos <= owner.end or owner is self.fields[-1]:
            return owner
        return owner

    def is_data_pos(self, pos: int) -> Optional[FakeField]:
        for fld in self.fields:
            if fld.start <= pos <= fld.end:
                return fld
        return None

    def field_text(self, fld: FakeField) -> str:
        return self.buffer[fld.start - 1:fld.end].decode(ENCODING)

    def modified_fields(self) -> dict:
        return {(f.name or f.start): self.field_text(f).rstrip() for f in self.fields if f.mdt and not f.protected}

    # ----------------------------------------------------------------- estado do teclado
    @property
    def busy(self) -> bool:
        now = time.monotonic()
        if now >= self.locked_until:
            return False
        if self.lock_visible_after > 0 and self.last_aid_at is not None and now < self.last_aid_at + self.lock_visible_after:
            return False  # bloqueio ainda não observável (extensão lock_visible_after)
        return True

    def lock_for(self, seconds: float) -> None:
        self.locked_until = time.monotonic() + seconds


class _FakeFunction:
    """Função "exportada": chamável e aceita ``argtypes``/``restype``/``errcheck`` como uma função ctypes."""

    def __init__(self, fn, name, owner=None):
        self._fn = fn
        self._owner = owner
        self.__name__ = name
        self.argtypes = None
        self.restype = None
        self.errcheck = None

    def __call__(self, *args):
        owner = self._owner
        if owner is None:
            return self._fn(*args)
        with owner._lock:
            forced = owner._forced_rc(self.__name__)
            if forced is not None:
                owner.calls.append((self.__name__, ("rc_override", forced)))
                return forced
            return self._fn(*args)


class FakeEhllapi:
    """Substituto da ``ctypes.WinDLL(ehlapi32.Dll)`` para testes."""

    def __init__(self):
        self._lock = threading.RLock()  # serializa as chamadas e o host atrasado (response_delay)
        self.rc_override: dict = {}     # {"WD_CopyPS": [4, 9]}: rc forçados, em fila, por função
        for name in dir(type(self)):
            if name.startswith("WD_") or name == "MultiThreadConnectionOn":
                setattr(self, name, _FakeFunction(getattr(self, name), name, self))
        self.sessions: dict = {}
        self.connected: Optional[str] = None
        self.calls: list = []
        self.hang_on: set = set()
        self.hang_event = threading.Event()  # set() libera chamadas "travadas"
        self.params: set = {"NOATTRB", "NOEAB", "TWAIT", "SRCHALL", "SRCHFRWD", "AUTORESET", "CONLOG", "FPAUSE"}
        self.reset_count = 0
        self.wait_timeout = 0.2  # o TWAIT simulado (o real é 60 s)
        self.multithread = False

    # ----------------------------------------------------------------- utilidades de teste
    def add_session(self, letter: str, **kw) -> FakeSession:
        s = FakeSession(letter, **kw)
        self.sessions[letter] = s
        return s

    def remove_session(self, letter: str) -> None:
        self.sessions.pop(letter, None)
        if self.connected == letter:
            self.connected = None

    def _log(self, name, *args):
        self.calls.append((name, args))
        if name in self.hang_on:
            self._hang()

    def _hang(self) -> None:
        """Espera ``hang_event`` sem segurar a trava do simulador (outras threads e o host atrasado seguem)."""
        held = 0
        while True:
            try:
                self._lock.release()
            except RuntimeError:  # a thread não tem (mais) a trava
                break
            held += 1
        try:
            self.hang_event.wait()
        finally:
            for _ in range(held):
                self._lock.acquire()

    def _forced_rc(self, name: str) -> Optional[int]:
        """Próximo rc forçado de ``name`` (aceita ``"WD_CopyPS"`` ou ``"CopyPS"``), ou ``None``."""
        if not self.rc_override:
            return None
        for key in (name, name[3:] if name.startswith("WD_") else name):
            queue = self.rc_override.get(key)
            if queue:
                return queue.pop(0)
        return None

    def _cur(self) -> Optional[FakeSession]:
        if self.connected is None:
            return None
        return self.sessions.get(self.connected)

    # ----------------------------------------------------------------- fn 10/22/20/1/2/21
    def WD_QuerySessions(self, h, p_count, buf):
        self._log("WD_QuerySessions")
        maximum = _in_word_ptr(p_count)
        entries = b""
        letters = sorted(self.sessions)[:maximum]
        for letter in letters:
            s = self.sessions[letter]
            entries += letter.encode() + b"   " + b" " * 8 + b"H" + b" " + s.size.to_bytes(2, "little")
        if entries:
            _out_write(buf, entries)
        _out_word(p_count, len(letters))
        return 0

    def WD_QuerySessionStatus(self, h, buf):
        self._log("WD_QuerySessionStatus")
        letter = bytes(buf)[:1].decode("latin-1").upper()
        s = self.sessions.get(letter) or (self._cur() if letter == " " else None)
        if s is None:
            return 1
        data = (s.letter.encode() + b"   " + s.letter.encode() + b"\0" + b" " * 6 + b"D" + b"\0"
                + s.rows.to_bytes(2, "little") + s.cols.to_bytes(2, "little") + b"\0\0")
        _out_write(buf, data)
        return 0

    def WD_QuerySystem(self, h, buf):
        self._log("WD_QuerySystem")
        _out_write(buf, b"410090422000U00000\x00" + b"0" * 16 + b"\x00")
        return 0

    def WD_ConnectPS(self, h, short):
        self._log("WD_ConnectPS", short)
        letter = _in_bytes(short)[:1].decode("latin-1").upper()
        s = self.sessions.get(letter)
        if s is None:
            return 1
        if time.monotonic() - s.registered_at < s.connect_delay:
            return 1
        self.connected = letter
        if s.inhibited:
            return 5
        return 4 if s.busy else 0

    def WD_DisconnectPS(self, h):
        self._log("WD_DisconnectPS")
        if self.connected is None:
            return 1
        self.connected = None
        return 0

    def WD_ResetSystem(self, h):
        self._log("WD_ResetSystem")
        self.connected = None
        self.reset_count += 1
        self.params = {"NOATTRB", "NOEAB", "TWAIT", "SRCHALL", "SRCHFRWD", "AUTORESET", "CONLOG", "FPAUSE"}
        return 0

    def MultiThreadConnectionOn(self):
        self._log("MultiThreadConnectionOn")
        self.multithread = True
        return 0

    # ----------------------------------------------------------------- fn 9
    def WD_SetSessionParamEx(self, h, text):
        self._log("WD_SetSessionParamEx", text)
        known = {"ATTRB", "NOATTRB", "EAB", "NOEAB", "XLATE", "NOXLATE", "NEWRET", "OLDRET", "NWAIT", "LWAIT", "TWAIT",
                 "SRCHALL", "SRCHFROM", "SRCHFRWD", "SRCHBKWD", "FPAUSE", "IPAUSE", "NORESET", "AUTORESET", "CONLOG",
                 "CONPHYS", "WRITE_SUPER", "WRITE_WRITE", "WRITE_READ", "WRITE_NONE", "SUPER_WRITE", "QUIET", "NOQUIET",
                 "CFGSIZE", "NOCFGSIZE"}
        rc = 0
        for word in _in_bytes(text).decode("latin-1").replace(",", " ").split():
            word = word.upper()
            if word not in known:
                rc = 2
                continue
            for group in EXCLUSIVE_PARAMS:  # extensão P-0: a opção oposta deixa de valer
                if word in group:
                    self.params -= group - {word}
            self.params.add(word)
        return rc

    # ----------------------------------------------------------------- fn 4 / 18
    def WD_Wait(self, h):
        self._log("WD_Wait")
        s = self._cur()
        if s is None:
            return 1
        if s.inhibited:
            return 5
        if "NWAIT" in self.params:
            return 4 if s.busy else 0
        deadline = time.monotonic() + self.wait_timeout
        while s.busy and time.monotonic() < deadline:
            time.sleep(0.005)
        return 4 if s.busy else 0

    def WD_Pause(self, h, half_seconds):
        self._log("WD_Pause", half_seconds)
        time.sleep(min(_int(half_seconds) * 0.5, 0.05))
        return 0

    # ----------------------------------------------------------------- fn 5 / 8 / 15
    def WD_CopyPS(self, h, buf, length=0):
        self._log("WD_CopyPS")
        s = self._cur()
        if s is None:
            return 1
        _out_write(buf, bytes(s.buffer))
        if s.inhibited:
            return 5
        return 4 if s.busy else 0

    def WD_CopyPSToString(self, h, pos, buf, length):
        self._log("WD_CopyPSToString", _int(pos), _int(length))
        s = self._cur()
        if s is None:
            return 1
        pos, length = _int(pos), _int(length)
        if not 1 <= pos <= s.size:
            return 7
        if length == 0 or pos + length - 1 > s.size:
            return 2
        _out_write(buf, bytes(s.buffer[pos - 1:pos - 1 + length]))
        if s.inhibited:
            return 5
        return 4 if s.busy else 0

    def WD_CopyStringToPS(self, h, pos, data, length):
        self._log("WD_CopyStringToPS", _int(pos), _int(length))
        s = self._cur()
        if s is None:
            return 1
        pos, length = _int(pos), _int(length)
        if length <= 0:
            return 2
        if not 1 <= pos <= s.size:
            return 7
        if s.busy or s.inhibited:
            return 5
        payload = _in_raw(data, length)
        rc = 0
        for i, byte in enumerate(payload):
            p = pos + i
            if p > s.size:
                return 6
            fld = s.is_data_pos(p)
            if fld is None or fld.protected:
                return 6
            s.buffer[p - 1] = byte
            fld.mdt = True
        return rc

    # ----------------------------------------------------------------- fn 6 / 7 / 40
    def WD_SearchPS(self, h, p_found, start_pos, text):
        self._log("WD_SearchPS", _int(start_pos))
        s = self._cur()
        if s is None:
            return 1
        needle = _in_bytes(text)
        if not needle:
            return 2
        hay = bytes(s.buffer)
        if "SRCHFROM" in self.params:
            start = max(_int(start_pos), 1)
            if "SRCHBKWD" in self.params:
                idx = hay.rfind(needle, 0, start)
            else:
                idx = hay.find(needle, start - 1)
        else:
            idx = hay.rfind(needle) if "SRCHBKWD" in self.params else hay.find(needle)
        if idx < 0:
            _out_word(p_found, 0)
            return 24
        _out_word(p_found, idx + 1)
        return 0

    def WD_QueryCursorLocation(self, h, p_pos):
        self._log("WD_QueryCursorLocation")
        s = self._cur()
        if s is None:
            return 1
        _out_word(p_pos, s.cursor)
        return 0

    def WD_SetCursor(self, h, pos):
        self._log("WD_SetCursor", _int(pos))
        s = self._cur()
        if s is None:
            return 1
        pos = _int(pos)
        if not 1 <= pos <= s.size:
            return 7
        if s.busy:
            return 4
        s.cursor = pos
        return 0

    # ----------------------------------------------------------------- fn 13 (OIA)
    def WD_CopyOIA(self, h, buf, length=103):
        self._log("WD_CopyOIA")
        s = self._cur()
        if s is None:
            return 1
        groups = bytearray(22)
        groups[0] = 0x10  # G1: sessão LU-LU
        if s.busy:
            groups[8] = 0x20  # G8 byte 2: system wait (X SYSTEM)
        if s.inhibited:
            groups[7] = 0x04  # G8 byte 1: minus function / operador
        data = bytes([1]) + b"\x10" * 80 + bytes(groups)
        _out_write(buf, data)
        if s.inhibited:
            return 5
        return 4 if s.busy else 0

    # ----------------------------------------------------------------- fn 14 / 30-34
    def WD_QueryFieldAttribute(self, h, p_attr, pos):
        self._log("WD_QueryFieldAttribute", _int(pos))
        s = self._cur()
        if s is None:
            return 1
        pos = _int(pos)
        if not 1 <= pos <= s.size:
            return 7
        if not s.fields:
            _out_word(p_attr, 0)
            return 24
        _out_word(p_attr, s.field_at(pos).attribute_byte())
        return 0

    def _field_by_code(self, s: FakeSession, pos: int, code: str) -> Optional[FakeField]:
        if not s.fields:
            return None
        current = s.field_at(pos)
        idx = s.fields.index(current)
        code = (code + "  ")[:2]
        if code.strip() in ("T", ""):
            return current
        direction = 1 if code[0] == "N" else -1
        n = len(s.fields)
        for step in range(1, n + 1):
            cand = s.fields[(idx + direction * step) % n]
            if code[1] == "P" and not cand.protected:
                continue
            if code[1] == "U" and cand.protected:
                continue
            return cand
        return None

    def WD_FindFieldPosition(self, h, p_pos, pos, code):
        self._log("WD_FindFieldPosition", _int(pos))
        s = self._cur()
        if s is None:
            return 1
        fld = self._field_by_code(s, _int(pos), _in_bytes(code).decode("latin-1"))
        if fld is None:
            return 24
        if fld.length == 0:
            return 28
        _out_word(p_pos, fld.start)
        return 0

    def WD_FindFieldLength(self, h, p_len, pos, code):
        self._log("WD_FindFieldLength", _int(pos))
        s = self._cur()
        if s is None:
            return 1
        fld = self._field_by_code(s, _int(pos), _in_bytes(code).decode("latin-1"))
        if fld is None:
            return 24
        _out_word(p_len, fld.length)
        return 28 if fld.length == 0 else 0

    def WD_CopyFieldToString(self, h, pos, buf, length):
        self._log("WD_CopyFieldToString", _int(pos), _int(length))
        s = self._cur()
        if s is None:
            return 1
        if not s.fields:
            return 24
        fld = s.field_at(_int(pos))
        data = bytes(s.buffer[fld.start - 1:fld.end])
        length = _int(length)
        _out_write(buf, data[:length])
        return 6 if len(data) > length else 0

    def WD_CopyStringToField(self, h, pos, text):
        self._log("WD_CopyStringToField", _int(pos))
        s = self._cur()
        if s is None:
            return 1
        if not s.fields:
            return 24
        fld = s.field_at(_int(pos))
        if fld.protected or s.busy or s.inhibited:
            return 5
        data = _in_bytes(text)
        rc = 6 if len(data) > fld.length else 0
        data = data[:fld.length]
        s.buffer[fld.start - 1:fld.start - 1 + len(data)] = data
        fld.mdt = True
        return rc

    # ----------------------------------------------------------------- fn 1002 / 1001
    def WD_GetSessionHWND(self, h):
        self._log("WD_GetSessionHWND")
        s = self._cur()
        return s.hwnd if s else 0

    def WD_ShowSession(self, h, cmd):
        self._log("WD_ShowSession", _int(cmd))
        if not 0 <= _int(cmd) <= 9:
            return 2
        return 0 if self._cur() else 1

    # ----------------------------------------------------------------- fn 3 (SendKey)
    def WD_SendKey(self, h, keys):
        self._log("WD_SendKey", keys)
        s = self._cur()
        if s is None:
            return 1
        raw = _in_bytes(keys)
        if len(raw) >= 256:
            return 2
        if s.inhibited and "AUTORESET" not in self.params and not raw.startswith(b"@R"):
            return 5  # com NORESET só a tecla RESET destrava o "X -f"
        if s.busy:
            return 4
        s.inhibited = False  # AUTORESET
        text = raw.decode(ENCODING)
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "@" and i + 1 < len(text):
                code = text[i + 1]
                i += 2
                if code == "@":
                    if not self._type_char(s, "@"):
                        return 5
                    continue
                if code == "A" and i + 1 < len(text) and text[i] == "@":
                    sub = text[i + 1]
                    i += 2
                    if sub in AID_A_KEYS:
                        self._aid(s, AID_A_KEYS[sub])
                        return 0
                    if sub in LOCAL_A_KEYS:
                        self._local(s, LOCAL_A_KEYS[sub])
                        continue
                    return 2
                if code == "S" and i + 1 < len(text) and text[i] == "@":
                    i += 2  # DUP / FIELD MARK: não simulados
                    continue
                if code in AID_KEYS:
                    self._aid(s, AID_KEYS[code])
                    return 0  # o resto da string é descartado
                if code in LOCAL_KEYS:
                    self._local(s, LOCAL_KEYS[code])
                    continue
                return 2
            if not self._type_char(s, ch):
                return 5
            i += 1
        return 0

    def _type_char(self, s: FakeSession, ch: str) -> bool:
        fld = s.is_data_pos(s.cursor)
        if fld is None or fld.protected:
            s.inhibited = True  # "X -f" ao digitar em área protegida
            return False
        if fld.numeric and not ch.isdigit():
            s.inhibited = True
            return False
        s.buffer[s.cursor - 1] = ch.encode(ENCODING, "replace")[0]
        fld.mdt = True
        if s.cursor == fld.end:
            nxt = self._field_by_code(s, s.cursor, "NU")
            s.cursor = nxt.start if nxt else fld.start
        else:
            s.cursor += 1
        return True

    def _local(self, s: FakeSession, key: str) -> None:
        if key == "TAB":
            nxt = self._field_by_code(s, s.cursor, "NU")
            if nxt:
                s.cursor = nxt.start
        elif key == "BACKTAB":
            prv = self._field_by_code(s, s.cursor, "PU")
            if prv:
                s.cursor = prv.start
        elif key == "HOME":
            unprot = [f for f in s.fields if not f.protected]
            s.cursor = unprot[0].start if unprot else 1
        elif key == "ERASEEOF":
            fld = s.is_data_pos(s.cursor)
            if fld and not fld.protected:
                s.buffer[s.cursor - 1:fld.end] = b" " * (fld.end - s.cursor + 1)
                fld.mdt = True
        elif key == "ERASEINPUT":
            for fld in s.fields:
                if not fld.protected:
                    s.buffer[fld.start - 1:fld.end] = b" " * fld.length
                    fld.mdt = False
            unprot = [f for f in s.fields if not f.protected]
            s.cursor = unprot[0].start if unprot else 1
        elif key == "RESET":
            s.inhibited = False
        elif key in ("LEFT", "BACKSPACE"):
            s.cursor = s.cursor - 1 if s.cursor > 1 else s.size
            if key == "BACKSPACE":
                fld = s.is_data_pos(s.cursor)
                if fld and not fld.protected:
                    s.buffer[s.cursor - 1] = 0x20
        elif key == "RIGHT":
            s.cursor = s.cursor + 1 if s.cursor < s.size else 1
        elif key == "UP":
            s.cursor = s.cursor - s.cols if s.cursor > s.cols else s.cursor + s.size - s.cols
        elif key == "DOWN":
            s.cursor = s.cursor + s.cols if s.cursor + s.cols <= s.size else s.cursor - s.size + s.cols
        elif key == "NEWLINE":
            row, _ = s.rowcol(s.cursor)
            s.cursor = s.pos(row % s.rows + 1, 1)
        # INSERT/END/PRINT/DELETE etc.: sem efeito relevante para os testes

    def _aid(self, s: FakeSession, aid: str) -> None:
        fields = s.modified_fields()
        s.aid_log.append((aid, fields))
        s.last_aid_at = time.monotonic()
        if s.response_delay > 0:  # extensão P-0: o host responde depois; a tela antiga continua até lá
            s.locked_until = float("inf")
            s.pending_responses += 1
            timer = threading.Timer(s.response_delay, self._respond, args=(s, aid, fields))
            timer.daemon = True
            timer.start()
            return
        s.lock_for(s.host_delay)
        if s.on_aid is not None:
            s.on_aid(s, aid, fields)
        for fld in s.fields:
            fld.mdt = False

    def _respond(self, s: FakeSession, aid: str, fields: dict) -> None:
        """Resposta atrasada do host (thread do ``threading.Timer``)."""
        with self._lock:
            try:
                if s.on_aid is not None:
                    s.on_aid(s, aid, fields)
                for fld in s.fields:
                    fld.mdt = False
            finally:
                s.pending_responses -= 1
                s.lock_for(s.host_delay)  # o teclado só libera com a tela nova no lugar

    # ----------------------------------------------------------------- nomes alternativos
    def __getattr__(self, name):
        if name.startswith("WD_") or name == "hllapi":
            raise AttributeError(f"função {name} não simulada pelo FakeEhllapi")
        raise AttributeError(name)


def make_cics_login_host(valid_user: str = "A000000", valid_password: str = "senha", expired: bool = False):
    """Host CICS simulado: tela de ambiente (22,56) → signon (12,21)/(13,21) → menu 'Signon' (5,20)."""
    env = FakeScreen(name="env", lines=[""] * 13 + [" " * 25 + "Select CICS Environment:"] + [""] * 7 +
                     [" " * 20 + "Please inform the Environment:"], fields=[
        {"row": 22, "col": 56, "length": 1, "name": "env"},
    ])
    signon = FakeScreen(name="signon", lines=["", "", "", "     TERMINAL  T1B11210"] + [""] * 20, fields=[
        {"row": 12, "col": 21, "length": 8, "name": "uid"},
        {"row": 13, "col": 21, "length": 8, "name": "pwd", "hidden": True},
        {"row": 19, "col": 19, "length": 40, "name": "msg", "protected": True},
    ])
    menu = FakeScreen(name="menu", lines=["", "", "", "", " " * 19 + "Signon is complete"] + [""] * 19, fields=[
        {"row": 1, "col": 2, "length": 70, "name": "cmd"},
    ])

    def host(session: FakeSession, aid: str, fields: dict) -> None:
        if session.screen_name == "env" and aid == "ENTER" and fields.get("env", "").upper() == "C":
            session.load(signon)
        elif session.screen_name == "signon" and aid == "ENTER":
            if fields.get("uid") == valid_user and fields.get("pwd") == valid_password:
                if expired:
                    session.load(signon)
                    msg = session.fields[2]
                    session.buffer[msg.start - 1:msg.start - 1 + 14] = b"SENHA EXPIRADA"
                else:
                    session.load(menu)
            else:
                session.load(signon)
                msg = session.fields[2]
                session.buffer[msg.start - 1:msg.start - 1 + 14] = b"SENHA INVALIDA"

    return env, host
