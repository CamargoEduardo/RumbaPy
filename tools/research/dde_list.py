"""Lista servidores/tópicos DDE ativos (DDEML via ctypes). Qualquer Python (32 ou 64-bit).

    set RUMBAPY_LIVE=1
    python tools\\research\\dde_list.py [--all]

O controle HLLAPI do RUMBA (WDApiSvc.ocx) também é um servidor DDE: serviço "RUMBADSP" para
sessões mainframe ("RUMBAWSF" AS/400, "RUMBAVT" UNIX, "RUMBAHP" HP). Ele só existe quando a sessão
foi aberta por um perfil com HLLAPI (ex.: profpro/BRAZIL_D.html via index_pro_api.html).
Como DDE é baseado em mensagens de janela, funciona entre processos 32 e 64-bit.

NÃO é somente leitura: o ``DdeConnectList`` manda ``WM_DDE_INITIATE`` por ``SendMessage`` a TODAS as janelas de nível
superior (mesmo com um serviço específico: só quem responde muda) e abre uma conversa com cada servidor que responder
(inclusive o ``RUMBADSP`` do emulador). Uma janela pendurada trava a sonda. Por isso exige ``RUMBAPY_LIVE=1`` (recusa
com ``RUMBAPY_SANDBOX=1``) e a confirmação ``SIM`` digitada no console; só o agente principal ou o dono a roda, com
autorização e com o RUMBA ocioso. Por padrão consulta só o serviço ``RUMBADSP``; ``--all`` lista todos (curinga).
"""
from collections.abc import Mapping

import ctypes.wintypes as wt
import ctypes
import sys
import os

user32 = ctypes.WinDLL("user32", use_last_error=True)
DDECALLBACK = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
APPCMD_CLIENTONLY = 0x10
CP_WINUNICODE = 1200

user32.DdeInitializeW.argtypes = [ctypes.POINTER(wt.DWORD), DDECALLBACK, wt.DWORD, wt.DWORD]
user32.DdeConnectList.argtypes = [wt.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.DdeConnectList.restype = ctypes.c_void_p
user32.DdeQueryNextServer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.DdeQueryNextServer.restype = ctypes.c_void_p
user32.DdeQueryConvInfo.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
user32.DdeQueryStringW.argtypes = [wt.DWORD, ctypes.c_void_p, wt.LPWSTR, wt.DWORD, ctypes.c_int]
user32.DdeDisconnectList.argtypes = [ctypes.c_void_p]
user32.DdeUninitialize.argtypes = [wt.DWORD]
user32.DdeCreateStringHandleW.argtypes = [wt.DWORD, wt.LPCWSTR, ctypes.c_int]
user32.DdeCreateStringHandleW.restype = ctypes.c_void_p
user32.DdeFreeStringHandle.argtypes = [wt.DWORD, ctypes.c_void_p]
SERVICE = "RUMBADSP"


def refusal(env: Mapping[str, str]) -> str | None:
    """Motivo para NÃO rodar (``None`` = pode pedir a confirmação)."""
    if env.get("RUMBAPY_SANDBOX") == "1":
        return "RUMBAPY_SANDBOX=1: esta sonda manda mensagens a janelas reais e nunca roda no sandbox dos testes."
    if env.get("RUMBAPY_LIVE") != "1":
        return ("esta sonda faz broadcast de WM_DDE_INITIATE e abre conversas DDE com janelas reais; defina "
                "RUMBAPY_LIVE=1 (só o agente principal ou o dono, com autorização).")
    return None


def confirmed() -> bool:
    """Confirmação ``SIM`` digitada no console (``CONIN$``)."""
    try:
        conin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        conout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except OSError:
        print("Sem console (CONIN$): recusado.")
        return False
    try:
        conout.write("Esta sonda abre conversas DDE com as janelas reais (inclusive o RUMBA). Digite SIM: ")
        conout.flush()
        return conin.readline().strip() == "SIM"
    finally:
        conin.close()
        conout.close()


class CONVINFO(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("hUser", ctypes.c_void_p), ("hConvPartner", ctypes.c_void_p),
                ("hszSvcPartner", ctypes.c_void_p), ("hszServiceReq", ctypes.c_void_p),
                ("hszTopic", ctypes.c_void_p), ("hszItem", ctypes.c_void_p), ("wFmt", wt.UINT),
                ("wType", wt.UINT), ("wStatus", wt.UINT), ("wConvst", wt.UINT), ("wLastError", wt.UINT),
                ("hConvList", ctypes.c_void_p), ("ConvCtxt", ctypes.c_byte * 24), ("hwnd", wt.HWND),
                ("hwndPartner", wt.HWND)]


def main():
    reason = refusal(os.environ)
    if reason is not None:
        raise SystemExit(f"Recusado: {reason}")
    if not confirmed():
        raise SystemExit("Não confirmado: nada foi enviado.")
    wildcard = "--all" in sys.argv[1:]
    callback = DDECALLBACK(lambda *args: 0)
    inst = wt.DWORD(0)
    if user32.DdeInitializeW(ctypes.byref(inst), callback, APPCMD_CLIENTONLY, 0) != 0:
        raise SystemExit("DdeInitialize falhou")

    def text(hsz):
        out = ctypes.create_unicode_buffer(256)
        user32.DdeQueryStringW(inst.value, hsz, out, 256, CP_WINUNICODE)
        return out.value

    service = None if wildcard else user32.DdeCreateStringHandleW(inst.value, SERVICE, CP_WINUNICODE)
    conv_list = user32.DdeConnectList(inst.value, service, None, None, None)
    if service:
        user32.DdeFreeStringHandle(inst.value, service)
    conv = None
    while conv_list:
        conv = user32.DdeQueryNextServer(conv_list, conv)
        if not conv:
            break
        info = CONVINFO(cb=ctypes.sizeof(CONVINFO))
        user32.DdeQueryConvInfo(conv, 0xFFFFFFFF, ctypes.byref(info))
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(info.hwndPartner, ctypes.byref(pid))
        print(f"servico={text(info.hszSvcPartner)!r} topico={text(info.hszTopic)!r} pid={pid.value}")
    if conv_list:
        user32.DdeDisconnectList(conv_list)
    user32.DdeUninitialize(inst.value)


if __name__ == "__main__":
    main()
