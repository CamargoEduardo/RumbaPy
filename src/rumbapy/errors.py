"""Exceções do RumbaPy (B.7) e sua serialização no canal do processo auxiliar (``to_wire``/``from_wire``).

Toda falha da biblioteca levanta uma subclasse de :class:`RumbaError`, que herda de ``RuntimeError`` (scripts
antigos com ``except RuntimeError`` continuam funcionando). Algumas classes também herdam de ``ValueError``,
``TimeoutError`` ou ``IndexError``.

As mensagens têm texto fixo (tabela B.7): cada classe guarda os modelos em ``templates`` (e as dicas em ``hints``),
e ``Classe.make(variante, **campos)`` monta a mensagem exata, por exemplo::

    raise InvalidPosition.make(r=25, c=1, rows=24, cols=80)        # "(25,1) está fora da tela 24x80."

Regras: mensagens nunca citam credenciais nem conteúdo de campos ocultos; ``to_wire`` nunca leva ``screen``.
Este módulo não importa ``ctypes``; ``screen`` só é importado para checagem de tipos.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self
from collections.abc import Mapping

from ._version import RECOVERY_COMMAND

if TYPE_CHECKING:
    from .screen import Screen

Delivery = Literal["none", "partial", "unknown"]
DELIVERY_VALUES: tuple[str, ...] = ("none", "partial", "unknown")
WIRE_KEYS: tuple[str, ...] = ("type", "message", "hint", "rc", "function", "delivery", "details")

_DELIVERY_PT = {"none": "nenhuma", "partial": "parcial", "unknown": "desconhecida"}
_REGISTRY: dict[str, type[RumbaError]] = {}
_DEFAULT_HINT: Any = object()
_RECOVERY = RECOVERY_COMMAND.replace("{", "{{").replace("}", "}}")   # literal dentro de modelos de str.format


class RumbaError(RuntimeError):
    """Erro base do RumbaPy. Atributos: ``message, hint, rc, function, terminal, screen, delivery, details``.

    ``delivery`` diz o que aconteceu com uma entrada: ``"none"`` (nada chegou ao RUMBA), ``"partial"`` (parte das
    teclas/texto pode ter sido aceita) ou ``"unknown"`` (não sei). Nunca repita uma tecla AID dentro de ``except``.
    """

    _rumbapy_error: ClassVar[bool] = True       # usado pelo __init__ (A.6) para não "curar" erros da biblioteca
    code: ClassVar[str] = "RumbaError"          # nome de wire estável (= nome da classe)
    default_delivery: ClassVar[Delivery | None] = None
    templates: ClassVar[Mapping[str, str]] = {
        "internal": "Erro interno no processo auxiliar ({tipo}).",
        "legacy_shim": "uso interno: papel legacy-shim",
    }
    hints: ClassVar[Mapping[str, str]] = {}

    message: str
    hint: str | None
    rc: int | None
    function: str | None
    terminal: str | None
    screen: Screen | None
    delivery: Delivery | None
    details: dict[str, Any] | None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.code = cls.__name__
        if cls.__module__ == __name__:          # o espaço de nomes do wire é fechado: só as classes deste módulo
            _REGISTRY[cls.code] = cls

    def __init__(self, message: str, *, hint: str | None = None, rc: int | None = None,
                 function: str | None = None, terminal: str | None = None, screen: Screen | None = None,
                 delivery: Delivery | None = None, details: dict[str, Any] | None = None) -> None:
        message = message if isinstance(message, str) else str(message)
        super().__init__(message)               # UM argumento (E23): args == (message,)
        if delivery is None:
            delivery = self.default_delivery
        if delivery is not None and delivery not in DELIVERY_VALUES:
            raise ValueError(f"delivery inválido: {delivery!r} (use {', '.join(DELIVERY_VALUES)})")
        self.message = message
        self.hint = hint
        self.rc = rc
        self.function = function
        self.terminal = terminal
        self.screen = screen
        self.delivery = delivery
        self.details = details

    @classmethod
    def make(cls, variant: str = "default", /, *, hint: Any = _DEFAULT_HINT, rc: int | None = None,
             function: str | None = None, terminal: str | None = None, screen: Screen | None = None,
             delivery: Delivery | None = None, details: dict[str, Any] | None = None, **fields: Any) -> Self:
        """Cria a exceção com a mensagem exata da variante ``variant`` (campos em ``fields``).

        A dica padrão da variante (``hints``) é usada se ``hint`` não for informado; ``hint=None`` a suprime.
        Só valem os modelos definidos na própria classe (nunca os herdados).
        """
        own_templates = cls.__dict__.get("templates") or {}
        if variant not in own_templates:
            raise KeyError(f"{cls.__name__} não tem a variante de mensagem {variant!r}")
        message = own_templates[variant].format(**fields).strip()
        if hint is _DEFAULT_HINT:
            own_hint = (cls.__dict__.get("hints") or {}).get(variant)
            hint = own_hint.format(**fields) if own_hint else None
        return cls(message, hint=hint, rc=rc, function=function, terminal=terminal, screen=screen,
                   delivery=delivery, details=details)

    def __str__(self) -> str:
        head = f"[{self.terminal}] {self.message}" if self.terminal else self.message
        lines = [head]
        if self.hint:
            lines.append(f"  Como resolver: {self.hint}")
        tech: list[str] = []
        if self.function and self.rc is not None:
            tech.append(f"{self.function} rc={self.rc}")
        elif self.function:
            tech.append(self.function)
        elif self.rc is not None:
            tech.append(f"rc={self.rc}")
        if self.delivery:
            tech.append(f"entrega: {_DELIVERY_PT[self.delivery]}")
        if tech:
            lines.append("  (" + "; ".join(tech) + ")")
        return "\n".join(lines)

    def __reduce__(self) -> tuple[Any, ...]:
        kwargs = {"hint": self.hint, "rc": self.rc, "function": self.function, "terminal": self.terminal,
                  "screen": self.screen, "delivery": self.delivery, "details": self.details}
        return (_restore, (type(self), self.message, kwargs), dict(self.__dict__))


def _restore(cls: type[RumbaError], message: str, kwargs: dict[str, Any]) -> RumbaError:
    return cls(message, **kwargs)


_REGISTRY[RumbaError.code] = RumbaError


# ------------------------------------------------------------------------------------------- ambiente e instalação

class SetupError(RumbaError):
    """Ambiente inadequado (sistema, RUMBA, Python 32-bit, sandbox dos testes)."""


class UnsupportedPlatform(SetupError):
    templates = {"default": "O RumbaPy só funciona no Windows."}


class RumbaNotInstalled(SetupError):
    templates = {"default": "Não encontrei o RUMBA ({o_que}) em {caminho}."}


class Python32NotFound(SetupError, ValueError):
    templates = {
        "default": "Não encontrei um Python 32-bit >= 3.12 (o RUMBA só existe em 32-bit). Motivo: {motivo}.",
        "not_found": "Não encontrei um Python 32-bit >= 3.12 (o RUMBA só existe em 32-bit).",
    }
    hints = {
        "not_found": 'instale o "Windows installer (32-bit)" do Python 3.13 em python.org e rode de novo '
                     '(ele será encontrado sozinho), ou informe o caminho: '
                     'rumbapy.open("D", python32=r"C:\\...\\python.exe").',
    }


class SandboxViolation(SetupError):
    templates = {"default": "Bloqueado pelo modo sandbox dos testes: {acao}."}


# ------------------------------------------------------------------------------------------- atualização

class UpdateError(RumbaError):
    """Falhas do auto-update obrigatório (H)."""


class UpdateRequired(UpdateError):
    templates = {
        "default": "O RumbaPy foi atualizado para {versao} ({sha7}). {acao}",
        "during_import": "O RumbaPy foi atualizado durante a importação. Rode de novo.",
        "other_process": "Outro processo atualizou o RumbaPy. Rode de novo.",
    }
    # texto de {acao} por contexto (H.4)
    ACTIONS: ClassVar[Mapping[str, str]] = {
        "script": "Rode o script de novo.",
        "jupyter": "Reinicie o kernel (Kernel → Restart) e rode a célula de novo.",
        "repl": "Feche este Python e abra de novo.",
        "process": "Este processo ainda usa a versão antiga; reinicie-o.",
    }


class UpdateCheckFailed(UpdateError):
    templates = {
        "default": "Não consegui verificar a versão mais recente no GitHub ({motivo}). Conecte-se à rede/VPN e rode "
                   "de novo. Para fechar terminais abertos agora, feche as janelas '<PERFIL> - Pro Client' pelo X.",
    }


class UpdateFailed(UpdateError):
    # B.7: "mensagem + comando de recuperação (H.4)" — o comando faz parte da MENSAGEM (sem dica separada).
    # "checkout": H.2 passos 2 (pasta .git acima do pacote) e 4 (instalação editable), texto exato da H.2.
    templates = {
        "default": "A atualização do RumbaPy falhou ({motivo}). Detalhes em {log}. Para reinstalar à mão, rode: "
                   + _RECOVERY,
        "checkout": "Instalação de desenvolvimento detectada (checkout). Defina RUMBAPY_DEV=1 se você é "
                    "desenvolvedor, ou instale com: " + _RECOVERY,
    }


# ------------------------------------------------------------------------------------------- processo auxiliar

class WorkerError(RumbaError):
    """Falhas do processo auxiliar 32-bit (worker) e do canal com ele."""

    templates = {
        "not_exited": "O processo auxiliar do terminal {L} (PID {pid}) não encerrou.",
        "authkey_corrupt": "authkey.bin está corrompido e há processos auxiliares vivos; feche os scripts e rode "
                           "de novo",
        "not_responding": "o processo auxiliar não responde",
    }
    hints = {"not_exited": "feche as janelas do RUMBA; se persistir, faça logoff (não use o Gerenciador de Tarefas)"}


class WorkerStartFailed(WorkerError):
    templates = {
        "default": "O processo auxiliar 32-bit do terminal {L} não iniciou (código {n}). {fim_do_boot}",
        "live_without_guard": "RUMBAPY_LIVE exige --guard",
    }


class WorkerCrashed(WorkerError):
    templates = {
        "default": "O processo auxiliar 32-bit do terminal {L} parou.",
        "input": "O processo auxiliar 32-bit do terminal {L} parou. Não sei se a entrada chegou ao host.",
    }


class WorkerHung(WorkerError):
    templates = {
        "default": "O RUMBA não respondeu a {funcao} em {s:.0f} s; o processo auxiliar foi reiniciado.",
        "input": "O RUMBA não respondeu a {funcao} em {s:.0f} s; o processo auxiliar foi reiniciado. Não sei se a "
                 "entrada chegou ao host.",
    }


class ProtocolError(WorkerError):
    templates = {"default": "Mensagem inválida no canal do terminal {L} ({detalhe})."}


class ClientTableFull(WorkerError):
    templates = {"default": "O RUMBA esgotou a tabela de 32 clientes HLLAPI."}
    hints = {"default": "feche as janelas do RUMBA e os scripts; se persistir, faça logoff (ou reinicie, se a máquina "
                        "for compartilhada)."}


class ChannelHijacked(WorkerError):
    templates = {"default": "Outro processo (PID {pid}) ocupa o canal do terminal {L}; não vou usá-lo."}


# ------------------------------------------------------------------------------------------- terminal e sessão

class TerminalError(RumbaError):
    """Abrir, anexar, fechar e o estado da sessão do terminal."""


class UnknownTerminal(TerminalError, ValueError):
    templates = {"default": "Terminal '{x}' desconhecido. Use D, Z ou A."}


class TerminalNotOpen(TerminalError):
    templates = {"default": "O terminal {L} não está aberto."}
    hints = {"default": "use rumbapy.open('{L}')."}


class NotConnected(TerminalError):
    templates = {"default": "Sem conexão HLLAPI com {L}."}
    hints = {"default": "t.reconnect()."}


class LaunchFailed(TerminalError):
    templates = {"default": "O Host Access Web Launcher terminou com código {n} antes de registrar {L}."}


class LaunchTimeout(TerminalError, TimeoutError):
    templates = {"default": "O {perfil} não ficou pronto em {s:.0f} s: {fase}."}
    PHASES: ClassVar[Mapping[str, str]] = {
        "register": "a letra não foi registrada",
        "connect": "a conexão com o host não subiu (verifique VPN/rede)",
        "first_screen": "a conexão subiu, mas o host não mandou a primeira tela",
    }


class MultipleInstances(TerminalError):
    templates = {
        "attach": "Há {n} janelas {perfil} abertas (PIDs {pids}).",
        "open": "Outra janela que não consegui fechar usa a letra {L}.",
    }
    hints = {
        "attach": "rumbapy.open('{L}') fecha todas.",
        "open": "feche-a (aba do Edge no modo IE?) e tente de novo.",
    }


class InstanceNotClosable(TerminalError):
    templates = {"default": "A letra {L} continua registrada por um processo que não consigo fechar ({imagem}, PID "
                            "{pid})."}


class TerminalInUse(TerminalError):
    # "rc": rc 11 do ConnectPS (extra="" ou um complemento JÁ formatado, ex. o servidor legado ocupado, E.5/E.8);
    # "rc_stuck": a reconexão depois de reciclar o worker esbarrou em rc 11 por 15 s (texto da C.8).
    templates = {
        "lease": "O terminal {L} está em uso por outro script (PID {pid}, {script}, desde {hora}).",
        "rc": "Outro programa HLLAPI está conectado ao {L}{extra}.",
        "rc_stuck": "Outro programa HLLAPI está conectado ao {L} (a conexão do processo auxiliar anterior pode ter "
                    "ficado presa; use rumbapy.open('{L}'), que fecha e reabre).",
    }
    hints = {"lease": "rumbapy.open('{L}') ou rumbapy.close('{L}') fecham e liberam."}


class CloseFailed(TerminalError):
    templates = {"default": "Não consegui fechar {n} janela(s) do {perfil}: {lista}."}
    hints = {"default": "feche-as pelo X."}


class SessionStopped(TerminalError):
    templates = {"default": "A sessão do emulador {L} foi encerrada (janela fechada?)."}


class HostConnectionLost(TerminalError):
    templates = {"default": "A conexão com o mainframe caiu ({L})."}
    hints = {"default": "rumbapy.open('{L}') de novo."}


class SessionReplaced(TerminalError):
    default_delivery = "none"
    templates = {"default": "Este objeto se refere a uma instância do {L} que foi fechada/reaberta (por este ou outro "
                            "script). Use a Session nova."}


class SessionUncertain(TerminalError):
    default_delivery = "none"
    templates = {"default": "A última entrada pode ter chegado ao host; confirme a tela com wait_text/wait_any antes "
                            "de digitar (ou t.acknowledge())."}


# ------------------------------------------------------------------------------------------- HLLAPI (rc da DLL)

class HllapiError(RumbaError):
    """Códigos de retorno da ehlapi32.Dll e validações equivalentes feitas antes de chamá-la."""


class ParameterError(HllapiError, ValueError):
    default_delivery = "none"
    templates = {"default": "{detalhe}"}


class HostBusy(HllapiError):
    templates = {"default": "O host está ocupado (X SYSTEM); parte das teclas pode ter sido digitada."}


class KeyboardLocked(HllapiError):
    templates = {"default": "O RUMBA recusou as teclas (teclado bloqueado); parte delas pode ter sido digitada."}
    hints = {"default": "confira a tela (print(t.screen)) antes de repetir qualquer tecla; se houver 'X -f', use "
                        "t.press(Key.RESET)."}


class Truncated(HllapiError):
    templates = {
        "default": "O texto ({n}) não cabe no campo ({m}) em ({r},{c}).",
        "secret": "O valor secreto é maior que o campo em ({r},{c}).",
    }


class ProtectedField(HllapiError):
    default_delivery = "none"
    templates = {"default": "({r},{c}) é um campo protegido; não dá para escrever."}


class InvalidPosition(HllapiError, IndexError):
    default_delivery = "none"
    templates = {"default": "({r},{c}) está fora da tela {rows}x{cols}."}


class UndefinedKey(HllapiError, ValueError):
    default_delivery = "none"
    templates = {"default": "Tecla '{x}' desconhecida. {sugestao}"}


class FieldNotFound(HllapiError):
    default_delivery = "none"
    templates = {"default": "Não há campo em ({r},{c}) (tela não formatada?)."}


class HllapiSystemError(HllapiError):
    templates = {"default": "Erro interno do RUMBA em {funcao} (rc 9)."}


class GuardViolation(HllapiError):
    default_delivery = "none"
    templates = {"default": "Bloqueado pelo intertravamento de teste ({politica}): {detalhe}."}


# ------------------------------------------------------------------------------------------- esperas e texto

class WaitTimeout(RumbaError, TimeoutError):
    """A condição de espera não aconteceu no prazo; ``screen`` traz a última tela (G.4)."""

    # G.3/G.4; quem levanta acrescenta o estado do OIA (oia.describe()) ao fim da mensagem
    templates = {
        "at": "Esperei {s:g} s por '{x}' em ({r},{c}), mas ali está '{atual}'.",
        "anywhere": "Esperei {s:g} s por '{x}' em qualquer lugar da tela. Linha 1 atual: '{linha1}'.",
        "gone": "'{x}' continua em ({r},{c}) após {s:g} s.",
        "keyboard": "O teclado continua bloqueado após {s:g} s (G8={hex}). Se for erro de digitação ('X -f'), use "
                    "t.press(Key.RESET).",
    }
    hints = {
        "at": "confira a tela (print(e.screen)) e a coordenada; se o host for lento, aumente timeout.",
        "anywhere": "confira a tela (print(e.screen)) e a coordenada; se o host for lento, aumente timeout.",
    }


class NoHostResponse(WaitTimeout):
    default_delivery = "unknown"
    templates = {"default": "A tecla {tecla} foi enviada, mas o host não respondeu em {s:.0f} s."}


class EncodingError(RumbaError, ValueError):
    default_delivery = "none"
    templates = {
        "default": "O caractere '{ch}' (U+{cp:04X}) na posição {i} não existe no terminal.",
        "secret": "O valor secreto contém um caractere não suportado.",
    }


# ------------------------------------------------------------------------------------------- receitas

class RecipeError(RumbaError):
    """Fluxos de logon e detectores de erro do host (B.8)."""


class RecipeNotAvailable(RecipeError):
    templates = {"default": "t.{receita} só existe em terminais {tipo} ({letras})."}


class UnexpectedScreen(RecipeError):
    templates = {"default": "Esperava {tela}; a linha 1 atual é: '{linha1}'."}
    hints = {"default": "use rumbapy.open('{L}') para começar de uma tela limpa."}


class LogonFailed(RecipeError):
    templates = {"default": "O logon não terminou: {motivo}."}


class PasswordExpired(RecipeError):
    templates = {"default": "A senha do CICS está expirada. Troque-a manualmente no terminal."}


class InvalidPassword(RecipeError):
    templates = {"default": "Usuário ou senha do CICS incorretos."}


class HostError(RecipeError):
    templates = {"default": "O CICS mostrou a tela 'E R R O   G R A V E'."}


__all__ = [name for name, cls in list(globals().items())
           if isinstance(cls, type) and issubclass(cls, RumbaError) and cls.__module__ == __name__]


# ------------------------------------------------------------------------------------------- wire

def error_classes() -> dict[str, type[RumbaError]]:
    """Mapa nome de wire -> classe (cópia)."""
    return dict(_REGISTRY)


def to_wire(exc: RumbaError) -> dict[str, Any]:
    """Serializa a exceção para o canal: ``{"type","message","hint","rc","function","delivery","details"}``.

    Nunca inclui ``screen`` nem ``terminal`` (o cliente acrescenta os dois).
    """
    return {
        "type": type(exc).code if isinstance(exc, RumbaError) else "RumbaError",
        "message": exc.message if isinstance(exc, RumbaError) else str(exc),
        "hint": getattr(exc, "hint", None),
        "rc": getattr(exc, "rc", None),
        "function": getattr(exc, "function", None),
        "delivery": getattr(exc, "delivery", None),
        "details": getattr(exc, "details", None),
    }


def from_wire(d: Mapping[str, Any], *, terminal: str | None = None) -> RumbaError:
    """Reconstrói a exceção recebida do worker; tipo desconhecido vira ``RumbaError`` (``details.wire_type``).

    Tolerante a campos ausentes ou com tipo errado (nunca levanta por causa do conteúdo de ``d``).
    """
    if not isinstance(d, Mapping):
        return ProtocolError.make(L=terminal or "?", detalhe="erro sem formato de objeto", terminal=terminal)
    wire_type = d.get("type")
    cls = _REGISTRY.get(wire_type) if isinstance(wire_type, str) else None
    message = d.get("message")
    if not isinstance(message, str):
        message = "Erro sem mensagem no processo auxiliar."
    hint = d.get("hint") if isinstance(d.get("hint"), str) else None
    rc = d.get("rc")
    rc = rc if isinstance(rc, int) and not isinstance(rc, bool) else None
    function = d.get("function") if isinstance(d.get("function"), str) else None
    delivery = d.get("delivery") if d.get("delivery") in DELIVERY_VALUES else None
    details = d.get("details")
    details = dict(details) if isinstance(details, Mapping) else None
    if cls is None:
        cls = RumbaError
        details = {**(details or {}), "wire_type": wire_type if isinstance(wire_type, str) else repr(wire_type)}
    return cls(message, hint=hint, rc=rc, function=function, terminal=terminal, delivery=delivery,
               details=details)
