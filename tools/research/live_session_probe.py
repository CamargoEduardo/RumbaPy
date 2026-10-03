"""Sonda AO VIVO e SOMENTE LEITURA de uma sessão RUMBA aberta pelo Host Access Web Launcher.

Rode com o Python 32-bit:
    python32 tools\\research\\live_session_probe.py [--profile BRAZIL_D] [--letter D] [--keep-open]

O que faz (nada é digitado e nenhuma tecla é enviada ao host):
  1. Lança "Host Access Web Launcher.exe" com o perfil pedido (processo próprio, PID conhecido).
  2. Espera a letra aparecer em WD_QuerySessions.
  3. WD_ConnectPS (conexão HLLAPI local), WD_QuerySessionStatus, WD_CopyOIA, WD_CopyPS,
     WD_QueryCursorLocation, WD_Wait, WD_GetSessionHWND, WD_ShowSession (minimiza/restaura a janela local).
  4. Mostra a tela decodificada em cp1252 e cp850 e os bytes >= 0x80 (para descobrir o encoding).
  5. WD_DisconnectPS + WD_ResetSystem e fecha a janela do launcher com WM_CLOSE (a menos que --keep-open).

Um watchdog encerra o processo com os._exit() se alguma chamada da DLL travar (ela espera sem prazo).
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys
import threading
import time

DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
LAUNCHER = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
PORTAL = "https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/{profile}.html"
H = 1

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WM_CLOSE = 0x0010


class Watchdog:
    """Mata o processo (saída limpa via os._exit) se uma etapa passar do prazo."""

    def __init__(self):
        self._deadline = None
        self._label = ""
        threading.Thread(target=self._run, daemon=True).start()

    def arm(self, label, seconds):
        self._label, self._deadline = label, time.monotonic() + seconds

    def disarm(self):
        self._deadline = None

    def _run(self):
        while True:
            time.sleep(0.2)
            if self._deadline is not None and time.monotonic() > self._deadline:
                print(f"!! WATCHDOG: etapa '{self._label}' travou; saindo com os._exit(3)", flush=True)
                os._exit(3)


def window_info(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    return {"hwnd": hex(hwnd or 0), "pid": pid.value, "class": cls.value, "title": title.value,
            "visible": bool(user32.IsWindowVisible(hwnd)), "iconic": bool(user32.IsIconic(hwnd))}


def top_windows_of(pid):
    found = []

    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return found


def bind(dll):
    sig = {
        "WD_QuerySessions": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_char_p], ctypes.c_ushort),
        "WD_ConnectPS": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_ushort),
        "WD_DisconnectPS": ([ctypes.c_void_p], ctypes.c_ushort),
        "WD_ResetSystem": ([ctypes.c_void_p], ctypes.c_ushort),
        "WD_QuerySessionStatus": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_ushort),
        "WD_CopyOIA": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ushort], ctypes.c_ushort),
        "WD_CopyPS": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ushort], ctypes.c_ushort),
        "WD_QueryCursorLocation": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort)], ctypes.c_ushort),
        "WD_Wait": ([ctypes.c_void_p], ctypes.c_ushort),
        "WD_GetSessionHWND": ([ctypes.c_void_p], ctypes.c_void_p),
        "WD_ShowSession": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_ushort),
        "WD_QueryFieldAttribute": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.c_ushort], ctypes.c_ushort),
    }
    for name, (args, res) in sig.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, res
    return dll


def query_sessions(dll):
    count = ctypes.c_ushort(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    rc = dll.WD_QuerySessions(H, ctypes.byref(count), buf)
    entries = []
    for i in range(count.value):
        e = buf.raw[i * 16:(i + 1) * 16]
        entries.append({"short": e[:1].decode("latin-1"), "long": e[4:12].decode("latin-1"),
                        "type": e[12:13].decode("latin-1"), "ps_size": struct.unpack_from("<H", e, 14)[0],
                        "raw": e.hex(" ")})
    return rc, entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="BRAZIL_D")
    ap.add_argument("--letter", default="D")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--attach-only", action="store_true", help="não lança o launcher; só anexa")
    args = ap.parse_args()
    if struct.calcsize("P") != 4:
        sys.exit("Use o Python 32-bit.")

    wd = Watchdog()
    dll = bind(ctypes.WinDLL(DLL_PATH))
    report = {"profile": args.profile, "letter": args.letter}

    wd.arm("QuerySessions inicial", 15)
    report["sessions_before"] = query_sessions(dll)
    wd.disarm()

    proc = None
    if not args.attach_only:
        url = "hawlauncher://" + PORTAL.format(profile=args.profile) + "/microfocus_w2h_hawlauncher"
        t0 = time.monotonic()
        proc = subprocess.Popen([LAUNCHER, url])
        report["launcher_pid"] = proc.pid
        registered = None
        while time.monotonic() - t0 < 120:
            wd.arm("QuerySessions (aguardando registro)", 15)
            rc, entries = query_sessions(dll)
            wd.disarm()
            match = [e for e in entries if e["short"] == args.letter]
            if match:
                registered = match[0]
                break
            if proc.poll() is not None:
                report["launcher_exit"] = proc.returncode
                break
            time.sleep(0.5)
        report["registered"] = registered
        report["seconds_to_register"] = round(time.monotonic() - t0, 1)
        report["launcher_windows"] = [window_info(h) for h in top_windows_of(proc.pid)]
        if not registered:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 1

    wd.arm("ConnectPS", 30)
    t0 = time.monotonic()
    rc = dll.WD_ConnectPS(H, args.letter.encode())
    report["connect_rc"] = rc
    report["connect_s"] = round(time.monotonic() - t0, 2)
    wd.disarm()

    # Se conectou com rc 4/5 (ocupado/travado), aguarda o teclado liberar com WD_Wait (sem digitar nada).
    waits = []
    for _ in range(10):
        wd.arm("Wait", 90)
        t0 = time.monotonic()
        wrc = dll.WD_Wait(H)
        waits.append((wrc, round(time.monotonic() - t0, 2)))
        wd.disarm()
        if wrc == 0:
            break
        time.sleep(1)
    report["wait"] = waits

    status = ctypes.create_string_buffer(32)
    status[0] = args.letter.encode()
    wd.arm("QuerySessionStatus", 15)
    rc = dll.WD_QuerySessionStatus(H, status)
    wd.disarm()
    raw = status.raw[:20]
    rows, cols, cp = struct.unpack_from("<HHH", raw, 14)
    report["session_status"] = {"rc": rc, "raw": raw.hex(" "), "long": raw[4:12].decode("latin-1"),
                                "type": raw[12:13].decode("latin-1"), "char": hex(raw[13]),
                                "rows": rows, "cols": cols, "host_cp": cp}

    oia = ctypes.create_string_buffer(110)
    wd.arm("CopyOIA", 15)
    rc = dll.WD_CopyOIA(H, oia, 103)
    wd.disarm()
    report["oia"] = {"rc": rc, "format": oia.raw[0], "image_cp1252": oia.raw[1:81].decode("cp1252", "replace"),
                     "groups": oia.raw[81:103].hex(" ")}

    size = (rows or 24) * (cols or 80)
    ps = ctypes.create_string_buffer(size * 2 + 16)
    wd.arm("CopyPS", 15)
    rc = dll.WD_CopyPS(H, ps, size)
    wd.disarm()
    data = ps.raw[:size]
    report["copyps_rc"] = rc
    high = sorted({b for b in data if b >= 0x80})
    report["high_bytes"] = [hex(b) for b in high]
    report["nul_count"] = data.count(0)
    samples = []
    for i, b in enumerate(data):
        if b >= 0x80 and len(samples) < 12:
            ctx = data[max(0, i - 12):i + 12]
            samples.append({"pos": i + 1, "byte": hex(b), "cp1252": ctx.decode("cp1252", "replace"),
                            "cp850": ctx.decode("cp850", "replace")})
    report["high_samples"] = samples

    cur = ctypes.c_ushort(0)
    wd.arm("QueryCursorLocation", 15)
    report["cursor_rc"] = dll.WD_QueryCursorLocation(H, ctypes.byref(cur))
    wd.disarm()
    report["cursor_pos"] = cur.value

    # Atributo do campo na posição do cursor (deve ser um campo de entrada na tela de logon)
    attr = ctypes.c_ushort(0)
    wd.arm("QueryFieldAttribute", 15)
    report["field_attr_rc"] = dll.WD_QueryFieldAttribute(H, ctypes.byref(attr), cur.value or 1)
    wd.disarm()
    report["field_attr"] = hex(attr.value)

    wd.arm("GetSessionHWND", 15)
    hwnd = dll.WD_GetSessionHWND(H)
    wd.disarm()
    report["session_hwnd"] = window_info(hwnd) if hwnd else None
    if hwnd:
        root = user32.GetAncestor(hwnd, 2)  # GA_ROOT
        report["session_root"] = window_info(root)

    # Minimiza e restaura pela própria API (janela local; nenhuma tecla vai ao host)
    shows = []
    for cmd in (6, 9):  # SW_MINIMIZE, SW_RESTORE
        wd.arm(f"ShowSession({cmd})", 15)
        shows.append((cmd, dll.WD_ShowSession(H, cmd)))
        wd.disarm()
        time.sleep(1.0)
        if hwnd:
            shows.append(("root_iconic", bool(user32.IsIconic(user32.GetAncestor(hwnd, 2)))))
    report["show_session"] = shows

    wd.arm("Disconnect/Reset", 15)
    report["disconnect_rc"] = dll.WD_DisconnectPS(H)
    report["reset_rc"] = dll.WD_ResetSystem(H)
    wd.disarm()

    lines = [data[r * cols:(r + 1) * cols].replace(b"\0", b" ").decode("cp1252", "replace").rstrip()
             for r in range(rows or 24)]
    report["screen_cp1252"] = lines

    if proc is not None and not args.keep_open:
        wins = top_windows_of(proc.pid)
        for h in wins:
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
        t0 = time.monotonic()
        while proc.poll() is None and time.monotonic() - t0 < 20:
            time.sleep(0.5)
        report["launcher_closed"] = proc.poll() is not None
        report["launcher_close_s"] = round(time.monotonic() - t0, 1)
        if proc.poll() is None:
            report["windows_after_close"] = [window_info(h) for h in top_windows_of(proc.pid)]
        wd.arm("QuerySessions final", 15)
        report["sessions_after"] = query_sessions(dll)
        wd.disarm()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
