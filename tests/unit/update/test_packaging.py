"""Empacotamento sem PyPI (H.7) e bootstrap congelado (H.6): ``test_wheel_contents``, ``test_bootstrap_frozen``."""
# ruff: noqa: I001
from __future__ import annotations

from types import ModuleType
from pathlib import Path

import importlib.util
import subprocess
import tarfile
import hashlib
import zipfile
import tomllib
import base64
import shutil
import sys

import pytest

from tests.e2e.update.support import PKG_SRC, REPO_ROOT, child_env, run_python

BUILD_DIR = REPO_ROOT / "_build"
# arquivos que o wheel TEM que ter (inclusive o script do fallback IE, E.7)
REQUIRED = ["__init__.py", "__main__.py", "_bootstrap.py", "_update.py", "_role.py", "_version.py", "_buildid.py",
            "_paths.py", "_locks.py", "_log.py", "errors.py", "keys.py", "screen.py", "profiles.py", "client.py",
            "server.py", "api.py", "_worker/__init__.py", "_worker/guard.py", "_worker/hllapi.py",
            "_ie_open.js", "session.py", "compat.py", "_bridge.py", "_launcher.py", "_runtime32.py", "_doctor.py",
            "_legacy.py", "_instances.py", "_ipc.py", "_worker/main.py", "recipes/__init__.py", "recipes/cics.py",
            "recipes/ims.py", "waits.py", "_backend.py", "_user32.py", "_win32.py", "_codec.py", "_protocol.py",
            "_types.py", "_worker/server.py", "_worker/dispatch.py", "_worker/engine.py", "_worker/rc.py",
            "_worker/structs.py", "_worker/mask.py", "_worker/watchdog.py"]


@pytest.fixture(scope="module")
def backend() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rumbapy_build_under_test", BUILD_DIR / "rumbapy_build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wheel(backend: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    return out / backend.build_wheel(str(out))


def git_package_files() -> set[str]:
    """Lista EXATA esperada no wheel: o que o Git versiona (ou vai versionar: não ignorado) em ``src/rumbapy``.

    Independe do backend (não é a mesma varredura de pasta): sobra de editor/``.tmp`` em src/ aparece aqui e o
    backend a recusa, então o teste acusa.
    """
    git = shutil.which("git")
    if not git or not (REPO_ROOT / ".git").exists():
        pytest.skip("sem git: a lista exata do wheel vem de 'git ls-files'")
    done = subprocess.run([git, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "src/rumbapy"],
                          cwd=REPO_ROOT, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    names = [n.decode("utf-8") for n in done.stdout.split(b"\0") if n]
    present = [n for n in names if (REPO_ROOT / n).is_file()]    # apagado no disco, ainda no índice: não conta
    return {"rumbapy/" + n.removeprefix("src/rumbapy/") for n in present}


# ------------------------------------------------------------------------------------------- wheel

def test_wheel_contents(wheel: Path, backend: ModuleType) -> None:
    assert wheel.name == "rumbapy-2.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    dist = "rumbapy-2.0.0.dist-info"
    tracked = git_package_files()
    odd = sorted(n for n in tracked if Path(n).suffix not in backend.PACKAGE_SUFFIXES or Path(n).name[0] == ".")
    assert not odd, f"arquivo fora do pacote em src/rumbapy (apague, ou inclua o tipo em PACKAGE_SUFFIXES): {odd}"
    expected = tracked | {f"{dist}/METADATA", f"{dist}/WHEEL", f"{dist}/RECORD", f"{dist}/top_level.txt"}
    assert set(names) == expected                                # lista exata
    assert len(names) == len(set(names))
    assert names[-1] == f"{dist}/RECORD"
    for prefix in ("tests/", "docs/", "tools/", "_build/", "src/"):
        assert not any(n.startswith(prefix) for n in names)
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


@pytest.mark.parametrize("rel", REQUIRED)
def test_wheel_has_required_file(wheel: Path, rel: str) -> None:
    with zipfile.ZipFile(wheel) as zf:
        assert f"rumbapy/{rel}" in zf.namelist()


def test_wheel_record_hashes(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        record = zf.read("rumbapy-2.0.0.dist-info/RECORD").decode("utf-8").splitlines()
        rows = [line.rsplit(",", 2) for line in record]
        assert {r[0] for r in rows} == set(zf.namelist())
        for name, digest, size in rows:
            if name.endswith("/RECORD"):
                assert digest == "" and size == ""
                continue
            data = zf.read(name)
            b64 = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            assert digest == f"sha256={b64}" and int(size) == len(data)
        for name in zf.namelist():
            if name.startswith("rumbapy/"):
                assert zf.read(name) == (PKG_SRC / name[len("rumbapy/"):]).read_bytes()   # bytes idênticos (H.10)


def test_wheel_metadata(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        meta = zf.read("rumbapy-2.0.0.dist-info/METADATA").decode("utf-8")
        wheel_txt = zf.read("rumbapy-2.0.0.dist-info/WHEEL").decode("utf-8")
        top = zf.read("rumbapy-2.0.0.dist-info/top_level.txt").decode("utf-8")
    head = meta.split("\n\n", 1)[0].splitlines()
    assert head[:3] == ["Metadata-Version: 2.1", "Name: rumbapy", "Version: 2.0.0"]
    assert "Requires-Python: >=3.12" in head
    assert not any(line.startswith("Requires-Dist") for line in head)
    assert "Summary: Automação de terminais RUMBA via Python e EHLAPI" in head
    assert "Tag: py3-none-any" in wheel_txt and "Root-Is-Purelib: true" in wheel_txt
    assert top == "rumbapy\n"


def test_wheel_is_reproducible(backend: ModuleType, wheel: Path, tmp_path: Path) -> None:
    again = tmp_path / backend.build_wheel(str(tmp_path))
    assert again.read_bytes() == wheel.read_bytes()


def test_wheel_installs_and_imports(wheel: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)
    env = child_env(tmp_path / "home", RUMBAPY_DEV="1", RUMBAPY_QUIET="1", PYTHONPATH=str(site))
    done = run_python(sys.executable, "import rumbapy; print(rumbapy.__file__, rumbapy.BUILD_ID)", env, tmp_path)
    assert done.returncode == 0, done.stderr
    file, build_id = done.stdout.split()
    assert Path(file).resolve().is_relative_to(site.resolve())
    from rumbapy import _buildid

    assert build_id == _buildid.compute(PKG_SRC)                 # mesmo build_id do código-fonte


def test_prepare_metadata_matches_wheel(backend: ModuleType, wheel: Path, tmp_path: Path) -> None:
    name = backend.prepare_metadata_for_build_wheel(str(tmp_path))
    assert name == "rumbapy-2.0.0.dist-info"
    with zipfile.ZipFile(wheel) as zf:
        assert (tmp_path / name / "METADATA").read_bytes() == zf.read(f"{name}/METADATA")


def test_requires_are_empty(backend: ModuleType) -> None:
    assert backend.get_requires_for_build_wheel() == []
    assert backend.get_requires_for_build_sdist() == []
    assert backend.get_requires_for_build_editable() == []


def test_editable_points_to_src(backend: ModuleType, tmp_path: Path) -> None:
    name = backend.build_editable(str(tmp_path))
    with zipfile.ZipFile(tmp_path / name) as zf:
        pth = [n for n in zf.namelist() if n.endswith(".pth")]
        assert len(pth) == 1
        assert Path(zf.read(pth[0]).decode().strip()) == PKG_SRC.parent.resolve()
        assert not any(n.startswith("rumbapy/") for n in zf.namelist())


def test_sdist(backend: ModuleType, tmp_path: Path) -> None:
    name = backend.build_sdist(str(tmp_path))
    assert name == "rumbapy-2.0.0.tar.gz"
    with tarfile.open(tmp_path / name) as tar:
        names = set(tar.getnames())
    base = "rumbapy-2.0.0"
    assert {f"{base}/pyproject.toml", f"{base}/_build/rumbapy_build.py", f"{base}/_build/bootstrap.sha256",
            f"{base}/src/rumbapy/__init__.py", f"{base}/PKG-INFO"} <= names
    assert not any(n.startswith((f"{base}/tests", f"{base}/docs", f"{base}/tools")) for n in names)


def test_version_mismatch_breaks_the_build(backend: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                           tmp_path: Path) -> None:
    real = backend._project()
    monkeypatch.setattr(backend, "_project", lambda: {**real, "version": "9.9.9"})
    with pytest.raises(RuntimeError, match="9.9.9"):
        backend.build_wheel(str(tmp_path))


def test_sdist_builds_a_wheel(backend: ModuleType, tmp_path: Path) -> None:
    """O sdist (= archive do GitHub) contém tudo que o backend precisa para gerar o mesmo wheel."""
    with tarfile.open(tmp_path / backend.build_sdist(str(tmp_path))) as tar:
        tar.extractall(tmp_path / "x", filter="data")
    root = tmp_path / "x" / "rumbapy-2.0.0"
    spec = importlib.util.spec_from_file_location("rumbapy_build_from_sdist", root / "_build" / "rumbapy_build.py")
    assert spec and spec.loader
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    out = tmp_path / "out"
    with zipfile.ZipFile(out / other.build_wheel(str(out))) as zf:
        assert "rumbapy/_bootstrap.py" in zf.namelist()
    shutil.rmtree(tmp_path / "x")


# ------------------------------------------------------------------------------------------- pyproject e bootstrap

def test_pyproject_contract() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["build-system"] == {"requires": [], "build-backend": "rumbapy_build", "backend-path": ["_build"]}
    project = data["project"]
    from rumbapy._version import __version__

    assert project["name"] == "rumbapy" and project["version"] == __version__ == "2.0.0"
    assert project["requires-python"] == ">=3.12" and project["dependencies"] == []
    assert "optional-dependencies" not in project
    assert data["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src", "."]


def bootstrap_digest() -> str:
    data = (PKG_SRC / "_bootstrap.py").read_bytes().replace(b"\r\n", b"\n")   # independe do autocrlf do checkout
    return hashlib.sha256(data).hexdigest()


def test_bootstrap_frozen() -> None:
    recorded = (BUILD_DIR / "bootstrap.sha256").read_text(encoding="ascii").split()[0]
    assert recorded == bootstrap_digest(), (
        "src/rumbapy/_bootstrap.py mudou: atualize _build/bootstrap.sha256 NO MESMO COMMIT e registre no PR o teste "
        "manual do heal (H.6)")


def test_bootstrap_imports_only_stdlib() -> None:
    import ast

    tree = ast.parse((PKG_SRC / "_bootstrap.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module and node.module.split(".")[0] in sys.stdlib_module_names | \
                {"__future__"}
        elif isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in node.names)


def test_sources_parse_as_python_312() -> None:
    import ast

    for path in [*PKG_SRC.rglob("*.py"), *BUILD_DIR.rglob("*.py")]:      # requires-python >= 3.12 (A.4.6)
        ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 12))


def test_gitignore_keeps_package_and_tests() -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git ausente")
    paths = ["src/rumbapy/_bootstrap.py", "tests/unit/update/fixtures/info_refs_master_2026-10-03.bin",
             "tests/unit/x.txt", "src/rumbapy/_ie_open.js", "_build/rumbapy_build.py", "_build/bootstrap.sha256"]
    done = subprocess.run([git, "check-ignore", "--no-index", *paths], cwd=REPO_ROOT, capture_output=True, text=True,
                          timeout=30)
    assert done.stdout.strip() == ""                             # nenhum é ignorado
    ignored = ["dados.xlsx", "data/x.csv", "config.ini", "src/rumbapy/__pycache__/x.pyc", "docs/interno/x.md"]
    done = subprocess.run([git, "check-ignore", "--no-index", *ignored], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=30)
    assert sorted(done.stdout.split()) == sorted(ignored)
