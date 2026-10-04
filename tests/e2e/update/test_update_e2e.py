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


LONG_RUNNING = """
import json, os, time
import rumbapy
from rumbapy import _update, errors
print("pronto", flush=True)
go = os.environ["GO_FILE"]
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.05)
_update._checked_at = None                       # TTL do open vencido (1 h / 12 h)
try:
    _update.ensure_latest("open")
    print(json.dumps({"res": "sem UpdateRequired"}))
except errors.UpdateRequired as exc:
    print(json.dumps({"res": "UpdateRequired", "msg": exc.message, "info": list(_update.restart_info())}))
"""


def test_long_process_sees_update_by_other_process(installation: Installation) -> None:
    """Kernel longo importou A; outro script atualiza o disco para B; a reconsulta do ``open`` pede reinício."""
    go = installation.work / "go.flag"
    proc = installation.popen(LONG_RUNNING, GO_FILE=str(go))
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "pronto"
        installation.github.master = SHA_B
        (installation.home / "update-state.json").unlink()      # o cache de 60 s (E7) dispensaria a consulta
        other = installation.run()                            # o "outro script": instala B e pede para rodar de novo
        assert other.returncode == 1 and "UpdateRequired: O RumbaPy foi atualizado para 2.0.0 (bbbbbbb)" in other.stderr
        assert installation.installed_url().endswith(f"/archive/{SHA_B}.zip")      # type: ignore[union-attr]
        info_before = len(installation.github.paths("info"))
        go.write_text("1", encoding="utf-8")
        out, err = proc.communicate(timeout=90)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    assert proc.returncode == 0, err
    result = json.loads(out.strip().splitlines()[-1])
    assert result == {"res": "UpdateRequired", "info": [SHA_B, None],
                      "msg": "Outro processo atualizou o RumbaPy. Este processo ainda usa a versão antiga; reinicie-o."}
    assert len(installation.github.paths("info")) == info_before                # nem consultou o GitHub
    assert len(installation.pip_calls()) == 1                                    # nem reinstalou
    log = (installation.home / "logs" / "update.log").read_text(encoding="utf-8")
    assert "update context=open mode=archive local=bbbbbbb remote=none result=restart-required" in log
    assert "context=open mode=archive local=bbbbbbb remote=bbbbbbb result=up-to-date" not in log


def test_cache_dispenses_second_query(installation: Installation) -> None:
    assert installation.run().returncode == 0
    assert installation.run().returncode == 0
    assert len(installation.github.paths("info")) == 1           # 2º import em < 60 s usou o update-state.json
