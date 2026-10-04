"""``_bridge.handshake``: ordem de decisão da C.3 (o dono vem antes do build, R1/E2) e o ``hello_ack``."""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

from tests.unit.client.bridge.fakes import BUILD, FakeChannel, hello
from rumbapy.errors import ChannelHijacked, ProtocolError, TerminalInUse, UpdateRequired
from rumbapy import _bridge, _buildid, _protocol

import os

import pytest

WELCOME = {"t": "welcome", "mode": "owner"}


def run(channel: FakeChannel, *, mode: str = "owner", build: str = BUILD, pkgdir: Path | None = None,
        expected_pid: int | None = 4321) -> tuple[str, dict]:
    return _bridge.handshake(channel, letter="D", mode=mode, build=build, pkgdir=pkgdir or _bridge.PKGDIR,
                             expected_pid=expected_pid)


def test_welcome_and_hello_ack_contents() -> None:
    ch = FakeChannel([hello(), WELCOME])
    outcome, got = run(ch)
    assert outcome == "ok" and got["pid"] == 4321
    (ack,) = ch.sent
    assert tuple(ack) == _protocol.HELLO_ACK_KEYS
    assert ack["t"] == "hello_ack" and ack["mode"] == "owner" and ack["build_id"] == BUILD
    assert ack["client_pid"] == os.getpid()                                # PID real (A.9)
    assert ack["client_version"] == "2.0.0" and isinstance(ack["script"], str) and ack["script"]


def test_newer_protocol_requires_update_and_older_is_protocol_error() -> None:
    with pytest.raises(UpdateRequired):
        run(FakeChannel([hello(protocol=3)]))
    with pytest.raises(ProtocolError):
        run(FakeChannel([hello(protocol=1)]))
    with pytest.raises(ProtocolError):
        run(FakeChannel([None]))
    with pytest.raises(ProtocolError):
        run(FakeChannel([{"t": "auth"}]))


def test_hello_pid_must_match_registry() -> None:
    with pytest.raises(ChannelHijacked):
        run(FakeChannel([hello(pid=999)]), expected_pid=4321)


def test_stopping_comes_before_owner_and_build() -> None:
    ch = FakeChannel([hello(state="stopping", owner={"pid": 1, "script": "x.py", "since": "s"}, build_id="old")])
    assert run(ch)[0] == "stopping" and ch.sent == []


def test_other_owner_is_terminal_in_use_before_build() -> None:
    owner = {"pid": 5555, "script": "lote.py", "since": "2026-10-04T09:00:00-03:00"}
    ch = FakeChannel([hello(owner=owner, build_id="velho")])
    with pytest.raises(TerminalInUse) as info:
        run(ch)
    assert ch.sent == []                                                    # nada de hello_ack; o worker fica
    text = str(info.value)
    assert "5555" in text and "lote.py" in text and "09:00:00" in text
    assert "rumbapy.close('D')" in text


def test_own_pid_as_owner_reopens_the_lease() -> None:
    ch = FakeChannel([hello(owner={"pid": os.getpid(), "script": "eu.py", "since": "s"}), WELCOME])
    assert run(ch)[0] == "ok"


def test_old_build_without_owner(tmp_path: Path) -> None:
    disk = _buildid.compute(_bridge.PKGDIR)
    ch = FakeChannel([hello(build_id="antigo")])
    assert run(ch, build=disk)[0] == "old_build" and ch.sent == []


def test_new_build_on_disk_is_update_required() -> None:
    ch = FakeChannel([hello(build_id="antigo")])
    with pytest.raises(UpdateRequired) as info:
        run(ch, build="nao-bate-com-o-disco")
    assert "Outro processo atualizou o RumbaPy" in info.value.message


def test_admin_ignores_build_and_owner() -> None:
    ch = FakeChannel([hello(build_id="outro", owner={"pid": 5555, "script": "x", "since": "s"}),
                      {"t": "welcome", "mode": "admin"}])
    assert run(ch, mode="admin")[0] == "ok"
    assert ch.sent[0]["mode"] == "admin"


@pytest.mark.parametrize("reason, expected", [("build", "old_build"), ("stopping", "stopping")])
def test_rejects(reason: str, expected: str) -> None:
    assert run(FakeChannel([hello(), {"t": "reject", "reason": reason, "owner": None}]))[0] == expected


def test_reject_busy_is_terminal_in_use() -> None:
    owner = {"pid": 7777, "script": "outro.py", "since": "s"}
    with pytest.raises(TerminalInUse) as info:
        run(FakeChannel([hello(), {"t": "reject", "reason": "busy", "owner": owner}]))
    assert "7777" in info.value.message


def test_no_answer_to_ack_is_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        run(FakeChannel([hello()]))
