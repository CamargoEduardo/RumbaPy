"""Contrato de ``_win32`` (D.9): kernel32/advapi32/iphlpapi e as 3 funções user32 que o worker usa.

P-0 congela as assinaturas e as constantes; a implementação (ctypes) é do pacote P-B. Regras para a implementação:
``ctypes.WinDLL(nome, use_last_error=True)`` em variáveis privadas, ``argtypes``/``restype`` explícitos em toda função,
``ctypes.get_last_error()`` para erros, e esperas em objetos do kernel sempre em fatias de 100 ms.
Com ``RUMBAPY_SANDBOX=1``, ``show_window_async`` levanta ``SandboxViolation``.
"""
# ruff: noqa: I001
from __future__ import annotations

from .errors import SandboxViolation

import os

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

_P_B = "pendente: implementação do pacote P-B (worker + IPC)"


class SecurityAttributes:
    """``SECURITY_ATTRIBUTES`` com a DACL do pipe; mantém o descritor de segurança vivo enquanto existir (P-B)."""

    def address(self) -> int:
        """Endereço da estrutura, para ``CreateNamedPipe``."""
        raise NotImplementedError(f"SecurityAttributes.address: {_P_B}")


def user_sid() -> str:
    """SID do usuário do processo (``S-1-5-21-…``)."""
    raise NotImplementedError(f"user_sid: {_P_B}")


def session_id() -> int:
    """``ProcessIdToSessionId(GetCurrentProcessId())``."""
    raise NotImplementedError(f"session_id: {_P_B}")


def process_alive(pid: int, created: int | None = None) -> bool:
    """O processo existe (e, se ``created`` for dado, é o mesmo: PID reutilizado conta como morto)?"""
    raise NotImplementedError(f"process_alive: {_P_B}")


def process_created(pid: int) -> int | None:
    """Instante de criação (FILETIME de ``GetProcessTimes``) ou ``None`` se não existir."""
    raise NotImplementedError(f"process_created: {_P_B}")


def process_image(pid: int) -> str | None:
    """Basename minúsculo da imagem do processo."""
    raise NotImplementedError(f"process_image: {_P_B}")


def parent_pid(pid: int) -> int | None:
    """PID do pai (Toolhelp32)."""
    raise NotImplementedError(f"parent_pid: {_P_B}")


def hllapi_client_pids(dll_name: str = "ehlapi32.dll") -> list[int]:
    """Processos que têm o módulo ``dll_name`` carregado."""
    raise NotImplementedError(f"hllapi_client_pids: {_P_B}")


def pipe_security_attributes() -> SecurityAttributes:
    """DACL ``D:P(A;;GA;;;<SID do usuário>)(A;;GA;;;SY)`` para o pipe do worker (R3)."""
    raise NotImplementedError(f"pipe_security_attributes: {_P_B}")


def pipe_server_pid(handle: int) -> int:
    """``GetNamedPipeServerProcessId``."""
    raise NotImplementedError(f"pipe_server_pid: {_P_B}")


def pipe_client_pid(handle: int) -> int:
    """``GetNamedPipeClientProcessId``."""
    raise NotImplementedError(f"pipe_client_pid: {_P_B}")


def open_event(name: str, *, create: bool) -> int:
    """Abre (ou cria, manual-reset) o evento nomeado ``name``."""
    raise NotImplementedError(f"open_event: {_P_B}")


def set_event(h: int) -> None:
    raise NotImplementedError(f"set_event: {_P_B}")


def reset_event(h: int) -> None:
    raise NotImplementedError(f"reset_event: {_P_B}")


def event_is_set(h: int) -> bool:
    raise NotImplementedError(f"event_is_set: {_P_B}")


def wait_handle(h: int, timeout: float) -> bool:
    """Espera o objeto do kernel em fatias de 100 ms (Ctrl+C funciona); ``True`` se sinalizado."""
    raise NotImplementedError(f"wait_handle: {_P_B}")


def window_thread_pid(hwnd: int) -> int:
    """PID dono da janela (``GetWindowThreadProcessId``); 0 se não existir."""
    raise NotImplementedError(f"window_thread_pid: {_P_B}")


def is_window(hwnd: int) -> bool:
    raise NotImplementedError(f"is_window: {_P_B}")


def show_window_async(hwnd: int, cmd: int) -> None:
    """``ShowWindowAsync`` (nunca ativa a janela). Sandbox -> ``SandboxViolation``."""
    if os.environ.get("RUMBAPY_SANDBOX") == "1":
        raise SandboxViolation.make(acao=f"ShowWindowAsync(hwnd={hwnd}, cmd={cmd})")
    raise NotImplementedError(f"show_window_async: {_P_B}")


def hllapi_error_dialog_visible(pid: int | None = None) -> bool:
    """Há um ``#32770`` visível com título ``"HLLAPI ERROR"`` (do processo ``pid``, se dado)?"""
    raise NotImplementedError(f"hllapi_error_dialog_visible: {_P_B}")


def tcp_listener_pid(port: int) -> int | None:
    """PID que escuta em ``127.0.0.1:<port>`` (``GetExtendedTcpTable``; usado na neutralização da v0.1.5, E.8)."""
    raise NotImplementedError(f"tcp_listener_pid: {_P_B}")
