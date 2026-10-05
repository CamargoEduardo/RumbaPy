"""Instalação falsa num venv temporário + GitHub falso no loopback + pip falso (J.1): sem rede real, sem pip real."""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import subprocess
import json

import pytest

from tests.e2e.update.support import (FAKE_PIP, SHA_A, FakeGitHub, archive_direct_url, child_env, copy_package,
                                      make_venv, run_python, venv_site, write_dist_info)


@dataclass
class Installation:
    python: Path
    site: Path
    home: Path
    good: Path                     # cópia intacta do pacote (o que o pip falso "instala")
    pip_log: Path
    github: FakeGitHub
    work: Path
    github_root: str = ""          # guardado: continua valendo depois de github.stop()

    def install(self, sha: str) -> None:
        copy_package(self.site, self.good)
        write_dist_info(self.site, archive_direct_url(self.github_root, sha))

    def env(self, **extra: str | None) -> dict[str, str]:
        base: dict[str, str | None] = {"RUMBAPY_TEST_GITHUB": self.github_root, "RUMBAPY_TEST_PIP": str(FAKE_PIP),
                                       "FAKE_PIP_LOG": str(self.pip_log), "FAKE_PIP_SITE": str(self.site),
                                       "FAKE_PIP_SOURCE": str(self.good)}
        return child_env(self.home, **{**base, **extra})

    def run(self, code: str = "import rumbapy", **extra: str | None) -> subprocess.CompletedProcess[str]:
        return run_python(self.python, code, self.env(**extra), self.work, timeout=90)

    def popen(self, code: str = "import rumbapy", **extra: str | None) -> subprocess.Popen[str]:
        return subprocess.Popen([str(self.python), "-c", code], env=self.env(**extra), cwd=str(self.work),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", stdin=subprocess.DEVNULL)

    def pip_calls(self) -> list[list[str]]:
        if not self.pip_log.exists():
            return []
        return [json.loads(line) for line in self.pip_log.read_text(encoding="utf-8").splitlines()]

    def installed_url(self) -> str | None:
        dist = next(self.site.glob("rumbapy-*.dist-info"))
        return json.loads((dist / "direct_url.json").read_text(encoding="utf-8"))["url"]


@pytest.fixture()
def installation(tmp_path: Path) -> Iterator[Installation]:
    python = make_venv(tmp_path / "venv")
    good = copy_package(tmp_path / "pristine")
    with FakeGitHub(SHA_A) as gh:
        inst = Installation(python=python, site=venv_site(python), home=tmp_path / "home", good=good,
                            pip_log=tmp_path / "pip-argv.jsonl", github=gh, work=tmp_path,
                            github_root=gh.root)
        inst.home.mkdir()
        inst.install(SHA_A)
        yield inst
