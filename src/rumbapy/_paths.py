"""Pastas, nomes e gravação atômica (A.5) — fonte única de caminhos e nomes do RumbaPy.

Nada é criado no import: as pastas nascem sob demanda (``atomic_write``, ``ensure_dir``, travas e logs).

* Raiz: ``HOME = %LOCALAPPDATA%\\rumbapy`` (``RUMBAPY_HOME`` sobrescreve e vira caminho absoluto; com
  ``RUMBAPY_SANDBOX=1`` é obrigatório).
* ``user_tag()`` = ``sha256(f"{SID}|{sessão do Windows}")[:12]``; no sandbox a pasta ``HOME`` entra no hash, para que os
  nomes globais da sessão (pipe, evento de parada) dos testes nunca coincidam com os de um worker de verdade.
"""
# ruff: noqa: I001
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import ParameterError, SandboxViolation

import threading
import hashlib
import logging
import secrets
import json
import time
import os

logger = logging.getLogger(__name__)

APP_DIR = "rumbapy"
RESERVED_LETTER = "_"                      # worker temporário do doctor (nunca conecta)
PIPE_PREFIX = r"\\.\pipe\rumbapy2-"
EVENT_PREFIX = r"Local\rumbapy2-"
UPDATE_LOCK = "update"
KEY_LOCK = "key"
PY32_LOCK = "py32"
BOOT_LOGS_KEEP = 5
ATOMIC_ATTEMPTS = 20                       # os.replace em PermissionError: até 20 tentativas de 50 ms (R23)
ATOMIC_DELAY = 0.05

_tag_lock = threading.Lock()
_tag_cache: dict[str, str] = {}


# ------------------------------------------------------------------------------------------- raiz e pastas

def sandboxed() -> bool:
    """``RUMBAPY_SANDBOX=1`` (testes): o código de produção recusa ações perigosas."""
    return os.environ.get("RUMBAPY_SANDBOX") == "1"


def home() -> Path:
    """Pasta de dados do RumbaPy, sempre ABSOLUTA (não cria).

    ``RUMBAPY_HOME`` relativo é resolvido contra o diretório atual: o worker roda com ``cwd=HOME`` (D.1), então quem
    repassa a pasta a um subprocesso deve passar ``str(home())``, nunca o valor cru da variável.
    """
    override = os.environ.get("RUMBAPY_HOME")
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    if sandboxed():
        raise SandboxViolation.make(acao="RUMBAPY_HOME é obrigatório com RUMBAPY_SANDBOX=1")
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_DIR


def run_dir() -> Path:
    return home() / "run"


def logs_dir() -> Path:
    return home() / "logs"


def boot_logs_dir() -> Path:
    return logs_dir() / "boot"


def locks_dir() -> Path:
    return home() / "locks"


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """Cria a pasta (e as de cima) se preciso e a devolve."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------------------------------- identidade e nomes

def _check_letter(letter: str) -> str:
    if not (isinstance(letter, str) and len(letter) == 1 and (letter == RESERVED_LETTER or "A" <= letter <= "Z")):
        raise ParameterError.make(detalhe=f"Letra de sessão inválida: {letter!r} (use A-Z maiúscula).")
    return letter


def user_tag() -> str:
    """Identifica usuário + sessão do Windows nos nomes (12 hex). Cache por processo."""
    key = f"sandbox|{home()}" if sandboxed() else "real"
    with _tag_lock:
        cached = _tag_cache.get(key)
        if cached is None:
            from . import _win32               # tardio: _paths fica sem ctypes no import (usado pelo _update)

            base = f"{_win32.user_sid()}|{_win32.session_id()}"
            if sandboxed():
                base += f"|{key}"
            cached = hashlib.sha256(base.encode()).hexdigest()[:12]
            _tag_cache[key] = cached
        return cached


def _tag(tag: str | None) -> str:
    return user_tag() if tag is None else tag


def worker_registry(letter: str, *, tag: str | None = None) -> Path:
    """``run\\worker-<tag>-<L>.json`` (D.7)."""
    return run_dir() / f"worker-{_tag(tag)}-{_check_letter(letter)}.json"


def launched_registry(letter: str, *, tag: str | None = None) -> Path:
    """``run\\launched-<tag>-<L>.json`` = ``{"pids": [{"pid", "created", "at"}]}`` (últimas 5)."""
    return run_dir() / f"launched-{_tag(tag)}-{_check_letter(letter)}.json"


def new_pipe_rand() -> str:
    """Sufixo aleatório do pipe (gerado pelo cliente a cada spawn, R3)."""
    return secrets.token_hex(8)


def pipe_name(letter: str, rand: str, *, tag: str | None = None) -> str:
    """``\\\\.\\pipe\\rumbapy2-<tag>-<L>-<rand>`` (o cliente nunca deduz: lê do registro do worker)."""
    if not (isinstance(rand, str) and rand and all(c in "0123456789abcdef" for c in rand)):
        raise ParameterError.make(detalhe="Sufixo de pipe inválido (use new_pipe_rand()).")
    return f"{PIPE_PREFIX}{_tag(tag)}-{_check_letter(letter)}-{rand}"


def stop_event_name(letter: str, *, tag: str | None = None) -> str:
    """``Local\\rumbapy2-<tag>-<L>-stop`` (evento manual-reset)."""
    return f"{EVENT_PREFIX}{_tag(tag)}-{_check_letter(letter)}-stop"


def spawn_lock_name(letter: str, *, tag: str | None = None) -> str:
    """Nome da trava de spawn da letra (``locks\\spawn-<tag>-<L>.lock``)."""
    return f"spawn-{_tag(tag)}-{_check_letter(letter)}"


def lock_path(name: str) -> Path:
    """``locks\\<name>.lock``."""
    if not (isinstance(name, str) and name and all(c.isalnum() or c in "-_" for c in name)):
        raise ParameterError.make(detalhe=f"Nome de trava inválido: {name!r}.")
    return locks_dir() / f"{name}.lock"


def authkey_path() -> Path:
    return home() / "authkey.bin"


def update_state_path() -> Path:
    return home() / "update-state.json"


def heal_state_path() -> Path:
    return home() / "heal-state.json"


def update_in_progress_path() -> Path:
    return home() / "update-in-progress.json"


def runtime_cache_path() -> Path:
    return home() / "runtime.json"


# ------------------------------------------------------------------------------------------- logs

def client_log_path(day: date | None = None) -> Path:
    """``logs\\client-AAAAMMDD.log``."""
    day = day or date.today()
    return logs_dir() / f"client-{day:%Y%m%d}.log"


def worker_log_path(letter: str, *, tag: str | None = None) -> Path:
    """``logs\\worker-<tag>-<L>.log`` (+ ``.1``..``.5`` pela rotação)."""
    return logs_dir() / f"worker-{_tag(tag)}-{_check_letter(letter)}.log"


def update_log_path() -> Path:
    return logs_dir() / "update.log"


def pip_log_path(when: datetime | None = None) -> Path:
    """``logs\\pip-AAAAMMDD-HHMMSS.log``."""
    when = when or datetime.now()
    return logs_dir() / f"pip-{when:%Y%m%d-%H%M%S}.log"


def boot_log_path(letter: str, *, tag: str | None = None, when: datetime | None = None) -> Path:
    """``logs\\boot\\worker-<tag>-<L>-<AAAAMMDD-HHMMSS-fff>.txt`` (stderr do worker; um arquivo novo por spawn)."""
    when = when or datetime.now()
    stamp = f"{when:%Y%m%d-%H%M%S}-{when.microsecond // 1000:03d}"
    return boot_logs_dir() / f"worker-{_tag(tag)}-{_check_letter(letter)}-{stamp}.txt"


def prune_boot_logs(letter: str, *, tag: str | None = None, keep: int = BOOT_LOGS_KEEP) -> int:
    """Mantém só os ``keep`` boot logs mais novos da letra; ignora arquivos em uso. Devolve quantos apagou."""
    pattern = f"worker-{_tag(tag)}-{_check_letter(letter)}-*.txt"
    try:
        files = sorted(boot_logs_dir().glob(pattern), key=lambda p: p.name)
    except OSError:
        return 0
    removed = 0
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
            removed += 1
        except OSError:                    # em uso por um worker vivo (stderr) ou já apagado
            continue
    return removed


# ------------------------------------------------------------------------------------------- gravação e leitura

def atomic_write(path: str | os.PathLike[str], data: bytes) -> None:
    """Grava ``path.tmp-<rand>`` e troca por ``os.replace`` (até 20 tentativas de 50 ms em ``PermissionError``)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ParameterError.make(detalhe=f"atomic_write espera bytes (recebi {type(data).__name__}).")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{secrets.token_hex(4)}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        for attempt in range(ATOMIC_ATTEMPTS):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt == ATOMIC_ATTEMPTS - 1:
                    raise
                time.sleep(ATOMIC_DELAY)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: str | os.PathLike[str], obj: Any) -> None:
    """``atomic_write`` de um objeto JSON (UTF-8)."""
    atomic_write(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def read_json(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Lê um JSON-objeto. Ausente -> ``None`` (só DEBUG: é o caso normal, ex. nenhum worker ainda);
    ilegível, inválido ou não-objeto -> ``None`` + WARNING."""
    target = Path(path)
    raw = b""
    for attempt in range(ATOMIC_ATTEMPTS):
        try:
            raw = target.read_bytes()
            break
        except FileNotFoundError:
            logger.debug("read_json: %s ausente", target.name)
            return None
        except PermissionError:            # outro processo trocando o arquivo neste instante
            if attempt == ATOMIC_ATTEMPTS - 1:
                logger.warning("read_json: %s continua bloqueado; ignorado", target.name)
                return None
            time.sleep(ATOMIC_DELAY)
        except OSError as exc:
            logger.warning("read_json: não consegui ler %s (%s); ignorado", target.name, type(exc).__name__)
            return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning("read_json: JSON inválido em %s; ignorado", target.name)
        return None
    if not isinstance(obj, dict):
        logger.warning("read_json: %s não contém um objeto JSON; ignorado", target.name)
        return None
    return obj
