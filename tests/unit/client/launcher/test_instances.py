"""E.3: classificação de janelas (``_instances``), diálogos e ``sanitize`` — puro, só com ``FakeUser32``."""
# ruff: noqa: I001
from __future__ import annotations

from tests.fakes.fake_user32 import FakeUser32
from rumbapy._instances import (Snapshot, classify, dialog_kind, dialogs_of, find_instances, hllapi_error_visible,
                                is_ok_button, rumba_dialogs, sanitize)
from rumbapy._user32 import RawWindow
from rumbapy import profiles

import pytest

D = profiles.resolve("D")
Z = profiles.resolve("Z")
LAUNCHER = "host access web launcher.exe"
WINFORMS = "WindowsForms10.Window.8.app.0.1"


def raw(title: str, cls: str = WINFORMS, pid: int = 10) -> RawWindow:
    return RawWindow(hwnd=1, title=title, cls=cls, pid=pid, owner=0, visible=True, iconic=False)


# ------------------------------------------------------------------------------------------- classify
@pytest.mark.parametrize("title", ["BRAZIL_D - Pro Client", "BRAZIL_D - Pro Client [D]",
                                   "BRAZIL_D - Pro Client - Connecting"])
def test_launcher_titles_of_the_profile(title: str) -> None:
    assert classify(raw(title), "BRAZIL_D", LAUNCHER) == "launcher"


@pytest.mark.parametrize("title", ["BRAZIL_DX - Pro Client", "BRAZIL_D - Pro ClientX", "brazil_d - pro client",
                                   "BRAZIL_Z - Pro Client", "x BRAZIL_D - Pro Client"])
def test_other_titles_never_match(title: str) -> None:
    assert classify(raw(title), "BRAZIL_D", LAUNCHER) is None


def test_excel_with_the_profile_name_is_ignored() -> None:
    assert classify(raw("BRAZIL_D.xlsx - Excel", cls="XLMAIN"), "BRAZIL_D", "excel.exe") is None
    # mesmo título exato do launcher em outro programa: a regra começa pela imagem
    assert classify(raw("BRAZIL_D - Pro Client"), "BRAZIL_D", "excel.exe") is None
    assert classify(raw("BRAZIL_D - Pro Client"), "BRAZIL_D", None) is None


def test_launcher_needs_winforms_class() -> None:
    assert classify(raw("BRAZIL_D - Pro Client", cls="#32770"), "BRAZIL_D", LAUNCHER) is None


@pytest.mark.parametrize("title", ["BRAZIL_D - Pro Client - Internet Explorer",
                                   "Portal - BRAZIL_D - Pro Client - Internet Explorer",
                                   "BRAZIL_D - Pro Client [D] - Internet Explorer"])
def test_ie_frame(title: str) -> None:
    assert classify(raw(title, cls="IEFrame"), "BRAZIL_D", "iexplore.exe", "explorer.exe") == "ie"
    assert classify(raw(title, cls="IEFrame"), "BRAZIL_D", "iexplore.exe", None) == "ie"


def test_ie_frame_child_of_edge_is_ignored() -> None:
    title = "BRAZIL_D - Pro Client - Internet Explorer"
    assert classify(raw(title, cls="IEFrame"), "BRAZIL_D", "iexplore.exe", "msedge.exe") is None


def test_ie_needs_class_and_suffix() -> None:
    assert classify(raw("BRAZIL_D - Pro Client - Internet Explorer", cls="Other"), "BRAZIL_D", "iexplore.exe") is None
    assert classify(raw("BRAZIL_DX - Pro Client - Internet Explorer", cls="IEFrame"), "BRAZIL_D",
                    "iexplore.exe") is None
    assert classify(raw("BRAZIL_D - Pro Client", cls="IEFrame"), "BRAZIL_D", "iexplore.exe") is None


# ------------------------------------------------------------------------------------------- find_instances
def _world() -> FakeUser32:
    u = FakeUser32()
    u.add_process(100, LAUNCHER)
    u.add_process(200, "excel.exe")
    u.add_process(300, "iexplore.exe", parent_pid=301)
    u.add_process(301, "msedge.exe")
    u.add_process(400, LAUNCHER)
    return u


def test_find_instances_only_rumba_windows_of_the_profile() -> None:
    u = _world()
    a = u.add_window("BRAZIL_D - Pro Client", pid=100)
    b = u.add_window("BRAZIL_D - Pro Client [D]", pid=400)
    u.add_window("BRAZIL_D.xlsx - Excel", cls="XLMAIN", pid=200)
    u.add_window("BRAZIL_D - Pro Client - Internet Explorer", cls="IEFrame", pid=300)
    u.add_window("BRAZIL_Z - Pro Client", pid=100)
    u.add_window("BRAZIL_DX - Pro Client", pid=400)
    found = find_instances(u, D)
    assert [(i.hwnd, i.kind) for i in found] == [(a.hwnd, "launcher"), (b.hwnd, "launcher")]
    assert not u.calls_to("post_close") and not u.calls_to("show") and not u.calls_to("click")


def test_hllapi_hwnd_with_any_title_is_an_instance() -> None:
    u = _world()
    main = u.add_window("Sessão desconectada", pid=100)
    child = u.add_child(main.hwnd, "Static", "terminal")
    found = find_instances(u, D, hllapi_hwnds=[child.hwnd, None])
    assert [(i.hwnd, i.kind) for i in found] == [(main.hwnd, "hllapi")]


def test_hllapi_hwnd_of_another_program_is_ignored() -> None:
    u = _world()
    excel = u.add_window("Book1 - Excel", cls="XLMAIN", pid=200)
    edge_ie = u.add_window("Algo - Internet Explorer", cls="IEFrame", pid=300)
    assert find_instances(u, D, hllapi_hwnds=[excel.hwnd, edge_ie.hwnd]) == []


def test_launched_pid_without_main_window_contributes_its_windows() -> None:
    u = _world()
    stuck = u.add_window("Host Access Web Launcher", pid=100, cls="WindowsForms10.Window.8.app.0.2")
    prompt = u.add_dialog("Aviso", pid=100, texts=["Atualizando o cliente..."])
    u.add_window("Book1 - Excel", cls="XLMAIN", pid=200)            # PID que não lançamos: nunca entra
    found = find_instances(u, D, launched_pids=[100])
    assert [(i.hwnd, i.kind) for i in found] == [(stuck.hwnd, "launched"), (prompt.hwnd, "launched")]
    assert find_instances(u, D) == []                                  # sem o registro de PIDs, nada casa


def test_launched_rule_only_counts_visible_windows_of_launched_pids() -> None:
    u = _world()
    visible = u.add_window("Host Access Web Launcher", pid=100, cls="WindowsForms10.Window.8.app.0.2")
    u.add_window("GDI+ Window", pid=100, visible=False)
    u.add_window("Book1 - Excel", cls="XLMAIN", pid=200)
    found = find_instances(u, D, launched_pids=[100])
    assert [(i.hwnd, i.kind) for i in found] == [(visible.hwnd, "launched")]


def test_launched_pid_with_main_window_does_not_add_helper_windows() -> None:
    # ao vivo (2026-10-04): o launcher tem "Internet Explorer_Hidden" com WS_VISIBLE e sem título; WM_CLOSE nela
    # atrapalhou o encerramento e a DLL travou no QuerySessions seguinte
    u = _world()
    main = u.add_window("BRAZIL_D - Pro Client", pid=100, cls="WindowsForms10.Window.8.app.0.141b42a_r7_ad1")
    u.add_window("", pid=100, cls="Internet Explorer_Hidden")
    u.add_dialog("Aviso", pid=100, texts=["algo"])                   # diálogo sem dono: tratado como diálogo
    found = find_instances(u, D, launched_pids=[100])
    assert [(i.hwnd, i.kind) for i in found] == [(main.hwnd, "launcher")]


@pytest.mark.parametrize("cls", ["Internet Explorer_Hidden", "GDI+ Hook Window Class", "IME", "MSCTFIME UI",
                                 "SocketNotifyWindow", "Telnet3270 Application",
                                 ".NET-BroadcastEventWindow.4.0.0.0.141b42a.0", "AlgumaClasse"])
def test_launched_rule_ignores_helper_windows(cls: str) -> None:
    u = _world()
    stuck = u.add_window("Host Access Web Launcher", pid=100, cls="WindowsForms10.Window.8.app.0.2")
    u.add_window("" if cls == "AlgumaClasse" else "x", pid=100, cls=cls)   # sem título fora das classes principais
    found = find_instances(u, D, launched_pids=[100])
    assert [(i.hwnd, i.kind) for i in found] == [(stuck.hwnd, "launched")]


def test_launched_pid_with_instance_of_another_profile_is_left_alone() -> None:
    u = _world()
    u.add_window("BRAZIL_Z - Pro Client", pid=100)
    u.add_window("Algum diálogo", pid=100, cls="WindowsForms10.Window.8.app.0.2")
    assert find_instances(u, D, launched_pids=[100]) == []


def test_launched_pid_hllapi_error_dialog_is_never_an_instance() -> None:
    u = _world()
    u.add_dialog("HLLAPI ERROR", pid=100, texts=["Exceeded maximum number of clients, reboot for correction"])
    assert find_instances(u, D, launched_pids=[100]) == []


# ------------------------------------------------------------------------------------------- diálogos
def test_dialogs_by_owner_and_unowned_same_pid() -> None:
    u = _world()
    main = u.add_window("BRAZIL_D - Pro Client", pid=100)
    owned = u.add_dialog("Telnet3270 Application", owner=main.hwnd, texts=["Connecting To Address: x.y.z"])
    unowned = u.add_dialog("RUMBA API Service Control", pid=100, texts=["Short Name already in use"])
    u.add_dialog("Outro", pid=400, texts=["de outro processo"])
    inst = find_instances(u, D)[0]
    got = {(d.hwnd, d.kind) for d in dialogs_of(u, inst, profile_name=D.name)}
    assert got == {(owned.hwnd, "connecting"), (unowned.hwnd, "short_name")}


def test_unowned_dialog_of_pid_with_other_profile_is_not_ours() -> None:
    u = _world()
    main = u.add_window("BRAZIL_D - Pro Client", pid=100)
    u.add_window("BRAZIL_Z - Pro Client", pid=100)
    u.add_dialog("Confirmação", pid=100, texts=["Fechar?"])
    inst = next(i for i in find_instances(u, D) if i.hwnd == main.hwnd)
    assert dialogs_of(u, inst, profile_name=D.name) == []


def test_dialog_kinds() -> None:
    assert dialog_kind("Telnet3270 Application", ["Connecting To Address: h"]) == "connecting"
    assert dialog_kind("RUMBA API Service Control", ["Short Name already in use"]) == "short_name"
    assert dialog_kind("HLLAPI ERROR", ["Exceeded maximum number of clients"]) == "hllapi_error"
    assert dialog_kind("HLLAPI ERROR", []) == "hllapi_error"
    assert dialog_kind("Pro Client", ["Deseja sair?"]) == "other"
    assert dialog_kind("RUMBA API Service Control", ["outra coisa"]) == "other"
    assert is_ok_button("OK") and is_ok_button("&OK") and not is_ok_button("Cancel") and not is_ok_button("Sim")


def test_rumba_dialogs_filters_by_process_image() -> None:
    u = _world()
    ok = u.add_dialog("RUMBA API Service Control", pid=100, texts=["Short Name already in use"])
    u.add_dialog("RUMBA API Service Control", pid=200, texts=["Short Name already in use"])   # Excel?!
    assert [d.hwnd for d in rumba_dialogs(u, "short_name")] == [ok.hwnd]
    assert rumba_dialogs(u, "short_name", images={"iexplore.exe"}) == []


def test_hllapi_error_visible() -> None:
    u = _world()
    assert not hllapi_error_visible(u)
    u.add_dialog("HLLAPI ERROR", pid=100, texts=["Exceeded maximum number of clients"])
    assert hllapi_error_visible(u, snapshot=Snapshot(u))


# ------------------------------------------------------------------------------------------- sanitize
def test_sanitize_removes_hosts_urls_and_ips() -> None:
    text = ("Connecting To Address: mainframe-01.corp.example.com:23 via https://portal.example.com/x?y=1 "
            "IP 10.20.30.40 e host app.interno.example.net")
    clean = sanitize(text)
    assert "mainframe-01" not in clean and "portal.example.com" not in clean
    assert "10.20.30.40" not in clean and "app.interno" not in clean
    assert "Address: <host>" in clean and "<url>" in clean
    assert sanitize("BRAZIL_D - Pro Client") == "BRAZIL_D - Pro Client"
    assert sanitize("Address:   abc") == "Address:   <host>"


def test_instance_safe_title_is_sanitized() -> None:
    u = _world()
    u.add_window("BRAZIL_D - Pro Client host.corp.example.com", pid=100)
    inst = find_instances(u, D)[0]
    assert "corp" not in inst.safe_title and "<host>" in inst.safe_title


# ------------------------------------------------------------------------------------------- correções da verificação
def test_launched_pid_hosting_a_portal_profile_outside_profiles_is_left_alone() -> None:
    """Outro perfil do portal (fora de ``PROFILES``) no mesmo processo do launcher: nunca entra pela regra 'launched'."""
    u = _world()
    u.add_window("BRAZIL_Q - Pro Client [Q]", pid=100)
    u.add_window("Algum diálogo", pid=100, cls="WindowsForms10.Window.8.app.0.2")
    assert find_instances(u, D, launched_pids=[100]) == []
    assert Snapshot(u).other_profile_pids("BRAZIL_D") == {100}


def test_other_profile_detection_never_counts_the_profile_itself() -> None:
    u = _world()
    u.add_window("BRAZIL_D - Pro Client [D]", pid=100)
    u.add_window("Portal - BRAZIL_D - Pro Client - Internet Explorer", cls="IEFrame", pid=500)
    u.add_process(500, "iexplore.exe")
    u.add_window("CICS_X - Pro Client", pid=200)                     # Excel com título parecido: não é RUMBA
    assert Snapshot(u).other_profile_pids("BRAZIL_D") == set()
    u.add_window("Portal - OUTRO_P - Pro Client - Internet Explorer", cls="IEFrame", pid=600)
    u.add_process(600, "iexplore.exe")
    assert Snapshot(u).other_profile_pids("BRAZIL_D") == {600}


def test_launched_pid_reused_by_another_program_is_never_an_instance() -> None:
    u = _world()
    u.add_window("Book1 - Excel", cls="XLMAIN", pid=200)
    assert find_instances(u, D, launched_pids=[200]) == []


def test_repr_never_shows_raw_window_text() -> None:
    u = _world()
    main = u.add_window("BRAZIL_D - Pro Client", pid=100)
    u.add_dialog("Telnet3270 Application", owner=main.hwnd, pid=100,
                 texts=["Connecting To Address: host.example.com"])
    inst = find_instances(u, D)[0]
    dialog = dialogs_of(u, inst)[0]
    assert dialog.kind == "connecting" and "host.example.com" in dialog.texts[0]
    assert "host.example.com" not in repr(dialog) and "Pro Client" not in repr(inst)
