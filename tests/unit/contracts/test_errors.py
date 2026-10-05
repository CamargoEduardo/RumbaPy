"""Aceite P-0: hierarquia de exceções (B.7), formato de ``str()``, ida e volta no wire e pickle."""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from rumbapy.errors import RumbaError
from rumbapy import errors

import string
import pickle
import json

import pytest

# B.7: classe -> classe-mãe (dentro da hierarquia)
PARENT = {
    "SetupError": "RumbaError", "UnsupportedPlatform": "SetupError", "RumbaNotInstalled": "SetupError",
    "Python32NotFound": "SetupError", "SandboxViolation": "SetupError",
    "UpdateError": "RumbaError", "UpdateRequired": "UpdateError", "UpdateCheckFailed": "UpdateError",
    "UpdateFailed": "UpdateError",
    "WorkerError": "RumbaError", "WorkerStartFailed": "WorkerError", "WorkerCrashed": "WorkerError",
    "WorkerHung": "WorkerError", "ProtocolError": "WorkerError", "ClientTableFull": "WorkerError",
    "ChannelHijacked": "WorkerError",
    "TerminalError": "RumbaError", "UnknownTerminal": "TerminalError", "TerminalNotOpen": "TerminalError",
    "NotConnected": "TerminalError", "LaunchFailed": "TerminalError", "LaunchTimeout": "TerminalError",
    "MultipleInstances": "TerminalError", "InstanceNotClosable": "TerminalError", "TerminalInUse": "TerminalError",
    "CloseFailed": "TerminalError", "SessionStopped": "TerminalError", "HostConnectionLost": "TerminalError",
    "SessionReplaced": "TerminalError", "SessionUncertain": "TerminalError",
    "HllapiError": "RumbaError", "ParameterError": "HllapiError", "HostBusy": "HllapiError",
    "KeyboardLocked": "HllapiError", "Truncated": "HllapiError", "ProtectedField": "HllapiError",
    "InvalidPosition": "HllapiError", "UndefinedKey": "HllapiError", "FieldNotFound": "HllapiError",
    "HllapiSystemError": "HllapiError", "GuardViolation": "HllapiError",
    "WaitTimeout": "RumbaError", "NoHostResponse": "WaitTimeout", "EncodingError": "RumbaError",
    "RecipeError": "RumbaError", "RecipeNotAvailable": "RecipeError", "UnexpectedScreen": "RecipeError",
    "LogonFailed": "RecipeError", "PasswordExpired": "RecipeError", "InvalidPassword": "RecipeError",
    "HostError": "RecipeError",
}
EXTRA_BASE = {
    "Python32NotFound": ValueError, "UnknownTerminal": ValueError, "LaunchTimeout": TimeoutError,
    "ParameterError": ValueError, "InvalidPosition": IndexError, "UndefinedKey": ValueError,
    "WaitTimeout": TimeoutError, "NoHostResponse": TimeoutError, "EncodingError": ValueError,
}
FIXED_DELIVERY = {
    "SessionReplaced": "none", "SessionUncertain": "none", "ParameterError": "none", "ProtectedField": "none",
    "InvalidPosition": "none", "UndefinedKey": "none", "FieldNotFound": "none", "GuardViolation": "none",
    "NoHostResponse": "unknown", "EncodingError": "none",
}
CLASSES = [getattr(errors, name) for name in errors.__all__]


def test_all_lists_exactly_the_b7_hierarchy() -> None:
    assert set(errors.__all__) == {"RumbaError", *PARENT}
    assert len(errors.__all__) == len(set(errors.__all__))
    assert set(errors.error_classes()) == set(errors.__all__)


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_every_class_is_a_runtime_error_with_stable_code(cls: type[RumbaError]) -> None:
    assert issubclass(cls, RumbaError) and issubclass(cls, RuntimeError)
    assert cls.code == cls.__name__
    assert cls._rumbapy_error is True
    if cls is not RumbaError:
        assert cls.__mro__[1].__name__ == PARENT[cls.__name__]


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_extra_bases_match_the_table(cls: type[RumbaError]) -> None:
    for base in (ValueError, TimeoutError, IndexError):
        assert issubclass(cls, base) == (EXTRA_BASE.get(cls.__name__) is base), (cls.__name__, base.__name__)


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_default_delivery(cls: type[RumbaError]) -> None:
    exc = cls("mensagem")
    assert exc.delivery == FIXED_DELIVERY.get(cls.__name__)
    assert exc.args == ("mensagem",)          # um único argumento (E23)
    assert exc.message == "mensagem"


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
@pytest.mark.parametrize("delivery", [None, "none", "partial", "unknown"])
def test_wire_round_trip(cls: type[RumbaError], delivery: str | None) -> None:
    original = cls("O RUMBA recusou as teclas.", hint="confira a tela", rc=5, function="WD_SendKey",
                   terminal="D", delivery=delivery, details={"chunks_sent": 1})
    wire = errors.to_wire(original)
    assert set(wire) == set(errors.WIRE_KEYS)
    assert "screen" not in wire and "terminal" not in wire
    back = errors.from_wire(json.loads(json.dumps(wire)), terminal="Z")
    assert type(back) is cls
    assert (back.message, back.hint, back.rc, back.function, back.delivery, back.details) == \
        (original.message, original.hint, original.rc, original.function, original.delivery, original.details)
    assert back.terminal == "Z" and back.screen is None
    assert str(back) == str(original).replace("[D]", "[Z]", 1)


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_pickle_round_trip(cls: type[RumbaError]) -> None:
    original = cls("mensagem", hint="dica", rc=7, function="WD_CopyPS", terminal="A", delivery="unknown",
                   details={"pos": 3})
    original.extra_attribute = "fica"
    back = pickle.loads(pickle.dumps(original))
    assert type(back) is cls
    assert back.__dict__ == original.__dict__
    assert str(back) == str(original)
    assert back.args == original.args


def test_str_format_is_the_b7_example() -> None:
    exc = errors.KeyboardLocked.make(terminal="D", rc=5, function="WD_SendKey", delivery="partial")
    assert str(exc) == (
        "[D] O RUMBA recusou as teclas (teclado bloqueado); parte delas pode ter sido digitada.\n"
        "  Como resolver: confira a tela (print(t.screen)) antes de repetir qualquer tecla; se houver 'X -f', "
        "use t.press(Key.RESET).\n"
        "  (WD_SendKey rc=5; entrega: parcial)"
    )


@pytest.mark.parametrize("kwargs, expected", [
    ({}, "msg"),
    ({"terminal": "D"}, "[D] msg"),
    ({"hint": "faça X"}, "msg\n  Como resolver: faça X"),
    ({"rc": 2}, "msg\n  (rc=2)"),
    ({"function": "WD_CopyPS"}, "msg\n  (WD_CopyPS)"),
    ({"delivery": "none"}, "msg\n  (entrega: nenhuma)"),
    ({"delivery": "unknown", "rc": 9, "function": "WD_SendKey"}, "msg\n  (WD_SendKey rc=9; entrega: desconhecida)"),
])
def test_str_lines_only_when_present(kwargs: dict[str, Any], expected: str) -> None:
    assert str(errors.HostBusy("msg", **kwargs)) == expected


def test_timeout_based_errors_keep_the_b7_str_and_are_oserror() -> None:
    exc = errors.WaitTimeout("Esperei 30 s.", terminal="D")
    assert isinstance(exc, TimeoutError) and isinstance(exc, OSError) and isinstance(exc, RuntimeError)
    assert str(exc) == "[D] Esperei 30 s."
    assert exc.errno is None
    with pytest.raises(TimeoutError):
        raise errors.LaunchTimeout.make(perfil="BRAZIL_D", s=90.4, fase=errors.LaunchTimeout.PHASES["register"])


def test_invalid_delivery_is_refused() -> None:
    with pytest.raises(ValueError):
        errors.HostBusy("x", delivery="talvez")  # type: ignore[arg-type]


def test_from_wire_unknown_type_and_garbage_never_raise() -> None:
    exc = errors.from_wire({"type": "NovoErroDoFuturo", "message": "algo", "rc": "x", "delivery": "talvez",
                            "details": [1]}, terminal="D")
    assert type(exc) is RumbaError
    assert exc.message == "algo" and exc.rc is None and exc.delivery is None
    assert exc.details == {"wire_type": "NovoErroDoFuturo"}
    assert type(errors.from_wire({})) is RumbaError
    assert isinstance(errors.from_wire("lixo"), errors.ProtocolError)  # type: ignore[arg-type]
    assert errors.from_wire({"type": "ParameterError", "message": 3, "rc": True}).rc is None


def _dummy_fields(template: str) -> dict[str, Any]:
    return {name: 7 for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_every_template_and_hint_formats(cls: type[RumbaError]) -> None:
    templates = cls.__dict__.get("templates") or {}
    for variant, template in templates.items():
        fields = _dummy_fields(template)
        fields.update(_dummy_fields((cls.__dict__.get("hints") or {}).get(variant, "")))
        exc = cls.make(variant, **fields)
        assert type(exc) is cls and exc.message and "{" not in exc.message
        assert exc.delivery == FIXED_DELIVERY.get(cls.__name__)


@pytest.mark.parametrize("cls, variant, fields, message, hint", [
    (errors.InvalidPosition, "default", {"r": 25, "c": 1, "rows": 24, "cols": 80},
     "(25,1) está fora da tela 24x80.", None),
    (errors.TerminalNotOpen, "default", {"L": "D"}, "O terminal D não está aberto.", "use rumbapy.open('D')."),
    (errors.NotConnected, "default", {"L": "Z"}, "Sem conexão HLLAPI com Z.", "t.reconnect()."),
    (errors.UnknownTerminal, "default", {"x": "Q"}, "Terminal 'Q' desconhecido. Use D, Z ou A.", None),
    (errors.Truncated, "secret", {"r": 13, "c": 21}, "O valor secreto é maior que o campo em (13,21).", None),
    (errors.Truncated, "default", {"n": 10, "m": 8, "r": 12, "c": 21},
     "O texto (10) não cabe no campo (8) em (12,21).", None),
    (errors.WorkerHung, "input", {"funcao": "WD_CopyPS", "s": 20.4},
     "O RUMBA não respondeu a WD_CopyPS em 20 s; o processo auxiliar foi reiniciado. Não sei se a entrada chegou "
     "ao host.", None),
    (errors.NoHostResponse, "default", {"tecla": "ENTER", "s": 30},
     "A tecla ENTER foi enviada, mas o host não respondeu em 30 s.", None),
    (errors.EncodingError, "default", {"ch": "→", "cp": 0x2192, "i": 3},
     "O caractere '→' (U+2192) na posição 3 não existe no terminal.", None),
    (errors.HostError, "default", {}, "O CICS mostrou a tela 'E R R O   G R A V E'.", None),
    (errors.UndefinedKey, "default", {"x": "PF33", "sugestao": ""}, "Tecla 'PF33' desconhecida.", None),
    (errors.MultipleInstances, "attach", {"n": 2, "perfil": "BRAZIL_D", "pids": "10, 11", "L": "D"},
     "Há 2 janelas BRAZIL_D abertas (PIDs 10, 11).", "rumbapy.open('D') fecha todas."),
    (errors.TerminalInUse, "lease", {"L": "D", "pid": 5555, "script": "lote.py", "hora": "14:00"},
     "O terminal D está em uso por outro script (PID 5555, lote.py, desde 14:00).",
     "rumbapy.open('D') ou rumbapy.close('D') fecham e liberam."),
    (errors.ClientTableFull, "default", {}, "O RUMBA esgotou a tabela de 32 clientes HLLAPI.",
     "feche as janelas do RUMBA e os scripts; se persistir, faça logoff (ou reinicie, se a máquina for "
     "compartilhada)."),
    (errors.RumbaError, "internal", {"tipo": "KeyError"}, "Erro interno no processo auxiliar (KeyError).", None),
])
def test_exact_messages(cls: type[RumbaError], variant: str, fields: dict[str, Any], message: str,
                        hint: str | None) -> None:
    exc = cls.make(variant, **fields)
    assert exc.message == message
    assert exc.hint == hint


def test_make_rules() -> None:
    with pytest.raises(KeyError):
        errors.MultipleInstances.make(n=1)           # sem variante "default": a escolha é explícita
    with pytest.raises(KeyError):
        errors.WorkerHung.make("internal", tipo="X")  # modelos herdados não valem
    assert errors.TerminalNotOpen.make(L="D", hint=None).hint is None
    assert errors.TerminalNotOpen.make(L="D", hint="outra").hint == "outra"
    action = errors.UpdateRequired.ACTIONS["jupyter"]
    assert errors.UpdateRequired.make(versao="2.0.1", sha7="1a2b3c4", acao=action).message == \
        "O RumbaPy foi atualizado para 2.0.1 (1a2b3c4). Reinicie o kernel (Kernel → Restart) e rode a célula de novo."


RECOVERY = "python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/" \
           "refs/heads/master.zip"


def test_update_failed_carries_the_recovery_command_in_the_message() -> None:
    """B.7: "mensagem + comando de recuperação"; H.2: texto exato do checkout (também usado para editable)."""
    exc = errors.UpdateFailed.make(motivo="pip código 1", log="update.log")
    assert exc.message == ("A atualização do RumbaPy falhou (pip código 1). Detalhes em update.log. Para reinstalar à "
                           "mão, rode: " + RECOVERY)
    assert exc.hint is None
    checkout = errors.UpdateFailed.make("checkout")
    assert checkout.message == ("Instalação de desenvolvimento detectada (checkout). Defina RUMBAPY_DEV=1 se você é "
                                "desenvolvedor, ou instale com: " + RECOVERY)
    assert str(checkout) == checkout.message


def test_terminal_in_use_rc_variants_are_fully_formatted() -> None:
    """C.8: rc 11 por 15 s após reciclar -> o texto completo, com a letra formatada (nunca ``{L}`` literal)."""
    stuck = errors.TerminalInUse.make("rc_stuck", L="D", rc=11, function="WD_ConnectPS")
    assert stuck.message == ("Outro programa HLLAPI está conectado ao D (a conexão do processo auxiliar anterior pode "
                             "ter ficado presa; use rumbapy.open('D'), que fecha e reabre).")
    assert "{" not in str(stuck) and stuck.rc == 11
    plain = errors.TerminalInUse.make("rc", L="Z", extra="")
    assert plain.message == "Outro programa HLLAPI está conectado ao Z." and plain.hint is None
    assert not hasattr(errors.TerminalInUse, "EXTRA_STUCK")          # o modelo solto com '{L}' foi removido


def test_guard_violation_of_the_dll_proxy_is_not_the_wire_type() -> None:
    from rumbapy._worker import guard

    assert issubclass(guard.GuardViolation, PermissionError)
    assert not issubclass(guard.GuardViolation, RumbaError)
    assert errors.GuardViolation.make(politica="readonly", detalhe="ENTER").delivery == "none"
