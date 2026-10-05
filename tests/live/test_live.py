"""Roteiros ao vivo da J.6 contra o RUMBA de PRODUÇÃO (manuais; NUNCA no CI).

Pulados sem ``RUMBAPY_LIVE=1`` (``tests/conftest.py``) e sempre com ``RUMBAPY_SANDBOX=1``. Cada roteiro: recusa se já
houver janela de BRAZIL_D/BRAZIL_Z aberta, se ``RUMBAPY_GUARD`` (teto do dono) for mais restritivo que o do roteiro
ou se não houver console (E30); depois roda num processo novo (``tests/live/runner.py``, que herda o console) que
pede a confirmação ``SIM <letra>`` em ``CONIN$`` e, nos roteiros de login (L6/L7/compat), o usuário e a senha (sem
eco). Nenhuma confirmação passa por variável de ambiente, argumento ou stdin. O L7 (RHELP) só roda com
``RUMBAPY_LIVE_L7_AUTORIZADO=1``. O aceite confere o relatório (``support.check_report``), inclusive a varredura da
senha feita pelo executor e as AIDs registradas no log do worker.

Se um roteiro passar do prazo, o executor NÃO é encerrado (pode estar limpando; o worker nunca é morto) e os roteiros
seguintes são pulados enquanto ele estiver vivo.
"""
# ruff: noqa: I001
from __future__ import annotations

from tests.live.support import PYTEST_SCRIPTS, REPO, SCRIPTS
from tests.live import support, runner

import subprocess
import json
import sys
import os

import pytest

pytestmark = pytest.mark.live
RUN_LIMIT_S = 1500
# executores que passaram do prazo e ficaram vivos (nunca são mortos): os roteiros seguintes esperam por eles
_STILL_RUNNING: list[subprocess.Popen[str]] = []


def _prepare(script: str) -> None:
    """As mesmas recusas do executor (``runner.refusal``), sem perguntar nada: pula com o motivo (E30 sem console)."""
    alive = [proc.pid for proc in _STILL_RUNNING if proc.poll() is None]
    if alive:
        pytest.skip(f"roteiro anterior ainda em execução (PID {', '.join(map(str, alive))}): confira à mão")
    reason, _how, _creds = runner.refusal(script, confirm=False)
    if reason:
        pytest.skip(reason)


@pytest.mark.timeout(RUN_LIMIT_S + 120)
@pytest.mark.parametrize("script", PYTEST_SCRIPTS, ids=[SCRIPTS[s].label for s in PYTEST_SCRIPTS])
def test_live_script(script: str, no_rumbapy_in_pytest: None) -> None:
    _prepare(script)
    label = SCRIPTS[script].label
    report_file = support.report_path(script)
    argv = [sys.executable, "-m", "tests.live.runner", "--script", script, "--report", str(report_file)]
    # stdin fechado: a confirmação e as credenciais vêm só do console (CONIN$), que o executor herda
    env = dict(os.environ, PYTHONIOENCODING="utf-8")          # o pai decodifica em UTF-8 (motivos com acento)
    proc = subprocess.Popen(argv, cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        _out, err = proc.communicate(timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        # nunca mata o executor: ele pode estar no meio da limpeza (e o worker dele nunca é morto)
        _STILL_RUNNING.append(proc)
        pytest.fail(f"{label} passou de {RUN_LIMIT_S} s; o processo {proc.pid} NÃO foi encerrado (confira à mão; "
                    "os roteiros seguintes serão pulados enquanto ele estiver vivo)", pytrace=False)
    if proc.returncode == runner.EXIT_REFUSED:
        lines = [line for line in err.splitlines() if line.startswith("RECUSADO:")]
        pytest.skip(lines[-1] if lines else "o executor recusou o roteiro")
    if not report_file.is_file():
        pytest.fail(f"{label}: o executor saiu com {proc.returncode} sem relatório", pytrace=False)
    report = json.loads(report_file.read_text(encoding="utf-8"))
    problems = support.check_report(script, report)
    if problems or proc.returncode != 0:
        pytest.fail(f"{label} (saída {proc.returncode}): " + "; ".join(problems) + f" — relatório: {report_file}",
                    pytrace=False)
