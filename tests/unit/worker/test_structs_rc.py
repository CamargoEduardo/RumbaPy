"""F.3 (estruturas com os bytes ao vivo) e F.6 (rc -> exceção, percorrendo todo rc de RUMBA_EHLLAPI §9)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy._worker import rc as rcmod, structs
from rumbapy import errors

import pytest

LIVE_ENTRY = bytes.fromhex("44" + "20" * 11 + "48" + "20" + "8007")
LIVE_STATUS = bytes.fromhex("44202020" + "44" + "00" + "20" * 6 + "44" + "00" + "1800" + "5000" + "0000")

# todos os rc da tabela §9 de docs/RUMBA_EHLLAPI.md
ALL_RC = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 31, 32, 34, 35, 36,
          37, 38, 39, 40, 41, 42, 43, 301, 302, 305, 308, 310, 311, 9998, 9999]

E = errors
# F.6, escrita à mão (independente de rc.TABLE): função -> (sucesso, tolerados, {rc: classe})
SPEC = {
    "WD_ConnectPS": ({0, 4, 5}, {1}, {2: E.ParameterError, 11: E.TerminalInUse}),
    "WD_DisconnectPS": ({0}, {1}, {}),
    "WD_SetSessionParamEx": ({0}, {2}, {}),
    "WD_SendKey": ({0}, set(), {1: E.NotConnected, 2: E.ParameterError, 4: E.HostBusy, 5: E.KeyboardLocked,
                                20: E.UndefinedKey}),
    "WD_CopyPS": ({0}, {4, 5}, {1: E.NotConnected}),
    "WD_CopyOIA": ({0}, {4, 5}, {1: E.NotConnected}),
    "WD_CopyPSToString": ({0}, {4, 5}, {1: E.NotConnected, 2: E.ParameterError, 7: E.InvalidPosition}),
    "WD_CopyStringToPS": ({0}, set(), {1: E.NotConnected, 2: E.ParameterError, 5: E.KeyboardLocked,
                                       6: E.Truncated, 7: E.InvalidPosition}),
    "WD_CopyFieldToString": ({0}, set(), {1: E.NotConnected, 2: E.ParameterError, 6: E.Truncated,
                                          7: E.InvalidPosition, 24: E.FieldNotFound}),
    "WD_QueryCursorLocation": ({0}, set(), {1: E.NotConnected}),
    "WD_SetCursor": ({0}, set(), {1: E.NotConnected, 4: E.HostBusy, 7: E.InvalidPosition}),
    "WD_QueryFieldAttribute": ({0}, {24}, {1: E.NotConnected, 7: E.InvalidPosition}),
    "WD_FindFieldPosition": ({0}, {24, 28}, {1: E.NotConnected, 2: E.ParameterError, 7: E.InvalidPosition}),
    "WD_FindFieldLength": ({0}, {24, 28}, {1: E.NotConnected, 2: E.ParameterError, 7: E.InvalidPosition}),
    "WD_QuerySessions": ({0}, set(), {2: E.ParameterError, 9: E.HllapiSystemError}),
    "WD_QuerySessionStatus": ({0}, set(), {1: E.TerminalNotOpen, 2: E.ParameterError}),
}
GENERIC = {12: E.SessionStopped, 36: E.HostConnectionLost, 9: E.HllapiSystemError}
CTX = {"L": "D", "r": 3, "c": 4, "rows": 24, "cols": 80, "n": 9, "m": 8}


def test_session_entry_live_bytes() -> None:
    entry = structs.parse_session_entry(LIVE_ENTRY)
    assert (entry.letter, entry.long_name, entry.type, entry.ps_size) == ("D", "", "H", 1920)
    assert structs.parse_sessions(LIVE_ENTRY * 2, 2) == [entry, entry]
    assert structs.parse_sessions(LIVE_ENTRY, 5) == [entry]          # nunca lê além do buffer
    with pytest.raises(ValueError):
        structs.parse_session_entry(LIVE_ENTRY[:10])


def test_session_status_live_bytes() -> None:
    status = structs.parse_status(LIVE_STATUS)
    assert (status.letter, status.long_name, status.type, status.rows, status.cols, status.host_cp) == \
        ("D", "D", "D", 24, 80, 0)
    assert status.eab is False
    assert structs.parse_status(LIVE_STATUS[:13] + b"\x80" + LIVE_STATUS[14:]).eab is True
    ims = structs.parse_status(LIVE_STATUS[:14] + (32).to_bytes(2, "little") + LIVE_STATUS[16:])
    assert (ims.rows, ims.cols) == (32, 80)


def test_table_covers_every_bound_function() -> None:
    from rumbapy._worker.hllapi import BOUND_FUNCTIONS

    assert set(rcmod.TABLE) == set(BOUND_FUNCTIONS) - {"WD_GetSessionHWND"}


@pytest.mark.parametrize("func", sorted(SPEC))
@pytest.mark.parametrize("rc", ALL_RC)
def test_every_rc_of_every_function(func: str, rc: int) -> None:
    success, tolerated, errs = SPEC[func]
    exc = rcmod.error_for(func, rc, CTX)
    if rc in success:
        assert rcmod.classify(func, rc) == "ok" and exc is None
        return
    if rc in tolerated:
        assert rcmod.classify(func, rc) == "tolerated" and exc is None
        rcmod.check(func, rc, CTX)                                     # não levanta
        return
    assert rcmod.classify(func, rc) == "error"
    expected = errs.get(rc) or GENERIC.get(rc) or E.HllapiError
    assert type(exc) is expected, (func, rc, type(exc))
    assert exc.rc == rc and exc.function == func
    if expected is E.HllapiError:
        assert exc.message == f"rc {rc}"
    with pytest.raises(expected):
        rcmod.check(func, rc, CTX)


@pytest.mark.parametrize("rc", ALL_RC)
def test_reset_system_accepts_anything(rc: int) -> None:
    assert rcmod.classify("WD_ResetSystem", rc) == "ok"
    rcmod.check("WD_ResetSystem", rc)


def test_messages_and_delivery() -> None:
    assert str(rcmod.error_for("WD_CopyPSToString", 7, CTX).message) == "(3,4) está fora da tela 24x80."
    assert rcmod.error_for("WD_CopyStringToPS", 5, CTX).delivery == "none"         # nada copiado
    assert rcmod.error_for("WD_CopyStringToPS", 6, CTX).delivery == "partial"      # parte copiada
    trunc = rcmod.error_for("WD_CopyStringToPS", 6, CTX)
    assert trunc.message == "O texto (9) não cabe no campo (8) em (3,4)."
    secret = rcmod.error_for("WD_CopyStringToPS", 6, {**CTX, "secret": True})
    assert secret.message == "O valor secreto é maior que o campo em (3,4)." and "9" not in secret.message
    assert rcmod.error_for("WD_ConnectPS", 11, CTX).message == "Outro programa HLLAPI está conectado ao D."
    assert rcmod.error_for("WD_SendKey", 9, {**CTX, "delivery": "unknown"}).delivery == "unknown"
    assert rcmod.error_for("WD_SendKey", 1, CTX).message == "Sem conexão HLLAPI com D."
    assert rcmod.error_for("WD_QuerySessionStatus", 1, CTX).hint == "use rumbapy.open('D')."
    assert rcmod.error_for("WD_CopyPS", 12, CTX).message == "A sessão do emulador D foi encerrada (janela fechada?)."
    assert rcmod.error_for("WD_CopyPS", 9, CTX).message == "Erro interno do RUMBA em WD_CopyPS (rc 9)."
    assert rcmod.error_for("WD_SetCursor", 4, CTX).delivery == "none"
    details = rcmod.error_for("WD_SendKey", 5, {**CTX, "details": {"chunks_sent": 1}, "delivery": "partial"})
    assert details.details == {"chunks_sent": 1} and details.delivery == "partial"
    wire = errors.to_wire(details)
    assert errors.from_wire(wire).delivery == "partial"
