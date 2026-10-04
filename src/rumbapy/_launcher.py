"""Abrir, anexar e fechar terminais do RUMBA no cliente 64-bit (E.4–E.7).

Requisito do dono: **abrir e fechar fecham TODAS as instâncias do perfil** (duas instâncias da mesma letra dão
"Short Name already in use" e o HLLAPI fica preso à 1ª), mas **só** janelas que sejam sessões RUMBA daquele perfil
(regras da E.3 em ``_instances``). Fechar é ``WM_CLOSE`` (validado ao vivo: ~0,5 s, sem confirmação); diálogos
desconhecidos recebem ``WM_CLOSE`` (= Cancelar/X), nunca um botão afirmativo. **Nunca** se mata processo.

A DLL nunca é chamada daqui: tudo passa por um ``Backend`` (``get_backend(letra, modo)``, modo ``"admin"`` ou
``"owner"``). Pontos de injeção para testes (J.3): ``Launcher(user32, get_backend, spawn, clock=..., sleep=...)``.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .errors import (ClientTableFull, CloseFailed, HllapiSystemError, InstanceNotClosable, LaunchFailed,
                     LaunchTimeout, MultipleInstances, RumbaError, RumbaNotInstalled, SandboxViolation,
                     SessionReplaced, TerminalError, TerminalInUse, TerminalNotOpen, HllapiError, HostBusy,
                     KeyboardLocked)
from ._instances import (Instance, Snapshot, dialogs_of, find_instances, hllapi_error_visible, is_ok_button,
                         rumba_dialogs, sanitize, LAUNCHER_IMAGE, RUMBA_IMAGES, EDGE_IMAGE, IE_IMAGE)
from ._user32 import SW_SHOWMINNOACTIVE, SW_SHOWNOACTIVATE, User32
from ._types import ConnectResult, OpenInfo, Profile, WindowInfo
from . import profiles, _log, _paths

import subprocess
import logging
import inspect
import time
import json
import os

if TYPE_CHECKING:
    from ._backend import Backend
    from .screen import Screen

logger = logging.getLogger(__name__)

WindowMode = Literal["normal", "minimized"]
GetBackend = Callable[[str, str], "Backend"]          # (letra, "admin" | "owner") -> Backend

OPEN_TIMEOUT = 90.0                                   # B.11 (RUMBAPY_OPEN_TIMEOUT)
ATTACH_TIMEOUT = 10.0
CLOSE_TIMEOUT = 15.0
REUSE_CONNECT_S = 10.0
REGISTER_POLL = 0.25
CONNECT_POLL = 0.5
CLOSE_POLL = 0.1
SCREEN_POLL = 0.1
STABLE_S = 0.3                                        # 1ª tela: igual ao snapshot de >= 0,3 s antes
BLANK_STABLE_S = 1.0                                  # attach/reuse: tela em branco pronta precisa de 1 s estável
CLOSE_CHECKS = (3.0, 8.0)                             # diálogos e novo WM_CLOSE nos sobreviventes
REGISTER_MAX_S = 45.0
NOT_CLOSABLE_GRACE = 1.0                              # sem nenhuma janela fechada: quanto esperar o desregistro
PROCESS_EXIT_S = 5.0                                  # depois das janelas: espera a saída dos processos (sem DLL)
SYSTEM_ERRORS_MAX = 3
LAUNCHED_KEEP = 5
IE_TIMEOUT_MAX = 60
IE_TIMEOUT_MIN = 10
HOLDER_IMAGES = (EDGE_IMAGE, IE_IMAGE, LAUNCHER_IMAGE)  # quem costuma segurar a letra sem janela fechável (E.4 6)

DEFAULT_LAUNCHER = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
CSCRIPT = r"C:\Windows\System32\cscript.exe"
IE_SCRIPT = Path(__file__).with_name("_ie_open.js")
STILL_CONNECTING_HINT = "o RUMBA ainda está tentando conectar — verifique VPN/rede"


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class Opened:
    """Resultado de ``open``/``attach``: o ``Backend`` de dono (já com ``bind_instance``) e o ``OpenInfo``."""

    backend: Backend
    info: OpenInfo
    profile: Profile
    screen: Screen | None = None           # a 1ª tela estável (passo 7 da E.5)


# ------------------------------------------------------------------------------------------- padrões (produção)

def _sandboxed() -> bool:
    return os.environ.get("RUMBAPY_SANDBOX") == "1"


def default_spawn(argv: list[str]) -> ProcessLike:
    """``Popen`` do Web Launcher (E.5 passo 4): grupo novo e fora do Job da IDE (sem breakaway se negado).

    Sandbox -> ``SandboxViolation`` antes de criar qualquer processo."""
    if _sandboxed():
        raise SandboxViolation.make(acao=f"iniciar {os.path.basename(str(argv[0]))}")
    from . import _win32

    common: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL, "close_fds": True}
    try:
        try:
            return subprocess.Popen(argv, creationflags=_win32.CREATE_NEW_PROCESS_GROUP
                                    | _win32.CREATE_BREAKAWAY_FROM_JOB, **common)
        except PermissionError:
            logger.warning("CREATE_BREAKAWAY_FROM_JOB negado; iniciando o launcher dentro do Job atual")
            return subprocess.Popen(argv, creationflags=_win32.CREATE_NEW_PROCESS_GROUP, **common)
    except OSError as exc:
        raise _spawn_error(exc, "Host Access Web Launcher", argv) from exc


def _spawn_error(exc: OSError, what: str, argv: list[str]) -> RumbaError:
    """``OSError`` do ``Popen`` -> ``RumbaError`` em português (C.5/B.7: só ``RumbaError`` sai da biblioteca).

    Arquivo ausente -> ``RumbaNotInstalled``; o resto (política de grupo/AppLocker, acesso negado...) ->
    ``LaunchFailed`` variante ``spawn``. Nada do texto do sistema vai à mensagem: só o tipo e o código."""
    code = getattr(exc, "winerror", None) or exc.errno
    logger.warning("não consegui executar %s (%s, erro %s)", what, type(exc).__name__, code)
    if isinstance(exc, FileNotFoundError):
        return RumbaNotInstalled.make(o_que=what, caminho=str(argv[0]) if argv else "?")
    motivo = f"{type(exc).__name__}, erro {code}" if code else type(exc).__name__
    return LaunchFailed.make("spawn", o_que=what, motivo=motivo, details={"winerror": code})


def default_run_script(argv: list[str], timeout: float) -> tuple[int | None, bytes]:
    """Roda o ``cscript`` do fallback IE (``CREATE_NO_WINDOW``) e devolve ``(código, stdout)``.

    Estourou o prazo -> ``(None, b"")`` e o ``cscript`` NÃO é morto (ele tem o próprio limite). Sandbox ->
    ``SandboxViolation`` antes de criar o processo."""
    if _sandboxed():
        raise SandboxViolation.make(acao=f"iniciar {os.path.basename(str(argv[0]))}")
    from . import _win32

    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                close_fds=True, creationflags=_win32.CREATE_NO_WINDOW)
    except OSError as exc:
        raise _spawn_error(exc, "cscript (fallback Internet Explorer)", argv) from exc
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("cscript do fallback IE não terminou em %.0f s (PID %d); seguindo sem esperar", timeout,
                       proc.pid)
        return None, b""
    return proc.returncode, out or b""


def default_find_launcher() -> str | None:
    """Executável do Web Launcher: 1º token de ``HKCR\\hawlauncher\\shell\\open\\command``, senão o caminho padrão."""
    candidates: list[str] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"hawlauncher\shell\open\command") as key:
            value, _kind = winreg.QueryValueEx(key, "")
        first = _first_token(str(value))
        if first:
            candidates.append(first)
    except OSError:
        pass
    candidates.append(DEFAULT_LAUNCHER)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _first_token(command: str) -> str:
    command = command.strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    return command.split(" ", 1)[0]


def default_find_worker(letter: str) -> dict[str, Any] | None:
    """Registro do worker da letra se o processo dele estiver vivo (``created`` confere), senão ``None``."""
    from . import _win32

    reg = _paths.read_json(_paths.worker_registry(letter))
    if not reg:
        return None
    pid, created = reg.get("pid"), reg.get("created")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    try:
        alive = _win32.process_alive(pid, created if isinstance(created, int) else None)
    except OSError:
        return None
    return reg if alive else None


def _default_process_alive(pid: int, created: int | None) -> bool:
    from . import _win32

    try:
        return _win32.process_alive(pid, created)
    except OSError:
        return False


def _default_process_created(pid: int) -> int | None:
    from . import _win32

    try:
        return _win32.process_created(pid)
    except OSError:
        return None


def _default_hllapi_client_pids() -> list[int]:
    from . import _win32

    try:
        return _win32.hllapi_client_pids()
    except OSError:
        return []


def _default_neutralize(letter: str) -> None:
    from . import _legacy

    _legacy.neutralize(letter)


def _legacy_busy_extra(letter: str) -> str:
    from . import _legacy

    return _legacy.busy_extra(letter)


def _default_get_backend(python32: str | os.PathLike[str] | None = None) -> GetBackend:
    """``_bridge.get_worker(letra, modo)`` (P-D; import tardio enquanto a Onda 2 não termina)."""

    def get(letter: str, mode: str) -> Backend:
        from . import _bridge  # type: ignore[attr-defined]  # P-D

        if python32 is not None:
            return _bridge.get_worker(letter, mode, python32=python32)
        return _bridge.get_worker(letter, mode)

    def find_admin(letter: str) -> Backend | None:
        """Admin só de worker JÁ vivo (E.4 passo 1): ``close`` nunca sobe worker (nem carrega a DLL)."""
        from ._backend import ExistingAdmin      # tardio: P-D

        return ExistingAdmin.find(letter, python32=python32)  # type: ignore[return-value]

    get.find_admin = find_admin                  # type: ignore[attr-defined]  # usado pelo Launcher._close
    return get


def _adapt_get_backend(fn: Callable[..., Backend]) -> GetBackend:
    """Aceita ``get_backend(letra, modo)`` (D.8) ou ``get_backend(modo)`` já ligado à letra (fábrica da ``session``)."""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return fn
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
        return fn
    positional = [p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                  inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if len(positional) >= 2:
        return fn

    def by_mode(_letter: str, mode: str) -> Backend:
        return fn(mode)

    return by_mode


def _release_quietly(backend: Backend | None, letter: str) -> None:
    """Devolve o lease de dono depois de uma falha de ``open``/``attach`` (melhor esforço)."""
    if backend is None:
        return
    try:
        backend.release()
    except Exception as exc:
        logger.warning("%s: release do processo auxiliar falhou (%s)", letter, type(exc).__name__)


def open_timeout_default() -> float:
    raw = os.environ.get("RUMBAPY_OPEN_TIMEOUT")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning("RUMBAPY_OPEN_TIMEOUT inválido (%r); usando %.0f s", raw, OPEN_TIMEOUT)
    return OPEN_TIMEOUT


# ------------------------------------------------------------------------------------------- Launcher

class Launcher:
    """Orquestra abrir/anexar/fechar (E). Sem estado entre chamadas além das dependências injetadas."""

    def __init__(self, user32: User32, get_backend: GetBackend, spawn: Callable[[list[str]], ProcessLike] | None = None,
                 *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 find_worker: Callable[[str], dict[str, Any] | None] = default_find_worker,
                 find_launcher: Callable[[], str | None] = default_find_launcher,
                 run_script: Callable[[list[str], float], tuple[int | None, bytes]] = default_run_script,
                 neutralize: Callable[[str], object] = _default_neutralize,
                 legacy_busy_extra: Callable[[str], str] = _legacy_busy_extra,
                 process_alive: Callable[[int, int | None], bool] = _default_process_alive,
                 process_created: Callable[[int], int | None] = _default_process_created,
                 hllapi_client_pids: Callable[[], Iterable[int]] = _default_hllapi_client_pids,
                 find_admin: Callable[[str], Backend | None] | None = None) -> None:
        self.user32 = user32
        self.get_backend = _adapt_get_backend(get_backend)
        # close sem admin do chamador: conexão admin só com worker JÁ vivo (E.4 passo 1). Padrão: a do get_backend
        # de produção (``_default_get_backend``); sem ela, ``get_backend(L, "admin")`` (dublês dos testes)
        self.find_admin = find_admin or getattr(get_backend, "find_admin", None)
        self.spawn = spawn or default_spawn
        self.clock = clock
        self.sleep = sleep
        self.find_worker = find_worker
        self.find_launcher = find_launcher
        self.run_script = run_script
        self.neutralize = neutralize
        self.legacy_busy_extra = legacy_busy_extra
        self.process_alive = process_alive
        self.process_created = process_created
        self.hllapi_client_pids = hllapi_client_pids

    # ----------------------------------------------------------------- PIDs lançados (launched-<tag>-<L>.json)
    def launched_pids(self, letter: str) -> list[int]:
        """PIDs lançados por nós para a letra que continuam vivos (``created`` confere)."""
        data = _paths.read_json(_paths.launched_registry(letter)) or {}
        out: list[int] = []
        for item in data.get("pids") or []:
            if not isinstance(item, dict):
                continue
            pid, created = item.get("pid"), item.get("created")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                continue
            if not isinstance(created, int) or isinstance(created, bool):
                continue                     # sem ``created`` não dá para descartar um PID reaproveitado
            if self.process_alive(pid, created):
                out.append(pid)
        return out

    def record_launch(self, letter: str, pid: int) -> None:
        path = _paths.launched_registry(letter)
        data = _paths.read_json(path) or {}
        created = self.process_created(pid)
        if not isinstance(created, int) or isinstance(created, bool):
            logger.warning("%s: não consegui ler o início do processo %d; ele fica fora da regra 'launched'", letter,
                           pid)
            return
        items = [i for i in (data.get("pids") or []) if isinstance(i, dict)]
        items.append({"pid": pid, "created": created, "at": time.time()})
        try:
            _paths.write_json(path, {"pids": items[-LAUNCHED_KEEP:]})
        except OSError as exc:
            logger.warning("Não consegui gravar %s (%s)", path.name, type(exc).__name__)

    # ----------------------------------------------------------------- instâncias
    def _registry_hwnd(self, letter: str) -> tuple[dict[str, Any] | None, int | None]:
        reg = self.find_worker(letter)
        hwnd = reg.get("hwnd") if reg else None
        return reg, (hwnd if isinstance(hwnd, int) and not isinstance(hwnd, bool) and hwnd else None)

    def instances(self, profile: Profile | str, *, hllapi_hwnds: Iterable[int | None] = (),
                  snapshot: Snapshot | None = None) -> list[Instance]:
        """Instâncias do perfil (E.3) com o HWND HLLAPI do registro do worker e os PIDs que nós lançamos."""
        profile = profiles.resolve(profile)
        _reg, reg_hwnd = self._registry_hwnd(profile.letter)
        return find_instances(self.user32, profile, hllapi_hwnds=[reg_hwnd, *hllapi_hwnds],
                              launched_pids=self.launched_pids(profile.letter), snapshot=snapshot)

    # ----------------------------------------------------------------- close_all (E.4)
    def close_all(self, profile: Profile | str, *, timeout: float = CLOSE_TIMEOUT,
                  admin: Backend | None = None) -> int:
        """Fecha TODAS as instâncias do perfil (só janelas RUMBA, E.3) revogando qualquer lease; não exige lease
        nem DLL. Devolve o nº de janelas de instância fechadas. Nunca mata processo."""
        return self._close(profiles.resolve(profile), timeout=timeout, admin=admin)

    def _close(self, profile: Profile, *, timeout: float, admin: Backend | None,
               keep: Callable[[Instance], bool] | None = None, extra_hllapi: Iterable[int | None] = (),
               verify: bool = True) -> int:
        start = self.clock()
        deadline = start + max(0.0, float(timeout))
        letter = profile.letter
        reg, reg_hwnd = self._registry_hwnd(letter)
        own: Backend | None = None
        if reg is not None and admin is None:        # 1. worker vivo: conexão admin -> evict
            try:
                if self.find_admin is not None:
                    own = self.find_admin(letter)    # nunca sobe worker; None = ele já saiu
                    if own is None:
                        logger.info("close %s: o processo auxiliar já saiu; só as janelas", letter)
                else:
                    own = self.get_backend(letter, "admin")
            except RumbaError as exc:
                logger.warning("close %s: conexão admin com o processo auxiliar falhou (%s)", letter,
                               type(exc).__name__)
        try:
            return self._close_with(profile, admin if admin is not None else own, start=start, deadline=deadline,
                                    reg=reg, reg_hwnd=reg_hwnd, keep=keep, extra_hllapi=extra_hllapi,
                                    verify=verify)
        finally:
            _release_quietly(own, letter)            # só a conexão admin que este close abriu

    def _close_with(self, profile: Profile, adm: Backend | None, *, start: float, deadline: float,
                    reg: dict[str, Any] | None, reg_hwnd: int | None, keep: Callable[[Instance], bool] | None,
                    extra_hllapi: Iterable[int | None], verify: bool) -> int:
        letter, name = profile.letter, profile.name
        if adm is not None:
            try:
                result = adm.evict("close")
                owner = result.get("evicted_owner") if isinstance(result, dict) else None
                if owner:
                    logger.info("close %s: lease revogado (PID %s)", letter, owner.get("pid"))
            except RumbaError as exc:
                logger.warning("close %s: evict falhou (%s: %s)", letter, type(exc).__name__, sanitize(exc.message))
        found = find_instances(self.user32, profile, hllapi_hwnds=[reg_hwnd, *extra_hllapi],
                               launched_pids=self.launched_pids(letter))      # 2.
        if keep is not None:
            found = [inst for inst in found if keep(inst)]
        if found:
            _log.notice(f"RumbaPy: fechando {len(found)} janela(s) de {name}…")
        for inst in found:                           # 3. oculta -> minimizada sem ativar (C24)
            if not inst.visible:
                self.user32.show(inst.hwnd, SW_SHOWMINNOACTIVE)
        exiting = self._exiting_processes(profile, found)
        for inst in found:                           # 4.
            logger.info("close %s: WM_CLOSE em hwnd=%d pid=%d (%s) '%s'", letter, inst.hwnd, inst.pid, inst.kind,
                        inst.safe_title)
            self.user32.post_close(inst.hwnd)
        pending = {inst.hwnd: inst for inst in found}
        checks = [start + s for s in CLOSE_CHECKS]
        saw_table_full = False
        while True:                                  # 5.
            pending = {h: inst for h, inst in pending.items() if self.user32.is_window(h)}
            if not pending:
                break
            now = self.clock()
            if now >= deadline:
                break
            if checks and now >= checks[0]:
                while checks and now >= checks[0]:
                    checks.pop(0)
                saw_table_full |= self._nudge(profile, list(pending.values()))
            self.sleep(CLOSE_POLL)
        closed = len(found) - len(pending)
        if pending:                                  # 7. nunca TerminateProcess
            survivors = list(pending.values())
            if saw_table_full or any(d.kind == "hllapi_error" for inst in survivors
                                     for d in dialogs_of(self.user32, inst, profile_name=name)):
                raise ClientTableFull.make(terminal=letter)
            listing = ", ".join(f"'{inst.safe_title}' (PID {inst.pid})" for inst in survivors)
            raise CloseFailed.make(n=len(survivors), perfil=name, lista=listing, terminal=letter)
        if verify:                                   # 6.
            self._wait_processes_exit(letter, exiting, min(deadline, self.clock() + PROCESS_EXIT_S))
            until = deadline if found else min(deadline, self.clock() + NOT_CLOSABLE_GRACE)
            self._verify_unregistered(profile, adm, until, reg)
        return closed                                # 8.

    def _exiting_processes(self, profile: Profile, found: list[Instance]) -> dict[int, int | None]:
        """PIDs (com ``created``) que devem sair depois do WM_CLOSE: os das instâncias, menos os que hospedam janela
        de OUTRO perfil (não vão sair) e o próprio processo. Lido ANTES do WM_CLOSE (PID reaproveitado = outro)."""
        if not found:
            return {}
        others = Snapshot(self.user32).other_profile_pids(profile.name)
        out: dict[int, int | None] = {}
        for inst in found:
            if inst.pid and inst.pid not in others and inst.pid != os.getpid() and inst.pid not in out:
                out[inst.pid] = self.process_created(inst.pid)
        return out

    def _wait_processes_exit(self, letter: str, exiting: dict[int, int | None], until: float) -> None:
        """Passo 5b (ao vivo, 2026-10-04): ``WD_QuerySessions`` chamado enquanto o emulador encerra pode travar
        dentro da DLL (watchdog de 20 s + reciclagem do worker). Antes da conferência do passo 6, espera SEM chamar
        a DLL que os processos das instâncias fechadas saiam (ao vivo: 0,3 s depois do WM_CLOSE na janela principal)."""
        pending = dict(exiting)
        while pending:
            pending = {pid: created for pid, created in pending.items() if self.process_alive(pid, created)}
            if not pending or self.clock() >= until:
                break
            self.sleep(CLOSE_POLL)
        if pending:
            logger.info("close %s: processo(s) %s ainda saindo após o WM_CLOSE; conferindo o desregistro assim mesmo",
                        letter, sorted(pending))

    def _nudge(self, profile: Profile, survivors: list[Instance]) -> bool:
        """Aos 3 s e 8 s: trata os diálogos de cada sobrevivente (tabela E.3) e manda outro WM_CLOSE."""
        saw_table_full = False
        snap = Snapshot(self.user32)
        for inst in survivors:
            for dialog in dialogs_of(self.user32, inst, snapshot=snap, profile_name=profile.name):
                if dialog.kind == "connecting":
                    continue                       # o WM_CLOSE da janela principal basta (validado)
                if dialog.kind == "hllapi_error":
                    saw_table_full = True          # nunca clicar
                    continue
                if dialog.kind == "short_name":
                    self._click_ok(dialog.hwnd)
                    continue
                logger.info("close %s: diálogo desconhecido '%s' (hwnd=%d): WM_CLOSE", profile.letter,
                            dialog.safe_title, dialog.hwnd)
                self.user32.post_close(dialog.hwnd)   # equivale a Cancelar/X; nunca botão afirmativo
            self.user32.post_close(inst.hwnd)
        return saw_table_full

    def _click_ok(self, dialog_hwnd: int) -> bool:
        for button, text in self.user32.buttons(dialog_hwnd):
            if is_ok_button(text):
                self.user32.click(button)
                return True
        logger.warning("diálogo hwnd=%d sem botão OK; WM_CLOSE", dialog_hwnd)
        self.user32.post_close(dialog_hwnd)
        return False

    def _verify_unregistered(self, profile: Profile, adm: Backend | None, until: float,
                             reg: dict[str, Any] | None) -> None:
        """Passo 6 da E.4: com processo auxiliar, a letra tem de sair do ``QuerySessions``."""
        letter = profile.letter
        if adm is None:
            logger.warning("close %s: sem processo auxiliar; só as janelas foram conferidas", letter)
            return
        while True:
            try:
                registered = any(e.letter == letter for e in adm.sessions())
            except RumbaError as exc:
                logger.warning("close %s: sessions falhou (%s); verificação HLLAPI pulada", letter,
                               type(exc).__name__)
                return
            if not registered:
                return
            if self.clock() >= until:
                break
            self.sleep(CLOSE_POLL)
        pid: Any = reg.get("hwnd_pid") if reg else None
        hwnd = reg.get("hwnd") if reg else None
        if not pid and isinstance(hwnd, int) and hwnd:
            root = self.user32.root_of(hwnd)
            pid = next((w.pid for w in self.user32.top_windows() if w.hwnd == root), None)
        if not (isinstance(pid, int) and pid):
            pid = self._hllapi_holder_pid(reg)
        image = self.user32.process_image(pid) if isinstance(pid, int) and pid else None
        raise InstanceNotClosable.make(L=letter, imagem=image or "processo desconhecido", pid=pid or "?",
                                       terminal=letter)

    def _hllapi_holder_pid(self, reg: dict[str, Any] | None) -> int | None:
        """Sem hwnd no registro: o processo com a ``ehlapi32`` carregada (só leitura, sem carregar nada) de imagem
        Edge/IE/launcher — o caso típico é a aba do Edge no modo IE. ``None`` se não houver candidato."""
        try:
            pids = [int(p) for p in self.hllapi_client_pids()]
        except Exception as exc:                     # diagnóstico de melhor esforço
            logger.debug("lista de clientes HLLAPI ilegível (%s)", type(exc).__name__)
            return None
        skip = {os.getpid(), reg.get("pid") if reg else None}
        ranked: list[tuple[int, int]] = []
        for pid in pids:
            if pid in skip:
                continue
            image = (self.user32.process_image(pid) or "").lower()
            if image in HOLDER_IMAGES:
                ranked.append((HOLDER_IMAGES.index(image), pid))
        return min(ranked)[1] if ranked else None

    # ----------------------------------------------------------------- open (E.5)
    def open(self, profile: Profile | str, *, reuse: bool = False, window: WindowMode = "normal",
             timeout: float | None = None) -> Opened:
        """Fecha todas as instâncias do perfil e abre uma nova pelo Web Launcher (ou reaproveita, com ``reuse``).

        Não digita nada. Toda falha depois do lançamento fecha só o que nós abrimos (passo 10)."""
        profile = profiles.resolve(profile)
        if window not in ("normal", "minimized"):
            raise ValueError(f"window deve ser 'normal' ou 'minimized' (recebi {window!r})")
        total = float(timeout) if timeout is not None else open_timeout_default()
        t0 = self.clock()
        deadline = t0 + total
        letter = profile.letter
        self.neutralize(letter)                                              # 0.
        if hllapi_error_visible(self.user32):
            raise ClientTableFull.make(terminal=letter)
        admin = self.get_backend(letter, "admin")                            # 1.
        owners: list[Backend] = []
        try:
            return self._open(profile, admin, owners, reuse=reuse, window=window, total=total, t0=t0,
                              deadline=deadline)
        finally:
            self._release_admin(admin, owners, letter)

    def _open(self, profile: Profile, admin: Backend, owners: list[Backend], *, reuse: bool, window: WindowMode,
              total: float, t0: float, deadline: float) -> Opened:
        letter, name = profile.letter, profile.name
        before = self.instances(profile)
        before_hwnds = {inst.hwnd for inst in before}
        if reuse:                                                            # 2.
            if len(before) == 1:
                opened = self._try_reuse(profile, before[0], deadline, window, t0, owners)
                if opened is not None:
                    return opened
                _log.notice(f"RumbaPy: não consegui reaproveitar a janela de {name}; vou fechá-la e abrir outra.")
            else:
                found = "nenhuma janela" if not before else f"{len(before)} janelas"
                _log.notice(f"RumbaPy: reuse pedido, mas há {found} de {name}; vou abrir uma nova.")
        closed = self._close(profile, timeout=min(CLOSE_TIMEOUT, 0.15 * total), admin=admin)    # 3.
        try:
            via, proc = self._launch(profile, deadline)                      # 4.
        except (LaunchTimeout, LaunchFailed):
            # só o fallback IE falha aqui depois de criar processo: o cscript pode ter aberto (ou ainda abrir) o IE
            self._cleanup(profile, admin, before_hwnds, None, [])           # 10.
            raise
        t_launch = self.clock()
        launched = [proc.pid] if proc is not None and isinstance(getattr(proc, "pid", None), int) else []
        hllapi_hwnd: list[int | None] = [None]
        box: list[Backend | None] = [None]
        try:
            seconds_to_register = self._wait_registered(profile, admin, proc, before_hwnds, t0, t_launch,  # 5.
                                                        deadline, window, via, total)
            box[0] = self._get_owner(letter, owners)                        # 6.
            result = self._connect_loop(profile, box, owners, until=deadline - 5, t0=t0, total=total,
                                        on_hwnd=hllapi_hwnd)
            owner = _boxed(box)
            seconds_to_connect = self.clock() - t_launch
            owner.bind_instance(result.hwnd)                                 # 8. (antes do snapshot: A.9 i)
            screen = self._first_screen(profile, owner, until=deadline, t0=t0, total=total)    # 7.
            self._apply_window(result.hwnd, window)                          # 9.
        except BaseException:
            self._cleanup(profile, admin, before_hwnds, hllapi_hwnd[0], launched)   # 10.
            _release_quietly(box[0], letter)
            raise
        info = OpenInfo(launched=True, closed_instances=closed, hwnd=result.hwnd, hwnd_pid=result.hwnd_pid,
                        seconds_to_register=round(seconds_to_register, 3),
                        seconds_to_connect=round(seconds_to_connect, 3), rows=result.rows or 24,
                        cols=result.cols or 80, via=via)
        logger.info("open %s: via=%s registro=%.1f s conexão=%.1f s fechadas=%d", letter, via, seconds_to_register,
                    seconds_to_connect, closed)
        return Opened(owner, info, profile, screen)

    # ----------------------------------------------------------------- dono (lease) e conexão admin
    def _get_owner(self, letter: str, owners: list[Backend]) -> Backend:
        """``get_backend(L, "owner")``, anotado em ``owners`` (a conexão admin nunca é devolvida se for o mesmo objeto)."""
        owner = self.get_backend(letter, "owner")
        owners.append(owner)
        return owner

    def _replace_owner(self, letter: str, box: list[Backend | None], owners: list[Backend]) -> None:
        """O handle de dono veio despejado (``SessionReplaced``): é o lease de uma ``Session`` anterior deste processo,
        revogado pelo ``evict`` do passo 3 (o aviso só é lido na próxima requisição). Devolve-o e pega outro."""
        logger.info("open %s: o processo auxiliar devolveu um lease antigo (já revogado); pegando outro", letter)
        stale, box[0] = box[0], None
        _release_quietly(stale, letter)
        box[0] = self._get_owner(letter, owners)

    def _release_admin(self, admin: Backend, owners: list[Backend], letter: str) -> None:
        """Fim de ``open``/``attach``: devolve a conexão admin (o worker aceita poucas, ``MAX_CONNECTIONS``)."""
        if any(admin is owner for owner in owners):
            return                                   # mesmo objeto do dono (fábricas de teste): o dono decide
        _release_quietly(admin, letter)

    def _try_reuse(self, profile: Profile, inst: Instance, deadline: float, window: WindowMode,
                   t0: float, owners: list[Backend]) -> Opened | None:
        """Passo 2: exatamente 1 instância -> conecta em até 10 s; ``None`` = a conexão falhou (segue o open).

        Depois de conectar, a 1ª tela tem o prazo inteiro (``D``) e o ``LaunchTimeout`` dela propaga: a janela que o
        usuário pediu para reaproveitar (talvez logada) nunca é fechada por uma tela lenta."""
        letter = profile.letter
        box: list[Backend | None] = [None]
        try:
            box[0] = self._get_owner(letter, owners)
            result = self._reuse_connect(letter, box, owners, until=min(deadline, self.clock() + REUSE_CONNECT_S))
        except TerminalInUse as exc:
            _release_quietly(box[0], letter)
            if exc.rc != 11:
                # lease de OUTRO script: a instância está saudável, só ocupada; reuse=True nunca a derruba
                raise
            logger.info("open %s: reuse falhou (TerminalInUse rc 11)", letter)
            return None
        except (TerminalError, HllapiError) as exc:
            logger.info("open %s: reuse falhou (%s)", letter, type(exc).__name__)
            _release_quietly(box[0], letter)
            return None
        except BaseException:
            _release_quietly(box[0], letter)
            raise
        owner = _boxed(box)
        if result is None:
            logger.info("open %s: reuse sem conexão em %.0f s", letter, REUSE_CONNECT_S)
            _release_quietly(owner, letter)
            return None
        if result.hwnd and self.user32.root_of(result.hwnd) not in (inst.hwnd, 0):
            logger.info("open %s: o HLLAPI está em outra janela (hwnd=%d); não reaproveito", letter, result.hwnd)
            _release_quietly(owner, letter)
            return None
        try:
            owner.bind_instance(result.hwnd)                 # antes do snapshot (A.9 i)
            screen = self._first_screen(profile, owner, until=deadline, t0=t0, total=deadline - t0,
                                        require_text=False)       # instância que já existia: branco vale
            self._apply_window(result.hwnd, window)
        except BaseException:
            _release_quietly(owner, letter)
            raise
        info = OpenInfo(launched=False, closed_instances=0, hwnd=result.hwnd, hwnd_pid=result.hwnd_pid,
                        seconds_to_register=None, seconds_to_connect=round(self.clock() - t0, 3),
                        rows=result.rows or 24, cols=result.cols or 80, via="reuse")
        return Opened(owner, info, profile, screen)

    def _reuse_connect(self, letter: str, box: list[Backend | None], owners: list[Backend], *,
                       until: float) -> ConnectResult | None:
        replaced = False
        while True:
            try:
                attempt = _boxed(box).connect()
            except SessionReplaced:
                if replaced:
                    raise
                replaced = True
                self._replace_owner(letter, box, owners)
                continue
            if attempt.connected:
                return attempt
            if self.clock() >= until:
                return None
            self.sleep(CONNECT_POLL)

    def _launch(self, profile: Profile, deadline: float) -> tuple[Literal["launcher", "ie"], ProcessLike | None]:
        """Passo 4: Web Launcher (ou fallback IE só sem launcher / ``RUMBAPY_LAUNCH=ie``; nunca após falha dele)."""
        mode = (os.environ.get("RUMBAPY_LAUNCH") or "").strip().lower()
        exe = None if mode == "ie" else self.find_launcher()
        if exe:
            argv = [exe, profiles.launcher_arg(profile)]
            proc = self.spawn(argv)
            pid = getattr(proc, "pid", None)
            if isinstance(pid, int):
                self.record_launch(profile.letter, pid)
            logger.info("open %s: Web Launcher iniciado (PID %s)", profile.letter, pid)
            return "launcher", proc
        if mode == "launcher":
            raise RumbaNotInstalled.make(o_que="Host Access Web Launcher", caminho=DEFAULT_LAUNCHER,
                                         terminal=profile.letter)
        self._open_ie(profile, deadline)
        return "ie", None

    def _timeout(self, profile: Profile, phase: str, t0: float, *, hint: str | None = None) -> LaunchTimeout:
        kwargs: dict[str, Any] = {} if hint is None else {"hint": hint}
        return LaunchTimeout.make(perfil=profile.name, s=self.clock() - t0, fase=LaunchTimeout.PHASES[phase],
                                  terminal=profile.letter, details={"phase": phase}, **kwargs)

    def _wait_registered(self, profile: Profile, admin: Backend, proc: ProcessLike | None, before: set[int],
                         t0: float, t_launch: float, deadline: float, window: WindowMode,
                         via: str, total: float) -> float:
        """Passo 5: espera a letra aparecer no ``QuerySessions`` (a cada 250 ms)."""
        letter = profile.letter
        until = min(deadline - 15, t_launch + REGISTER_MAX_S)
        images = {IE_IMAGE} if via == "ie" else set(RUMBA_IMAGES)
        shown_early = False
        while True:
            if any(entry.letter == letter for entry in admin.sessions()):
                return self.clock() - t_launch
            if proc is not None:
                code = proc.poll()
                if code is not None and code != 0:       # 0 NÃO é falha: launcher de instância única (R7)
                    raise LaunchFailed.make(n=code, L=letter, terminal=letter)
            snap = Snapshot(self.user32)
            if hllapi_error_visible(self.user32, snapshot=snap):
                raise ClientTableFull.make(terminal=letter)
            short = rumba_dialogs(self.user32, "short_name", images=images, snapshot=snap)
            if short:
                for dialog in short:
                    self._click_ok(dialog.hwnd)
                raise MultipleInstances.make("open", L=letter, terminal=letter)
            if window == "minimized" and not shown_early:
                fresh = [inst for inst in self.instances(profile, snapshot=snap) if inst.hwnd not in before]
                for inst in fresh:
                    self.user32.show(inst.hwnd, SW_SHOWMINNOACTIVE)
                shown_early = bool(fresh)
            if self.clock() >= until:
                raise self._timeout(profile, "register", t0)
            self.sleep(REGISTER_POLL)

    def _connect_loop(self, profile: Profile, box: list[Backend | None], owners: list[Backend], *, until: float,
                      t0: float, total: float, on_hwnd: list[int | None] | None = None) -> ConnectResult:
        """Passo 6: ``connect`` a cada 500 ms; rc 1 = ainda conectando (o diálogo "Connecting" é normal).

        ``box[0]`` é o ``Backend`` de dono; um ``SessionReplaced`` (lease antigo deste processo, revogado pelo
        ``evict``) troca-o uma vez por outro (``_replace_owner``)."""
        letter = profile.letter
        system_errors = 0
        replaced = False
        while True:
            try:
                result = _boxed(box).connect()
            except SessionReplaced:
                if replaced:
                    raise
                replaced = True
                self._replace_owner(letter, box, owners)
                continue
            except TerminalInUse as exc:
                extra = self.legacy_busy_extra(letter) if exc.rc == 11 else ""
                if extra:
                    raise TerminalInUse.make("rc", L=letter, extra=extra, rc=11, function=exc.function,
                                             terminal=letter) from exc
                raise
            except HllapiSystemError:
                system_errors += 1
                if system_errors >= SYSTEM_ERRORS_MAX:
                    raise
            else:
                system_errors = 0
                if result.connected:
                    if on_hwnd is not None:
                        on_hwnd[0] = result.hwnd
                    return result
            if self.clock() >= until:
                connecting = rumba_dialogs(self.user32, "connecting")
                raise self._timeout(profile, "connect", t0, hint=STILL_CONNECTING_HINT if connecting else None)
            self.sleep(CONNECT_POLL)

    def _first_screen(self, profile: Profile, owner: Backend, *, until: float, t0: float, total: float,
                      require_text: bool = True) -> Screen:
        """Passo 7: snapshot livre, online e pronto, igual a outro tirado >= 0,3 s antes.

        ``require_text=True`` (instância lançada por este ``open``): a tela não pode estar toda em branco — logo
        depois do rc 0 o PS ainda vem vazio (R16). ``require_text=False`` (``attach``/``reuse`` de uma instância que já
        existia): tela em branco pronta é válida (ex.: CICS depois de CLEAR), mas só depois de ficar estável por
        ``BLANK_STABLE_S`` (instância recém-aberta por outro processo ainda esperando a 1ª tela)."""
        candidate: tuple[Screen, float] | None = None
        while True:
            try:
                screen: Screen | None = owner.snapshot()
            except (HostBusy, KeyboardLocked):
                screen = None
            now = self.clock()
            if screen is not None and _looks_ready(screen, require_text):
                if candidate is not None and _same(candidate[0], screen):
                    needed = STABLE_S if screen.text.strip() else BLANK_STABLE_S
                    if now - candidate[1] >= needed:
                        return screen
                else:
                    candidate = (screen, now)
            else:
                candidate = None
            if now >= until:
                raise self._timeout(profile, "first_screen", t0)
            self.sleep(SCREEN_POLL)

    def _apply_window(self, hwnd: int | None, window: WindowMode) -> None:
        if window == "minimized" and hwnd:
            self.user32.show(hwnd, SW_SHOWMINNOACTIVE)

    def _cleanup(self, profile: Profile, admin: Backend | None, before: set[int], hllapi_hwnd: int | None,
                 launched: list[int]) -> None:
        """Passo 10: fecha só as instâncias novas, a do HWND HLLAPI e as dos PIDs que lançamos. Nunca mata."""
        root = self.user32.root_of(hllapi_hwnd) if hllapi_hwnd else 0
        pids = set(launched)

        def keep(inst: Instance) -> bool:
            return inst.hwnd not in before or inst.hwnd == root or inst.pid in pids

        try:
            self._close(profile, timeout=CLOSE_TIMEOUT, admin=admin, keep=keep, extra_hllapi=[hllapi_hwnd],
                        verify=False)
        except Exception as exc:
            logger.warning("open %s: limpeza depois da falha não terminou (%s: %s)", profile.letter,
                           type(exc).__name__, sanitize(getattr(exc, "message", "") or ""))

    # ----------------------------------------------------------------- fallback IE (E.7)
    def _open_ie(self, profile: Profile, deadline: float) -> None:
        letter = profile.letter
        remaining = deadline - self.clock() - 20
        timeout_s = int(max(IE_TIMEOUT_MIN, min(IE_TIMEOUT_MAX, remaining)))
        argv = [CSCRIPT, "//NoLogo", "//E:JScript", str(IE_SCRIPT), profiles.index_url(), profile.link_text,
                str(timeout_s)]
        logger.info("open %s: sem Web Launcher; fallback Internet Explorer (prazo %d s)", letter, timeout_s)
        code, raw = self.run_script(argv, timeout_s + 10)
        if code is None:
            raise LaunchTimeout.make(perfil=profile.name, s=timeout_s + 10,
                                     fase=LaunchTimeout.PHASES["register"], terminal=letter,
                                     details={"phase": "register", "via": "ie"})
        result = parse_ie_output(raw)
        if result.get("ok") is True:
            return
        step = sanitize(str(result.get("step") or "sem saída"))
        error = sanitize(str(result.get("error") or f"código {code}"))
        raise LaunchFailed(f"O Internet Explorer não abriu o {profile.name} ({step}: {error}).", terminal=letter,
                           details={"via": "ie", "step": step, "code": code})

    # ----------------------------------------------------------------- attach (E.6)
    def attach(self, profile: Profile | str, timeout: float = ATTACH_TIMEOUT) -> Opened:
        """Conecta a uma instância já aberta. Não destrutivo: nunca abre nem fecha janelas."""
        profile = profiles.resolve(profile)
        t0 = self.clock()
        deadline = t0 + float(timeout)
        letter, name = profile.letter, profile.name
        self.neutralize(letter)
        found = self.instances(profile)
        if len(found) >= 2:
            pids = ", ".join(str(p) for p in sorted({inst.pid for inst in found}))
            raise MultipleInstances.make("attach", n=len(found), perfil=name, pids=pids, L=letter, terminal=letter)
        admin = self.get_backend(letter, "admin")
        owners: list[Backend] = []
        try:
            registered = any(entry.letter == letter for entry in admin.sessions())
            if not registered and not found:
                raise TerminalNotOpen.make(L=letter, terminal=letter, rc=1)
            box: list[Backend | None] = [self._get_owner(letter, owners)]      # dono vivo -> TerminalInUse
            try:
                result = self._connect_loop(profile, box, owners, until=deadline, t0=t0, total=float(timeout))
                owner = _boxed(box)
                seconds_to_connect = self.clock() - t0
                owner.bind_instance(result.hwnd)             # antes do snapshot (A.9 i)
                screen = self._first_screen(profile, owner, until=deadline, t0=t0, total=float(timeout),
                                            require_text=False)   # CICS depois de CLEAR: tela em branco pronta
            except BaseException:
                _release_quietly(box[0], letter)
                raise
        finally:
            self._release_admin(admin, owners, letter)
        info = OpenInfo(launched=False, closed_instances=0, hwnd=result.hwnd, hwnd_pid=result.hwnd_pid,
                        seconds_to_register=None, seconds_to_connect=round(seconds_to_connect, 3),
                        rows=result.rows or 24, cols=result.cols or 80, via="attach")
        return Opened(owner, info, profile, screen)

    # ----------------------------------------------------------------- forma usada pela ``session`` (P-E)
    def open_terminal(self, profile: Profile | str, *, reuse: bool = False, window: WindowMode = "normal",
                      timeout: float | None = None) -> tuple[Backend, OpenInfo]:
        """``open`` devolvendo ``(Backend de dono já com bind_instance, OpenInfo)``."""
        opened = self.open(profile, reuse=reuse, window=window, timeout=timeout)
        return opened.backend, opened.info

    def attach_terminal(self, profile: Profile | str, timeout: float = ATTACH_TIMEOUT) -> tuple[Backend, OpenInfo]:
        """``attach`` devolvendo ``(Backend de dono já com bind_instance, OpenInfo)``."""
        opened = self.attach(profile, timeout=timeout)
        return opened.backend, opened.info

    # ----------------------------------------------------------------- janela (E.6; nunca ativa)
    def show(self, hwnd: int) -> None:
        self.user32.show(hwnd, SW_SHOWNOACTIVATE)

    def minimize(self, hwnd: int) -> None:
        self.user32.show(hwnd, SW_SHOWMINNOACTIVE)

    def window_info(self, profile: Profile | str, hwnd: int) -> WindowInfo | None:
        """``WindowInfo`` da janela-raiz do HWND HLLAPI (título sanitizado); ``None`` se ela não existir mais."""
        profile = profiles.resolve(profile)
        root = self.user32.root_of(hwnd) if hwnd else 0
        if not root:
            return None
        snap = Snapshot(self.user32)
        win = snap.by_hwnd.get(root)
        if win is None:
            return None
        kind = snap.classify(win, profile.name) or "hllapi"
        return WindowInfo(hwnd=win.hwnd, pid=win.pid, title=sanitize(win.title), process=snap.image(win.pid),
                          kind=kind, visible=win.visible, minimized=win.iconic)


# ------------------------------------------------------------------------------------------- auxiliares puros

def _boxed(box: list[Backend | None]) -> Backend:
    backend = box[0]
    if backend is None:                              # só se ``_replace_owner`` falhou no meio (já levantou)
        raise RuntimeError("Backend de dono ausente")
    return backend


def _looks_ready(screen: Screen, require_text: bool = True) -> bool:
    """Tela pronta para o passo 7: livre, online, pronta e — só se ``require_text`` — não toda em branco."""
    oia = screen.oia
    return (not screen.host_busy and (not require_text or bool(screen.text.strip())) and bool(oia.online)
            and bool(oia.ready))


RootKind = Literal["rumba", "edge", "other"]


def root_kind(user32: User32, root: int) -> RootKind | None:
    """De quem é a janela-raiz ``root`` de uma sessão HLLAPI (``Session.show/minimize/window``, E.3).

    ``"rumba"`` = processo do RUMBA (launcher ou IE que não é aba do Edge); ``"edge"`` = Edge (inclusive a aba no modo
    IE, cuja janela é a do navegador inteiro); ``"other"`` = outro programa; ``None`` = a janela não existe mais."""
    snap = Snapshot(user32)
    win = snap.by_hwnd.get(root)
    if win is None:
        return None
    image = snap.image(win.pid)
    if image == EDGE_IMAGE:
        return "edge"
    if image not in RUMBA_IMAGES:
        return "other"
    if image == IE_IMAGE and snap.parent_image(win.pid) == EDGE_IMAGE:
        return "edge"
    return "rumba"


def _same(a: Screen, b: Screen) -> bool:
    return a.text == b.text and a.cursor == b.cursor


def parse_ie_output(raw: bytes | str) -> dict[str, Any]:
    """Última linha JSON da saída do ``_ie_open.js`` (ASCII com ``\\uXXXX``); ilegível -> ``{}``."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


# ------------------------------------------------------------------------------------------- API do módulo

def default_launcher(*, python32: str | os.PathLike[str] | None = None, user32: User32 | None = None,
                     get_backend: GetBackend | None = None) -> Launcher:
    """``Launcher`` de produção: ``RealUser32`` + ``_bridge.get_worker`` (P-D) + ``Popen`` (recusado no sandbox)."""
    if user32 is None:
        from ._user32 import RealUser32

        user32 = RealUser32()
    return Launcher(user32, get_backend or _default_get_backend(python32))


def close_all(profile: Profile | str, *, timeout: float = CLOSE_TIMEOUT, admin: Backend | None = None,
              launcher: Launcher | None = None) -> int:
    """E.4: fecha TODAS as instâncias do perfil (de qualquer script) e devolve quantas janelas fechou."""
    return (launcher or default_launcher()).close_all(profile, timeout=timeout, admin=admin)


def open_terminal(profile: Profile | str, *, reuse: bool = False, window: WindowMode = "normal",
                  timeout: float | None = None, python32: str | os.PathLike[str] | None = None,
                  launcher: Launcher | None = None) -> tuple[Backend, OpenInfo]:
    """E.5: ``(backend, info)``; a ``Session`` é criada por quem chamou (P-E). ``Launcher.open`` dá também a 1ª tela."""
    return (launcher or default_launcher(python32=python32)).open_terminal(profile, reuse=reuse, window=window,
                                                                           timeout=timeout)


def attach_terminal(profile: Profile | str, timeout: float = ATTACH_TIMEOUT, *,
                    python32: str | os.PathLike[str] | None = None,
                    launcher: Launcher | None = None) -> tuple[Backend, OpenInfo]:
    """E.6: conecta sem abrir nem fechar janelas; ``(backend, info)``."""
    return (launcher or default_launcher(python32=python32)).attach_terminal(profile, timeout=timeout)
