"""Aceite P-0: ``_log`` (D.7) — formato, rollover tolerante a ``PermissionError``, log do cliente e ``notice``."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from rumbapy import _log, _paths

import logging
import io
import re

import pytest

LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} (INFO |WARNING|ERROR) \d+ \S+ (.*)$")


def make_record(message: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("rumbapy.teste", level, __file__, 1, message, None, None)


def test_format() -> None:
    line = _log.formatter().format(make_record("L=D op=read id=17 row=23 col=7 len=74 rc=0 ms=1.2"))
    match = LINE.match(line)
    assert match and match.group(2) == "L=D op=read id=17 row=23 col=7 len=74 rc=0 ms=1.2"
    assert " INFO  " in line                                   # levelname com largura 5


def test_safe_rotating_handler_rotates_normally(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "worker-x-D.log"
    handler = _log.SafeRotatingFileHandler(path, maxBytes=300, backupCount=2)
    handler.setFormatter(_log.formatter())
    try:
        for i in range(30):
            handler.emit(make_record(f"linha {i:02d}"))
    finally:
        handler.close()
    assert path.exists() and (tmp_path / "logs" / "worker-x-D.log.1").exists()
    assert not (tmp_path / "logs" / "worker-x-D.log.3").exists()


def test_safe_rotating_handler_ignores_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "worker.log"
    handler = _log.SafeRotatingFileHandler(path, maxBytes=200, backupCount=5)
    handler.setFormatter(_log.formatter())
    attempts: list[str] = []

    def denied(source: str, dest: str) -> None:
        attempts.append(dest)
        raise PermissionError(13, "o arquivo está aberto em outro processo (simulado)")

    monkeypatch.setattr(handler, "rotate", denied)
    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", errors.append)
    try:
        for i in range(40):
            handler.emit(make_record(f"registro {i:02d}"))
    finally:
        handler.close()
    assert errors == []
    assert len(attempts) == 1                                  # só tenta de novo depois de ROLLOVER_RETRY_S
    content = path.read_text(encoding="utf-8")
    assert all(f"registro {i:02d}" in content for i in range(40))


@pytest.fixture
def client_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    logger = logging.getLogger("rumbapy")
    previous = _log._client_handler              # o import do pacote já ligou o log do cliente (A.14)
    if previous is not None:
        logger.removeHandler(previous)
    monkeypatch.setattr(_log, "_client_handler", None)
    old_level = logger.level
    yield tmp_path / "home" / "logs"
    handler = _log._client_handler
    if handler is not None:
        logger.removeHandler(handler)
        handler.close()
    if previous is not None:
        logger.addHandler(previous)
    logger.setLevel(old_level)


def test_setup_client_logging(client_logging: Path) -> None:
    logs = client_logging
    logs.mkdir(parents=True)
    old = logs / f"client-{date.today() - timedelta(days=15):%Y%m%d}.log"
    recent = logs / f"client-{date.today() - timedelta(days=3):%Y%m%d}.log"
    stranger = logs / "client-notadate.log"
    for path in (old, recent, stranger):
        path.write_text("antigo\n", encoding="utf-8")
    handler = _log.setup_client_logging()
    assert _log.setup_client_logging() is handler              # uma vez por processo
    assert not old.exists() and recent.exists() and stranger.exists()
    logging.getLogger("rumbapy.session").info("op=read rc=0")
    logging.getLogger("rumbapy.session").debug("não aparece em INFO")
    handler.flush()
    today = logs / f"client-{date.today():%Y%m%d}.log"
    lines = today.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and LINE.match(lines[0]) and lines[0].endswith("op=read rc=0")
    assert _log.setup_client_logging(logging.DEBUG) is handler
    assert logging.getLogger("rumbapy").level == logging.DEBUG


def test_client_log_switches_file_at_midnight(client_logging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDate(date):
        current = date(2026, 10, 3)

        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return cls.current

    monkeypatch.setattr(_log, "date", FakeDate)
    handler = _log.setup_client_logging()
    log = logging.getLogger("rumbapy.teste")
    log.info("dia 3")
    FakeDate.current = date(2026, 10, 4)
    log.info("dia 4")
    handler.flush()
    assert (client_logging / "client-20261003.log").read_text(encoding="utf-8").count("dia") == 1
    assert "dia 4" in (client_logging / "client-20261004.log").read_text(encoding="utf-8")


def test_prune_client_logs_ignores_files_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    logs = _paths.ensure_dir(_paths.logs_dir())
    (logs / "client-20200101.log").write_text("x")
    (logs / "client-20200102.log").write_text("x")
    real_unlink = Path.unlink

    def busy(self: Path, missing_ok: bool = False) -> None:
        if self.name == "client-20200101.log":
            raise PermissionError(13, "em uso (simulado)")
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert _log.prune_client_logs(date(2026, 10, 3)) == 1
    assert (logs / "client-20200101.log").exists() and not (logs / "client-20200102.log").exists()


def test_notice_prints_one_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    with caplog.at_level(logging.INFO, logger="rumbapy._log"):
        _log.notice("RumbaPy: abrindo BRAZIL_D (leva ~12 s)…")
    assert capsys.readouterr().err == "RumbaPy: abrindo BRAZIL_D (leva ~12 s)…\n"
    assert "notice: RumbaPy: abrindo BRAZIL_D" in caplog.text


def test_notice_quiet(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("RUMBAPY_QUIET", "1")
    _log.notice("não aparece")
    assert capsys.readouterr().err == ""
    _log.notice("sempre aparece", force=True)                 # só o aviso do RUMBAPY_DEV=1 usa force (H.2, E38)
    assert capsys.readouterr().err == "sempre aparece\n"


class _Broken(io.StringIO):
    def write(self, text: str) -> int:
        raise OSError(22, "stderr fechado")


def test_notice_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    monkeypatch.setattr("sys.stderr", None)                    # pythonw.exe / Agendador de Tarefas
    _log.notice("sem stderr")
    monkeypatch.setattr("sys.stderr", _Broken())
    _log.notice("stderr quebrado")
    raw = io.BytesIO()
    ascii_stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict", newline="\n")
    monkeypatch.setattr("sys.stderr", ascii_stream)
    _log.notice("atualização €")
    ascii_stream.flush()
    assert raw.getvalue() == b"atualiza??o ?\n"


def test_flush_all_flushes_every_handler() -> None:
    flushed: list[str] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            pass

        def flush(self) -> None:
            flushed.append("ok")

    spy = Spy()
    logging.getLogger("rumbapy.teste.flush").addHandler(spy)
    try:
        _log.flush_all()
    finally:
        logging.getLogger("rumbapy.teste.flush").removeHandler(spy)
    assert flushed == ["ok"]
