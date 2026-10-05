"""Engine + Dispatcher em processo (D.5, C.4): connect, write (C9), send (delivery), validações, instância, erros."""
# ruff: noqa: I001
from __future__ import annotations

from tests.fakes.fake_ehllapi import FakeEhllapi, FakeScreen
from rumbapy._worker.dispatch import Dispatcher, JobCtx
from tests.fakes.fake_backend import InProcessBackend
from rumbapy._protocol import OPS
from tests.fakes import scenarios
from rumbapy import errors

import logging

import pytest

FORM = FakeScreen(name="form", lines=["TITULO DA TELA"] + [""] * 23, fields=[
    {"row": 5, "col": 10, "length": 8, "name": "free"},
    {"row": 6, "col": 10, "length": 10, "name": "prot", "protected": True, "text": "SOMENTE"},
    {"row": 7, "col": 10, "length": 8, "name": "pwd", "hidden": True},
    {"row": 8, "col": 10, "length": 20, "name": "other"},
])


def form_backend(**kw: object) -> tuple[FakeEhllapi, InProcessBackend]:
    fake = FakeEhllapi()
    session = fake.add_session("D", hwnd=0x1000, pid=4242)
    session.load(FORM)
    backend = InProcessBackend(fake, "D", **kw)  # type: ignore[arg-type]
    result = backend.connect()
    backend.bind_instance(result.hwnd)
    return fake, backend


def calls(fake: FakeEhllapi, name: str) -> int:
    return sum(1 for c in fake.calls if c[0] == name)


# ------------------------------------------------------------------------------------------- connect
def test_connect_waits_for_the_session_then_applies_noreset() -> None:
    fake = FakeEhllapi()
    session = fake.add_session("D", connect_delay=11)
    session.load(FORM)
    backend = InProcessBackend(fake, "D")
    first = backend.connect()
    assert (first.connected, first.rc) == (False, 1)                 # a TN3270 ainda conecta (ao vivo ~11 s)
    session.registered_at -= 11
    second = backend.connect()
    assert second.connected and second.rc == 0 and not second.already
    assert (second.rows, second.cols, second.hwnd, second.hwnd_pid) == (24, 80, 0x1000, 4242)
    assert "NORESET" in fake.params and "AUTORESET" not in fake.params
    before = calls(fake, "WD_ConnectPS")
    third = backend.connect()
    assert third.already and calls(fake, "WD_ConnectPS") == before


def test_connect_errors() -> None:
    fake = FakeEhllapi()
    fake.add_session("D")
    backend = InProcessBackend(fake, "D")
    fake.rc_override = {"WD_ConnectPS": [11, 2, 9]}
    with pytest.raises(errors.TerminalInUse) as info:
        backend.connect()
    assert info.value.rc == 11 and "Outro programa HLLAPI" in info.value.message
    with pytest.raises(errors.ParameterError):
        backend.connect()
    with pytest.raises(errors.HllapiSystemError):
        backend.connect()
    fake.rc_override = {"WD_SetSessionParamEx": [2]}
    result = backend.connect()                                        # NORESET recusado: só WARNING
    assert result.connected


def test_connect_rejects_other_letters() -> None:
    backend = InProcessBackend(scenarios.two_letters(), "D")
    with pytest.raises(errors.ParameterError):
        backend._call("connect", {"letter": "Z"})


def test_ims_geometry_is_used_for_validation() -> None:
    backend = InProcessBackend(scenarios.ims_login(), "A")
    result = backend.connect()
    assert (result.rows, result.cols) == (32, 80)
    backend.bind_instance(result.hwnd)
    assert len(backend.read(32, 1, 80).text) == 80
    with pytest.raises(errors.InvalidPosition):
        backend.read(33, 1, 1)


# ------------------------------------------------------------------------------------------- validações
def test_ops_need_connection_and_valid_positions() -> None:
    backend = InProcessBackend(scenarios.cics_login(), "D")
    with pytest.raises(errors.NotConnected):
        backend.read(1, 1, 5)
    backend.bind_instance(backend.connect().hwnd)
    for args in ((25, 1, 1), (0, 1, 1), (1, 81, 1), (24, 80, 2)):
        with pytest.raises(errors.InvalidPosition):
            backend.read(*args)
    with pytest.raises(errors.ParameterError):
        backend.read(1, 1, 0)
    with pytest.raises(errors.InvalidPosition):
        backend.write(25, 1, "x", secret=False)


def test_handlers_match_ops() -> None:
    assert set(Dispatcher.HANDLERS) == set(OPS) - {"ping"}


def test_session_replaced_when_hwnd_differs_or_after_evict() -> None:
    _fake, backend = form_backend()
    backend.read(1, 1, 5)
    backend.bind_instance(0x9999)
    with pytest.raises(errors.SessionReplaced) as info:
        backend.read(1, 1, 5)
    assert info.value.delivery == "none"
    backend.bind_instance(0x1000)
    assert backend.evict("close")["was_connected"] is True
    with pytest.raises(errors.SessionReplaced):
        backend.read(1, 1, 5)
    backend.bind_instance(None)
    with pytest.raises(errors.NotConnected):
        backend.read(1, 1, 5)


# ------------------------------------------------------------------------------------------- write (C9)
def test_write_protected_field_never_copies() -> None:
    fake, backend = form_backend()
    with pytest.raises(errors.ProtectedField) as info:
        backend.write(6, 12, "X", secret=False)
    assert info.value.delivery == "none" and info.value.message == "(6,12) é um campo protegido; não dá para escrever."
    with pytest.raises(errors.ProtectedField):
        backend.write(5, 9, "X", secret=False)                      # byte de atributo
    assert calls(fake, "WD_CopyStringToPS") == 0


def test_write_too_long_is_truncated_before_copying() -> None:
    fake, backend = form_backend()
    with pytest.raises(errors.Truncated) as info:
        backend.write(5, 10, "123456789", secret=False)
    assert info.value.delivery == "none"
    assert info.value.message == "O texto (9) não cabe no campo (8) em (5,10)."
    with pytest.raises(errors.Truncated) as info:
        backend.write(5, 15, "abcd", secret=False)
    assert info.value.message == "O texto (4) não cabe no campo (3) em (5,15)."
    with pytest.raises(errors.Truncated) as info:
        backend.write(5, 10, "123456789", secret=True)
    assert info.value.message == "O valor secreto é maior que o campo em (5,10)."
    assert calls(fake, "WD_CopyStringToPS") == 0
    backend.write(5, 15, "abc", secret=False)
    assert fake.sessions["D"].line(5)[9:17] == "     abc"


def test_write_rc_from_the_dll() -> None:
    fake, backend = form_backend()
    fake.rc_override = {"WD_CopyStringToPS": [6, 5, 7, 1, 1]}     # rc 1: reconecta, repete 1x e falha de novo
    with pytest.raises(errors.Truncated) as info:
        backend.write(5, 10, "abc", secret=False)
    assert info.value.delivery == "partial" and info.value.rc == 6
    with pytest.raises(errors.KeyboardLocked) as locked:
        backend.write(5, 10, "abc", secret=False)
    assert locked.value.delivery == "none"
    with pytest.raises(errors.InvalidPosition):
        backend.write(5, 10, "abc", secret=False)
    with pytest.raises(errors.NotConnected):
        backend.write(5, 10, "abc", secret=False)


def test_write_unformatted_screen_limit_is_end_of_screen() -> None:
    fake = FakeEhllapi()
    fake.add_session("D").load(FakeScreen(lines=["x"] * 24))
    backend = InProcessBackend(fake, "D")
    backend.bind_instance(backend.connect().hwnd)
    with pytest.raises(errors.Truncated):
        backend.write(24, 79, "abc", secret=False)
    assert calls(fake, "WD_CopyStringToPS") == 0


# ------------------------------------------------------------------------------------------- send (delivery)
@pytest.mark.parametrize("chunks, forced, delivery, sent", [
    (["@T"], [5], "none", 0),                  # 1º chunk de 1 mnemônico recusado: nada entrou
    (["ab"], [5], "partial", 0),               # 1º chunk com 2 unidades: parte pode ter entrado
    (["ab", "@T"], [0, 5], "partial", 1),      # 2º chunk
    (["@T"], [4], "none", 0),
    (["ab"], [2], "none", 0),
    (["ab", "cd"], [0, 20], "partial", 1),
    (["ab"], [9], "unknown", 0),
    (["@E"], [1, 1], "none", 0),               # rc 1 = sem conexão: recusado antes de enviar (repete 1x e falha)
    (["ab", "@E"], [0, 1], "partial", 1),      # queda no 2º chunk: nunca repete
])
def test_send_delivery_rules(chunks: list[str], forced: list[int], delivery: str, sent: int) -> None:
    fake, backend = form_backend()
    fake.rc_override = {"WD_SendKey": list(forced)}
    with pytest.raises(errors.RumbaError) as info:
        backend.send(chunks, secret=False)
    assert info.value.delivery == delivery
    assert info.value.details == {"chunks_sent": sent}
    assert info.value.function == "WD_SendKey"


def test_send_returns_oia_after_and_counts_aids() -> None:
    fake, backend = form_backend()
    result = backend.send(["abc", "@T"], secret=False)
    assert result.chunks_sent == 2 and len(result.oia_after.raw) == 103
    result = backend.send(["@E"], secret=False)
    assert result.chunks_sent == 1
    assert [aid for aid, _ in fake.sessions["D"].aid_log] == ["ENTER"]
    assert backend.engine.aid_seq == 1
    oia_calls = [i for i, c in enumerate(fake.calls) if c[0] == "WD_CopyOIA"]
    key_calls = [i for i, c in enumerate(fake.calls) if c[0] == "WD_SendKey"]
    assert oia_calls and oia_calls[-1] == key_calls[-1] + 1            # CopyOIA logo após o último chunk


@pytest.mark.parametrize("chunks", [["@E", "abc"], ["abc@E"], ["@E@T"], ["@T", "@E", "@T"]])
def test_send_aid_only_as_the_whole_last_chunk(chunks: list[str]) -> None:
    fake, backend = form_backend()
    with pytest.raises(errors.ParameterError):
        backend.send(chunks, secret=False)
    assert calls(fake, "WD_SendKey") == 0


def test_send_limits() -> None:
    fake, backend = form_backend()
    with pytest.raises(errors.ParameterError):
        backend.send(["a" * 129], secret=False)
    with pytest.raises(errors.ParameterError):
        backend.send(["a"] * 65, secret=False)
    with pytest.raises(errors.UndefinedKey):
        backend.send(["@"], secret=False)
    with pytest.raises(errors.ParameterError):
        backend.send([], secret=False)
    assert calls(fake, "WD_SendKey") == 0


def test_set_cursor() -> None:
    fake, backend = form_backend()
    backend.set_cursor(8, 12)
    assert fake.sessions["D"].cursor == (8 - 1) * 80 + 12
    fake.rc_override = {"WD_SetCursor": [4]}
    with pytest.raises(errors.HostBusy) as info:
        backend.set_cursor(8, 12)
    assert info.value.delivery == "none"


# ------------------------------------------------------------------------------------------- leituras
def test_read_and_nuls() -> None:
    fake, backend = form_backend()
    session = fake.sessions["D"]
    session.buffer[80 * 23:80 * 23 + 4] = b"ab\0\0"
    result = backend.read(24, 1, 4)
    assert result.text == "ab  " and result.nul_at == 2 and result.nul_tail == 2 and not result.busy
    session.buffer[80 * 23:80 * 23 + 4] = b"abcd"
    assert backend.read(24, 1, 4).nul_at == -1
    fake.rc_override = {"WD_CopyPSToString": [4]}
    assert backend.read(1, 1, 6).busy


def test_read_field_and_status_and_sessions() -> None:
    fake, backend = form_backend()
    field = backend.read_field(6, 15)
    assert field.start == (6, 10) and field.length == 10 and field.text == "SOMENTE   " and field.attr & 0x20
    status = backend.status()
    assert (status.letter, status.rows, status.cols) == ("D", 24, 80)
    assert [e.letter for e in backend.sessions()] == ["D"]
    unformatted = FakeEhllapi()
    unformatted.add_session("D").load(FakeScreen(lines=[""] * 24))
    b2 = InProcessBackend(unformatted, "D")
    b2.bind_instance(b2.connect().hwnd)
    with pytest.raises(errors.FieldNotFound):
        b2.read_field(1, 1)
    backend.disconnect()
    assert not backend.engine.connected


def test_snapshot_fields_and_geometry() -> None:
    _fake, backend = form_backend()
    screen = backend.snapshot(fields=True)
    assert (screen.rows, screen.cols) == (24, 80) and not screen.hidden_unknown
    assert [f.start for f in screen.fields] == [(5, 10), (6, 10), (7, 10), (8, 10)]
    assert screen.cursor == (5, 10)
    assert backend.snapshot().fields is None


# ------------------------------------------------------------------------------------------- erros internos e guard
def test_unexpected_exception_becomes_internal_error(monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    fake, backend = form_backend()

    secret_message = "SEGREDO-NA-MENSAGEM"

    def boom(*args: object) -> None:
        raise ZeroDivisionError(secret_message)

    monkeypatch.setattr(backend.engine.hll, "copy_ps_to_string", boom)
    caplog.set_level(logging.DEBUG, logger="rumbapy")
    with pytest.raises(errors.RumbaError) as info:
        backend.read(1, 1, 3)
    assert type(info.value) is errors.RumbaError
    assert info.value.message == "Erro interno no processo auxiliar (ZeroDivisionError)."
    assert info.value.details is None                                 # read é I: sem resync
    monkeypatch.setattr(backend.engine.hll, "copy_string_to_ps", boom)
    with pytest.raises(errors.RumbaError) as info:
        backend.write(5, 10, "ab", secret=False)
    assert info.value.details == {"resynced": True}
    assert "SEGREDO" not in caplog.text and "ZeroDivisionError" in caplog.text


def test_guard_violation_is_translated() -> None:
    fake, backend = form_backend(guard="readonly")
    with pytest.raises(errors.GuardViolation) as info:
        backend.send(["@E"], secret=False)
    assert info.value.delivery == "none" and "readonly" in info.value.message
    with pytest.raises(errors.GuardViolation):
        backend.write(5, 10, "ab", secret=False)
    assert backend.snapshot().text                                    # leitura continua liberada
    assert fake.sessions["D"].aid_log == []


def test_logs_never_contain_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="rumbapy")
    fake, backend = form_backend()
    backend.write(5, 10, "TXTLIVRE", secret=False)
    backend.write(7, 10, "SENHA123", secret=True)
    backend.send(["OUTROTXT", "@T"], secret=False)
    backend.read(1, 1, 14)
    backend.snapshot()
    for needle in ("TXTLIVRE", "SENHA123", "OUTROTXT", "TITULO"):
        assert needle not in caplog.text
    assert "keys=[TAB] text_units=omitido" in caplog.text        # a tela tem campo oculto: pode ser senha (D.7)
    assert any("op=read" in r.getMessage() and "sha=" in r.getMessage() for r in caplog.records)   # DEBUG (D.7)
    secret_lines = [r.getMessage() for r in caplog.records if "secret" in r.getMessage()]
    assert secret_lines and all("len=" not in line for line in secret_lines)


def test_dispatcher_direct_ctx() -> None:
    fake, backend = form_backend()
    result = backend.dispatcher.handle("read", {"row": 1, "col": 1, "length": 6}, JobCtx("read", hwnd=0x1000))
    assert result["text"] == "TITULO"
    with pytest.raises(errors.ParameterError):
        backend.dispatcher.handle("ping", {}, JobCtx("ping"))


def test_op_without_hwnd_while_connected_is_a_parameter_error() -> None:
    _fake, backend = form_backend()
    backend.bind_instance(None)                                            # cliente que não fez bind_instance
    with pytest.raises(errors.ParameterError, match="hwnd"):
        backend.read(1, 1, 5)
    backend.bind_instance(0x1000)
    assert backend.read(1, 1, 5).text == "TITUL"


def test_session_without_hwnd_still_works_without_bind() -> None:
    # GetSessionHWND 0 -> None (D.5/F.6): o worker também não tem hwnd, então None == None é a mesma instância
    fake = FakeEhllapi()
    session = fake.add_session("D", hwnd=0x1000, pid=4242)
    session.load(FORM)
    backend = InProcessBackend(fake, "D")
    backend.hll.hwnd = lambda: None  # type: ignore[method-assign]
    result = backend.connect()
    assert result.connected and result.hwnd is None and result.hwnd_pid is None
    backend.bind_instance(result.hwnd)
    assert backend.snapshot().text.startswith("TITULO")
    assert backend.read(1, 1, 6).text == "TITULO"
    assert backend.status() is not None
    with pytest.raises(errors.SessionReplaced):                            # hwnd de outra instância: recusado
        backend.dispatcher.handle("read", {"row": 1, "col": 1, "length": 6}, JobCtx("read", hwnd=0x2000))


@pytest.mark.parametrize("rc", [9, 12, 36])
def test_connect_noreset_generic_rc_is_an_error(rc: int) -> None:
    fake = FakeEhllapi()
    fake.add_session("D").load(FORM)
    backend = InProcessBackend(fake, "D")
    fake.rc_override = {"WD_SetSessionParamEx": [rc]}
    expected = {9: errors.HllapiSystemError, 12: errors.SessionStopped, 36: errors.HostConnectionLost}[rc]
    with pytest.raises(expected):
        backend.connect()
    assert not backend.engine.connected
    assert calls(fake, "WD_DisconnectPS") == 1                             # não fica conectado pela metade


def test_connect_noreset_rc_2_is_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="rumbapy")
    fake = FakeEhllapi()
    fake.add_session("D").load(FORM)
    backend = InProcessBackend(fake, "D")
    fake.rc_override = {"WD_SetSessionParamEx": [2]}
    result = backend.dispatcher.handle("connect", {"letter": "D"}, JobCtx("connect"))
    assert result["connected"] and result["details"] == {"noreset": False}
    assert "SetSessionParamEx(NORESET) rc=2" in caplog.text


def test_snapshot_with_screen_changing_during_both_enumerations_masks_field_by_field() -> None:
    fake, backend = form_backend()
    real_copy = backend.hll.copy_ps
    counter = {"n": 0}

    def changing_copy(size: int) -> tuple[int, bytes]:
        rc, raw = real_copy(size)
        counter["n"] += 1
        # o host escreve na linha 24 a cada CopyPS; o campo oculto (7,10) recebe a senha "por baixo"
        buf = bytearray(raw)
        buf[23 * 80: 23 * 80 + 4] = str(counter["n"]).rjust(4).encode()
        pwd = 6 * 80 + 9
        buf[pwd: pwd + 8] = b"SENHA123"
        return rc, bytes(buf)

    backend.hll.copy_ps = changing_copy  # type: ignore[method-assign]
    backend.engine.mask.last_hidden = []                                   # sem spans antigos: só a conferência salva
    backend.engine.mask.invalidate()
    snap = backend.snapshot()
    assert counter["n"] == 3                                               # bruto + 2 releituras (enumerou 2x)
    assert snap.hidden_unknown is True
    assert "SENHA123" not in snap.text and "SENHA123" not in backend.traffic(replies_only=True)
    assert backend.engine.mask.valid is False                              # o próximo snapshot reenumera


def test_send_log_omits_text_size_after_a_field_move(caplog: pytest.LogCaptureFixture) -> None:
    # "usuario<Tab>senha<Enter>" sem secret: o tamanho do texto depois do <Tab> seria o da senha (D.7)
    caplog.set_level(logging.DEBUG, logger="rumbapy")
    _fake, backend = form_backend()
    backend.send(["A000000@TXYZW", "@E"], secret=False)
    assert "keys=[TAB,ENTER] text_units=omitido" in caplog.text
    assert "text_units=11" not in caplog.text and "XYZW" not in caplog.text


# ------------------------------------------------------------------------------------------- ajustes da Onda 1
PLAIN = FakeScreen(name="plain", lines=["TITULO"] + [""] * 23, fields=[
    {"row": 5, "col": 10, "length": 8, "name": "free"},
])


def test_send_log_counts_text_only_on_a_screen_known_without_hidden_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rumbapy")
    fake = FakeEhllapi()
    fake.add_session("D", hwnd=0x1000, pid=4242).load(PLAIN)
    backend = InProcessBackend(fake, "D")
    backend.bind_instance(backend.connect().hwnd)
    backend.send(["ABC@T"], secret=False)                         # máscara ainda desconhecida: omite
    assert "keys=[TAB] text_units=omitido" in caplog.text
    backend.snapshot()                                            # enumerou: nenhum campo oculto
    backend.send(["DEF@T"], secret=False)
    assert "keys=[TAB] text_units=3" in caplog.text


def test_write_into_hidden_field_never_logs_the_length_even_when_it_fails(
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="rumbapy")
    fake, backend = form_backend()
    fake.rc_override = {"WD_FindFieldLength": [7]}
    with pytest.raises(errors.InvalidPosition):
        backend.write(7, 10, "senha123", secret=False)
    with pytest.raises(errors.ProtectedField):
        backend.write(7, 9, "senha123", secret=False)           # byte de atributo do campo oculto
    lines = [r.getMessage() for r in caplog.records if "op=write" in r.getMessage()]
    assert len(lines) == 2 and all("len=" not in line and "secret" in line for line in lines)
    assert calls(fake, "WD_CopyStringToPS") == 0


def test_disconnect_with_generic_rc_still_ends_the_connection() -> None:
    fake, backend = form_backend()
    fake.rc_override = {"WD_DisconnectPS": [9]}
    with pytest.raises(errors.HllapiSystemError):
        backend.disconnect()
    assert backend.engine.connected is False and backend.engine.last_disconnect == "client"
    result = backend.connect()
    assert result.connected and not result.already                 # conectou de novo, sem "already"


def test_resync_updates_the_hwnd_and_its_pid() -> None:
    fake, backend = form_backend()
    session = fake.sessions["D"]
    session.hwnd, session.pid = 0x2000, 5151
    backend.engine.resync()
    assert (backend.engine.hwnd, backend.engine.hwnd_pid) == (0x2000, 5151)


def test_undefined_key_names_only_the_keys_of_the_chunk() -> None:
    fake, backend = form_backend()
    fake.rc_override = {"WD_SendKey": [20, 20]}
    with pytest.raises(errors.UndefinedKey) as info:
        backend.send(["@3"], secret=False)
    assert info.value.message.startswith("Tecla 'PF3' desconhecida.")
    with pytest.raises(errors.UndefinedKey) as info:
        backend.send(["XY@T"], secret=True)                          # com secret nem o nome da tecla
    assert info.value.message.startswith("Tecla '?' desconhecida.")


# ------------------------------------------------------------------------------------------- queda da conexão pela DLL
# ao vivo (2026-10-04, L9): fechar OUTRO terminal derruba as conexões HLLAPI das demais sessões (rc 1 na próxima chamada)

def test_dll_drop_reconnects_and_repeats_reads(caplog: pytest.LogCaptureFixture) -> None:
    fake, backend = form_backend()
    before = calls(fake, "WD_ConnectPS")
    fake.connected = None                                           # a DLL "esqueceu" a conexão
    assert backend.snapshot().text.startswith("TITULO")
    assert backend.read(1, 1, 6).text == "TITULO"
    assert calls(fake, "WD_ConnectPS") == before + 1                  # uma reconexão
    assert backend.engine.connected and backend.engine.hwnd == 0x1000
    assert "foi refeita; repetindo 1x" in caplog.text


def test_dll_drop_repeats_input_refused_at_the_first_call() -> None:
    fake, backend = form_backend()
    fake.connected = None
    backend.write(5, 10, "ab", secret=False)                          # recusado antes de escrever: repete
    assert fake.sessions["D"].line(5)[9:11] == "ab"
    fake.connected = None
    backend.send(["@E"], secret=False)
    assert len(fake.sessions["D"].aid_log) == 1                       # exatamente uma AID


def test_partial_send_after_a_dll_drop_is_never_repeated() -> None:
    fake, backend = form_backend()
    fake.rc_override = {"WD_SendKey": [0, 1]}                         # o 1º chunk entrou; a queda veio no 2º
    with pytest.raises(errors.NotConnected) as info:
        backend.send(["abc", "@E"], secret=False)
    assert info.value.delivery == "partial"
    assert fake.sessions["D"].aid_log == []                           # a AID nunca foi reenviada


def test_dll_drop_onto_another_instance_is_session_replaced() -> None:
    fake, backend = form_backend()
    fake.connected = None
    fake.sessions["D"].hwnd = 0x2000                                  # a letra agora é de outra janela
    with pytest.raises(errors.SessionReplaced):
        backend.read(1, 1, 6)
    assert backend.engine.connected is False

