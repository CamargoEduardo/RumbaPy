"""Contrato de ``_win32`` (D.9): kernel32/advapi32/iphlpapi e as 3 funções user32 que o worker usa.

Regras: ``ctypes.WinDLL(nome, use_last_error=True)`` em variáveis privadas, ``argtypes``/``restype`` explícitos em toda
função, ``ctypes.get_last_error()`` para erros, e esperas em objetos do kernel sempre em fatias de 100 ms (Ctrl+C
funciona). Nada aqui manda mensagem a janelas, ativa janelas ou mata processos; a única função com efeito visível
(``show_window_async``) recusa no sandbox (``RUMBAPY_SANDBOX=1`` -> ``SandboxViolation``).

Handles são devolvidos como ``int`` (0 = inválido). Erros do Windows viram ``OSError`` com ``winerror``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from .errors import SandboxViolation

import threading
import ctypes
import time
import os

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

PIPE_SDDL = "D:P(A;;GA;;;{sid})(A;;GA;;;SY)"
HLLAPI_ERROR_TITLE = "HLLAPI ERROR"
DIALOG_CLASS = "#32770"
SLICE_S = 0.1                                       # esperas em objetos do kernel: fatias de 100 ms

# ------------------------------------------------------------------------------------------- constantes do Windows
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_EVENT_MODIFY_STATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1
_SE_KERNEL_OBJECT = 6
_DACL_SECURITY_INFORMATION = 0x00000004
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_ERROR_BAD_LENGTH = 24
_ERROR_INSUFFICIENT_BUFFER = 122
_TH32CS_SNAPPROCESS = 0x00000002
_TH32CS_SNAPMODULE = 0x00000008
_TH32CS_SNAPMODULE32 = 0x00000010
_AF_INET = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3
_NO_ERROR = 0
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)


def _bind(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> Callable[..., Any]:
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    def as_int(self) -> int:
        return (int(self.dwHighDateTime) << 32) | int(self.dwLowDateTime)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class _MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("GlblcntUsage", wintypes.DWORD), ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wintypes.DWORD), ("hModule", ctypes.c_void_p), ("szModule", wintypes.WCHAR * 256),
                ("szExePath", wintypes.WCHAR * 260)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


_H = wintypes.HANDLE
_PDWORD = ctypes.POINTER(wintypes.DWORD)
_PULONG = ctypes.POINTER(wintypes.ULONG)
_PFILETIME = ctypes.POINTER(_FILETIME)
_PVOID = ctypes.c_void_p
_PPVOID = ctypes.POINTER(ctypes.c_void_p)
_PLPWSTR = ctypes.POINTER(wintypes.LPWSTR)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_GetCurrentProcess = _bind(_kernel32, "GetCurrentProcess", _H)
_GetCurrentProcessId = _bind(_kernel32, "GetCurrentProcessId", wintypes.DWORD)
_ProcessIdToSessionId = _bind(_kernel32, "ProcessIdToSessionId", wintypes.BOOL, wintypes.DWORD, _PDWORD)
_OpenProcess = _bind(_kernel32, "OpenProcess", _H, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_CloseHandle = _bind(_kernel32, "CloseHandle", wintypes.BOOL, _H)
_GetProcessTimes = _bind(_kernel32, "GetProcessTimes", wintypes.BOOL, _H, _PFILETIME, _PFILETIME, _PFILETIME,
                         _PFILETIME)
_GetExitCodeProcess = _bind(_kernel32, "GetExitCodeProcess", wintypes.BOOL, _H, _PDWORD)
_WaitForSingleObject = _bind(_kernel32, "WaitForSingleObject", wintypes.DWORD, _H, wintypes.DWORD)
_QueryFullProcessImageNameW = _bind(_kernel32, "QueryFullProcessImageNameW", wintypes.BOOL, _H, wintypes.DWORD,
                                    wintypes.LPWSTR, _PDWORD)
_CreateToolhelp32Snapshot = _bind(_kernel32, "CreateToolhelp32Snapshot", _H, wintypes.DWORD, wintypes.DWORD)
_Process32FirstW = _bind(_kernel32, "Process32FirstW", wintypes.BOOL, _H, ctypes.POINTER(_PROCESSENTRY32W))
_Process32NextW = _bind(_kernel32, "Process32NextW", wintypes.BOOL, _H, ctypes.POINTER(_PROCESSENTRY32W))
_Module32FirstW = _bind(_kernel32, "Module32FirstW", wintypes.BOOL, _H, ctypes.POINTER(_MODULEENTRY32W))
_Module32NextW = _bind(_kernel32, "Module32NextW", wintypes.BOOL, _H, ctypes.POINTER(_MODULEENTRY32W))
_GetNamedPipeServerProcessId = _bind(_kernel32, "GetNamedPipeServerProcessId", wintypes.BOOL, _H, _PULONG)
_GetNamedPipeClientProcessId = _bind(_kernel32, "GetNamedPipeClientProcessId", wintypes.BOOL, _H, _PULONG)
_CreateEventW = _bind(_kernel32, "CreateEventW", _H, _PVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_OpenEventW = _bind(_kernel32, "OpenEventW", _H, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_SetEvent = _bind(_kernel32, "SetEvent", wintypes.BOOL, _H)
_ResetEvent = _bind(_kernel32, "ResetEvent", wintypes.BOOL, _H)
_LocalFree = _bind(_kernel32, "LocalFree", _PVOID, _PVOID)

_OpenProcessToken = _bind(_advapi32, "OpenProcessToken", wintypes.BOOL, _H, wintypes.DWORD, ctypes.POINTER(_H))
_GetTokenInformation = _bind(_advapi32, "GetTokenInformation", wintypes.BOOL, _H, ctypes.c_int, _PVOID,
                             wintypes.DWORD, _PDWORD)
_ConvertSidToStringSidW = _bind(_advapi32, "ConvertSidToStringSidW", wintypes.BOOL, _PVOID, _PLPWSTR)
_ConvertStringSDToSD = _bind(_advapi32, "ConvertStringSecurityDescriptorToSecurityDescriptorW", wintypes.BOOL,
                             wintypes.LPCWSTR, wintypes.DWORD, _PPVOID, _PULONG)
_ConvertSDToStringSD = _bind(_advapi32, "ConvertSecurityDescriptorToStringSecurityDescriptorW", wintypes.BOOL,
                             _PVOID, wintypes.DWORD, wintypes.DWORD, _PLPWSTR, _PULONG)
_GetSecurityInfo = _bind(_advapi32, "GetSecurityInfo", wintypes.DWORD, _H, ctypes.c_int, wintypes.DWORD, _PPVOID,
                         _PPVOID, _PPVOID, _PPVOID, _PPVOID)

_IsWindow = _bind(_user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _bind(_user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
_GetWindowThreadProcessId = _bind(_user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, _PDWORD)
_ShowWindowAsync = _bind(_user32, "ShowWindowAsync", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_EnumWindows = _bind(_user32, "EnumWindows", wintypes.BOOL, _WNDENUMPROC, wintypes.LPARAM)
_GetClassNameW = _bind(_user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
# InternalGetWindowText lê o título guardado pelo sistema SEM mandar WM_GETTEXT (GetWindowTextW manda a mensagem de
# forma síncrona às janelas do próprio processo: travaria o watchdog se o dono do diálogo não processar mensagens)
_InternalGetWindowText = _bind(_user32, "InternalGetWindowText", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR,
                               ctypes.c_int)

_GetExtendedTcpTable = _bind(_iphlpapi, "GetExtendedTcpTable", wintypes.DWORD, _PVOID, _PDWORD, wintypes.BOOL,
                             wintypes.ULONG, ctypes.c_int, wintypes.ULONG)


def _error(what: str) -> OSError:
    err = ctypes.get_last_error()
    exc = ctypes.WinError(err)
    exc.strerror = f"{what}: {exc.strerror}"
    return exc


def _h(value: Any) -> int:
    """Handle devolvido pelo ctypes (``None``/int) -> int (0 = inválido)."""
    return int(value or 0)


def close_handle(h: int) -> None:
    """``CloseHandle`` (ignora 0)."""
    if h:
        _CloseHandle(h)


# ------------------------------------------------------------------------------------------- identidade

def user_sid() -> str:
    """SID do usuário do processo (``S-1-5-21-…``)."""
    token = _H()
    if not _OpenProcessToken(_GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _error("OpenProcessToken")
    try:
        size = wintypes.DWORD(0)
        _GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            raise _error("GetTokenInformation")
        buf = ctypes.create_string_buffer(size.value)
        if not _GetTokenInformation(token, _TOKEN_USER, buf, size, ctypes.byref(size)):
            raise _error("GetTokenInformation")
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]     # TOKEN_USER.User.Sid
        text = wintypes.LPWSTR()
        if not _ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise _error("ConvertSidToStringSidW")
        try:
            return str(text.value)
        finally:
            _LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        close_handle(_h(token.value))


def session_id() -> int:
    """``ProcessIdToSessionId(GetCurrentProcessId())``."""
    sid = wintypes.DWORD(0)
    if not _ProcessIdToSessionId(_GetCurrentProcessId(), ctypes.byref(sid)):
        raise _error("ProcessIdToSessionId")
    return int(sid.value)


# ------------------------------------------------------------------------------------------- processos

def _open_process(pid: int, access: int = _PROCESS_QUERY_LIMITED_INFORMATION) -> int:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return 0
    return _h(_OpenProcess(access, False, pid))


def _creation_time(h: int) -> int | None:
    created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not _GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                            ctypes.byref(user)):
        return None
    return created.as_int()


def process_alive(pid: int, created: int | None = None) -> bool:
    """O processo existe (e, se ``created`` for dado, é o mesmo: PID reutilizado conta como morto)?"""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False                     # sem chamada real a OpenProcess, get_last_error traria um erro antigo
    h = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE)
    if not h:
        # acesso negado = o processo existe, mas é de outro usuário: não dá para conferir a identidade
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED and created is None
    try:
        if _WaitForSingleObject(h, 0) == _WAIT_OBJECT_0:
            return False
        code = wintypes.DWORD(0)
        if _GetExitCodeProcess(h, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return False
        if created is not None:
            return _creation_time(h) == created
        return True
    finally:
        close_handle(h)


def process_created(pid: int) -> int | None:
    """Instante de criação (FILETIME de ``GetProcessTimes``) ou ``None`` se não existir."""
    h = _open_process(pid)
    if not h:
        return None
    try:
        return _creation_time(h)
    finally:
        close_handle(h)


def process_image(pid: int) -> str | None:
    """Basename minúsculo da imagem do processo."""
    h = _open_process(pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value).lower() or None
    finally:
        close_handle(h)


def _processes() -> list[tuple[int, int, str]]:
    """(pid, pid do pai, imagem) de todos os processos (Toolhelp32)."""
    snap = _h(_CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0))
    if not snap or snap == _INVALID_HANDLE:
        raise _error("CreateToolhelp32Snapshot")
    out: list[tuple[int, int, str]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), str(entry.szExeFile)))
            ok = _Process32NextW(snap, ctypes.byref(entry))
    finally:
        close_handle(snap)
    return out


def parent_pid(pid: int) -> int | None:
    """PID do pai (Toolhelp32)."""
    for child, parent, _image in _processes():
        if child == pid:
            return parent
    return None


def _has_module(pid: int, wanted: str) -> bool:
    for _attempt in range(3):
        snap = _h(_CreateToolhelp32Snapshot(_TH32CS_SNAPMODULE | _TH32CS_SNAPMODULE32, pid))
        if snap and snap != _INVALID_HANDLE:
            break
        if ctypes.get_last_error() != _ERROR_BAD_LENGTH:      # sem acesso / processo de 64 bits visto do 32: ignora
            return False
    else:
        return False
    try:
        entry = _MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(_MODULEENTRY32W)
        ok = _Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if str(entry.szModule).lower() == wanted:
                return True
            ok = _Module32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        close_handle(snap)


def hllapi_client_pids(dll_name: str = "ehlapi32.dll") -> list[int]:
    """Processos que têm o módulo ``dll_name`` carregado (só leitura: Toolhelp32)."""
    wanted = dll_name.lower()
    return [pid for pid, _parent, _image in _processes()
            if pid not in (0, 4) and _has_module(pid, wanted)]                # 0/4 = Idle/System


# ------------------------------------------------------------------------------------------- segurança do pipe

class SecurityAttributes:
    """``SECURITY_ATTRIBUTES`` com a DACL do pipe; mantém o descritor de segurança vivo enquanto existir."""

    def __init__(self, sddl: str) -> None:
        self.sddl = sddl
        self._sd = ctypes.c_void_p()
        if not _ConvertStringSDToSD(sddl, _SDDL_REVISION_1, ctypes.byref(self._sd), None):
            raise _error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        self._sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), self._sd, False)

    def address(self) -> int:
        """Endereço da estrutura, para ``CreateNamedPipe``."""
        return ctypes.addressof(self._sa)

    def close(self) -> None:
        sd, self._sd = self._sd, ctypes.c_void_p()
        if sd:
            _LocalFree(sd)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def pipe_security_attributes() -> SecurityAttributes:
    """DACL ``D:P(A;;GA;;;<SID do usuário>)(A;;GA;;;SY)`` para o pipe do worker (R3)."""
    return SecurityAttributes(PIPE_SDDL.format(sid=user_sid()))


def object_dacl_sddl(handle: int) -> str:
    """DACL de um objeto do kernel em SDDL (``GetSecurityInfo``; usado para conferir o pipe)."""
    psd = ctypes.c_void_p()
    rc = _GetSecurityInfo(handle, _SE_KERNEL_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None,
                          ctypes.byref(psd))
    if rc != _NO_ERROR:
        raise ctypes.WinError(rc)
    try:
        text = wintypes.LPWSTR()
        if not _ConvertSDToStringSD(psd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None):
            raise _error("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        try:
            return str(text.value)
        finally:
            _LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        _LocalFree(psd)


def pipe_server_pid(handle: int) -> int:
    """``GetNamedPipeServerProcessId``."""
    pid = wintypes.ULONG(0)
    if not _GetNamedPipeServerProcessId(handle, ctypes.byref(pid)):
        raise _error("GetNamedPipeServerProcessId")
    return int(pid.value)


def pipe_client_pid(handle: int) -> int:
    """``GetNamedPipeClientProcessId``."""
    pid = wintypes.ULONG(0)
    if not _GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        raise _error("GetNamedPipeClientProcessId")
    return int(pid.value)


# ------------------------------------------------------------------------------------------- eventos e esperas

def open_event(name: str, *, create: bool) -> int:
    """Abre (ou cria, manual-reset e não sinalizado) o evento nomeado ``name``. Falha -> ``OSError``."""
    if create:
        h = _h(_CreateEventW(None, True, False, name))
        if not h:
            raise _error("CreateEventW")
        return h
    h = _h(_OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, name))
    if not h:
        raise _error("OpenEventW")
    return h


def set_event(h: int) -> None:
    if not _SetEvent(h):
        raise _error("SetEvent")


def reset_event(h: int) -> None:
    if not _ResetEvent(h):
        raise _error("ResetEvent")


def event_is_set(h: int) -> bool:
    return _WaitForSingleObject(h, 0) in (_WAIT_OBJECT_0, _WAIT_ABANDONED)


def wait_handle(h: int, timeout: float) -> bool:
    """Espera o objeto do kernel em fatias de 100 ms (Ctrl+C funciona); ``True`` se sinalizado."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        remaining = deadline - time.monotonic()
        ms = int(max(0.0, min(SLICE_S, remaining)) * 1000)
        rc = _WaitForSingleObject(h, ms)
        if rc in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            return True
        if rc != _WAIT_TIMEOUT:
            raise _error("WaitForSingleObject")
        if remaining <= 0:
            return False


# ------------------------------------------------------------------------------------------- janelas (só leitura)

def window_thread_pid(hwnd: int) -> int:
    """PID dono da janela (``GetWindowThreadProcessId``); 0 se não existir."""
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(_IsWindow(hwnd))


def show_window_async(hwnd: int, cmd: int) -> None:
    """``ShowWindowAsync`` (nunca ativa a janela). Sandbox -> ``SandboxViolation``."""
    if os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SandboxViolation.make(acao=f"ShowWindowAsync(hwnd={hwnd}, cmd={cmd})")
    _ShowWindowAsync(hwnd, cmd)


def _window_text(fn: Callable[..., object], hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    fn(hwnd, buf, len(buf))
    return buf.value


_enum_lock = threading.Lock()


def top_level_windows() -> list[int]:
    """HWNDs de todas as janelas de nível superior (``EnumWindows``)."""
    found: list[int] = []

    def collect(hwnd: Any, _lparam: Any) -> bool:
        found.append(int(hwnd or 0))
        return True

    callback = _WNDENUMPROC(collect)
    with _enum_lock:
        _EnumWindows(callback, 0)
    return [h for h in found if h]


def hllapi_error_dialog_visible(pid: int | None = None) -> bool:
    """Há um ``#32770`` visível com título ``"HLLAPI ERROR"`` (do processo ``pid``, se dado)?"""
    for hwnd in top_level_windows():
        if not _IsWindowVisible(hwnd):
            continue
        if pid is not None and window_thread_pid(hwnd) != pid:
            continue
        if _window_text(_GetClassNameW, hwnd) != DIALOG_CLASS:
            continue
        if HLLAPI_ERROR_TITLE in _window_text(_InternalGetWindowText, hwnd).upper():
            return True
    return False


# ------------------------------------------------------------------------------------------- rede (só leitura)

def tcp_listener_pid(port: int) -> int | None:
    """PID que escuta em ``127.0.0.1:<port>`` (ou ``0.0.0.0``) (``GetExtendedTcpTable``; E.8)."""
    size = wintypes.DWORD(0)
    buf = None
    for _attempt in range(5):
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = _GetExtendedTcpTable(buf, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0)
        if rc == _NO_ERROR:
            break
        if rc != _ERROR_INSUFFICIENT_BUFFER:
            raise ctypes.WinError(rc)
    else:
        return None
    raw = buf.raw
    count = int.from_bytes(raw[0:4], "little")
    loopback = int.from_bytes(bytes([127, 0, 0, 1]), "little")
    for i in range(count):
        row = raw[4 + i * 24: 4 + (i + 1) * 24]                 # MIB_TCPROW_OWNER_PID: 6 DWORDs
        if len(row) < 24:
            break
        local_addr = int.from_bytes(row[4:8], "little")
        local_port = int.from_bytes(row[8:12], "little") & 0xFFFF
        local_port = ((local_port & 0xFF) << 8) | (local_port >> 8)       # ordem de rede -> host
        if local_port == port and local_addr in (loopback, 0):
            return int.from_bytes(row[20:24], "little")
    return None
