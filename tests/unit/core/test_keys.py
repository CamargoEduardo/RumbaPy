"""Aceite P-A (B.6): ``Key.parse/pf``, ``tokenize_mnemonic``, ``tokenize_extra``, ``split_at_aids``, ``encode_body``."""
# ruff: noqa: I001
from __future__ import annotations

from typing import Any

from rumbapy.keys import (AIDS, CHUNK_MAX_BYTES, Key, Segment, encode_body, split_at_aids, tokenize_extra,
                          tokenize_mnemonic)
from rumbapy.errors import EncodingError, ParameterError, UndefinedKey
from rumbapy import _protocol
from rumbapy import keys

import pytest


# ------------------------------------------------------------------ tokenize_mnemonic (aceite P-A)

def test_tokenize_mnemonic_acceptance() -> None:
    assert tokenize_mnemonic("SPRE@E") == ["SPRE", Key.ENTER]
    assert tokenize_mnemonic("@@") == ["@"]
    assert tokenize_mnemonic("@A@F") == [Key.ERASE_INPUT]
    with pytest.raises(UndefinedKey):
        tokenize_mnemonic("abc@")


def test_tokenize_mnemonic_round_trips_encode_body() -> None:
    body = ["user@volvo", Key.TAB, "a@@b", Key.ERASE_EOF]
    chunks = encode_body(body, host="cics")
    tokens = [t for chunk in chunks for t in tokenize_mnemonic(chunk)]
    assert tokens == body


# ------------------------------------------------------------------ Key.parse / Key.pf

@pytest.mark.parametrize("name, key", [
    ("ENTER", Key.ENTER), ("enter", Key.ENTER), ("PF3", Key.PF3), ("F3", Key.PF3), ("<Pf3>", Key.PF3),
    ("pf24", Key.PF24), ("PAGE_UP", Key.PAGE_UP), ("PAGEUP", Key.PAGE_UP), ("page down", Key.PAGE_DOWN),
    ("PageDown", Key.PAGE_DOWN), ("erase-eof", Key.ERASE_EOF), ("ERASEEOF", Key.ERASE_EOF), ("@3", Key.PF3),
    ("@a", Key.PF10), ("@A@F", Key.ERASE_INPUT), ("<@E>", Key.ENTER), (" Tab ", Key.TAB), ("backtab", Key.BACKTAB),
    ("FIELDEXIT", Key.FIELD_EXIT), ("sysreq", Key.SYSREQ), ("PA2", Key.PA2), ("<clear>", Key.CLEAR),
])
def test_parse_accepts(name: str, key: Key) -> None:
    assert Key.parse(name) is key


def test_parse_passes_keys_through() -> None:
    for key in Key:
        assert Key.parse(key) is key
        assert Key.parse(key.name) is key
        assert Key.parse(key.value) is key
        assert Key.parse(f"<{key.name.lower()}>") is key


def test_parse_suggests() -> None:
    with pytest.raises(UndefinedKey) as info:
        Key.parse("PF33")
    assert info.value.message == "Tecla 'PF33' desconhecida. Você quis dizer PF3?"
    assert info.value.delivery == "none" and isinstance(info.value, ValueError)
    with pytest.raises(UndefinedKey) as info:
        Key.parse("ENTR")
    assert "Você quis dizer ENTER?" in info.value.message
    with pytest.raises(UndefinedKey) as info:
        Key.parse("xyzzy")
    assert info.value.message == "Tecla 'xyzzy' desconhecida. Use nomes como ENTER, PF3, TAB ou PAGE_DOWN."


@pytest.mark.parametrize("bad", ["", "  ", "<>", "@", "@W", "@@", "@E@E", "PF0", "PF25", "F 3 x", "<PF3"])
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(UndefinedKey):
        Key.parse(bad)


def test_parse_long_names_are_cut_in_the_message() -> None:
    with pytest.raises(UndefinedKey) as info:
        Key.parse("x" * 50)
    assert "x" * 21 not in info.value.message


@pytest.mark.parametrize("bad", [3, None, b"ENTER", 1.0, ["ENTER"]])
def test_parse_rejects_non_str(bad: Any) -> None:
    with pytest.raises(ParameterError):
        Key.parse(bad)


def test_pf() -> None:
    assert [Key.pf(n) for n in range(1, 25)] == [Key[f"PF{n}"] for n in range(1, 25)]
    assert Key.pf(3) is Key.PF3 and Key.pf(24).value == "@o"
    for bad in (0, 25, -1, True, 3.0, "3", None):
        with pytest.raises(ParameterError):
            Key.pf(bad)  # type: ignore[arg-type]


# ------------------------------------------------------------------ tokenize_extra

@pytest.mark.parametrize("text, expected", [
    ("abc<Tab>1<Enter>", ["abc", Key.TAB, "1", Key.ENTER]),
    ("", []),
    ("abc", ["abc"]),
    ("<<", ["<"]),
    ("a<<b", ["a<b"]),
    ("<<Enter>", ["<Enter>"]),                  # "<<" é literal: o resto é texto
    ("a>b", ["a>b"]),
    ("x@y@E", ["x@y@E"]),                       # "@" é texto comum na sintaxe extra
    ("<Pf3>", [Key.PF3]),
    ("<F3><PAGE_DOWN><page up>", [Key.PF3, Key.PAGE_DOWN, Key.PAGE_UP]),
    ("A000000<Tab>senha<Enter>", ["A000000", Key.TAB, "senha", Key.ENTER]),
    ("<@E>", [Key.ENTER]),
    ("<erase eof>fim", [Key.ERASE_EOF, "fim"]),
    ("çã€<Tab>", ["çã€", Key.TAB]),
])
def test_tokenize_extra(text: str, expected: list) -> None:
    assert tokenize_extra(text) == expected


def test_tokenize_extra_suggests() -> None:
    with pytest.raises(UndefinedKey) as info:
        tokenize_extra("abc<Pf33>")
    assert info.value.message == "Tecla '<Pf33>' desconhecida. Você quis dizer <PF3>? Para digitar '<' use '<<'."


@pytest.mark.parametrize("text", ["a<b", "<", "x<Tab", "a<>b", "<foo>"])
def test_tokenize_extra_rejects(text: str) -> None:
    with pytest.raises(UndefinedKey) as info:
        tokenize_extra(text)
    assert "'<<'" in info.value.message


def test_tokenize_extra_does_not_quote_data() -> None:
    """Um ``<`` perdido no meio de dados não faz a mensagem citar os dados (T-SEC-1)."""
    for text in ("senha<SegredoMuitoComprido123>", "a<b c=1; senha!>", "x<segredo"):
        with pytest.raises(UndefinedKey) as info:
            tokenize_extra(text)
        assert "egredo" not in str(info.value) and "senha" not in str(info.value)


def test_tokenize_extra_rejects_non_str() -> None:
    with pytest.raises(ParameterError):
        tokenize_extra(Key.ENTER)  # type: ignore[arg-type]


# ------------------------------------------------------------------ split_at_aids

def test_split_at_aids() -> None:
    tokens = tokenize_extra("abc<Tab>1<Enter>x<PAGE_DOWN><PF3>tail<Tab>")
    assert split_at_aids(tokens) == [
        Segment(("abc", Key.TAB, "1"), Key.ENTER),
        Segment(("x",), Key.PAGE_DOWN),
        Segment((), Key.PF3),
        Segment(("tail", Key.TAB), None),
    ]


def test_split_at_aids_page_down_cuts() -> None:
    segments = split_at_aids(["linha", Key.PAGE_DOWN, "mais"])
    assert segments == [Segment(("linha",), Key.PAGE_DOWN), Segment(("mais",), None)]
    assert all(Key.PAGE_DOWN not in s.body for s in segments)


def test_split_at_aids_edges() -> None:
    assert split_at_aids([]) == []
    assert split_at_aids(["", Key.ENTER, ""]) == [Segment((), Key.ENTER)]
    assert split_at_aids((Key.TAB,)) == [Segment((Key.TAB,), None)]
    for aid in AIDS:
        assert split_at_aids(["a", aid]) == [Segment(("a",), aid)]
    for bad in ("abc", Key.ENTER, None, ["a", 3], [b"a"]):
        with pytest.raises(ParameterError):
            split_at_aids(bad)  # type: ignore[arg-type]


# ------------------------------------------------------------------ encode_body

def _bytes(chunks: list[str]) -> list[bytes]:
    return [c.encode("cp1252") for c in chunks]


def _decoded(chunks: list[str]) -> list:
    """Chunks -> tokens, juntando textos vizinhos (um texto longo atravessa chunks)."""
    out: list = []
    for token in (t for chunk in chunks for t in tokenize_mnemonic(chunk)):
        if isinstance(token, str) and out and isinstance(out[-1], str):
            out[-1] += token
        else:
            out.append(token)
    return out


def test_encode_body_300_chars_gives_3_chunks() -> None:
    chunks = encode_body(["x" * 300], host="cics")
    assert len(chunks) == 3
    assert [len(b) for b in _bytes(chunks)] == [128, 128, 44]
    assert "".join(chunks) == "x" * 300
    assert CHUNK_MAX_BYTES == _protocol.CHUNK_MAX_BYTES == 128


def test_encode_body_never_splits_units() -> None:
    for prefix in range(0, 6):
        body = ["a" * prefix + "@" * 200, Key.ERASE_INPUT, "b" * 127, Key.DUP, "@"]
        chunks = encode_body(body, host="cics")
        assert all(1 <= len(b) <= 128 for b in _bytes(chunks))
        for chunk in chunks:
            tokenize_mnemonic(chunk)                   # cada chunk sozinho é válido: nada partido no meio
        assert _decoded(chunks) == \
            ["a" * prefix + "@" * 200, Key.ERASE_INPUT, "b" * 127, Key.DUP, "@"]


def test_encode_body_escapes_and_keys() -> None:
    assert encode_body(["a@b"], host="ims") == ["a@@b"]
    assert encode_body(["A000000", Key.TAB, "x"], host="cics") == ["A000000@Tx"]
    assert encode_body([Key.ERASE_EOF, Key.RESET, Key.HELP], host="cics") == ["@F@R@H"]
    assert encode_body([], host="cics") == []
    assert encode_body([""], host="cics") == []
    assert encode_body("abc", host="cics") == ["abc"]      # str sozinha = um texto
    assert encode_body(["çã€"], host="cics") == ["çã€"]
    assert encode_body(["café"], host="ims") == ["café"]


def test_encode_body_has_no_aid() -> None:
    for aid in AIDS:
        with pytest.raises(ParameterError):
            encode_body(["a", aid], host="cics")
    chunks = encode_body(["x" * 500, Key.TAB, "@" * 70], host="cics")
    for chunk in chunks:
        assert not any(isinstance(t, Key) and t in AIDS for t in tokenize_mnemonic(chunk))


def test_encode_body_chunks_pass_the_worker_validation() -> None:
    chunks = encode_body(["x@" * 150, Key.TAB, "é" * 10], host="cics")
    _protocol.validate_args("send", {"chunks": chunks, "secret": False})
    _protocol.validate_args("send", {"chunks": chunks + [Key.ENTER.value], "secret": True})


def test_encode_body_encoding_rules() -> None:
    with pytest.raises(EncodingError) as info:
        encode_body(["€"], host="ims")
    assert "U+20AC" in info.value.message
    with pytest.raises(EncodingError):
        encode_body(["a\tb"], host="cics")
    assert encode_body(["“x” – …"], host="cics", errors="replace") == ['"x" - ...']
    assert encode_body(["é"], host="cics") == ["é"]                  # NFC


def test_encode_body_error_position_counts_from_body_start() -> None:
    with pytest.raises(EncodingError) as info:
        encode_body(["abc", Key.TAB, "d☃"], host="cics")
    assert info.value.message == "O caractere '☃' (U+2603) na posição 4 não existe no terminal."
    assert info.value.details and info.value.details["index"] == 4


def test_encode_body_error_position_counts_nfc_of_previous_tokens() -> None:
    """A posição conta o texto já em NFC (F.4), igual a um único token com o mesmo conteúdo."""
    nfd = "e\u0301e\u0301"  # "éé" decomposto: 4 code points, 2 em NFC
    with pytest.raises(EncodingError) as split:
        encode_body([nfd, "x\u00a4"], host="cics")
    with pytest.raises(EncodingError) as single:
        encode_body([nfd + "x\u00a4"], host="cics")
    assert split.value.details and single.value.details
    assert split.value.details["index"] == single.value.details["index"] == 3
    assert split.value.message == single.value.message


def test_tokenize_extra_secret_never_quotes_the_value() -> None:
    """``send_keys(..., secret=True)``: um trecho ``<...>`` curto da senha não aparece na ``UndefinedKey`` (T-SEC-1)."""
    with pytest.raises(UndefinedKey) as info:
        keys._tokenize_extra("<Tab>ab<cd>ef<Enter>", secret=True)
    assert "cd" not in str(info.value) and "ab" not in str(info.value)
    with pytest.raises(UndefinedKey):
        keys._tokenize_extra("x<segredo", secret=True)
    assert keys._tokenize_extra("abc<Tab>1<Enter>", secret=True) == tokenize_extra("abc<Tab>1<Enter>")
    with pytest.raises(UndefinedKey) as public:
        tokenize_extra("<Tab>ab<cd>ef<Enter>")
    assert "<cd>" not in public.value.message and "'<...>'" in public.value.message   # não parece tecla: não cita
    with pytest.raises(UndefinedKey) as typo:
        tokenize_extra("ab<Entr>")
    assert typo.value.message.startswith("Tecla '<Entr>' desconhecida. Você quis dizer <ENTER>?")


@pytest.mark.parametrize("text", ["<P F 1>", "<E-N-T-E-R>", "<pa 1>", "<PF_3>", "<page__down>"])
def test_separators_never_compact_into_a_key(text: str) -> None:
    # só a forma SEM separadores é compactada (PAGEDOWN); um texto incomum entre <> nunca vira AID (B.6)
    with pytest.raises(UndefinedKey):
        tokenize_extra("x" + text)
    with pytest.raises(UndefinedKey):
        Key.parse(text)


def test_invalid_prefixed_mnemonic_quotes_at_most_the_code() -> None:
    with pytest.raises(UndefinedKey) as info:
        tokenize_mnemonic("@Axyz")
    assert info.value.message.startswith("Tecla '@A' desconhecida.")
    with pytest.raises(UndefinedKey) as info:
        tokenize_mnemonic("@A@Z")
    assert info.value.message.startswith("Tecla '@A@Z' desconhecida.")


def test_encode_body_secret_never_shows_the_value() -> None:
    with pytest.raises(EncodingError) as info:
        encode_body(["ok", Key.TAB, "s3nh☃"], host="cics", secret=True)
    assert info.value.message == "O valor secreto contém um caractere não suportado."
    assert "☃" not in str(info.value) and "s3nh" not in str(info.value)


@pytest.mark.parametrize("kwargs", [{"host": "tso"}, {"host": "cics", "errors": "ignore"}])
def test_encode_body_validates_options(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ParameterError):
        encode_body([Key.TAB], **kwargs)


@pytest.mark.parametrize("bad", [Key.TAB, None, 3, ["a", 1], [b"x"]])
def test_encode_body_rejects_bad_body(bad: Any) -> None:
    with pytest.raises(ParameterError):
        encode_body(bad, host="cics")
