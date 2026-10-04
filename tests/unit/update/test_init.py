"""``rumbapy/__init__.py`` (A.6): papéis, import ansioso, ``BUILD_ID``, aviso do modo dev e ``heal``.

Cada caso roda em um Python novo (o pacote deste processo já foi importado pelo pytest).
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import subprocess
import json
import sys

import pytest

from tests.e2e.update.support import PKG_SRC, child_env, copy_package, run_python

SRC = PKG_SRC.parent


def run(code: str, tmp_path: Path, *, pythonpath: Path = SRC, **env: str | None) -> subprocess.CompletedProcess[str]:
    base = child_env(tmp_path / "home", RUMBAPY_DEV="1", RUMBAPY_QUIET="1", PYTHONPATH=str(pythonpath))
    for key, value in env.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return run_python(sys.executable, code, base, tmp_path, timeout=60)


def last_json(done: subprocess.CompletedProcess[str]) -> object:
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_user_role_exports_and_eager_imports(tmp_path: Path) -> None:
    code = (
        "import json, sys, rumbapy\n"
        "from rumbapy import _buildid\n"
        "mods = ['errors','keys','screen','waits','profiles','session','compat','client','_bridge','_backend',\n"
        "        '_launcher','_runtime32','_doctor','_legacy']\n"
        "present = [m for m in mods if (rumbapy.__path__[0] + '/' + m + '.py') and "
        "__import__('os').path.exists(rumbapy.__path__[0] + '/' + m + '.py')]\n"
        "print(json.dumps({'role': rumbapy._ROLE, 'version': rumbapy.__version__, 'all': rumbapy.__all__,\n"
        "  'build': rumbapy.BUILD_ID == _buildid.compute(rumbapy.__path__[0]),\n"
        "  'loaded': [m for m in present if 'rumbapy.' + m not in sys.modules],\n"
        "  'attrs': [hasattr(rumbapy, a) for a in ('client', 'errors', 'profiles', 'RumbaClient', 'Key', 'Screen',\n"
        "            'Field', 'Cursor', 'OIA', 'UpdateRequired', 'RumbaError')],\n"
        "  'client_cls': rumbapy.RumbaClient.__module__, 'doctor': 'doctor' in rumbapy.__all__\n"
        "  and callable(rumbapy.open) and callable(rumbapy.attach) and callable(rumbapy.close)}))\n"
    )
    out = last_json(run(code, tmp_path))
    assert isinstance(out, dict)
    assert out["role"] == "user" and out["version"] == "2.0.0" and out["build"] is True
    assert out["loaded"] == []                                   # todos os módulos existentes já importados
    assert all(out["attrs"])
    assert {"RumbaClient", "Key", "Screen", "Field", "Cursor", "OIA", "RumbaError", "UpdateRequired"} <= set(out["all"])
    assert "open" not in out["all"] and "close" not in out["all"]  # I34
    assert out["client_cls"] == "rumbapy.compat"
    assert out["doctor"] is True


def test_star_import_works(tmp_path: Path) -> None:
    done = run("from rumbapy import *\nprint(RumbaClient.__name__, Key.__name__, RumbaError.__name__)", tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["RumbaClient", "Key", "RumbaError"]


def test_dev_notice_on_every_import(tmp_path: Path) -> None:
    for _ in range(2):
        done = run("import rumbapy", tmp_path, RUMBAPY_QUIET=None)
        assert done.returncode == 0, done.stderr
        assert "RumbaPy: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)" in done.stderr
    log = (tmp_path / "home" / "logs" / "update.log").read_text(encoding="utf-8")
    assert log.count("AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1) versão=2.0.0") == 2


def test_stderr_none_does_not_break(tmp_path: Path) -> None:
    done = run("import sys\nsys.stderr = None\nimport rumbapy\nprint('ok')", tmp_path, RUMBAPY_QUIET=None)
    assert done.returncode == 0 and done.stdout.strip() == "ok"


def test_legacy_shim_role_does_not_update_nor_operate(tmp_path: Path) -> None:
    code = ("import sys, json\nsys.orig_argv = [sys.executable, '-m', 'rumbapy.server', '--terminal_type', 'D']\n"
            "import rumbapy\n"
            "print(json.dumps([rumbapy._ROLE, rumbapy.__version__, hasattr(rumbapy, 'RumbaClient'),\n"
            "  'rumbapy._update' in sys.modules, 'rumbapy.client' in sys.modules]))")
    assert last_json(run(code, tmp_path, RUMBAPY_DEV=None)) == ["legacy-shim", "2.0.0", False, False, False]


def test_cli_role(tmp_path: Path) -> None:
    code = ("import sys, json\nsys.orig_argv = [sys.executable, '-m', 'rumbapy', 'version']\n"
            "import rumbapy\nprint(json.dumps([rumbapy._ROLE, hasattr(rumbapy, 'RumbaClient')]))")
    assert last_json(run(code, tmp_path)) == ["cli", True]


def test_checkout_without_dev_is_refused_not_healed(tmp_path: Path) -> None:
    done = run("import rumbapy", tmp_path, RUMBAPY_DEV=None)
    assert done.returncode == 1
    assert "UpdateFailed" in done.stderr
    assert "Instalação de desenvolvimento detectada (checkout)" in done.stderr
    assert "reparado" not in done.stderr


# ------------------------------------------------------------------------------------------- cópias do pacote

@pytest.fixture()
def copy(tmp_path: Path) -> Path:
    return copy_package(tmp_path / "site")


def test_existing_module_failing_is_not_masked(tmp_path: Path, copy: Path) -> None:
    (copy / "session.py").write_text("import modulo_que_nao_existe_xyz\n", encoding="utf-8")
    done = run("import rumbapy", tmp_path, pythonpath=copy.parent)
    assert done.returncode == 1
    assert "ModuleNotFoundError: No module named 'modulo_que_nao_existe_xyz'" in done.stderr


@pytest.mark.parametrize("name", ["session.py", "compat.py", "_bridge.py", "_launcher.py", "_runtime32.py",
                                  "_doctor.py", "_legacy.py"])
def test_missing_client_module_goes_to_heal(tmp_path: Path, copy: Path, name: str) -> None:
    # forma literal da A.6: todo módulo do cliente é importado; um arquivo sumido (instalação pela metade) vai ao heal
    (copy / name).unlink()
    done = run("import rumbapy", tmp_path, pythonpath=copy.parent)           # DEV=1: heal relança o original
    assert done.returncode == 1
    assert "ModuleNotFoundError" in done.stderr or "ImportError" in done.stderr
    log = (tmp_path / "home" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "heal: o import do RumbaPy falhou" in log


def test_build_id_changed_during_import(tmp_path: Path, copy: Path) -> None:
    with open(copy / "_backend.py", "a", encoding="utf-8") as fh:
        fh.write("\nimport os as _o\nopen(_o.path.join(_o.path.dirname(__file__), 'zz_touch.json'), 'w').close()\n")
    done = run("import rumbapy", tmp_path, pythonpath=copy.parent)
    assert done.returncode == 1
    assert "UpdateRequired: O RumbaPy foi atualizado durante a importação. Rode de novo." in done.stderr


def test_broken_role_module_goes_to_heal(tmp_path: Path, copy: Path) -> None:
    (copy / "_role.py").write_text("def detect(:\n", encoding="utf-8")
    done = run("import rumbapy", tmp_path, pythonpath=copy.parent)           # DEV=1: heal relança o original
    assert done.returncode == 1
    assert "SyntaxError" in done.stderr
    log = (tmp_path / "home" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "heal: o import do RumbaPy falhou (SyntaxError" in log


def test_library_errors_are_never_healed(tmp_path: Path, copy: Path) -> None:
    with open(copy / "keys.py", "a", encoding="utf-8") as fh:
        fh.write("\nfrom .errors import UpdateCheckFailed as _U\nraise _U.make(motivo='simulado')\n")
    done = run("import rumbapy", tmp_path, pythonpath=copy.parent)
    assert done.returncode == 1
    assert "UpdateCheckFailed" in done.stderr
    assert not (tmp_path / "home" / "logs" / "update.log").exists() or \
        "heal:" not in (tmp_path / "home" / "logs" / "update.log").read_text(encoding="utf-8")


def test_broken_role_in_legacy_shim_is_not_healed(tmp_path: Path, copy: Path) -> None:
    (copy / "_role.py").write_text("def detect(:\n", encoding="utf-8")
    code = "import sys\nsys.orig_argv = [sys.executable, '-m', 'rumbapy.server']\nimport rumbapy"
    done = run(code, tmp_path, pythonpath=copy.parent, RUMBAPY_DEV=None)   # papel calculado inline (A.6)
    assert done.returncode == 1
    assert "SyntaxError" in done.stderr and "reparado" not in done.stderr
    log = tmp_path / "home" / "logs" / "update.log"
    assert not log.exists() or "heal:" not in log.read_text(encoding="utf-8")
