"""Aceite P-0: perfis fixos D/Z/A, ``resolve`` e URLs do portal (E.1)."""
# ruff: noqa: I001
from __future__ import annotations

from rumbapy.errors import UnknownTerminal
from rumbapy._types import Profile
from rumbapy import profiles

import pytest


def test_fixed_profiles() -> None:
    assert profiles.PROFILES == {
        "D": Profile("BRAZIL_D", "D", "cics", "BRAZIL D"),
        "Z": Profile("BRAZIL_Z", "Z", "cics", "BRAZIL Z"),
        "A": Profile("IMS_A", "A", "ims", "IMS A"),
    }


@pytest.mark.parametrize("given, letter", [
    ("D", "D"), ("d", "D"), (" z ", "Z"), ("A", "A"), ("BRAZIL_D", "D"), ("brazil_z", "Z"), ("ims_a", "A"),
])
def test_resolve(given: str, letter: str) -> None:
    assert profiles.resolve(given) is profiles.PROFILES[letter]
    assert profiles.resolve(profiles.PROFILES[letter]) is profiles.PROFILES[letter]


@pytest.mark.parametrize("given", ["Q", "", "_", "BRAZIL D", "BRAZIL_DX", None, 3, Profile("X", "X", "cics", "X")])
def test_resolve_unknown(given: object) -> None:
    with pytest.raises(UnknownTerminal) as info:
        profiles.resolve(given)  # type: ignore[arg-type]
    assert isinstance(info.value, ValueError)
    assert info.value.message.endswith("desconhecido. Use D, Z ou A.")


def test_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profiles, "PORTAL", "https://portal.exemplo/raiz/")
    d = profiles.PROFILES["D"]
    assert profiles.profile_url(d) == "https://portal.exemplo/raiz/profpro/BRAZIL_D.html"
    assert profiles.launcher_arg(d) == \
        "hawlauncher://https://portal.exemplo/raiz/profpro/BRAZIL_D.html/microfocus_w2h_hawlauncher"
    assert profiles.index_url() == "https://portal.exemplo/raiz/index_pro_api.html"


def test_default_portal_is_the_v015_base_over_https() -> None:
    assert profiles.PORTAL_DEFAULT.startswith("https://")
    assert profiles.PORTAL_DEFAULT.endswith("/rumba694/w2hlegacy")
    assert " " not in profiles.launcher_arg(profiles.PROFILES["A"])      # um único argumento para o launcher
