"""Matriz de ``_update.install_mode()`` (H.2; E11, C5)."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

import logging

import pytest

from tests.e2e.update.support import SHA_A, SHA_B, write_dist_info
from rumbapy.errors import UpdateFailed
from rumbapy import _update, _log


@pytest.fixture(autouse=True)
def no_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUMBAPY_DEV", raising=False)
    monkeypatch.delenv("RUMBAPY_TEST_GITHUB", raising=False)


def make_site(root: Path, direct_url: dict[str, Any] | None, *, dist: bool = True) -> Path:
    site = root / "site-packages"
    pkg = site / "rumbapy"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    if dist:
        write_dist_info(site, direct_url)
    return pkg


def mode_of(pkg: Path) -> tuple[str, str | None]:
    return _update.install_mode(here=pkg, search_path=[str(pkg.parent)])


def test_dev(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    notices: list[tuple[str, bool]] = []
    monkeypatch.setattr(_log, "notice", lambda message, force=False: notices.append((message, force)))
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    with caplog.at_level(logging.WARNING, logger="rumbapy._update"):
        assert _update.install_mode() == ("dev", None)
        assert _update.install_mode() == ("dev", None)
    # force=True: o aviso sai mesmo com RUMBAPY_QUIET=1 (H.2, E38)
    assert notices == [("RumbaPy: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)", True)] * 2
    assert "AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1) versão=2.0.0 script=" in caplog.text


def test_dev_notice_ignores_quiet(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """``RUMBAPY_DEV=1`` esquecido + ``RUMBAPY_QUIET=1``: o auto-update desligado continua avisado no stderr."""
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    monkeypatch.setenv("RUMBAPY_QUIET", "1")
    assert _update.install_mode() == ("dev", None)
    assert capsys.readouterr().err == "RumbaPy: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)\n"


def test_this_checkout_without_dev_is_refused() -> None:
    with pytest.raises(UpdateFailed) as info:
        _update.install_mode()                                   # src/rumbapy deste repositório (.git acima)
    assert info.value.message.startswith("Instalação de desenvolvimento detectada (checkout). Defina RUMBAPY_DEV=1")
    assert "python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/" \
           "refs/heads/master.zip" in info.value.message


def test_checkout_dot_git_up_to_four_levels(tmp_path: Path) -> None:
    pkg = make_site(tmp_path / "a" / "b", None, dist=False)      # pasta do pacote sem distribuição (checkout)
    (tmp_path / ".git").mkdir()                                  # pkg.parents[3]
    with pytest.raises(UpdateFailed) as info:
        mode_of(pkg)
    assert info.value.details == {"mode": "checkout"}


def test_dot_git_beyond_four_levels_is_missing(tmp_path: Path) -> None:
    pkg = make_site(tmp_path / "a" / "b" / "c", None, dist=False)
    (tmp_path / ".git").mkdir()                                  # pkg.parents[4]
    with pytest.raises(UpdateFailed) as info:
        mode_of(pkg)
    assert info.value.details is not None and info.value.details["mode"] == "missing"


@pytest.mark.parametrize("direct_url, expected", [
    ({"url": f"https://github.com/CamargoEduardo/RumbaPy/archive/{SHA_A}.zip"}, ("archive", SHA_A)),
    ({"url": "https://github.com/CamargoEduardo/RumbaPy.git", "vcs_info": {"vcs": "git", "commit_id": SHA_B}},
     ("vcs", SHA_B)),
    (None, ("unknown", None)),
])
def test_venv_inside_user_git_repo_is_a_normal_install(tmp_path: Path, direct_url: dict[str, Any] | None,
                                                       expected: tuple[str, str | None]) -> None:
    proj = tmp_path / "proj"                                     # <proj>/.venv/Lib/site-packages/rumbapy
    pkg = make_site(proj / ".venv" / "Lib", direct_url)
    (proj / ".git").mkdir()                                      # pkg.parents[3] = <proj>
    assert mode_of(pkg) == expected


def test_editable_inside_git_repo_is_checkout(tmp_path: Path) -> None:
    pkg = make_site(tmp_path / "a", {"url": "file:///C:/x", "dir_info": {"editable": True}})
    (tmp_path / ".git").mkdir()
    with pytest.raises(UpdateFailed) as info:
        mode_of(pkg)
    assert info.value.details == {"mode": "checkout"}


def test_editable(tmp_path: Path) -> None:
    pkg = make_site(tmp_path, {"url": "file:///C:/Codes/RumbaPy", "dir_info": {"editable": True}})
    with pytest.raises(UpdateFailed) as info:
        mode_of(pkg)
    assert info.value.details == {"mode": "editable"}
    assert "Instalação de desenvolvimento detectada (checkout)" in info.value.message


def test_editable_via_pth_without_git_is_editable(tmp_path: Path) -> None:
    proj = tmp_path / "proj"                                     # ex.: sdist extraído e instalado com pip -e
    pkg = make_site(proj / "src", None, dist=False)              # <proj>/src/rumbapy (sem .git acima)
    site = tmp_path / "venv" / "site-packages"                   # só a dist-info + .pth do build_editable
    write_dist_info(site, {"url": proj.resolve().as_uri(), "dir_info": {"editable": True}})
    with pytest.raises(UpdateFailed) as info:
        _update.install_mode(here=pkg, search_path=[str(site)])
    assert info.value.details == {"mode": "editable"}
    other = make_site(tmp_path / "elsewhere", None, dist=False)  # editable de OUTRA pasta não conta
    with pytest.raises(UpdateFailed) as info:
        _update.install_mode(here=other, search_path=[str(site)])
    assert info.value.details is not None and info.value.details["mode"] == "missing"


@pytest.mark.parametrize(("url", "expected"), [
    (f"https://github.com/CamargoEduardo/RumbaPy/archive/{SHA_A}.zip", ("archive", SHA_A)),
    (f"https://github.com/CamargoEduardo/RumbaPy/archive/{SHA_B.upper()}.zip", ("archive", SHA_B)),
    (f"https://codeload.github.com/CamargoEduardo/RumbaPy/zip/{SHA_B}", ("archive", SHA_B)),
    ("https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip", ("unknown", None)),
    ("file:///C:/temp/rumbapy.zip", ("unknown", None)),
    (f"http://github.com/CamargoEduardo/RumbaPy/archive/{SHA_A}.zip", ("unknown", None)),
])
def test_archive_urls(tmp_path: Path, url: str, expected: tuple[str, str | None]) -> None:
    pkg = make_site(tmp_path, {"url": url, "archive_info": {}})
    assert mode_of(pkg) == expected


def test_vcs_from_0_1_x(tmp_path: Path) -> None:
    pkg = make_site(tmp_path, {"url": "https://github.com/CamargoEduardo/RumbaPy.git",
                               "vcs_info": {"vcs": "git", "commit_id": SHA_A, "requested_revision": None}})
    assert mode_of(pkg) == ("vcs", SHA_A)


def test_vcs_without_valid_commit_is_unknown(tmp_path: Path) -> None:
    pkg = make_site(tmp_path, {"url": "https://x/RumbaPy.git", "vcs_info": {"vcs": "git", "commit_id": "HEAD"}})
    assert mode_of(pkg) == ("unknown", None)


def test_unknown_without_direct_url(tmp_path: Path) -> None:
    pkg = make_site(tmp_path, None)
    assert mode_of(pkg) == ("unknown", None)


def test_missing(tmp_path: Path) -> None:
    pkg = make_site(tmp_path, None, dist=False)
    with pytest.raises(UpdateFailed) as info:
        mode_of(pkg)
    assert info.value.details == {"mode": "missing"}
    assert info.value.message.startswith("A atualização do RumbaPy falhou (instalação não reconhecida")
    assert "Para reinstalar à mão, rode: python -m pip install --force-reinstall" in info.value.message


def test_distribution_of_other_path_is_discarded(tmp_path: Path) -> None:
    other = make_site(tmp_path / "other", {"url": f"https://github.com/o/r/archive/{SHA_A}.zip"})
    mine = make_site(tmp_path / "mine", None, dist=False)
    search = [str(other.parent), str(mine.parent)]
    with pytest.raises(UpdateFailed):
        _update.install_mode(here=mine, search_path=search)
    write_dist_info(mine.parent, {"url": f"https://github.com/o/r/archive/{SHA_B}.zip"})
    assert _update.install_mode(here=mine, search_path=search) == ("archive", SHA_B)
    assert _update.install_mode(here=other, search_path=search) == ("archive", SHA_A)


def test_fake_github_archive_only_in_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = "http://127.0.0.1:5555"
    pkg = make_site(tmp_path, {"url": f"{root}/CamargoEduardo/RumbaPy/archive/{SHA_A}.zip", "archive_info": {}})
    assert mode_of(pkg) == ("unknown", None)
    monkeypatch.setenv("RUMBAPY_TEST_GITHUB", root)
    assert mode_of(pkg) == ("archive", SHA_A)
    monkeypatch.setenv("RUMBAPY_SANDBOX", "0")
    assert mode_of(pkg) == ("unknown", None)


def test_archive_re_is_the_spec_regex() -> None:
    assert _update.ARCHIVE_RE.pattern == (r"^https://(?:github\.com/[^/]+/[^/]+/archive/([0-9a-f]{40})\.zip"
                                          r"|codeload\.github\.com/[^/]+/[^/]+/zip/([0-9a-f]{40}))$")
