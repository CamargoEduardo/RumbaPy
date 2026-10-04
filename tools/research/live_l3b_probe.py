"""Sonda AO VIVO do L3b (J.6, R8): um cliente HLLAPI conectado sai por ``os._exit(3)`` SEM ``WD_DisconnectPS``
(como o watchdog do worker) e um processo novo mede o rc e o tempo do ``WD_ConnectPS``.

Rode com o Python 64-bit do repositório (``RUMBAPY_LIVE=1``); os clientes são processos 32-bit avulsos (Python do
``.venv32``) que chamam SÓ ``WD_QuerySessions``/``WD_ConnectPS``/``WD_DisconnectPS``/``WD_ResetSystem`` — nenhuma função
de entrada é sequer ligada. Passos: lança o BRAZIL_D pelo Web Launcher; cliente A conecta e sai por ``os._exit(3)``;
cliente B conecta (rc/tempo), desconecta e sai normalmente; WM_CLOSE na janela; conferência final. Cada cliente tem
um watchdog próprio (``os._exit`` se uma chamada travar). Recusa rodar se já houver janela do BRAZIL_D aberta.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import ctypes.wintypes as wt
import subprocess
import tempfile
import ctypes
import json
import time
import sys
import os

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
PORTAL = "https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/{profile}.html"
WM_CLOSE = 0x0010
CLIENT = r'''
import ctypes, json, os, struct, sys, threading, time
DLL = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
mode, letter, timeout = sys.argv[1], sys.argv[2].encode(), float(sys.argv[3])
def guard():
    time.sleep(timeout)
    print(json.dumps({"mode": mode, "watchdog": "travou"}), flush=True)
    os._exit(9)
threading.Thread(target=guard, daemon=True).start()
dll = ctypes.WinDLL(DLL)
for name, args in (("WD_QuerySessions", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_char_p]),
                   ("WD_ConnectPS", [ctypes.c_void_p, ctypes.c_char_p]), ("WD_DisconnectPS", [ctypes.c_void_p]),
                   ("WD_ResetSystem", [ctypes.c_void_p])):
    fn = getattr(dll, name); fn.argtypes = args; fn.restype = ctypes.c_ushort
def registered():
    count = ctypes.c_ushort(26); buf = ctypes.create_string_buffer(26 * 16 + 16)
    rc = dll.WD_QuerySessions(1, ctypes.byref(count), buf)
    return rc, [buf.raw[i * 16:i * 16 + 1] for i in range(count.value)]
out = {"mode": mode, "pid": os.getpid()}
if mode == "wait":
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout - 5:
        rc, letters = registered()
        if letter in letters:
            break
        time.sleep(0.5)
    out["registered_s"] = round(time.monotonic() - t0, 1); out["letters"] = [l.decode() for l in letters]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 40:
        rc = dll.WD_ConnectPS(1, letter)
        if rc == 0:
            break
        time.sleep(0.5)
    out["connect_ready_rc"] = rc; out["connect_ready_s"] = round(time.monotonic() - t0, 1)
    out["disconnect_rc"] = dll.WD_DisconnectPS(1); out["reset_rc"] = dll.WD_ResetSystem(1)
    print(json.dumps(out), flush=True); sys.exit(0)
t0 = time.perf_counter()
out["connect_rc"] = dll.WD_ConnectPS(1, letter)
out["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
if mode == "crash":
    print(json.dumps(out), flush=True)
    os._exit(3)                                   # sem DisconnectPS/ResetSystem: como o watchdog do worker
out["disconnect_rc"] = dll.WD_DisconnectPS(1); out["reset_rc"] = dll.WD_ResetSystem(1)
print(json.dumps(out), flush=True)
'''

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def profile_windows(profile: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if title.value.startswith(f"{profile} - Pro Client"):
                found.append((hwnd, title.value))
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return found


def python32() -> str:
    for line in (REPO / ".venv32" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return str(Path(value.strip()) / "python.exe")
    raise SystemExit("não achei o Python 32-bit do .venv32")


def client(script: Path, mode: str, timeout: float) -> dict[str, Any]:
    done = subprocess.run([python32(), str(script), mode, "D", str(timeout)], capture_output=True, text=True,
                          timeout=timeout + 15)
    line = (done.stdout.strip().splitlines() or ["{}"])[-1]
    result = json.loads(line) if line.startswith("{") else {"saida": line}
    result["exit_code"] = done.returncode
    return result


def main() -> int:
    if os.environ.get("RUMBAPY_LIVE") != "1" or os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SystemExit("teste ao vivo: RUMBAPY_LIVE=1 e sem RUMBAPY_SANDBOX")
    if profile_windows("BRAZIL_D"):
        raise SystemExit("já há janela do BRAZIL_D aberta: não mexo na sessão de ninguém")
    work = Path(tempfile.mkdtemp(prefix="rumbapy-l3b-"))
    script = work / "client.py"
    script.write_text(CLIENT, encoding="utf-8")
    report: dict[str, Any] = {}
    url = "hawlauncher://" + PORTAL.format(profile="BRAZIL_D") + "/microfocus_w2h_hawlauncher"
    launcher = subprocess.Popen([LAUNCHER, url])
    try:
        report["wait"] = client(script, "wait", 120)
        report["A_crash"] = client(script, "crash", 30)          # conecta e sai por os._exit(3)
        report["B_after_crash"] = client(script, "measure", 30)  # rc/tempo do ConnectPS depois da morte de A
        report["C_again"] = client(script, "measure", 30)        # mais uma vez, para comparar
    finally:
        for hwnd, _title in profile_windows("BRAZIL_D"):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            report["launcher_exit"] = launcher.wait(30)
        except subprocess.TimeoutExpired:
            report["launcher_exit"] = "não saiu em 30 s"
        report["windows_left"] = [t for _h, t in profile_windows("BRAZIL_D")]
    out = REPO / "docs" / "interno" / "referencia" / f"live_l3b_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
