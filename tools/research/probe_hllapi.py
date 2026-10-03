"""Sonda SOMENTE LEITURA do estado HLLAPI do RUMBA. Rode com o Python 32-bit:

    %LOCALAPPDATA%\\Programs\\Python\\Python313-32\\python.exe tools\\research\\probe_hllapi.py

Não conecta em nenhuma sessão, não envia teclas e não altera a tela. Mostra:
  * WD_QuerySystem       — versão/nível do EHLLAPI
  * WD_QuerySessions     — sessões publicadas na memória compartilhada da ehlapi32.Dll
  * WD_QuerySessionStatus para cada letra encontrada (linhas, colunas, code page)

Lembre: só aparecem sessões abertas por perfis com HLLAPI (index_pro_api.html → BRAZIL_D etc.).
"""
import ctypes
import struct
import sys

DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
H = 1  # 1º argumento das WD_*: identificador/HWND da aplicação chamadora (não é a identidade HLLAPI)


def main() -> int:
    if struct.calcsize("P") != 4:
        print("Use o Python 32-bit.")
        return 2
    dll = ctypes.WinDLL(DLL_PATH)

    dll.WD_QuerySystem.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.WD_QuerySystem.restype = ctypes.c_ushort
    buf = ctypes.create_string_buffer(64)
    rc = dll.WD_QuerySystem(H, buf)
    print(f"WD_QuerySystem rc={rc} data={buf.raw[:36]!r}")

    dll.WD_QuerySessions.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_char_p]
    dll.WD_QuerySessions.restype = ctypes.c_ushort
    count = ctypes.c_ushort(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    rc = dll.WD_QuerySessions(H, ctypes.byref(count), buf)
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
        rc = dll.WD_QuerySessionStatus(H, status)
        raw = status.raw[:20]
        # layout 32-bit: [0] short, [4:12] long name, [12] tipo, [13] caract., [14:16] linhas,
        # [16:18] colunas, [18:20] code page do host
        rows, cols, codepage = struct.unpack_from("<HHH", raw, 14)
        print(f"  status {letter.decode()} rc={rc} long={raw[4:12]!r} type={raw[12:13]!r} "
              f"char=0x{raw[13]:02x} rows={rows} cols={cols} cp={codepage} raw={raw.hex(' ')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
