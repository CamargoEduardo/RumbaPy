"""Sonda AO VIVO e SOMENTE LEITURA do worker 32-bit da v2 contra a ehlapi32.Dll real.

Rode com o Python 64-bit do repositório (ele sobe o worker no Python 32-bit do ``.venv32``):
    set RUMBAPY_LIVE=1
    .venv\\Scripts\\python.exe tools\\research\\live_worker_probe.py [--profile BRAZIL_D] [--letter D] [--keep-open]

O que faz (nenhuma tecla é enviada ao host e nada é digitado):
  1. Recusa rodar se já houver uma janela do perfil aberta (não mexe na sessão de ninguém).
  2. Exporta o código do ``HEAD`` (``git archive``) para uma pasta temporária: edições em andamento na árvore não
     afetam o teste, e o worker roda exatamente o que está commitado.
  3. Sobe o worker 32-bit com a DLL real e ``--guard readonly`` (o GuardedDll recusa SendKey/CopyStringToPS/SetCursor
     e as funções sempre bloqueadas antes de chegar à DLL).
  4. Lança o "Host Access Web Launcher.exe" com o perfil, espera a letra em ``sessions``, faz ``connect`` (NORESET),
     ``status``, ``snapshot`` com campos até a 1ª tela, ``read``, ``read_field`` e mede a latência do ``snapshot``.
  5. Com ``--write-erase`` (guard ``text``: texto liberado, TODA AID e tecla local bloqueadas): ``write`` recusado
     em posição protegida e com texto maior que o campo (nenhuma cópia), escreve ``X`` no campo do cursor, confere,
     apaga com espaços e confere. Nada vai ao host: 3270 só transmite com uma AID, e nenhuma é enviada.
  6. ``disconnect``, ``shutdown`` (admin) e fecha as janelas do launcher com WM_CLOSE (a menos que ``--keep-open``).

O relatório completo (com a tela) vai para ``docs/interno/referencia/`` (fora do Git); no console sai um resumo.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import ctypes.wintypes as wt
import importlib
import subprocess
import tempfile
import tarfile
import ctypes
import struct
import types
import json
import time
import sys
import io
import os

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
PORTAL = "https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/{profile}.html"
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windows() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            found.append({"hwnd": hwnd, "pid": pid.value, "title": title.value})
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return found


def profile_windows(profile: str) -> list[dict[str, Any]]:
    head, ie = f"{profile} - Pro Client", f" - {profile} - Pro Client"
    return [w for w in windows() if w["title"].startswith(head) or ie in w["title"]]


def snapshot_code(dest: Path) -> Path:
    paths = ["src/rumbapy", "tests/__init__.py", "tests/unit/__init__.py", "tests/unit/worker/__init__.py",
             "tests/unit/worker/helpers.py"]
    data = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "HEAD", *paths],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def load_helpers(code: Path) -> Any:
    pkg = types.ModuleType("rumbapy")                    # sem o __init__ (auto-update): como o BOOT do worker
    pkg.__path__ = [str(code / "src" / "rumbapy")]
    pkg.__file__ = str(code / "src" / "rumbapy" / "__init__.py")
    sys.modules["rumbapy"] = pkg
    sys.path.insert(0, str(code))
    return importlib.import_module("tests.unit.worker.helpers")


def python32() -> str:
    for line in (REPO / ".venv32" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return str(Path(value.strip()) / "python.exe")
    raise SystemExit("não achei o Python 32-bit do .venv32")


def until(fn: Any, timeout: float, step: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(step)
    return None


def write_erase(c: Any, hwnd: int | None, snap: dict[str, Any]) -> dict[str, Any]:
    """Escreve e apaga no campo de entrada do cursor; erros esperados conferidos antes (nunca uma AID)."""
    row, col = snap["cursor"]
    out: dict[str, Any] = {"at": [row, col]}
    field = c.result("read_field", {"row": row, "col": col}, hwnd=hwnd)
    out["field"] = {k: field[k] for k in ("start", "length", "attr")}
    if field["attr"] & 0x20 or field["length"] < 1:
        out["skipped"] = "o cursor não está num campo de entrada"
        return out
    start = tuple(field["start"])

    def err(reply: dict[str, Any]) -> Any:
        return (reply.get("error") or {}).get("type") if not reply.get("ok") else "ok"

    protected = next(([f["start"][0], f["start"][1]] for f in snap.get("fields") or []
                      if f["attr"] & 0x20 and f["length"] > 0), None)
    if protected:
        out["protected"] = {"at": protected, "result": err(c.call("write", {"row": protected[0], "col": protected[1],
                                                                            "text": "X", "secret": False}, hwnd=hwnd))}
    out["too_long"] = err(c.call("write", {"row": start[0], "col": start[1], "text": "X" * (field["length"] + 1),
                                           "secret": False}, hwnd=hwnd))
    out["write"] = err(c.call("write", {"row": start[0], "col": start[1], "text": "X", "secret": False}, hwnd=hwnd))
    out["after_write"] = c.result("read_field", {"row": row, "col": col}, hwnd=hwnd)["text"]
    out["erase"] = err(c.call("write", {"row": start[0], "col": start[1], "text": " " * field["length"],
                                        "secret": False}, hwnd=hwnd))
    out["after_erase"] = c.result("read_field", {"row": row, "col": col}, hwnd=hwnd)["text"]
    out["oia_after"] = c.result("snapshot", {"fields": False}, hwnd=hwnd)["oia"]
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="BRAZIL_D")
    ap.add_argument("--letter", default="D")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--write-erase", action="store_true", help="escreve 'X' no campo do cursor e apaga (sem AID)")
    args = ap.parse_args()
    if os.environ.get("RUMBAPY_LIVE") != "1":
        raise SystemExit("teste ao vivo: defina RUMBAPY_LIVE=1")
    if os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SystemExit("o sandbox bloqueia a DLL real: rode sem RUMBAPY_SANDBOX")
    if struct.calcsize("P") != 8:
        raise SystemExit("rode com o Python 64-bit (o worker 32-bit sobe sozinho)")
    already = profile_windows(args.profile)
    if already:
        raise SystemExit(f"já há {len(already)} janela(s) de {args.profile} aberta(s): não mexo na sessão de ninguém")

    home = Path(tempfile.mkdtemp(prefix="rumbapy-live-"))
    helpers = load_helpers(snapshot_code(home / "code"))
    report: dict[str, Any] = {"profile": args.profile, "letter": args.letter, "home": str(home)}
    argv, meta = helpers.worker_argv(python32(), home, letter=args.letter, fake=False, idle=300.0,
                                     extra=["--guard", "text" if args.write_erase else "readonly"])
    key = helpers.write_key(meta["keyfile"])
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    env["RUMBAPY_HOME"] = str(home)
    t_boot = time.monotonic()
    proc = helpers.run_worker(argv, home, env=env)
    worker = helpers.Worker(proc, meta["pipe"], meta["registry"], key, meta["log"], proc.boot_path,
                            meta["stop_event"], meta["build_id"], args.letter)
    reg = until(lambda: (lambda r: r if r and r.get("state") == "listening" else None)(
        helpers._paths.read_json(meta["registry"])) if proc.poll() is None else {"exit": proc.returncode}, 30, 0.05)
    report["worker_boot_s"] = round(time.monotonic() - t_boot, 2)
    if not reg or "exit" in reg:
        report["worker_boot_failed"] = reg
        report["worker_log"] = worker.log_text()[-4000:]
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 2
    worker.reg = reg
    launcher = None
    try:
        with helpers.WireClient(worker, letter=args.letter, script="live_worker_probe.py") as c:
            report["hello"] = {k: c.hello.get(k) for k in ("protocol", "version", "guard", "state")}
            report["sessions_before"] = c.result("sessions")["sessions"]
            if any(s.get("letter") == args.letter for s in report["sessions_before"]):
                raise RuntimeError(f"a letra {args.letter} já está registrada por outra sessão: abortando")
            url = "hawlauncher://" + PORTAL.format(profile=args.profile) + "/microfocus_w2h_hawlauncher"
            t0 = time.monotonic()
            launcher = subprocess.Popen([LAUNCHER, url])
            report["launcher_pid"] = launcher.pid
            entry = until(lambda: next((s for s in c.result("sessions")["sessions"]
                                        if s.get("letter") == args.letter), None), 120)
            report["registered_s"] = round(time.monotonic() - t0, 1)
            report["registered"] = entry
            if not entry:
                raise RuntimeError("a letra não apareceu em 120 s")
            conn = until(lambda: (lambda r: r if r.get("connected") else None)(
                c.result("connect", {"letter": args.letter})), 90)
            report["connected_s"] = round(time.monotonic() - t0, 1)
            report["connect"] = conn
            if not conn:
                raise RuntimeError("connect não completou em 90 s")
            hwnd = conn.get("hwnd")
            report["status"] = c.result("status", hwnd=hwnd)

            def first_screen() -> Any:
                snap = c.result("snapshot", {"fields": True}, hwnd=hwnd)
                return snap if not snap["busy"] and snap["text"].strip() else None

            snap = until(first_screen, 60)
            report["first_screen_s"] = round(time.monotonic() - t0, 1)
            if not snap:
                raise RuntimeError("a 1ª tela não chegou em 60 s")
            cols = conn["cols"]
            text = snap["text"]
            report["screen"] = [text[i:i + cols].rstrip() for i in range(0, len(text), cols)]
            report["cursor"] = snap["cursor"]
            report["oia"] = snap["oia"]
            report["hidden_unknown"] = snap["hidden_unknown"]
            fields = snap.get("fields") or []
            report["fields"] = fields
            report["fields_summary"] = {"total": len(fields),
                                        "input": sum(1 for f in fields if not f["attr"] & 0x20),
                                        "hidden": sum(1 for f in fields if f["attr"] & 0x0C == 0x0C)}
            report["read_row1"] = c.result("read", {"row": 1, "col": 1, "length": cols}, hwnd=hwnd)
            row, col = snap["cursor"]
            report["read_field_at_cursor"] = c.result("read_field", {"row": row, "col": col}, hwnd=hwnd)
            times = []
            for _ in range(20):
                t1 = time.perf_counter()
                c.result("snapshot", {"fields": False}, hwnd=hwnd)
                times.append((time.perf_counter() - t1) * 1000)
            times.sort()
            report["snapshot_ms"] = {"min": round(times[0], 2), "median": round(times[10], 2),
                                     "max": round(times[-1], 2)}
            if args.write_erase:
                report["write_erase"] = write_erase(c, hwnd, snap)
            report["disconnect"] = c.result("disconnect", hwnd=hwnd)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["worker_exit"] = worker.stop()
        if launcher is not None and not args.keep_open:
            for w in profile_windows(args.profile):
                user32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
            try:
                report["launcher_exit"] = launcher.wait(30)
            except subprocess.TimeoutExpired:
                report["launcher_exit"] = None
            report["windows_left"] = [w["title"] for w in profile_windows(args.profile)]
        log = worker.log_text()
        report["worker_log_tail"] = log.splitlines()[-60:]
    out = REPO / "docs" / "interno" / "referencia" / f"live_worker_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("screen", "fields", "worker_log_tail", "read_row1",
                                                            "read_field_at_cursor")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("relatório completo:", out)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
