"""Descoberta e validação do Python 32-bit que roda o processo auxiliar (I; reduzido na v2.0, I26).

O RUMBA só tem a ``ehlapi32.Dll`` em 32-bit: o worker (``_worker``) precisa de um Python 32-bit >= 3.12 "final".
Este módulo só PROCURA e VALIDA; nunca instala nada nem baixa o *embeddable* (provisionamento fica para a v2.1, I.3).

Runtime válido (I.1): a sonda ``[exe, "-I", "-S", "-c", PROBE]`` (prazo 10 s) importa os módulos que o worker usa e
imprime ``{"bits", "version", "level", "real"}``; vale se ``bits == 32``, ``version >= (3, 12)``, ``level == "final"`` e
código 0. ``real`` é o executável de verdade (venv/redirecionador, E14): é ele que o ``Popen`` do worker usa.

Ordem de busca (I.2; para no primeiro válido): argumento -> ``RUMBAPY_PYTHON32`` -> o próprio processo (se for 32-bit)
-> ``runtime.json`` (revalidado por tamanho+mtime) -> ``py -0p`` -> globs das pastas padrão. Argumento ou variável
inválidos levantam ``Python32NotFound`` com o motivo, sem cair para as fontes seguintes.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple
from pathlib import Path

from .errors import Python32NotFound
from . import _locks, _paths

import subprocess
import threading
import logging
import struct
import json
import glob
import sys
import os
import re

logger = logging.getLogger(__name__)

MIN_VERSION = (3, 12)
PROBE_TIMEOUT = 10.0
PY_LAUNCHER_TIMEOUT = 10.0
REAP_TIMEOUT = 1.0                    # depois do kill da sonda: nunca espera sem prazo
CACHE_LOCK_S = 2.0
CREATE_NO_WINDOW = 0x08000000
PROBE_MODULES = ("ctypes", "_ctypes", "_overlapped", "_winapi", "_hashlib", "hmac", "json", "queue", "threading")
PROBE = ("import " + ",".join(PROBE_MODULES) + ",os,sys,struct; "
         "print(json.dumps({'bits': struct.calcsize('P') * 8, 'version': list(sys.version_info[:3]), "
         "'level': sys.version_info.releaselevel, "
         "'real': os.path.realpath(getattr(sys, '_base_executable', sys.executable))}))")
ENV_VAR = "RUMBAPY_PYTHON32"

Runner = Callable[[list[str], float], tuple[int | None, str]]


class Runtime32(NamedTuple):
    """Python 32-bit válido: ``path`` (como foi achado), ``real`` (o executável de verdade) e ``version``."""

    path: str
    real: str
    version: tuple[int, int, int]


class _Invalid(Exception):
    """Candidato recusado; ``reason`` vai para o ``Motivo:`` do ``Python32NotFound``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------------------------------- execução

def run_capture(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """Roda ``argv`` sem janela e devolve ``(código, stdout)``; ``(None, "")`` se estourar o prazo.

    Só é usado com Pythons (sonda) e com o ``py.exe`` (listagem); nada aqui carrega a DLL do RUMBA.
    """
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW, close_fds=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()                          # é só a sonda (um Python sem a DLL): pode ser encerrada
        try:
            # prazo também aqui: num redirecionador de venv o neto herda o stdout e pode seguir vivo (I.1)
            proc.communicate(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning("sonda do Python 32-bit: a saída não fechou depois do prazo; abandonando a leitura")
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        return None, ""
    return proc.returncode, out.decode("utf-8", "replace")


# ------------------------------------------------------------------------------------------- py -0p e globs

_PY_LINE = re.compile(r"^\s*-(?:V:)?(?P<tag>\S+?)(?:\s+\*)?\s+(?P<path>[A-Za-z]:\\.*?\.exe)(?:\s+\*)?\s*$",
                      re.IGNORECASE)
_VERSION_IN = re.compile(r"(\d+)\.?(\d+)")


def _version_key(text: str) -> tuple[int, int]:
    """``3.13-32`` / ``Python313-32`` / ``pythoncore-3.13-32`` -> ``(3, 13)`` (desconhecido -> ``(0, 0)``)."""
    m = _VERSION_IN.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def parse_py_list(output: str) -> list[str]:
    """Caminhos 32-bit da saída de ``py -0p``, da maior versão para a menor.

    Formatos aceitos (testes com saídas gravadas): o lançador clássico (`` -V:3.13-32  C:\\...\\python.exe``, e o
    antigo `` -3.8-32  C:\\...\\python.exe *``) e o Python Install Manager (`` -V:3.13-32 *  C:\\...\\python.exe``,
    ``-V:PythonCore/3.13-32``). Entradas 64-bit/ARM64 são ignoradas (a sonda decide o resto).
    """
    found: list[tuple[tuple[int, int], int, str]] = []
    for index, line in enumerate(output.splitlines()):
        m = _PY_LINE.match(line)
        if not m:
            continue
        tag, path = m.group("tag"), m.group("path").strip()
        tag_l, path_l = tag.lower(), path.lower()
        is32 = "-32" in tag_l or "-32\\" in path_l or "(x86)" in path_l
        if not is32 or "64" in tag_l.replace("-32", ""):
            continue
        found.append((_version_key(tag.rsplit("/", 1)[-1]), -index, path))
    found.sort(reverse=True)
    out: list[str] = []
    for _key, _index, path in found:
        if path.lower() not in (p.lower() for p in out):
            out.append(path)
    return out


def default_globs(env: Mapping[str, str]) -> list[str]:
    """Pastas padrão do instalador 32-bit (I.2 #6), na ordem da especificação."""
    local = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    x86 = env.get("ProgramFiles(x86)") or env.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    return [os.path.join(local, "Programs", "Python", "Python3*-32", "python.exe"),
            os.path.join(local, "Python", "pythoncore-3.*-32", "python.exe"),
            os.path.join(x86, "Python3*", "python.exe")]


def expand_glob(pattern: str, globber: Callable[[str], Iterable[str]] = glob.glob) -> list[str]:
    """Um glob, com a maior versão primeiro (pelo nome da pasta)."""
    paths = list(globber(pattern))
    return sorted(paths, key=lambda p: _version_key(os.path.basename(os.path.dirname(p))), reverse=True)


def _parse_probe(out: str) -> dict[str, Any] | None:
    for line in reversed(out.strip().splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _check(exe: str, info: Mapping[str, Any]) -> Runtime32:
    bits, version, level, real = info.get("bits"), info.get("version"), info.get("level"), info.get("real")
    if not (isinstance(version, list) and len(version) == 3 and all(isinstance(v, int) for v in version)):
        raise _Invalid(f"{exe} devolveu uma versão inválida")
    shown = ".".join(str(v) for v in version)
    if bits != 32:
        raise _Invalid(f"{exe} é um Python {bits}-bit (precisa ser 32-bit)")
    if tuple(version[:2]) < MIN_VERSION:
        raise _Invalid(f"{exe} é o Python {shown} (precisa ser >= {MIN_VERSION[0]}.{MIN_VERSION[1]})")
    if level != "final":
        raise _Invalid(f"{exe} é uma versão de pré-lançamento ({shown} {level})")
    if not isinstance(real, str) or not real:
        raise _Invalid(f"{exe} não informou o executável real")
    return Runtime32(exe, real, (version[0], version[1], version[2]))


# ------------------------------------------------------------------------------------------- busca

class Finder:
    """Busca da I.2 com dependências injetáveis (testes: ``run``, ``env``, ``globber``, ``is_32``...)."""

    def __init__(self, *, run: Runner | None = None, env: Mapping[str, str] | None = None,
                 globber: Callable[[str], Iterable[str]] | None = None, is_32: bool | None = None,
                 executable: str | None = None, cache_path: Path | None = None,
                 globs: list[str] | None = None) -> None:
        self.run = run or run_capture
        self.env = env if env is not None else os.environ
        self.globber = globber or glob.glob
        self.is_32 = (struct.calcsize("P") == 4) if is_32 is None else is_32
        self.executable = executable if executable is not None else sys.executable
        self._cache_path = cache_path
        self._globs = globs
        self._memo: dict[str, Runtime32] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- validação (I.1)
    def probe(self, exe: str) -> Runtime32:
        """Roda a sonda em ``exe``; ``_Invalid`` com o motivo se não servir."""
        if not os.path.isfile(exe):
            raise _Invalid(f"{exe} não existe")
        try:
            code, out = self.run([exe, "-I", "-S", "-c", PROBE], PROBE_TIMEOUT)
        except OSError as exc:
            raise _Invalid(f"não consegui executar {exe} ({type(exc).__name__})") from None
        if code is None:
            raise _Invalid(f"{exe} não respondeu à sonda em {PROBE_TIMEOUT:.0f} s")
        if code != 0:
            raise _Invalid(f"{exe} falhou na sonda (código {code}; falta algum módulo da biblioteca padrão?)")
        info = _parse_probe(out)
        if info is None:
            raise _Invalid(f"{exe} devolveu uma resposta inválida à sonda")
        return _check(exe, info)

    def _self_runtime(self) -> Runtime32:
        """O próprio processo é 32-bit: mesma checagem da sonda, sem subprocesso."""
        missing = []
        for name in PROBE_MODULES:
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        if missing:
            raise _Invalid(f"o Python atual não tem {', '.join(missing)}")
        info = {"bits": struct.calcsize("P") * 8, "version": list(sys.version_info[:3]),
                "level": sys.version_info.releaselevel,
                "real": os.path.realpath(getattr(sys, "_base_executable", sys.executable))}
        return _check(self.executable, info)

    # ----------------------------------------------------------------- cache em disco (runtime.json)
    def cache_path(self) -> Path:
        return self._cache_path if self._cache_path is not None else _paths.runtime_cache_path()

    def _from_cache(self) -> Runtime32 | None:
        data = _paths.read_json(self.cache_path())
        if not data:
            return None
        path, real, version = data.get("path"), data.get("real"), data.get("version")
        if not (isinstance(path, str) and isinstance(real, str) and isinstance(version, list) and len(version) == 3
                and all(isinstance(v, int) for v in version)):
            return None
        try:
            st = os.stat(path)
            real_ok = os.path.isfile(real)
        except OSError:
            return None
        if real_ok and st.st_size == data.get("size") and int(st.st_mtime) == data.get("mtime"):
            return Runtime32(path, real, (version[0], version[1], version[2]))
        try:                                     # mudou (atualização do Python?): sonda de novo
            return self._remember(self.probe(path))
        except _Invalid as exc:
            logger.info("runtime32: o cache não vale mais (%s)", exc.reason)
            return None

    def _save(self, rt: Runtime32) -> None:
        try:
            st = os.stat(rt.path)
            data = {"path": rt.path, "real": rt.real, "size": st.st_size, "mtime": int(st.st_mtime),
                    "version": list(rt.version)}
            try:
                lock = _locks.file_lock(_paths.PY32_LOCK, CACHE_LOCK_S)
            except _locks.LockTimeout:
                logger.info("runtime32: runtime.json ocupado por outro processo; não gravei o cache")
                return
            try:
                _paths.write_json(self.cache_path(), data)
            finally:
                lock.release()
        except Exception as exc:                 # o cache é só otimização
            logger.warning("runtime32: não consegui gravar runtime.json (%s)", type(exc).__name__)

    def _remember(self, rt: Runtime32) -> Runtime32:
        self._save(rt)
        return rt

    # ----------------------------------------------------------------- busca (I.2)
    def _explicit(self, value: str | os.PathLike[str], source: str) -> Runtime32:
        path = os.path.abspath(os.path.expanduser(os.fspath(value)))
        if os.path.isdir(path):
            path = os.path.join(path, "python.exe")
        try:
            return self.probe(path)
        except _Invalid as exc:
            raise Python32NotFound.make(motivo=f"{source}: {exc.reason}") from None

    def candidates(self) -> list[str]:
        """``py -0p`` (5) e depois os globs (6), sem repetir caminhos."""
        out: list[str] = []
        try:
            code, text = self.run(["py", "-0p"], PY_LAUNCHER_TIMEOUT)
        except OSError:
            code, text = None, ""
        if code == 0:
            out.extend(parse_py_list(text))
        for pattern in self._globs if self._globs is not None else default_globs(self.env):
            out.extend(expand_glob(pattern, self.globber))
        unique: list[str] = []
        seen: set[str] = set()
        for path in out:
            norm = os.path.normcase(path)
            if norm not in seen:
                seen.add(norm)
                unique.append(path)
        return unique

    def find(self, explicit: str | os.PathLike[str] | None = None) -> Runtime32:
        """Primeiro Python 32-bit válido (I.2). Uma descoberta por processo para cada ``explicit``."""
        key = "" if explicit is None else os.path.normcase(os.path.abspath(os.fspath(explicit)))
        if explicit is None and self.env.get(ENV_VAR):
            key = "env:" + os.path.normcase(str(self.env.get(ENV_VAR)))
        with self._lock:
            hit = self._memo.get(key)
            if hit is not None:
                return hit
            rt = self._find(explicit)
            self._memo[key] = rt
            return rt

    def _find(self, explicit: str | os.PathLike[str] | None) -> Runtime32:
        if explicit is not None:
            return self._remember(self._explicit(explicit, "argumento python32"))
        env_value = self.env.get(ENV_VAR)
        if env_value:
            return self._remember(self._explicit(env_value, ENV_VAR))
        reasons: list[str] = []
        if self.is_32:
            try:
                return self._self_runtime()
            except _Invalid as exc:
                reasons.append(exc.reason)
        cached = self._from_cache()
        if cached is not None:
            return cached
        for path in self.candidates():
            try:
                return self._remember(self.probe(path))
            except _Invalid as exc:
                logger.info("runtime32: %s recusado (%s)", path, exc.reason)
                reasons.append(exc.reason)
        logger.warning("runtime32: nenhum Python 32-bit válido (%d candidato(s) recusado(s))", len(reasons))
        raise Python32NotFound.make("not_found", details={"rejected": reasons} if reasons else None)

    def clear(self) -> None:
        with self._lock:
            self._memo.clear()


_default = Finder()


def find(explicit: str | os.PathLike[str] | None = None) -> Runtime32:
    """Python 32-bit para o worker (I.2); ``Python32NotFound`` se não houver."""
    return _default.find(explicit)


def default_finder() -> Finder:
    return _default
