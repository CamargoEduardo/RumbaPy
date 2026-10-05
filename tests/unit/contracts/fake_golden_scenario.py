"""Roteiro determinístico do FakeEhllapi para provar que as extensões do P-0 vêm desligadas.

``golden/fake_ehllapi_golden.json`` foi gerado com o ``tests/fakes/fake_ehllapi.py`` ANTERIOR às extensões do P-0
(commit 929d8e7: sem ``response_delay``, ``lock_visible_after``, ``rc_override``, opções opostas no
``WD_SetSessionParamEx``). O teste ``test_fake_extensions.py`` roda este mesmo roteiro no simulador atual e exige
resultado idêntico.

Não regenere o JSON, salvo mudança INTENCIONAL de comportamento do simulador (e registre o motivo no commit). Para
regenerar a partir do simulador antigo: ``git show 929d8e7:tests/fakes/fake_ehllapi.py`` num módulo temporário e
``dumps(run(modulo))``. O conjunto ``params`` não é comparado: a remoção da opção oposta é a mudança intencional do
P-0 (testada à parte). Teclas locais novas no simulador (ex.: ``@H``) não mudam o golden, que não as usa.
"""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

import ctypes
import json


def _wire(value: Any) -> Any:
    """Converte argumentos/saídas em algo serializável e estável."""
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": bytes(value).hex()}
    if isinstance(value, ctypes.Array):
        return {"buffer": bytes(value).hex()}
    if isinstance(value, bool) or value is None or isinstance(value, (int, str, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(v) for v in value]
    return {"type": type(value).__name__}


def _session_state(session: Any) -> dict:
    return {
        "letter": session.letter,
        "rows": session.rows,
        "cols": session.cols,
        "buffer": bytes(session.buffer).hex(),
        "cursor": session.cursor,
        "inhibited": session.inhibited,
        "screen_name": session.screen_name,
        "fields": [[f.attr_pos, f.length, f.protected, f.numeric, f.hidden, f.mdt, f.name] for f in session.fields],
        "aid_log": [[aid, _wire(fields)] for aid, fields in session.aid_log],
    }


def run(mod: Any) -> dict:
    """Executa o roteiro no módulo ``mod`` (o ``fake_ehllapi``) e devolve tudo o que foi observado."""
    fake = mod.FakeEhllapi()
    steps: list = []

    def word(value: int = 0) -> ctypes.c_ushort:
        return ctypes.c_ushort(value)

    def call(label: str, name: str, *args: Any, outs: tuple = ()) -> Any:
        try:
            rc = getattr(fake, name)(*args)
        except AttributeError as exc:
            steps.append([label, name, "AttributeError", str(exc)])
            return None
        observed = []
        for out in outs:
            if isinstance(out, ctypes.c_ushort):
                observed.append(out.value)
            else:
                observed.append(bytes(out).hex())
        steps.append([label, name, _wire(rc), observed])
        return rc

    h = None
    # --- sem sessões
    count = word(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    call("qs-vazio", "WD_QuerySessions", h, ctypes.byref(count), buf, outs=(count,))
    call("connect-sem-sessao", "WD_ConnectPS", h, b"D")
    call("copyps-desconectado", "WD_CopyPS", h, ctypes.create_string_buffer(64), 0)

    d = fake.add_session("D")
    z = fake.add_session("Z", rows=32, cols=80, hwnd=0x2000, pid=5000)
    count = word(26)
    buf = ctypes.create_string_buffer(26 * 16 + 16)
    call("qs-duas", "WD_QuerySessions", h, ctypes.byref(count), buf, outs=(count, buf))
    for letter in (b"D", b"Z", b"Q", b" "):
        st = ctypes.create_string_buffer(32)
        st[0] = letter
        call(f"status-{letter.decode()}", "WD_QuerySessionStatus", h, st, outs=(st,))
    sysbuf = ctypes.create_string_buffer(64)
    call("query-system", "WD_QuerySystem", h, sysbuf, outs=(sysbuf,))
    call("connect-Q", "WD_ConnectPS", h, b"Q")
    call("connect-D", "WD_ConnectPS", h, b"D")

    env, host = mod.make_cics_login_host()
    d.load(env)
    d.on_aid = host

    ps = ctypes.create_string_buffer(1920 + 64)
    call("copyps-env", "WD_CopyPS", h, ps, 1920, outs=(ps,))
    out = ctypes.create_string_buffer(64)
    call("copy-linha14", "WD_CopyPSToString", h, 1041, out, 40, outs=(out,))
    call("copy-pos0", "WD_CopyPSToString", h, 0, ctypes.create_string_buffer(8), 4)
    call("copy-len0", "WD_CopyPSToString", h, 5, ctypes.create_string_buffer(8), 0)
    call("copy-estoura", "WD_CopyPSToString", h, 1900, ctypes.create_string_buffer(64), 40)
    cur = word()
    call("cursor", "WD_QueryCursorLocation", h, ctypes.byref(cur), outs=(cur,))
    for pos in (1736, 1, 1735, 1920):
        attr = word()
        call(f"attr-{pos}", "WD_QueryFieldAttribute", h, ctypes.byref(attr), pos, outs=(attr,))
    for code in (b"T ", b"N ", b"P ", b"NU", b"NP", b"PU", b"PP"):
        p = word()
        n = word()
        call(f"ffpos-{code.decode()}", "WD_FindFieldPosition", h, ctypes.byref(p), 1736, ctypes.create_string_buffer(code), outs=(p,))
        call(f"fflen-{code.decode()}", "WD_FindFieldLength", h, ctypes.byref(n), 1736, ctypes.create_string_buffer(code), outs=(n,))
    fbuf = ctypes.create_string_buffer(16)
    call("copyfield", "WD_CopyFieldToString", h, 1736, fbuf, 10, outs=(fbuf,))
    call("copyfield-curto", "WD_CopyFieldToString", h, 1736, ctypes.create_string_buffer(4), 0)
    call("csps-C", "WD_CopyStringToPS", h, 1736, b"C", 1)
    call("csps-protegido", "WD_CopyStringToPS", h, 1, b"X", 1)
    call("csps-transborda", "WD_CopyStringToPS", h, 1736, b"CC", 2)
    call("csps-len0", "WD_CopyStringToPS", h, 1736, b"C", 0)
    call("csps-pos0", "WD_CopyStringToPS", h, 0, b"C", 1)
    call("csps-volta", "WD_CopyStringToPS", h, 1736, b" ", 1)
    call("setcursor", "WD_SetCursor", h, 1736)
    call("setcursor-0", "WD_SetCursor", h, 0)
    oia = ctypes.create_string_buffer(110)
    call("oia-livre", "WD_CopyOIA", h, oia, 103, outs=(oia,))

    # --- teclas: ambiente -> signon -> menu
    call("sendkey-C-enter", "WD_SendKey", h, b"C@E")
    call("copyps-signon", "WD_CopyPS", h, ps, 1920, outs=(ps,))
    call("cursor-signon", "WD_QueryCursorLocation", h, ctypes.byref(cur), outs=(cur,))
    call("sendkey-uid-tab", "WD_SendKey", h, b"A000000@T")
    call("sendkey-senha-enter-resto", "WD_SendKey", h, b"senha@EXYZ@E")
    call("copyps-menu", "WD_CopyPS", h, ps, 1920, outs=(ps,))
    call("sendkey-256", "WD_SendKey", h, b"A" * 256)
    call("sendkey-255", "WD_SendKey", h, b"@0" + b"B" * 70)
    call("sendkey-arroba", "WD_SendKey", h, b"@0@@x@@")
    call("sendkey-invalido", "WD_SendKey", h, b"@0Q@!")
    call("sendkey-locais", "WD_SendKey", h, b"@0abc@L@<@Z@U@V@N@B@T@F@R@q@I@D@P")
    call("sendkey-erase-input", "WD_SendKey", h, b"@A@F")
    call("sendkey-dup", "WD_SendKey", h, b"@S@x@S@y")
    call("sendkey-a-invalido", "WD_SendKey", h, b"@A@Z")
    call("copyps-apos-locais", "WD_CopyPS", h, ps, 1920, outs=(ps,))
    # digitar em área protegida => X -f (inibido)
    call("setcursor-protegido", "WD_SetCursor", h, 1)
    call("sendkey-protegido", "WD_SendKey", h, b"Q")
    call("copyps-inibido", "WD_CopyPS", h, ps, 1920)
    call("oia-inibido", "WD_CopyOIA", h, oia, 103, outs=(oia,))
    call("wait-inibido", "WD_Wait", h)
    call("connect-inibido", "WD_ConnectPS", h, b"D")
    call("sendkey-autoreset", "WD_SendKey", h, b"@0Z")
    # ocupado (X SYSTEM)
    d.lock_for(1000)
    call("copyps-ocupado", "WD_CopyPS", h, ps, 1920)
    call("copy-ocupado", "WD_CopyPSToString", h, 1, ctypes.create_string_buffer(8), 4)
    call("oia-ocupado", "WD_CopyOIA", h, oia, 103, outs=(oia,))
    call("sendkey-ocupado", "WD_SendKey", h, b"A")
    call("setcursor-ocupado", "WD_SetCursor", h, 81)
    call("csps-ocupado", "WD_CopyStringToPS", h, 81, b"A", 1)
    call("connect-ocupado", "WD_ConnectPS", h, b"D")
    call("wait-ocupado", "WD_Wait", h)
    call("param-nwait", "WD_SetSessionParamEx", h, b"NWAIT")
    call("wait-ocupado-nwait", "WD_Wait", h)
    d.locked_until = 0.0
    call("wait-livre", "WD_Wait", h)
    # busca
    call("param-srch", "WD_SetSessionParamEx", h, b"SRCHFROM,SRCHBKWD")
    call("param-invalido", "WD_SetSessionParamEx", h, b"BOGUS EAB")
    found = word()
    call("search-signon", "WD_SearchPS", h, ctypes.byref(found), 200, b"Signon", outs=(found,))
    call("search-ausente", "WD_SearchPS", h, ctypes.byref(found), 1, b"NAOEXISTE", outs=(found,))
    call("search-vazio", "WD_SearchPS", h, ctypes.byref(found), 1, b"", outs=(found,))
    call("cstofield", "WD_CopyStringToField", h, 82, b"TRANSACAO")
    call("searchfield-ausente", "WD_SearchField", h)
    call("hwnd", "WD_GetSessionHWND", h)
    call("show-6", "WD_ShowSession", h, 6)
    call("show-10", "WD_ShowSession", h, 10)
    call("pause", "WD_Pause", h, 1)
    call("multithread", "MultiThreadConnectionOn")
    call("disconnect", "WD_DisconnectPS", h)
    call("disconnect-2", "WD_DisconnectPS", h)
    call("cursor-desconectado", "WD_QueryCursorLocation", h, ctypes.byref(cur))
    call("hwnd-desconectado", "WD_GetSessionHWND", h)
    call("reset", "WD_ResetSystem", h)

    # --- Z (32x80): senha inválida e senha expirada
    env_z, host_z = mod.make_cics_login_host("B111111", "outra")
    z.load(env_z)
    z.on_aid = host_z
    call("connect-Z", "WD_ConnectPS", h, b"Z")
    call("z-ambiente", "WD_SendKey", h, b"c@E")
    call("z-ambiente-C", "WD_SendKey", h, b"@0C@E")
    call("z-signon-invalido", "WD_SendKey", h, b"B111111@Terrada@E")
    zps = ctypes.create_string_buffer(2560 + 64)
    call("z-copyps", "WD_CopyPS", h, zps, 2560, outs=(zps,))
    env_x, host_x = mod.make_cics_login_host("B111111", "outra", expired=True)
    z.load(env_x)
    z.on_aid = host_x
    call("z-ambiente-2", "WD_SendKey", h, b"@0C@E")
    call("z-signon-expirada", "WD_SendKey", h, b"B111111@Toutra@E")
    call("z-copyps-2", "WD_CopyPS", h, zps, 2560, outs=(zps,))
    call("z-copyfield-oculto", "WD_CopyFieldToString", h, (13 - 1) * 80 + 21, ctypes.create_string_buffer(16), 8)
    fake.remove_session("Z")
    call("z-removida", "WD_CopyPS", h, zps, 2560)
    fake.add_session("C", connect_delay=10_000)
    call("connect-atraso", "WD_ConnectPS", h, b"C")
    call("pf-sem-conexao", "WD_SendKey", h, b"@3")

    return {
        "steps": steps,
        "calls": [[name, _wire(list(args))] for name, args in fake.calls],
        "sessions": {letter: _session_state(s) for letter, s in sorted(fake.sessions.items())},
        "connected": fake.connected,
        "reset_count": fake.reset_count,
        "multithread": fake.multithread,
        "detached_z": _session_state(z),
    }


def dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=True, indent=1, sort_keys=True)
