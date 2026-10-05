"""Sonda do estado HLLAPI do RUMBA — carrega a ``ehlapi32.Dll`` REAL. Rode com o Python 32-bit:

    set RUMBAPY_LIVE=1
    .venv32\\Scripts\\python.exe tools\\research\\probe_hllapi.py

NÃO é inofensiva: carrega a DLL de produção (sem ``GuardedDll``) e chama ``WD_QuerySessions``, que pode travar
dentro da DLL logo depois de um emulador fechar (A.14 l/m). Só o agente principal ou o dono a roda, com autorização;
exige ``RUMBAPY_LIVE=1`` (e recusa com ``RUMBAPY_SANDBOX=1``) e a confirmação ``SIM`` digitada no console
(``CONIN$``; nenhuma variável de ambiente confirma). Cada chamada tem um watchdog que sai com ``os._exit`` se a DLL
travar — **nunca** mate o processo à mão (``taskkill /F`` vaza a tabela de 32 clientes da DLL). No fim chama
``WD_ResetSystem``.

Só consulta: não conecta em nenhuma sessão, não envia teclas e não altera a tela. Mostra:
  * WD_QuerySystem       — versão/nível do EHLLAPI
  * WD_QuerySessions     — sessões publicadas na memória compartilhada da ehlapi32.Dll
  * WD_QuerySessionStatus para cada letra encontrada (linhas, colunas, code page)

Lembre: só aparecem sessões abertas por perfis com HLLAPI (index_pro_api.html → BRAZIL_D etc.).
"""
from collections.abc import Callable, Mapping
from typing import Any, TextIO

import threading
import ctypes
import struct
import sys
import os

DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
H = 1  # 1º argumento das WD_*: identificador/HWND da aplicação chamadora (não é a identidade HLLAPI)
# limites do watchdog (= _worker/watchdog.py): 8 s no WD_QuerySessions, 5 s no encerramento, 20 s nas demais
LIMITS = {"WD_QuerySessions": 8.0, "WD_ResetSystem": 5.0}
DEFAULT_LIMIT = 20.0
EXIT_HUNG = 3


def refusal(env: Mapping[str, str]) -> str | None:
    """Motivo para NÃO rodar (``None`` = pode pedir a confirmação)."""
    if env.get("RUMBAPY_SANDBOX") == "1":
        return "RUMBAPY_SANDBOX=1: esta sonda carrega a DLL real e nunca roda no sandbox dos testes."
    if env.get("RUMBAPY_LIVE") != "1":
        return ("esta sonda carrega a ehlapi32.Dll REAL; defina RUMBAPY_LIVE=1 (só o agente principal ou o dono, com "
                "autorização).")
    return None


def open_console() -> tuple[TextIO, TextIO] | None:
    """``(CONIN$, CONOUT$)`` do console do Windows, ou ``None`` sem console."""
    try:
        conin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        conout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except OSError:
        conin.close()
        return None
    return conin, conout


def confirmed(console_factory: Callable[[], tuple[TextIO, TextIO] | None] = open_console) -> bool:
    """Confirmação ``SIM`` digitada no console (nada de stdin redirecionado nem variável de ambiente)."""
    console = console_factory()
    if console is None:
        print("Sem console (CONIN$): recusado.")
        return False
    conin, conout = console
    try:
        conout.write("Esta sonda carrega a ehlapi32.Dll REAL do RUMBA (só consultas). Digite SIM para continuar: ")
        conout.flush()
        return conin.readline().strip() == "SIM"
    finally:
        conin.close()
        conout.close()


def guarded(name: str, fn: Callable[[], Any]) -> Any:
    """Chama ``fn`` com watchdog: se a DLL travar, sai com ``os._exit`` (nunca ``TerminateProcess``)."""
    limit = LIMITS.get(name, DEFAULT_LIMIT)

    def hung() -> None:
        sys.stderr.write(f"{name} travou por {limit:g} s dentro da DLL; saindo com os._exit({EXIT_HUNG}). "
                         "NÃO mate processos à mão.\n")
        sys.stderr.flush()
        os._exit(EXIT_HUNG)

    timer = threading.Timer(limit, hung)
    timer.daemon = True
    timer.start()
    try:
        return fn()
    finally:
        timer.cancel()


def probe(dll: Any) -> None:
    dll.WD_QuerySystem.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.WD_QuerySystem.restype = ctypes.c_ushort
    buf = ctypes.create_string_buffer(64)
    rc = guarded("WD_QuerySystem", lambda: dll.WD_QuerySystem(H, buf))
    print(f"WD_QuerySystem rc={rc} data={buf.raw[:36]!r}")

    dll.WD_QuerySessions.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_char_p]
    dll.WD_QuerySessions.restype = ctypes.c_ushort
    count = ctypes.c_ushort(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    rc = guarded("WD_QuerySessions", lambda: dll.WD_QuerySessions(H, ctypes.byref(count), buf))
    print(f"WD_QuerySessions rc={rc} count={count.value}")
    letters = []
    for i in range(count.value):
        # layout 32-bit (struct QuerySession do RDEEHLLAPI.dll): [0] short, [4:12] long name, [12] tipo,
        # [14:16] tamanho do PS (WORD)
        entry = buf.raw[i * 16:(i + 1) * 16]
        ps_size = struct.unpack_from("<H", entry, 14)[0]
        print(f"  [{i}] short={entry[:1]!r} long={entry[4:12]!r} type={entry[12:13]!r} ps_size={ps_size} "
              f"raw={entry.hex(' ')}")
        if entry[:1].isalpha():
            letters.append(entry[:1])

    dll.WD_QuerySessionStatus.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.WD_QuerySessionStatus.restype = ctypes.c_ushort
    for letter in letters:
        status = ctypes.create_string_buffer(32)
        status[0] = letter
        rc = guarded("WD_QuerySessionStatus", lambda: dll.WD_QuerySessionStatus(H, status))
        raw = status.raw[:20]
        # layout 32-bit: [0] short, [4:12] long name, [12] tipo, [13] caract., [14:16] linhas,
        # [16:18] colunas, [18:20] code page do host
        rows, cols, codepage = struct.unpack_from("<HHH", raw, 14)
        print(f"  status {letter.decode()} rc={rc} long={raw[4:12]!r} type={raw[12:13]!r} "
              f"char=0x{raw[13]:02x} rows={rows} cols={cols} cp={codepage} raw={raw.hex(' ')}")


def main() -> int:
    reason = refusal(os.environ)
    if reason is not None:
        print(f"Recusado: {reason}")
        return 2
    if struct.calcsize("P") != 4:
        print("Use o Python 32-bit.")
        return 2
    if not confirmed():
        print("Não confirmado: nada foi carregado.")
        return 2
    dll = ctypes.WinDLL(DLL_PATH)
    try:
        probe(dll)
    finally:
        dll.WD_ResetSystem.argtypes = [ctypes.c_void_p]
        dll.WD_ResetSystem.restype = ctypes.c_ushort
        rc = guarded("WD_ResetSystem", lambda: dll.WD_ResetSystem(H))
        print(f"WD_ResetSystem rc={rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
