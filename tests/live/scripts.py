"""Roteiros ao vivo da J.6 (lógica validada ao vivo em 2026-10-03/04 pelas sondas de ``tools/research``).

Só rodam dentro do executor (``tests/live/runner.py``), num processo novo por roteiro, com ``RUMBAPY_HOME``
temporário, ``RUMBAPY_DEV=1`` e o ``RUMBAPY_GUARD`` do roteiro já definidos e o ``rumbapy`` importado da cópia do
código testado. Cada função recebe o ``Context`` e preenche ``ctx.report``; a limpeza (close, shutdown dos workers,
conferência das janelas) e a varredura da senha ficam no executor.
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.live.support import L4_CHARS, L4_TEXT, Credentials

import subprocess
import threading
import tempfile
import json
import time
import sys
import os


@dataclass
class Context:
    rumbapy: Any
    py32: str
    code: Path
    home: Path
    credentials: Credentials | None = None
    report: dict[str, Any] = field(default_factory=dict)
    texts: list[str] = field(default_factory=list)       # repr/exceções a varrer em busca da senha


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


def open_d(ctx: Context, name: str = "open", letter: str = "D") -> Any:
    return timed(ctx.report, name, lambda: ctx.rumbapy.open(letter, window="minimized", timeout=150,
                                                             python32=ctx.py32))


def profile_titles(ctx: Context, *letters: str) -> list[str]:
    from tests.live.support import terminal_windows

    return terminal_windows(letters)


class DialogWatch:
    """Fotografa, a cada 100 ms e só lendo, os diálogos #32770 visíveis das imagens do RUMBA (títulos e textos)."""

    def __init__(self) -> None:
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


# ------------------------------------------------------------------------------------------- L1 e L5

def l1(ctx: Context) -> None:
    """L1: abrir minimizado, 1ª tela, campos, cursor, leitura, tempo do snapshot, fechar (0 instâncias)."""
    report = ctx.report
    t = open_d(ctx)
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
    times = []
    for _ in range(20):
        t1 = time.perf_counter()
        t.snapshot()
        times.append((time.perf_counter() - t1) * 1000)
    times.sort()
    report["snapshot_ms"] = {"min": round(times[0], 2), "median": round(times[10], 2), "max": round(times[-1], 2)}
    with DialogWatch() as watch:
        timed(report, "close", t.close)
    report["dialogs_during_close"] = watch.seen
    report["state_after_close"] = t.state
    report["windows_after_close"] = profile_titles(ctx, "D")


def l5(ctx: Context) -> None:
    """L5: show/minimize com leituras entre eles; título da janela em cada estado."""
    report = ctx.report
    t = open_d(ctx)
    report["info"] = t.info.to_wire()
    report["window_minimized"] = window(t)
    t.show()
    time.sleep(1.0)
    report["window_shown"] = window(t)
    report["read_while_shown"] = t.read(14, 26, 24)
    t.minimize()
    time.sleep(1.0)
    report["window_minimized_again"] = window(t)
    report["read_while_minimized"] = t.read(14, 26, 24)
    timed(report, "close", t.close)
    report["state_after_close"] = t.state


# ------------------------------------------------------------------------------------------- L2

def l2(ctx: Context) -> None:
    """L2: 2ª instância do mesmo perfil aberta "à mão" (launcher direto) -> diálogo Short Name -> open fecha as duas."""
    from rumbapy import _launcher, _user32, profiles

    report = ctx.report
    u32 = _user32.RealUser32()
    exe = _launcher.default_find_launcher()            # registro (hawlauncher) ou o caminho padrão
    if exe is None:
        raise RuntimeError("não achei o Host Access Web Launcher")
    url = profiles.launcher_arg(profiles.resolve("D"))  # fonte única do perfil/portal (E.1; respeita RUMBAPY_PORTAL)
    t = open_d(ctx, "open_1")
    report["first"] = t.info.to_wire()
    t.release()
    with DialogWatch() as watch:
        second = subprocess.Popen([exe, url])
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
        t2 = timed(report, "open_2", lambda: ctx.rumbapy.open("D", window="minimized", timeout=180,
                                                               python32=ctx.py32))
        report["dialogs_until_open_2"] = list(watch.seen)
    report["second_open"] = t2.info.to_wire()
    report["menu"] = t2.read(14, 26, 24)
    report["windows_after_open_2"] = profile_titles(ctx, "D")
    try:
        report["second_launcher_exit"] = second.wait(5)
    except subprocess.TimeoutExpired:
        report["second_launcher_exit"] = "ainda vivo"
    timed(report, "close", t2.close)


# ------------------------------------------------------------------------------------------- L3

def read_json_file(path: Path, wait_s: float) -> Any:
    """JSON gravado por um processo filho (``os.replace``); tolera, até o prazo, arquivo ausente ou pela metade."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
        time.sleep(0.2)


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
def save():
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rep))
    os.replace(tmp, out)   # o pai nunca vê o arquivo pela metade
save()
deadline = time.time() + 120
while not os.path.exists(go) and time.time() < deadline:
    time.sleep(0.2)
if t is not None:
    try:
        rep["read_after_close"] = t.read(14, 26, 24)
    except Exception as exc:
        rep["read_after_close"] = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
    rep["state"] = t.state
save()
'''


def l3(ctx: Context) -> None:
    """L3: attach -> release -> attach de OUTRO processo; 2º dono -> TerminalInUse; close com dono vivo (evict);
    reciclagem por shutdown e read de novo."""
    rumbapy, report = ctx.rumbapy, ctx.report
    t = open_d(ctx)
    hwnd = t.info.hwnd
    t.release()
    work = Path(tempfile.mkdtemp(prefix="l3-", dir=ctx.home))   # apagado com o RUMBAPY_HOME no fim
    out, go = work / "child.json", work / "go"
    env = dict(os.environ, LIVE_CODE=str(ctx.code), LIVE_PY32=ctx.py32)
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(out), str(go)], env=env)
    report["child_attach"] = read_json_file(out, 60)
    report["child_pid"] = child.pid
    try:
        rumbapy.attach("D", python32=ctx.py32)
        report["second_owner"] = "aceito (ERRO: devia ser TerminalInUse)"
    except rumbapy.RumbaError as exc:
        report["second_owner"] = exc_info(exc)
    report["close_with_live_owner"] = timed(report, "close_with_owner", lambda: rumbapy.close("D"))
    go.write_text("x", encoding="utf-8")
    child.wait(60)
    report["child_after_close"] = read_json_file(out, 5)
    t2 = open_d(ctx, "reopen")
    report["reopen"] = t2.info.to_wire()
    backend: Any = getattr(t2, "_backend")
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


# ------------------------------------------------------------------------------------------- L3b

# Processo 32-bit que faz o papel do worker que o watchdog derruba: carrega a MESMA DLL pelo hllapi do pacote, SEMPRE
# embrulhada no GuardedDll(readonly), faz só ConnectPS (+ NORESET, como o worker) e sai por os._exit(3) SEM
# DisconnectPS/ResetSystem (ExitProcess: o DllMain ainda roda; nunca TerminateProcess). Carrega o pacote como o BOOT
# do worker (D.1): sem rumbapy/__init__.py.
HELPER = r'''
import json, os, sys, threading, time, types
d, out, limit = sys.argv[1], sys.argv[2], float(sys.argv[3])
m = types.ModuleType("rumbapy"); m.__path__ = [d]; m.__file__ = os.path.join(d, "__init__.py")
sys.modules["rumbapy"] = m
from rumbapy._worker import hllapi
rep = {"pid": os.getpid()}
def save():
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rep))
    os.replace(tmp, out)
def watchdog():
    # a DLL pode esperar sem prazo (WaitForSingleObject INFINITE): sai por os._exit(9) (ExitProcess)
    time.sleep(limit)
    rep["watchdog"] = "travou"
    try:
        save()
    finally:
        os._exit(9)
threading.Thread(target=watchdog, daemon=True).start()
try:
    dll = hllapi.load_dll(hllapi.DLL_PATH, guard="readonly")
    h = hllapi.Hllapi(dll)
    t0 = time.perf_counter()
    rc = h.connect("D")
    rep["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rep["connect_rc"] = rc
    if rc == 0:
        rep["noreset_rc"] = h.set_param(b"NORESET")
    rep["guard_audit"] = [str(e) for e in getattr(getattr(dll, "policy", None), "audit", [])][:50]
except Exception as exc:
    rep["error"] = f"{type(exc).__name__}: {exc}"
save()
time.sleep(0.5)
os._exit(3)
'''


HELPER_WATCHDOG_S = 20          # prazo do processo de teste do L3b para as chamadas à DLL (sai por os._exit(9))
HELPER_WATCHDOG_EXIT = 9


def worker_reg(letter: str = "D") -> tuple[int | None, Any]:
    """(PID, created) do worker da letra pelo registro (D.7); ``(None, None)`` sem registro."""
    from rumbapy import _paths

    reg = _paths.read_json(_paths.worker_registry(letter))
    return (reg.get("pid"), reg.get("created")) if isinstance(reg, dict) else (None, None)


def l3b(ctx: Context) -> None:
    """L3b (R8, K-R6): uma conexão HLLAPI do D sai por os._exit(3) sem DisconnectPS (como o watchdog); mede rc e
    tempo do ConnectPS do worker novo (``op=connect`` do log) e lê. Só conexão local: nenhuma tecla, nenhuma escrita.

    O protocolo não tem op que faça o worker de produção sair por os._exit(3) (de propósito: só o watchdog), então o
    processo que sai é um 32-bit próprio do roteiro, com a mesma DLL e o mesmo hllapi.py do worker."""
    from rumbapy import _bridge

    rumbapy, report = ctx.rumbapy, ctx.report
    t = open_d(ctx)
    report["info"] = t.info.to_wire()
    first, created = worker_reg()
    report["worker_pid_before"] = first
    if first is None:
        raise RuntimeError("registro do worker não encontrado: o L3b não segue sem saber se a conexão anterior saiu")
    t.release()
    admin = _bridge.get_worker("D", "admin")           # sem dono: o worker conectado para (DisconnectPS + Reset)
    admin.shutdown("L3b: o processo de teste assume a conexão")
    try:
        admin.release()
    except Exception:
        pass
    report["worker_before_exited"] = _bridge.wait_exit(first, created, 20) if first is not None else None
    if report["worker_before_exited"] is False:
        raise RuntimeError("o worker anterior não saiu: o L3b não segue com duas conexões")
    work = Path(tempfile.mkdtemp(prefix="l3b-", dir=ctx.home))
    out = work / "helper.json"
    proc = subprocess.Popen([ctx.py32, "-I", "-S", "-c", HELPER, str(Path(ctx.code) / "rumbapy"), str(out),
                             str(HELPER_WATCHDOG_S)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        code: Any = proc.wait(HELPER_WATCHDOG_S + 40)
    except subprocess.TimeoutExpired:
        code = f"não saiu em {HELPER_WATCHDOG_S + 40} s (NÃO foi morto; confira à mão)"
    helper = read_json_file(out, 5) or {}
    helper["exit"] = code
    report["helper"] = helper
    if code == HELPER_WATCHDOG_EXIT:
        raise RuntimeError(f"uma chamada à DLL do processo de teste travou por {HELPER_WATCHDOG_S} s: o watchdog "
                           "dele saiu por os._exit(9) (o L3b não mediu nada)")
    if code != 3:
        raise RuntimeError("o processo de teste não saiu por os._exit(3)")
    t0 = time.monotonic()
    t2 = timed(report, "attach_after_exit", lambda: rumbapy.attach("D", timeout=60, python32=ctx.py32))
    report["read_after_exit"] = timed(report, "read_after_exit", lambda: t2.read(14, 26, 24))
    report["s_until_read"] = round(time.monotonic() - t0, 2)
    report["worker_pid_after"] = worker_reg()[0]
    from tests.live import support

    records = support.connect_records(support.guard_audit_lines(ctx.home))
    report["new_worker_connects"] = [r for r in records if r["pid"] not in (None, first)]
    timed(report, "close", t2.close)


# ------------------------------------------------------------------------------------------- L4

def l4(ctx: Context) -> None:
    """L4: escrever e apagar no campo desprotegido da 1ª tela (nenhuma AID: o guard text recusaria)."""
    rumbapy, report = ctx.rumbapy, ctx.report
    t = open_d(ctx)
    report["info"] = t.info.to_wire()
    t.wait_text("Select CICS Environment:", 14, 26, timeout=30)
    s = t.snapshot(fields=True)
    unprotected = next(f for f in (s.fields or ()) if not f.protected)
    report["field"] = {"start": list(unprotected.start), "length": unprotected.length,
                       "hidden": unprotected.hidden}
    row, col = unprotected.start
    report["before"] = t.read_field(row, col)
    for value in L4_CHARS:                              # cada caractere de "TESTE çãÉ€" vai e volta pelo PS local
        t.fill(row, col, value)
        report[f"after_{value}"] = t.read_field(row, col)
    if len(L4_TEXT) <= unprotected.length:
        t.fill(row, col, L4_TEXT)
        report["after_text"] = t.read_field(row, col)
    else:
        report["after_text"] = {"skipped": "não coube no campo", "field_length": unprotected.length}
    t.fill(row, col, "")
    report["after_erase"] = t.read_field(row, col)
    try:
        t.fill(row, col, "X" * (unprotected.length + 1))   # 1 a mais que o campo: Truncated sem copiar nada
        report["too_long"] = "aceito (ERRO)"
    except rumbapy.Truncated as exc:
        report["too_long"] = f"Truncated delivery={exc.delivery}"
    try:
        t.fill(14, 26, "X")
        report["protected"] = "aceito (ERRO)"
    except rumbapy.ProtectedField as exc:
        report["protected"] = f"ProtectedField delivery={exc.delivery}"
    report["oia_after"] = t.snapshot().oia.describe()
    timed(report, "close", t.close)                     # MDT fica ligado (C25): nunca ENTER depois; só fechar


# ------------------------------------------------------------------------------------------- L6 e L7

def _require_credentials(ctx: Context) -> Credentials:
    if ctx.credentials is None:
        raise RuntimeError("roteiro de login sem credenciais")
    return ctx.credentials


class OiaTimeline:
    """Linha do tempo do OIA depois de cada ENTER, PASSIVA: embrulha ``snapshot``/``send`` do backend da Session e
    registra o OIA de cada snapshot que a própria receita faz (polling das esperas: 50 ms no 1º segundo, depois
    100 ms) e o ``oia_after`` de cada ``send``. Nenhuma chamada extra à DLL; nenhum texto de tela (só a âncora fixa
    do SIGNON, lida depois do 1º ENTER)."""

    LIMIT = 3000

    def __init__(self, t: Any, signon: tuple[str, int, int] | None = None) -> None:
        self.backend: Any = getattr(t, "_backend")
        self.signon = signon
        self.events: list[dict[str, Any]] = []
        self.enters = 0
        self.t_enter: float | None = None
        self.signon_anchor: str | None = None

    def _aids(self, chunks: Any) -> list[str]:
        from rumbapy import keys

        names: list[str] = []
        for chunk in chunks if isinstance(chunks, (list, tuple)) else ():
            try:
                tokens = keys.tokenize_mnemonic(str(chunk))
            except Exception:
                continue
            names.extend(tok.name for tok in tokens if isinstance(tok, keys.Key) and tok in keys.AIDS)
        return names

    def _add(self, event: dict[str, Any]) -> None:
        if len(self.events) < self.LIMIT:
            self.events.append(event)

    def _ms(self) -> float:
        return round((time.monotonic() - (self.t_enter or time.monotonic())) * 1000, 1)

    def __enter__(self) -> OiaTimeline:
        snapshot, send = self.backend.snapshot, self.backend.send

        def recorded_send(chunks: Any, *args: Any, **kwargs: Any) -> Any:
            result = send(chunks, *args, **kwargs)
            if "ENTER" in self._aids(chunks):
                self.enters += 1
                self.t_enter = time.monotonic()
                oia = getattr(result, "oia_after", None)
                self._add({"enter": self.enters, "t_ms": 0.0, "oia_after": oia.describe() if oia else None})
            return result

        def recorded_snapshot(*args: Any, **kwargs: Any) -> Any:
            screen = snapshot(*args, **kwargs)
            if self.t_enter is not None:
                self._add({"enter": self.enters, "t_ms": self._ms(), "oia": screen.oia.describe()})
                if self.enters == 1 and self.signon is not None and self.signon_anchor != self.signon[0]:
                    text, row, col = self.signon
                    self.signon_anchor = screen.get(row, col, len(text))
            return screen

        try:
            self.backend.send, self.backend.snapshot = recorded_send, recorded_snapshot
        except AttributeError:                  # backend sem __dict__: o logon roda sem a linha do tempo
            self._add({"erro": "linha do tempo indisponível (backend sem atributos de instância)"})
        return self

    def __exit__(self, *exc: object) -> None:
        for name in ("send", "snapshot"):
            getattr(self.backend, "__dict__", {}).pop(name, None)


def _logon(ctx: Context, t: Any, run: Any, anchors: dict[str, tuple[int, int, int]],
           signon: tuple[str, int, int] | None = None) -> None:
    """Executa o logon e grava âncoras/tela e a linha do tempo do OIA no relatório (que só vai para docs/interno,
    fora do Git)."""
    rumbapy, report = ctx.rumbapy, ctx.report
    timeline = OiaTimeline(t, signon)
    try:
        with timeline:
            ok = timed(report, "logon", run)
        report["logon"] = "ok"
        for key, (row, col, length) in anchors.items():
            report[key] = ok.get(row, col, length).strip()
        report["line1"] = ok.line(1).strip()[:40]
        report["oia_after_logon"] = ok.oia.describe()
        report["screen_after_logon"] = [ok.line(r) for r in range(1, t.rows + 1)]
    except rumbapy.RumbaError as exc:
        report["logon"] = exc_info(exc)
        ctx.texts.extend([str(exc), repr(exc), str(getattr(exc, "hint", "") or "")])
        if exc.screen is not None:
            ctx.texts.append(str(exc.screen))
            report["screen_at_error"] = [exc.screen.line(r) for r in range(1, t.rows + 1)]
    report["oia_timeline"] = timeline.events
    report["enters_seen"] = timeline.enters
    if signon is not None:
        report["signon_anchor"] = timeline.signon_anchor
    ctx.texts.append(repr(t))
    with DialogWatch() as watch:
        timed(report, "close", t.close)
    report["dialogs_during_close"] = watch.seen
    report["state_after_close"] = t.state


def l6(ctx: Context) -> None:
    """L6: logon CICS com o login do dono (guard login: só texto e ENTER); âncoras; fechar a sessão logada."""
    creds = _require_credentials(ctx)
    t = open_d(ctx)
    ctx.report["info"] = t.info.to_wire()
    ctx.report["menu_anchor"] = t.snapshot(fields=True).get(14, 26, 24)
    _logon(ctx, t, lambda: t.cics.logon(creds.user, creds.password),
           {"ok_anchor": (5, 20, 6), "msg_line": (19, 19, 40)}, signon=tuple(t.cics.SIGNON))


def l7(ctx: Context) -> None:
    """L7 (só com autorização explícita do dono): logon IMS (RHELP) no IMS_A; âncoras; fechar a sessão logada."""
    if os.environ.get("RUMBAPY_LIVE_L7_AUTORIZADO") != "1":
        raise RuntimeError("L7 sem RUMBAPY_LIVE_L7_AUTORIZADO=1")
    creds = _require_credentials(ctx)
    t = open_d(ctx, letter="A")
    ctx.report["info"] = t.info.to_wire()
    ctx.report["geometry"] = [t.rows, t.cols]
    _logon(ctx, t, lambda: t.ims.logon(creds.user, creds.password), {"line2": (2, 1, 40)})


# ------------------------------------------------------------------------------------------- compat

def compat(ctx: Context) -> None:
    """Script no estilo da v0.1.5, sem alteração: RumbaClient('D').logon_cics -> copy_ps_to_string -> close_terminal."""
    report = ctx.report
    creds = _require_credentials(ctx)
    from rumbapy import RumbaClient

    client = RumbaClient("D", python32bit_path=ctx.py32)
    report["logon_cics"] = timed(report, "logon_cics", lambda: client.logon_cics(creds.user, creds.password))
    report["connected"] = client.connected
    report["copy_ps_to_string"] = client.copy_ps_to_string(5, 20, 6)
    report["screen_load"] = timed(report, "screen_load", lambda: client.screen_load(5, 20, "Signon"))
    with DialogWatch() as watch:
        report["close_terminal"] = timed(report, "close_terminal", client.close_terminal)
    report["dialogs_during_close"] = watch.seen
    ctx.texts.append(repr(client))


# ------------------------------------------------------------------------------------------- L9

def l9(ctx: Context) -> None:
    """L9: com BRAZIL_Z aberto, open("D"): processos do launcher (instância única?) e close de D sem afetar Z."""
    rumbapy, report = ctx.rumbapy, ctx.report
    z = open_d(ctx, "open_z", letter="Z")
    report["z"] = z.info.to_wire()
    d = open_d(ctx, "open_d")
    report["d"] = d.info.to_wire()
    report["same_launcher_process"] = z.info.hwnd_pid == d.info.hwnd_pid
    report["z_windows"], report["d_windows"] = window(z), window(d)
    try:                                                   # a abertura do D derruba a conexão do Z?
        report["z_read_after_open_d"] = z.read(1, 1, 20)
    except rumbapy.RumbaError as exc:
        report["z_read_after_open_d"] = exc_info(exc)
    timed(report, "close_d", d.close)
    try:                                                   # fechar derruba (A.14 n): o worker do Z reconecta sozinho
        report["z_read_after_close_d"] = z.read(1, 1, 20)
        report["z_state"] = z.state
    except rumbapy.RumbaError as exc:
        report["z_read_after_close_d"] = exc_info(exc)
    report["windows_after_close_d"] = profile_titles(ctx, "D", "Z")
    timed(report, "close_z", z.close)


# ------------------------------------------------------------------------------------------- sonda do fechamento

def close_probe(ctx: Context) -> None:
    """E.4: janelas da instância e quando a DLL volta a responder. evict pela admin; WM_CLOSE SÓ na janela principal
    (raiz do HWND do HLLAPI); acompanha SEM chamar a DLL (IsWindow/process_alive a cada 100 ms); só depois de o
    processo sair (ou de 30 s) chama sessions (WD_QuerySessions) cronometrado."""
    from rumbapy import _bridge, _user32, _win32

    report = ctx.report
    u32 = _user32.RealUser32()
    t = open_d(ctx)
    report["info"] = t.info.to_wire()
    pid = t.info.hwnd_pid
    if not t.info.hwnd:
        raise RuntimeError("sem HWND da instância: a sonda não manda WM_CLOSE às cegas")
    main_hwnd = u32.root_of(t.info.hwnd)
    if not main_hwnd:
        raise RuntimeError("sem a janela principal (raiz do HWND do HLLAPI): a sonda não manda WM_CLOSE às cegas")
    report["main_hwnd"] = main_hwnd
    report["windows_of_launcher"] = [
        {"hwnd": w.hwnd, "title": w.title, "cls": w.cls, "visible": w.visible, "iconic": w.iconic,
         "owner": w.owner, "is_main": w.hwnd == main_hwnd}
        for w in u32.top_windows() if w.pid == pid]
    t.release()
    admin = _bridge.get_worker("D", "admin")
    report["evict"] = admin.evict("teste de fechamento")
    created = _win32.process_created(pid) if pid else None
    t1 = time.monotonic()
    u32.post_close(main_hwnd)
    window_gone = process_gone = None
    timeline: list[tuple[Any, ...]] = []
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
    report["after_wm_close"] = {"window_gone_s": window_gone, "process_gone_s": process_gone, "timeline": timeline}
    t2 = time.monotonic()
    entries = admin.sessions()
    report["sessions_after_exit"] = {"ms": round((time.monotonic() - t2) * 1000, 1),
                                     "letters": [e.letter for e in entries]}
    admin.release()


# ------------------------------------------------------------------------------------------- L8 (servidor 0.1.5)

L8_DIR = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "rumbapy-l8"
L8_PORT = 8500
L8_PREPARE = (r"py -3.13-32 -m venv %TEMP%\rumbapy-l8\venv32 && %TEMP%\rumbapy-l8\venv32\Scripts\pip install pywin32 "
              r"pywinauto && git archive --format=tar master src/rumbapy | tar -x -C %TEMP%\rumbapy-l8")
LEGACY_WRAPPER = r'''
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


def port_open(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def l8_precondition(base: Path = L8_DIR) -> str | None:
    """Motivo para não rodar o L8 (``None`` = pronto): venv 32-bit com o código da 0.1.5 e a porta 8500 livre."""
    py32, server = base / "venv32" / "Scripts" / "python.exe", base / "src" / "rumbapy" / "server.py"
    ready = py32.is_file() and server.is_file()
    if not ready:
        return f"L8: prepare {base} antes ({L8_PREPARE})"
    if port_open(L8_PORT):
        return "L8: a porta 8500 já tem um ouvinte (servidor 0.1.5 do usuário?): não mexo"
    return None


def l8(ctx: Context) -> None:
    """L8 (E.8): a v2 neutraliza um servidor 0.1.5 REAL (código do master) rodando em 32-bit. Nenhum terminal é
    aberto: o 0.1.5 só carrega a DLL e, no disconnect, chama WD_DisconnectPS (rc 1, nada conectado). O invólucro sai
    por os._exit(0) (ExitProcess: o DllMain libera o slot HLLAPI) quando o arquivo de parada aparece; nunca é morto."""
    report = ctx.report
    reason = l8_precondition()
    if reason:
        raise RuntimeError(reason)
    py32 = L8_DIR / "venv32" / "Scripts" / "python.exe"
    work = Path(tempfile.mkdtemp(prefix="l8run-", dir=ctx.home))   # apagado com o RUMBAPY_HOME no fim
    stop, log = work / "stop", work / "legacy.log"
    (work / "wrapper.py").write_text(LEGACY_WRAPPER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    env["PYTHONPATH"] = str(L8_DIR / "src")
    proc = subprocess.Popen([str(py32), str(work / "wrapper.py"), str(stop), str(log)], env=env, cwd=str(work),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000)
    report["legacy_pid"] = proc.pid
    try:
        deadline = time.monotonic() + 20
        while not port_open(L8_PORT) and time.monotonic() < deadline:
            time.sleep(0.2)
        report["listening"] = port_open(L8_PORT)
        from rumbapy import _legacy, _win32

        report["listener_pids"] = _win32.tcp_listener_pids(L8_PORT)
        t0 = time.monotonic()
        status = _legacy.neutralize("D")
        report["neutralize_ms"] = round((time.monotonic() - t0) * 1000, 1)
        report["status"] = {k: getattr(status, k) for k in dir(status) if not k.startswith("_")
                            and not callable(getattr(status, k))}
        report["still_listening"] = port_open(L8_PORT)
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


SCRIPT_FUNCTIONS = {"l1": l1, "l2": l2, "l3": l3, "l3b": l3b, "l4": l4, "l5": l5, "l6": l6, "l7": l7, "l8": l8,
                    "l9": l9, "compat": compat, "close": close_probe}
