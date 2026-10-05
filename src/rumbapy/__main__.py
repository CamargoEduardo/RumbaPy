"""Linha de comando: ``python -m rumbapy {doctor | close <L> | version | update}`` (H.9).

Códigos de saída: 0 = ok; 1 = erro (mensagem em português, sem traceback); 2 = uso inválido; 3 = o RumbaPy acabou de
ser atualizado e o comando precisa ser repetido; 130 = Ctrl+C.

No papel ``cli`` a checagem obrigatória do update é feita aqui (``ensure_latest(context="cli")``) antes de qualquer
comando que fale com o terminal; ``version`` só lê o estado local (não consulta a rede). Se a checagem não alcança o
GitHub (``UpdateCheckFailed``), ``close`` e ``update`` param com 1, mas o ``doctor`` (que não fala com o terminal)
mostra a falha como item ``[FALHA] Update obrigatório`` do relatório e roda o diagnóstico inteiro, saindo com 1.
Erros de uso (saída 2) e a ajuda (``-h``) saem em português.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NoReturn

import argparse
import sys
import os
import re

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RERUN = 3
EXIT_INTERRUPTED = 130

# mensagens do argparse (em inglês) -> português; nomes de comandos/argumentos ficam como estão
_ARGPARSE_PT: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(old), new) for old, new in (
    (r"^the following arguments are required: (.*)$", r"faltam os argumentos: \1"),
    (r"^argument (.*?): invalid choice: (.*?) \(choose from (.*)\)$",
     r"argumento \1: opção inválida: \2 (escolha entre \3)"),
    (r"^unrecognized arguments: (.*)$", r"argumentos não reconhecidos: \1"),
    (r"^argument (.*?): expected one argument$", r"argumento \1: falta o valor"),
    (r"^argument (.*?): ignored explicit argument (.*)$", r"argumento \1: valor não aceito: \2"),
    (r"^ambiguous option: (.*?) could match (.*)$", r"opção ambígua: \1 pode ser \2"),
))


def _pt(message: str) -> str:
    """Traduz uma mensagem de erro do argparse (as desconhecidas ficam como estão)."""
    for pattern, new in _ARGPARSE_PT:
        if pattern.match(message):
            return pattern.sub(new, message)
    return message


def _usage_pt(text: str) -> str:
    return "uso: " + text[len("usage: "):] if text.startswith("usage: ") else text


class _Parser(argparse.ArgumentParser):
    """``ArgumentParser`` com ajuda, uso e erros em português (erro de uso continua saindo com 2)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "argumentos"
        self._optionals.title = "opções"
        self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="mostra esta ajuda e sai")

    def format_usage(self) -> str:
        return _usage_pt(super().format_usage())

    def format_help(self) -> str:
        return _usage_pt(super().format_help())

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {_pt(message)}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m rumbapy", description="RumbaPy: automação de terminais RUMBA.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{doctor,close,version,update}",
                                title="comandos", parser_class=_Parser)
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
    from rumbapy._doctor import format_checked_at
    from rumbapy import _update

    info = _update.status()
    _out(f"RumbaPy {info['version']}")
    _out(f"  commit: {_short(info['commit'])}")
    _out(f"  modo de instalação: {info['mode']}")
    if info.get("error"):
        _out(f"  aviso: {info['error']}")
    last = info.get("last_check")
    if last and info.get("last_check_other"):
        # o update-state.json é por usuário: a checagem registrada é de OUTRO Python/venv com o RumbaPy
        _out(f"  última checagem: nenhuma desta instalação (a registrada, em "
             f"{format_checked_at(last.get('checked_at'))}, foi feita por outra instalação do RumbaPy deste usuário)")
    elif last:
        _out(f"  última checagem: {format_checked_at(last.get('checked_at'))} ({last.get('result', '?')}, remoto "
             f"{_short(last.get('remote'))})")
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


def _cmd_doctor(verbose: bool, update_failure: BaseException | None = None) -> int:
    import rumbapy

    if update_failure is None:
        report = rumbapy.doctor(verbose=verbose)
    else:
        # sem rede/VPN o doctor é justamente o que mais ajuda: a falha da checagem vira um item do relatório
        # (ok=False, rodapé "Há itens com FALHA") e o resto roda, sem consultar o GitHub de novo (A.12 c)
        from rumbapy import _doctor

        report = _doctor.run(verbose=verbose, update_failure=update_failure)
    return EXIT_OK if report.ok else EXIT_ERROR


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
        update_failure: errors.UpdateCheckFailed | None = None
        try:
            _update.ensure_latest(context="cli")
        except errors.UpdateRequired:
            sha, version = _update.restart_info()
            _err(f"RumbaPy atualizado para {version or '?'} ({_short(sha)}). Rode o comando de novo.")
            return EXIT_RERUN
        except errors.UpdateCheckFailed as exc:
            if args.command != "doctor":           # close continua bloqueado sem a checagem (A.9 l)
                raise
            update_failure = exc
        if args.command == "doctor":
            return _cmd_doctor(args.verbose, update_failure)
        return _cmd_close(args.terminal)
    except errors.RumbaError as exc:
        _err(str(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err("RumbaPy: interrompido.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
