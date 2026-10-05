"""Stub para clientes v0.1.5 ainda em memória (B.9.4): responde no lugar do antigo servidor TCP, SEM a DLL.

``python -m rumbapy.server --terminal_type=D`` (papel ``legacy-shim``: o ``__init__`` não atualiza nem importa nada)
escuta ``127.0.0.1:<porta da letra>``, uma conexão por requisição (lê até EOF). ``ping`` -> ``success`` com
``"shim": true``; qualquer outra ação -> erro pedindo para rodar o script de novo, e o stub sai. Sai também após 120 s.
Nunca registra ``params`` (podem conter credenciais).
"""
# ruff: noqa: I001
from __future__ import annotations

import argparse
import logging
import socket
import json
import time

logger = logging.getLogger(__name__)

PORTS = {"D": 8500, "A": 8600, "Z": 8700}       # mesma tabela da v0.1.5
DEFAULT_PORT = 8800                             # outras letras (inclusive minúsculas: a v0.1.5 não normalizava)
LIFETIME = 120.0
UPDATED = "O RumbaPy foi atualizado para a v2. Rode o script de novo."


def _version() -> str:
    try:
        from ._version import __version__
        return __version__
    except Exception:                            # o stub responde mesmo com o pacote meio instalado
        return "?"


def _reply(conn: socket.socket) -> bool:
    """Atende uma conexão; devolve ``True`` se a ação não era ``ping`` (o stub deve sair)."""
    data = b""
    while chunk := conn.recv(4096):
        data += chunk
        if len(data) > 1_048_576:
            break
    try:
        action = json.loads(data.decode("utf-8")).get("action")
    except (ValueError, AttributeError):
        action = None
    if action == "ping":
        answer = {"success": True, "message": "Server is running", "shim": True, "version": _version()}
    else:
        answer = {"success": False, "error": UPDATED}
    logger.info("stub v0.1.5: ação %r respondida", action if isinstance(action, str) else None)
    conn.sendall(json.dumps(answer).encode("utf-8"))
    return action != "ping"


def serve(server: socket.socket, *, lifetime: float = LIFETIME) -> None:
    """Atende em ``server`` (já em ``listen``) até a 1ª ação que não seja ``ping`` ou até ``lifetime`` segundos."""
    deadline = time.monotonic() + lifetime
    while (left := deadline - time.monotonic()) > 0:
        server.settimeout(min(left, 1.0))
        try:
            conn, _ = server.accept()
        except (socket.timeout, TimeoutError):
            continue
        with conn:
            conn.settimeout(5.0)
            try:
                if _reply(conn):
                    return
            except OSError as exc:
                logger.warning("stub v0.1.5: conexão falhou (%s)", type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stub do servidor RumbaPy v0.1.5 (sem DLL)")
    parser.add_argument("--terminal_type", type=str, default="D")
    args, _ = parser.parse_known_args(argv)
    port = PORTS.get(args.terminal_type, DEFAULT_PORT)   # sensível a maiúsculas, como o cliente v0.1.5
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        try:
            server.bind(("127.0.0.1", port))
        except OSError:
            return 0                             # outro processo já atende a porta
        server.listen(5)
        serve(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
