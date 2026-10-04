"""Sonda AO VIVO do fechamento (E.4): quais janelas a instância tem e quando a DLL volta a responder.

Rode com o Python 64-bit do repositório (``RUMBAPY_LIVE=1``). Código do ``HEAD`` exportado, ``RUMBAPY_DEV=1``,
``RUMBAPY_HOME`` temporário, ``RUMBAPY_GUARD=readonly`` (nenhuma tecla vai ao host). Passos:
  1. ``rumbapy.open("D", window="minimized")``; lista TODAS as janelas de nível superior do PID do launcher
     (título, classe, visível, dono) — só leitura.
  2. ``evict`` pela conexão admin (o worker faz ``WD_DisconnectPS``).
  3. ``WM_CLOSE`` só na janela principal (raiz do HWND do HLLAPI) e acompanhamento SEM chamar a DLL: janela some?
     processo do launcher sai? (``IsWindow``/``process_alive`` a cada 100 ms).
  4. Só depois de o processo sair (ou de 30 s): ``sessions`` (``WD_QuerySessions``) cronometrado.
  5. ``rumbapy.close("D")`` de limpeza (se sobrou algo), ``shutdown`` do worker, conferência final.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess
import traceback
import tempfile
import tarfile
import struct
import json
import time
import sys
import io
import os

REPO = Path(__file__).resolve().parents[2]
WM_CLOSE = 0x0010


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


def main() -> int:
    if os.environ.get("RUMBAPY_LIVE") != "1" or os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SystemExit("teste ao vivo: RUMBAPY_LIVE=1 e sem RUMBAPY_SANDBOX")
    if struct.calcsize("P") != 8:
        raise SystemExit("rode com o Python 64-bit")
    home = Path(tempfile.mkdtemp(prefix="rumbapy-close-"))
    os.environ.update({"RUMBAPY_DEV": "1", "RUMBAPY_HOME": str(home), "RUMBAPY_GUARD": "readonly",
                       "RUMBAPY_LOG_LEVEL": "DEBUG"})
    sys.path.insert(0, str(snapshot_code(home / "code")))
    import rumbapy
    from rumbapy import _bridge, _paths, _user32, _win32

    u32 = _user32.RealUser32()
    if any("BRAZIL_D - Pro Client" in w.title for w in u32.top_windows() if w.visible):
        raise SystemExit("já há janela do BRAZIL_D aberta: não mexo na sessão de ninguém")
    report: dict[str, Any] = {"home": str(home)}
    try:
        t0 = time.monotonic()
        t = rumbapy.open("D", window="minimized", timeout=150, python32=python32())
        report["open_s"] = round(time.monotonic() - t0, 1)
        report["info"] = t.info.to_wire()
        pid = t.info.hwnd_pid
        main_hwnd = u32.root_of(t.info.hwnd or 0)
        report["main_hwnd"] = main_hwnd
        report["windows_of_launcher"] = [
            {"hwnd": w.hwnd, "title": w.title, "cls": w.cls, "visible": w.visible, "iconic": w.iconic,
             "owner": w.owner, "is_main": w.hwnd == main_hwnd}
            for w in u32.top_windows() if w.pid == pid]
        report["windows_with_profile_name"] = [
            {"pid": w.pid, "title": w.title, "cls": w.cls, "visible": w.visible}
            for w in u32.top_windows() if "BRAZIL_D" in w.title]
        t.release()
        admin = _bridge.get_worker("D", "admin")
        report["evict"] = admin.evict("teste de fechamento")
        created = _win32.process_created(pid) if pid else None
        t1 = time.monotonic()
        u32.post_close(main_hwnd)
        window_gone = process_gone = None
        timeline = []
        while time.monotonic() - t1 < 30:
            now = round(time.monotonic() - t1, 2)
            alive_w = u32.is_window(main_hwnd)
            alive_p = _win32.process_alive(pid, created) if pid else False
            others = [w.title for w in u32.top_windows() if w.pid == pid and w.visible]
            if not timeline or timeline[-1][1:] != (alive_w, alive_p, others):
                timeline.append((now, alive_w, alive_p, others))
            if window_gone is None and not alive_w:
                window_gone = now
            if not alive_p:
                process_gone = now
                break
            time.sleep(0.1)
        report["after_wm_close"] = {"window_gone_s": window_gone, "process_gone_s": process_gone,
                                    "timeline": timeline}
        t2 = time.monotonic()
        entries = admin.sessions()
        report["sessions_after_exit"] = {"ms": round((time.monotonic() - t2) * 1000, 1),
                                         "letters": [e.letter for e in entries]}
        admin.release()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc().splitlines()[-12:]
    finally:
        try:
            if any("BRAZIL_D - Pro Client" in w.title for w in u32.top_windows() if w.visible):
                report["cleanup_close"] = rumbapy.close("D")
        except Exception as exc:
            report["cleanup_close"] = f"{type(exc).__name__}: {exc}"
        try:
            reg = _paths.read_json(_paths.worker_registry("D"))
            if reg and _bridge.registry_alive(reg):
                _bridge.get_worker("D", "admin").shutdown("fim da sonda de fechamento")
                report["worker_exit"] = _bridge.wait_exit(reg.get("pid"), reg.get("created"), 20)
        except Exception as exc:
            report["worker_exit"] = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
        report["windows_left"] = [w.title for w in u32.top_windows() if w.visible and "BRAZIL_D" in w.title]
        log = next(iter(sorted((home / "logs").glob("worker-*.log"))), None)
        if log is not None:
            report["worker_log_tail"] = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()
                                         if "op=snapshot" not in ln][-25:]
    out = REPO / "docs" / "interno" / "referencia" / f"live_close_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
