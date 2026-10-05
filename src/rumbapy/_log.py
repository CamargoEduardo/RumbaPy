"""Logging do RumbaPy (D.7): formato comum aos dois lados, handler rotativo tolerante, log diário do cliente e
``notice()`` (avisos curtos em stderr).

Regra de ouro: nenhum texto lido da tela ou escrito nela vai para log, em nível nenhum (R15, C21, I15).
"""
# ruff: noqa: I001
from __future__ import annotations

from logging.handlers import RotatingFileHandler
from datetime import date, datetime, timedelta
from typing import Any

from . import _paths

import threading
import logging
import time
import sys
import os

logger = logging.getLogger(__name__)

FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(process)d %(threadName)s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
WORKER_MAX_BYTES = 5_000_000
WORKER_BACKUPS = 5
CLIENT_KEEP_DAYS = 14
ROLLOVER_RETRY_S = 60.0          # depois de um rollover recusado, espera antes de tentar de novo

_setup_lock = threading.Lock()
_client_handler: _DailyFileHandler | None = None


def formatter() -> logging.Formatter:
    """``2026-10-03 14:05:12.345 INFO  4321 dll L=D op=read ...``."""
    return logging.Formatter(FORMAT, DATEFMT)


class SafeRotatingFileHandler(RotatingFileHandler):
    """``RotatingFileHandler`` cujo rollover recusado pelo Windows (arquivo aberto por outro processo, E35) não
    derruba nada: o log continua no arquivo atual e o rollover é tentado de novo só depois de ``ROLLOVER_RETRY_S``."""

    def __init__(self, filename: str | os.PathLike[str], mode: str = "a", maxBytes: int = WORKER_MAX_BYTES,
                 backupCount: int = WORKER_BACKUPS, encoding: str | None = "utf-8", delay: bool = True,
                 errors: str | None = None) -> None:
        _paths.ensure_dir(os.path.dirname(os.path.abspath(filename)))
        super().__init__(filename, mode=mode, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding,
                         delay=delay, errors=errors)
        self._retry_at = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if time.monotonic() < self._retry_at:
            return False
        try:
            return bool(super().shouldRollover(record))
        except OSError:
            return False

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:                    # PermissionError: outro processo segura o arquivo
            self._retry_at = time.monotonic() + ROLLOVER_RETRY_S
            if self.stream is None and not self.delay:
                self.stream = self._open()


class _DailyFileHandler(logging.FileHandler):
    """``logs\\client-AAAAMMDD.log`` em modo append; troca de arquivo na virada do dia e apaga os antigos."""

    def __init__(self) -> None:
        self._day = date.today()
        path = _paths.client_log_path(self._day)
        _paths.ensure_dir(path.parent)
        super().__init__(path, mode="a", encoding="utf-8", delay=True)

    def emit(self, record: logging.LogRecord) -> None:
        today = date.today()
        if today != self._day:
            self.acquire()
            try:
                if today != self._day:
                    self._day = today
                    if self.stream is not None:
                        self.stream.close()
                        self.stream = None  # type: ignore[assignment]
                    self.baseFilename = os.path.abspath(_paths.client_log_path(today))
            finally:
                self.release()
            prune_client_logs(today)
        super().emit(record)


def prune_client_logs(today: date | None = None, *, keep_days: int = CLIENT_KEEP_DAYS) -> int:
    """Apaga ``client-*.log`` com mais de ``keep_days`` dias (ignora arquivos em uso). Devolve quantos apagou."""
    today = today or date.today()
    limit = today - timedelta(days=keep_days)
    removed = 0
    try:
        candidates = list(_paths.logs_dir().glob("client-*.log"))
    except OSError:
        return 0
    for path in candidates:
        try:
            day = datetime.strptime(path.stem[len("client-"):], "%Y%m%d").date()
        except ValueError:
            continue
        if day < limit:
            try:
                path.unlink()
                removed += 1
            except OSError:                # PermissionError: outro processo ainda escreve nele
                continue
    return removed


def setup_client_logging(level: int | str | None = None) -> logging.Handler:
    """Liga o log do cliente (uma vez por processo) no logger ``rumbapy``. Nível: ``level``, ``RUMBAPY_LOG_LEVEL``
    ou INFO. Os registros continuam propagando para o logging do usuário (se ele tiver configurado um)."""
    global _client_handler
    with _setup_lock:
        if _client_handler is None:
            handler = _DailyFileHandler()
            handler.setFormatter(formatter())
            logging.getLogger("rumbapy").addHandler(handler)
            _client_handler = handler
            prune_client_logs()
        chosen: Any = level if level is not None else (os.environ.get("RUMBAPY_LOG_LEVEL") or "INFO")
        if isinstance(chosen, str):
            chosen = logging.getLevelName(chosen.strip().upper())
        logging.getLogger("rumbapy").setLevel(chosen if isinstance(chosen, int) else logging.INFO)
        return _client_handler


def flush_all() -> None:
    """``flush()`` em todos os handlers conhecidos, sem ``logging.shutdown`` (usado antes de ``os._exit``, D.4)."""
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers += [lg for lg in list(logging.Logger.manager.loggerDict.values()) if isinstance(lg, logging.Logger)]
    seen: set[int] = set()
    for lg in loggers:
        for handler in list(lg.handlers):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                handler.flush()
            except Exception:
                pass


def notice(message: str, *, force: bool = False) -> None:
    """Uma linha curta em stderr (desligável com ``RUMBAPY_QUIET=1``) + INFO no log.

    ``force=True`` ignora o ``RUMBAPY_QUIET`` (só o aviso "AUTO-UPDATE DESLIGADO" do ``RUMBAPY_DEV=1``, que tem de sair
    a cada import: H.2, E38). Nunca falha: ``sys.stderr`` ausente (pythonw.exe, Agendador de Tarefas), fechado ou sem
    suporte a algum caractere não interrompe nada (E25).
    """
    logger.info("notice: %s", message)
    if not force and os.environ.get("RUMBAPY_QUIET") == "1":
        return
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        try:
            print(message.encode(encoding, "replace").decode(encoding, "replace"), file=stream, flush=True)
        except (OSError, ValueError, LookupError):
            pass
    except (OSError, ValueError, AttributeError):
        pass
