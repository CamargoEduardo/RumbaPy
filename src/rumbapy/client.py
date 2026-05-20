"""
Cliente que se comunica com o servidor Rumba persistente.
"""
from typing import Any, Literal

from . import config as cf

import subprocess
import logging
import socket
import time
import json

Mnemonic = Literal[
    "ENTER", "TAB", "BACKSPACE", "BACKTAB", "CLEAR", "DOWN", "LEFT", "RIGHT",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "UP", "DELETE", "ERASEEOF", "HOME", "PAGEUP", "PAGEDOWN", "RESET", "HELP",
    "INSERT"
]

logger = logging.getLogger(__name__)

class RumbaClient:
    def __init__(self, terminal_type: str, config_path: str = "config.ini"):
        self.terminal_type = terminal_type
        self.config = cf.Config(config_path)

        self.python_32bit = self.config.paths.python_32bit

        self.session_id = f"{terminal_type}_{int(time.time())}"
        self.HOST = 'localhost'

        ports = {'D': 8500, 'A': 8600, 'Z': 8700}
        self.PORT = ports.get(terminal_type, 8800)
        self.connected = False

        self._start_server()

    def _start_server(self):
        """Verifica se o servidor está ativo e, se não estiver, inicia."""
        try:
            # Tenta conectar ao servidor para verificar se está rodando
            result = self._send_command('ping')
            if result.get('success', False):
                logger.debug("Conectado ao servidor Rumba existente")
                return
        except Exception:
            logger.debug("Servidor Rumba não está em execução, iniciando...")

        # Se chegou aqui, precisa iniciar o servidor
        logger.debug(f"Iniciando servidor Rumba usando Python 32-bit: {self.python_32bit}")
        subprocess.Popen(
            [self.python_32bit, "-m", "rumbapy.server", f"--terminal_type={self.terminal_type}"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Aguarda até 30s o servidor iniciar
        for _ in range(30):
            try:
                if self._send_command('ping').get('success'):
                    logger.debug("Servidor Rumba iniciado com sucesso.")
                    return
            except:
                pass
            time.sleep(1)
        raise RuntimeError("Timeout aguardando o servidor Rumba iniciar")

    def _send_command(self, action: str, params: dict | None = None):
        """Envia comando ao servidor Rumba e retorna resposta como dict."""
        if params is None:
            params = {}

        # Adicionar terminal_type aos parâmetros se não estiver presente
        if 'terminal_type' not in params:
            params['terminal_type'] = self.terminal_type

        request = json.dumps({
            "session_id": self.session_id,
            "action": action,
            "params": params
        })

        # Criar socket TCP
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.HOST, self.PORT))

            # Enviar request
            s.sendall(request.encode('utf-8'))

            # Receber resposta
            data = b""
            while chunk := s.recv(4096):
                data += chunk
                if len(chunk) < 4096:
                    break

        try:
            result: dict[str, Any] = json.loads(data.decode('utf-8'))
        except json.JSONDecodeError:
            raise RuntimeError(f"Resposta inválida do servidor: {data[:100]}")

        if not result.get('success', False):
            error = result.get("error", "Erro desconhecido")
            raise RuntimeError(f"Comando '{action}' falhou: {error}")

        return result

    def logon_cics(self) -> bool:
        self._send_command("logon_cics")
        self.connected = True
        return True

    def logon_rhelp(self) -> bool:
        self._send_command("logon_rhelp")
        self.connected = True
        return True

    def logon_and_go_to_SCOM_A(self) -> bool:
        self._send_command("logon_and_go_to_SCOM_A")
        self.connected = True
        return True

    def logon_and_go_to_SCPE_ALT(self) -> bool:
        self._send_command("logon_and_go_to_SCPE_ALT")
        self.connected = True
        return True

    def logon_and_go_to_SCPE_INC(self) -> bool:
        self._send_command("logon_and_go_to_SCPE_INC")
        self.connected = True
        return True

    def logon_and_go_to_SCPE_EXC(self) -> bool:
        self._send_command("logon_and_go_to_SCPE_EXC")
        self.connected = True
        return True

    def send_key(self, key: Mnemonic) -> bool:
        self._send_command("send_key", {"key": key})
        return True

    def copy_ps_to_string(self, y: int, x: int, length: int) -> str:
        result = self._send_command("copy_ps_to_string", {"y": y, "x": x, "length": length})
        return result.get("text", "")

    def copy_string_to_ps(self, y: int, x: int, text: str) -> bool:
        self._send_command("copy_string_to_ps", {"y": y, "x": x, "text": text})
        return True

    def copy_field(self, y: int, x: int) -> str:
        result = self._send_command("copy_field", {"y": y, "x": x})
        return result.get("text", "")

    def screen_load(self, y: int, x: int, text: str, text_diff: bool = False) -> bool:
        """Aguarda até que um texto específico apareça na tela"""
        self._send_command('screen_load', {
            'y': y,
            'x': x,
            'text': text,
            'text_diff': text_diff
        })
        return True

    def close_terminal(self) -> bool:
        self._send_command("close_terminal")
        self.connected = False
        return True

    def disconnect(self) -> bool:
        self._send_command("disconnect")
        self.connected = False
        return True

    def connect(self) -> bool:
        result = self._send_command('connect')
        self.connected = result.get('success', False)
        return self.connected
