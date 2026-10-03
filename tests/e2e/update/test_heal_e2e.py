"""``heal`` de ponta a ponta (H.6, E1): ``SyntaxError`` injetado em ``_update.py`` de uma cópia instalada do pacote."""
# ruff: noqa: I001
from __future__ import annotations

import pytest

from tests.e2e.update.conftest import Installation
from tests.e2e.update.support import SHA_B

pytestmark = pytest.mark.e2e


def break_update(inst: Installation) -> None:
    target = inst.site / "rumbapy" / "_update.py"
    target.write_text(target.read_text(encoding="utf-8") + "\ndef quebrado(:\n", encoding="utf-8")


def test_syntax_error_heals_by_reinstalling_master(installation: Installation) -> None:
    installation.github.master = SHA_B                           # o master já foi corrigido
    break_update(installation)
    first = installation.run()
    assert first.returncode == 1
    assert "RuntimeError: O RumbaPy foi reparado para bbbbbbb. Rode de novo." in first.stderr
    assert "SyntaxError" in first.stderr                         # causa encadeada no traceback
    calls = installation.pip_calls()
    assert len(calls) == 1 and calls[0][-1].endswith(f"/archive/{SHA_B}.zip")
    log = (installation.home / "logs" / "update.log").read_text(encoding="utf-8")
    assert "heal: o import do RumbaPy falhou (SyntaxError" in log
    second = installation.run("import rumbapy; print(rumbapy._update.read_state()['result'])")
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip().splitlines()[-1] == "up-to-date"
    assert len(installation.pip_calls()) == 1


def test_still_broken_after_heal_reports_defect(installation: Installation) -> None:
    installation.github.master = SHA_B
    break_update(installation)
    first = installation.run(FAKE_PIP_MODE="noop")               # o master publicado continua quebrado
    assert "reparado para bbbbbbb" in first.stderr
    second = installation.run(FAKE_PIP_MODE="noop")
    assert second.returncode == 1
    assert ("RuntimeError: A versão publicada do RumbaPy (bbbbbbb) está com defeito; avise o responsável. "
            "Detalhes em update.log") in second.stderr
    assert len(installation.pip_calls()) == 1                    # não reinstala em loop


def test_heal_without_network_gives_recovery_command(installation: Installation) -> None:
    break_update(installation)
    installation.github.stop()
    done = installation.run(PATH="")
    assert done.returncode == 1
    assert "não consegui consultar a versão publicada" in done.stderr
    assert "python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/" \
           "refs/heads/master.zip" in done.stderr
    assert installation.pip_calls() == []


def test_dev_mode_shows_the_original_error(installation: Installation) -> None:
    break_update(installation)
    done = installation.run(RUMBAPY_DEV="1")
    assert done.returncode == 1
    assert "SyntaxError" in done.stderr and "reparado" not in done.stderr
    assert installation.pip_calls() == []
