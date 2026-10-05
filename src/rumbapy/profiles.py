"""Perfis fixos do portal Web-to-Host com HLLAPI (E.1) e as URLs usadas para abri-los.

Um perfil novo é uma linha em ``PROFILES`` (``register_profile`` foi cortado). ``RUMBAPY_PORTAL`` troca a base do
portal (padrão: a mesma base da v0.1.5, em https).
"""
# ruff: noqa: I001
from __future__ import annotations

from .errors import UnknownTerminal
from ._types import Profile

import os

PORTAL_DEFAULT = "https://rumba.srv.volvo.com/rumba694/w2hlegacy"
PORTAL = os.environ.get("RUMBAPY_PORTAL", PORTAL_DEFAULT)

PROFILES: dict[str, Profile] = {
    "D": Profile("BRAZIL_D", "D", "cics", "BRAZIL D"),
    "Z": Profile("BRAZIL_Z", "Z", "cics", "BRAZIL Z"),
    "A": Profile("IMS_A", "A", "ims", "IMS A"),
}

_BY_NAME = {p.name: p for p in PROFILES.values()}


def resolve(terminal: str | Profile) -> Profile:
    """Letra (``"D"``) ou nome do perfil (``"BRAZIL_D"``), sem diferenciar maiúsculas -> ``Profile``.

    Desconhecido -> ``UnknownTerminal`` (que também é ``ValueError``).
    """
    if isinstance(terminal, Profile) and PROFILES.get(terminal.letter) == terminal:
        return terminal
    if isinstance(terminal, str):
        key = terminal.strip().upper()
        found = PROFILES.get(key) or _BY_NAME.get(key)
        if found is not None:
            return found
    shown = terminal if isinstance(terminal, str) else repr(terminal)
    raise UnknownTerminal.make(x=shown)


def _portal() -> str:
    return PORTAL.rstrip("/")


def profile_url(p: Profile) -> str:
    """Página do perfil no portal: ``<portal>/profpro/<PERFIL>.html``."""
    return f"{_portal()}/profpro/{p.name}.html"


def launcher_arg(p: Profile) -> str:
    """Argumento ÚNICO do Host Access Web Launcher (validado ao vivo): ``hawlauncher://<url>/microfocus_w2h_hawlauncher``."""
    return f"hawlauncher://{profile_url(p)}/microfocus_w2h_hawlauncher"


def index_url() -> str:
    """Página com os perfis HLLAPI (usada só pelo fallback do Internet Explorer)."""
    return f"{_portal()}/index_pro_api.html"
