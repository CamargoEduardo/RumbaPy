"""Auto-update de ponta a ponta (H.4): ``import rumbapy`` num venv temporário contra o GitHub falso e o pip falso."""
# ruff: noqa: I001
from __future__ import annotations

import json

import pytest

from tests.e2e.update.conftest import Installation
from tests.e2e.update.support import SHA_A, SHA_B

pytestmark = pytest.mark.e2e

STATE = "import rumbapy, json; print(json.dumps(rumbapy._update.read_state()))"


def expected_pip_args(inst: Installation, sha: str) -> list[str]:
    return ["install", "--disable-pip-version-check", "--no-input", "--force-reinstall", "--no-deps",
            f"{inst.github.root}/CamargoEduardo/RumbaPy/archive/{sha}.zip"]


def test_up_to_date_does_nothing(installation: Installation) -> None:
    done = installation.run(STATE)
    assert done.returncode == 0, done.stderr
    state = json.loads(done.stdout.strip().splitlines()[-1])
    assert state["result"] == "up-to-date" and state["mode"] == "archive" and state["local"] == SHA_A
    assert installation.pip_calls() == []
    assert len(installation.github.paths("info")) == 1


def test_new_master_installs_and_asks_to_rerun(installation: Installation) -> None:
    installation.github.master = SHA_B
    first = installation.run()
    assert first.returncode == 1
    assert "UpdateRequired: O RumbaPy foi atualizado para 2.0.0 (bbbbbbb). Rode o script de novo." in first.stderr
    assert "RumbaPy: atualizando para bbbbbbb…" in first.stderr
    assert installation.pip_calls() == [expected_pip_args(installation, SHA_B)]
    assert installation.installed_url().endswith(f"/archive/{SHA_B}.zip")      # type: ignore[union-attr]
    second = installation.run(STATE)
    assert second.returncode == 0, second.stderr
    assert len(installation.pip_calls()) == 1                                    # nada de novo
    log = (installation.home / "logs" / "update.log").read_text(encoding="utf-8")
    assert "update context=import mode=archive local=aaaaaaa remote=bbbbbbb result=updated pip_s=" in log


def test_network_down_blocks(installation: Installation) -> None:
    installation.github.stop()
    done = installation.run(PATH="")                             # sem git: só info/refs (2x) e API
    assert done.returncode == 1
    assert "UpdateCheckFailed: Não consegui verificar a versão mais recente no GitHub" in done.stderr
    assert installation.pip_calls() == []


def test_html_from_proxy_blocks(installation: Installation) -> None:
    installation.github.content_type = "text/html"
    installation.github.status = {"/api/": 503}
    done = installation.run(PATH="")                             # sem git no PATH
    assert done.returncode == 1
    assert "UpdateCheckFailed" in done.stderr and "text/html" in done.stderr


def test_pip_failure_reports_and_retries(installation: Installation) -> None:
    installation.github.master = SHA_B
    done = installation.run(FAKE_PIP_RC="1")
    assert done.returncode == 1
    assert "UpdateFailed: A atualização do RumbaPy falhou (pip código 1)." in done.stderr
    assert "python -m pip install --force-reinstall --no-deps" in done.stderr
    again = installation.run()
    assert again.returncode == 1 and "UpdateRequired" in again.stderr
    assert len(installation.pip_calls()) == 2


def test_sandbox_refuses_pip_without_substitute(installation: Installation) -> None:
    installation.github.master = SHA_B
    done = installation.run(RUMBAPY_TEST_PIP=None)
    assert done.returncode == 1
    assert "SandboxViolation" in done.stderr and "RUMBAPY_TEST_PIP" in done.stderr


def test_two_processes_one_pip(installation: Installation) -> None:
    installation.github.master = SHA_B
    procs = [installation.popen(FAKE_PIP_SLEEP="1.5") for _ in range(2)]
    results = [p.communicate(timeout=90) for p in procs]
    for proc, (_out, err) in zip(procs, results):
        assert proc.returncode == 1
        assert "UpdateRequired: O RumbaPy foi atualizado para 2.0.0 (bbbbbbb)" in err
    assert len(installation.pip_calls()) == 1                    # o 2º esperou a trava e viu o commit novo


def test_cache_dispenses_second_query(installation: Installation) -> None:
    assert installation.run().returncode == 0
    assert installation.run().returncode == 0
    assert len(installation.github.paths("info")) == 1           # 2º import em < 60 s usou o update-state.json
