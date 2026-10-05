"""Política do auto-update obrigatório (H.1, H.2, H.4). As primitivas (rede, pip, verificação) moram no
``_bootstrap`` congelado; aqui só se decide quando consultar, quando instalar e o que dizer ao usuário.

Invariantes: o usuário roda o commit de ``refs/heads/master``; a checagem acontece antes de qualquer código que fale
com o terminal; a única exceção é ``RUMBAPY_DEV=1`` (avisada a cada import); falha de rede BLOQUEIA
(``UpdateCheckFailed``); depois de instalar, sempre ``UpdateRequired`` (nunca reexecuta o script, H.5).
"""
# ruff: noqa: I001
from __future__ import annotations

from urllib.request import url2pathname
from urllib.parse import urlparse
from importlib import metadata
from pathlib import Path
from typing import Any

from .errors import SandboxViolation, UpdateCheckFailed, UpdateFailed, UpdateRequired
from ._locks import LockTimeout, file_lock
from . import _bootstrap, _paths, _log
from ._version import __version__

import threading
import logging
import json
import time
import sys
import os

logger = logging.getLogger(__name__)
# biblioteca: sem handler configurado pelo usuário, os WARNING daqui não vão para o stderr pelo "lastResort"
# (os avisos ao usuário saem por _log.notice, que respeita RUMBAPY_QUIET)
logging.getLogger("rumbapy").addHandler(logging.NullHandler())

ARCHIVE_RE = _bootstrap.ARCHIVE_RE
CACHE_TTL = 60.0                          # update-state.json dispensa a consulta por até 60 s (E7)
TTL_INTERACTIVE = 3600.0                  # open/attach/RumbaClient() em REPL/Jupyter: reconsulta após 1 h
TTL_SCRIPT = 12 * 3600.0                  # ... em script: após 12 h
LOCK_TIMEOUT = 600.0
PIP_POLL = 0.5                            # espera de um pip anterior ainda vivo (marcador com pip_pid)
CONTEXTS = frozenset({"import", "open", "cli", "legacy"})
VALID_RESULTS = frozenset({"up-to-date", "updated"})

_restart_required: str | None = None      # variável de MÓDULO (nunca os.environ: não vaza para filhos; E8, R26)
_restart_message: str | None = None
_restart_version: str | None = None
_checked_at: float | None = None          # time.monotonic() da última checagem bem-sucedida deste processo
# (modo, commit) do disco na 1ª checagem deste processo = o código que está na memória. Numa reconsulta (TTL do
# open), commit diferente no disco quer dizer que outro processo atualizou o RumbaPy: este tem de ser reiniciado.
_seen_local: tuple[str, str | None] | None = None
_guard = threading.RLock()
_sleep = time.sleep                       # ponto de injeção dos testes


# ------------------------------------------------------------------------------------------- ambiente

def _orig_head() -> list[str]:
    return list(getattr(sys, "orig_argv", []) or [])[1:3]


def _is_legacy_shim() -> bool:
    # mesmo critério de _role.detect() (A.7); repetido porque _update só importa a lista da A.4.2
    return _orig_head() == ["-m", "rumbapy.server"]


def _is_cli() -> bool:
    return _orig_head() == ["-m", "rumbapy"]


def _interactive() -> bool:
    """REPL/Jupyter/IDLE/``python -i`` (TTL de 1 h do ``open``, H.1): o mesmo critério do ``_action``."""
    return (hasattr(sys, "ps1") or bool(sys.flags.interactive)
            or any(name in sys.modules for name in ("ipykernel", "IPython", "idlelib")))


def _action(context: str) -> str:
    """Texto de ``{acao}`` do ``UpdateRequired`` (H.4)."""
    if context in ("open", "legacy", "cli"):
        return UpdateRequired.ACTIONS["process"]
    if "ipykernel" in sys.modules or "IPython" in sys.modules:
        return UpdateRequired.ACTIONS["jupyter"]
    if hasattr(sys, "ps1") or sys.flags.interactive or "idlelib" in sys.modules:
        return UpdateRequired.ACTIONS["repl"]
    return UpdateRequired.ACTIONS["script"]


def _update_log() -> str:
    try:
        return str(_paths.update_log_path())
    except SandboxViolation:
        return "update.log"


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "none"


def _failed(motivo: str, **details: Any) -> UpdateFailed:
    return UpdateFailed.make(motivo=motivo, log=_update_log(), details=details or None)


# ------------------------------------------------------------------------------------------- modo de instalação (H.2)

def _matching_distribution(here: Path, search_path: list[str] | None) -> Any:
    target = os.path.normcase(str((here / "__init__.py").resolve()))
    kwargs: dict[str, Any] = {"name": "rumbapy"}
    if search_path is not None:
        kwargs["path"] = [str(p) for p in search_path]
    for dist in metadata.distributions(**kwargs):
        try:
            located = os.path.normcase(str(Path(str(dist.locate_file("rumbapy/__init__.py"))).resolve()))
        except Exception:                 # distribuição quebrada/estranha: só não é a nossa
            continue
        if located == target:
            return dist
    return None


def _direct_url(dist: Any) -> dict[str, Any] | None:
    try:
        raw = dist.read_text("direct_url.json")
        direct = json.loads(raw) if raw else None
    except (OSError, ValueError):
        direct = None
    return direct if isinstance(direct, dict) else None


def _file_url_path(url: Any) -> Path | None:
    if not isinstance(url, str) or not url.lower().startswith("file:"):
        return None
    try:
        return Path(url2pathname(urlparse(url).path)).resolve()
    except (OSError, ValueError):
        return None


def _editable_owner(here: Path, search_path: list[str] | None) -> bool:
    """Alguma distribuição ``rumbapy`` editable (``dir_info.editable``) aponta para uma pasta que contém ``here``?

    O ``build_editable`` só grava um ``.pth`` para ``src``: o ``locate_file`` dessa distribuição aponta para o
    site-packages e não casa com ``here`` (H.2 passo 3), mas a instalação é editable (H.2 passo 4).
    """
    kwargs: dict[str, Any] = {"name": "rumbapy"}
    if search_path is not None:
        kwargs["path"] = [str(p) for p in search_path]
    target = os.path.normcase(str(here))
    for dist in metadata.distributions(**kwargs):
        direct = _direct_url(dist)
        dir_info = direct.get("dir_info") if direct is not None else None
        if not (isinstance(dir_info, dict) and dir_info.get("editable")):
            continue
        root = _file_url_path(direct.get("url") if direct is not None else None)
        if root is None:
            continue
        base = os.path.normcase(str(root))
        if target == base or target.startswith(base.rstrip("\\/") + os.sep):
            return True
    return False


def install_mode(*, here: str | os.PathLike[str] | None = None,
                 search_path: list[str] | None = None) -> tuple[str, str | None]:
    """``(modo, commit)`` desta instalação: ``dev`` | ``archive`` | ``vcs`` | ``unknown`` (H.2).

    ``UpdateFailed`` para checkout/editable (variante ``checkout``) e para instalação sem distribuição (``missing``).
    ``here``/``search_path`` existem para os testes (padrão: a pasta deste arquivo e ``sys.path``).
    """
    if os.environ.get("RUMBAPY_DEV") == "1":
        script = sys.argv[0] if sys.argv else ""
        text = f"update: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1) versão={__version__} script={script}"
        logger.warning("%s", text)
        _bootstrap.log_line(text, "WARNING")
        _log.notice("RumbaPy: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)", force=True)   # mesmo com RUMBAPY_QUIET=1
        return ("dev", None)
    pkg = Path(here).resolve() if here is not None else Path(__file__).resolve().parent
    # A distribuição vem ANTES do ``.git``: um venv dentro de um repositório do usuário (``<proj>/.venv/Lib/
    # site-packages/rumbapy`` com ``<proj>/.git``) é uma instalação normal, não um checkout.
    dist = _matching_distribution(pkg, search_path)
    direct = _direct_url(dist) if dist is not None else None
    dir_info = direct.get("dir_info") if isinstance(direct, dict) else None
    editable = isinstance(dir_info, dict) and bool(dir_info.get("editable"))
    if dist is None and _editable_owner(pkg, search_path):
        editable = True                   # editable pelo .pth (fora de um checkout com .git, ex.: a partir do sdist)
    if dist is None or editable:
        for parent in list(pkg.parents)[:4]:
            if (parent / ".git").exists():
                raise UpdateFailed.make("checkout", details={"mode": "checkout"})
    if editable:
        raise UpdateFailed.make("checkout", details={"mode": "editable"})
    if dist is None:
        raise _failed("instalação não reconhecida: nenhuma distribuição 'rumbapy' corresponde a esta pasta",
                      mode="missing")
    if isinstance(direct, dict):
        sha = _bootstrap.archive_sha(direct.get("url"))
        if sha:
            return ("archive", sha)
        commit = _bootstrap.commit_from_direct_url(direct)
        if commit:
            return ("vcs", commit)
    return ("unknown", None)


# ------------------------------------------------------------------------------------------- estado e log

def read_state() -> dict[str, Any] | None:
    """Última checagem registrada (``update-state.json``) ou ``None``."""
    try:
        return _paths.read_json(_paths.update_state_path())
    except SandboxViolation:
        return None


def _write_state(context: str, mode: str, local: str | None, remote: str | None, result: str,
                 reason: str | None = None) -> None:
    state = {"checked_at": time.time(), "context": context, "mode": mode, "local": local, "remote": remote,
             "result": result, "version": __version__}
    if reason:
        state["reason"] = reason
    try:
        _paths.write_json(_paths.update_state_path(), state)
    except (OSError, SandboxViolation) as exc:
        logger.warning("update: não consegui gravar update-state.json (%s)", type(exc).__name__)


def _cache_valid(local: str | None) -> bool:
    """Cache só dispensa a consulta com ``up-to-date``/``updated``, remote == local == instalado e idade < 60 s."""
    state = read_state()
    if not state or local is None or state.get("result") not in VALID_RESULTS:
        return False
    if not (state.get("remote") == state.get("local") == local):
        return False
    checked = state.get("checked_at")
    return isinstance(checked, (int, float)) and 0 <= time.time() - checked < CACHE_TTL


def _record(context: str, mode: str, local: str | None, remote: str | None, result: str,
            pip_s: float | None = None, level: int = logging.INFO, reason: str | None = None) -> None:
    """Linha do ``update.log`` no formato da H.9 (+ o mesmo texto no log do cliente)."""
    text = f"update context={context} mode={mode} local={_short(local)} remote={_short(remote)} result={result}"
    if pip_s is not None:
        text += f" pip_s={pip_s:.1f}"
    if reason:
        text += f" motivo={reason}"
    logger.log(level, "%s", text)
    _bootstrap.log_line(text, logging.getLevelName(level))


# ------------------------------------------------------------------------------------------- algoritmo (H.4)

def _fresh_enough(context: str) -> bool:
    """Já checado neste processo e sem TTL vencido (H.1)?"""
    if _checked_at is None:
        return False
    if context == "open":
        ttl = TTL_INTERACTIVE if _interactive() else TTL_SCRIPT
        return time.monotonic() - _checked_at < ttl
    return True


def _mark_checked() -> None:
    global _checked_at
    _checked_at = time.monotonic()


def _other_process_message(context: str) -> str:
    """Texto da B.7 (variante ``other_process``) com a ação do contexto no lugar do "Rode de novo." genérico."""
    text = UpdateRequired.make("other_process").message
    tail = "Rode de novo."
    if text.endswith(tail):
        text = text[: -len(tail)] + _action(context)
    return text


def _changed_on_disk(context: str, mode: str, local: str | None) -> bool:
    """Registra o commit da 1ª checagem; nas seguintes, ``True`` se o disco mudou desde então (outro processo
    instalou outra versão por cima: o código na memória deste processo ficou velho)."""
    global _seen_local
    if _seen_local is None:
        _seen_local = (mode, local)
        return False
    seen = _seen_local[1]
    if local == seen:
        return False
    # sem consulta ao GitHub (remote=none) e sem tocar no update-state.json: ele descreve o disco, que está certo
    reason = f"outro processo atualizou o RumbaPy (este processo roda {_short(seen)})"
    _record(context, mode, local, None, "restart-required", level=logging.WARNING, reason=reason)
    return True


def _remove_marker(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("update: não consegui apagar %s (%s)", path.name, type(exc).__name__)


def _pip(marker: Path, remote: str) -> int:
    def on_spawn(proc: Any) -> None:          # pip_pid no marcador: ninguém reinstala por cima enquanto ele vive
        _paths.write_json(marker, _bootstrap.marker_record(remote, proc))

    try:
        return _bootstrap.pip_install(remote, on_spawn=on_spawn)
    except _bootstrap.BootstrapError as exc:
        if exc.kind == "sandbox":
            raise SandboxViolation.make(acao=exc.reason) from None
        raise _failed(exc.reason) from None
    except OSError as exc:
        raise _failed(f"não consegui iniciar o pip ({type(exc).__name__})") from None


def _wait_orphan_pip(previous: dict[str, Any] | None) -> None:
    """Um pip de uma atualização anterior ainda roda (dono interrompido com Ctrl+C duplo, ou morto): espera-o.

    Instalar por cima dele seria dois ``pip --force-reinstall`` no mesmo site-packages (E6). Prazo: ``LOCK_TIMEOUT``.
    """
    pip_pid = _bootstrap.pip_alive(previous)
    if pip_pid is None:
        return
    logger.warning("update: o pip de uma atualização anterior (PID %s) ainda está rodando; aguardando", pip_pid)
    _log.notice("RumbaPy: aguardando o fim de uma atualização em andamento…")
    deadline = time.monotonic() + LOCK_TIMEOUT
    while _bootstrap.pip_alive(previous) is not None:
        if time.monotonic() >= deadline:
            raise _failed(f"outra atualização não terminou (pip PID {pip_pid} ainda rodando)")
        _sleep(PIP_POLL)


def _check_marker(marker: Path, remote: str) -> bool:
    """Lê/grava ``update-in-progress.json``; ``True`` se a atualização anterior foi interrompida.

    Quem chama segura a trava de update, então nenhum dono de marcador está no meio da instalação: um marcador
    encontrado aqui é de uma atualização que não terminou (dono morto, Ctrl+C, falha ao apagar) e o resultado do pip
    dela é desconhecido (E6). Se o pip dela ainda vive, espera-o; depois reinstala.

    Erros de disco (antivírus, disco cheio) e marcador corrompido viram ``UpdateFailed`` (nunca escapam crus: o
    ``__init__`` mandaria ao ``heal`` um erro que é da biblioteca).
    """
    try:
        previous = _paths.read_json(marker)
        _wait_orphan_pip(previous)
        interrupted = previous is not None
        if interrupted:
            logger.warning("update: atualização anterior interrompida (PID %s); reinstalando",
                           previous.get("pid") if previous is not None else None)
        _paths.write_json(marker, _bootstrap.marker_record(remote))
    except (OSError, TypeError, ValueError) as exc:
        raise _failed(f"não consegui gravar update-in-progress.json ({type(exc).__name__})") from None
    return interrupted


def _update_lock() -> Any:
    try:
        return file_lock(_paths.UPDATE_LOCK, timeout=LOCK_TIMEOUT)
    except LockTimeout:                       # antes de OSError: LockTimeout é TimeoutError
        raise _failed("outra atualização não terminou") from None
    except OSError as exc:                    # antivírus/disco no locks\update.lock: falha da atualização, não do código
        raise _failed(f"não consegui a trava de atualização ({type(exc).__name__})") from None


def _fail(context: str, mode: str, local: str | None, remote: str, exc: UpdateFailed | SandboxViolation,
          pip_s: float | None) -> None:
    _write_state(context, mode, local, remote, "failed", exc.message)
    _record(context, mode, local, remote, "failed", pip_s, logging.ERROR, exc.message)


def _install(context: str, mode: str, local: str | None, remote: str) -> tuple[float | None, str | None]:
    """Instala ``remote`` sob a trava de update e confere num interpretador novo. Devolve ``(pip_s, versão)``."""
    try:
        lock = _update_lock()
    except (UpdateFailed, SandboxViolation) as exc:
        _fail(context, mode, local, remote, exc, None)
        raise
    with lock:
        marker = _paths.update_in_progress_path()
        pip_s: float | None = None
        owned = False                         # só apaga o marcador que ESTE processo gravou (E6)
        try:
            interrupted = _check_marker(marker, remote)
            owned = True
            current = _bootstrap.fresh_installed()
            if current.get("error"):
                logger.warning("update: a sonda do que está instalado falhou (%s); reinstalando", current["error"])
            if current.get("commit") != remote or interrupted:
                started = time.monotonic()
                rc = _pip(marker, remote)     # Ctrl+C: KeyboardInterrupt sobe e o marcador fica (com o pip_pid)
                pip_s = time.monotonic() - started
                if rc != 0:
                    raise _failed(f"pip código {rc}", rc=rc)
            after = _bootstrap.fresh_installed()
            if after.get("commit") != remote:
                probe = f" (sonda: {after['error']})" if after.get("error") else ""
                raise _failed(f"após instalar, o commit ainda é {_short(after.get('commit'))}{probe}")
        except (UpdateFailed, SandboxViolation) as exc:
            # Falha antes de gravar o próprio marcador (ex.: o pip de uma atualização anterior ainda roda): o marcador
            # em disco é do outro processo e protege aquele pip; apagá-lo liberaria um 2º pip --force-reinstall (E6).
            if owned:
                _remove_marker(marker)
            _fail(context, mode, local, remote, exc, pip_s)
            raise
        _remove_marker(marker)
        _write_state(context, mode, remote, remote, "updated")
    _record(context, mode, local, remote, "updated", pip_s)
    return pip_s, after.get("version")


def ensure_latest(context: str = "import", force: bool = False) -> None:
    """Garante que este processo roda o commit do ``master`` (H.4); levanta ``UpdateRequired`` depois de instalar.

    ``context``: ``import`` | ``open`` | ``cli`` | ``legacy``. ``force`` ignora a checagem anterior e o cache.
    No papel ``cli`` a checagem do ``import`` é feita pelo ``__main__`` (para responder sem traceback).
    """
    global _restart_required, _restart_message, _restart_version
    if context not in CONTEXTS:
        raise ValueError(f"contexto de update desconhecido: {context!r}")
    if _is_legacy_shim():
        return
    if context == "import" and _is_cli():
        return                            # __main__ chama ensure_latest(context="cli") antes de qualquer comando
    with _guard:
        if _restart_required is not None:
            raise UpdateRequired(_restart_message or "")
        if not force and _fresh_enough(context):
            return
        mode, local = install_mode()
        if mode == "dev":
            _mark_checked()
            return
        if _changed_on_disk(context, mode, local):
            _restart_required = local or ""
            _restart_message = _other_process_message(context)
            _restart_version = None
            raise UpdateRequired(_restart_message)
        if not force and _cache_valid(local):
            _mark_checked()
            return
        try:
            remote = _bootstrap.remote_master_sha()
        except _bootstrap.BootstrapError as exc:
            if exc.kind == "sandbox":
                raise SandboxViolation.make(acao=exc.reason) from None
            _write_state(context, mode, local, None, "failed", exc.reason)
            _record(context, mode, local, None, "failed", level=logging.WARNING, reason=exc.reason)
            raise UpdateCheckFailed.make(motivo=exc.reason, details={"kind": exc.kind}) from None
        if remote == local:
            _write_state(context, mode, local, remote, "up-to-date")
            _record(context, mode, local, remote, "up-to-date")
            _mark_checked()
            return
        if mode == "unknown":
            _log.notice("RumbaPy: instalação sem commit conhecido; reinstalando a versão publicada")
        _pip_s, new_version = _install(context, mode, local, remote)
        error = UpdateRequired.make(versao=new_version or "?", sha7=remote[:7], acao=_action(context))
        _restart_required = remote
        _restart_message = error.message
        _restart_version = new_version
        raise error


# ------------------------------------------------------------------------------------------- consultas (CLI)

def restart_info() -> tuple[str | None, str | None]:
    """``(sha, versão)`` instalados por este processo; se outro processo atualizou o disco, ``(sha do disco, None)``.

    ``(None, None)`` se nada mudou.
    """
    return _restart_required, _restart_version


def status() -> dict[str, Any]:
    """Para ``python -m rumbapy version``: versão, modo, commit e a última checagem registrada."""
    info: dict[str, Any] = {"version": __version__, "mode": None, "commit": None, "error": None}
    try:
        info["mode"], info["commit"] = install_mode()
    except UpdateFailed as exc:
        info["mode"] = (exc.details or {}).get("mode", "error")
        info["error"] = exc.message
    state = read_state()
    info["last_check"] = state
    info["last_check_other"] = checked_by_other_install(state, info["mode"], info["commit"])
    return info


def checked_by_other_install(state: dict[str, Any] | None, mode: str | None, commit: str | None) -> bool:
    """O ``update-state.json`` (um por usuário, não por instalação) foi gravado por OUTRA instalação?

    Só para a exibição (``version``/``doctor``): o cache do update já confere ``local`` (``_cache_valid``). É desta
    instalação se o ``local`` registrado é o commit dela, ou se ela é o resultado daquela checagem (``updated`` com
    ``remote`` == commit); sem commit conhecido, se o registro também não tem commit e o modo bate.
    """
    if not isinstance(state, dict):
        return False
    if commit:
        return not (state.get("local") == commit
                    or (state.get("result") == "updated" and state.get("remote") == commit))
    return not (state.get("local") is None and state.get("mode") == mode)
