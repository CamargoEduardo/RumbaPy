"""Salvaguardas dos roteiros ao vivo (J.6), com dublês: não tocam o RUMBA; rodam no sandbox e no CI.

Decisão de pular (inclusive o teto ``RUMBAPY_GUARD``), confirmação ``SIM <letra>`` (``CONIN$`` falso; nenhum atalho
por ambiente), credenciais só digitadas, varredura/redação da senha, registro do guard tirado do log do worker
(AIDs, ``op=connect``, NORESET), aceite do relatório e as recusas do executor (``tests/live``).

Ficam fora de ``tests/live`` porque o ``tests/conftest.py`` decide "ao vivo" por ``item.keywords``, que inclui o nome
da pasta ``live``: qualquer teste dentro dela seria pulado sem ``RUMBAPY_LIVE=1``.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.live.support import Credentials, SCRIPTS
from tests.live import support, runner

import datetime
import json
import ast
import os
import re

import pytest

LIVE_OK = {"RUMBAPY_LIVE": "1"}
LIVE_MODULES = ("support.py", "runner.py", "scripts.py", "test_live.py", "conftest.py", "__init__.py")
RESEARCH = Path(__file__).resolve().parents[2] / "tools" / "research"
# toda sonda ao vivo de tools/research (as versões avulsas antigas viraram invólucros do executor)
PROBES = tuple(sorted(p.name for p in RESEARCH.glob("live_*_probe.py")))
SECRET = "Pz8%q;W2"                   # '%' e ';' de propósito (configparser com interpolação/comentário quebraria)


class FakeConsole:
    """``Console`` falso: respostas prontas, prompts registrados."""

    def __init__(self, *answers: str, password: str = "", fail: type[BaseException] | None = None) -> None:
        self.answers = list(answers)
        self.password = password
        self.fail = fail
        self.prompts: list[str] = []
        self.closed = False

    def readline(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if self.fail is not None:
            raise self.fail()
        return self.answers.pop(0)

    def getpass(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.password

    def write(self, text: str) -> None:
        self.prompts.append(text)

    def close(self) -> None:
        self.closed = True


def no_console() -> None:
    return None


def forbidden_console() -> Any:
    raise AssertionError("o console não devia ser aberto")


# ------------------------------------------------------------------------------------------- decisão de pular

def test_skip_without_live_and_always_with_sandbox() -> None:
    assert "RUMBAPY_LIVE=1" in str(support.skip_reason("l1", {}, pointer_bits=64, branch="dev"))
    assert "RUMBAPY_LIVE=1" in str(support.skip_reason("l1", {"RUMBAPY_LIVE": "0"}, pointer_bits=64, branch="dev"))
    reason = support.skip_reason("l1", {"RUMBAPY_LIVE": "1", "RUMBAPY_SANDBOX": "1"}, pointer_bits=64, branch="dev")
    assert reason is not None and "SANDBOX" in reason


def test_skip_in_this_test_session_for_every_script() -> None:
    """Nesta sessão (sandbox ligado pelo conftest) todo roteiro é pulado, inclusive com RUMBAPY_LIVE=1."""
    env = dict(os.environ, RUMBAPY_LIVE="1")
    assert os.environ["RUMBAPY_SANDBOX"] == "1"
    for name in SCRIPTS:
        assert support.skip_reason(name, env, pointer_bits=64, branch="dev") is not None


def test_skip_for_32_bit_other_branch_and_unknown_script() -> None:
    assert "64-bit" in str(support.skip_reason("l1", LIVE_OK, pointer_bits=32, branch="dev"))
    assert "dev" in str(support.skip_reason("l1", LIVE_OK, pointer_bits=64, branch="master"))
    assert "desconhecida" in str(support.skip_reason("l1", LIVE_OK, pointer_bits=64, branch=None))
    assert "desconhecido" in str(support.skip_reason("l99", LIVE_OK, pointer_bits=64, branch="dev"))
    assert support.skip_reason("l1", LIVE_OK, pointer_bits=64, branch="dev") is None


def test_l7_needs_explicit_authorization() -> None:
    reason = support.skip_reason("l7", LIVE_OK, pointer_bits=64, branch="dev")
    assert reason is not None and "RUMBAPY_LIVE_L7_AUTORIZADO=1" in reason
    assert support.skip_reason("l7", dict(LIVE_OK, RUMBAPY_LIVE_L7_AUTORIZADO="sim"), pointer_bits=64,
                               branch="dev") is not None
    assert support.skip_reason("l7", dict(LIVE_OK, RUMBAPY_LIVE_L7_AUTORIZADO="1"), pointer_bits=64,
                               branch="dev") is None


def test_guards_follow_j6() -> None:
    from rumbapy import _protocol

    assert support.GUARDS == {"l1": "readonly", "l2": "readonly", "l3": "readonly", "l3b": "readonly", "l4": "text",
                              "l5": "readonly", "l6": "login", "l7": "login", "l8": "readonly", "l9": "readonly",
                              "compat": "login", "close": "readonly"}
    assert all(guard in _protocol.GUARD_MODES and guard != "none" for guard in support.GUARDS.values())
    assert set(support.GUARD_ORDER) == set(_protocol.GUARD_MODES) - {"none"}
    assert {name for name, s in SCRIPTS.items() if s.credentials} == {"l6", "l7", "compat"}
    assert set(support.PYTEST_SCRIPTS) == {"l1", "l2", "l3", "l3b", "l4", "l5", "l6", "l7", "l8", "l9", "compat"}
    from tests.live.scripts import SCRIPT_FUNCTIONS

    assert set(SCRIPT_FUNCTIONS) == set(SCRIPTS)


def test_aid_names_match_the_package() -> None:
    from rumbapy import keys

    assert support.AID_KEY_NAMES == {key.name for key in keys.AIDS}


def test_guard_ceiling_from_the_owner() -> None:
    """``RUMBAPY_GUARD`` do dono é teto: mais restritivo pula o roteiro; inválido/none pula tudo; vazio não conta."""
    def reason(script: str, guard: str) -> str | None:
        return support.skip_reason(script, dict(LIVE_OK, RUMBAPY_GUARD=guard), pointer_bits=64, branch="dev")

    assert reason("l1", "readonly") is None and reason("l4", "text") is None and reason("l6", "login") is None
    assert reason("l1", "login") is None and reason("l4", "LOGIN") is None and reason("l1", "") is None
    blocked = reason("l6", "readonly")
    assert blocked is not None and "L6 precisa do guard login" in blocked
    assert reason("compat", "text") is not None and reason("l4", "readonly") is not None
    for bad in ("none", "tudo"):
        text = reason("l1", bad)
        assert text is not None and "não vale ao vivo" in text


def test_profile_names_match_the_package() -> None:
    from rumbapy import profiles

    assert {letter: profiles.resolve(letter).name for letter in support.PROFILE_NAMES} == support.PROFILE_NAMES


def test_matching_windows_and_what_to_check() -> None:
    titles = ["BRAZIL_D - Pro Client", "Outra - BRAZIL_Z - Pro Client", "BRAZIL_DX", "Bloco de notas",
              "IMS_A - Pro Client [Connecting]"]
    assert support.matching_windows(titles, ("D", "Z")) == ["BRAZIL_D - Pro Client", "Outra - BRAZIL_Z - Pro Client"]
    assert support.matching_windows(titles, ("A",)) == ["IMS_A - Pro Client [Connecting]"]
    assert support.terminal_windows(("D",), lambda: titles) == ["BRAZIL_D - Pro Client"]
    assert support.windows_to_check("l1") == ("D", "Z")
    assert support.windows_to_check("l7") == ("D", "Z", "A")


def test_live_modules_never_import_rumbapy_at_import_time() -> None:
    """O pytest ao vivo importa estes módulos: um ``import rumbapy`` no topo rodaria o auto-update fora do sandbox."""
    for name in LIVE_MODULES:
        tree = ast.parse((support.REPO / "tests" / "live" / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "rumbapy" for a in node.names), name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "rumbapy", name


def test_no_confirmation_or_credentials_shortcut_anywhere() -> None:
    """Nenhum atalho que dispense a pessoa no console: sem RUMBAPY_LIVE_CONFIRM, sem credenciais por arquivo/stdin."""
    files = [support.REPO / "tests" / "live" / name for name in LIVE_MODULES] + [RESEARCH / n for n in PROBES]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for banned in ("RUMBAPY_LIVE_CONFIRM", "credentials-stdin", "configparser", "config.ini", "from_wire"):
            assert banned not in text, (path.name, banned)


# ------------------------------------------------------------------------------------------- confirmação

@pytest.mark.parametrize(("answer", "ok"), [("SIM D", True), ("sim d", True), ("  SIM D ", True), ("SIM", False),
                                            ("sim", False), ("SIM Z", False), ("SIMM D", False), ("", False),
                                            ("NAO", False), ("SIM D X", False), ("D SIM", False)])
def test_accepted_answers_need_the_letter(answer: str, ok: bool) -> None:
    assert support.accepted(answer, ("D",)) is ok


def test_accepted_answers_with_two_letters() -> None:
    assert support.accepted("SIM Z", ("Z", "D")) and support.accepted("SIM D", ("Z", "D"))
    assert support.confirm_phrase("l9") == "SIM D" and support.confirm_phrase("l7") == "SIM A"


def test_confirm_has_no_environment_shortcut() -> None:
    import inspect

    assert "env" not in inspect.signature(support.confirm).parameters
    ok, reason = support.confirm("l1", console_factory=no_console)
    assert ok is False and "E30" in reason and "SIM <letra>" in reason


def test_confirm_typed_in_the_console() -> None:
    console = FakeConsole("SIM D")
    assert support.confirm("l1", console_factory=lambda: console) == (True, "console")
    assert console.closed and "PRODUÇÃO" in console.prompts[0] and "guard readonly" in console.prompts[0]
    assert "SIM D" in console.prompts[0]
    for answer in ("ok", "SIM"):
        refused = FakeConsole(answer)
        ok, reason = support.confirm("l6", console_factory=lambda c=refused: c)
        assert ok is False and "L6 não confirmado" in reason and "SIM D" in reason and refused.closed
    broken = FakeConsole(fail=EOFError)
    assert support.confirm("l1", console_factory=lambda: broken)[0] is False and broken.closed


def test_has_console_closes_it() -> None:
    console = FakeConsole()
    assert support.has_console(lambda: console) is True and console.closed and console.prompts == []
    assert support.has_console(no_console) is False


# ------------------------------------------------------------------------------------------- credenciais

def test_credentials_only_typed_in_the_console() -> None:
    import inspect

    assert set(inspect.signature(support.get_credentials).parameters) == {"section", "console_factory"}
    console = FakeConsole("A000001", password=SECRET)
    typed = support.get_credentials("cics", console_factory=lambda: console)
    assert typed is not None and (typed.user, typed.password, typed.source) == ("A000001", SECRET, "console")
    assert console.closed and all(SECRET not in p for p in console.prompts)
    assert support.get_credentials("cics", console_factory=no_console) is None
    assert support.get_credentials("cics", console_factory=lambda: FakeConsole("")) is None
    assert support.get_credentials("cics", console_factory=lambda: FakeConsole("A0", password="")) is None
    assert support.get_credentials("cics", console_factory=lambda: FakeConsole(fail=OSError)) is None


def test_credentials_never_shown() -> None:
    creds = Credentials("A000000", SECRET)
    for text in (repr(creds), str(creds), f"{creds}", repr([creds]), repr({"c": creds})):
        assert SECRET not in text and "A000000" not in text
    assert not hasattr(creds, "to_wire")


# ------------------------------------------------------------------------------------------- varredura da senha

def test_secret_found_in_texts_and_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    (home / "code" / "src").mkdir(parents=True)
    (home / "logs" / "worker-20261004.log").write_text("op=send text_units=omitido\n", encoding="utf-8")
    (home / "code" / "src" / "x.py").write_text(f"# {SECRET}\n", encoding="utf-8")   # cópia do código: fora
    files = support.files_to_scan(home)
    assert [p.name for p in files] == ["worker-20261004.log"]
    assert support.secret_found(SECRET, ["<Session D ok>", "{}"], files) is False
    assert support.secret_found(SECRET, [f"erro {SECRET}"], files) is True
    for encoding in ("utf-8", "cp1252", "utf-16-le"):
        leak = home / "boot" / f"boot-{encoding}.txt"
        leak.parent.mkdir(exist_ok=True)
        leak.write_bytes(f"senha={SECRET}ção".encode(encoding))
        assert support.secret_found(SECRET, (), support.files_to_scan(home)) is True
        leak.unlink()
    assert support.secret_found(SECRET, (), [home / "nao-existe.log"]) is False
    with pytest.raises(ValueError):
        support.secret_found("", ["qualquer"])


def test_secret_variants_cover_accented_passwords() -> None:
    variants = support.secret_variants("çã€")
    assert "çã€".encode("cp1252") in variants and "çã€".encode("utf-8") in variants
    cyrillic = support.secret_variants("абв")
    assert "абв".encode("utf-8") in cyrillic and "абв".encode("utf-16-le") in cyrillic
    escaped = b"\\u0430\\u0431\\u0432"   # json.dumps com ensure_ascii e ascii()
    assert escaped in cyrillic and escaped.decode().encode("utf-16-le") in cyrillic
    assert b"???" not in cyrillic               # fora do cp1252: nenhuma forma em bytes do worker (nada de '???')
    cp = support.secret_text_variants("senhaçã€")
    raw = "senhaçã€".encode("cp1252")
    assert raw.hex() in cp and raw.hex().upper() in cp and repr(raw)[2:-1] in cp   # repr de bytes e hex do buffer
    # JSON escapa a aspa; repr não; e o hex dos bytes cp1252 (o buffer do worker)
    assert support.secret_text_variants('a"b') == ['612262', 'a\\"b', 'a"b']


def test_secret_locations_and_redaction(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    leak = tmp_path / "logs" / "client-1.log"
    leak.write_text(f"x {SECRET} y", encoding="utf-8")
    report = {"error": f"HostError: {SECRET}", "tela": ["ok", f"..{SECRET}.."], "n": 3, "ok": True}
    where = support.secret_locations(SECRET, report, ["limpo", f"repr {SECRET}"], [leak], tmp_path)
    assert where == ["relatório:error", "relatório:tela", "texts[1]", "arquivo:" + str(Path("logs/client-1.log"))]
    clean = support.redact(report, SECRET)
    assert SECRET not in json.dumps(clean) and clean["error"] == "HostError: ***" and clean["n"] == 3
    assert clean["tela"] == ["ok", "..***.."] and clean["ok"] is True
    assert support.redact({SECRET: Path("p")}, SECRET) == {"***": "p"}
    with pytest.raises(ValueError):
        support.redact("x", "")


ESCAPED_SECRETS = ['ab"cd', "a\\b", "x\ty", "q'w\"e", "çã\\É€", "tab\t\"\\"]


@pytest.mark.parametrize("secret", ESCAPED_SECRETS)
def test_secret_found_in_escaped_forms(secret: str, tmp_path: Path) -> None:
    """Aspas, barra invertida, tabulação e acento mudam de forma em JSON/``%r``: a varredura acha mesmo assim."""
    report = {"error": f"HostError: senha {secret}", "nested": {"lista": [1, {"k": f"..{secret}"}]}, "n": 7}
    assert support.secret_locations(secret, report) == ["relatório:error", "relatório:nested"]
    assert support.secret_locations(secret, {f"chave {secret}": 1}) == [f"relatório:chave {secret}"]
    for text in (json.dumps({"p": secret}), json.dumps({"p": secret}, ensure_ascii=False), repr({"p": secret}),
                 ascii(secret), f"%r -> {secret!r}"):
        assert support.secret_found(secret, [text]), text
        clean = support.redact({"t": text}, secret)
        assert not support.secret_found(secret, support.report_strings(clean)), text
    for encoding in ("utf-8", "cp1252", "utf-16-le"):
        log = tmp_path / f"worker-x-D.log.{encoding}"
        try:
            log.write_bytes(f"op=x {secret!r} {json.dumps(secret)}".encode(encoding))
        except UnicodeEncodeError:
            continue
        assert support.secret_found(secret, (), [log]), encoding
    clean_report = support.redact(report, secret)
    assert support.secret_locations(secret, clean_report) == [] and clean_report["n"] == 7


def test_scan_and_redact_with_escaped_password(tmp_path: Path) -> None:
    secret = 'ab"c\\d'
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "client-1.log").write_text(f"repr={secret!r}\n", encoding="utf-8")
    dirty = runner.scan_and_redact({"error": f"HostError: {secret}"}, secret, [], home)
    assert dirty["password_found_anywhere"] is True
    assert dirty["password_found_in"] == ["relatório:error", "arquivo:" + str(Path("logs/client-1.log"))]
    assert not support.secret_found(secret, [json.dumps(dirty, ensure_ascii=False)])


def test_report_strings_walks_everything() -> None:
    value = {"a": ["x", ("y", {"z": Path("p")})], 3: None, "b": {1.5, "s"}, "c": True}
    assert sorted(support.report_strings(value)) == sorted(["a", "x", "y", "z", "p", "3", "b", "s", "c"])


# ------------------------------------------------------------------------------------------- registro do guard

def log_line(pid: int, message: str, level: str = "INFO") -> str:
    """Linha no formato do log do worker (``_log.FORMAT`` com ``L=<letra>``)."""
    return f"2026-10-04 12:00:00.123 {level:<5} {pid} T-dll L=D {message}"


WORKER_LOG = [
    log_line(41, "start pid=41 python=3.13.2 letter=D guard=login commit=x fake=False"),
    log_line(41, "op=connect id=1 letter=D rc=0 ms=15.2 ok"),
    log_line(41, "op=snapshot id=2 rc=0 ms=3.1 ok"),
    log_line(41, "op=read id=3 row=14 col=26 len=24 rc=0 ms=1.0 ok"),
    log_line(41, "op=write id=4 row=14 col=50 len=1 rc=0 ms=2.0 ok"),
    log_line(41, "op=send id=5 keys=[ENTER] text_units=0 chunks=1 rc=0 ms=9.0 ok"),
    log_line(41, "op=write id=6 row=20 col=20 secret rc=0 ms=2.0 ok"),
    log_line(41, "op=send id=7 keys=[ENTER] text_units=0 chunks=1 rc=0 ms=9.0 ok"),
    log_line(41, "stop: DisconnectPS rc=0"),
]


def write_worker_log(home: Path, lines: list[str], name: str = "worker-x-D.log") -> None:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "logs" / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_guard_audit_keeps_writes_aids_connects_and_markers(tmp_path: Path) -> None:
    write_worker_log(tmp_path, WORKER_LOG)
    (tmp_path / "logs" / "client-1.log").write_text("op=send keys=[PF3] do cliente não conta\n", encoding="utf-8")
    audit = support.guard_audit_lines(tmp_path)
    assert audit == [WORKER_LOG[i] for i in (0, 1, 4, 5, 6, 7, 8)]       # sem snapshot/read (só contadas)
    assert support.read_op_counts(tmp_path) == {"snapshot": 1, "read": 1}
    assert support.sent_aids(audit) == ["ENTER", "ENTER"]
    assert support.connect_records(audit) == [{"pid": 41, "rc": 0, "ms": 15.2}]
    assert support.noreset_rc(audit) == 0
    assert support.guard_audit_lines(tmp_path / "vazio") == []


def test_guard_audit_blocked_reads_and_noreset_warning(tmp_path: Path) -> None:
    lines = [log_line(7, "op=connect id=1 letter=D rc=0 ms=1.0 ok"),
             log_line(7, "SetSessionParamEx(NORESET) rc=2 (tolerado)", "WARN"),
             log_line(7, "op=snapshot id=2 GuardViolation BLOQUEADO"),
             log_line(7, "op=send id=3 keys=[TAB,PF3,PAGE_UP] text_units=omitido chunks=1 rc=0 ms=1.0 ok")]
    write_worker_log(tmp_path, lines)
    audit = support.guard_audit_lines(tmp_path)
    assert audit == lines
    assert support.noreset_rc(audit) == 2
    assert support.sent_aids(audit) == ["PF3", "PAGE_UP"]                 # TAB é tecla local
    assert support.noreset_rc([]) is None
    assert support.connect_records([log_line(9, "op=connect id=1 letter=D ms=3.0 erro")]) == [
        {"pid": 9, "rc": None, "ms": 3.0}]


def test_rotated_worker_logs_are_read_oldest_first(tmp_path: Path) -> None:
    """``worker-...log.5`` (mais antigo) .. ``.1`` e depois o atual: AIDs do arquivo rotacionado não somem."""
    write_worker_log(tmp_path, [log_line(41, "op=send id=1 keys=[PF3] text_units=0 chunks=1 rc=0 ms=1.0 ok")],
                     "worker-x-D.log.2")
    write_worker_log(tmp_path, [log_line(41, "op=send id=2 keys=[CLEAR] text_units=0 chunks=1 rc=0 ms=1.0 ok")],
                     "worker-x-D.log.1")
    write_worker_log(tmp_path, [log_line(41, "op=send id=3 keys=[ENTER] text_units=0 chunks=1 rc=0 ms=1.0 ok")])
    write_worker_log(tmp_path, ["não é log do worker"], "worker-x-D.json")
    assert [p.name for p in support.worker_logs(tmp_path)] == ["worker-x-D.log.2", "worker-x-D.log.1",
                                                              "worker-x-D.log"]
    assert support.sent_aids(support.guard_audit_lines(tmp_path)) == ["PF3", "CLEAR", "ENTER"]
    assert support.rotated_worker_logs(tmp_path) == ["worker-x-D.log.2", "worker-x-D.log.1"]
    report: dict[str, Any] = {}
    runner.attach_audit(report, tmp_path)
    assert report["worker_logs_rotated"] == ["worker-x-D.log.2", "worker-x-D.log.1"]
    problems = support.check_report("l1", good_report("l1", guard_audit=audit_for("l1"),
                                                      worker_logs_rotated=report["worker_logs_rotated"]))
    assert any("rotacionou" in p for p in problems)


def test_guard_audit_cut_is_flagged_but_aids_use_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(support, "AUDIT_LIMIT", 3)
    lines = audit_for("l1") + [log_line(1, f"op=write id={n} row=1 col=1 len=1 rc=0 ms=1.0 ok") for n in range(5)]
    write_worker_log(tmp_path, lines + [log_line(1, "op=send id=9 keys=[PF3] text_units=0 chunks=1 rc=0 ms=1 ok")])
    report: dict[str, Any] = {}
    runner.attach_audit(report, tmp_path)
    assert len(report["guard_audit"]) == 3 and report["guard_audit_truncated"] is True
    assert report["aids_sent"] == ["PF3"]
    problems = support.check_report("l1", good_report("l1", guard_audit=report["guard_audit"],
                                                      guard_audit_truncated=True))
    assert any("foi cortado" in p for p in problems)


def test_runner_attach_audit(tmp_path: Path) -> None:
    write_worker_log(tmp_path, WORKER_LOG)
    report: dict[str, Any] = {}
    runner.attach_audit(report, tmp_path)
    assert report["aids_sent"] == ["ENTER", "ENTER"] and report["noreset_rc"] == 0
    assert report["connects"] == [{"pid": 41, "rc": 0, "ms": 15.2}] and report["read_ops"]["snapshot"] == 1
    assert any("guard=login" in line for line in report["guard_audit"])


# ------------------------------------------------------------------------------------------- relatório e aceite

def audit_for(script: str, aids: tuple[str, ...] = ()) -> list[str]:
    guard = SCRIPTS[script].guard
    lines = [log_line(1, f"start pid=1 python=3.13.2 letter=D guard={guard} commit=x fake=False"),
             log_line(1, "op=connect id=1 letter=D rc=0 ms=10.0 ok")]
    lines += [log_line(1, f"op=send id={n} keys=[{aid}] text_units=0 chunks=1 rc=0 ms=1.0 ok")
              for n, aid in enumerate(aids, 2)]
    return lines


GOOD_EXTRA: dict[str, dict[str, Any]] = {
    "l1": {"state_after_close": "closed", "cursor": [22, 56], "geometry": [24, 80], "windows_after_close": []},
    "l3": {"second_owner": {"type": "TerminalInUse", "message": "m"}, "read_after_recycle": "Select",
           "child_attach": {"attach": "ok"}, "close_with_live_owner": 1,
           "child_after_close": {"attach": "ok", "read_after_close": "SessionReplaced: m", "state": "replaced"}},
    "l3b": {"helper": {"connect_rc": 0, "exit": 3, "pid": 5}, "read_after_exit": "Select",
            "new_worker_connects": [{"pid": 2, "rc": 11, "ms": 5.0}, {"pid": 2, "rc": 0, "ms": 9.0}]},
    "l4": {"too_long": "Truncated delivery=none", "protected": "ProtectedField delivery=none", "after_erase": " ",
           "after_text": {"skipped": "não coube no campo", "field_length": 1},
           **{f"after_{c}": c for c in support.L4_CHARS}},
    "l6": {"logon": "ok"},
    "l9": {"z_read_after_close_d": "x", "windows_after_close_d": ["BRAZIL_Z - Pro Client"]},
    "compat": {"logon_cics": True, "close_terminal": True},
}


def good_report(script: str, **extra: Any) -> dict[str, Any]:
    spec = SCRIPTS[script]
    aids = ("ENTER", "ENTER") if script in support.LOGIN_AIDS else ()
    report: dict[str, Any] = {"script": script, "guard": spec.guard, "windows_left": [],
                              "workers": {"D": {"pid": 1, "saiu": True}}, "guard_audit": audit_for(script, aids)}
    if spec.credentials:
        report["password_found_anywhere"] = False
    report.update(GOOD_EXTRA.get(script, {}))
    report.update(extra)
    return report


@pytest.mark.parametrize("script", ["l1", "l3", "l3b", "l4", "l6", "l9", "compat"])
def test_check_report_accepts_good_reports(script: str) -> None:
    assert support.check_report(script, good_report(script)) == []


def test_check_report_accepts_l8_without_terminal() -> None:
    assert support.check_report("compat", good_report("compat", workers="nenhum vivo")) == []
    assert support.check_report("l8", {"guard": "readonly", "legacy_exit": 0,
                                       "status": {"present": True, "disconnected": 1}}) == []


@pytest.mark.parametrize(("change", "expected"), [
    ({"error": f"HostError: {SECRET}"}, "terminou com erro (HostError)"),
    ({"password_found_anywhere": True}, "varredura da senha"),
    ({"password_found_anywhere": None}, "varredura da senha"),
    ({"password_found_in": ["relatório:error"]}, "a senha apareceu em: relatório:error"),
    ({"windows_left": ["BRAZIL_D - Pro Client"]}, "sobrou janela"),
    ({"cleanup_close_D": 1}, "a limpeza fechou (cleanup_close_D)"),
    ({"workers": {"D": {"pid": 1, "saiu": False}}}, "worker não saiu no fim: D"),
    ({"workers": "RuntimeError: x"}, "encerramento dos workers"),
    ({"guard": "none"}, "guard do relatório"),
    ({"guard_audit": []}, "guard=login"),
    ({"guard_audit": audit_for("l6", ("ENTER",))}, "exatamente 2 ENTER (o log mostra 1)"),
    ({"guard_audit": audit_for("l6", ("ENTER", "ENTER", "ENTER"))}, "exatamente 2 ENTER"),
    ({"guard_audit": audit_for("l6", ("ENTER", "PF3"))}, "AID além de ENTER"),
    ({"logon": {"type": "InvalidPassword", "message": "m"}}, "logon: valor inesperado"),
])
def test_check_report_flags_problems_without_citing_values(change: dict[str, Any], expected: str) -> None:
    report = good_report("l6", **change)
    problems = support.check_report("l6", report)
    assert any(expected in p for p in problems), problems
    assert all(SECRET not in p for p in problems)


def test_cleanup_close_after_a_failure_is_not_a_second_problem() -> None:
    problems = support.check_report("l1", good_report("l1", error="WaitTimeout: x", cleanup_close_D=1))
    assert not any("limpeza fechou" in p for p in problems)


@pytest.mark.parametrize(("script", "aids", "expected"), [
    ("l1", ("ENTER",), "mandou AID num roteiro readonly: ENTER"),
    ("l4", ("PF3",), "mandou AID num roteiro text: PF3"),
    ("l3b", ("CLEAR",), "mandou AID num roteiro readonly"),
])
def test_check_report_no_aid_outside_login(script: str, aids: tuple[str, ...], expected: str) -> None:
    report = good_report(script, guard_audit=audit_for(script, aids))
    assert any(expected in p for p in support.check_report(script, report))


@pytest.mark.parametrize(("script", "change", "key"), [
    ("l1", {"windows_after_close": ["BRAZIL_D - Pro Client"]}, "windows_after_close"),
    ("l3", {"child_attach": {"attach": "TerminalInUse: m"}}, "child_attach"),
    ("l3", {"close_with_live_owner": 0}, "close_with_live_owner"),
    ("l3", {"close_with_live_owner": True}, "close_with_live_owner"),
    ("l3", {"child_after_close": {"read_after_close": "Select"}}, "child_after_close"),
    ("l3", {"child_after_close": None}, "child_after_close"),
    ("l3b", {"helper": {"connect_rc": 0, "exit": 0}}, "helper"),
    ("l3b", {"helper": {"connect_rc": 11, "exit": 3}}, "helper"),
    ("l3b", {"new_worker_connects": [{"pid": 2, "rc": 11}]}, "new_worker_connects"),
    ("l3b", {"read_after_exit": {"type": "NotConnected", "message": "m"}}, "read_after_exit"),
    ("l4", {"after_ç": "?"}, "after_ç"),
    ("l4", {"after_É": None}, "after_É"),
    ("l4", {"after_text": "TESTE ça?€"}, "after_text"),
    ("l4", {"too_long": "aceito (ERRO)"}, "too_long"),
    ("l9", {"windows_after_close_d": []}, "windows_after_close_d"),
    ("l9", {"windows_after_close_d": ["BRAZIL_D - Pro Client"]}, "windows_after_close_d"),
    ("l9", {"windows_after_close_d": ["BRAZIL_Z - Pro Client", "BRAZIL_D - Pro Client"]}, "windows_after_close_d"),
])
def test_check_report_script_expectations(script: str, change: dict[str, Any], key: str) -> None:
    assert f"{key}: valor inesperado" in support.check_report(script, good_report(script, **change))


def test_report_path_and_write(tmp_path: Path) -> None:
    when = datetime.datetime(2026, 10, 4, 12, 34, 56)
    assert support.report_path("l1", when).name == "live_v2_l1_2026-10-04_123456.json"
    assert support.report_path("l1").parent == support.REPO / "docs" / "interno" / "referencia"
    path = support.write_report({"ç": "€", "p": Path("x")}, tmp_path / "sub" / "r.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {"ç": "€", "p": "x"}


def test_report_dir_and_config_are_not_versioned() -> None:
    """O relatório (telas, títulos) vai para docs/interno/, que está no .gitignore (repositório público)."""
    ignore = (support.REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "docs/interno/" in ignore and "config.ini" in ignore


# ------------------------------------------------------------------------------------------- recusas do executor

def refuse(script: str, *, env: dict[str, str] | None = None, titles: list[str] | None = None,
           console: Any = no_console, confirm: bool = True,
           branch: str | None = "dev") -> tuple[str | None, str, Credentials | None]:
    """``runner.refusal`` só com dublês (ambiente, git, janelas, console e a pré-condição do L8 falsos)."""
    return runner.refusal(script, confirm=confirm, env=env if env is not None else dict(LIVE_OK), pointer_bits=64,
                          branch=lambda: branch, titles=lambda: titles or [], console_factory=console,
                          l8_check=lambda: None)


def test_refusal_in_the_sandbox_and_without_live() -> None:
    reason, _, creds = runner.refusal("l1", console_factory=forbidden_console,
                                      titles=lambda: pytest.fail("não devia listar janelas"))
    assert reason is not None and creds is None                    # este processo está no sandbox
    assert "RUMBAPY_LIVE=1" in str(refuse("l1", env={})[0])


def test_refusal_when_a_terminal_window_is_already_open() -> None:
    reason, _, _ = refuse("l1", titles=["BRAZIL_Z - Pro Client"], console=forbidden_console)
    assert reason is not None and "não mexo na sessão de ninguém" in reason
    assert "BRAZIL" not in reason                                  # o título (pode ter dados) não vai à mensagem
    reason, _, _ = refuse("l7", env=dict(LIVE_OK, RUMBAPY_LIVE_L7_AUTORIZADO="1"),
                          titles=["IMS_A - Pro Client"], console=forbidden_console)
    assert reason is not None


def test_refusal_needs_the_console_even_with_the_old_variable() -> None:
    env = dict(LIVE_OK, RUMBAPY_LIVE_CONFIRM="SIM", RUMBAPY_LIVE_CONFIRMED_BY="console")
    for confirm in (True, False):
        reason, how, _ = refuse("l1", env=env, confirm=confirm)
        assert reason is not None and "E30" in reason and how == ""
    assert refuse("l4", console=lambda: FakeConsole("SIM D")) == (None, "console", None)
    assert refuse("l4", console=lambda: FakeConsole("SIM"))[0] is not None
    assert refuse("l4", branch="master", console=forbidden_console)[0] is not None
    assert refuse("l6", env=dict(LIVE_OK, RUMBAPY_GUARD="readonly"), console=forbidden_console)[0] is not None


def test_precheck_for_pytest_does_not_ask_anything() -> None:
    """``confirm=False`` (pytest): só confere que há console, sem ler nada dele; quem pergunta é o executor."""
    console = FakeConsole()
    assert refuse("l6", console=lambda: console, confirm=False) == (None, "", None)
    assert console.closed and console.prompts == []


def test_refusal_asks_credentials_after_the_confirmation() -> None:
    console = FakeConsole("SIM D", "A000000", password=SECRET)
    reason, how, creds = refuse("l6", console=lambda: console)
    assert reason is None and how == "console" and creds is not None and creds.password == SECRET
    assert "PRODUÇÃO" in console.prompts[0] and console.prompts[1].startswith("Usuário [cics]")
    reason, _, creds = refuse("compat", console=lambda: FakeConsole("SIM D", ""))
    assert creds is None and reason is not None and "sem credenciais" in reason and SECRET not in reason
    reason, _, creds = refuse("compat", console=lambda: FakeConsole("SIM X"))
    assert creds is None and reason is not None and "não confirmado" in reason


def test_l8_precondition_is_checked_before_the_console() -> None:
    reason, _, _ = runner.refusal("l8", env=dict(LIVE_OK), pointer_bits=64, branch=lambda: "dev",
                                  titles=lambda: [], console_factory=forbidden_console,
                                  l8_check=lambda: "L8: prepare antes")
    assert reason == "L8: prepare antes"


def test_runner_main_refuses_in_the_sandbox(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main(["--script", "l1"]) == runner.EXIT_REFUSED
    assert "RECUSADO:" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- executor: senha e HOME

def test_scan_and_redact_never_keeps_the_password(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "worker-x-D.log").write_text("op=write secret\n", encoding="utf-8")
    clean = runner.scan_and_redact({"logon": "ok"}, SECRET, ["<Session D>"], home)
    assert clean == {"logon": "ok", "password_found_anywhere": False}
    (home / "logs" / "client-1.log").write_text(f"vazou {SECRET}\n", encoding="utf-8")
    dirty = runner.scan_and_redact({"error": f"X: {SECRET}", "tela": [SECRET]}, SECRET, [f"r {SECRET}"], home)
    assert dirty["password_found_anywhere"] is True and SECRET not in json.dumps(dirty, ensure_ascii=False)
    assert dirty["password_found_in"] == ["relatório:error", "relatório:tela", "texts[0]",
                                          "arquivo:" + str(Path("logs/client-1.log"))]
    assert dirty["error"] == "X: ***"


def test_remove_home(tmp_path: Path) -> None:
    import logging

    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    handler = logging.FileHandler(home / "logs" / "client-1.log", encoding="utf-8")
    log = logging.getLogger("rumbapy")
    log.addHandler(handler)
    try:
        assert runner.remove_home(home, keep=True) is False and home.exists()
        assert runner.remove_home(home, keep=False) is True and not home.exists()
        assert handler not in log.handlers                     # o log aberto deste processo foi fechado antes
    finally:
        log.removeHandler(handler)
        handler.close()


# ------------------------------------------------------------------------------------------- pytest e roteiros

def test_pytest_skips_while_a_timed_out_runner_is_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.live import test_live

    class Alive:
        pid = 4242

        def poll(self) -> None:
            return None

    monkeypatch.setattr(test_live, "_STILL_RUNNING", [Alive()])
    with pytest.raises(pytest.skip.Exception, match="ainda em execução \\(PID 4242\\)"):
        test_live._prepare("l8")


def test_pytest_never_sends_confirmation_or_credentials_to_the_runner() -> None:
    """O executor é lançado com stdin fechado e o ambiente herdado: nada confirma no lugar do console."""
    tree = ast.parse((support.REPO / "tests" / "live" / "test_live.py").read_text(encoding="utf-8"))
    popen = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    assert len(popen) == 1
    keywords = {kw.arg: kw.value for kw in popen[0].keywords}
    assert ast.unparse(keywords["stdin"]) == "subprocess.DEVNULL"
    # o único acréscimo ao ambiente herdado é a codificação da saída (nada de confirmação nem credenciais)
    assert "env" not in keywords or ast.unparse(keywords["env"]) == "env"
    assigns = [ast.unparse(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "env" for t in node.targets)]
    assert assigns in ([], ["dict(os.environ, PYTHONIOENCODING='utf-8')"])


def test_read_json_file_tolerates_partial_writes(tmp_path: Path) -> None:
    from tests.live.scripts import read_json_file

    path = tmp_path / "child.json"
    assert read_json_file(path, 0) is None
    path.write_text('{"attach": ', encoding="utf-8")            # gravação pela metade
    assert read_json_file(path, 0.3) is None
    path.write_text('{"attach": "ok"}', encoding="utf-8")
    assert read_json_file(path, 0) == {"attach": "ok"}


def test_child_scripts_write_atomically() -> None:
    from tests.live import scripts

    for code in (scripts.CHILD, scripts.HELPER):
        ast.parse(code)
        assert "os.replace(tmp, out)" in code and 'open(out, "w"' not in code
    assert "os._exit(3)" in scripts.HELPER and 'guard="readonly"' in scripts.HELPER
    for banned in ("disconnect", "send_key", "copy_string", "reset_system", "TerminateProcess"):
        assert banned not in scripts.HELPER


def test_research_probes_use_the_shared_code() -> None:
    """As sondas de tools/research chamam o executor/roteiros de tests/live (sem cópia da lógica)."""
    for name in PROBES:
        tree = ast.parse((RESEARCH / name).read_text(encoding="utf-8"))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert "tests.live" in modules or "tests.live.runner" in modules, name
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        assert not defined & {"snapshot_code", "python32", "l1", "l6", "stop_workers"}, name


def test_every_live_probe_is_a_runner_wrapper() -> None:
    """Nenhuma sonda ao vivo contorna a J.6: todas passam pelo executor (confirmação no console, guard, HOME)."""
    assert set(PROBES) >= {"live_v2_probe.py", "live_close_probe.py", "live_legacy_probe.py", "live_l3b_probe.py",
                           "live_session_probe.py", "live_worker_probe.py"}
    for name in PROBES:
        tree = ast.parse((RESEARCH / name).read_text(encoding="utf-8"))
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert "runner.main" in calls, name


DIRECT_DLL = re.compile(r"(WinDLL|CDLL|LoadLibrary\w*)\s*\([^)]*(ehlapi|DLL_PATH|\bDLL\b)", re.IGNORECASE)


def test_no_direct_dll_load_launcher_path_or_window_message_in_live_code() -> None:
    """Fora de ``hllapi.load_dll`` (que sempre embrulha no GuardedDll) ninguém carrega a ehlapi32.Dll; nada de
    caminho fixo do launcher nem ``PostMessage``/``SendMessage`` direto (só pelo ``_user32`` do pacote)."""
    files = [support.REPO / "tests" / "live" / name for name in LIVE_MODULES] + [RESEARCH / n for n in PROBES]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert DIRECT_DLL.search(text) is None, path.name
        for banned in ("ehlapi32.Dll\"", "Program Files (x86)", "PostMessageW", "SendMessageW", "TerminateProcess(",
                       '"taskkill'):
            assert banned not in text, (path.name, banned)
    helper = __import__("tests.live.scripts", fromlist=["HELPER"]).HELPER
    assert "hllapi.load_dll(hllapi.DLL_PATH, guard=\"readonly\")" in helper


def test_l3b_helper_has_its_own_watchdog() -> None:
    from tests.live import scripts

    assert "os._exit(9)" in scripts.HELPER and "threading.Thread(target=watchdog, daemon=True).start()" in scripts.HELPER
    assert scripts.HELPER.index("threading.Thread(target=watchdog") < scripts.HELPER.index("hllapi.load_dll")
    assert scripts.HELPER_WATCHDOG_EXIT == 9 and 0 < scripts.HELPER_WATCHDOG_S <= 30
    assert support.check_report("l3b", good_report(
        "l3b", helper={"connect_rc": 0, "exit": 3, "watchdog": "travou"})) == ["helper: valor inesperado"]


def test_scripts_use_the_package_launcher_and_profiles() -> None:
    """E.1: o L2 usa profiles.launcher_arg e _launcher.default_find_launcher (sem caminho/URL duplicados)."""
    text = (support.REPO / "tests" / "live" / "scripts.py").read_text(encoding="utf-8")
    assert "profiles.launcher_arg" in text and "default_find_launcher" in text
    assert "rumba.srv.volvo.com" not in text and "Host Access Web Launcher.exe" not in text




# ------------------------------------------------------------------------------------------- outras sondas de tools/research

WINDOW_MESSAGES = re.compile(r"\b(DdeConnect\w*|SendMessage\w*|PostMessage\w*|GetWindowText[AW]?|SendNotifyMessage\w*)"
                             r"\s*[(.]")


def _load_research(name: str) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"research_{name}", RESEARCH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_tools_that_touch_the_real_rumba_are_gated() -> None:
    """Toda ferramenta de tools/research (fora das ``live_*_probe.py``) que carrega a ehlapi32.Dll ou manda mensagem a
    janelas reais exige ``RUMBAPY_LIVE=1``, recusa o sandbox e pede a confirmação no console (``CONIN$``)."""
    touching = []
    for path in sorted(RESEARCH.glob("*.py")):
        if path.name in PROBES:
            continue
        text = path.read_text(encoding="utf-8")
        if DIRECT_DLL.search(text) or WINDOW_MESSAGES.search(text):
            touching.append(path.name)
            for needed in ('"RUMBAPY_LIVE"', '"RUMBAPY_SANDBOX"', '"CONIN$"', "TerminateProcess(", '"taskkill'):
                if needed in ("TerminateProcess(", '"taskkill'):
                    assert needed not in text, (path.name, needed)
                else:
                    assert needed in text, (path.name, needed)
    assert {"probe_hllapi.py", "dde_list.py"} <= set(touching)
    assert "enum_windows.py" not in touching                # títulos por InternalGetWindowText (sem WM_GETTEXT)


def test_probe_hllapi_refuses_without_live_and_in_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _load_research("probe_hllapi")
    assert "RUMBAPY_LIVE=1" in str(probe.refusal({}))
    assert "SANDBOX" in str(probe.refusal({"RUMBAPY_LIVE": "1", "RUMBAPY_SANDBOX": "1"}))
    assert probe.refusal({"RUMBAPY_LIVE": "1"}) is None

    def no_dll(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a sonda não pode carregar a DLL aqui")
    monkeypatch.setattr(probe.ctypes, "WinDLL", no_dll)
    monkeypatch.setenv("RUMBAPY_LIVE", "1")
    assert probe.main() == 2                                # o sandbox do conftest recusa antes de qualquer coisa


def test_probe_hllapi_confirmation_is_typed_on_the_console() -> None:
    import io

    probe = _load_research("probe_hllapi")
    assert probe.confirmed(lambda: None) is False
    assert probe.confirmed(lambda: (io.StringIO("sim\n"), io.StringIO())) is False
    assert probe.confirmed(lambda: (io.StringIO("SIM\n"), io.StringIO())) is True


def test_probe_hllapi_watchdog_and_reset_system(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    probe = _load_research("probe_hllapi")
    text = (RESEARCH / "probe_hllapi.py").read_text(encoding="utf-8")
    for line in text.splitlines():                          # toda chamada WD_* passa pelo watchdog
        if re.search(r"dll\.WD_\w+\(", line):
            assert "lambda: dll.WD_" in line and "guarded(" in line, line
    assert "WD_ResetSystem" in text and probe.LIMITS["WD_QuerySessions"] == 8.0
    exited = threading.Event()
    monkeypatch.setattr(probe.os, "_exit", lambda code: exited.set())
    monkeypatch.setitem(probe.LIMITS, "WD_Teste", 0.05)
    probe.guarded("WD_Teste", lambda: time.sleep(0.3))
    assert exited.is_set()
