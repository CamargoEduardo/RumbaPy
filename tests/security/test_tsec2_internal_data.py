"""T-SEC-2 (J.5): nenhum arquivo versionado contém dados internos da Volvo (hostnames/IPs de mainframe, portas…).

O repositório é PÚBLICO (CLAUDE.md, regra 5). Os padrões NÃO ficam no repositório: vêm do segredo do CI
``RUMBAPY_TSEC2_PATTERNS`` (separados por ``;``) ou, na máquina do dono, de ``docs/interno/tsec2_patterns.txt``
(fora do Git; uma linha por padrão, ``#`` = comentário). Sem nenhum dos dois o teste é pulado.

Arquivos conferidos: os rastreados (``git ls-files``) e os novos ainda não rastreados que não estão no
``.gitignore`` (seriam versionados no próximo commit). A busca é literal e sem diferenciar maiúsculas, em UTF-8 e
UTF-16. O teste NUNCA imprime os padrões nem os trechos casados: só o nome dos arquivos.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import subprocess
import os

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCAL_PATTERNS = REPO / "docs" / "interno" / "tsec2_patterns.txt"
ENV_NAME = "RUMBAPY_TSEC2_PATTERNS"
MAX_BYTES = 32 * 1024 * 1024


def load_patterns(env: Mapping[str, str], local: Path = LOCAL_PATTERNS) -> tuple[list[str], str | None]:
    """``(padrões, origem)``; origem ``"env"``, ``"arquivo local"`` ou ``None`` (sem padrões: pular)."""
    raw = env.get(ENV_NAME, "")
    patterns = [p.strip() for p in raw.split(";") if p.strip()]
    if patterns:
        return patterns, "env"
    try:
        text = local.read_text(encoding="utf-8-sig")
    except OSError:
        return [], None
    patterns = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return (patterns, "arquivo local") if patterns else ([], None)


def versioned_files(repo: Path = REPO) -> list[str] | None:
    """Rastreados + novos não ignorados (caminhos relativos, ``/``); ``None`` se o git não responder."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = out.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted({name for name in names if name})


def _needles(patterns: Iterable[str]) -> list[bytes]:
    needles: list[bytes] = []
    for pattern in patterns:
        lowered = pattern.lower()
        for encoding in ("utf-8", "utf-16-le"):
            data = lowered.encode(encoding)
            if data and data not in needles:
                needles.append(data)
    return needles


def files_with_patterns(repo: Path, files: Iterable[str], patterns: Iterable[str]) -> list[str]:
    """Nomes dos arquivos que contêm algum padrão (só os nomes; nunca o padrão nem o trecho)."""
    needles = _needles(patterns)
    hits = []
    for name in files:
        path = repo / name
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_BYTES)
        except OSError:                              # removido da árvore (ainda no índice) ou ilegível
            continue
        lowered = data.lower()
        if any(needle in lowered for needle in needles):
            hits.append(name)
    return hits


def failure_message(hits: list[str]) -> str:
    return (f"T-SEC-2: {len(hits)} arquivo(s) versionado(s) contêm dados internos (padrões de docs/interno ou do "
            f"segredo {ENV_NAME}; mova o conteúdo para docs/interno/ ou use apelidos): " + ", ".join(hits))


def test_no_versioned_file_contains_internal_patterns() -> None:
    patterns, origin = load_patterns(os.environ)
    if not patterns:
        pytest.skip(f"T-SEC-2 sem padrões: defina o segredo {ENV_NAME} (CI) ou crie docs/interno/tsec2_patterns.txt")
    files = versioned_files()
    if files is None:
        pytest.skip("T-SEC-2: git ls-files indisponível")
    assert origin in ("env", "arquivo local") and files
    hits = files_with_patterns(REPO, files, patterns)
    del patterns                                     # nada dos padrões fica em variáveis exibíveis pelo pytest
    if hits:
        pytest.fail(failure_message(hits), pytrace=False)


# ------------------------------------------------------------------------------------------- o próprio T-SEC-2

FAKE = "mainframe-falso.exemplo.invalid"


def test_scanner_finds_a_planted_pattern_and_reports_only_names(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("host CICS-BR (apelido)\n", encoding="utf-8")
    (tmp_path / "docs" / "vaza.md").write_text(f"conecte em {FAKE.upper()}:23\n", encoding="utf-8")
    (tmp_path / "utf16.txt").write_bytes(f"x {FAKE} y".encode("utf-16-le"))
    (tmp_path / "bin.dat").write_bytes(b"\x00\xff" + FAKE.encode() + b"\x00")
    files = ["docs/ok.md", "docs/vaza.md", "utf16.txt", "bin.dat", "removido.txt"]
    hits = files_with_patterns(tmp_path, files, [FAKE])
    assert hits == ["docs/vaza.md", "utf16.txt", "bin.dat"]
    message = failure_message(hits)
    assert FAKE not in message.lower() and "docs/vaza.md" in message
    assert files_with_patterns(tmp_path, files, ["outro-host.invalid"]) == []


def test_patterns_from_env_or_local_file(tmp_path: Path) -> None:
    local = tmp_path / "tsec2_patterns.txt"
    assert load_patterns({}, local) == ([], None)
    assert load_patterns({ENV_NAME: " a.invalid ; ;b.invalid;"}, local) == (["a.invalid", "b.invalid"], "env")
    local.write_text("# comentário\n\n  c.invalid  \n# outro\nd.invalid\n", encoding="utf-8")
    assert load_patterns({ENV_NAME: " ; "}, local) == (["c.invalid", "d.invalid"], "arquivo local")
    assert load_patterns({ENV_NAME: "e.invalid"}, local) == (["e.invalid"], "env")      # o segredo do CI vence
    local.write_text("# só comentário\n", encoding="utf-8")
    assert load_patterns({}, local) == ([], None)


def test_versioned_files_lists_this_repository() -> None:
    files = versioned_files()
    if files is None:
        pytest.skip("git indisponível")
    assert files is not None
    assert "pyproject.toml" in files and "tests/security/test_tsec2_internal_data.py" in files
    assert not [f for f in files if f.startswith("docs/interno/") or f == "config.ini"]
