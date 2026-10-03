"""``install-smoke`` (J.1): zip do código (``git archive`` no CI) → GitHub falso no loopback → venv limpo →
``pip install --no-deps <url local>`` com o backend in-tree → ``import rumbapy`` em modo ``archive`` → update real
(pip real, só dentro do venv temporário) para um commit novo → BOOT do worker no Python 32-bit.

Lento (cria um venv com pip): só roda com ``RUMBAPY_SMOKE=1`` (job ``install-smoke`` do CI). Variáveis opcionais:
``RUMBAPY_SMOKE_ZIP`` (zip do ``git archive``), ``RUMBAPY_SMOKE_SHA`` (sha do zip), ``RUMBAPY_SMOKE_PY32`` (python 32-bit).
Nada aqui acessa a rede real: o pip roda com ``PIP_NO_INDEX=1`` e a URL é do loopback.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import subprocess
import json
import os

import pytest

from tests.e2e.update.support import (FakeGitHub, child_env, make_venv, run_python, venv_site, working_tree_files,
                                      zip_tree, REPO_ROOT)

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(os.environ.get("RUMBAPY_SMOKE") != "1", reason="defina RUMBAPY_SMOKE=1 (lento)"),
              pytest.mark.timeout(600)]

SHA_NEXT = "c" * 40
BOOT_IMPORT = ("import os,sys,types; d=sys.argv[1]; m=types.ModuleType('rumbapy'); m.__path__=[d]; "
               "m.__file__=os.path.join(d,'__init__.py'); sys.modules['rumbapy']=m; "
               "import rumbapy._worker.main as w; print('boot-ok', callable(w.main), 'site' in sys.modules)")

_STUB = ("import os,sys,types; d=sys.argv[1]; m=types.ModuleType('rumbapy'); m.__path__=[d]; "
         "m.__file__=os.path.join(d,'__init__.py'); sys.modules['rumbapy']=m; ")
BOOT_RUN = _STUB + "import rumbapy._worker.main as w; raise SystemExit(w.main(sys.argv[2:]))"
BUILD_CALC = _STUB + "import rumbapy._buildid as b; print(b.compute(d))"


def head_sha() -> str:
    env_sha = os.environ.get("RUMBAPY_SMOKE_SHA")
    if env_sha:
        return env_sha.lower()
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
                          timeout=30).stdout.strip().lower()


def source_zip(sha: str) -> tuple[bytes, list[tuple[str, bytes]]]:
    files = working_tree_files()
    given = os.environ.get("RUMBAPY_SMOKE_ZIP")
    data = Path(given).read_bytes() if given else zip_tree(files, f"RumbaPy-{sha}")
    return data, files


def pip_env(home: Path, root: str, **extra: str) -> dict[str, str]:
    return child_env(home, RUMBAPY_TEST_GITHUB=root, PIP_NO_INDEX="1", PIP_CONFIG_FILE=os.devnull,
                     PIP_DISABLE_PIP_VERSION_CHECK="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                     **extra)


def test_install_smoke(tmp_path: Path) -> None:
    sha = head_sha()
    data, files = source_zip(sha)
    home = tmp_path / "home"
    home.mkdir()
    python = make_venv(tmp_path / "venv", with_pip=True)
    with FakeGitHub(sha) as gh:
        gh.archives[sha] = data
        env = pip_env(home, gh.root)
        url = f"{gh.root}/CamargoEduardo/RumbaPy/archive/{sha}.zip"
        install = subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-input", url], env=env,
                                 cwd=str(tmp_path), capture_output=True, text=True, timeout=300)
        assert install.returncode == 0, install.stdout + install.stderr

        # 1) import em modo archive, commit = HEAD, sem pip
        done = run_python(python, "import rumbapy, json; print(json.dumps([rumbapy._update.install_mode(), "
                                  "rumbapy._update.read_state()['result'], rumbapy.__file__]))", env, tmp_path)
        assert done.returncode == 0, done.stderr
        mode, result, file = json.loads(done.stdout.strip().splitlines()[-1])
        assert mode == ["archive", sha] and result == "up-to-date"
        assert Path(file).resolve().is_relative_to(venv_site(python).resolve())

        # 2) direct_url.json gravado pelo pip
        done = run_python(python, "import importlib.metadata as m; print(m.distribution('rumbapy')"
                                  ".read_text('direct_url.json'))", env, tmp_path)
        direct = json.loads(done.stdout)
        assert direct["url"] == url and "archive_info" in direct

        # 3) master novo → update com o pip REAL do venv → UpdateRequired → próxima execução já no commit novo
        gh.archives[SHA_NEXT] = zip_tree(files, f"RumbaPy-{SHA_NEXT}")
        gh.master = SHA_NEXT
        (home / "update-state.json").unlink()                    # o cache de 60 s (E7) dispensaria a consulta
        env_update = {**env, "RUMBAPY_TEST_PIP": "pip"}
        first = run_python(python, "import rumbapy", env_update, tmp_path, timeout=300)
        assert first.returncode == 1
        assert "O RumbaPy foi atualizado para 2.0.0 (ccccccc). Rode o script de novo." in first.stderr, first.stderr
        second = run_python(python, "import rumbapy; print(rumbapy._update.install_mode()[1])", env_update, tmp_path)
        assert second.returncode == 0, second.stderr
        assert second.stdout.strip().splitlines()[-1] == SHA_NEXT
        pip_logs = list((home / "logs").glob("pip-*.log"))
        assert pip_logs, "o pip do update não deixou log"

    # 4) BOOT do worker (32-bit) a partir dos arquivos instalados, sem executar rumbapy/__init__.py (D.1)
    pkgdir = venv_site(python) / "rumbapy"
    required = bool(os.environ.get("RUMBAPY_SMOKE_PY32"))      # no CI o BOOT é obrigatório: falha, nunca "skip"
    py32 = os.environ.get("RUMBAPY_SMOKE_PY32") or str(REPO_ROOT / ".venv32" / "Scripts" / "python.exe")
    for missing, why in ((not (pkgdir / "_worker" / "main.py").exists(), "_worker/main.py não foi instalado"),
                         (not Path(py32).exists(), f"Python 32-bit indisponível ({py32})")):
        if missing:
            (pytest.fail if required else pytest.skip)(f"BOOT do worker impossível: {why}")
    boot = subprocess.run([py32, "-I", "-S", "-c", BOOT_IMPORT, str(pkgdir)], env=child_env(home), capture_output=True,
                          text=True, timeout=60, cwd=str(home))
    assert boot.returncode == 0, boot.stderr
    assert boot.stdout.split() == ["boot-ok", "True", "False"]

    # 5) worker com a linha de comando da D.1 e --fake-dll: build divergente -> 14; build certo e sem chave -> 13
    calc = subprocess.run([str(python), "-I", "-S", "-c", BUILD_CALC, str(pkgdir)], env=child_env(home),
                          capture_output=True, text=True, timeout=60, cwd=str(home))
    assert calc.returncode == 0, calc.stderr
    build = calc.stdout.strip().splitlines()[-1]
    argv = ["--letter", "D", "--pipe", r"\\.\pipe\rumbapy2-smoke-D", "--keyfile", str(home / "sem-chave.bin"),
            "--stop-event", r"Local\rumbapy2-smoke-D-stop", "--registry", str(home / "workers"),
            "--log-level", "INFO", "--idle", "5", "--commit", SHA_NEXT,
            "--fake-dll", "tests.fakes.scenarios:cics_login", "--fake-path", str(REPO_ROOT)]
    for expect, code in (("0" * 16, 14), (build, 13)):
        run = subprocess.run([py32, "-I", "-S", "-c", BOOT_RUN, str(pkgdir), *argv, "--expect-build", expect],
                             env=child_env(home), capture_output=True, text=True, timeout=60, cwd=str(home))
        assert run.returncode == code, run.stderr
    assert not (home / "sem-chave.bin").exists()                # o worker nunca cria a chave (C.2)
