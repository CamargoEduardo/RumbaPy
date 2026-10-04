"""Lógica comum dos roteiros ao vivo (J.6): decisão de pular, confirmação, credenciais, varredura da senha, relatório.

Este módulo NÃO importa ``rumbapy`` nem toca o RUMBA ao ser importado: é usado pelo pytest (``tests/live``), pelo
executor dos roteiros (``tests/live/runner.py``, sempre num processo novo) e pelas sondas de ``tools/research``.
As únicas funções com efeito no sistema são só leitura: ``visible_window_titles`` (``EnumWindows`` +
``InternalGetWindowText``, que não manda mensagem à janela) e ``open_console`` (``CONIN$``/``CONOUT$``).

Salvaguardas (todas obrigatórias, J.6):

* ``RUMBAPY_LIVE=1``, nunca com ``RUMBAPY_SANDBOX=1``, Python 64-bit, branch ``dev``;
* recusa se já houver janela de BRAZIL_D/BRAZIL_Z (ou IMS_A, no L7) aberta: não mexe na sessão de ninguém;
* guard obrigatório por roteiro (``readonly``/``text``/``login``), repassado ao worker por ``RUMBAPY_GUARD``, com
  ``RUMBAPY_HOME`` temporário por roteiro (processo e worker novos por roteiro). Um ``RUMBAPY_GUARD`` definido pelo
  dono é um TETO: roteiro que precisa de um guard mais permissivo (ex.: L6 ``login`` com ``RUMBAPY_GUARD=readonly``)
  é pulado; valor inválido (inclusive ``none``) pula tudo;
* confirmação digitada ``SIM <letra>`` lida SEMPRE de ``CONIN$`` pelo próprio executor (nenhuma variável de ambiente
  ou argumento confirma no lugar da pessoa); sem console o roteiro é PULADO (E30);
* credenciais (L6/L7/compat) só digitadas no console (senha sem eco, J.6); nunca exibidas;
* L7 (RHELP) sempre pulado salvo ``RUMBAPY_LIVE_L7_AUTORIZADO=1``.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from pathlib import Path

import subprocess
import datetime
import json
import re

REPO = Path(__file__).resolve().parents[2]
REPORT_DIR = REPO / "docs" / "interno" / "referencia"
REQUIRED_BRANCH = "dev"
CONFIRM_WORD = "SIM"
# do mais restritivo ao mais permissivo (os modos de rumbapy._worker.hllapi.POLICIES, sem "none")
GUARD_ORDER = ("readonly", "text", "login")
# nomes das teclas AID de rumbapy.keys.AIDS (conferido em tests/security sem importar rumbapy aqui)
AID_KEY_NAMES = frozenset({"ENTER", "CLEAR", "SYSREQ", "ATTN", "PAGE_UP", "PAGE_DOWN",
                           *(f"PF{n}" for n in range(1, 25)), *(f"PA{n}" for n in range(1, 4))})
# nomes dos perfis (iguais a rumbapy.profiles; conferido em tests/security/test_live_safeguards.py sem importar
# rumbapy aqui)
PROFILE_NAMES = {"D": "BRAZIL_D", "Z": "BRAZIL_Z", "A": "IMS_A"}
SCAN_LIMIT_BYTES = 64 * 1024 * 1024
AUDIT_LIMIT = 2000                             # linhas do registro do guard gravadas no relatório
SKIPPED_HOME_DIRS = ("code",)                  # cópia do código testado (git archive): não tem como conter a senha


@dataclass(frozen=True)
class Script:
    """Um roteiro ao vivo: ``guard`` do worker, letras que abre, seção de credenciais e o que faz."""

    name: str
    label: str
    guard: str
    letters: tuple[str, ...]
    description: str
    credentials: str | None = None             # sistema das credenciais pedidas no console (cics/rhelp)
    opens_terminal: bool = True
    needs_authorization: str | None = None     # variável de ambiente que precisa ser "1"


SCRIPTS: dict[str, Script] = {s.name: s for s in (
    Script("l1", "L1", "readonly", ("D",), "abrir minimizado, 1ª tela, campos, cursor, leitura e fechar"),
    Script("l2", "L2", "readonly", ("D",), "2ª instância aberta à mão; open fecha as duas (diálogo Short Name)"),
    Script("l3", "L3", "readonly", ("D",), "lease entre processos, close com dono vivo (evict), reciclagem"),
    Script("l3b", "L3b", "readonly", ("D",), "conexão HLLAPI que sai por os._exit(3) sem DisconnectPS; ConnectPS "
           "do worker novo (só conexão local)"),
    Script("l4", "L4", "text", ("D",), "escrever e apagar no PS local, sem nenhuma AID"),
    Script("l5", "L5", "readonly", ("D",), "show/minimize com leituras entre eles e o título em cada estado"),
    Script("l6", "L6", "login", ("D",), "logon CICS com o login do dono; âncoras; fechar logado", credentials="cics"),
    Script("l7", "L7", "login", ("A",), "logon IMS (RHELP)", credentials="rhelp",
           needs_authorization="RUMBAPY_LIVE_L7_AUTORIZADO"),
    Script("l8", "L8", "readonly", ("D",), "neutralização de um servidor 0.1.5 real em 32-bit (sem terminal)",
           opens_terminal=False),
    Script("l9", "L9", "readonly", ("Z", "D"), "com BRAZIL_Z aberto, open D; fechar D sem afetar Z"),
    Script("compat", "compat", "login", ("D",), "script da v0.1.5 sem alteração (RumbaClient)", credentials="cics"),
    Script("close", "close", "readonly", ("D",), "sonda do fechamento (E.4): janelas da instância e QuerySessions"),
)}
GUARDS = {name: script.guard for name, script in SCRIPTS.items()}
# roteiros da J.6 que a suíte pytest executa (a sonda "close" fica só em tools/research)
PYTEST_SCRIPTS = ("l1", "l2", "l3", "l3b", "l4", "l5", "l6", "l7", "l8", "l9", "compat")
# AIDs que cada roteiro pode mandar, conferidas nas linhas op=send do log do worker (D.7): login = só ENTER
LOGIN_AIDS = {"l6": 2, "compat": 2}          # menu -> ENTER -> signon -> ENTER (exatamente 2)


# ------------------------------------------------------------------------------------------- decisão de pular

def skip_reason(script: str, env: Mapping[str, str], *, pointer_bits: int, branch: str | None) -> str | None:
    """Motivo para NÃO rodar o roteiro (``None`` = pode seguir para a confirmação). Puro: só olha os argumentos."""
    if env.get("RUMBAPY_LIVE") != "1":
        return "teste ao vivo: exige RUMBAPY_LIVE=1 (J.6)"
    if env.get("RUMBAPY_SANDBOX") == "1":
        return "RUMBAPY_SANDBOX=1 bloqueia o RUMBA real: roteiro ao vivo sempre pulado"
    spec = SCRIPTS.get(script)
    if spec is None:
        return f"roteiro desconhecido: {script!r}"
    if pointer_bits != 64:
        return "os roteiros ao vivo rodam com o Python 64-bit (o worker 32-bit é lançado por eles)"
    if branch != REQUIRED_BRANCH:
        return f"os roteiros ao vivo rodam só na branch {REQUIRED_BRANCH} (atual: {branch or 'desconhecida'})"
    if spec.needs_authorization and env.get(spec.needs_authorization) != "1":
        return (f"{spec.label} exige autorização explícita do dono antes de cada execução "
                f"({spec.needs_authorization}=1)")
    return guard_ceiling_reason(spec, env.get("RUMBAPY_GUARD"))


def guard_ceiling_reason(spec: Script, ceiling: str | None) -> str | None:
    """``RUMBAPY_GUARD`` do dono é um teto: inválido pula tudo; mais restritivo que o do roteiro pula o roteiro."""
    if ceiling is None or not ceiling.strip():
        return None
    value = ceiling.strip().lower()
    if value not in GUARD_ORDER:
        return (f"RUMBAPY_GUARD={ceiling!r} não vale ao vivo (use {', '.join(GUARD_ORDER)}; "
                "'none' nunca): roteiro pulado")
    if GUARD_ORDER.index(value) < GUARD_ORDER.index(spec.guard):
        return (f"{spec.label} precisa do guard {spec.guard}, mais permissivo que RUMBAPY_GUARD={value}: "
                "roteiro pulado")
    return None


def current_branch(repo: Path = REPO) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def git_head(repo: Path = REPO) -> dict[str, Any]:
    """Commit do HEAD e se a árvore tem alterações (vai ao relatório)."""
    info: dict[str, Any] = {}
    for key, argv in (("head", ["rev-parse", "HEAD"]), ("dirty", ["status", "--porcelain", "--untracked-files=no"])):
        try:
            out = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, timeout=20)
            info[key] = out.stdout.strip() if key == "head" else bool(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            info[key] = None
    return info


# ------------------------------------------------------------------------------------------- janelas (só leitura)

def matching_windows(titles: Iterable[str], letters: Iterable[str]) -> list[str]:
    """Títulos de janela dos perfis de ``letters`` (``"BRAZIL_D - Pro Client"``, ``"x - BRAZIL_D - Pro Client"``)."""
    names = [PROFILE_NAMES[letter] for letter in letters]
    found = []
    for title in titles:
        if any(title.startswith(f"{name} - Pro Client") or f" - {name} - Pro Client" in title for name in names):
            found.append(title)
    return found


def visible_window_titles() -> list[str]:
    """Títulos das janelas de nível superior visíveis (``InternalGetWindowText``: não manda ``WM_GETTEXT``)."""
    import ctypes.wintypes as wt
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.InternalGetWindowText.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.InternalGetWindowText(hwnd, buf, 512)
            if buf.value:
                titles.append(buf.value)
        return True

    user32.EnumWindows(callback, 0)
    return titles


def terminal_windows(letters: Iterable[str] = ("D", "Z"),
                     titles: Callable[[], Iterable[str]] = visible_window_titles) -> list[str]:
    return matching_windows(titles(), letters)


def windows_to_check(script: str) -> tuple[str, ...]:
    """Perfis cujas janelas abertas impedem o roteiro (BRAZIL_D/BRAZIL_Z sempre; IMS_A também no L7)."""
    spec = SCRIPTS[script]
    return tuple(dict.fromkeys(("D", "Z", *spec.letters)))


# ------------------------------------------------------------------------------------------- console (CONIN$)

class Console:
    """Console do Windows aberto direto (``CONIN$``/``CONOUT$``): funciona com o pytest capturando stdin/stdout."""

    def __init__(self, conin: Any, conout: Any) -> None:
        self._in, self._out = conin, conout

    def write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()

    def readline(self, prompt: str = "") -> str:
        if prompt:
            self.write(prompt)
        return self._in.readline().rstrip("\r\n")

    def getpass(self, prompt: str) -> str:
        """Lê sem eco (``msvcrt.getwch`` lê o buffer do console, não o ``sys.stdin``)."""
        import msvcrt

        self.write(prompt)
        chars: list[str] = []
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":
                self.write("\n")
                raise KeyboardInterrupt
            if ch == "\x08":
                if chars:
                    chars.pop()
                continue
            if ch in ("\x00", "\xe0"):             # tecla especial: descarta o 2º código
                msvcrt.getwch()
                continue
            chars.append(ch)
        self.write("\n")
        return "".join(chars)

    def close(self) -> None:
        for stream in (self._in, self._out):
            try:
                stream.close()
            except OSError:
                pass


def open_console() -> Console | None:
    """``Console`` se este processo tem console; ``None`` sem console (CI, pythonw, serviço): o roteiro é pulado."""
    try:
        conin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        conout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except OSError:
        conin.close()
        return None
    return Console(conin, conout)


class ConsoleLike(Protocol):
    """O que os roteiros usam do console (o ``Console`` real ou um dublê dos testes)."""

    def readline(self, prompt: str = "") -> str: ...

    def getpass(self, prompt: str) -> str: ...

    def close(self) -> None: ...


ConsoleFactory = Callable[[], "ConsoleLike | None"]


NO_CONSOLE = "sem console para digitar a confirmação SIM <letra> (sem console o roteiro é pulado, E30)"


def confirm_phrase(script: str) -> str:
    """O que a pessoa tem de digitar: ``SIM <letra>`` com a (última) letra que o roteiro abre."""
    return f"{CONFIRM_WORD} {SCRIPTS[script].letters[-1]}"


def accepted(answer: str, letters: Sequence[str]) -> bool:
    """Só ``SIM <letra>`` com uma letra do roteiro (sem diferenciar maiúsculas): obriga a ler qual terminal abre."""
    words = answer.strip().upper().split()
    return len(words) == 2 and words[0] == CONFIRM_WORD and words[1] in letters


def has_console(console_factory: ConsoleFactory = open_console) -> bool:
    """Há console para a confirmação? (o pytest usa isto para pular cedo; quem pergunta é o executor)."""
    console = console_factory()
    if console is None:
        return False
    console.close()
    return True


def confirm(script: str, *, console_factory: ConsoleFactory = open_console) -> tuple[bool, str]:
    """Confirmação digitada em ``CONIN$`` (J.6, E30): ``(True, "console")`` ou ``(False, motivo)``.

    Não existe atalho por variável de ambiente nem por argumento: sem console o roteiro é pulado."""
    spec = SCRIPTS[script]
    console = console_factory()
    if console is None:
        return False, NO_CONSOLE
    phrase = confirm_phrase(script)
    try:
        answer = console.readline(
            f"\nRoteiro ao vivo {spec.label} ({spec.description}; guard {spec.guard}) contra o RUMBA de PRODUÇÃO.\n"
            f"Digite {phrase} para continuar (qualquer outra coisa cancela): ")
    except (OSError, EOFError):
        return False, "não consegui ler a confirmação do console"
    finally:
        console.close()
    if accepted(answer, spec.letters):
        return True, "console"
    return False, f"{spec.label} não confirmado (era preciso digitar {phrase})"


# ------------------------------------------------------------------------------------------- credenciais

@dataclass(frozen=True)
class Credentials:
    """Usuário e senha do dono, digitados no console. ``repr``/``str`` nunca mostram os valores."""

    user: str = field(repr=False)
    password: str = field(repr=False)
    source: str = "console"

    def __repr__(self) -> str:
        return f"<Credentials de {self.source}: ***>"

    __str__ = __repr__


def get_credentials(section: str, *, console_factory: ConsoleFactory = open_console) -> Credentials | None:
    """Digitadas no console (usuário com eco, senha sem eco; J.6 "só por getpass"). ``None`` = sem credenciais (pular).

    Nunca lidas de arquivo, argumento ou variável de ambiente."""
    console = console_factory()
    if console is None:
        return None
    try:
        user = console.readline(f"Usuário [{section}] (vazio para pular): ").strip()
        if not user:
            return None
        password = console.getpass(f"Senha [{section}] (não aparece): ")
    except (OSError, EOFError):
        return None
    finally:
        console.close()
    return Credentials(user, password, "console") if password else None


# ------------------------------------------------------------------------------------------- varredura da senha

def secret_text_variants(secret: str) -> list[str]:
    """A senha como texto e nas formas escapadas em que pode aparecer num log/relatório: ``json.dumps`` (com e sem
    ``ensure_ascii``), ``repr`` (``%r``) e ``ascii``. Senha com ``"``, ``\\``, tabulação ou acento muda de forma
    nesses escapes e uma busca só pelo texto cru não a acharia. Da mais longa para a mais curta (redação)."""
    if not secret:
        raise ValueError("varredura de segredo vazio sempre acharia algo")
    forms = [secret, json.dumps(secret, ensure_ascii=False)[1:-1], json.dumps(secret)[1:-1], repr(secret)[1:-1],
             ascii(secret)[1:-1]]
    try:
        raw = secret.encode("cp1252")                       # o que o worker manipula (texto da DLL em cp1252)
    except UnicodeEncodeError:
        raw = None                                          # nunca "replace": '???' casaria com qualquer log
    if raw is not None:
        forms += [repr(raw)[2:-1], raw.hex(), raw.hex().upper()]
    unique = [f for f in dict.fromkeys(forms) if f]
    return sorted(unique, key=len, reverse=True)


def secret_variants(secret: str) -> list[bytes]:
    """Bytes a procurar nos arquivos: cada forma de ``secret_text_variants`` em UTF-8, cp1252 e UTF-16-LE."""
    variants: list[bytes] = []
    for text in secret_text_variants(secret):
        for encoding in ("utf-8", "cp1252", "utf-16-le"):
            try:
                data = text.encode(encoding)
            except UnicodeEncodeError:
                continue
            if data not in variants:
                variants.append(data)
    return variants


def files_to_scan(home: Path) -> list[Path]:
    """Todos os arquivos de ``home`` (logs, boot, registros), menos a cópia do código testado."""
    out = []
    for path in sorted(home.rglob("*")):
        rel = path.relative_to(home).parts
        if rel and rel[0] in SKIPPED_HOME_DIRS:
            continue
        if path.is_file():
            out.append(path)
    return out


def secret_found(secret: str, texts: Iterable[str] = (), paths: Iterable[Path] = ()) -> bool:
    """A senha aparece em algum texto (repr, exceções, relatório) ou arquivo (logs, boot)? Tem de dar ``False``."""
    forms = secret_text_variants(secret)
    variants = secret_variants(secret)
    for text in texts:
        if any(form in text for form in forms):
            return True
    for path in paths:
        try:
            with open(path, "rb") as fh:
                data = fh.read(SCAN_LIMIT_BYTES)
        except OSError:
            continue
        if any(v in data for v in variants):
            return True
    return False


def secret_locations(secret: str, report: Mapping[str, Any], texts: Sequence[str] = (),
                     paths: Iterable[Path] = (), home: Path | None = None) -> list[str]:
    """ONDE a senha apareceu (chave do relatório, ``texts[i]``, arquivo relativo ao ``home``), nunca o conteúdo.

    O relatório é percorrido como objeto (chaves e folhas texto em qualquer profundidade), nunca serializado antes:
    o ``json.dumps`` escaparia ``"``/``\\`` da senha e a busca não a acharia."""
    found: list[str] = []
    for key, value in report.items():
        if secret_found(secret, [str(key), *report_strings(value)]):
            found.append(f"relatório:{key}")
    found.extend(f"texts[{i}]" for i, text in enumerate(texts) if secret_found(secret, [text]))
    for path in paths:
        if secret_found(secret, (), [path]):
            try:
                found.append(f"arquivo:{path.relative_to(home)}" if home is not None else f"arquivo:{path.name}")
            except ValueError:
                found.append(f"arquivo:{path.name}")
    return found


def report_strings(value: Any) -> list[str]:
    """Todo texto de ``value`` em qualquer profundidade: chaves, folhas ``str`` e ``str()`` de objetos (``Path``...).
    Números, ``bool`` e ``None`` ficam de fora (``redact`` os mantém como estão; a senha não chega a eles)."""
    out: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping):
            for k, v in item.items():
                out.append(str(k))
                stack.append(v)
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
        elif not (isinstance(item, (int, float, bool)) or item is None):
            out.append(str(item))
    return out


def redact(value: Any, secret: str, mask: str = "***") -> Any:
    """Cópia de ``value`` (dict/list/tuple/str) com toda ocorrência de ``secret`` (também nas formas escapadas de
    ``secret_text_variants``) trocada por ``mask``."""
    if not secret:
        raise ValueError("redação de segredo vazio")
    if isinstance(value, str):
        for form in secret_text_variants(secret):
            value = value.replace(form, mask)
        return value
    if isinstance(value, Mapping):
        return {redact(str(k), secret, mask): redact(v, secret, mask) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secret, mask) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value), secret, mask)


# ------------------------------------------------------------------------------------------- relatório

def report_path(script: str, now: datetime.datetime | None = None, directory: Path = REPORT_DIR) -> Path:
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return directory / f"live_v2_{script}_{stamp}.json"


def write_report(report: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


# linhas do log do worker que vão ao relatório: início (guard=...), bloqueios, NORESET, parada, watchdog
AUDIT_MARKERS = ("guard", "BLOQUEADO", "GuardViolation", "NORESET", "stop:", "watchdog", "parada", "evict")
# ops de leitura: muitas (polling das esperas); só contadas, sem linha no relatório
READ_OPS = frozenset({"snapshot", "read", "read_field", "status", "ping", "sessions"})
_OP = re.compile(r"\bop=(\w+)")
_KEYS = re.compile(r"\bkeys=\[([^\]]*)\]")
_RC = re.compile(r"\brc=(-?\d+)")
_MS = re.compile(r"\bms=([\d.]+)")
_PID = re.compile(r"\s(\d+)\s\S+\sL=[A-Z]\s")


_ROTATED = re.compile(r"^(worker-.+\.log)(?:\.(\d+))?$")


def worker_logs(home: Path) -> list[Path]:
    """Logs do worker em ordem cronológica: por arquivo base, os rotacionados do mais antigo (``.5``) ao mais novo
    (``.1``) e depois o atual (``RotatingFileHandler``, 5 MB com DEBUG ligado)."""
    keyed: list[tuple[str, int, Path]] = []
    for path in (home / "logs").glob("worker-*.log*"):
        m = _ROTATED.match(path.name)
        if m is not None and path.is_file():
            keyed.append((m.group(1), -int(m.group(2) or 0), path))
    return [path for _base, _n, path in sorted(keyed)]


def rotated_worker_logs(home: Path) -> list[str]:
    """Nomes dos logs do worker que já rotacionaram (``worker-...log.N``): o roteiro escreveu mais de 5 MB."""
    return [p.name for p in worker_logs(home) if not p.name.endswith(".log")]


def worker_log_lines(home: Path) -> list[str]:
    lines: list[str] = []
    for log in worker_logs(home):
        try:
            lines.extend(log.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return lines


def guard_audit_lines(home: Path, limit: int | None = None) -> list[str]:
    """O registro do que passou pelo guard, tirado dos logs do worker (o ``policy.audit`` vive no processo do worker
    e não atravessa o protocolo): as linhas ``op=`` de toda op que não é leitura (``send`` com ``keys=[...]``,
    ``write``, ``connect`` com ``rc=``, ``set_cursor``, ``evict``, ``shutdown``...) e as de início/guard/NORESET/parada.
    Essas linhas já omitem texto e tamanho de segredo/campo oculto (D.7)."""
    out: list[str] = []
    for line in worker_log_lines(home):
        op = _OP.search(line)
        if op is not None and op.group(1) in READ_OPS and "BLOQUEADO" not in line and "Guard" not in line:
            continue
        if op is not None or any(m in line for m in AUDIT_MARKERS):
            out.append(line)
    return out if limit is None else out[:limit]


def read_op_counts(home: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in worker_log_lines(home):
        op = _OP.search(line)
        if op is not None and op.group(1) in READ_OPS:
            counts[op.group(1)] = counts.get(op.group(1), 0) + 1
    return counts


def sent_aids(audit: Iterable[str]) -> list[str]:
    """AIDs enviadas, na ordem, segundo as linhas ``op=send ... keys=[...]`` do worker."""
    aids: list[str] = []
    for line in audit:
        op = _OP.search(line)
        keys = _KEYS.search(line)
        if op is None or op.group(1) != "send" or keys is None:
            continue
        aids.extend(k.strip() for k in keys.group(1).split(",") if k.strip() in AID_KEY_NAMES)
    return aids


def connect_records(audit: Iterable[str]) -> list[dict[str, Any]]:
    """``op=connect`` do log: PID do worker, rc e ms de cada ``ConnectPS`` (L1, L3b)."""
    out: list[dict[str, Any]] = []
    for line in audit:
        op = _OP.search(line)
        if op is None or op.group(1) != "connect":
            continue
        pid, rc, ms = _PID.search(line), _RC.search(line), _MS.search(line)
        out.append({"pid": int(pid.group(1)) if pid else None, "rc": int(rc.group(1)) if rc else None,
                    "ms": float(ms.group(1)) if ms else None})
    return out


def noreset_rc(audit: Sequence[str]) -> int | str | None:
    """rc do ``SetSessionParamEx("NORESET")``: o worker só registra rc ≠ 0 (WARNING, D.5); sem WARNING e com um
    ``connect`` rc=0, foi 0. ``None`` = não houve conexão."""
    for line in audit:
        if "SetSessionParamEx(NORESET)" in line:
            rc = _RC.search(line)
            return int(rc.group(1)) if rc else "?"
    if any(rec["rc"] == 0 for rec in connect_records(audit)):
        return 0
    return None


# ------------------------------------------------------------------------------------------- aceite do relatório

def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "type" in value and "message" in value


def check_report(script: str, report: Mapping[str, Any]) -> list[str]:
    """Problemas do relatório de um roteiro (vazio = aceito). Só nomes de chave e tipos de exceção nas mensagens."""
    spec = SCRIPTS[script]
    problems: list[str] = []
    if "error" in report:
        problems.append(f"o roteiro terminou com erro ({str(report['error']).split(':', 1)[0]})")
    if report.get("guard") != spec.guard:
        problems.append(f"guard do relatório = {report.get('guard')!r}, esperado {spec.guard!r}")
    if spec.credentials and report.get("password_found_anywhere") is not False:
        problems.append("a varredura da senha não deu False (vazou ou não rodou)")
    if report.get("password_found_in"):
        problems.append("a senha apareceu em: " + ", ".join(map(str, report["password_found_in"])))
    if spec.opens_terminal:
        if report.get("windows_left") != []:
            problems.append("sobrou janela do terminal depois da limpeza")
        if "error" not in report:
            covered = sorted(k for k in report if k.startswith("cleanup_close_"))
            if covered:
                problems.append(f"o roteiro deixou janela aberta e a limpeza fechou ({', '.join(covered)})")
        workers = report.get("workers")
        if isinstance(workers, dict):
            stuck = [letter for letter, info in workers.items() if not (isinstance(info, dict) and info.get("saiu"))]
            if stuck:
                problems.append(f"worker não saiu no fim: {', '.join(stuck)}")
        elif workers != "nenhum vivo":
            problems.append("não consegui conferir o encerramento dos workers")
        audit = report.get("guard_audit") or []
        if report.get("guard_audit_truncated"):
            problems.append(f"o registro do guard passou de {AUDIT_LIMIT} linhas e foi cortado: confira os logs")
        if report.get("worker_logs_rotated"):
            problems.append("o log do worker rotacionou durante o roteiro (" + ", ".join(
                map(str, report["worker_logs_rotated"])) + "): confira que nenhuma linha se perdeu")
        starts = [line for line in audit if " start pid=" in line]
        if not starts or any(f"guard={spec.guard}" not in line for line in starts):
            problems.append(f"algum worker do roteiro não subiu com guard={spec.guard}")
        if any("GuardViolation" in line or "BLOQUEADO" in line for line in audit):
            problems.append("o guard recusou uma chamada: o roteiro tentou algo além do permitido")
        if spec.guard == "readonly":
            writes = [m.group(1) for m in (_OP.search(line) for line in audit) if m and m.group(1) in ("write", "set_cursor")]
            if writes:
                problems.append(f"escrita num roteiro readonly: {', '.join(sorted(set(writes)))}")
        problems.extend(aid_problems(script, sent_aids(audit)))
    for key, ok in EXPECTATIONS.get(script, {}).items():
        if not ok(report.get(key)):
            problems.append(f"{key}: valor inesperado")
    return problems


def aid_problems(script: str, aids: Sequence[str]) -> list[str]:
    """Conferência das AIDs pelo log do worker: readonly/text = nenhuma; login = só ENTER (L6/compat: exatamente 2)."""
    spec = SCRIPTS[script]
    if spec.guard != "login":
        return [f"o worker mandou AID num roteiro {spec.guard}: {', '.join(aids)}"] if aids else []
    problems = []
    others = sorted(set(aids) - {"ENTER"})
    if others:
        problems.append(f"AID além de ENTER num roteiro de login: {', '.join(others)}")
    expected = LOGIN_AIDS.get(script)
    if expected is not None and aids.count("ENTER") != expected:
        problems.append(f"{spec.label} devia mandar exatamente {expected} ENTER (o log mostra {aids.count('ENTER')})")
    return problems


def _helper_ok(v: Any) -> bool:
    return isinstance(v, dict) and v.get("connect_rc") == 0 and v.get("exit") == 3 and "watchdog" not in v


# L4 (J.6): cada caractere de "TESTE çãÉ€" (sem repetir) vai e volta pelo PS local; o texto inteiro só se couber
L4_TEXT = "TESTE çãÉ€"
L4_CHARS = tuple(dict.fromkeys(c for c in L4_TEXT if c != " "))


def _round_trip(expected: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and v.rstrip() == expected


def _l4_text_ok(v: Any) -> bool:
    """O texto inteiro voltou igual, ou não coube no campo (e o roteiro diz o tamanho)."""
    return _round_trip(L4_TEXT)(v) or (isinstance(v, dict) and v.get("skipped") == "não coube no campo")


def _only_z_left(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 1 and matching_windows(v, ("Z",)) == v


EXPECTATIONS: dict[str, dict[str, Callable[[Any], bool]]] = {
    "l1": {"state_after_close": lambda v: v == "closed", "cursor": lambda v: v == [22, 56],
           "geometry": lambda v: v == [24, 80], "windows_after_close": lambda v: v == []},
    "l2": {"windows_after_open_2": lambda v: isinstance(v, list) and len(v) == 1},
    "l3": {"second_owner": lambda v: _is_error(v) and v["type"] == "TerminalInUse",
           "read_after_recycle": lambda v: isinstance(v, str),
           "child_attach": lambda v: isinstance(v, dict) and v.get("attach") == "ok",
           "close_with_live_owner": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
           "child_after_close": lambda v: isinstance(v, dict)
           and str(v.get("read_after_close", "")).startswith("SessionReplaced")},
    # L3b: a conexão de teste conectou (rc 0) e saiu por os._exit(3); o worker novo conectou e leu
    "l3b": {"helper": _helper_ok, "read_after_exit": lambda v: isinstance(v, str),
            "new_worker_connects": lambda v: isinstance(v, list) and any(r.get("rc") == 0 for r in v)},
    "l4": {"too_long": lambda v: str(v).startswith("Truncated"),
           "protected": lambda v: str(v).startswith("ProtectedField"),
           "after_erase": lambda v: isinstance(v, str) and not v.strip(),
           "after_text": _l4_text_ok,
           **{f"after_{c}": _round_trip(c) for c in L4_CHARS}},
    "l5": {"read_while_shown": lambda v: isinstance(v, str), "read_while_minimized": lambda v: isinstance(v, str)},
    "l6": {"logon": lambda v: v == "ok"},
    "l7": {"logon": lambda v: v == "ok"},
    # E.8: o 0.1.5 é desconectado (fica ocioso, ainda ouvindo a porta) e nunca encerrado; ele sai pelo invólucro
    "l8": {"status": lambda v: isinstance(v, dict) and v.get("present") is True and v.get("disconnected", 0) >= 1,
           "legacy_exit": lambda v: v == 0},
    "l9": {"z_read_after_close_d": lambda v: isinstance(v, str), "windows_after_close_d": _only_z_left},
    "compat": {"logon_cics": lambda v: v is True, "close_terminal": lambda v: v is True},
}
