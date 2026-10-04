"""Roteiros AO VIVO da API v2 (J.6) contra o RUMBA real, com o guard do worker SEMPRE ligado.

Rode com o Python 64-bit do repositório (``set RUMBAPY_LIVE=1``):
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l1   (readonly: abrir/ler/mostrar/fechar; L1+L5)
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l2   (readonly: 2 instâncias -> open fecha as duas)
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l3   (readonly: lease entre processos, evict, reciclagem)
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l4   (text: escrever e apagar, sem AID)
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l6   (login: logon CICS com o config.ini do dono)
    .venv\\Scripts\\python.exe tools\\research\\live_v2_probe.py --script l9   (readonly: BRAZIL_Z aberto + open D)

* O código testado é o do ``HEAD`` (``git archive`` para uma pasta temporária): edições em andamento não interferem.
* ``RUMBAPY_DEV=1`` (sem auto-update), ``RUMBAPY_HOME`` temporário e ``RUMBAPY_GUARD`` do roteiro: ``readonly`` recusa
  TODA tecla e escrita; ``text`` libera só escrita local (nenhuma AID/tecla local); ``login`` libera texto e ENTER.
* Recusa rodar se já houver janela de BRAZIL_D/BRAZIL_Z aberta (não mexe na sessão de ninguém).
* L6 lê ``config.ini`` ([cics] id/password) e NUNCA mostra os valores: o relatório só diz se a senha apareceu (não
  deveria) em algum log/repr/exceção.
* No fim: ``close`` das instâncias, ``shutdown`` dos workers e conferência de que não sobrou janela.

O relatório completo vai para ``docs/interno/referencia/`` (fora do Git); no console sai o mesmo JSON.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import ctypes.wintypes as wt
import configparser
import subprocess
import threading
import traceback
import tempfile
import tarfile
import ctypes
import struct
import json
import time
import sys
import io
import os

REPO = Path(__file__).resolve().parents[2]
GUARDS = {"l1": "readonly", "l2": "readonly", "l3": "readonly", "l4": "text", "l6": "login", "l9": "readonly"}
LAUNCHER = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
PORTAL = "https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/{profile}.html"

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def profile_windows(*profiles: str) -> list[str]:
    found: list[str] = []

    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            for profile in profiles:
                if title.value.startswith(f"{profile} - Pro Client") or f" - {profile} - Pro Client" in title.value:
                    found.append(title.value)
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return found


def snapshot_code(dest: Path) -> Path:
    data = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "HEAD", "src/rumbapy"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "src"


def python32() -> str:
    for line in (REPO / ".venv32" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return str(Path(value.strip()) / "python.exe")
    raise SystemExit("não achei o Python 32-bit do .venv32")


def timed(report: dict[str, Any], name: str, fn: Any) -> Any:
    t0 = time.monotonic()
    try:
        return fn()
    finally:
        report.setdefault("ms", {})[name] = round((time.monotonic() - t0) * 1000, 1)


def window(t: Any) -> dict[str, Any]:
    w = t.window
    return {"title": w.title, "kind": w.kind, "visible": w.visible, "minimized": w.minimized, "process": w.process,
            "pid": w.pid}


def exc_info(exc: BaseException) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": getattr(exc, "message", str(exc)),
            "delivery": getattr(exc, "delivery", None)}


class DialogWatch:
    """Fotografa, a cada 100 ms e só lendo, os diálogos #32770 visíveis das imagens do RUMBA (títulos e textos)."""

    def __init__(self, rumbapy: Any) -> None:
        from rumbapy import _user32

        self.u32 = _user32.RealUser32()
        self.seen: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        keys: set[tuple[int, str]] = set()
        t0 = time.monotonic()
        while not self.stop.is_set():
            try:
                for w in self.u32.top_windows():
                    if w.cls != "#32770" or not w.visible:
                        continue
                    image = self.u32.process_image(w.pid) or ""
                    if image not in ("host access web launcher.exe", "iexplore.exe"):
                        continue
                    if (w.hwnd, w.title) in keys:
                        continue
                    keys.add((w.hwnd, w.title))
                    self.seen.append({"t_s": round(time.monotonic() - t0, 2), "title": w.title, "pid": w.pid,
                                      "texts": self.u32.static_texts(w.hwnd)[:6],
                                      "buttons": [b for _h, b in self.u32.buttons(w.hwnd)]})
            except Exception:
                pass
            time.sleep(0.1)

    def __enter__(self) -> DialogWatch:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(2)


# ------------------------------------------------------------------------------------------- roteiros

def l1(rumbapy: Any, report: dict[str, Any], py32: str) -> None:
    """L1 + L5: abrir minimizado, 1ª tela, campos, ler, mostrar/minimizar com leituras entre eles, fechar."""
    t = timed(report, "open", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["info"] = t.info.to_wire()
    report["repr"] = repr(t)
    s = timed(report, "snapshot_fields", lambda: t.snapshot(fields=True))
    report["cursor"] = list(s.cursor)
    report["geometry"] = [t.rows, t.cols]
    fields = s.fields or ()
    report["fields"] = {"total": len(fields), "input": sum(1 for f in fields if not f.protected),
                        "hidden": sum(1 for f in fields if f.hidden)}
    report["oia"] = s.oia.describe()
    report["menu"] = timed(report, "read", lambda: t.read(14, 26, 24))
    report["window_minimized"] = window(t)
    t.show()
    time.sleep(1.0)
    report["window_shown"] = window(t)
    report["read_while_shown"] = t.read(14, 26, 24)
    t.minimize()
    time.sleep(1.0)
    report["window_minimized_again"] = window(t)
    report["read_while_minimized"] = t.read(14, 26, 24)
    times = []
    for _ in range(20):
        t1 = time.perf_counter()
        t.snapshot()
        times.append((time.perf_counter() - t1) * 1000)
    times.sort()
    report["snapshot_ms"] = {"min": round(times[0], 2), "median": round(times[10], 2), "max": round(times[-1], 2)}
    with DialogWatch(rumbapy) as watch:
        timed(report, "close", t.close)
    report["dialogs_during_close"] = watch.seen
    report["state_after_close"] = t.state


def l2(rumbapy: Any, report: dict[str, Any], py32: str) -> None:
    """L2: 2ª instância do mesmo perfil aberta "à mão" (launcher direto) -> diálogo Short Name -> open fecha as duas."""
    from rumbapy import _user32

    u32 = _user32.RealUser32()
    t = timed(report, "open_1", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["first"] = t.info.to_wire()
    t.release()
    url = "hawlauncher://" + PORTAL.format(profile="BRAZIL_D") + "/microfocus_w2h_hawlauncher"
    with DialogWatch(rumbapy) as watch:
        second = subprocess.Popen([LAUNCHER, url])
        report["second_launcher_pid"] = second.pid
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not watch.seen:
            time.sleep(0.5)
        time.sleep(3.0)
        report["dialogs_after_second"] = list(watch.seen)
        report["windows_before_open"] = [
            {"title": w.title, "cls": w.cls, "pid": w.pid, "visible": w.visible}
            for w in u32.top_windows() if w.visible and ("BRAZIL_D" in w.title or w.cls == "#32770")
            and (u32.process_image(w.pid) or "") == "host access web launcher.exe"]
        t2 = timed(report, "open_2", lambda: rumbapy.open("D", window="minimized", timeout=180, python32=py32))
        report["dialogs_until_open_2"] = list(watch.seen)
    report["second_open"] = t2.info.to_wire()
    report["menu"] = t2.read(14, 26, 24)
    report["windows_after_open_2"] = profile_windows("BRAZIL_D")
    try:
        report["second_launcher_exit"] = second.wait(5)
    except subprocess.TimeoutExpired:
        report["second_launcher_exit"] = "ainda vivo"
    timed(report, "close", t2.close)


CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["LIVE_CODE"])
import rumbapy
out, go = sys.argv[1], sys.argv[2]
rep = {}
try:
    t = rumbapy.attach("D", python32=os.environ["LIVE_PY32"])
    rep["attach"] = "ok"
    rep["read"] = t.read(14, 26, 24)
except Exception as exc:
    rep["attach"] = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
    t = None
open(out, "w", encoding="utf-8").write(json.dumps(rep))
deadline = time.time() + 120
while not os.path.exists(go) and time.time() < deadline:
    time.sleep(0.2)
if t is not None:
    try:
        rep["read_after_close"] = t.read(14, 26, 24)
    except Exception as exc:
        rep["read_after_close"] = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
    rep["state"] = t.state
open(out, "w", encoding="utf-8").write(json.dumps(rep))
'''


def l3(rumbapy: Any, report: dict[str, Any], py32: str, code: Path) -> None:
    """L3: attach -> release -> attach de OUTRO processo; 2º dono -> TerminalInUse; close com dono vivo (evict);
    reciclagem por shutdown e read de novo."""
    t = timed(report, "open", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    hwnd = t.info.hwnd
    t.release()
    work = Path(tempfile.mkdtemp(prefix="rumbapy-l3-"))
    out, go = work / "child.json", work / "go"
    env = dict(os.environ, LIVE_CODE=str(code), LIVE_PY32=py32)
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(out), str(go)], env=env)
    deadline = time.monotonic() + 60
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    report["child_attach"] = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    report["child_pid"] = child.pid
    try:
        rumbapy.attach("D", python32=py32)
        report["second_owner"] = "aceito (ERRO: devia ser TerminalInUse)"
    except rumbapy.RumbaError as exc:
        report["second_owner"] = exc_info(exc)
    report["close_with_live_owner"] = timed(report, "close_with_owner", lambda: rumbapy.close("D"))
    go.write_text("x", encoding="utf-8")
    child.wait(60)
    report["child_after_close"] = json.loads(out.read_text(encoding="utf-8"))
    t2 = timed(report, "reopen", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["reopen"] = t2.info.to_wire()
    backend = getattr(t2, "_backend", None)
    old_pid = getattr(backend, "worker_pid", None)
    backend.shutdown("L3: reciclagem")                 # pela conexão do próprio dono (C.6.3: o dono pode parar)
    time.sleep(2.0)
    try:
        report["read_after_recycle"] = timed(report, "read_after_recycle", lambda: t2.read(14, 26, 24))
        report["state_after_recycle"] = t2.state
    except rumbapy.RumbaError as exc:
        report["read_after_recycle"] = exc_info(exc)
    report["worker_pid_before"] = old_pid
    report["first_hwnd"] = hwnd
    timed(report, "close", t2.close)


def l4(rumbapy: Any, report: dict[str, Any], py32: str) -> None:
    """L4: escrever e apagar no campo desprotegido da 1ª tela (nenhuma AID: o guard text recusaria)."""
    t = timed(report, "open", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["info"] = t.info.to_wire()
    t.wait_text("Select CICS Environment:", 14, 26, timeout=30)
    s = t.snapshot(fields=True)
    field = next(f for f in (s.fields or ()) if not f.protected)
    report["field"] = {"start": list(field.start), "length": field.length, "hidden": field.hidden}
    row, col = field.start
    report["before"] = t.read_field(row, col)
    for value in ("ç", "€", "X"):
        t.fill(row, col, value)
        report[f"after_{value}"] = t.read_field(row, col)
    t.fill(row, col, "")
    report["after_erase"] = t.read_field(row, col)
    try:
        t.fill(row, col, "XY")
        report["too_long"] = "aceito (ERRO)"
    except rumbapy.Truncated as exc:
        report["too_long"] = f"Truncated delivery={exc.delivery}"
    try:
        t.fill(14, 26, "X")
        report["protected"] = "aceito (ERRO)"
    except rumbapy.ProtectedField as exc:
        report["protected"] = f"ProtectedField delivery={exc.delivery}"
    report["oia_after"] = t.snapshot().oia.describe()
    timed(report, "close", t.close)


def l6(rumbapy: Any, report: dict[str, Any], py32: str) -> None:
    """L6: logon CICS com o login do dono (guard login: só texto e ENTER); âncoras; fechar a sessão logada."""
    cfg = configparser.ConfigParser()
    if not cfg.read(REPO / "config.ini", encoding="utf-8") or not cfg.has_section("cics"):
        raise SystemExit("config.ini sem a seção [cics] (id/password)")
    user, password = cfg.get("cics", "id"), cfg.get("cics", "password")
    t = timed(report, "open", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["info"] = t.info.to_wire()
    menu = t.snapshot(fields=True)
    report["menu_anchor"] = menu.get(14, 26, 24)
    try:
        ok = timed(report, "logon", lambda: t.cics.logon(user, password))
        report["logon"] = "ok"
        report["ok_anchor"] = ok.get(5, 20, 6)
        report["line1"] = ok.line(1).strip()[:40]
        report["msg_line"] = ok.get(19, 19, 40).strip()
        report["oia_after_logon"] = ok.oia.describe()
        report["screen_after_logon"] = [ok.line(r) for r in range(1, t.rows + 1)]
    except rumbapy.RumbaError as exc:
        report["logon"] = exc_info(exc)
        if exc.screen is not None:
            report["screen_at_error"] = [exc.screen.line(r) for r in range(1, t.rows + 1)]
    texts = [repr(t), str(report.get("logon")), json.dumps(report, ensure_ascii=False, default=str)]
    with DialogWatch(rumbapy) as watch:
        timed(report, "close", t.close)
    report["dialogs_during_close"] = watch.seen
    home = Path(os.environ["RUMBAPY_HOME"])
    for path in home.rglob("*"):
        if path.is_file() and path.suffix in (".log", ".txt", ".json"):
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    report["password_found_anywhere"] = any(password in text for text in texts)     # tem de ser False


def l9(rumbapy: Any, report: dict[str, Any], py32: str) -> None:
    """L9: com BRAZIL_Z aberto, open("D"): processos do launcher (instância única?) e close de D sem afetar Z."""
    z = timed(report, "open_z", lambda: rumbapy.open("Z", window="minimized", timeout=150, python32=py32))
    report["z"] = z.info.to_wire()
    d = timed(report, "open_d", lambda: rumbapy.open("D", window="minimized", timeout=150, python32=py32))
    report["d"] = d.info.to_wire()
    report["same_launcher_process"] = z.info.hwnd_pid == d.info.hwnd_pid
    report["z_windows"], report["d_windows"] = window(z), window(d)
    timed(report, "close_d", d.close)
    try:
        report["z_read_after_close_d"] = z.read(1, 1, 20)
        report["z_state"] = z.state
    except rumbapy.RumbaError as exc:
        report["z_read_after_close_d"] = exc_info(exc)
    report["windows_after_close_d"] = profile_windows("BRAZIL_D", "BRAZIL_Z")
    timed(report, "close_z", z.close)


def stop_workers(rumbapy: Any, report: dict[str, Any]) -> None:
    from rumbapy import _bridge, _paths

    out = {}
    for letter in ("D", "Z"):
        reg = _paths.read_json(_paths.worker_registry(letter))
        if not (reg and _bridge.registry_alive(reg)):
            continue
        _bridge.get_worker(letter, "admin").shutdown("fim do roteiro ao vivo")
        out[letter] = {"pid": reg.get("pid"), "saiu": _bridge.wait_exit(reg.get("pid"), reg.get("created"), 20)}
    report["workers"] = out or "nenhum vivo"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--script", choices=sorted(GUARDS), required=True)
    args = ap.parse_args()
    if os.environ.get("RUMBAPY_LIVE") != "1":
        raise SystemExit("teste ao vivo: defina RUMBAPY_LIVE=1")
    if os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SystemExit("o sandbox bloqueia o RUMBA real: rode sem RUMBAPY_SANDBOX")
    if struct.calcsize("P") != 8:
        raise SystemExit("rode com o Python 64-bit")
    if profile_windows("BRAZIL_D", "BRAZIL_Z"):
        raise SystemExit("já há janela de BRAZIL_D/BRAZIL_Z aberta: não mexo na sessão de ninguém")
    home = Path(tempfile.mkdtemp(prefix="rumbapy-live2-"))
    os.environ.update({"RUMBAPY_DEV": "1", "RUMBAPY_HOME": str(home), "RUMBAPY_GUARD": GUARDS[args.script],
                       "RUMBAPY_LOG_LEVEL": "DEBUG"})
    code = snapshot_code(home / "code")
    sys.path.insert(0, str(code))
    report: dict[str, Any] = {"script": args.script, "guard": GUARDS[args.script], "home": str(home)}
    import rumbapy

    report["version"] = rumbapy.__version__
    py32 = python32()
    try:
        if args.script == "l3":
            l3(rumbapy, report, py32, code)
        else:
            {"l1": l1, "l2": l2, "l4": l4, "l6": l6, "l9": l9}[args.script](rumbapy, report, py32)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
        report["traceback"] = traceback.format_exc().splitlines()[-12:]
        for letter in ("D", "Z"):
            try:
                report[f"cleanup_close_{letter}"] = rumbapy.close(letter)
            except Exception as again:
                report[f"cleanup_close_{letter}"] = f"{type(again).__name__}: {again}"
    finally:
        try:
            stop_workers(rumbapy, report)
        except Exception as exc:
            report["workers"] = f"{type(exc).__name__}: {exc}"
        time.sleep(2.0)
        report["windows_left"] = profile_windows("BRAZIL_D", "BRAZIL_Z")
    out = REPO / "docs" / "interno" / "referencia" / f"live_v2_{args.script}_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    shown = {k: v for k, v in report.items() if k not in ("screen_after_logon", "screen_at_error")}
    print(json.dumps(shown, indent=2, ensure_ascii=False, default=str))
    print("relatório:", out)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
