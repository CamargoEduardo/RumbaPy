"""
Cliente que se comunica com o servidor Rumba persistente.
"""
from importlib.metadata import distribution, PackageNotFoundError
from typing import Any, Literal
from pathlib import Path
from uuid import uuid4

import subprocess
import logging
import socket
import time
import json
import sys

Mnemonic = Literal[
    "ENTER",  "TAB",  "BACKSPACE",  "BACKTAB",  "CLEAR", "DOWN",
    "LEFT",  "RIGHT",  "F1", "F2", "F3", "F4", "F5", "F6", "F7",
    "F8", "F9", "F10", "F11", "F12", "UP", "DELETE", "ERASEEOF",
    "HOME",  "PAGEUP",  "PAGEDOWN",  "RESET",  "HELP", "INSERT"
]

logger = logging.getLogger(__name__)

RUMBAPY_REPO = "https://github.com/CamargoEduardo/RumbaPy.git"

def find_python_32bit():
    base = Path.home() / "AppData/Local/Programs/Python"
    candidates: list[tuple[tuple[int, int, int], Path]] = []
    for python_exe in base.glob("Python*/python.exe"):
        try:
            result = subprocess.run(
                [
                    str(python_exe),
                    "-c",
                    (
                        "import struct, sys; "
                        "print(struct.calcsize('P') * 8); "
                        "print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
                    )
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            output = result.stdout.strip().splitlines()
            if len(output) < 2:
                continue

            architecture = output[0].strip()
            version = tuple(map(int, output[1].strip().split(".")))
            if architecture == "32" and version >= (3, 13, 0):
                candidates.append((version, python_exe))
        except (subprocess.SubprocessError, ValueError, OSError):
            continue
    if not candidates:
        raise ValueError("Nenhuma versão do Python 32 bits >= 3.13 foi encontrada.")

    _, python_32bit = max(candidates, key=lambda item: item[0])
    return python_32bit

def update_python_32bit(python_32bit: Path | str) -> None:
    python = str(python_32bit)

    check_script = """
        import json
        from importlib.metadata import distribution, PackageNotFoundError

        result = {}

        for name in ("pywin32", "pywinauto", "rumbapy"):
            try:
                dist = distribution(name)

                info = {
                    "installed": True,
                    "version": dist.version,
                }

                if name == "rumbapy":
                    direct_url = dist.read_text("direct_url.json")

                    if direct_url:
                        data = json.loads(direct_url)
                        info["commit"] = (
                            data
                            .get("vcs_info", {})
                            .get("commit_id")
                        )
                    else:
                        info["commit"] = None

                result[name] = info

            except PackageNotFoundError:
                result[name] = {
                    "installed": False
                }

        print(json.dumps(result))
        """

    # Uma única chamada ao Python 32-bit para verificar tudo
    result = subprocess.run(
        [python, "-c", check_script],
        capture_output=True,
        text=True,
        check=True,
    )

    packages = json.loads(result.stdout)

    # ---------------------------------------------------------
    # pywin32 / pywinauto
    # ---------------------------------------------------------

    missing = [
        package
        for package in ("pywin32", "pywinauto")
        if not packages[package]["installed"]
    ]

    if missing:
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                *missing,
            ],
            check=True,
        )

    # ---------------------------------------------------------
    # RumbaPy
    # ---------------------------------------------------------

    rumbapy = packages["rumbapy"]

    remote_commit = subprocess.run(
        [
            "git",
            "ls-remote",
            RUMBAPY_REPO,
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]

    if not rumbapy["installed"]:
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"git+{RUMBAPY_REPO}",
            ],
            check=True,
        )

    elif rumbapy.get("commit") != remote_commit:
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--force-reinstall",
                "--no-deps",
                f"git+{RUMBAPY_REPO}",
            ],
            check=True,
        )

def update_python_64bit() -> None:
    python_64bit = sys.executable

    try:
        dist = distribution("rumbapy")
        direct_url = dist.read_text("direct_url.json")

        if direct_url:
            data = json.loads(direct_url)
            installed_commit = (
                data
                .get("vcs_info", {})
                .get("commit_id")
            )
        else:
            installed_commit = None

    except PackageNotFoundError:
        installed_commit = None

    remote_commit = subprocess.run(
        [
            "git",
            "ls-remote",
            RUMBAPY_REPO,
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]

    if installed_commit == remote_commit:
        return

    command = [
        python_64bit,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]

    if installed_commit is not None:
        command.extend([
            "--force-reinstall",
            "--no-deps",
        ])

    command.append(f"git+{RUMBAPY_REPO}")

    subprocess.run(
        command,
        check=True,
    )

class RumbaClient:
    def __init__(self,
                 terminal_type: str,
                 python32bit_path: Path | str | None = None,
                 auto_update: bool = True):
        self.terminal_type = terminal_type

        self.python_32bit = (
            Path(python32bit_path)
            if python32bit_path
            else find_python_32bit()
        )
        if auto_update:
            update_python_32bit(self.python_32bit)
            update_python_64bit()

        self.session_id = f"{terminal_type}_{uuid4().hex}"
        self.HOST = 'localhost'

        ports = {'D': 8500, 'A': 8600, 'Z': 8700}
        self.PORT = ports.get(terminal_type, 8800)
        self.connected = False

        self._start_server()


    def _start_server(self):
        """Verifica se o servidor está ativo e, se não estiver, inicia."""
        try:
            result = self._send_command('ping')
            if result.get('success', False):
                logger.debug("Conectado ao servidor Rumba existente")
                return
        except ConnectionRefusedError:
            logger.debug("Servidor Rumba não está em execução, iniciando...")
        except OSError as e:
            if e.winerror == 10061:
                logger.debug("Servidor Rumba não está em execução, iniciando...")
            else:
                raise

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

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.HOST, self.PORT))
            s.sendall(request.encode('utf-8'))
            s.shutdown(socket.SHUT_WR)

            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk

        try:
            result: dict[str, Any] = json.loads(data.decode('utf-8'))
        except json.JSONDecodeError:
            raise RuntimeError(f"Resposta inválida do servidor: {data[:100]}")

        if not result.get('success', False):
            error = result.get("error", "Erro desconhecido")
            raise RuntimeError(f"Comando '{action}' falhou: {error}")

        return result

    def logon_cics(self,  uid: str, pwd: str) -> bool:
        self._send_command("logon_cics", {'uid': uid, 'pwd': pwd})
        self.connected = True
        return True

    def logon_rhelp(self,  uid: str, pwd: str) -> bool:
        self._send_command("logon_rhelp", {'uid': uid, 'pwd': pwd})
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
