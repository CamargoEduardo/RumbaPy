"""Commit remoto (H.3): pkt-lines, fixture real do ``info/refs``, Content-Type, fallbacks e classificação de erros.

Rede só no loopback (``FakeGitHub``); o GitHub real nunca é acessado.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import subprocess

import pytest

from tests.e2e.update.support import ADVERTISEMENT, SHA_A, SHA_B, SHA_REAL, FakeGitHub, info_refs_body, pkt
from rumbapy import _bootstrap

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "info_refs_master_2026-10-03.bin"


@pytest.fixture()
def github(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_bootstrap, "_sleep", lambda s: None)
    monkeypatch.setattr(_bootstrap, "_git_executable", lambda: None)
    with FakeGitHub(SHA_A) as gh:
        monkeypatch.setenv("RUMBAPY_TEST_GITHUB", gh.root)
        yield gh


# ------------------------------------------------------------------------------------------- parse

def test_real_fixture_from_github() -> None:
    data = FIXTURE.read_bytes()
    master, symref = _bootstrap.parse_info_refs(data)
    assert master == SHA_REAL
    assert symref == "refs/heads/master"


def test_pkt_lines_of_real_fixture() -> None:
    lines = _bootstrap.parse_pkt_lines(FIXTURE.read_bytes())
    assert lines[0] == b"# service=git-upload-pack\n"
    assert lines[1] is None and lines[-1] is None                 # flush depois do serviço e no fim
    assert lines[2].startswith(SHA_REAL.encode() + b" HEAD\0")


def test_head_pointing_to_dev_still_picks_master() -> None:
    body = info_refs_body({"refs/heads/dev": SHA_B, "refs/heads/master": SHA_A}, head="refs/heads/dev")
    master, symref = _bootstrap.parse_info_refs(body)
    assert (master, symref) == (SHA_A, "refs/heads/dev")


def test_no_master_ref() -> None:
    body = info_refs_body({"refs/heads/dev": SHA_B}, head="refs/heads/dev")
    assert _bootstrap.parse_info_refs(body) == (None, "refs/heads/dev")


@pytest.mark.parametrize("data", [
    b"<html>login</html>",
    b"00zz# service=git-upload-pack\n",
    pkt(b"# service=git-upload-pack\n")[:-3],
    pkt(b"hello\n") + b"0000",
])
def test_malformed(data: bytes) -> None:
    with pytest.raises(ValueError):
        _bootstrap.parse_info_refs(data)


def test_invalid_sha_on_master() -> None:
    body = pkt(b"# service=git-upload-pack\n") + b"0000" + pkt(b"XYZ refs/heads/master\n") + b"0000"
    with pytest.raises(ValueError):
        _bootstrap.parse_info_refs(body)


# ------------------------------------------------------------------------------------------- HTTP (loopback)

def test_remote_from_info_refs_with_headers(github: FakeGitHub) -> None:
    assert _bootstrap.remote_master_sha() == SHA_A
    path, headers = github.requests[0]
    assert path == "/CamargoEduardo/RumbaPy.git/info/refs?service=git-upload-pack"
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered["user-agent"].startswith("git/2.45 (rumbapy/")
    assert lowered["cache-control"] == "no-cache" and lowered["pragma"] == "no-cache"
    assert "git-protocol" not in lowered


def test_head_dev_logs_warning_and_follows_master(github: FakeGitHub, tmp_path: Path) -> None:
    github.extra_refs = {"refs/heads/dev": SHA_B}
    github.head = "refs/heads/dev"
    assert _bootstrap.remote_master_sha() == SHA_A
    log = _bootstrap.update_log_path()
    assert log is not None and "refs/heads/dev" in log.read_text(encoding="utf-8")


def test_html_content_type_is_network_error(github: FakeGitHub) -> None:
    github.content_type = "text/html; charset=utf-8"
    github.status = {"/api/": 503}
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "network"
    assert "text/html" in info.value.reason
    assert len(github.paths("info")) == 2                        # 2 tentativas


def test_api_fallback(github: FakeGitHub) -> None:
    github.content_type = "text/html"
    github.api_sha = SHA_B
    assert _bootstrap.remote_master_sha() == SHA_B
    (path, headers), = [(p, h) for p, h in github.requests if "/api/" in p]
    assert path == "/api/repos/CamargoEduardo/RumbaPy/commits/master"
    assert {k.lower(): v for k, v in headers.items()}["accept"] == "application/vnd.github.sha"


def test_git_fallback(github: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    github.status = {"/info/refs": 503, "/api/": 503}
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        assert kwargs["timeout"] == _bootstrap.GIT_TIMEOUT
        return subprocess.CompletedProcess(argv, 0, f"{SHA_B}\trefs/heads/master\n".encode(), b"")

    monkeypatch.setattr(_bootstrap, "_git_executable", lambda: "git.exe")
    monkeypatch.setattr(_bootstrap.subprocess, "run", fake_run)
    assert _bootstrap.remote_master_sha() == SHA_B
    assert calls == [["git.exe", "ls-remote", f"{github.root}/CamargoEduardo/RumbaPy.git", "refs/heads/master"]]


def test_404_is_config_error(github: FakeGitHub) -> None:
    github.status = {"/info/refs": 404, "/api/": 404}
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "config"
    assert len(github.paths("info")) == 1                        # erro de configuração não repete


def test_missing_master_is_config_error(github: FakeGitHub) -> None:
    github.master = None
    github.extra_refs = {"refs/heads/dev": SHA_B}
    github.status = {"/api/": 404}
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "config"


def test_5xx_and_429_are_network(github: FakeGitHub) -> None:
    github.status = {"/info/refs": 500, "/api/": 429}
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "network"
    assert "HTTP 500" in info.value.reason and "HTTP 429" in info.value.reason


def test_connection_refused_is_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_bootstrap, "_sleep", lambda s: None)
    monkeypatch.setattr(_bootstrap, "_git_executable", lambda: None)
    monkeypatch.setattr(_bootstrap, "HTTP_ATTEMPTS", 1)          # no Windows cada recusa leva ~2 s
    gh = FakeGitHub().start()
    root = gh.root
    gh.stop()                                                    # porta fechada
    monkeypatch.setenv("RUMBAPY_TEST_GITHUB", root)
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "network"


def test_sandbox_without_test_github_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_TEST_GITHUB", raising=False)
    with pytest.raises(_bootstrap.BootstrapError) as info:
        _bootstrap.remote_master_sha()
    assert info.value.kind == "sandbox"


def test_test_variables_ignored_without_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUMBAPY_TEST_GITHUB", "http://127.0.0.1:1")
    monkeypatch.setenv("RUMBAPY_TEST_PIP", "x.py")
    monkeypatch.setenv("RUMBAPY_SANDBOX", "0")
    assert _bootstrap.fake_github_root() is None and _bootstrap.fake_pip() is None
    assert _bootstrap.info_refs_url() == ("https://github.com/CamargoEduardo/RumbaPy.git/info/refs"
                                          "?service=git-upload-pack")
    assert _bootstrap.api_commit_url() == "https://api.github.com/repos/CamargoEduardo/RumbaPy/commits/master"


def test_advertisement_constant() -> None:
    assert _bootstrap.ADVERTISEMENT == ADVERTISEMENT
