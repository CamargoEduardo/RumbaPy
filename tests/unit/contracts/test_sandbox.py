"""Aceite P-0: o ``tests/conftest.py`` liga o sandbox (J.2) e as sentinelas bloqueiam ações perigosas.

Cuidado ao mexer aqui: todo teste usa alvos inofensivos MESMO que a sentinela falhe (caminhos inexistentes, HWND 0,
PID impossível, endereço TEST-NET), e TODA chamada a algo bloqueado só acontece depois de conferir que a função foi
trocada pela versão bloqueada (``sentinel.is_guarded`` ou o ``__name__`` do substituto). Funções de injeção de
entrada (``keybd_event``, ``SendInput``, ``mouse_event``) nunca são chamadas: só a verificação delas é exercitada.
"""
# ruff: noqa: I001
from __future__ import annotations

from ctypes import wintypes
from pathlib import Path
from typing import Any

from rumbapy import _paths

import subprocess
import webbrowser
import tempfile
import _winapi
import ctypes
import signal
import socket
import sys
import os

import pytest

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def test_environment_is_sandboxed(sandbox_home: str) -> None:
    assert os.environ["RUMBAPY_SANDBOX"] == "1"
    assert os.environ["RUMBAPY_DEV"] == "1"
    assert os.environ["RUMBAPY_QUIET"] == "1"
    home = Path(os.environ["RUMBAPY_HOME"])
    assert home == Path(sandbox_home) and home.is_dir()
    assert Path(tempfile.gettempdir()).resolve() in home.resolve().parents
    real = Path(os.environ.get("LOCALAPPDATA", "Z:/nao-existe")) / "rumbapy"
    assert real.resolve() != home.resolve() and real.resolve() not in home.resolve().parents
    assert _paths.sandboxed() and _paths.home() == home
    assert os.environ.get("RUMBAPY_LIVE") != "1"


@pytest.mark.live
def test_live_tests_are_skipped_without_rumbapy_live() -> None:
    pytest.fail("um teste 'live' não pode rodar sem RUMBAPY_LIVE=1")


def test_sentinels_are_installed(sentinel: Any) -> None:
    assert sentinel.installed
    assert issubclass(sentinel.Violation, BaseException) and not issubclass(sentinel.Violation, Exception)


def test_blocks_loading_the_hllapi_dll(sentinel: Any, tmp_path: Path) -> None:
    missing = tmp_path / "nao-existe"           # inexistente: mesmo sem sentinela nada seria carregado
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            ctypes.WinDLL(str(missing / "ehlapi32.Dll"), use_last_error=True)
        with pytest.raises(sentinel.Violation):
            ctypes.CDLL(str(missing / "WHLLAPI.DLL"))
        with pytest.raises(sentinel.Violation):
            ctypes.windll.LoadLibrary(str(missing / "EHLAPI32.DLL"))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        with pytest.raises(sentinel.Violation):
            kernel32.LoadLibraryW(str(missing / "ehlapi32.dll"))
    assert len(caught) == 4
    assert sentinel.blocked_dll("C:/x/EhLaPi32.dll") and not sentinel.blocked_dll("user32")


WINDOW_CALLS = (("PostMessageW", (0, 0, 0, 0)), ("SendMessageW", (0, 0, 0, 0)), ("ShowWindow", (0, 0)),
                ("ShowWindowAsync", (0, 0)), ("SetForegroundWindow", (0,)), ("DestroyWindow", (0,)))   # HWND 0
INPUT_INJECTION = ("keybd_event", "SendInput", "mouse_event")       # iriam à janela em 1º plano: nunca chamadas


def test_blocks_window_messages_and_activation(sentinel: Any) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    post = user32.PostMessageW
    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]   # atributos vão para a real
    post.restype = wintypes.BOOL
    assert post.argtypes is not None and post.restype is wintypes.BOOL
    for name, _ in WINDOW_CALLS:
        assert sentinel.is_guarded(getattr(user32, name)), name        # conferido ANTES de qualquer chamada
    assert sentinel.is_guarded(user32["PostMessageA"])
    with sentinel.expect() as caught:
        for name, args in WINDOW_CALLS:
            with pytest.raises(sentinel.Violation):
                getattr(user32, name)(*args)
        with pytest.raises(sentinel.Violation):
            user32["PostMessageA"](0, 0, 0, 0)
    assert len(caught) == 7 and caught[0] == "user32.PostMessageW"
    assert not sentinel.is_guarded(user32.IsWindow) and user32.IsWindow(0) == 0   # leitura continua livre


def test_input_injection_is_guarded_without_ever_being_called(sentinel: Any) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    for name in INPUT_INJECTION:
        assert sentinel.is_guarded(getattr(user32, name)), name
    with sentinel.expect() as caught:
        for name in INPUT_INJECTION:
            check = sentinel.function_check("user32", name)
            with pytest.raises(sentinel.Violation):
                check((0, 0, 0, 0))                                   # só a verificação; a função real nunca
    assert caught == [f"user32.{name}" for name in INPUT_INJECTION]


def test_blocks_prototype_bound_functions(sentinel: Any) -> None:
    """``WINFUNCTYPE/CFUNCTYPE(...)(("Nome", dll))`` e ``dll._FuncPtr`` não passam por ``dll.Nome``/``dll["Nome"]``."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    assert ctypes.WINFUNCTYPE.__name__ == "guarded_winfunctype" and ctypes.CFUNCTYPE.__name__ == "guarded_cfunctype"
    signature = (wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    candidates = [(ctypes.WINFUNCTYPE(*signature)(("PostMessageW", user32)), (0, 0, 0, 0)),   # criar é livre
                  (ctypes.WINFUNCTYPE(*signature, use_last_error=True)(("SendMessageW", user32)), (0, 0, 0, 0)),
                  (ctypes.CFUNCTYPE(*signature)(("PostMessageA", user32)), (0, 0, 0, 0)),
                  (user32._FuncPtr(("ShowWindow", user32)), (0, 0))]
    for func, _ in candidates:
        assert sentinel.is_guarded(func)                              # conferido ANTES de chamar
    with sentinel.expect() as caught:
        for func, args in candidates:
            with pytest.raises(sentinel.Violation):
                func(*args)                                           # HWND 0: inofensivo mesmo sem sentinela
    assert caught == ["user32.PostMessageW", "user32.SendMessageW", "user32.PostMessageA", "user32.ShowWindow"]


def test_guarded_prototypes_keep_working() -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    proto = ctypes.WINFUNCTYPE(wintypes.DWORD)
    assert proto is ctypes.WINFUNCTYPE(wintypes.DWORD)                # um tipo por assinatura (isinstance/argtypes)
    get_pid = proto(("GetCurrentProcessId", kernel32))
    assert get_pid() == os.getpid() and isinstance(get_pid, proto)
    assert ctypes.cast(ctypes.cast(get_pid, ctypes.c_void_p), proto)() == os.getpid()
    callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda x: x + 1)   # callbacks (EnumWindowsProc…)
    assert callback(41) == 42
    raw = kernel32._FuncPtr(("GetCurrentProcessId", kernel32))
    raw.restype = wintypes.DWORD
    assert raw() == os.getpid()


def test_blocks_raw_loadlibrary(sentinel: Any, tmp_path: Path) -> None:
    import _ctypes

    assert _ctypes.LoadLibrary.__name__ == "guarded_loader"
    missing = tmp_path / "nao-existe" / "ehlapi32.dll"                # inexistente: nada seria carregado
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            _ctypes.LoadLibrary(str(missing))
    assert len(caught) == 1


def test_blocks_crt_process_creation(sentinel: Any) -> None:
    """``os.system``/``os.spawn*``/``os.exec*`` criam processos pelo CRT, sem passar por ``_winapi.CreateProcess``."""
    import nt

    for module in (os, nt):                                          # conferido ANTES de chamar
        assert module.system.__name__ == "blocked_system"
        assert module.execv.__name__ == module.execve.__name__ == "blocked_exec"
        assert module.spawnv.__name__ == "guarded_spawnv" and module.spawnve.__name__ == "guarded_spawnve"
        assert module.startfile.__name__ == "blocked_startfile"
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            os.system("exit 0")                                       # inofensivo mesmo que a sentinela falhe
        with pytest.raises(sentinel.Violation):
            os.spawnv(os.P_WAIT, cmd, ["cmd.exe", "/c", "exit", "0"])
        with pytest.raises(sentinel.Violation):
            os.spawnl(os.P_WAIT, cmd, "cmd.exe", "/c", "exit", "0")   # spawnl -> spawnv
        with pytest.raises(sentinel.Violation):
            nt.spawnve(os.P_WAIT, cmd, ["cmd.exe", "/c", "exit", "0"], dict(os.environ))
        with pytest.raises(sentinel.Violation):
            os.execv(sys.executable, [sys.executable, "-c", "pass"])  # substituiria o pytest
        with pytest.raises(sentinel.Violation):
            os.execl(sys.executable, sys.executable, "-c", "pass")    # execl -> execv
    assert len(caught) == 6


def test_blocks_process_termination(sentinel: Any) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assert sentinel.is_guarded(kernel32.TerminateProcess) and sentinel.is_guarded(kernel32.OpenProcess)
    assert _winapi.OpenProcess.__name__ == "guarded_open" and os.kill.__name__ == "guarded_kill"
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            kernel32.TerminateProcess(0, 1)                           # handle nulo: inofensivo mesmo sem sentinela
        with pytest.raises(sentinel.Violation):
            kernel32.OpenProcess(PROCESS_TERMINATE, False, os.getpid())
        with pytest.raises(sentinel.Violation):
            _winapi.OpenProcess(PROCESS_TERMINATE, False, os.getpid())
        with pytest.raises(sentinel.Violation):
            os.kill(2, signal.SIGTERM)                                # PID 2 não existe no Windows (múltiplos de 4)
    assert len(caught) == 4
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, os.getpid())
    assert handle
    kernel32.CloseHandle(handle)


def test_children_may_receive_signals_but_never_ctrl_c(sentinel: Any) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        assert child.pid in sentinel.children
        sentinel.check_kill(child.pid, signal.CTRL_BREAK_EVENT)        # permitido (só a verificação)
        with sentinel.expect() as caught:
            with pytest.raises(sentinel.Violation):
                sentinel.check_kill(child.pid, signal.CTRL_C_EVENT)
            with pytest.raises(sentinel.Violation):
                sentinel.check_kill(os.getpid(), signal.SIGTERM)
        assert len(caught) == 2
    finally:
        assert child.stdin is not None
        child.stdin.close()                                          # o filho sai sozinho
        assert child.wait(timeout=20) == 0


@pytest.mark.parametrize("application, command_line, env, blocked", [
    (None, '"C:\\Program Files (x86)\\Micro Focus\\Host Access Web Launcher\\Host Access Web Launcher.exe" '
           '"hawlauncher://https://portal/profpro/BRAZIL_D.html/microfocus_w2h_hawlauncher"', None, True),
    (None, "x.exe hawlauncher://algo", None, True),
    (None, "C:\\Windows\\System32\\cscript.exe //NoLogo //E:JScript x.js", None, True),
    (None, "iexplore.exe about:blank", None, True),
    (None, "cmd.exe /c exit 0", None, True),
    (None, "taskkill /F /PID 1234", None, True),                      # sem .exe: o Windows completa
    (None, "C:\\Windows\\System32\\taskkill.exe /F /PID 1234", None, True),
    (None, "powershell -Command Stop-Process 1234", None, True),
    (None, '"C:\\Program Files (x86)\\Micro Focus\\RUMBA\\System\\qualquer.exe"', None, True),
    (None, 'py32.exe -I -S -c BOOT pkg --dll "C:\\Program Files (x86)\\Micro Focus\\RUMBA\\System\\ehlapi32.Dll"',
     {"RUMBAPY_SANDBOX": "1"}, True),                                 # worker sem --fake-dll
    (None, "py32.exe -c BOOT pkg --dll x --fake-dll tests.fakes.scenarios:cics_login", {"PATH": "x"}, True),
    (None, "py32.exe -c BOOT pkg --dll x --fake-dll tests.fakes.scenarios:cics_login", {"RUMBAPY_SANDBOX": "1"},
     False),
    ("C:\\Python\\python.exe", "python.exe -c pass", None, False),
])
def test_create_process_rules(sentinel: Any, application: str | None, command_line: str,
                              env: dict[str, str] | None, blocked: bool) -> None:
    with sentinel.expect() as caught:
        if blocked:
            with pytest.raises(sentinel.Violation):
                sentinel.check_create_process(application, command_line, env)
        else:
            sentinel.check_create_process(application, command_line, env)
    assert len(caught) == int(blocked)


def test_popen_of_a_blocked_program_never_starts(sentinel: Any) -> None:
    assert _winapi.CreateProcess.__name__ == "guarded_create"
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            subprocess.Popen(["cmd.exe", "/c", "exit", "0"])           # inofensivo mesmo que a sentinela falhe
    assert caught == ["criar processo 'cmd.exe'"]


def test_blocks_startfile_and_browser(sentinel: Any) -> None:
    assert os.startfile.__name__ == "blocked_startfile"                # type: ignore[attr-defined]
    assert webbrowser.open.__name__ == "blocked_browser"
    with sentinel.expect() as caught:
        with pytest.raises(sentinel.Violation):
            os.startfile("Z:\\nao-existe\\nada.txt")                   # type: ignore[attr-defined]
        with pytest.raises(sentinel.Violation):
            webbrowser.open("https://exemplo.invalid/")
    assert len(caught) == 2


def test_network_rules(sentinel: Any) -> None:
    with sentinel.expect() as caught:
        for family, address in ((socket.AF_INET, ("192.0.2.1", 443)), (socket.AF_INET, ("127.0.0.1", 8500)),
                                (socket.AF_INET, ("localhost", 8700)), (socket.AF_INET6, ("::1", 8600))):
            with pytest.raises(sentinel.Violation):
                sentinel.check_address(family, address)
        sentinel.check_address(socket.AF_INET, ("127.0.0.1", 50123))
        sentinel.check_address(socket.AF_INET6, ("::1", 50123))
    assert len(caught) == 4
    assert socket.socket.connect.__name__ == "guarded_connect"
    assert socket.socket.connect_ex.__name__ == "guarded_connect_ex"
    with sentinel.expect() as caught:
        # socket próprio num "with": a SentinelViolation (BaseException) escapa da limpeza de create_connection e
        # deixaria o socket aberto (ResourceWarning com -X dev -W error)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            with pytest.raises(sentinel.Violation):
                sock.connect(("192.0.2.1", 9))                        # TEST-NET-1: nada sairia de qualquer jeito
            with pytest.raises(sentinel.Violation):
                sock.connect_ex(("192.0.2.1", 9))
    assert len(caught) == 2


def test_loopback_on_free_ports_still_works() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            conn, _ = server.accept()
            conn.close()


def test_swallowed_violation_is_still_recorded(sentinel: Any) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    assert sentinel.is_guarded(user32.ShowWindow)
    with sentinel.expect() as caught:
        try:
            user32.ShowWindow(0, 7)
        except BaseException:
            pass                                                      # código que engole tudo
    assert caught == ["user32.ShowWindow"]                            # o guard autouse reprovaria o teste
