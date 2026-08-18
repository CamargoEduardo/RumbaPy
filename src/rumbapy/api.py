from typing import Tuple, Any, Callable
from win32com.client import Dispatch
from dataclasses import dataclass
from pywinauto import Desktop
from functools import wraps

import win32process
import pythoncom
import threading
import platform
import logging
import ctypes
import time

logger = logging.getLogger(__name__)

ERROR_CODES = {
    "WD_ConnectPS": {
        0: "Function successful; host PS unlocked and ready for input",
        1: "Invalid host presentation space ID entered",
        4: "Connection succeeded, but host PS was busy",
        5: "Connection succeeded, but host PS was locked (input inhibited)",
        9: "System error occurred",
        11: "Requested PS was in use by another application"
    },
    "WD_DisconnectPS": {
        0: "Function successful",
        1: "Application was not connected with a host PS",
        9: "System error occurred"
    },
    "WD_SendKey": {
        0: "Function successful",
        1: "Application was not connected with a host PS",
        2: "Incorrect parameter entered",
        4: "Host session busy; not all keystrokes sent",
        5: "Host session inhibited; not all keystrokes sent",
        9: "System error occurred"
    },
    "WD_Wait": {
        0: "Function successful (host ready for input)",
        1: "Application not connected with a host PS",
        4: "Host PS still busy or input inhibited",
        9: "System error occurred"
    },
    "WD_CopyPSToString": {
        0: "Successful; requested data copied to string",
        1: "Application not connected to valid PS",
        2: "String length was zero or exceeded PS end",
        4: "Data copied, but PS waiting for host response",
        5: "Data copied, but keyboard locked",
        7: "Invalid PS position specified",
        9: "System error occurred"
    },
    "WD_CopyStringToPS": {
        0: "Function successful",
        1: "Application not connected to a valid PS",
        2: "Invalid parameter specified",
        5: "PS busy or locked; data not copied",
        6: "String copied but truncated at end of field/screen",
        7: "Invalid PS position specified",
        9: "System error occurred",
        10: "Not supported on UNIX/VAX hosts"
    },
    "WD_CopyFieldToString": {
        0: "Success; field text copied to string",
        1: "Application not connected to host PS",
        2: "Parameter error detected",
        6: "String copied but truncated (shorter than field)",
        7: "Invalid PS position specified",
        9: "System error occurred",
        10: "Not supported on UNIX/VAX hosts",
        24: "Presentation space unformatted"
    },
    "WD_Reserve": {
        0: "Function successful",
        1: "Application not connected to valid PS",
        5: "Presentation space cannot be used",
        9: "System error occurred"
    },
    "WD_Release": {
        0: "Function successful",
        1: "Application not connected to valid PS",
        9: "System error occurred"
    },
    "WD_QuerySessionStatus": {
        0: "Function successful",
        1: "Invalid session ID",
        9: "System error occurred"
    }
}

KEYS = {
    "ENTER": "@E",
    "TAB": "@T",
    "BACKSPACE": "@<",
    "BACKTAB": "@B",
    "CLEAR": "@C",
    "DOWN": "@V",
    "LEFT": "@L",
    "RIGHT": "@Z",
    "UP": "@U",
    "DELETE": "@D",
    "ERASEEOF": "@F",
    "F1": "@1",
    "F2": "@2",
    "F3": "@3",
    "F4": "@4",
    "F5": "@5",
    "F6": "@6",
    "F7": "@7",
    "F8": "@8",
    "F9": "@9",
    "F10": "@a",
    "F11": "@b",
    "F12": "@c",
    "HOME": "@0",
    "PAGEUP": "@u",
    "PAGEDOWN": "@v",
    "RESET": "@R",
    "HELP": "@H",
    "INSERT": "@I"
}

@dataclass
class RumbaConfig:
    dll_path: str = "C:/Program Files (x86)/Micro Focus/RUMBA/system/ehlapi32.Dll"
    base_url: str = "http://rumba.srv.volvo.com/rumba694/w2hlegacy/index_pro_api.html"
    window_height: int = 700
    window_width: int = 925
    timeout: int = 120

def timeout(seconds: int = RumbaConfig.timeout) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            def target():
                nonlocal result, exception
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e

            result = [None]
            exception: list[None | BaseException] = [None]
            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(seconds)

            if thread.is_alive():
                raise TimeoutError(f"Function {func.__name__} timed out after {seconds} seconds")
            if exception[0]:
                raise exception[0]
            return result[0]
        return wrapper
    return decorator

class RumbaAPI:
    def __init__(self, gs_short_name_ims: str):
        self.config = RumbaConfig()
        self.wd_dll = self.load_dll()
        self.rumba_hwnd = 1
        self.gs_short_name_ims = gs_short_name_ims

    def load_dll(self) -> ctypes.CDLL:
        if platform.architecture()[0] != '32bit':
            logger.critical('Necessario utilizar a versão de 32bit do Python!')
            raise SystemError('Necessario utilizar a versão de 32bit do Python!')
        try:
            return ctypes.windll.LoadLibrary(self.config.dll_path)
        except OSError as e:
            raise SystemError(f'Erro ao carregar a DLL: {e}')

    def navigate_to_terminal(self, terminal_type: str) -> bool:
        try:
            # Abrindo uma nova guia do RUMBA no IE
            pythoncom.CoInitialize()
            IE = Dispatch('{D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E}')
            IE.Visible = False
            IE.Navigate(self.config.base_url)

            for _ in range(60):
                if not getattr(IE, 'Busy', False) and getattr(IE, 'ReadyState', 0) == 4:
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Timeout aguardando o Internet Explorer carregar.')

            # Clica no link correto do terminal
            htmlDoc = IE.Document
            for link in htmlDoc.getElementsByTagName('a'):
                if link.innerText == terminal_type:  # ex: BRAZIL D
                    link.click()
                    break

            parent_hwnd = IE.HWND
            _, parent_pid  = win32process.GetWindowThreadProcessId(parent_hwnd)

            IE.Quit()

            rumba_window = None
            for _ in range(60):
                shellWindows = Dispatch("Shell.Application").Windows()
                for window in shellWindows:
                    try: w_hwnd = window.HWND
                    except: continue

                    _, window_pid = win32process.GetWindowThreadProcessId(w_hwnd)
                    if window_pid == parent_pid:
                        self.rumba_hwnd = w_hwnd
                        rumba_window = window
                        break
                if rumba_window:
                    break  # encontrou a janela
                time.sleep(1)
            else:
                raise TimeoutError("Timeout aguardando a janela do Rumba aparecer.")

            for _ in range(61):
                if not getattr(rumba_window, 'Busy', False) and getattr(rumba_window, 'ReadyState', 0) == 4:
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Timeout a janela do terminal RUMBA carregar')

            rumba_window.Height = self.config.window_height
            rumba_window.Width  = self.config.window_width
            return True
        finally:
            pythoncom.CoUninitialize()

    def open_cics(self) -> bool:
        if self.gs_short_name_ims in ('D', 'Z'):
            return self.navigate_to_terminal(f'BRAZIL {self.gs_short_name_ims}')
        raise KeyError(f'O terminal RUMBA {self.gs_short_name_ims} não existe!')

    def open_rhelp(self) -> bool:
        return self.navigate_to_terminal("IMS A")

    def close_terminal(self) -> bool:
        try:
            self.wd_disconnect_ps()
            pattern = {
                'A': '.*IMS_A.*',
                'D': '.*BRAZIL_D.*',
                'Z': '.*BRAZIL_Z.*'
            }
            p = pattern.get(self.gs_short_name_ims)
            if p:
                windows = Desktop(backend='uia').windows(title_re=p, visible_only=False)
                for win in windows:
                    try:
                        win.close()
                        logger.debug(f'Janela do RumbaAPI {win.window_text()} fechada com sucesso!')
                    except Exception as e:
                        logger.warning(f'Erro ao encerrar a janela {win.window_text()}: {e}')
            return True
        except Exception as e:
            logger.error(f'Erro ao encerrar o terminal do Rumba: {e}')
            return False

    def wd_connect_ps(self) -> int:
        wd_connect_ps = self.wd_dll.WD_ConnectPS
        wd_connect_ps.argtypes = [ctypes.c_long, ctypes.c_char_p]
        wd_connect_ps.restype = ctypes.c_long

        result = wd_connect_ps(self.rumba_hwnd, self.gs_short_name_ims.encode("ascii"))
        return result

    def wd_disconnect_ps(self) -> int:
        ws_disconnect_ps = self.wd_dll.WD_DisconnectPS
        ws_disconnect_ps.argtypes = [ctypes.c_int]
        ws_disconnect_ps.restype = ctypes.c_int

        result = ws_disconnect_ps(self.rumba_hwnd)
        return result

    def wd_copy_ps_to_string(self, position: int, length: int) -> Tuple[str, int]:
        wd_copy_ps_to_string_var = self.wd_dll.WD_CopyPSToString
        wd_copy_ps_to_string_var.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        wd_copy_ps_to_string_var.restype = ctypes.c_long

        buffer = ctypes.create_string_buffer(length)
        n_ret_value = wd_copy_ps_to_string_var(self.rumba_hwnd, position, buffer, length)

        return buffer.value.decode("utf-8"), n_ret_value

    def wd_copy_string_to_ps(self, position: int, buffer: str, length: int) -> int:
        wd_copy_string_to_ps = self.wd_dll.WD_CopyStringToPS
        wd_copy_string_to_ps.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        wd_copy_string_to_ps.restype = ctypes.c_int

        result = wd_copy_string_to_ps(self.rumba_hwnd, position, buffer.encode("ascii"), length)
        return result

    def wd_query_session_status(self) -> int:
        wd_query_session_status = self.wd_dll.WD_QuerySessionStatus
        wd_query_session_status.argtypes = [ctypes.c_long, ctypes.c_char_p]
        wd_query_session_status.restype = ctypes.c_int

        return wd_query_session_status(self.rumba_hwnd, self.gs_short_name_ims.encode("ascii"))

    def wd_copy_field_to_string(self, position, length=80) -> Tuple[str, int]:
        wd_copy_field_to_string = self.wd_dll.WD_CopyFieldToString
        wd_copy_field_to_string.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        wd_copy_field_to_string.restype = ctypes.c_int

        buffer = ctypes.create_string_buffer(length)

        result = wd_copy_field_to_string(self.rumba_hwnd, position, buffer, length)
        return buffer.value.decode("utf-8").rstrip('\0'), result

    def wd_wait(self) -> int:
        wd_wait = self.wd_dll.WD_Wait
        wd_wait.argtypes = [ctypes.c_int]
        wd_wait.restype = ctypes.c_int
        result = wd_wait(self.rumba_hwnd)

        return result

    @timeout()
    def wd_send_key(self, mnemonic: str) -> int:
        wd_send_key = self.wd_dll.WD_SendKey
        wd_send_key.argtypes = [ctypes.c_int, ctypes.c_char_p]
        wd_send_key.restype = ctypes.c_int
        command = KEYS.get(mnemonic.upper(), mnemonic).encode("ascii")

        result = wd_send_key(self.rumba_hwnd, command)
        self.wd_wait()

        # if result != 0:
        #     raise Exception(ERROR_CODES['WD_SendKey'][result])
        return result

    @timeout()
    def copy_string_to_ps(self, y: int, x: int, indata: str) -> int:
        position = x + y * 80 - 80
        length = len(indata)
        result = self.wd_copy_string_to_ps(position, indata, length)
        self.wd_wait()

        return result

    @timeout()
    def copy_ps_to_string(self, y: int, x: int, length: int) -> str:
        position = x + y * 80 - 80
        sBuffer, _ = self.wd_copy_ps_to_string(position, length)
        self.wd_wait()

        return sBuffer

    @timeout()
    def copy_field(self, y, x) -> int:
        position = x + y * 80 - 80
        value, _ = self.wd_copy_field_to_string(position)
        self.wd_wait()

        return value
