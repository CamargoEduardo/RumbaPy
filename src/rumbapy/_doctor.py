"""``rumbapy.doctor()``: diagnóstico do ambiente em português (B.2, H.9).

Lê arquivos (registros dos workers, estado do update, logs), o registro do Windows (onde está o Web Launcher), a
tabela TCP (servidores 0.1.5 escutando) e faz o ``ping`` dos workers que já estão vivos (conexão admin; nunca sobe
worker, nunca conecta ao terminal, nunca carrega a DLL). A descoberta do Python 32-bit (``_runtime32.find``) pode rodar
``py -0p`` e sondar candidatos (``-I -S -c`` sem importar nada do pacote nem a DLL) e, como em qualquer uso, grava o
cache ``runtime.json`` — é a única escrita. Também consulta o ``info/refs`` do GitHub (alcance da rede; recusado no
sandbox sem ``RUMBAPY_TEST_GITHUB``).

Nada de segredo vai ao relatório: nem chave, nem senha, nem texto de tela; hostnames/URLs/IPs passam por
``_instances.sanitize`` (C22) e os valores de variáveis ``RUMBAPY_*`` só aparecem para uma lista fechada de chaves.
"""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import dataclass, field, replace
from collections.abc import Callable, Iterable
from typing import Any, Literal

from . import _paths, _protocol, _version

import platform
import logging
import struct
import sys
import os
import re

logger = logging.getLogger(__name__)

Status = Literal["ok", "aviso", "falha", "info"]
LETTERS = ("D", "Z", "A")
LEGACY_PORTS = {"D": 8500, "A": 8600, "Z": 8700, "outras": 8800}     # == _legacy.PORTS/OTHER_PORT (E.8)
LAUNCHER_DEFAULT = r"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe"
LAUNCHER_KEY = r"hawlauncher\shell\open\command"
ENV_SHOWN = ("RUMBAPY_SANDBOX", "RUMBAPY_DEV", "RUMBAPY_LIVE", "RUMBAPY_GUARD", "RUMBAPY_QUIET", "RUMBAPY_LOG_LEVEL",
             "RUMBAPY_LAUNCH", "RUMBAPY_OPEN_TIMEOUT")
_MARK = {"ok": "[ok]   ", "aviso": "[aviso]", "falha": "[FALHA]", "info": "[info] "}

# cópia das regras de _instances.sanitize (usada se o módulo não puder ser importado)
_ADDRESS_RE = re.compile(r"(Address:\s*)\S+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_FQDN_RE = re.compile(r"\b[\w-]+(?:\.[\w-]+){2,}\b")


def sanitize(text: Any) -> str:
    """Texto de fora (mensagens de erro, registro do Windows, nomes de script): sem hostnames, URLs nem IPs."""
    text = text if isinstance(text, str) else str(text)
    try:
        from ._instances import sanitize as _sanitize   # tardio: P-C (Onda 2)

        return _sanitize(text)
    except ImportError:
        text = _ADDRESS_RE.sub(r"\1<host>", text)
        text = _URL_RE.sub("<url>", text)
        text = _IPV4_RE.sub("<host>", text)
        return _FQDN_RE.sub("<host>", text)


def _scrub(text: str) -> str:
    """Passada final no relatório inteiro: URLs, IPv4 e ``Address:`` (versões ``3.13.2`` ficam intactas)."""
    text = _ADDRESS_RE.sub(r"\1<host>", text)
    text = _URL_RE.sub("<url>", text)
    return _IPV4_RE.sub("<host>", text)


@dataclass
class Check:
    """Um item do relatório; ``extra`` só aparece com ``verbose=True``."""

    name: str
    status: Status
    detail: str
    extra: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    """Resultado de ``doctor()``. ``ok`` = nenhum item com falha. ``str(report)`` = o texto impresso."""

    checks: list[Check]
    verbose: bool = False

    @property
    def ok(self) -> bool:
        return all(c.status != "falha" for c in self.checks)

    def __getitem__(self, name: str) -> Check:
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)

    def to_text(self, *, verbose: bool | None = None) -> str:
        verbose = self.verbose if verbose is None else verbose
        lines = [f"RumbaPy {_version.__version__} — diagnóstico"]
        for check in self.checks:
            lines.append(f"{_MARK[check.status]} {check.name}: {check.detail}")
            if verbose:
                lines.extend(f"          {line}" for line in check.extra)
        lines.append("Tudo certo." if self.ok else "Há itens com FALHA: veja acima como resolver.")
        return _scrub("\n".join(lines))

    def __str__(self) -> str:
        return self.to_text()

    def __repr__(self) -> str:
        bad = sum(1 for c in self.checks if c.status == "falha")
        return f"<DoctorReport {'ok' if self.ok else f'{bad} falha(s)'} · {len(self.checks)} itens>"

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [{"name": c.name, "status": c.status, "detail": _scrub(c.detail),
                                           "extra": [_scrub(e) for e in c.extra]} for c in self.checks]}


# ------------------------------------------------------------------------------------------- fontes (só leitura)

def _launcher_from_registry() -> str | None:
    """1º token de ``HKCR\\hawlauncher\\shell\\open\\command`` (só leitura) ou ``None``."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, LAUNCHER_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "")
    except (ImportError, OSError):
        return None
    value = str(value or "").strip()
    if value.startswith('"'):
        return value[1:].split('"', 1)[0] or None
    return value.split(" ", 1)[0] or None


def _listener_pid(port: int) -> int | None:
    from . import _win32

    return _win32.tcp_listener_pid(port)


def _remote_sha() -> str:
    from . import _bootstrap

    return _bootstrap.remote_master_sha()


def _update_status() -> dict[str, Any]:
    from . import _update

    if os.environ.get("RUMBAPY_DEV") == "1":
        # H.2: o aviso "AUTO-UPDATE DESLIGADO" sai uma vez por processo (no import ou no ``ensure_latest("cli")``);
        # ``_update.status()`` chamaria ``install_mode()`` de novo e repetiria o aviso e o WARNING no update.log.
        return {"version": _version.__version__, "mode": "dev", "commit": None, "error": None,
                "last_check": _update.read_state()}
    return _update.status()


def _once(fn: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
    """Memoiza ``fn`` (inclusive a exceção) para um único relatório: o estado do update é lido uma vez só."""
    cache: list[Any] = []

    def wrapper() -> dict[str, Any]:
        if not cache:
            try:
                cache.append((True, fn()))
            except Exception as exc:
                cache.append((False, exc))
        ok, value = cache[0]
        if ok:
            return value
        raise value

    return wrapper


def _find_runtime() -> Any:
    from . import _runtime32

    return _runtime32.find()


def _probe_worker(letter: str) -> dict[str, Any]:
    from . import _bridge

    return _bridge.default_bridge().probe(letter)


def _build_id() -> str:
    from . import _bridge

    return _bridge.build_id()


@dataclass
class Probes:
    """Fontes do diagnóstico (injetáveis nos testes)."""

    dll_path: str = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
    launcher_default: str = LAUNCHER_DEFAULT
    launcher_from_registry: Callable[[], str | None] = _launcher_from_registry
    is_file: Callable[[str], bool] = os.path.isfile
    remote_sha: Callable[[], str] = _remote_sha
    update_status: Callable[[], dict[str, Any]] = _update_status
    find_runtime: Callable[[], Any] = _find_runtime
    probe_worker: Callable[[str], dict[str, Any]] = _probe_worker
    listener_pid: Callable[[int], int | None] = _listener_pid
    build_id: Callable[[], str] = _build_id
    letters: Iterable[str] = LETTERS
    environ: Any = None

    def env(self) -> Any:
        return self.environ if self.environ is not None else os.environ


def _short(sha: Any) -> str:
    return sha[:7] if isinstance(sha, str) and sha else "?"


def _guard(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as exc:                      # um item quebrado nunca derruba o relatório
        logger.warning("doctor: item %r falhou (%s)", name, type(exc).__name__)
        return Check(name, "aviso", f"não consegui verificar ({type(exc).__name__})")


# ------------------------------------------------------------------------------------------- itens

def _check_system() -> Check:
    bits = struct.calcsize("P") * 8
    detail = f"{platform.system()} {platform.release()}, Python {platform.python_version()} {bits}-bit"
    extra = [f"executável: {sys.executable}"]
    if sys.platform != "win32":
        return Check("Sistema", "falha", detail + " — o RumbaPy só funciona no Windows.", extra)
    return Check("Sistema", "ok", detail, extra)


def _check_package(p: Probes) -> Check:
    status = p.update_status()
    mode, commit = status.get("mode"), status.get("commit")
    detail = f"versão {_version.__version__}, instalação {mode or '?'}, commit {_short(commit)}"
    extra = [f"build_id: {p.build_id()}", f"protocolo: {_protocol.PROTOCOL}"]
    last = status.get("last_check")
    if isinstance(last, dict):
        extra.append(f"última checagem: {last.get('at', '?')} resultado={last.get('result', '?')} "
                     f"remoto={_short(last.get('remote'))}")
    if status.get("error"):
        return Check("RumbaPy", "aviso", detail + f" — {sanitize(status['error'])}", extra)
    return Check("RumbaPy", "ok", detail, extra)


def _check_github(p: Probes) -> Check:
    env = p.env()
    if env.get("RUMBAPY_SANDBOX") == "1" and not env.get("RUMBAPY_TEST_GITHUB"):
        return Check("GitHub", "info", "não consultado (modo sandbox dos testes)")
    try:
        remote = p.remote_sha()
    except Exception as exc:
        reason = sanitize(getattr(exc, "reason", None) or type(exc).__name__)
        return Check("GitHub", "aviso", f"não consegui consultar a versão publicada ({reason}); sem rede/VPN o "
                                        "auto-update não roda")
    try:
        commit = p.update_status().get("commit")
    except Exception:
        commit = None
    if commit and commit != remote:
        return Check("GitHub", "aviso", f"há versão nova publicada ({_short(remote)}; instalada {_short(commit)}): "
                                        "rode o script de novo para atualizar")
    return Check("GitHub", "ok", f"alcançável; versão publicada {_short(remote)}")


def _check_dll(p: Probes) -> Check:
    name = "RUMBA (DLL HLLAPI)"
    if p.is_file(p.dll_path):                     # só confere o arquivo: a DLL nunca é carregada aqui
        return Check(name, "ok", "ehlapi32.Dll instalada", [f"caminho: {p.dll_path}"])
    return Check(name, "falha", f"não encontrei a ehlapi32.Dll em {p.dll_path}; instale o RUMBA pelo portal da empresa")


def _check_launcher(p: Probes) -> Check:
    name = "Host Access Web Launcher"
    found = p.launcher_from_registry()
    source = "registro do Windows"
    if not found:
        found, source = p.launcher_default, "caminho padrão"
    if p.is_file(found):
        return Check(name, "ok", f"instalado ({source})", [f"caminho: {sanitize(found)}"])
    return Check(name, "aviso", "não encontrado; rumbapy.open usará o Internet Explorer (se existir)",
                 [f"procurado em: {sanitize(found)}"])


def _check_python32(p: Probes) -> Check:
    try:
        rt = p.find_runtime()
    except Exception as exc:
        message = getattr(exc, "message", None) or type(exc).__name__
        hint = getattr(exc, "hint", None)
        text = sanitize(message) + (f" Como resolver: {sanitize(hint)}" if hint else "")
        return Check("Python 32-bit", "falha", text)
    version = ".".join(str(v) for v in rt.version)
    return Check("Python 32-bit", "ok", f"Python {version} 32-bit", [f"caminho: {rt.path}", f"real: {rt.real}"])


def _worker_line(letter: str, info: dict[str, Any]) -> tuple[str, bool, bool]:
    """``(linha, vivo, sem_resposta)``."""
    if not info.get("registered"):
        return f"{letter}: nenhum", False, False
    if not info.get("alive"):
        reason = sanitize(info.get("exit_reason") or "-")
        return f"{letter}: registro antigo (o PID {info.get('pid')} não existe mais; saída: {reason})", False, False
    owner = (f"dono PID {info.get('owner_pid')} ({sanitize(info.get('owner_script') or '?')})"
             if info.get("owner_pid") else "sem dono")
    line = (f"{letter}: PID {info.get('pid')}, {info.get('state')}, {owner}, "
            f"{'conectado' if info.get('connected') else 'desconectado'}, versão {info.get('version')}")
    ping = info.get("ping")
    if isinstance(ping, dict):
        return line + f", ping ok (ativo há {float(ping.get('uptime_s') or 0):.0f} s)", True, False
    return line + f", sem resposta ({sanitize(info.get('error') or '?')})", True, True


def _check_workers(p: Probes) -> Check:
    lines: list[str] = []
    alive = problems = 0
    for letter in p.letters:
        line, is_alive, silent = _worker_line(letter, p.probe_worker(letter))
        lines.append(line)
        alive += is_alive
        problems += silent
    detail = f"{alive} vivo(s)" + (f", {problems} sem resposta" if problems else "") + "; " + "; ".join(lines)
    return Check("Processos auxiliares", "aviso" if problems else "ok", detail, lines)


def _check_legacy(p: Probes) -> Check:
    found = []
    for name, port in LEGACY_PORTS.items():
        pid = p.listener_pid(port)
        if pid is not None:
            found.append(f"porta {port} ({name}): PID {pid}")
    if found:
        return Check("Servidores v0.1.5", "aviso", "; ".join(found) + " — scripts antigos ainda abertos? Eles são "
                                                                      "neutralizados no próximo open/attach", found)
    return Check("Servidores v0.1.5", "ok", "nenhum escutando")


def _check_paths(p: Probes) -> Check:
    env = p.env()
    shown = [f"{k}={env[k]}" for k in ENV_SHOWN if env.get(k)]
    others = sorted(k for k in env if k.upper().startswith("RUMBAPY_") and k not in ENV_SHOWN and k != "RUMBAPY_HOME")
    extra = [f"dados: {_paths.home()}"]
    if shown:
        extra.append("variáveis: " + ", ".join(shown))
    if others:                                    # só os nomes: valores podem ser caminhos/URLs internos
        extra.append("outras variáveis definidas: " + ", ".join(others))
    return Check("Logs", "info", str(_paths.logs_dir()), extra)


def collect(*, verbose: bool = False, probes: Probes | None = None) -> DoctorReport:
    """Monta o relatório sem imprimir (usado por ``doctor`` e pelos testes)."""
    p = probes or Probes()
    p = replace(p, update_status=_once(p.update_status))  # o estado do update é lido uma vez por relatório
    checks = [
        _guard("Sistema", _check_system),
        _guard("RumbaPy", lambda: _check_package(p)),
        _guard("GitHub", lambda: _check_github(p)),
        _guard("RUMBA (DLL HLLAPI)", lambda: _check_dll(p)),
        _guard("Host Access Web Launcher", lambda: _check_launcher(p)),
        _guard("Python 32-bit", lambda: _check_python32(p)),
        _guard("Processos auxiliares", lambda: _check_workers(p)),
        _guard("Servidores v0.1.5", lambda: _check_legacy(p)),
        _guard("Logs", lambda: _check_paths(p)),
    ]
    return DoctorReport(checks, verbose=verbose)


def doctor(*, verbose: bool = False) -> DoctorReport:
    """Imprime o diagnóstico do ambiente (RUMBA, Python 32-bit, update, processos auxiliares) e o devolve.

    Não toca o terminal: só lê arquivos, o registro do Windows e o estado dos processos auxiliares já vivos.
    ``report.ok`` é ``False`` se algum item falhou; ``print(report)`` repete o texto.
    """
    report = collect(verbose=verbose)
    logger.info("doctor: ok=%s %s", report.ok, " ".join(f"{c.name}={c.status}" for c in report.checks))
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(report.to_text() + "\n")
            stream.flush()
        except (OSError, ValueError, UnicodeEncodeError):
            pass
    return report

