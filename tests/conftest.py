"""Configuração raiz dos testes (J.2): o sandbox é ligado AQUI, antes de qualquer import de ``rumbapy``.

Sem ``RUMBAPY_LIVE=1``:

* ``RUMBAPY_SANDBOX=1``, ``RUMBAPY_DEV=1``, ``RUMBAPY_QUIET=1`` e ``RUMBAPY_HOME=<pasta temporária da sessão>``
  (o código de produção recusa DLL real, launcher, cscript, ``PostMessage``/``ShowWindow`` e pip/rede);
* sentinelas (defesa em profundidade, também contra bugs nos próprios testes): neste processo, qualquer tentativa de
  carregar a ``ehlapi32.Dll``, mandar mensagem/mostrar/ativar janela real (``PostMessageW``, ``SendMessageW``,
  ``ShowWindow``…), abrir o RUMBA/Web Launcher/IE/cscript/shell, matar processo que não seja filho do teste, abrir a
  rede fora do loopback ou conectar nas portas dos servidores v0.1.5 (8500/8600/8700/8800) levanta
  ``SentinelViolation`` (``BaseException``: não é engolida por ``except Exception``) e o teste falha mesmo que o
  código engula o erro. Caminhos cobertos: ``ctypes.CDLL/WinDLL`` e ``ctypes.windll``, ``_ctypes.LoadLibrary``,
  ``kernel32.LoadLibrary*``; funções obtidas por ``dll.Nome``/``dll["Nome"]``, por protótipos
  ``WINFUNCTYPE/CFUNCTYPE(...)(("Nome", dll))`` e por ``dll._FuncPtr`` (conferidas na CHAMADA: criar é livre);
  ``_winapi.CreateProcess`` (``subprocess``, ``multiprocessing``), ``os.system``/``os.spawnv*``/``os.exec*`` (CRT) e
  ``os.startfile`` (também em ``nt``), ``webbrowser``, ``os.kill``, ``socket.connect*``;
* testes marcados ``live`` são pulados.

Com ``RUMBAPY_LIVE=1`` (roteiros ao vivo, J.6) nada disso é ligado e só rodam testes marcados ``live``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import contextlib
import webbrowser
import threading
import tempfile
import _winapi
import _ctypes
import atexit
import ctypes
import shutil
import signal
import socket
import nt
import os

import pytest

LIVE = os.environ.get("RUMBAPY_LIVE") == "1"
SANDBOX_HOME: str | None = None

if not LIVE:
    SANDBOX_HOME = tempfile.mkdtemp(prefix="rumbapy-tests-")
    atexit.register(shutil.rmtree, SANDBOX_HOME, ignore_errors=True)   # também se a sessão não chegar ao fim
    os.environ["RUMBAPY_SANDBOX"] = "1"
    os.environ["RUMBAPY_DEV"] = "1"
    os.environ["RUMBAPY_QUIET"] = "1"
    os.environ["RUMBAPY_HOME"] = SANDBOX_HOME


# ------------------------------------------------------------------------------------------- sentinelas

class SentinelViolation(BaseException):
    """Ação perigosa bloqueada pelas sentinelas dos testes."""


class Sentinel:
    """Registro das violações; ``children`` = PIDs criados por este processo de teste (podem receber sinais)."""

    Violation = SentinelViolation

    def __init__(self) -> None:
        self.installed = False
        self.violations: list[str] = []
        self.children: set[int] = set()
        self._lock = threading.Lock()

    def violate(self, what: str) -> None:
        with self._lock:
            self.violations.append(what)
        raise SentinelViolation(f"sentinela dos testes bloqueou: {what}")

    @contextmanager
    def expect(self) -> Iterator[list[str]]:
        """Para os testes das próprias sentinelas: captura (e consome) as violações ocorridas no bloco."""
        caught: list[str] = []
        with self._lock:
            start = len(self.violations)
        try:
            yield caught
        finally:
            with self._lock:
                caught.extend(self.violations[start:])
                del self.violations[start:]


SENTINEL = Sentinel()

BLOCKED_DLL_TOKENS = ("ehlapi", "hllapi", "pcshll", "wdapi")
BLOCKED_FUNCTIONS: dict[str, frozenset[str]] = {
    "user32": frozenset({
        "PostMessageW", "PostMessageA", "SendMessageW", "SendMessageA", "SendMessageTimeoutW", "SendMessageTimeoutA",
        "SendNotifyMessageW", "SendNotifyMessageA", "SendMessageCallbackW", "SendMessageCallbackA",
        "PostThreadMessageW", "PostThreadMessageA", "BroadcastSystemMessageW", "BroadcastSystemMessageA",
        "ShowWindow", "ShowWindowAsync", "SetForegroundWindow", "BringWindowToTop", "SetWindowPos", "MoveWindow",
        "SetActiveWindow", "SetFocus", "SwitchToThisWindow", "DestroyWindow", "CloseWindow", "EndDialog",
        "SetWindowTextW", "SetWindowTextA", "SendInput", "keybd_event", "mouse_event", "ExitWindowsEx",
    }),
    "kernel32": frozenset({
        "TerminateProcess", "TerminateThread", "CreateRemoteThread", "CreateRemoteThreadEx", "WriteProcessMemory",
        "VirtualAllocEx", "DebugActiveProcess", "GenerateConsoleCtrlEvent", "CreateProcessW", "CreateProcessA",
        "WinExec",
    }),
    "shell32": frozenset({"ShellExecuteW", "ShellExecuteA", "ShellExecuteExW", "ShellExecuteExA"}),
    "ntdll": frozenset({"NtTerminateProcess", "ZwTerminateProcess", "NtCreateThreadEx", "RtlCreateUserThread"}),
    "advapi32": frozenset({"CreateProcessAsUserW", "CreateProcessAsUserA", "CreateProcessWithLogonW"}),
}
BLOCKED_FUNCTIONS["kernelbase"] = BLOCKED_FUNCTIONS["kernel32"]
LOAD_LIBRARY_FUNCTIONS = frozenset({"LoadLibraryW", "LoadLibraryA", "LoadLibraryExW", "LoadLibraryExA"})
# PROCESS_TERMINATE | CREATE_THREAD | VM_OPERATION | VM_WRITE | SET_INFORMATION | SUSPEND_RESUME | WRITE_DAC |
# WRITE_OWNER | MAXIMUM_ALLOWED | GENERIC_ALL | GENERIC_WRITE
DANGEROUS_PROCESS_ACCESS = 0x0001 | 0x0002 | 0x0008 | 0x0020 | 0x0200 | 0x0800 | 0x40000 | 0x80000 | 0x02000000 \
    | 0x10000000 | 0x40000000
BLOCKED_PROGRAMS = frozenset({
    "host access web launcher.exe", "iexplore.exe", "msedge.exe", "cscript.exe", "wscript.exe", "mshta.exe",
    "rundll32.exe", "explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "taskkill.exe", "tskill.exe",
    "nmservicemanager.exe",
})
LEGACY_PORTS = frozenset({8500, 8600, 8700, 8800})


def _blocked_dll(name: Any) -> bool:
    if name is None:
        return False
    try:
        text = os.fspath(name)
    except TypeError:
        text = str(name)
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    base = os.path.basename(text).lower()
    return any(token in base for token in BLOCKED_DLL_TOKENS)


def _value(arg: Any) -> Any:
    return getattr(arg, "value", arg)


def _executable(application: Any, command_line: Any) -> str:
    """Executável de uma chamada CreateProcess (caminho, minúsculo)."""
    if application:
        return str(application).lower()
    text = str(command_line or "").strip()
    if text.startswith('"'):
        return text[1:].split('"', 1)[0].lower()
    return text.split(" ", 1)[0].lower()


class _GuardedFunction:
    """Envolve uma função ctypes perigosa: confere os argumentos antes de chamar (atributos vão para a real)."""

    __slots__ = ("_func", "_check")

    def __init__(self, func: Any, check: Callable[[tuple], None]) -> None:
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_check", check)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._func, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._func, name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._check(args)
        return self._func(*args, **kwargs)


_CHECK_ATTR = "_rumbapy_sentinel_check"
_PROTOTYPE_CACHE: dict[type, type] = {}


def _guarded_prototype(proto: type) -> type:
    """Subclasse de um tipo de ponteiro de função do ctypes cujas instâncias ``proto(("Nome", dll))`` conferem a função
    a cada CHAMADA (``WINFUNCTYPE(...)(("PostMessageW", user32))`` e ``dll._FuncPtr`` não passam por ``__getitem__``).

    Criar continua livre (módulos criam protótipos no import); o objeto continua sendo um ponteiro de função de
    verdade (``argtypes``, ``cast``, callbacks). Um protótipo por tipo original (cache): ``isinstance`` segue valendo.
    """
    if getattr(proto, "_rumbapy_guarded", False):
        return proto
    cached = _PROTOTYPE_CACHE.get(proto)
    if cached is not None:
        return cached

    def __new__(cls: type, *args: Any, **kwargs: Any) -> Any:
        self = proto.__new__(cls, *args, **kwargs)
        spec = args[0] if args else None
        if isinstance(spec, tuple) and len(spec) >= 2 and isinstance(spec[0], str):
            check = _function_check(getattr(spec[1], "_name", ""), spec[0])
            if check is not None:
                self.__dict__[_CHECK_ATTR] = check
        return self

    def __call__(self: Any, *args: Any, **kwargs: Any) -> Any:
        check = self.__dict__.get(_CHECK_ATTR)
        if check is not None:
            check(args)
        return proto.__call__(self, *args, **kwargs)

    namespace: dict[str, Any] = {"__new__": __new__, "__call__": __call__, "__module__": proto.__module__,
                                 "__qualname__": proto.__qualname__, "_rumbapy_guarded": True}
    for attr in ("_flags_", "_restype_", "_argtypes_"):     # o metatipo do ctypes só lê o dict da própria classe
        if hasattr(proto, attr):
            namespace[attr] = getattr(proto, attr)
    guarded = type(proto)(proto.__name__, (proto,), namespace)
    _PROTOTYPE_CACHE[proto] = guarded
    return guarded


def _is_guarded(func: Any) -> bool:
    """A função ctypes passa pela sentinela antes de chegar à função real?"""
    return isinstance(func, _GuardedFunction) or _CHECK_ATTR in getattr(func, "__dict__", {})


def _function_check(dll_name: Any, func: str) -> Callable[[tuple], None] | None:
    base = os.path.basename(str(dll_name or "")).lower()
    base = base[:-4] if base.endswith(".dll") else base
    if func in BLOCKED_FUNCTIONS.get(base, ()):
        return lambda args: SENTINEL.violate(f"{base}.{func}")
    if base in ("kernel32", "kernelbase") and func in LOAD_LIBRARY_FUNCTIONS:
        def check_load(args: tuple) -> None:
            if args and _blocked_dll(_value(args[0])):
                SENTINEL.violate(f"{base}.{func}({_value(args[0])!r})")
        return check_load
    if base in ("kernel32", "kernelbase") and func == "OpenProcess":
        def check_open(args: tuple) -> None:
            if len(args) >= 3 and int(_value(args[0]) or 0) & DANGEROUS_PROCESS_ACCESS \
                    and int(_value(args[2]) or 0) not in SENTINEL.children:
                SENTINEL.violate(f"{base}.OpenProcess(acesso=0x{int(_value(args[0])):X}, pid={_value(args[2])})")
        return check_open
    return None


def _check_create_process(application: Any, command_line: Any, env: Any) -> None:
    executable = _executable(application, command_line)
    program = os.path.basename(executable)
    if program and not os.path.splitext(program)[1]:
        program += ".exe"                                   # o CreateProcess completa o .exe sozinho
    text = f"{application or ''} {command_line or ''}".lower()
    if program in BLOCKED_PROGRAMS or "hawlauncher" in text:
        SENTINEL.violate(f"criar processo {program!r}")
    if "micro focus" in executable:
        SENTINEL.violate(f"criar processo do RUMBA ({program!r})")
    if "ehlapi" in text or "--dll" in text:
        effective = env if isinstance(env, dict) else os.environ
        if "--fake-dll" not in text or str(effective.get("RUMBAPY_SANDBOX")) != "1":
            SENTINEL.violate("worker com a DLL real (sem --fake-dll ou sem RUMBAPY_SANDBOX=1)")


def _check_kill(pid: Any, sig: Any) -> None:
    """``os.kill`` só para filhos deste processo; ``CTRL_C_EVENT`` nunca (no Windows ele vai para o console todo)."""
    if sig == signal.CTRL_C_EVENT or int(pid) not in SENTINEL.children:
        SENTINEL.violate(f"os.kill(pid={pid}, sinal={sig})")


def _check_address(family: Any, address: Any) -> None:
    if family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple) or len(address) < 2:
        return
    host, port = str(address[0]).lower(), address[1]
    loopback = host in ("localhost", "::1") or host.startswith("127.")
    if not loopback:
        SENTINEL.violate(f"conexão de rede para {host}:{port}")
    if port in LEGACY_PORTS:
        SENTINEL.violate(f"conexão na porta {port} (servidor RumbaPy v0.1.5 de verdade?)")


def _command_text(args: Any) -> str:
    try:
        return " ".join(os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args)
    except TypeError:
        return str(args)


def _install_sentinels() -> None:
    # 1) carregar a DLL do HLLAPI por qualquer caminho do ctypes (o nome do carregador interno muda entre versões do
    #    Python: ctypes._dlopen ou ctypes._LoadLibrary; _ctypes.LoadLibrary é o de baixo nível)
    original_init = ctypes.CDLL.__init__

    def guarded_init(self: Any, name: Any, *args: Any, **kwargs: Any) -> None:
        if _blocked_dll(name):
            SENTINEL.violate(f"carregar {name!r}")
        original_init(self, name, *args, **kwargs)
        self._FuncPtr = _guarded_prototype(self._FuncPtr)      # dll._FuncPtr(("Nome", dll)) também é conferido

    ctypes.CDLL.__init__ = guarded_init  # type: ignore[method-assign]
    for module, attr in ((ctypes, "_dlopen"), (ctypes, "_LoadLibrary"), (_ctypes, "LoadLibrary")):
        loader = getattr(module, attr, None)
        if callable(loader):
            def guarded_loader(name: Any, *args: Any, _loader: Any = loader, _where: str = attr, **kwargs: Any) -> Any:
                if _blocked_dll(name):
                    SENTINEL.violate(f"carregar {name!r} ({_where})")
                return _loader(name, *args, **kwargs)

            setattr(module, attr, guarded_loader)

    # 2) funções perigosas de user32/kernel32/shell32/ntdll/advapi32: por dll.Nome ou dll["Nome"], por protótipo
    #    WINFUNCTYPE/CFUNCTYPE(...)(("Nome", dll)) e por dll._FuncPtr — sempre conferidas na chamada
    original_getitem = ctypes.CDLL.__getitem__

    def guarded_getitem(self: Any, name_or_ordinal: Any) -> Any:
        func = original_getitem(self, name_or_ordinal)
        if isinstance(name_or_ordinal, str) and not _is_guarded(func):     # CDLL criado antes das sentinelas
            check = _function_check(getattr(self, "_name", ""), name_or_ordinal)
            if check is not None:
                return _GuardedFunction(func, check)
        return func

    ctypes.CDLL.__getitem__ = guarded_getitem  # type: ignore[method-assign]
    for factory_name in ("WINFUNCTYPE", "CFUNCTYPE"):
        factory = getattr(ctypes, factory_name, None)
        if callable(factory):
            def guarded_functype(restype: Any, *argtypes: Any, _factory: Any = factory, **kwargs: Any) -> type:
                return _guarded_prototype(_factory(restype, *argtypes, **kwargs))

            guarded_functype.__name__ = guarded_functype.__qualname__ = f"guarded_{factory_name.lower()}"
            setattr(ctypes, factory_name, guarded_functype)

    # 3) processos: CreateProcess (subprocess, multiprocessing), OpenProcess com direito de matar, os.kill
    original_create = _winapi.CreateProcess

    def guarded_create(application: Any, command_line: Any, *args: Any) -> Any:
        env = args[4] if len(args) > 4 else None
        _check_create_process(application, command_line, env)
        result = original_create(application, command_line, *args)
        with contextlib.suppress(Exception):
            SENTINEL.children.add(int(result[2]))
        return result

    _winapi.CreateProcess = guarded_create

    original_open = _winapi.OpenProcess

    def guarded_open(access: int, inherit: bool, pid: int) -> Any:
        if int(access) & DANGEROUS_PROCESS_ACCESS and int(pid) not in SENTINEL.children:
            SENTINEL.violate(f"_winapi.OpenProcess(acesso=0x{int(access):X}, pid={pid})")
        return original_open(access, inherit, pid)

    _winapi.OpenProcess = guarded_open

    original_kill = os.kill

    def guarded_kill(pid: int, sig: int) -> None:
        _check_kill(pid, sig)
        original_kill(pid, sig)

    os.kill = guarded_kill

    def blocked_startfile(*args: Any, **kwargs: Any) -> None:
        SENTINEL.violate(f"os.startfile{args!r}")

    # o CRT cria processos sem passar por _winapi.CreateProcess: os.system (sempre cmd.exe), os.spawnv/spawnve
    # (mesmas regras do CreateProcess; spawnl/spawnle chamam estes) e os.exec* (substituiria o processo do pytest;
    # execl/execlp/execvp… chamam execv/execve). Troca em os E em nt (o módulo de baixo nível).
    def blocked_system(command: Any) -> int:
        SENTINEL.violate(f"os.system({command!r})")
        return 1

    def blocked_exec(path: Any, *args: Any, **kwargs: Any) -> None:
        SENTINEL.violate(f"os.exec*({path!r})")

    original_spawnv, original_spawnve = os.spawnv, os.spawnve

    def guarded_spawnv(mode: int, path: Any, args: Any) -> int:
        _check_create_process(_command_text([path]), _command_text(args), None)
        return original_spawnv(mode, path, args)

    def guarded_spawnve(mode: int, path: Any, args: Any, env: Any) -> int:
        _check_create_process(_command_text([path]), _command_text(args), dict(env))
        return original_spawnve(mode, path, args, env)

    for module in (os, nt):
        module.startfile = blocked_startfile  # type: ignore[attr-defined]
        module.system = blocked_system
        module.execv = blocked_exec
        module.execve = blocked_exec
        module.spawnv = guarded_spawnv  # type: ignore[attr-defined]
        module.spawnve = guarded_spawnve  # type: ignore[attr-defined]

    for name in ("open", "open_new", "open_new_tab"):
        def blocked_browser(*args: Any, _name: str = name, **kwargs: Any) -> bool:
            SENTINEL.violate(f"webbrowser.{_name}{args!r}")
            return False

        setattr(webbrowser, name, blocked_browser)

    # 4) rede: só loopback, e nunca nas portas dos servidores v0.1.5
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        _check_address(self.family, address)
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        _check_address(self.family, address)
        return original_connect_ex(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]

    SENTINEL.installed = True


SENTINEL.blocked_dll = _blocked_dll                    # verificações puras, expostas para os testes das sentinelas
SENTINEL.check_create_process = _check_create_process
SENTINEL.check_address = _check_address
SENTINEL.check_kill = _check_kill
SENTINEL.function_check = _function_check              # (dll, função) -> verificação (sem chamar a função real)
SENTINEL.is_guarded = _is_guarded                      # confira ANTES de chamar algo que teria efeito real

if not LIVE:
    _install_sentinels()


# ------------------------------------------------------------------------------------------- hooks e fixtures

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE:
        skip = pytest.mark.skip(reason="com RUMBAPY_LIVE=1 só rodam testes marcados 'live' (sandbox desligado)")
        for item in items:
            if "live" not in item.keywords:
                item.add_marker(skip)
    else:
        skip = pytest.mark.skip(reason="teste ao vivo: exige RUMBAPY_LIVE=1 (J.6)")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if SANDBOX_HOME:
        shutil.rmtree(SANDBOX_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def _sentinel_guard() -> Iterator[None]:
    """Falha o teste se alguma sentinela disparou durante ele (mesmo que o código tenha engolido o erro)."""
    if not SENTINEL.installed:
        yield
        return
    start = len(SENTINEL.violations)
    yield
    new = SENTINEL.violations[start:]
    if new:
        del SENTINEL.violations[start:]
        pytest.fail("sentinela dos testes disparou: " + "; ".join(new), pytrace=False)


@pytest.fixture
def sentinel() -> Sentinel:
    """Acesso às sentinelas (``sentinel.expect()``, ``sentinel.Violation``)."""
    return SENTINEL


@pytest.fixture
def sandbox_home() -> str:
    """Pasta temporária usada como ``RUMBAPY_HOME`` nesta sessão de testes."""
    if SANDBOX_HOME is None:
        pytest.skip("sem sandbox (RUMBAPY_LIVE=1)")
    return SANDBOX_HOME
