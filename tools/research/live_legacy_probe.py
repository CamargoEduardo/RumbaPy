"""Sonda AO VIVO do L8 (E.8): a v2 neutraliza um servidor 0.1.5 REAL (código do ``master``) rodando em 32-bit.

Pré-requisito (fora do repositório): um venv 32-bit com ``pywin32`` e ``pywinauto`` e o código da 0.1.5 exportado:
    py -3.13-32 -m venv %TEMP%\\rumbapy-l8\\venv32 && %TEMP%\\rumbapy-l8\\venv32\\Scripts\\pip install pywin32 pywinauto
    git archive --format=tar master src/rumbapy | tar -x -C %TEMP%\\rumbapy-l8

Rode com o Python 64-bit do repositório (``RUMBAPY_LIVE=1``). Nenhum terminal é aberto: o servidor 0.1.5 só carrega a
DLL e, ao receber ``disconnect``, chama ``WD_DisconnectPS`` (rc 1, nada conectado). Ele roda num invólucro que sai por
``os._exit(0)`` (ExitProcess: o DllMain libera o slot HLLAPI) quando o arquivo de parada aparece. Recusa rodar se a
porta 8500 já tiver um ouvinte (pode ser um servidor 0.1.5 de produção do usuário).
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import subprocess
import tempfile
import tarfile
import socket
import json
import time
import sys
import io
import os

REPO = Path(__file__).resolve().parents[2]
L8 = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "rumbapy-l8"
WRAPPER = r'''
import logging, os, pathlib, sys, threading, time
stop, log = pathlib.Path(sys.argv[1]), sys.argv[2]
logging.basicConfig(filename=log, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s %(message)s")
sys.argv = ["rumbapy.server", "--terminal_type", "D"]
from rumbapy import server
threading.Thread(target=server.start_server, daemon=True).start()
deadline = time.time() + 180
while not stop.exists() and time.time() < deadline:
    time.sleep(0.2)
logging.getLogger("l8").info("saindo por os._exit(0)")
logging.shutdown()
os._exit(0)
'''


def snapshot_code(dest: Path) -> Path:
    data = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "HEAD", "src/rumbapy"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "src"


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def main() -> int:
    if os.environ.get("RUMBAPY_LIVE") != "1" or os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SystemExit("teste ao vivo: RUMBAPY_LIVE=1 e sem RUMBAPY_SANDBOX")
    py32 = L8 / "venv32" / "Scripts" / "python.exe"
    if not py32.is_file() or not (L8 / "src" / "rumbapy" / "server.py").is_file():
        raise SystemExit(f"prepare {L8} (veja o docstring)")
    if port_open(8500):
        raise SystemExit("a porta 8500 já tem um ouvinte (servidor 0.1.5 do usuário?): não mexo")
    work = Path(tempfile.mkdtemp(prefix="rumbapy-l8run-"))
    stop, log = work / "stop", work / "legacy.log"
    (work / "wrapper.py").write_text(WRAPPER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    env["PYTHONPATH"] = str(L8 / "src")
    proc = subprocess.Popen([str(py32), str(work / "wrapper.py"), str(stop), str(log)], env=env, cwd=str(work),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000)
    report: dict[str, Any] = {"legacy_pid": proc.pid}
    try:
        deadline = time.monotonic() + 20
        while not port_open(8500) and time.monotonic() < deadline:
            time.sleep(0.2)
        report["listening"] = port_open(8500)
        home = Path(tempfile.mkdtemp(prefix="rumbapy-l8home-"))
        os.environ.update({"RUMBAPY_DEV": "1", "RUMBAPY_HOME": str(home)})
        sys.path.insert(0, str(snapshot_code(home / "code")))
        from rumbapy import _legacy, _win32

        report["listener_pids"] = _win32.tcp_listener_pids(8500)
        t0 = time.monotonic()
        status = _legacy.neutralize("D")
        report["neutralize_ms"] = round((time.monotonic() - t0) * 1000, 1)
        report["status"] = {k: getattr(status, k) for k in dir(status) if not k.startswith("_")
                            and not callable(getattr(status, k))}
        report["still_listening"] = port_open(8500)
    finally:
        stop.write_text("x", encoding="utf-8")
        try:
            report["legacy_exit"] = proc.wait(20)
        except subprocess.TimeoutExpired:
            report["legacy_exit"] = "não saiu em 20 s (NÃO foi morto; encerre-o à mão)"
        try:
            report["legacy_log"] = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        except OSError:
            report["legacy_log"] = []
    out = REPO / "docs" / "interno" / "referencia" / f"live_l8_{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
