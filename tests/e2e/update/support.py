"""Apoio dos testes de update (P-F): GitHub falso no loopback, instalações falsas e ambiente de subprocessos.

Nada aqui acessa a rede real nem roda o pip real fora de um venv temporário (J.1/J.2).
"""
# ruff: noqa: I001
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import subprocess
import threading
import zipfile
import shutil
import json
import sys
import io
import os

REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_SRC = REPO_ROOT / "src" / "rumbapy"
FAKE_PIP = Path(__file__).resolve().parent / "fake_pip.py"
OWNER, NAME = "CamargoEduardo", "RumbaPy"
ADVERTISEMENT = "application/x-git-upload-pack-advertisement"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_REAL = "5c03d471d0f5f5e0992d64b015d5c0adbb9fee86"     # master em 2026-10-03 (fixture gravada)
VERSION = "2.0.0"


# ------------------------------------------------------------------------------------------- pkt-lines

def pkt(payload: bytes) -> bytes:
    return f"{len(payload) + 4:04x}".encode("ascii") + payload


def info_refs_body(refs: Mapping[str, str], head: str | None = "refs/heads/master") -> bytes:
    """Resposta ``info/refs`` no formato do GitHub (v0): serviço, flush, ``HEAD`` com capacidades, refs, flush."""
    caps = "multi_ack thin-pack side-band side-band-64k ofs-delta shallow no-progress include-tag"
    if head:
        caps += f" symref=HEAD:{head}"
    caps += " agent=git/github-fake"
    out = pkt(b"# service=git-upload-pack\n") + b"0000"
    head_sha = refs.get(head or "", next(iter(refs.values()), "0" * 40))
    out += pkt(f"{head_sha} HEAD\0{caps}\n".encode("ascii"))
    for name, sha in refs.items():
        out += pkt(f"{sha} {name}\n".encode("ascii"))
    return out + b"0000"


# ------------------------------------------------------------------------------------------- GitHub falso

class FakeGitHub:
    """Servidor HTTP em ``127.0.0.1`` que imita ``github.com`` (info/refs, archive/<sha>.zip) e ``api.github.com``
    (em ``/api``). Atributos mutáveis entre requisições: ``master``, ``head``, ``extra_refs``, ``content_type``,
    ``status`` (por caminho), ``archives`` (sha -> bytes do zip). ``requests`` registra (caminho, cabeçalhos)."""

    def __init__(self, master: str | None = SHA_A) -> None:
        self.master = master
        self.head: str | None = "refs/heads/master"
        self.extra_refs: dict[str, str] = {}
        self.content_type = ADVERTISEMENT
        self.status: dict[str, int] = {}
        self.archives: dict[str, bytes] = {}
        self.api_sha: str | None = None
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def root(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def refs(self) -> dict[str, str]:
        refs = dict(self.extra_refs)
        if self.master:
            refs["refs/heads/master"] = self.master
        return refs

    def paths(self, kind: str) -> list[str]:
        marks = {"info": "/info/refs", "archive": "/archive/", "api": "/api/"}
        return [p for p, _ in self.requests if marks[kind] in p]

    def _respond(self, handler: BaseHTTPRequestHandler) -> None:
        self.requests.append((handler.path, {k: v for k, v in handler.headers.items()}))
        path = handler.path.split("?", 1)[0]
        for prefix, code in self.status.items():
            if path.startswith(prefix) or prefix in path:
                return self._send(handler, code, b"erro", "text/plain")
        if path == f"/{OWNER}/{NAME}.git/info/refs":
            return self._send(handler, 200, info_refs_body(self.refs(), self.head), self.content_type)
        archive_prefix = f"/{OWNER}/{NAME}/archive/"
        if path.startswith(archive_prefix) and path.endswith(".zip"):
            sha = path[len(archive_prefix):-4]
            if sha in self.archives:
                return self._send(handler, 200, self.archives[sha], "application/zip")
        if path == f"/api/repos/{OWNER}/{NAME}/commits/master":
            sha = self.api_sha or self.master
            if sha:
                return self._send(handler, 200, sha.encode("ascii"), "application/vnd.github.sha")
        return self._send(handler, 404, b"not found", "text/plain")

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, code: int, body: bytes, content_type: str) -> None:
        handler.send_response(code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def start(self) -> FakeGitHub:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - nome exigido pelo http.server
                owner._respond(self)

            def log_message(self, format: str, *args: Any) -> None:  # silencioso
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-github", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> FakeGitHub:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ------------------------------------------------------------------------------------------- instalações falsas

def copy_package(dest_site: Path, source: Path = PKG_SRC) -> Path:
    """Copia ``src/rumbapy`` (sem ``__pycache__``) para ``dest_site/rumbapy``."""
    target = dest_site / "rumbapy"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return target


def write_dist_info(site: Path, direct_url: Mapping[str, Any] | None, *, version: str = VERSION,
                    name: str = "rumbapy") -> Path:
    """``rumbapy-<v>.dist-info`` mínimo (METADATA, RECORD, direct_url.json) em ``site``."""
    for old in site.glob(f"{name}-*.dist-info"):
        shutil.rmtree(old)
    dist = site / f"{name}-{version}.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    (dist / "RECORD").write_text("", encoding="utf-8")
    (dist / "top_level.txt").write_text("rumbapy\n", encoding="utf-8")
    if direct_url is not None:
        (dist / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    return dist


def archive_direct_url(root: str, sha: str) -> dict[str, Any]:
    return {"url": f"{root}/{OWNER}/{NAME}/archive/{sha}.zip", "archive_info": {}}


def make_venv(path: Path, *, with_pip: bool = False) -> Path:
    """venv temporário; devolve o ``python.exe`` dele."""
    args = [sys.executable, "-m", "venv", str(path)]
    if not with_pip:
        args.insert(3, "--without-pip")
    subprocess.run(args, check=True, capture_output=True, timeout=240)
    return path / "Scripts" / "python.exe"


def venv_site(python: Path) -> Path:
    return python.parent.parent / "Lib" / "site-packages"


def child_env(home: Path, **extra: str | None) -> dict[str, str]:
    """Ambiente de um subprocesso: sem ``PYTHON*``/``RUMBAPY_*`` herdados; sandbox ligado; ``extra`` por cima
    (valor ``None`` remove a chave)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "RUMBAPY_"))}
    env.update({"RUMBAPY_SANDBOX": "1", "RUMBAPY_HOME": str(home), "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1"})
    for key, value in extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def run_python(python: Path | str, code: str, env: Mapping[str, str], cwd: Path,
               timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(python), "-c", code], env=dict(env), cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def zip_tree(files: Iterable[tuple[str, bytes]], prefix: str) -> bytes:
    """Zip no formato do archive do GitHub (tudo sob ``<prefix>/``)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(f"{prefix}/{name}", data)
    return buf.getvalue()


def working_tree_files() -> list[tuple[str, bytes]]:
    """Arquivos versionados + novos não ignorados (equivale ao ``git archive HEAD`` no CI)."""
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=REPO_ROOT, capture_output=True,
                         check=True, timeout=60).stdout.decode("utf-8")
    files = []
    for rel in sorted(filter(None, out.split("\0"))):
        path = REPO_ROOT / rel
        if path.is_file():
            files.append((rel, path.read_bytes()))
    return files
