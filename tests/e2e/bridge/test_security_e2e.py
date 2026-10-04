"""T-SEC-1 de ponta a ponta (J.5): a senha só sai do cliente nas requisições ``write``/``send`` ao worker.

Pilha inteira: ``rumbapy.attach`` (fábricas de ``session._set_factories``: ``FakeUser32`` + ``Launcher`` com
``spawn``/``run_script`` proibidos) -> ``_bridge`` real -> worker REAL 32-bit com ``--fake-dll`` (fábricas
``secret_*`` de ``scenarios_bridge``, senhas únicas). Um espião em ``_ipc.Channel.send_msg/recv_msg`` guarda todo JSON
trafegado; no fim, com os workers já encerrados, varre cada arquivo de ``RUMBAPY_HOME`` (``worker-*.log``,
``client-*.log`` em DEBUG, ``boot\\*.txt``, registros) e os textos de ``repr``/``str``/``exc.screen``/``details`` e
do ``doctor``. Nada aqui carrega a DLL real nem toca janelas reais.
"""
# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from tests.e2e.bridge.scenarios_bridge import SECRET, SECRET_LT, WRONG
from tests.unit.client.session.support import user32_with_window
from tests.e2e.bridge.conftest import PY32, SCENARIOS, Env
from tests.unit.worker.helpers import REPO
from tests.fakes import scenarios
from rumbapy.errors import InvalidPassword, LogonFailed, RumbaError, Truncated, UndefinedKey
from rumbapy import _bridge, _doctor, _ipc, _launcher, _log
from rumbapy import session as session_mod
from rumbapy.compat import RumbaClient

import logging
import json

import rumbapy
import pytest

pytestmark = pytest.mark.e2e

OUTGOING_WITH_SECRET = {"write", "send"}          # as únicas ops que podem levar a senha (o texto a digitar)
ENCODINGS = ("utf-8", "cp1252", "utf-16-le")


def _forbidden(what: str) -> Callable[..., Any]:
    def call(*args: Any, **kw: Any) -> Any:
        raise AssertionError(f"o teste de segurança não pode {what}")
    return call


class Probe:
    """Espião do canal + textos observados + varredura final de ``RUMBAPY_HOME``."""

    def __init__(self, env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
        self.env = env
        self.monkeypatch = monkeypatch
        self.wire: list[tuple[str, dict[str, Any]]] = []
        self.texts: list[tuple[str, str]] = []
        self.clients: list[Any] = []
        send, recv = _ipc.Channel.send_msg, _ipc.Channel.recv_msg
        wire = self.wire

        def spy_send(channel: _ipc.Channel, obj: dict[str, Any], **kw: Any) -> bool:
            wire.append(("out", json.loads(json.dumps(obj))))
            return send(channel, obj, **kw)

        def spy_recv(channel: _ipc.Channel, timeout: float) -> dict[str, Any] | None:
            msg = recv(channel, timeout)
            if msg is not None:
                wire.append(("in", json.loads(json.dumps(msg))))
            return msg

        monkeypatch.setattr(_ipc.Channel, "send_msg", spy_send)
        monkeypatch.setattr(_ipc.Channel, "recv_msg", spy_recv)

    def scenario(self, factory: str) -> None:
        self.monkeypatch.setenv("RUMBAPY_TEST_FAKE_DLL", f"{SCENARIOS}:{factory}")

    def attach(self, terminal: str = "D") -> session_mod.Session:
        t = rumbapy.attach(terminal, timeout=20, python32=PY32)
        self.clients.append(t)
        return t

    def compat(self, terminal: str = "D") -> RumbaClient:
        """``RumbaClient`` cujo ``open`` (logon_*) vira ``attach``: aqui nada pode abrir o RUMBA."""
        def factory(action: str, letter: str, *, python32: Any = None, timeout: float | None = None) -> Any:
            return self.attach(letter)

        client = RumbaClient(terminal, PY32, auto_update=False, _session_factory=factory)
        self.clients.append(client)
        return client

    def see(self, label: str, obj: Any) -> None:
        """Guarda ``repr``/``str`` de ``obj``; de uma ``RumbaError`` também mensagem, dica, detalhes e a tela."""
        self.texts += [(f"{label} repr", repr(obj)), (f"{label} str", str(obj))]
        if isinstance(obj, RumbaError):
            self.texts += [(f"{label} message", obj.message), (f"{label} hint", str(obj.hint)),
                           (f"{label} details", json.dumps(obj.details, default=str, ensure_ascii=False))]
            if obj.screen is not None:
                self.see(f"{label}.screen", obj.screen)
                self.texts.append((f"{label}.screen text", obj.screen.text))
        elif hasattr(obj, "to_text") and hasattr(obj, "text"):           # Screen
            self.texts += [(f"{label} text", obj.text), (f"{label} to_text", obj.to_text())]

    def raises(self, label: str, exc_type: type[BaseException], fn: Callable[[], Any]) -> BaseException:
        with pytest.raises(exc_type) as info:
            fn()
        self.see(label, info.value)
        return info.value

    def doctor(self) -> None:
        probes = _doctor.Probes(letters=("D", "A"), remote_sha=lambda: "a" * 40, find_runtime=lambda: None,
                                is_file=lambda p: True, listener_pid=lambda port: None)
        report = _doctor.collect(verbose=True, probes=probes)
        self.texts += [("doctor", report.to_text(verbose=True)), ("doctor dict", json.dumps(report.to_dict())),
                       ("doctor repr", repr(report))]

    # ----------------------------------------------------------------- asserções finais
    def finish(self, *secrets: str) -> None:
        """Libera as sessões, encerra os workers (logs completos) e procura cada senha em tudo o que foi observado."""
        for client in self.clients:
            self.see("cliente", client)
            if isinstance(client, session_mod.Session):
                client.release()
        self.env.cleanup()
        _log.flush_all()
        carried: set[str] = set()
        for direction, msg in self.wire:
            blob = json.dumps(msg, ensure_ascii=False)
            for secret in secrets:
                if secret not in blob:
                    continue
                assert direction == "out", f"senha numa mensagem do worker: {msg.get('op') or msg.get('event')}"
                assert msg.get("op") in OUTGOING_WITH_SECRET, f"senha na requisição {msg.get('op')!r}"
                carried.add(secret)
        assert carried, "o espião não viu nenhuma senha sair (o cenário não digitou?)"
        for label, text in self.texts:
            for secret in secrets:
                assert secret not in text, f"senha em {label}"
        files = [p for p in self.env.home.rglob("*") if p.is_file()]
        names = {p.name for p in files}
        assert any(n.startswith("worker-") and n.endswith(".log") for n in names), names
        assert any(n.startswith("client-") and n.endswith(".log") for n in names), names
        assert self.env.boot_logs(), "nenhum boot\\*.txt"
        for path in files:
            data = path.read_bytes()
            for secret in secrets:
                for encoding in ENCODINGS:
                    assert secret.encode(encoding) not in data, f"senha em {path.relative_to(self.env.home)}"


@pytest.fixture
def probe(env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[Probe]:
    monkeypatch.setenv("RUMBAPY_DEV", "1")
    monkeypatch.setenv("RUMBAPY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RUMBAPY_TEST_FAKE_PATH", str(REPO))
    monkeypatch.setattr(session_mod, "_ensure_latest", lambda: None)
    bridge = _bridge.Bridge()                                  # SpawnConfig.from_env: o dublê vem do ambiente
    env.bridges.append(bridge)
    monkeypatch.setattr(_bridge, "_default_bridge", bridge)
    user32 = user32_with_window()
    user32.add_process(scenarios.PID_A, _launcher.LAUNCHER_IMAGE)
    user32.add_window("IMS_A - Pro Client", pid=scenarios.PID_A, hwnd=scenarios.HWND_A)

    def launcher(u32: Any, get_backend: Any) -> _launcher.Launcher:
        return _launcher.Launcher(u32, get_backend, spawn=_forbidden("abrir o Web Launcher"),
                                  run_script=_forbidden("rodar o cscript"), neutralize=lambda letter: None,
                                  hllapi_client_pids=lambda: [])

    session_mod._set_factories(user32_factory=lambda: user32, launcher_factory=launcher)
    monkeypatch.setattr(_log, "_client_handler", None)
    root = logging.getLogger("rumbapy")
    old_level = root.level
    handler = _log.setup_client_logging("DEBUG")               # client-*.log em DEBUG (J.5)
    try:
        yield Probe(env, monkeypatch)
    finally:
        session_mod._set_factories(backend_factory=None, user32_factory=None, launcher_factory=None)
        root.removeHandler(handler)
        handler.close()
        root.setLevel(old_level)


# ------------------------------------------------------------------------------------------- cenários

def test_cics_logon_right_and_wrong_password(probe: Probe) -> None:
    probe.scenario("secret_cics")
    t = probe.attach()
    probe.raises("logon errado", InvalidPassword, lambda: t.cics.logon(scenarios.VALID_USER, WRONG, timeout=10))
    probe.see("tela após logon errado", t.snapshot(fields=True))
    probe.see("logon certo", t.cics.logon(scenarios.VALID_USER, SECRET, timeout=10))
    probe.see("sessão", t)
    probe.doctor()
    probe.finish(SECRET, WRONG)


def test_send_keys_without_secret_right_and_wrong(probe: Probe) -> None:
    """``send_keys('A000000<Tab>senha<Enter>')`` SEM ``secret``: o worker mascara o campo oculto mesmo assim."""
    probe.scenario("secret_cics")
    t = probe.attach()
    t.fill(22, 56, "C").enter(timeout=10)
    t.wait_text("TERMINAL", 4, 6, timeout=10)
    t.send_keys(f"{scenarios.VALID_USER}<Tab>{WRONG}<Enter>", timeout=10)
    probe.see("tela após senha errada", t.wait_text("SENHA INVALIDA", 19, 19, timeout=10))
    probe.see("read do campo oculto", t.read(13, 21, 8))
    t.send_keys(f"{scenarios.VALID_USER}<Tab>{SECRET}<Enter>", timeout=10)
    probe.see("tela após senha certa", t.wait_text("Signon is complete", 5, 20, timeout=10))
    probe.doctor()
    probe.finish(SECRET, WRONG)


def test_password_with_lt_in_logon_and_send_keys(probe: Probe) -> None:
    """Senha com ``<`` (achado 1): sem ``<<`` o tokenizer recusa sem citar o trecho e nada é enviado."""
    probe.scenario("secret_cics_lt")
    t = probe.attach()
    t.fill(22, 56, "C").enter(timeout=10)
    t.wait_text("TERMINAL", 4, 6, timeout=10)
    before = len(probe.wire)
    exc = probe.raises("send_keys com '<' cru", UndefinedKey,
                       lambda: t.send_keys(f"{scenarios.VALID_USER}<Tab>{SECRET_LT}<Enter>"))
    assert isinstance(exc, UndefinedKey) and "Kp2W" not in str(exc)
    assert not any(d == "out" and m.get("op") == "send" for d, m in probe.wire[before:])  # recusado antes do worker
    exc = probe.raises("send_keys secret com '<' cru", UndefinedKey,
                       lambda: t.send_keys(f"{scenarios.VALID_USER}<Tab>{SECRET_LT}<Enter>", secret=True))
    escaped = SECRET_LT.replace("<", "<<")
    t.send_keys(f"{scenarios.VALID_USER}<Tab>{escaped}<Enter>", timeout=10)
    probe.see("tela após senha com '<'", t.wait_text("Signon is complete", 5, 20, timeout=10))
    probe.finish(SECRET_LT)


def test_password_with_lt_via_cics_logon(probe: Probe) -> None:
    probe.scenario("secret_cics_lt")
    t = probe.attach()
    probe.see("logon com '<'", t.cics.logon(scenarios.VALID_USER, SECRET_LT, timeout=10))
    probe.finish(SECRET_LT)


def test_compat_on_hidden_field(probe: Probe) -> None:
    """``copy_string_to_ps``/``copy_ps_to_string``/``copy_field``/``send_key`` (v0.1.5) no campo oculto."""
    probe.scenario("secret_cics")
    client = probe.compat()
    client.copy_string_to_ps(22, 56, "C")
    client.send_key("ENTER")
    client.screen_load(4, 6, "TERMINAL")
    client.copy_string_to_ps(12, 21, scenarios.VALID_USER)
    client.copy_string_to_ps(13, 21, WRONG)                     # sem secret: o worker promove (campo oculto)
    probe.see("copy_ps_to_string do campo", client.copy_ps_to_string(13, 21, 8))
    probe.see("copy_ps_to_string da tela", client.copy_ps_to_string(1, 1, 24 * 80))
    probe.see("copy_field", client.copy_field(13, 21))
    probe.raises("copy_string_to_ps longo", Truncated, lambda: client.copy_string_to_ps(13, 21, WRONG + "9"))
    client.send_key("ENTER")
    client.screen_load(19, 19, "SENHA INVALIDA")
    probe.see("tela após senha errada", client.copy_ps_to_string(1, 1, 24 * 80))
    assert client.logon_cics(scenarios.VALID_USER, SECRET) is True
    probe.see("cliente logado", client)
    probe.doctor()
    probe.finish(SECRET, WRONG)


def test_ims_logon_right_and_wrong(probe: Probe) -> None:
    probe.scenario("secret_ims")
    t = probe.attach("A")
    assert t.rows == 32
    probe.raises("logon IMS errado", LogonFailed, lambda: t.ims.logon(scenarios.VALID_USER, WRONG, timeout=3))
    probe.see("tela após logon IMS errado", t.snapshot(fields=True))
    # o host simulado volta à "Welcome": a 2ª tentativa é a mão (a receita começa na USS)
    t.fill(10, 47, scenarios.VALID_USER).fill(11, 47, SECRET, secret=True).enter(timeout=10)
    probe.see("tela Start", t.wait_text("Start", 16, 20, timeout=10))
    probe.see("read do campo oculto", t.read(11, 47, 8))
    probe.finish(SECRET, WRONG)


def test_slow_host_logon(probe: Probe) -> None:
    probe.scenario("secret_cics_slow")
    t = probe.attach()
    probe.see("logon com host lento", t.cics.logon(scenarios.VALID_USER, SECRET, timeout=15))
    probe.doctor()
    probe.finish(SECRET)


@pytest.mark.parametrize("leak", ["log", "resposta", "texto"])
def test_probe_catches_a_leak(probe: Probe, leak: str) -> None:
    """O próprio T-SEC-1 tem de falhar se a senha vazar (senão a suíte acima passaria sem verificar nada)."""
    probe.scenario("secret_cics")
    t = probe.attach()
    t.cics.logon(scenarios.VALID_USER, SECRET, timeout=10)
    if leak == "log":
        logging.getLogger("rumbapy.teste").debug("vazamento proposital %s", SECRET)
        where = "client-"
    elif leak == "resposta":
        probe.wire.append(("in", {"id": 999, "ok": True, "result": {"text": SECRET}}))
        where = "mensagem do worker"
    else:
        probe.texts.append(("exceção forjada", f"erro: {SECRET}"))
        where = "exceção forjada"
    with pytest.raises(AssertionError, match=where):
        probe.finish(SECRET)
