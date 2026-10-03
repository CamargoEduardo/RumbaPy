"""Intertravamento de segurança para testes em PRODUÇÃO: proxy da ehlapi32.Dll que bloqueia entradas não autorizadas.

Uso (no processo que carrega a DLL):
    dll = GuardedDll(ctypes.WinDLL(DLL_PATH), GuardPolicy(allowed_aids={"ENTER"}, allow_text=True))

Toda chamada passa pela política ANTES de chegar à DLL real:
* ``WD_SendKey``: o texto é analisado mnemônico a mnemônico; qualquer tecla AID fora de ``allowed_aids`` (ENTER, PF1…PF24,
  PA1…PA3, CLEAR, SYSREQ, ATTN, PAGEUP/PAGEDOWN) levanta ``GuardViolation`` e nada é enviado. Texto livre e teclas locais
  (TAB, ERASEEOF, setas…) dependem de ``allow_text`` / ``allow_local_keys``.
* ``WD_CopyStringToPS`` / ``WD_CopyStringToField``: exigem ``allow_text``.
* Funções de transferência/macro/perfil (``WD_SendFile``, ``WD_ReceiveFile``, ``WD_RunMacro``, ``WD_RunProfile``,
  ``WD_DeletePS``) são sempre bloqueadas.
* Leitura (CopyPS, CopyPSToString, OIA, cursor, status, QuerySessions, Wait…) é sempre liberada.

``policy.audit`` guarda o histórico (nome da função e resumo do argumento; senhas não são registradas: o conteúdo de
``CopyStringToPS`` é mascarado).
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field

AID_BY_CODE = {
    "E": "ENTER", "C": "CLEAR", "1": "PF1", "2": "PF2", "3": "PF3", "4": "PF4", "5": "PF5", "6": "PF6", "7": "PF7",
    "8": "PF8", "9": "PF9", "a": "PF10", "b": "PF11", "c": "PF12", "d": "PF13", "e": "PF14", "f": "PF15", "g": "PF16",
    "h": "PF17", "i": "PF18", "j": "PF19", "k": "PF20", "l": "PF21", "m": "PF22", "n": "PF23", "o": "PF24",
    "x": "PA1", "y": "PA2", "z": "PA3", "u": "PAGEUP", "v": "PAGEDOWN",
}
AID_BY_A_CODE = {"H": "SYSREQ", "Q": "ATTN"}
ALWAYS_BLOCKED = {"WD_SendFile", "WD_ReceiveFile", "WD_RunMacro", "WD_RunProfile", "WD_DeletePS",
                  "WD_StartKSIntercept", "WD_PostInterceptStatus", "hllapi"}
TEXT_FUNCS = {"WD_CopyStringToPS", "WD_CopyStringToField"}


class GuardViolation(PermissionError):
    """Entrada bloqueada pelo intertravamento de produção."""


@dataclass
class GuardPolicy:
    allowed_aids: set = field(default_factory=set)  # ex.: {"ENTER"} só durante o login
    allow_text: bool = False
    allow_local_keys: bool = False
    audit: list = field(default_factory=list)


def _keys_text(arg) -> str:
    if isinstance(arg, (bytes, bytearray)):
        raw = bytes(arg)
    elif isinstance(arg, ctypes.Array):
        raw = bytes(arg)
    elif isinstance(arg, ctypes.c_char_p):
        raw = arg.value or b""
    else:
        raw = str(arg).encode("cp1252", "replace")
    nul = raw.find(b"\0")
    return (raw if nul < 0 else raw[:nul]).decode("cp1252", "replace")


def classify_keys(text: str) -> tuple:
    """Devolve (aids, tem_texto, tem_teclas_locais) de uma string de SendKey."""
    aids, has_text, has_local = [], False, False
    i = 0
    while i < len(text):
        if text[i] == "@" and i + 1 < len(text):
            code = text[i + 1]
            i += 2
            if code == "@":
                has_text = True
                continue
            if code in ("A", "S") and i + 1 < len(text) and text[i] == "@":
                sub = text[i + 1]
                i += 2
                if code == "A" and sub in AID_BY_A_CODE:
                    aids.append(AID_BY_A_CODE[sub])
                else:
                    has_local = True
                continue
            if code in AID_BY_CODE:
                aids.append(AID_BY_CODE[code])
            else:
                has_local = True
            continue
        has_text = True
        i += 1
    return aids, has_text, has_local


class _GuardedFunction:
    """Envolve uma função ctypes repassando argtypes/restype para a função real."""

    def __init__(self, name, fn, guard):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_fn", fn)
        object.__setattr__(self, "_guard", guard)

    def __setattr__(self, key, value):
        setattr(self._fn, key, value)

    def __getattr__(self, key):
        return getattr(self._fn, key)

    def __call__(self, *args):
        self._guard.check(self._name, args)
        return self._fn(*args)


class GuardedDll:
    def __init__(self, dll, policy: GuardPolicy):
        self._dll = dll
        self.policy = policy
        self._cache = {}

    def check(self, name: str, args: tuple) -> None:
        pol = self.policy
        if name in ALWAYS_BLOCKED:
            pol.audit.append((name, "BLOQUEADO"))
            raise GuardViolation(f"{name} é sempre bloqueada pelo intertravamento")
        if name == "WD_SendKey":
            text = _keys_text(args[1])
            aids, has_text, has_local = classify_keys(text)
            bad = [a for a in aids if a not in pol.allowed_aids]
            if bad:
                pol.audit.append((name, f"BLOQUEADO aids={aids}"))
                raise GuardViolation(f"tecla(s) AID não autorizada(s): {bad}")
            if has_text and not pol.allow_text:
                pol.audit.append((name, "BLOQUEADO texto"))
                raise GuardViolation("digitação de texto não autorizada")
            if has_local and not pol.allow_local_keys:
                pol.audit.append((name, "BLOQUEADO teclas locais"))
                raise GuardViolation("teclas locais não autorizadas")
            pol.audit.append((name, f"aids={aids} texto={has_text} locais={has_local}"))
        elif name in TEXT_FUNCS:
            if not pol.allow_text:
                pol.audit.append((name, "BLOQUEADO"))
                raise GuardViolation(f"{name} (escrita na tela) não autorizada")
            pol.audit.append((name, "pos=%s len=***" % (args[1] if len(args) > 1 else "?")))
        else:
            pol.audit.append((name, ""))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in ALWAYS_BLOCKED:
            def blocked(*_args, _name=name):
                self.policy.audit.append((_name, "BLOQUEADO"))
                raise GuardViolation(f"{_name} é sempre bloqueada pelo intertravamento")
            return blocked
        if name not in self._cache:
            self._cache[name] = _GuardedFunction(name, getattr(self._dll, name), self)
        return self._cache[name]
