"""CLI ``python -m rumbapy`` (H.9): version, update, doctor, close e códigos de saída."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import sys

import pytest

import rumbapy
from tests.e2e.update.support import PKG_SRC, SHA_B, child_env, run_python
from rumbapy.errors import UpdateCheckFailed, UpdateRequired
from rumbapy import __main__ as cli, _update


@pytest.fixture(autouse=True)
def clean_update_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path / "home"))
    for name in ("_restart_required", "_restart_message", "_restart_version", "_checked_at"):
        monkeypatch.setattr(_update, name, None)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("RumbaPy 2.0.0\n")
    assert "modo de instalação: dev" in out and "última checagem: nenhuma registrada" in out


def test_update_in_dev_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["update"]) == 0
    assert "auto-update desligado (RUMBAPY_DEV=1)" in capsys.readouterr().out


def test_update_installed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake(context: str = "import", force: bool = False) -> None:
        assert (context, force) == ("cli", True)
        monkeypatch.setattr(_update, "_restart_required", SHA_B)
        monkeypatch.setattr(_update, "_restart_version", "2.0.1")
        raise UpdateRequired("x")

    monkeypatch.setattr(_update, "ensure_latest", fake)
    assert cli.main(["update"]) == 0
    assert capsys.readouterr().out == "RumbaPy atualizado para 2.0.1 (bbbbbbb).\n"


def test_update_already_latest(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("RUMBAPY_DEV")
    monkeypatch.setattr(_update, "ensure_latest", lambda context="import", force=False: None)
    monkeypatch.setattr(_update, "read_state", lambda: {"remote": SHA_B})
    assert cli.main(["update"]) == 0
    assert capsys.readouterr().out == "RumbaPy já está na versão publicada (2.0.0, bbbbbbb).\n"


def test_update_check_failed_prints_without_traceback(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    def fail(context: str = "import", force: bool = False) -> None:
        raise UpdateCheckFailed.make(motivo="info/refs: tempo esgotado")

    monkeypatch.setattr(_update, "ensure_latest", fail)
    assert cli.main(["update"]) == 1
    assert capsys.readouterr().err.startswith("Não consegui verificar a versão mais recente no GitHub")


@pytest.mark.parametrize("argv", [["doctor"], ["close", "D"]])
def test_commands_check_update_first(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                     argv: list[str]) -> None:
    seen: list[tuple[str, bool]] = []

    def updated(context: str = "import", force: bool = False) -> None:
        seen.append((context, force))
        monkeypatch.setattr(_update, "_restart_required", SHA_B)
        monkeypatch.setattr(_update, "_restart_version", "2.0.1")
        raise UpdateRequired("x")

    monkeypatch.setattr(_update, "ensure_latest", updated)
    assert cli.main(argv) == cli.EXIT_RERUN
    assert seen == [("cli", False)]
    assert "RumbaPy atualizado para 2.0.1 (bbbbbbb). Rode o comando de novo." in capsys.readouterr().err


def test_close_and_doctor_delegate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[object] = []

    class Report:
        ok = False

    monkeypatch.setattr(rumbapy, "close", lambda terminal: calls.append(terminal) or 2, raising=False)
    monkeypatch.setattr(rumbapy, "doctor", lambda verbose=False: calls.append(verbose) or Report(), raising=False)
    assert cli.main(["close", "d"]) == 0
    assert cli.main(["doctor", "--verbose"]) == 1
    assert calls == ["d", True]
    assert "2 janela(s) do terminal D fechada(s)" in capsys.readouterr().out


def test_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["voar"])
    assert info.value.code == 2


def test_python_m_rumbapy_subprocess(tmp_path: Path) -> None:
    env = child_env(tmp_path / "home", RUMBAPY_DEV="1", RUMBAPY_QUIET="1", PYTHONPATH=str(PKG_SRC.parent))
    done = run_python(sys.executable, "import runpy, sys; sys.argv = ['rumbapy', 'version']; "
                      "runpy.run_module('rumbapy', run_name='__main__')", env, tmp_path, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("RumbaPy 2.0.0")


def test_cli_update_in_checkout_without_dev_has_no_traceback(tmp_path: Path) -> None:
    import subprocess

    env = child_env(tmp_path / "home", RUMBAPY_QUIET="1", PYTHONPATH=str(PKG_SRC.parent))
    done = subprocess.run([sys.executable, "-m", "rumbapy", "update"], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert done.returncode == 1
    assert "Instalação de desenvolvimento detectada (checkout)" in done.stderr
    assert "Traceback" not in done.stderr
