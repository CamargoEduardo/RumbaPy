"""Máscara de conteúdo oculto (D.6): campos não exibidos e spans ``secret`` nunca atravessam o "pipe"."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy._worker.mask import MaskState, enumerate_fields, hidden_in_span, is_hidden
from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen, FakeSession
from tests.fakes.fake_backend import InProcessBackend
from tests.fakes import scenarios
from rumbapy import errors

import logging
import time

import pytest

PASSWORD = "senha"
FORM = FakeScreen(name="form", lines=["TELA COM SENHA"] + [""] * 23, fields=[
    {"row": 5, "col": 10, "length": 8, "name": "free"},
    {"row": 7, "col": 10, "length": 8, "name": "pwd", "hidden": True},
    {"row": 9, "col": 10, "length": 20, "name": "msg", "protected": True},
])
AFTER = FakeScreen(name="after", lines=["TELA NOVA"] + [""] * 23, fields=[
    {"row": 5, "col": 10, "length": 8, "name": "free", "text": "VISIVEL"},
    {"row": 7, "col": 10, "length": 8, "name": "x"},
])


def make(on_aid=None, **session_kw: object) -> tuple[FakeEhllapi, FakeSession, InProcessBackend]:
    fake = FakeEhllapi()
    session = fake.add_session("D")
    for name, value in session_kw.items():
        setattr(session, name, value)
    session.load(FORM)
    session.on_aid = on_aid
    backend = InProcessBackend(fake, "D")
    backend.bind_instance(backend.connect().hwnd)
    return fake, session, backend


def assert_hidden_everywhere(backend: InProcessBackend, secret: str) -> None:
    screen = backend.snapshot(fields=True)
    assert secret not in screen.text
    assert all(secret not in f.text for f in screen.fields)
    assert secret not in backend.read(1, 1, 1920).text
    assert secret not in backend.read(7, 10, 8).text
    assert backend.read_field(7, 12).text == " " * 8
    assert secret not in backend.traffic(replies_only=True)


def test_hidden_field_never_crosses_before_and_after_enter() -> None:
    _fake, session, backend = make(on_aid=lambda s, aid, fields: None)      # o host não muda a tela
    backend.write(7, 10, PASSWORD, secret=False)                           # sem secret: o campo é oculto
    assert session.field_text(session.fields[1]).startswith(PASSWORD)      # está mesmo no PS
    assert_hidden_everywhere(backend, PASSWORD)
    backend.send(["@E"], secret=False)
    assert_hidden_everywhere(backend, PASSWORD)


def test_typed_password_with_send_keys_is_hidden() -> None:
    fake = scenarios.cics_login()
    backend = InProcessBackend(fake, "D")
    backend.bind_instance(backend.connect().hwnd)
    backend.write(22, 56, "C", secret=False)
    backend.send(["@E"], secret=False)                                     # -> signon
    backend.set_cursor(12, 21)
    backend.send(["A000000", "@T", scenarios.VALID_PASSWORD], secret=False)  # "A000000<Tab>senha" sem secret
    session = fake.sessions["D"]
    assert scenarios.VALID_PASSWORD in session.text()
    screen = backend.snapshot()
    assert scenarios.VALID_PASSWORD not in screen.text and "A000000" in screen.text
    assert scenarios.VALID_PASSWORD not in backend.read(13, 1, 80).text
    backend.send(["@E"], secret=False)
    assert "Signon is complete" in backend.snapshot().text
    assert scenarios.VALID_PASSWORD not in backend.traffic(replies_only=True)


def test_secret_spans_fall_after_the_host_answers() -> None:
    _fake, _session, backend = make(on_aid=lambda s, aid, fields: s.load(AFTER), response_delay=0.3)
    backend.write(5, 10, "SEGRED", secret=True)                            # campo visível, mas secret
    assert backend.read(5, 10, 8).text == " " * 8
    assert "SEGRED" not in backend.snapshot().text
    backend.send(["@E"], secret=False)
    assert backend.read(5, 10, 8).text == " " * 8                          # host ainda não respondeu
    deadline = time.monotonic() + 3
    while backend.read(1, 1, 9).text != "TELA NOVA" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert backend.read(5, 10, 8).text == "VISIVEL "                       # tela nova: o span secreto caiu
    assert backend.mask.secret == []


def test_spontaneous_host_write_reenumerates() -> None:
    _fake, session, backend = make()
    backend.snapshot()
    before = backend.mask.enumerations
    spontaneous = FakeScreen(name="spont", lines=[""] * 24, fields=[
        {"row": 5, "col": 10, "length": 8, "name": "spont", "hidden": True, "text": "SPONTANE"},
    ])
    session.load(spontaneous)                                              # o host escreveu sem op nossa
    screen = backend.snapshot()
    assert "SPONTANE" not in screen.text
    assert backend.mask.enumerations == before + 1


def test_own_mutation_does_not_reenumerate() -> None:
    _fake, _session, backend = make()
    backend.snapshot()
    before = backend.mask.enumerations
    backend.write(5, 10, "abc", secret=False)
    backend.snapshot()                                                     # só atualiza last_hash
    assert backend.mask.enumerations == before
    backend.send(["xy"], secret=False)
    backend.snapshot()
    assert backend.mask.enumerations == before


def test_hidden_unknown_when_enumeration_fails() -> None:
    fake, _session, backend = make()
    fake.rc_override = {"WD_FindFieldPosition": [9]}
    screen = backend.snapshot(fields=True)
    assert screen.hidden_unknown and screen.fields is None
    again = backend.snapshot()
    assert not again.hidden_unknown                                        # tenta de novo na próxima leitura
    fake.rc_override = {"WD_QueryFieldAttribute": [9]}
    backend.engine.mask.valid = False
    assert backend.snapshot().hidden_unknown


def test_hidden_field_in_read_field_even_without_enumeration() -> None:
    fake, session, backend = make()
    session.buffer[session.fields[1].start - 1:session.fields[1].start - 1 + 5] = PASSWORD.encode()
    fake.rc_override = {"WD_FindFieldPosition": [0, 9]}                    # read_field ok, enumeração falha
    assert backend.read_field(7, 10).text == " " * 8


def test_apply_wraps_around_the_end_of_the_screen() -> None:
    mask = MaskState(size=20)
    mask.hidden = [(18, 5)]                                                # 18, 19, 20, 1, 2
    raw = bytes(range(65, 85))                                             # "ABCDEFGHIJKLMNOPQRST"
    assert mask.apply(raw, 1) == b"  CDEFGHIJKLMNOPQ   "
    assert mask.apply(raw[16:], 17) == b"Q   "
    mask.hidden = []
    mask.add_secret(3, 2)
    assert mask.apply(raw[:5], 1) == b"AB  E"


def test_enumerate_unformatted_and_attributes() -> None:
    fake = FakeEhllapi()
    fake.add_session("D").load(FakeScreen(lines=[""] * 24))
    backend = InProcessBackend(fake, "D")
    backend.connect()
    assert enumerate_fields(backend.hll) == []
    assert is_hidden(0xCC) and is_hidden(0xED) and not is_hidden(0xC5) and not is_hidden(0xE8)


def _typed_in_hidden_field_without_secret() -> tuple[FakeEhllapi, FakeSession, InProcessBackend]:
    fake, session, backend = make(on_aid=lambda s, aid, fields: None)
    backend.set_cursor(7, 10)
    backend.send([PASSWORD], secret=False)                                 # send sem secret num campo oculto
    assert session.field_text(session.fields[1]).startswith(PASSWORD)
    return fake, session, backend


def test_failed_reenumeration_keeps_the_known_hidden_spans() -> None:
    fake, _session, backend = _typed_in_hidden_field_without_secret()
    assert PASSWORD not in backend.snapshot().text                         # enumeração boa: spans conhecidos
    assert backend.mask.last_hidden
    backend.mask.valid = False
    fake.rc_override = {"WD_FindFieldPosition": [9]}                       # a reenumeração falha
    screen = backend.snapshot()
    assert screen.hidden_unknown and PASSWORD not in screen.text
    assert PASSWORD not in backend.traffic(replies_only=True)


def test_read_with_unknown_mask_checks_the_fields_of_the_span() -> None:
    fake, _session, backend = _typed_in_hidden_field_without_secret()
    backend.mask.last_hidden = []                                          # nada conhecido de antes
    fake.rc_override = {"WD_FindFieldPosition": [9]}                       # só a enumeração falha
    assert backend.read(7, 10, 8).text == " " * 8
    assert backend.mask.hidden_unknown
    assert PASSWORD not in backend.traffic(replies_only=True)


def test_read_and_snapshot_refused_when_the_mask_cannot_be_checked() -> None:
    fake, _session, backend = _typed_in_hidden_field_without_secret()
    backend.mask.last_hidden = []
    backend.mask.valid = False
    backend.mask.hidden = None
    fake.rc_override = {"WD_QueryFieldAttribute": [9] * 50}                # enumeração e conferência falham
    with pytest.raises(errors.HllapiError):
        backend.read(7, 10, 8)
    with pytest.raises(errors.HllapiError):
        backend.snapshot()
    assert PASSWORD not in backend.traffic(replies_only=True)
    fake.rc_override = {}
    assert PASSWORD not in backend.read(7, 10, 8).text                     # voltou a enumerar



def test_refusal_message_names_the_real_cause() -> None:
    fake, _session, backend = _typed_in_hidden_field_without_secret()
    backend.mask.last_hidden = []
    backend.mask.valid = False
    backend.mask.hidden = None
    fake.rc_override = {"WD_QueryFieldAttribute": [7] * 50}                # rc 7: não é "posição inválida" do usuário
    with pytest.raises(errors.HllapiError) as info:
        backend.read(1, 1, 8)
    assert type(info.value) is errors.HllapiError
    assert "a leitura foi recusada para não expor conteúdo oculto" in info.value.message
    assert info.value.rc == 7 and info.value.function == "WD_QueryFieldAttribute"


def test_hidden_in_span_unformatted_and_wrap() -> None:
    fake = FakeEhllapi()
    fake.add_session("D").load(FakeScreen(lines=[""] * 24))
    backend = InProcessBackend(fake, "D")
    backend.connect()
    assert hidden_in_span(backend.hll, 1, 1920, 1920) == ("WD_QueryFieldAttribute", 0, [])
    _fake, session, backend = make()
    pwd = session.fields[1]
    _func, rc, spans = hidden_in_span(backend.hll, 1, 1920, 1920)
    covered = {(start - 1 + k) % 1920 + 1 for start, length in spans for k in range(length)}
    assert rc == 0 and set(range(pwd.start, pwd.start + pwd.length)) <= covered
    free = session.fields[0]
    assert not covered & set(range(free.start, free.start + free.length))
    assert len(backend.fake.calls) < 200                                    # pula campo a campo, não byte a byte
    _func, rc, spans = hidden_in_span(backend.hll, 1900, 100, 1920)        # dá a volta, não toca o campo oculto
    assert rc == 0 and spans == []


def test_write_into_hidden_field_logs_no_length(caplog: pytest.LogCaptureFixture) -> None:
    _fake, _session, backend = make()
    with caplog.at_level(logging.INFO, logger="rumbapy._worker.dispatch"):
        backend.write(7, 10, PASSWORD, secret=False)                       # o Engine promove a secreto
    line = next(r.getMessage() for r in caplog.records if "op=write" in r.getMessage())
    assert "len=" not in line and "secret" in line
