"""Linha de comando: ``python -m rumbapy {doctor | close <L> | version | update}`` (H.9).

Códigos de saída: 0 = ok; 1 = erro (mensagem em português, sem traceback); 2 = uso inválido; 3 = o RumbaPy acabou de
ser atualizado e o comando precisa ser repetido; 130 = Ctrl+C.

No papel ``cli`` a checagem obrigatória do update é feita aqui (``ensure_latest(context="cli")``) antes de qualquer
comando que fale com o terminal; ``version`` só lê o estado local (não consulta a rede).
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import argparse
import sys
import os

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RERUN = 3
EXIT_INTERRUPTED = 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rumbapy", description="RumbaPy: automação de terminais RUMBA.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{doctor,close,version,update}")
    doctor = sub.add_parser("doctor", help="diagnóstico do ambiente (RUMBA, Python 32-bit, update, workers)")
    doctor.add_argument("--verbose", action="store_true", help="relatório detalhado")
    close = sub.add_parser("close", help="fecha todas as janelas do terminal (ex.: close D)")
    close.add_argument("terminal", help="letra (D, Z, A) ou nome do perfil")
    sub.add_parser("version", help="versão, commit instalado, modo de instalação e última checagem")
    sub.add_parser("update", help="força a checagem e instala a versão publicada")
    return parser


def _out(text: str) -> None:
    print(text, flush=True)


def _err(text: str) -> None:
    if sys.stderr is not None:
        print(text, file=sys.stderr, flush=True)


def _short(sha: Any) -> str:
    return sha[:7] if isinstance(sha, str) and sha else "desconhecido"


def _cmd_version() -> int:
    from rumbapy import _update

    info = _update.status()
    _out(f"RumbaPy {info['version']}")
    _out(f"  commit: {_short(info['commit'])}")
    _out(f"  modo de instalação: {info['mode']}")
    if info.get("error"):
        _out(f"  aviso: {info['error']}")
    last = info.get("last_check")
    if last:
        from datetime import datetime

        checked = last.get("checked_at")
        when = datetime.fromtimestamp(checked).strftime("%Y-%m-%d %H:%M:%S") if isinstance(checked, (int, float)) \
            else "?"
        _out(f"  última checagem: {when} ({last.get('result', '?')}, remoto {_short(last.get('remote'))})")
    else:
        _out("  última checagem: nenhuma registrada")
    build_id = getattr(sys.modules.get("rumbapy"), "BUILD_ID", None)
    if build_id:
        _out(f"  build_id: {build_id}")
    return EXIT_OK


def _cmd_update() -> int:
    from rumbapy import _update, errors

    try:
        _update.ensure_latest(context="cli", force=True)
    except errors.UpdateRequired:
        sha, version = _update.restart_info()
        _out(f"RumbaPy atualizado para {version or '?'} ({_short(sha)}).")
        return EXIT_OK
    if os.environ.get("RUMBAPY_DEV") == "1":
        _out("RumbaPy: auto-update desligado (RUMBAPY_DEV=1); nada foi instalado.")
        return EXIT_OK
    state = _update.read_state() or {}
    _out(f"RumbaPy já está na versão publicada ({_update.__version__}, {_short(state.get('remote'))}).")
    return EXIT_OK


def _cmd_doctor(verbose: bool) -> int:
    import rumbapy

    report = rumbapy.doctor(verbose=verbose)
    return EXIT_OK if getattr(report, "ok", True) else EXIT_ERROR


def _cmd_close(terminal: str) -> int:
    import rumbapy

    count = rumbapy.close(terminal)
    _out(f"RumbaPy: {count} janela(s) do terminal {terminal.upper()} fechada(s).")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    from rumbapy import _update, errors

    try:
        if args.command == "version":
            return _cmd_version()
        if args.command == "update":
            return _cmd_update()
        try:
            _update.ensure_latest(context="cli")
        except errors.UpdateRequired:
            sha, version = _update.restart_info()
            _err(f"RumbaPy atualizado para {version or '?'} ({_short(sha)}). Rode o comando de novo.")
            return EXIT_RERUN
        if args.command == "doctor":
            return _cmd_doctor(args.verbose)
        return _cmd_close(args.terminal)
    except errors.RumbaError as exc:
        _err(str(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err("RumbaPy: interrompido.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
