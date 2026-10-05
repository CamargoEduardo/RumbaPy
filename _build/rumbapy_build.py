"""Backend de build PEP 517/660 do RumbaPy, só stdlib (H.7; E12, E29): sem PyPI e sem setuptools.

* ``build_wheel`` → ``rumbapy-<v>-py3-none-any.whl`` com os ``.py``/``.js``/``.json``/``py.typed`` de
  ``src/rumbapy/`` (menos ``__pycache__``; outros tipos são ignorados com aviso) + ``dist-info`` (``METADATA`` 2.1,
  ``WHEEL``, ``top_level.txt``, ``RECORD`` com SHA-256 urlsafe-b64).
* ``build_sdist`` → ``rumbapy-<v>.tar.gz`` (``pyproject.toml``, ``_build/``, ``src/rumbapy/``, README/CHANGELOG).
* ``build_editable`` → wheel com ``rumbapy.pth`` apontando para ``src`` (só desenvolvimento: o auto-update recusa
  instalações editable sem ``RUMBAPY_DEV=1``, H.2).

A versão vem de ``src/rumbapy/_version.py`` (regex); os demais metadados, de ``[project]`` via ``tomllib``. Se
``[project].version`` existir e divergir de ``_version.py``, o build falha (as duas fontes nunca podem discordar).
Arquivos são gravados em ordem e com data fixa: o mesmo código gera o mesmo wheel.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import tarfile
import hashlib
import zipfile
import tomllib
import base64
import shutil
import sys
import io
import os
import re

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "rumbapy"
SRC = ROOT / "src"
PKG_DIR = SRC / PACKAGE
TAG = "py3-none-any"
FIXED_DATE = (2020, 1, 1, 0, 0, 0)        # data fixa nas entradas do zip (build reprodutível)
EXCLUDED_DIRS = frozenset({"__pycache__"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})
# só estes tipos vão para o wheel: sobra de editor, nota ou ``.tmp`` largado em src/rumbapy fica de fora (com aviso).
# Mesmos tipos do build_id (H.10: .py/.js/.json) + o marcador py.typed.
PACKAGE_SUFFIXES = frozenset({".py", ".js", ".json", ".typed"})
SDIST_EXTRA = ("README.md", "CHANGELOG.md", "LICENSE", "LICENSE.txt", "LICENSE.md")


# ------------------------------------------------------------------------------------------- metadados

def _project() -> dict[str, Any]:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return dict(tomllib.load(fh).get("project", {}))


def version() -> str:
    text = (PKG_DIR / "_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise RuntimeError("não achei __version__ em src/rumbapy/_version.py")
    found = match.group(1)
    declared = _project().get("version")
    if declared is not None and declared != found:
        raise RuntimeError(f"pyproject.toml diz version={declared!r}, mas _version.py diz {found!r}")
    return found


def _dist_name() -> str:
    return re.sub(r"[-_.]+", "_", str(_project().get("name", PACKAGE))).lower()


def _dist_info() -> str:
    return f"{_dist_name()}-{version()}.dist-info"


def metadata_text() -> str:
    """``METADATA``/``PKG-INFO`` (Metadata-Version 2.1, UTF-8 cru: sem codificação RFC 2047 nos cabeçalhos)."""
    project = _project()
    headers = [("Metadata-Version", "2.1"), ("Name", str(project.get("name", PACKAGE))), ("Version", version())]
    if project.get("description"):
        headers.append(("Summary", " ".join(str(project["description"]).split())))
    if project.get("requires-python"):
        headers.append(("Requires-Python", str(project["requires-python"])))
    headers += [("Requires-Dist", str(dep)) for dep in project.get("dependencies", []) or []]
    headers += [("Project-URL", f"{label}, {url}") for label, url in (project.get("urls", {}) or {}).items()]
    body = ""
    readme = project.get("readme")
    if isinstance(readme, str) and (ROOT / readme).is_file():
        headers.append(("Description-Content-Type",
                        "text/markdown" if readme.lower().endswith(".md") else "text/plain"))
        body = (ROOT / readme).read_text(encoding="utf-8")
    return "".join(f"{key}: {value}\n" for key, value in headers) + "\n" + body


def _wheel_text() -> str:
    return f"Wheel-Version: 1.0\nGenerator: rumbapy_build\nRoot-Is-Purelib: true\nTag: {TAG}\n"


# ------------------------------------------------------------------------------------------- arquivos

def package_files() -> list[Path]:
    """Arquivos de ``src/rumbapy`` que vão para o wheel (ordem estável; só ``PACKAGE_SUFFIXES``)."""
    out = []
    for path in sorted(PKG_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PKG_DIR)
        if EXCLUDED_DIRS.intersection(rel.parts) or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.suffix not in PACKAGE_SUFFIXES or path.name.startswith("."):
            print(f"rumbapy_build: AVISO: ignorando src/rumbapy/{rel.as_posix()} (tipo de arquivo fora do pacote)",
                  file=sys.stderr)
            continue
        out.append(path)
    return out


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={digest}"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _write_wheel(wheel_directory: str, entries: list[tuple[str, bytes]]) -> str:
    dist_info = _dist_info()
    entries = list(entries) + [
        (f"{dist_info}/METADATA", metadata_text().encode("utf-8")),
        (f"{dist_info}/WHEEL", _wheel_text().encode("utf-8")),
        (f"{dist_info}/top_level.txt", f"{PACKAGE}\n".encode("utf-8")),
    ]
    record_name = f"{dist_info}/RECORD"
    lines = [f"{name},{_record_hash(data)},{len(data)}" for name, data in entries] + [f"{record_name},,"]
    entries.append((record_name, ("\n".join(lines) + "\n").encode("utf-8")))
    filename = f"{_dist_name()}-{version()}-{TAG}.whl"
    os.makedirs(wheel_directory, exist_ok=True)
    target = Path(wheel_directory) / filename
    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w") as zf:
        for name, data in entries:
            zf.writestr(_zip_info(name), data)
    os.replace(tmp, target)
    return filename


def _write_metadata_dir(metadata_directory: str) -> str:
    dist_info = Path(metadata_directory) / _dist_info()
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_bytes(metadata_text().encode("utf-8"))
    (dist_info / "WHEEL").write_bytes(_wheel_text().encode("utf-8"))
    (dist_info / "top_level.txt").write_bytes(f"{PACKAGE}\n".encode("utf-8"))
    return dist_info.name


# ------------------------------------------------------------------------------------------- ganchos PEP 517/660

def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(metadata_directory: str,
                                     config_settings: dict[str, Any] | None = None) -> str:
    return _write_metadata_dir(metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory: str,
                                        config_settings: dict[str, Any] | None = None) -> str:
    return _write_metadata_dir(metadata_directory)


def build_wheel(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                metadata_directory: str | None = None) -> str:
    entries = [(f"{PACKAGE}/{p.relative_to(PKG_DIR).as_posix()}", p.read_bytes()) for p in package_files()]
    return _write_wheel(wheel_directory, entries)


def build_editable(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                   metadata_directory: str | None = None) -> str:
    pth = (str(SRC.resolve()) + "\n").encode("utf-8")
    return _write_wheel(wheel_directory, [(f"{_dist_name()}_editable.pth", pth)])


def sdist_files() -> list[Path]:
    """Arquivos do sdist, relativos à raiz (ordem estável)."""
    files = [ROOT / "pyproject.toml"]
    files += sorted(p for p in (ROOT / "_build").rglob("*")
                    if p.is_file() and not EXCLUDED_DIRS.intersection(p.parts) and p.suffix not in EXCLUDED_SUFFIXES)
    files += package_files()
    files += [ROOT / name for name in SDIST_EXTRA if (ROOT / name).is_file()]
    return files


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    base = f"{_dist_name()}-{version()}"
    filename = f"{base}.tar.gz"
    os.makedirs(sdist_directory, exist_ok=True)
    target = Path(sdist_directory) / filename
    tmp = target.with_name(target.name + ".part")

    def add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(f"{base}/{name}")
        info.size = len(data)
        info.mtime = 1577836800           # 2020-01-01 (reprodutível)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(tmp, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path in sdist_files():
            add(tar, path.relative_to(ROOT).as_posix(), path.read_bytes())
        add(tar, "PKG-INFO", metadata_text().encode("utf-8"))
    shutil.move(str(tmp), str(target))
    return filename
