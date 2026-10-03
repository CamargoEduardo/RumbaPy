"""Caminho crítico do auto-update (H.3, H.4, H.6) — CONGELADO.

Este arquivo é o que conserta uma instalação quebrada: por isso só usa a stdlib e **nunca importa** ``rumbapy.*``
(nem ``_version``: as constantes do repositório são repetidas aqui). O SHA-256 dele fica em ``_build/bootstrap.sha256``
e um teste falha se divergir. Mudar este arquivo = atualizar o hash no mesmo commit, com teste manual do ``heal``
registrado no PR (H.6).

API: ``say``, ``log_line``, ``remote_master_sha``, ``pip_install``, ``fresh_installed``, ``heal`` (+ auxiliares usados
pelo ``_update``: ``BootstrapError``, ``archive_sha``, ``commit_from_direct_url``, ``process_alive``,
``process_created``, ``pip_argv``, ``update_lock``, ``update_busy``, ``marker_record``, ``read_json``, ``write_json``
e caminhos).

Marcador ``update-in-progress.json`` (E6): ``{pid, created, remote, at}`` do processo que atualiza, mais
``{pip_pid, pip_created}`` assim que o pip nasce. O pip é desacoplado e sobrevive ao pai (Ctrl+C duplo, kernel
fechado): por isso "o dono morreu" não prova que o pip parou, e ninguém reinstala enquanto ``pip_pid`` estiver vivo.

Sandbox (J.1/J.2): com ``RUMBAPY_SANDBOX=1`` a rede só vai para ``RUMBAPY_TEST_GITHUB`` (raiz que imita
``https://github.com``; a API fica em ``<raiz>/api``) e o pip só roda com ``RUMBAPY_TEST_PIP`` (caminho de um script
que substitui ``-m pip``, ou ``pip`` = pip real, aceito só dentro de um venv). Sem sandbox essas variáveis são ignoradas.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error
import subprocess
import traceback
import sysconfig
import secrets
import shutil
import ctypes
import socket
import json
import site
import time
import ssl
import sys
import os
import re

try:
    import msvcrt
except ImportError:                       # pragma: no cover - o RumbaPy só existe no Windows
    msvcrt = None  # type: ignore[assignment]

REPO_OWNER = "CamargoEduardo"
REPO_NAME = "RumbaPy"
GITHUB_ROOT = "https://github.com"
API_ROOT = "https://api.github.com"
MASTER_REF = "refs/heads/master"
RECOVERY_COMMAND = ("python -m pip install --force-reinstall --no-deps "
                    "https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip")
ENSUREPIP_HINT = "python -m ensurepip --upgrade"

ADVERTISEMENT = "application/x-git-upload-pack-advertisement"
HTTP_TIMEOUT = 5.0
HTTP_ATTEMPTS = 2
HTTP_RETRY_DELAY = 1.0
GIT_TIMEOUT = 10.0
FRESH_TIMEOUT = 60.0
PIP_SLOW_NOTICE = 300.0
LOCK_TIMEOUT = 600.0
LOCK_POLL = 0.1
HEAL_WAIT = 60.0
HEAL_WINDOW = 3600.0
HEAL_KEEP = 10
HEAL_BUSY = "O RumbaPy está sendo atualizado por outro processo; rode de novo em alguns segundos."

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5

SHA_RE = re.compile(r"[0-9a-f]{40}")
ARCHIVE_RE = re.compile(r"^https://(?:github\.com/[^/]+/[^/]+/archive/([0-9a-f]{40})\.zip"
                        r"|codeload\.github\.com/[^/]+/[^/]+/zip/([0-9a-f]{40}))$", re.I)

_sleep = time.sleep                       # pontos de injeção dos testes
_k32: Any = None


class BootstrapError(Exception):
    """Falha de uma primitiva do bootstrap. ``kind``: ``network`` | ``config`` | ``sandbox`` | ``lock``."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


# ------------------------------------------------------------------------------------------- ambiente e caminhos

def sandboxed() -> bool:
    return os.environ.get("RUMBAPY_SANDBOX") == "1"


def fake_github_root() -> str | None:
    """Raiz falsa do GitHub (só com sandbox); ``None`` = GitHub de verdade."""
    value = os.environ.get("RUMBAPY_TEST_GITHUB") if sandboxed() else None
    return value.rstrip("/") if value else None


def fake_pip() -> str | None:
    """Substituto do pip (só com sandbox)."""
    return (os.environ.get("RUMBAPY_TEST_PIP") or None) if sandboxed() else None


def github_root() -> str:
    return fake_github_root() or GITHUB_ROOT


def api_root() -> str:
    root = fake_github_root()
    return f"{root}/api" if root else API_ROOT


def repo_url() -> str:
    return f"{github_root()}/{REPO_OWNER}/{REPO_NAME}"


def info_refs_url() -> str:
    return f"{repo_url()}.git/info/refs?service=git-upload-pack"


def archive_url(sha: str) -> str:
    return f"{repo_url()}/archive/{sha}.zip"


def api_commit_url() -> str:
    return f"{api_root()}/repos/{REPO_OWNER}/{REPO_NAME}/commits/master"


def home() -> Path | None:
    """Mesma regra de ``_paths.home()`` sem importá-lo; ``None`` no sandbox sem ``RUMBAPY_HOME``."""
    override = os.environ.get("RUMBAPY_HOME")
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    if sandboxed():
        return None
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "rumbapy"


def _home_file(*parts: str) -> Path | None:
    root = home()
    return root.joinpath(*parts) if root is not None else None


def update_log_path() -> Path | None:
    return _home_file("logs", "update.log")


def in_progress_path() -> Path | None:
    return _home_file("update-in-progress.json")


def heal_state_path() -> Path | None:
    return _home_file("heal-state.json")


def version() -> str:
    """``__version__`` lido do ``_version.py`` vizinho por regex (sem importar)."""
    try:
        text = (Path(__file__).resolve().parent / "_version.py").read_text(encoding="utf-8")
    except OSError:
        return "?"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return match.group(1) if match else "?"


# ------------------------------------------------------------------------------------------- saída e log

def say(msg: str) -> None:
    """Uma linha em stderr (nunca falha; ``RUMBAPY_QUIET=1`` silencia; ``sys.stderr`` pode ser ``None``, E25)."""
    if os.environ.get("RUMBAPY_QUIET") == "1":
        return
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(msg, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        try:
            print(msg.encode(encoding, "replace").decode(encoding, "replace"), file=stream, flush=True)
        except (OSError, ValueError, LookupError):
            pass
    except (OSError, ValueError, AttributeError):
        pass


def log_line(msg: str, level: str = "INFO") -> None:
    """Acrescenta ``AAAA-MM-DD HH:MM:SS.mmm NIVEL mensagem`` ao ``logs\\update.log`` (nunca falha)."""
    path = update_log_path()
    if path is None:
        return
    now = datetime.now()
    line = f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d} {level:<5} {msg}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
    except OSError:
        pass


# ------------------------------------------------------------------------------------------- JSON e trava

def read_json(path: Path | None) -> dict[str, Any] | None:
    """Objeto JSON do arquivo ou ``None`` (ausente, ilegível ou inválido)."""
    if path is None:
        return None
    try:
        obj = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def write_json(path: Path | None, obj: Any) -> None:
    """Gravação atômica (``tmp`` + ``os.replace`` com até 20 tentativas em ``PermissionError``)."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{secrets.token_hex(4)}")
    try:
        tmp.write_bytes(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@contextmanager
def update_lock(timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """``locks\\update.lock`` (mesmo arquivo e mesmo byte que ``_locks.file_lock("update")``)."""
    path = _home_file("locks", "update.lock")
    if path is None or msvcrt is None:
        raise BootstrapError("lock", "sem pasta do RumbaPy para a trava de atualização")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    fd = os.open(path, flags, 0o600)
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BootstrapError("lock", "outra atualização não terminou") from None
                time.sleep(LOCK_POLL)
        try:
            yield
        finally:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------------- processos

def _kernel32() -> Any:
    global _k32
    if _k32 is None:
        from ctypes import wintypes

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _k32.OpenProcess.restype = ctypes.c_void_p
        _k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        _k32.GetExitCodeProcess.restype = wintypes.BOOL
        _k32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        _k32.GetProcessTimes.restype = wintypes.BOOL
        _k32.CloseHandle.argtypes = [ctypes.c_void_p]
        _k32.CloseHandle.restype = wintypes.BOOL
    return _k32


def process_created(pid: int) -> int | None:
    """Hora de criação do processo (FILETIME, 100 ns) ou ``None`` se não deu para consultar."""
    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return int(times[0].value)
    finally:
        k32.CloseHandle(handle)


def process_alive(pid: int, created: int | None = None) -> bool:
    """O processo ``pid`` existe (e, com ``created``, é o mesmo: PID não reaproveitado)? Só consulta, nunca mata."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    from ctypes import wintypes

    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED     # existe, mas é de outro usuário
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
    finally:
        k32.CloseHandle(handle)
    try:
        expected = int(created) if created is not None else None
    except (TypeError, ValueError):       # marcador corrompido: não dá para conferir a reutilização do PID
        expected = None
    if expected is not None:
        now_created = process_created(pid)
        if now_created is not None and now_created != expected:
            return False
    return True


def marker_record(remote: str, pip: Any = None) -> dict[str, Any]:
    """Conteúdo do ``update-in-progress.json`` deste processo (+ ``pip_pid``/``pip_created`` se ``pip`` existir)."""
    record: dict[str, Any] = {"pid": os.getpid(), "created": process_created(os.getpid()), "remote": remote,
                              "at": time.time()}
    if pip is not None:
        record["pip_pid"] = int(pip.pid)
        record["pip_created"] = process_created(int(pip.pid))
    return record


def pip_alive(marker: dict[str, Any] | None) -> int | None:
    """PID do pip registrado no marcador, se ele ainda estiver rodando (senão ``None``)."""
    if not isinstance(marker, dict):
        return None
    pip_pid = marker.get("pip_pid")
    if isinstance(pip_pid, int) and process_alive(pip_pid, marker.get("pip_created")):
        return pip_pid
    return None


def _update_lock_free() -> bool:
    """Ninguém segura a trava de update agora? (``False`` também se não deu para consultar.)"""
    try:
        with update_lock(0):
            return True
    except (BootstrapError, OSError):
        return False


def update_busy(marker: dict[str, Any] | None) -> bool:
    """Alguém está mexendo na instalação agora?

    Sim se o pip do marcador está vivo (mesmo que o dono seja este processo ou já tenha morrido), ou se o dono é
    OUTRO processo vivo que ainda segura a trava de update. Dono vivo e ocioso (trava livre) não conta: o marcador
    ficou para trás (ex.: Ctrl+C depois de o pip terminar).
    """
    if not isinstance(marker, dict):
        return False
    if pip_alive(marker) is not None:
        return True
    pid = marker.get("pid")
    if not isinstance(pid, int) or pid == os.getpid() or not process_alive(pid, marker.get("created")):
        return False
    return not _update_lock_free()


# ------------------------------------------------------------------------------------------- commit remoto (H.3)

def parse_pkt_lines(data: bytes) -> list[bytes | None]:
    """Blocos ``<4 hex><dados>`` do protocolo Git; ``None`` = flush/delim. ``ValueError`` se malformado."""
    out: list[bytes | None] = []
    pos = 0
    while pos < len(data):
        head = data[pos:pos + 4]
        if len(head) < 4:
            raise ValueError("pkt-line truncada")
        try:
            size = int(head.decode("ascii"), 16)
        except (UnicodeDecodeError, ValueError):
            raise ValueError(f"tamanho de pkt-line inválido: {head!r}") from None
        if size in (0, 1, 2):
            out.append(None)
            pos += 4
            continue
        if size < 4 or pos + size > len(data):
            raise ValueError("pkt-line com tamanho inconsistente")
        out.append(data[pos + 4:pos + size])
        pos += size
    return out


def parse_info_refs(data: bytes) -> tuple[str | None, str | None]:
    """``(sha de refs/heads/master, alvo do symref HEAD)`` de uma resposta ``info/refs`` (v0).

    ``ValueError`` se a resposta não tiver o formato do Git; sha ``None`` se não houver ``refs/heads/master``.
    """
    lines = parse_pkt_lines(data)
    payloads = [p for p in lines if p is not None]
    if not payloads or not payloads[0].startswith(b"# service=git-upload-pack"):
        raise ValueError("resposta sem '# service=git-upload-pack'")
    master: str | None = None
    symref: str | None = None
    for raw in payloads[1:]:
        ref_part, _, caps = raw.rstrip(b"\n").partition(b"\0")
        for cap in caps.split():
            if cap.startswith(b"symref=HEAD:"):
                symref = cap[len(b"symref=HEAD:"):].decode("ascii", "replace")
        sha_b, _, name = ref_part.partition(b" ")
        sha = sha_b.decode("ascii", "replace").lower()
        if name == MASTER_REF.encode() and master is None:
            if not SHA_RE.fullmatch(sha):
                raise ValueError("sha inválido em refs/heads/master")
            master = sha
    return master, symref


def _opener() -> urllib.request.OpenerDirector:
    handlers: list[Any] = [urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if fake_github_root():
        handlers.append(urllib.request.ProxyHandler({}))       # GitHub falso no loopback: nunca via proxy
    return urllib.request.build_opener(*handlers)


def _classify(exc: BaseException) -> BootstrapError:
    if isinstance(exc, BootstrapError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        kind = "config" if exc.code in (401, 404) else "network"
        return BootstrapError(kind, f"HTTP {exc.code}")
    reason: Any = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return BootstrapError("network", "DNS: nome não resolvido")
    if isinstance(reason, ssl.SSLError):
        return BootstrapError("network", f"TLS: {type(reason).__name__}")
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return BootstrapError("network", "tempo esgotado")
    if isinstance(reason, ConnectionRefusedError):
        return BootstrapError("network", "conexão recusada")
    if isinstance(reason, OSError):
        return BootstrapError("network", f"rede: {type(reason).__name__}")
    return BootstrapError("network", f"{type(reason).__name__}: {reason}")


def _http_get(url: str, headers: dict[str, str]) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _opener().open(request, timeout=HTTP_TIMEOUT) as response:
            return response.read(), response.headers.get("Content-Type", "") or ""
    except Exception as exc:   # noqa: BLE001 - classificado em network/config
        raise _classify(exc) from None


def _git_headers() -> dict[str, str]:
    return {"User-Agent": f"git/2.45 (rumbapy/{version()})", "Cache-Control": "no-cache", "Pragma": "no-cache"}


def _from_info_refs() -> str:
    last: BootstrapError | None = None
    for attempt in range(HTTP_ATTEMPTS):
        if attempt:
            _sleep(HTTP_RETRY_DELAY)
        try:
            body, content_type = _http_get(info_refs_url(), _git_headers())
        except BootstrapError as exc:
            if exc.kind != "network":
                raise
            last = exc
            continue
        if content_type.split(";")[0].strip().lower() != ADVERTISEMENT:
            last = BootstrapError("network", f"resposta inesperada ({content_type or 'sem Content-Type'}; proxy ou "
                                             "portal cativo?)")
            continue
        try:
            master, symref = parse_info_refs(body)
        except ValueError as exc:
            last = BootstrapError("network", f"resposta do Git inválida ({exc})")
            continue
        if master is None:
            raise BootstrapError("config", "o repositório não tem refs/heads/master")
        if symref and symref != MASTER_REF:
            log_line(f"remote: o HEAD do repositório aponta para {symref}; sigo {MASTER_REF}", "WARNING")
        return master
    assert last is not None
    raise last


def _git_executable() -> str | None:
    return shutil.which("git")


def _from_git(git: str) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        done = subprocess.run([git, "ls-remote", f"{repo_url()}.git", MASTER_REF], capture_output=True,
                              timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, env=env, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise BootstrapError("network", "git ls-remote: tempo esgotado") from None
    except OSError as exc:
        raise BootstrapError("network", f"git ls-remote: {type(exc).__name__}") from None
    if done.returncode != 0:
        raise BootstrapError("network", f"git ls-remote: código {done.returncode}")
    for line in done.stdout.decode("ascii", "replace").splitlines():
        sha, _, name = line.strip().partition("\t")
        if name.strip() == MASTER_REF and SHA_RE.fullmatch(sha.lower()):
            return sha.lower()
    raise BootstrapError("config", "git ls-remote: o repositório não tem refs/heads/master")


def _from_api() -> str:
    headers = {"User-Agent": f"rumbapy/{version()}", "Accept": "application/vnd.github.sha",
               "Cache-Control": "no-cache", "Pragma": "no-cache"}
    body, _ = _http_get(api_commit_url(), headers)
    sha = body.decode("ascii", "replace").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise BootstrapError("network", "API do GitHub: resposta sem sha")
    return sha


def remote_master_sha() -> str:
    """Sha de ``refs/heads/master`` no GitHub: ``info/refs`` → ``git ls-remote`` → API (H.3).

    ``BootstrapError`` (``network``/``config``) se as três fontes falharem; ``sandbox`` se o sandbox recusar a rede.
    """
    fake = fake_github_root()
    if sandboxed():
        if not fake:
            raise BootstrapError("sandbox", "consulta ao GitHub sem RUMBAPY_TEST_GITHUB")
        log_line(f"remote: sandbox usando RUMBAPY_TEST_GITHUB={fake}")
    failures: list[tuple[str, BootstrapError]] = []
    try:
        return _from_info_refs()
    except BootstrapError as exc:
        failures.append(("info/refs", exc))
    git = _git_executable()
    if git:
        try:
            return _from_git(git)
        except BootstrapError as exc:
            failures.append(("git", exc))
    try:
        return _from_api()
    except BootstrapError as exc:
        failures.append(("api", exc))
    first = failures[0][1]
    reason = "; ".join(f"{source}: {exc.reason}" for source, exc in failures)
    log_line(f"remote: falhou ({reason})", "WARNING")
    raise BootstrapError(first.kind, reason)


# ------------------------------------------------------------------------------------------- instalação (H.4)

def archive_sha(url: Any) -> str | None:
    """Sha de uma URL de archive do GitHub (``archive/<sha>.zip`` ou ``codeload``); aceita a raiz falsa do sandbox."""
    if not isinstance(url, str):
        return None
    match = ARCHIVE_RE.match(url)
    if match:
        return (match.group(1) or match.group(2)).lower()
    fake = fake_github_root()
    if fake:
        local = re.match(re.escape(fake) + r"/[^/]+/[^/]+/archive/([0-9a-f]{40})\.zip$", url, re.I)
        if local:
            return local.group(1).lower()
    return None


def commit_from_direct_url(direct_url: Any) -> str | None:
    """Commit instalado segundo o ``direct_url.json`` (archive com sha, ou ``vcs_info`` git das 0.1.x)."""
    if not isinstance(direct_url, dict):
        return None
    dir_info = direct_url.get("dir_info")
    if isinstance(dir_info, dict) and dir_info.get("editable"):
        return None
    sha = archive_sha(direct_url.get("url"))
    if sha:
        return sha
    vcs = direct_url.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git":
        commit = str(vcs.get("commit_id") or "").lower()
        if SHA_RE.fullmatch(commit):
            return commit
    return None


def _writable(directory: str) -> bool:
    probe = Path(directory) / f".rumbapy-write-{secrets.token_hex(4)}"
    try:
        with open(probe, "xb"):
            pass
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        pass
    return True


def _needs_user() -> bool:
    """``--user`` só fora de venv, com o site do usuário habilitado e o site-packages sem escrita."""
    if sys.prefix != sys.base_prefix or not site.ENABLE_USER_SITE:
        return False
    return not _writable(sysconfig.get_paths()["purelib"])


def pip_argv(sha: str) -> list[str]:
    """Linha de comando exata do pip (H.4)."""
    argv = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--force-reinstall",
            "--no-deps", archive_url(sha)]
    if _needs_user():
        argv.append("--user")
    return argv


def _spawn_detached(argv: list[str], out: Any, cwd: str | None) -> subprocess.Popen[bytes]:
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=cwd,
                                close_fds=True, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
    except OSError as exc:
        if getattr(exc, "winerror", None) != ERROR_ACCESS_DENIED:
            raise
        log_line("pip: o Job atual não permite breakaway; o pip roda dentro dele", "WARNING")
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=cwd,
                                close_fds=True, creationflags=flags)


def _wait_pip(proc: Any, started: float) -> tuple[int, bool]:
    """Espera o pip terminar: ``(código, houve Ctrl+C)``.

    1º Ctrl+C: avisa e CONTINUA esperando (a trava e o marcador só podem ser soltos com o pip parado). 2º Ctrl+C:
    desiste de esperar e relança; o pip segue sozinho e o ``pip_pid`` do marcador impede outra instalação por cima.
    """
    warned = False
    interrupted = False
    while True:
        try:
            return int(proc.wait(timeout=1.0)), interrupted
        except subprocess.TimeoutExpired:
            if not warned and time.monotonic() - started >= PIP_SLOW_NOTICE:
                say("RumbaPy: a atualização está demorando…")
                warned = True
        except KeyboardInterrupt:
            if interrupted:
                log_line(f"pip: 2º Ctrl+C; o pip (PID {proc.pid}) continua rodando sozinho", "WARNING")
                say("RumbaPy: o pip continua rodando em segundo plano; espere ele terminar antes de usar o RumbaPy.")
                raise
            interrupted = True
            log_line(f"pip: Ctrl+C durante a instalação; esperando o pip (PID {proc.pid}) terminar", "WARNING")
            say("RumbaPy: aguarde: o pip está terminando a atualização… (Ctrl+C de novo para sair sem esperar)")


def pip_install(sha: str, on_spawn: Callable[[Any], None] | None = None) -> int:
    """Instala ``archive/<sha>.zip`` com o pip deste Python, desacoplado, **sem prazo e sem kill** (E6).

    Devolve o código de saída do pip. A saída vai para ``logs\\pip-<AAAAMMDD-HHMMSS>.log``. ``on_spawn(proc)`` é
    chamado logo que o pip nasce (o ``_update`` grava ``pip_pid`` no marcador); um erro nele só vai para o log. Um
    Ctrl+C durante a espera é relançado só depois de o pip terminar (ver ``_wait_pip``).
    """
    if not (isinstance(sha, str) and SHA_RE.fullmatch(sha)):
        raise ValueError(f"sha inválido: {sha!r}")
    argv = pip_argv(sha)
    if sandboxed():
        substitute = fake_pip()
        if not substitute:
            raise BootstrapError("sandbox", "pip install sem RUMBAPY_TEST_PIP")
        if substitute == "pip":
            if sys.prefix == sys.base_prefix:
                raise BootstrapError("sandbox", "pip real fora de um venv")
        else:
            argv = [argv[0], substitute, *argv[3:]]
        log_line(f"pip: sandbox usando RUMBAPY_TEST_PIP={substitute}")
    root = home()
    log_path = root / "logs" / f"pip-{datetime.now():%Y%m%d-%H%M%S}.log" if root is not None else None
    say(f"RumbaPy: atualizando para {sha[:7]}…")
    log_line(f"pip: {' '.join(argv)} (saída em {log_path or os.devnull})")
    started = time.monotonic()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path if log_path is not None else os.devnull, "ab") as out:
        proc = _spawn_detached(argv, out, str(root) if root is not None and root.is_dir() else None)
        if on_spawn is not None:
            try:
                on_spawn(proc)
            except Exception as exc:   # noqa: BLE001 - o pip já está rodando: nunca abandoná-lo por causa disto
                log_line(f"pip: on_spawn falhou ({type(exc).__name__}: {exc})", "WARNING")
        rc, interrupted = _wait_pip(proc, started)
    log_line(f"pip: código {rc} em {time.monotonic() - started:.1f} s")
    if interrupted:
        raise KeyboardInterrupt
    if rc != 0 and log_path is not None:
        try:
            tail = log_path.read_bytes()[-4000:].decode("utf-8", "replace")
        except OSError:
            tail = ""
        if "No module named pip" in tail:
            say(f"RumbaPy: este Python não tem o pip; rode: {ENSUREPIP_HINT}")
    return int(rc)


SONDA = (
    "import importlib.metadata,importlib.util,json,os,sys\n"
    "c=os.path.abspath(os.getcwd())\n"
    "sys.path[:]=[p for p in sys.path if p and os.path.abspath(p)!=c]\n"
    "s=importlib.util.find_spec('rumbapy')\n"
    "o=s.origin if s else None\n"
    "r={'file':o,'version':None,'direct_url':None}\n"
    "if o:\n"
    " h=os.path.normcase(os.path.realpath(o))\n"
    " for d in importlib.metadata.distributions(name='rumbapy'):\n"
    "  try:\n"
    "   if os.path.normcase(os.path.realpath(str(d.locate_file('rumbapy/__init__.py'))))!=h:\n"
    "    continue\n"
    "   t=d.read_text('direct_url.json')\n"
    "   r['version']=d.version\n"
    "   r['direct_url']=json.loads(t) if t else None\n"
    "  except Exception:\n"
    "   continue\n"
    "  break\n"
    "print(json.dumps(r))\n"
)


def fresh_installed() -> dict[str, Any]:
    """O que um interpretador NOVO vê instalado (E10): ``{"file", "url", "commit", "version"}`` (+ ``error``)."""
    result: dict[str, Any] = {"file": None, "url": None, "commit": None, "version": None}
    root = home()
    try:
        done = subprocess.run([sys.executable, "-E", "-c", SONDA], capture_output=True, timeout=FRESH_TIMEOUT,
                              stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                              cwd=str(root) if root is not None and root.is_dir() else None)
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["error"] = type(exc).__name__
        return result
    lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        data = json.loads(lines[-1]) if done.returncode == 0 and lines else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        result["error"] = f"sonda código {done.returncode}"
        return result
    direct_url = data.get("direct_url")
    result["file"] = data.get("file")
    result["version"] = data.get("version")
    result["url"] = direct_url.get("url") if isinstance(direct_url, dict) else None
    result["commit"] = commit_from_direct_url(direct_url)
    return result


# ------------------------------------------------------------------------------------------- heal (H.6)

def _wait_other_update(marker: dict[str, Any] | None) -> bool:
    """Se há uma atualização em andamento (``update_busy``), espera até 60 s. ``True`` se havia."""
    if not update_busy(marker):
        return False
    deadline = time.monotonic() + HEAL_WAIT
    while time.monotonic() < deadline:
        _sleep(0.5)
        if not update_busy(read_json(in_progress_path())):
            break
    return True


def _rerun_action() -> str:
    """Próxima ação depois do reparo, pelo contexto (mesmos textos do ``UpdateRequired``, H.4)."""
    if "ipykernel" in sys.modules or "IPython" in sys.modules:
        return "Reinicie o kernel (Kernel → Restart) e rode a célula de novo."
    if hasattr(sys, "ps1") or sys.flags.interactive or "idlelib" in sys.modules:
        return "Feche este Python e abra de novo."
    return "Rode de novo."


def _heal_pip(remote: str) -> int:
    """``pip_install`` do heal com o mesmo marcador do ``_update`` (pid e, depois, ``pip_pid``); só sob a trava."""
    path = in_progress_path()

    def mark(proc: Any = None) -> None:
        try:
            write_json(path, marker_record(remote, proc))
        except OSError as failure:
            log_line(f"heal: não consegui gravar update-in-progress.json ({type(failure).__name__})", "WARNING")

    def unmark() -> None:
        try:
            if path is not None:
                path.unlink()
        except FileNotFoundError:
            pass
        except OSError as failure:
            log_line(f"heal: não consegui apagar update-in-progress.json ({type(failure).__name__})", "WARNING")

    mark()
    try:
        rc = pip_install(remote, on_spawn=mark)
    except (BootstrapError, OSError, ValueError):
        unmark()                          # o pip nem começou
        raise
    unmark()                              # Ctrl+C (KeyboardInterrupt) não passa aqui: o marcador fica com o pip_pid
    return rc


def heal(exc: BaseException) -> NoReturn:
    """O ``import rumbapy`` falhou com um erro que não é da biblioteca (A.6): tenta reinstalar o ``master``.

    Sempre levanta: ``exc`` com ``RUMBAPY_DEV=1``; senão ``RuntimeError`` com a próxima ação para o usuário.
    """
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    log_line(f"heal: o import do RumbaPy falhou ({type(exc).__name__}: {exc})\n{detail}", "ERROR")
    if os.environ.get("RUMBAPY_DEV") == "1":
        raise exc
    if _wait_other_update(read_json(in_progress_path())):
        raise RuntimeError(HEAL_BUSY) from exc
    try:
        remote = remote_master_sha()
    except BootstrapError as failure:
        raise RuntimeError(f"O RumbaPy não pôde ser carregado e não consegui consultar a versão publicada "
                           f"({failure.reason}). Para reinstalar à mão, rode: {RECOVERY_COMMAND}") from exc
    state = read_json(heal_state_path()) or {}
    attempts = [a for a in state.get("attempts", []) if isinstance(a, dict)]
    now = time.time()
    for attempt in attempts:              # só conta reinstalação que DEU CERTO (rc 0) e o código continua quebrado
        at = attempt.get("at")
        if (attempt.get("remote") == remote and attempt.get("rc") == 0 and isinstance(at, (int, float))
                and 0 <= now - at < HEAL_WINDOW):
            log_line(f"heal: {remote[:7]} já foi reinstalado há {now - at:.0f} s e continua quebrado", "ERROR")
            raise RuntimeError(f"A versão publicada do RumbaPy ({remote[:7]}) está com defeito; avise o "
                               "responsável. Detalhes em update.log") from exc
    try:
        with update_lock(LOCK_TIMEOUT):
            # o dono de uma atualização pode ter morrido (Ctrl+C duplo) entre a espera acima e a trava, deixando o
            # pip dele rodando sozinho: dois pip --force-reinstall no mesmo site-packages, nunca (E6)
            if pip_alive(read_json(in_progress_path())) is not None:
                raise RuntimeError(HEAL_BUSY) from exc
            rc = _heal_pip(remote)
            attempts.append({"remote": remote, "at": now, "rc": rc, "error": type(exc).__name__})
            try:                          # falhar aqui não desfaz o reparo: só fica sem a proteção de 1 h
                write_json(heal_state_path(), {"attempts": attempts[-HEAL_KEEP:]})
            except OSError as failure:
                log_line(f"heal: não consegui gravar heal-state.json ({type(failure).__name__})", "WARNING")
    except (BootstrapError, OSError, ValueError) as failure:
        reason = failure.reason if isinstance(failure, BootstrapError) else type(failure).__name__
        log_line(f"heal: reinstalação impossível ({reason})", "ERROR")
        raise RuntimeError(f"O RumbaPy não pôde ser carregado nem reinstalado ({reason}). Para reinstalar à mão, "
                           f"rode: {RECOVERY_COMMAND}") from exc
    log_line(f"heal: pip código {rc} para {remote[:7]}")
    if rc != 0:
        raise RuntimeError(f"O RumbaPy não pôde ser carregado e a reinstalação falhou (pip código {rc}). Detalhes "
                           f"em update.log. Para reinstalar à mão, rode: {RECOVERY_COMMAND}") from exc
    raise RuntimeError(f"O RumbaPy foi reparado para {remote[:7]}. {_rerun_action()}") from exc
