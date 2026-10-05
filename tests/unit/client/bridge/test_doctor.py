"""``doctor()``: relatório em português, sem segredos e sem hostnames, sem tocar o terminal (só leitura)."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from rumbapy.errors import Python32NotFound
from rumbapy import _doctor, _runtime32

import secrets
import re

import pytest

HOST = "mainframe-cics.interno.example.com"
IP = "10.20.30.40"
PASSWORD = "SenhaSuperSecreta1"
CHECKED_AT = 1791156157.52            # epoch, como o update-state.json grava (chave "checked_at")


def probes(**kw: Any) -> _doctor.Probes:
    base: dict[str, Any] = dict(
        dll_path=r"C:\RUMBA\ehlapi32.Dll",
        launcher_from_registry=lambda: r"C:\HAWL\Host Access Web Launcher.exe",
        is_file=lambda path: True,
        remote_sha=lambda: "a" * 40,
        update_status=lambda: {"mode": "archive", "commit": "a" * 40, "error": None,
                               "last_check": {"checked_at": CHECKED_AT, "result": "up-to-date", "remote": "a" * 40}},
        find_runtime=lambda: _runtime32.Runtime32(r"C:\Py313-32\python.exe", r"C:\Py313-32\python.exe", (3, 13, 2)),
        probe_worker=lambda letter: {"letter": letter, "registered": False, "alive": False},
        listener_pid=lambda port: None,
        build_id=lambda: "9f2c1e0b7a4d3c21",
        environ={"RUMBAPY_DEV": "1", "RUMBAPY_TEST_GITHUB": f"http://{HOST}:8080/x"},
    )
    base.update(kw)
    return _doctor.Probes(**base)


def test_all_ok_report() -> None:
    report = _doctor.collect(probes=probes())
    assert report.ok
    text = str(report)
    assert text.startswith("RumbaPy 2.0.0 — diagnóstico") and text.endswith("Tudo certo.")
    assert "[ok]    Python 32-bit: Python 3.13.2 32-bit" in text
    assert "[ok]    GitHub: alcançável; versão publicada aaaaaaa" in text
    assert report["Processos auxiliares"].detail.startswith("0 vivo(s)")
    assert repr(report).startswith("<DoctorReport ok")


def test_failures_make_report_not_ok() -> None:
    def missing() -> Any:
        raise Python32NotFound.make("not_found")

    report = _doctor.collect(probes=probes(find_runtime=missing, is_file=lambda p: False))
    assert not report.ok
    assert report["Python 32-bit"].status == "falha" and "Como resolver" in report["Python 32-bit"].detail
    assert report["RUMBA (DLL HLLAPI)"].status == "falha"
    assert report["Host Access Web Launcher"].status == "aviso"
    assert "FALHA" in str(report)


def test_no_hostnames_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    key = secrets.token_bytes(32)

    class NetError(Exception):
        reason = f"https://{HOST}/info/refs recusou a conexão de {IP}"

    def remote() -> str:
        raise NetError

    worker = {"letter": "D", "registered": True, "alive": True, "pid": 4321, "state": "owned", "version": "2.0.0",
              "build_id": "x", "connected": True, "owner_pid": 5555, "owner_script": f"logon {HOST}.py",
              "error": f"Address: {HOST} ({IP})"}
    report = _doctor.collect(verbose=True, probes=probes(
        remote_sha=remote, probe_worker=lambda letter: worker if letter == "D" else {"registered": False},
        launcher_from_registry=lambda: rf"\\{HOST}\share\launcher.exe",
        listener_pid=lambda p: 777 if p == 8500 else None,
        update_status=lambda: {"mode": "archive", "commit": "b" * 40, "error": f"falhou em http://{HOST}/x"},
        environ={"RUMBAPY_DEV": "1", "RUMBAPY_TEST_GITHUB": f"http://{HOST}/", "RUMBAPY_PORTAL": f"https://{HOST}",
                 "RUMBAPY_SECRET_THING": PASSWORD}))
    for text in (report.to_text(verbose=True), str(report.to_dict())):
        assert HOST not in text and IP not in text and PASSWORD not in text
        assert key.hex() not in text and "authkey" not in text.lower()
        assert not re.search(r"https?://", text)
    full = report.to_text(verbose=True)
    assert "porta 8500 (D): PID 777" in full and "dono PID 5555" in full
    assert "RUMBAPY_PORTAL" in full and "RUMBAPY_SECRET_THING" in full    # só os nomes


def test_github_skipped_in_sandbox_without_fake() -> None:
    calls: list[int] = []
    report = _doctor.collect(probes=probes(remote_sha=lambda: calls.append(1) or "a" * 40,
                                           environ={"RUMBAPY_SANDBOX": "1"}))
    assert report["GitHub"].status == "info" and calls == []


def test_newer_version_published_is_warning() -> None:
    report = _doctor.collect(probes=probes(remote_sha=lambda: "c" * 40))
    assert report["GitHub"].status == "aviso" and "versão nova" in report["GitHub"].detail
    # o ensure_latest do CLI pode ter usado o cache de 60 s: "rode o script de novo" não atualizaria; o update força
    assert "python -m rumbapy update" in report["GitHub"].detail and "script de novo" not in report["GitHub"].detail


def test_verbose_shows_when_the_last_check_happened() -> None:
    from datetime import datetime

    report = _doctor.collect(verbose=True, probes=probes())
    when = datetime.fromtimestamp(CHECKED_AT).strftime("%Y-%m-%d %H:%M:%S")
    assert f"última checagem: {when} resultado=up-to-date remoto=aaaaaaa" in report["RumbaPy"].extra
    assert _doctor.format_checked_at(None) == "?" and _doctor.format_checked_at("ontem") == "?"
    assert _doctor.format_checked_at(True) == "?"


def test_verbose_flags_a_check_recorded_by_another_install() -> None:
    status = {"mode": "unknown", "commit": None, "error": None, "last_check_other": True,
              "last_check": {"checked_at": CHECKED_AT, "result": "updated", "remote": "e" * 40}}
    report = _doctor.collect(verbose=True, probes=probes(update_status=lambda: status))
    assert any("registrada por outra instalação" in line for line in report["RumbaPy"].extra)


def test_update_failure_is_an_item_and_github_is_not_queried_again() -> None:
    """``python -m rumbapy doctor`` sem rede (A.12 c): a falha é item do relatório, que não diz "Tudo certo.", e o GitHub
    não é consultado uma 2ª vez (cada rodada sem rede pode levar ~26 s)."""
    from rumbapy.errors import UpdateCheckFailed

    calls: list[int] = []
    failure = UpdateCheckFailed.make(motivo=f"info/refs: conexão recusada por {HOST}")
    report = _doctor.collect(probes=probes(remote_sha=lambda: calls.append(1) or "a" * 40), update_failure=failure)
    assert calls == []
    assert not report.ok
    item = report[_doctor.UPDATE_ITEM]
    assert item.status == "falha" and item.detail.startswith("Não consegui verificar a versão mais recente no GitHub")
    assert HOST not in item.detail
    assert report["GitHub"].status == "aviso" and "não consultado de novo" in report["GitHub"].detail
    text = str(report)
    assert "Tudo certo." not in text and text.endswith("Há itens com FALHA: veja acima como resolver.")
    names = [c.name for c in report.checks]
    assert names.index("RumbaPy") < names.index(_doctor.UPDATE_ITEM) < names.index("GitHub")


def test_broken_item_never_breaks_report() -> None:
    def boom(letter: str) -> dict:
        raise RuntimeError("x")

    report = _doctor.collect(probes=probes(probe_worker=boom))
    assert report["Processos auxiliares"].status == "aviso" and report.ok


def test_doctor_prints_and_returns(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = probes()
    monkeypatch.setattr(_doctor, "Probes", lambda: fake)
    report = _doctor.doctor(verbose=True)
    out = capsys.readouterr().out
    assert out.strip() == report.to_text(verbose=True)
    assert "build_id: 9f2c1e0b7a4d3c21" in out                           # verbose mostra os extras


def test_doctor_reads_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """O padrão real não sobe worker nem cria a chave: ``probe`` sem registro devolve "nenhum"."""
    monkeypatch.setenv("RUMBAPY_HOME", str(tmp_path))
    from rumbapy import _bridge

    info = _bridge.Bridge().probe("D")
    assert info == {"letter": "D", "registered": False, "alive": False}
    assert not (tmp_path / "authkey.bin").exists() and not (tmp_path / "run").exists()


# ------------------------------------------------------------- o estado do update é lido uma vez (H.2)

def test_update_status_read_once_per_report() -> None:
    calls: list[int] = []

    def status() -> dict[str, Any]:
        calls.append(1)
        return {"mode": "archive", "commit": "b" * 40, "error": None, "last_check": None}

    report = _doctor.collect(probes=probes(update_status=status))   # GitHub consultado (RUMBAPY_TEST_GITHUB)
    assert len(calls) == 1
    assert report["GitHub"].status == "aviso"                        # o commit memoizado chega ao item GitHub
    _doctor.collect(probes=probes(update_status=status))
    assert len(calls) == 2                                           # memo por relatório, não global


def test_update_status_failure_memoized_and_contained() -> None:
    calls: list[int] = []

    def status() -> dict[str, Any]:
        calls.append(1)
        raise OSError("estado ilegível")

    report = _doctor.collect(probes=probes(update_status=status))
    assert len(calls) == 1
    assert report["RumbaPy"].status == "aviso"
    assert report["GitHub"].status == "ok"


def test_dev_doctor_does_not_repeat_auto_update_notice(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """``RUMBAPY_DEV=1 python -m rumbapy doctor``: o aviso sai uma vez (do ``ensure_latest("cli")``), não três."""
    from rumbapy import _update

    monkeypatch.setenv("RUMBAPY_DEV", "1")
    monkeypatch.delenv("RUMBAPY_QUIET", raising=False)
    warnings: list[str] = []
    real_log_line = _update._bootstrap.log_line

    def log_line(text: str, level: str = "INFO") -> None:
        if level == "WARNING" and "AUTO-UPDATE DESLIGADO" in text:
            warnings.append(text)
        real_log_line(text, level)

    monkeypatch.setattr(_update._bootstrap, "log_line", log_line)
    assert _update.install_mode() == ("dev", None)                  # o que o ensure_latest("cli") faz uma vez
    report = _doctor.collect(probes=probes(update_status=_doctor._update_status))
    err = capsys.readouterr().err
    assert err.count("AUTO-UPDATE DESLIGADO") == 1
    assert len(warnings) == 1
    assert report["RumbaPy"].status == "ok" and "instalação dev" in report["RumbaPy"].detail


def test_update_status_outside_dev_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    from rumbapy import _update

    monkeypatch.delenv("RUMBAPY_DEV", raising=False)
    sentinel = {"mode": "archive", "commit": "c" * 40, "error": None, "last_check": None}
    monkeypatch.setattr(_update, "status", lambda: sentinel)
    assert _doctor._update_status() is sentinel
