"""Identificação das janelas que são instâncias de um perfil do RUMBA (E.3), puro (sem ``ctypes``).

Regras (``P = profile.name``):

* ``launcher``: imagem ``host access web launcher.exe``, classe ``WindowsForms10.*`` e título ``P - Pro Client`` (ou com
  sufixo depois de um espaço: ``BRAZIL_DX`` nunca casa com ``BRAZIL_D``);
* ``ie``: imagem ``iexplore.exe`` cujo pai não é ``msedge.exe`` (aba do Edge no modo IE), classe ``IEFrame`` e título
  ``[... - ]P - Pro Client[ ...] - Internet Explorer``;
* ``hllapi``: a janela-raiz do HWND devolvido por ``WD_GetSessionHWND`` (registro do worker ou ``ConnectResult``) num
  processo ``launcher``/``iexplore`` (``iexplore`` filho do Edge continua de fora), com qualquer título;
* ``launched``: janelas de nível superior visíveis de PIDs que nós lançamos (``run\\launched-<tag>-<L>.json``) e que não
  têm instância de outro perfil — qualquer título ``<NOME> - Pro Client``, inclusive de perfis do portal fora de
  ``PROFILES`` — e cuja imagem é ``launcher``/``iexplore`` (cobre o launcher parado num diálogo antes da janela
  principal; um PID reaproveitado por outro programa nunca casa).

Janelas de outros programas (``BRAZIL_D.xlsx - Excel``, Edge, Explorer) nunca casam: a regra começa pela imagem.
``sanitize`` limpa hosts/URLs/IPs de qualquer texto de janela antes de log, exceção ou ``doctor`` (C22).
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from collections.abc import Iterable
from dataclasses import dataclass, field

import re

if TYPE_CHECKING:
    from ._user32 import RawWindow, User32
    from ._types import Profile

LAUNCHER_IMAGE = "host access web launcher.exe"
IE_IMAGE = "iexplore.exe"
EDGE_IMAGE = "msedge.exe"
LAUNCHER_CLASS_PREFIX = "WindowsForms10."
IE_CLASS = "IEFrame"
DIALOG_CLASS = "#32770"
RUMBA_IMAGES = frozenset({LAUNCHER_IMAGE, IE_IMAGE})
# Janelas auxiliares dos processos do RUMBA que nunca são instância (ao vivo, 2026-10-04: o launcher tem uma
# "Internet Explorer_Hidden" com WS_VISIBLE e sem título, além das ocultas do .NET/GDI+/IME/Telnet3270). WM_CLOSE nelas
# não fecha o terminal e atrapalha o encerramento do emulador (a DLL travou no QuerySessions logo depois).
HELPER_CLASSES = frozenset({"Internet Explorer_Hidden", "GDI+ Hook Window Class", "IME", "MSCTFIME UI",
                            "SocketNotifyWindow", "Telnet3270 Application"})
HELPER_CLASS_PREFIXES = (".NET-BroadcastEventWindow",)

DialogKind = Literal["connecting", "short_name", "hllapi_error", "other"]
Kind = Literal["launcher", "ie", "hllapi", "launched"]

# (título, trecho do texto) dos diálogos conhecidos (E.3)
CONNECTING = ("Telnet3270 Application", "Connecting To Address")
SHORT_NAME = ("RUMBA API Service Control", "Short Name already in use")
HLLAPI_ERROR = ("HLLAPI ERROR", "Exceeded maximum number of clients")

_ADDRESS_RE = re.compile(r"(Address:\s*)\S+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_FQDN_RE = re.compile(r"\b[\w-]+(?:\.[\w-]+){2,}\b")


def sanitize(text: str) -> str:
    """Troca o que vem depois de ``Address:``, URLs, IPv4 e FQDNs por ``<host>``/``<url>`` (C22, R33)."""
    if not isinstance(text, str):
        text = str(text)
    text = _ADDRESS_RE.sub(r"\1<host>", text)
    text = _URL_RE.sub("<url>", text)
    text = _IPV4_RE.sub("<host>", text)
    return _FQDN_RE.sub("<host>", text)


def _ie_title_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?:.* - )?{re.escape(name)} - Pro Client(?: .*)? - Internet Explorer", re.DOTALL)


_ANY_LAUNCHER_TITLE_RE = re.compile(r"\S+ - Pro Client(?: .*)?", re.DOTALL)
_ANY_IE_TITLE_RE = re.compile(r"(?:.* - )?\S+ - Pro Client(?: .*)? - Internet Explorer", re.DOTALL)


def classify(win: RawWindow, profile_name: str, image: str | None, parent_image: str | None = None) -> Kind | None:
    """``"launcher"``/``"ie"`` se a janela de nível superior é instância do perfil ``profile_name`` pelas regras de
    título/classe/imagem; ``None`` caso contrário (a regra ``hllapi`` e a ``launched`` dependem de contexto)."""
    image = (image or "").lower()
    title = win.title or ""
    if image == LAUNCHER_IMAGE and win.cls.startswith(LAUNCHER_CLASS_PREFIX):
        base = f"{profile_name} - Pro Client"
        if title == base or title.startswith(base + " "):
            return "launcher"
        return None
    if image == IE_IMAGE and (parent_image or "").lower() != EDGE_IMAGE and win.cls == IE_CLASS:
        if _ie_title_re(profile_name).fullmatch(title):
            return "ie"
    return None


def is_helper_window(win: RawWindow) -> bool:
    """Janela auxiliar (MSHTML/.NET/GDI+/IME/Telnet3270) ou sem título fora das classes de janela principal."""
    if win.cls in HELPER_CLASSES or win.cls.startswith(HELPER_CLASS_PREFIXES):
        return True
    return not win.title and win.cls != DIALOG_CLASS and not win.cls.startswith((LAUNCHER_CLASS_PREFIX, IE_CLASS))


def dialog_kind(title: str, texts: Iterable[str]) -> DialogKind:
    """Qual dos diálogos conhecidos da E.3 é este (pelo título e pelos textos das filhas)."""
    joined = "\n".join(t for t in texts if t)
    for kind, (known_title, snippet) in (("hllapi_error", HLLAPI_ERROR), ("short_name", SHORT_NAME),
                                         ("connecting", CONNECTING)):
        if title.strip().upper() == known_title.upper() and snippet.upper() in joined.upper():
            return kind  # type: ignore[return-value]
    if title.strip().upper() == HLLAPI_ERROR[0].upper():
        return "hllapi_error"                            # nunca clicar nele, mesmo sem o texto completo
    return "other"


def is_ok_button(text: str) -> bool:
    """Botão ``OK`` (``&OK`` também vale)."""
    return text.replace("&", "").strip().upper() == "OK"


@dataclass(frozen=True)
class Instance:
    """Uma janela de nível superior que é instância do perfil."""

    hwnd: int
    pid: int
    title: str = field(repr=False)       # bruto (pode ter host/URL): use ``safe_title`` em logs/exceções (C22)
    cls: str
    image: str | None
    kind: Kind
    visible: bool
    iconic: bool
    owner: int = 0

    @property
    def safe_title(self) -> str:
        return sanitize(self.title)


@dataclass(frozen=True)
class Dialog:
    """Diálogo ``#32770`` de uma instância."""

    hwnd: int
    pid: int
    title: str = field(repr=False)       # bruto: "Connecting To Address: <host>" nunca vai ao repr (C22)
    owner: int
    texts: tuple[str, ...] = field(repr=False)
    kind: DialogKind

    @property
    def safe_title(self) -> str:
        return sanitize(self.title)


class Snapshot:
    """Fotografia das janelas de nível superior (uma chamada a ``top_windows``) com cache de imagem/pai por PID."""

    def __init__(self, user32: User32) -> None:
        self.user32 = user32
        self.windows: list[RawWindow] = list(user32.top_windows())
        self.by_hwnd: dict[int, RawWindow] = {w.hwnd: w for w in self.windows}
        self._images: dict[int, str | None] = {}
        self._parents: dict[int, str | None] = {}

    def image(self, pid: int) -> str | None:
        if pid not in self._images:
            image = self.user32.process_image(pid)
            self._images[pid] = image.lower() if image else None
        return self._images[pid]

    def parent_image(self, pid: int) -> str | None:
        if pid not in self._parents:
            image = self.user32.parent_image(pid)
            self._parents[pid] = image.lower() if image else None
        return self._parents[pid]

    def classify(self, win: RawWindow, profile_name: str) -> Kind | None:
        image = self.image(win.pid)
        if image not in RUMBA_IMAGES:
            return None
        parent = self.parent_image(win.pid) if image == IE_IMAGE else None
        return classify(win, profile_name, image, parent)

    def is_other_profile(self, win: RawWindow, profile_name: str) -> bool:
        """Janela de instância de OUTRO perfil do portal: qualquer ``<NOME> - Pro Client`` (não só os de
        ``PROFILES``), com as mesmas condições de imagem/classe das regras ``launcher``/``ie``."""
        if win.cls == DIALOG_CLASS:
            return False
        image = self.image(win.pid)
        title = win.title or ""
        if image == LAUNCHER_IMAGE and win.cls.startswith(LAUNCHER_CLASS_PREFIX):
            generic = _ANY_LAUNCHER_TITLE_RE.fullmatch(title) is not None
        elif image == IE_IMAGE and win.cls == IE_CLASS and self.parent_image(win.pid) != EDGE_IMAGE:
            generic = _ANY_IE_TITLE_RE.fullmatch(title) is not None
        else:
            return False
        return generic and self.classify(win, profile_name) is None

    def other_profile_pids(self, profile_name: str) -> set[int]:
        """PIDs que têm instância (por título) de OUTRO perfil (R6b)."""
        return {win.pid for win in self.windows if self.is_other_profile(win, profile_name)}

    def instance(self, win: RawWindow, kind: Kind) -> Instance:
        return Instance(win.hwnd, win.pid, win.title, win.cls, self.image(win.pid), kind, win.visible, win.iconic,
                        win.owner)


def find_instances(user32: User32, profile: Profile, *, hllapi_hwnds: Iterable[int | None] = (),
                   launched_pids: Iterable[int] = (), snapshot: Snapshot | None = None) -> list[Instance]:
    """Instâncias do perfil (E.3), na ordem do ``EnumWindows``; cada janela aparece uma vez (a 1ª regra que casar).

    ``launched_pids`` já devem ter sido conferidos como vivos (``created`` igual) pelo chamador.
    """
    snap = snapshot or Snapshot(user32)
    name = profile.name
    found: dict[int, Instance] = {}
    for win in snap.windows:
        if win.cls == DIALOG_CLASS:
            continue
        kind = snap.classify(win, name)
        if kind is not None:
            found[win.hwnd] = snap.instance(win, kind)
    for hwnd in hllapi_hwnds:
        if not hwnd:
            continue
        root = user32.root_of(int(hwnd))
        win = snap.by_hwnd.get(root) if root else None
        if win is None or root in found or win.cls == DIALOG_CLASS:
            continue
        image = snap.image(win.pid)
        if image not in RUMBA_IMAGES:
            continue
        if image == IE_IMAGE and snap.parent_image(win.pid) == EDGE_IMAGE:
            continue                              # aba do Edge no modo IE: nunca é classificada (I18)
        found[root] = snap.instance(win, "hllapi")
    pids = {int(p) for p in launched_pids if p}
    if pids:
        others = snap.other_profile_pids(name)
        instance_hwnds = set(found)
        # um PID lançado que já tem a janela principal achada (launcher/ie/hllapi) não contribui com mais janelas: o
        # WM_CLOSE na principal encerra o processo; os diálogos dele são tratados como diálogos (E.4 passo 5)
        instance_pids = {inst.pid for inst in found.values()}
        for win in snap.windows:
            if win.pid not in pids or win.pid in others or win.hwnd in found or not win.visible:
                continue
            if win.pid in instance_pids or is_helper_window(win):
                continue
            if snap.image(win.pid) not in RUMBA_IMAGES:
                continue                          # PID reaproveitado por outro programa: nunca é instância
            if win.owner and win.owner in instance_hwnds:
                continue                          # diálogo de uma instância já achada: é tratado como diálogo
            if win.cls == DIALOG_CLASS:
                texts = user32.static_texts(win.hwnd)
                if dialog_kind(win.title, texts) == "hllapi_error":
                    continue                      # nunca tocar no "HLLAPI ERROR"
            found[win.hwnd] = snap.instance(win, "launched")
    order = {w.hwnd: i for i, w in enumerate(snap.windows)}
    return sorted(found.values(), key=lambda inst: order.get(inst.hwnd, 0))


def dialogs_of(user32: User32, instance: Instance, *, snapshot: Snapshot | None = None,
               profile_name: str | None = None) -> list[Dialog]:
    """Diálogos ``#32770`` de nível superior da instância: com ``owner == hwnd``; sem dono, do mesmo PID, só se o PID
    não tiver instância de outro perfil (R6b)."""
    snap = snapshot or Snapshot(user32)
    others: set[int] | None = None
    out: list[Dialog] = []
    for win in snap.windows:
        if win.cls != DIALOG_CLASS or win.hwnd == instance.hwnd:
            continue
        if win.owner:
            if win.owner != instance.hwnd:
                continue
        else:
            if win.pid != instance.pid:
                continue
            if others is None:
                others = snap.other_profile_pids(profile_name) if profile_name else set()
            if win.pid in others:
                continue
        texts = tuple(user32.static_texts(win.hwnd))
        out.append(Dialog(win.hwnd, win.pid, win.title, win.owner, texts, dialog_kind(win.title, texts)))
    return out


def rumba_dialogs(user32: User32, kind: DialogKind, *, images: Iterable[str] = RUMBA_IMAGES,
                  snapshot: Snapshot | None = None, visible_only: bool = True) -> list[Dialog]:
    """Todos os diálogos ``#32770`` de nível superior do tipo ``kind`` em processos com imagem em ``images``."""
    snap = snapshot or Snapshot(user32)
    wanted = {i.lower() for i in images}
    out: list[Dialog] = []
    for win in snap.windows:
        if win.cls != DIALOG_CLASS or (visible_only and not win.visible):
            continue
        if snap.image(win.pid) not in wanted:
            continue
        texts = tuple(user32.static_texts(win.hwnd))
        found = dialog_kind(win.title, texts)
        if found == kind:
            out.append(Dialog(win.hwnd, win.pid, win.title, win.owner, texts, found))
    return out


def hllapi_error_visible(user32: User32, *, snapshot: Snapshot | None = None) -> bool:
    """Há um ``#32770`` visível com título ``HLLAPI ERROR`` (de qualquer processo)?"""
    snap = snapshot or Snapshot(user32)
    return any(w.cls == DIALOG_CLASS and w.visible and w.title.strip().upper() == HLLAPI_ERROR[0]
               for w in snap.windows)
