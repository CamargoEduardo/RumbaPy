"""Executor de UM roteiro ao vivo (J.6), sempre num processo novo (worker e ``RUMBAPY_HOME`` novos por roteiro).

Uso (Python 64-bit do repositório, na raiz, com ``RUMBAPY_LIVE=1``)::

    .venv\\Scripts\\python.exe -m tests.live.runner --script l1 [--code head|tree] [--report <arquivo.json>]

Também é chamado por ``tests/live/test_live.py`` (pytest) e pelas sondas de ``tools/research``. Ordem:

1. recusas (saída 2, nada é aberto): sem ``RUMBAPY_LIVE=1``, com ``RUMBAPY_SANDBOX=1``, Python 32-bit, fora da
   branch ``dev``, L7 sem ``RUMBAPY_LIVE_L7_AUTORIZADO=1``, ``RUMBAPY_GUARD`` do dono mais restritivo que o do
   roteiro (ou inválido), janela de BRAZIL_D/BRAZIL_Z já aberta, sem console ou sem a confirmação ``SIM <letra>``
   digitada em ``CONIN$`` (pedida SEMPRE aqui, também quando o pytest lança o executor: o console é herdado), roteiro
   de login sem credenciais digitadas;
2. ``RUMBAPY_DEV=1`` (sem auto-update), ``RUMBAPY_HOME`` temporário, ``RUMBAPY_GUARD`` do roteiro e log em DEBUG;
   o código testado é o do ``HEAD`` (``git archive``; ``--code tree`` usa ``src/`` como está);
3. o roteiro; depois, sempre: ``close`` do que sobrou, ``shutdown`` dos workers e conferência das janelas;
4. registro do guard tirado dos logs do worker (linhas ``op=`` de escrita/AID/conexão com ``rc``, início com
   ``guard=``, NORESET, parada), AIDs enviadas e conexões;
5. varredura da senha (L6/L7/compat) em repr/exceções/relatório e em todo arquivo do ``RUMBAPY_HOME``; se achar, o
   relatório só diz ONDE (as ocorrências viram ``***``);
6. o ``RUMBAPY_HOME`` temporário é apagado (``--keep-home`` mantém, para diagnóstico);
7. relatório JSON em ``docs/interno/referencia/`` (fora do Git).

Credenciais: só digitadas no console (senha sem eco), depois do ``SIM <letra>``; nunca por arquivo, argv, stdin ou
ambiente (o worker herdaria). Nunca são exibidas.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.live.support import REPO, SCRIPTS, Credentials
from tests.live import support

import subprocess
import traceback
import datetime
import argparse
import tempfile
import logging
import tarfile
import shutil
import struct
import json
import time
import sys
import io
import os

EXIT_OK, EXIT_FAILED = 0, 1
EXIT_REFUSED = 75                              # != 2 do argparse/"can't open file": recusa nunca se confunde com erro
WORKER_LETTERS = ("D", "Z", "A")
HIDDEN_IN_CONSOLE = ("screen_after_logon", "screen_at_error")


def snapshot_code(dest: Path, ref: str = "HEAD") -> Path:
    """``git archive <ref> src/rumbapy`` numa pasta temporária: edições em andamento não interferem no teste."""
    data = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", ref, "src/rumbapy"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "src"


def python32() -> str:
    """``RUMBAPY_LIVE_PY32`` ou o Python base do ``.venv32`` do repositório."""
    explicit = os.environ.get("RUMBAPY_LIVE_PY32")
    if explicit:
        return explicit
    cfg = REPO / ".venv32" / "pyvenv.cfg"
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "home":
            return str(Path(value.strip()) / "python.exe")
    raise RuntimeError("não achei o Python 32-bit do .venv32 (defina RUMBAPY_LIVE_PY32)")


def refusal(script: str, *, confirm: bool = True, env: Mapping[str, str] | None = None,
            pointer_bits: int | None = None, branch: Callable[[], str | None] = support.current_branch,
            titles: Callable[[], Iterable[str]] = support.visible_window_titles,
            console_factory: support.ConsoleFactory = support.open_console,
            l8_check: Callable[[], str | None] | None = None) -> tuple[str | None, str, Credentials | None]:
    """Todas as recusas antes de abrir qualquer coisa: ``(motivo | None, como confirmou, credenciais)``.

    ``confirm=False`` (o pytest, antes de lançar o executor) só confere as pré-condições e se há console; com
    ``confirm=True`` (o executor) pede ``SIM <letra>`` em ``CONIN$`` e, nos roteiros de login, as credenciais.
    Só lê (ambiente, git, títulos de janelas, console); os parâmetros existem para os testes com dublês."""
    spec = SCRIPTS[script]
    env = os.environ if env is None else env
    bits = struct.calcsize("P") * 8 if pointer_bits is None else pointer_bits
    reason = support.skip_reason(script, env, pointer_bits=bits, branch=branch())
    if reason:
        return reason, "", None
    if spec.opens_terminal:
        open_now = support.terminal_windows(support.windows_to_check(script), titles)
        if open_now:
            return f"já há {len(open_now)} janela(s) de terminal aberta(s): não mexo na sessão de ninguém", "", None
    if script == "l8":
        if l8_check is None:
            from tests.live.scripts import l8_precondition as l8_check
        reason = l8_check()
        if reason:
            return reason, "", None
    if not confirm:
        return (None if support.has_console(console_factory) else support.NO_CONSOLE), "", None
    ok, how = support.confirm(script, console_factory=console_factory)
    if not ok:
        return how, "", None
    creds = None
    if spec.credentials:
        creds = support.get_credentials(spec.credentials, console_factory=console_factory)
        if creds is None:
            return f"{spec.label}: sem credenciais (digite usuário e senha [{spec.credentials}] no console)", how, None
    return None, how, creds


def stop_workers(rumbapy: Any, report: dict[str, Any]) -> None:
    from rumbapy import _bridge, _paths

    out = {}
    for letter in WORKER_LETTERS:
        reg = _paths.read_json(_paths.worker_registry(letter))
        if not (reg and _bridge.registry_alive(reg)):
            continue
        # find_handle nunca sobe worker: se ele saiu entre a conferência e aqui, a limpeza não carrega a DLL de novo
        handle = _bridge.default_bridge().find_handle(letter, "admin")
        if handle is None:
            out[letter] = {"pid": reg.get("pid"), "saiu": True}
            continue
        try:
            handle.request("shutdown", {"reason": "fim do roteiro ao vivo"})
        finally:
            _bridge.default_bridge().release(handle)
        out[letter] = {"pid": reg.get("pid"), "saiu": _bridge.wait_exit(reg.get("pid"), reg.get("created"), 20)}
    report["workers"] = out or "nenhum vivo"


def describe_exc(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"


def cleanup(rumbapy: Any, script: str, report: dict[str, Any], failed: bool) -> None:
    """Fecha o que sobrou do roteiro, para os workers e confere que não ficou janela (sempre roda)."""
    spec = SCRIPTS[script]
    if rumbapy is not None and spec.opens_terminal:
        leftover = set()
        for letter in spec.letters:
            if failed or support.terminal_windows((letter,)):
                leftover.add(letter)
        for letter in sorted(leftover):
            try:
                report[f"cleanup_close_{letter}"] = rumbapy.close(letter)
            except Exception as exc:
                report[f"cleanup_close_{letter}"] = describe_exc(exc)
    if rumbapy is not None:
        try:
            stop_workers(rumbapy, report)
        except Exception as exc:
            report["workers"] = describe_exc(exc)
    if spec.opens_terminal:
        time.sleep(2.0)
        report["windows_left"] = support.terminal_windows(support.windows_to_check(script))


def run(script: str, *, code_mode: str = "head", credentials: Credentials | None = None, confirmed_by: str = "",
        report_file: Path | None = None, keep_home: bool = False) -> tuple[int, dict[str, Any], Path]:
    """Prepara o ambiente do roteiro, executa, limpa, varre a senha e grava o relatório (pré-condições conferidas)."""
    from tests.live.scripts import SCRIPT_FUNCTIONS, Context

    spec = SCRIPTS[script]
    home = Path(tempfile.mkdtemp(prefix=f"rumbapy-live-{script}-"))
    os.environ.update({"RUMBAPY_DEV": "1", "RUMBAPY_HOME": str(home), "RUMBAPY_GUARD": spec.guard,
                       "RUMBAPY_LOG_LEVEL": "DEBUG"})
    report: dict[str, Any] = {"script": script, "label": spec.label, "guard": spec.guard, "home": str(home),
                              "code": code_mode, "confirmed_by": confirmed_by, "git": support.git_head(),
                              "started": datetime.datetime.now().isoformat(timespec="seconds")}
    rumbapy = None
    ctx = None
    failed = False
    try:
        code = snapshot_code(home / "code") if code_mode == "head" else REPO / "src"
        sys.path.insert(0, str(code))
        import rumbapy

        report["version"] = rumbapy.__version__
        ctx = Context(rumbapy=rumbapy, py32=python32(), code=code, home=home, credentials=credentials, report=report)
        SCRIPT_FUNCTIONS[script](ctx)
    except Exception as exc:
        failed = True
        report["error"] = describe_exc(exc)
        report["traceback"] = traceback.format_exc().splitlines()[-12:]
    except KeyboardInterrupt:
        failed = True
        report["error"] = "KeyboardInterrupt: interrompido pelo usuário"
    finally:
        cleanup(rumbapy, script, report, failed)
        attach_audit(report, home)
        report["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    if credentials is not None:
        report = scan_and_redact(report, credentials.password, list(ctx.texts if ctx is not None else ()), home)
    report["home_removed"] = remove_home(home, keep=keep_home)
    path = support.write_report(report, report_file or support.report_path(script))
    return (EXIT_FAILED if "error" in report else EXIT_OK), report, path


def attach_audit(report: dict[str, Any], home: Path) -> None:
    """Registro do guard (linhas op= de escrita/AID/conexão, início, NORESET, parada), AIDs, rc do NORESET e as
    conexões, tudo tirado dos logs do worker no ``RUMBAPY_HOME`` do roteiro (inclusive os rotacionados, ``.1``..``.5``).
    AIDs e conexões vêm do registro inteiro; no relatório vão até ``AUDIT_LIMIT`` linhas (o corte é acusado).

    O ``policy.audit`` do ``GuardedDll`` vive no processo do worker e não atravessa o protocolo: estas linhas do log
    (D.7) são a reconstrução dele (pendência do P-B: registrar o audit no log ao parar o worker)."""
    audit = support.guard_audit_lines(home)
    report["guard_audit"] = audit[:support.AUDIT_LIMIT]
    if len(audit) > support.AUDIT_LIMIT:
        report["guard_audit_truncated"] = True
    rotated = support.rotated_worker_logs(home)
    if rotated:
        report["worker_logs_rotated"] = rotated
    report["aids_sent"] = support.sent_aids(audit)
    report["connects"] = support.connect_records(audit)
    report["noreset_rc"] = support.noreset_rc(audit)
    report["read_ops"] = support.read_op_counts(home)


def scan_and_redact(report: dict[str, Any], password: str, texts: list[str], home: Path) -> dict[str, Any]:
    """Varredura da senha (T-SEC-1) em repr/exceções, no relatório e em todo arquivo do ``home``; se achar, o
    relatório gravado leva só ONDE achou, com toda ocorrência trocada por ``***`` (nunca a senha em disco)."""
    where = support.secret_locations(password, report, texts, support.files_to_scan(home), home)
    report["password_found_anywhere"] = bool(where)
    if where:
        report["password_found_in"] = where
        return support.redact(report, password)
    return report


def remove_home(home: Path, *, keep: bool) -> bool | str:
    """Apaga o ``RUMBAPY_HOME`` temporário (logs DEBUG, registros, cópia do código) salvo ``--keep-home``.
    Devolve ``True`` (apagado), ``False`` (mantido a pedido) ou o motivo de ter sobrado algo."""
    if keep:
        return False
    for name in ("rumbapy", ""):                   # o log do cliente fica aberto neste processo: fecha antes
        log = logging.getLogger(name)
        for handler in list(log.handlers):
            target = getattr(handler, "baseFilename", None)
            if target and Path(target).resolve().is_relative_to(home.resolve()):
                log.removeHandler(handler)
                handler.close()
    shutil.rmtree(home, ignore_errors=True)
    return True if not home.exists() else f"sobrou parte de {home} (worker ainda vivo?): apague à mão"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.live.runner", description="Roteiro ao vivo da J.6")
    ap.add_argument("--script", choices=sorted(SCRIPTS), required=True)
    ap.add_argument("--code", choices=("head", "tree"), default=os.environ.get("RUMBAPY_LIVE_CODE") or "head")
    ap.add_argument("--keep-home", action="store_true", help="não apaga o RUMBAPY_HOME temporário (diagnóstico)")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")           # saída redirecionada em cp1252 não derruba o fim do roteiro
    reason, how, creds = refusal(args.script)
    if reason:
        print(f"RECUSADO: {reason}", file=sys.stderr)
        return EXIT_REFUSED
    code, report, path = run(args.script, code_mode=args.code, credentials=creds, confirmed_by=how,
                             report_file=args.report, keep_home=args.keep_home)
    if report.get("password_found_anywhere"):
        print("ATENÇÃO: a senha apareceu em " + ", ".join(report.get("password_found_in", ()))
              + "; o relatório (com as ocorrências trocadas por ***) não é exibido aqui.", file=sys.stderr)
    else:
        shown = {k: v for k, v in report.items() if k not in HIDDEN_IN_CONSOLE}
        print(json.dumps(shown, indent=2, ensure_ascii=False, default=str))
    print(f"relatório: {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
