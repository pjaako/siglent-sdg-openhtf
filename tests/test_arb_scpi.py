"""Tests for the ARWV and SRATE groups and the WVDT/STL helpers of scpi.py (PG02 §3.9.1, §3.31, §3.32, §3.40)."""

from pathlib import Path
from typing import Any

import pytest

from siglent_sdg_openhtf.scpi import (
    ARB_INDEX_MAX,
    BUILTIN_ARBS,
    GROUPS,
    ProtocolError,
    Setup,
    build_command,
    build_wvdt_header,
    encode_samples,
    order_setup,
    parse_reply,
    parse_stl,
    parse_stl_builtin,
    parse_wvdt_reply,
    strip_unit,
    typed_fields,
    validate_setup,
    values_match,
)

DATA = Path(__file__).parent / "data"


def _ok(setup: Setup) -> None:
    validate_setup(setup, None)


def _bad(setup: Setup, *fragments: str) -> None:
    with pytest.raises(ValueError) as info:
        validate_setup(setup, None)
    for fragment in fragments:
        assert fragment in str(info.value)


def _arb(**groups: Any) -> Setup:
    return {"C1": {"BSWV": {"WVTP": "ARB"}, **groups}}


# -- BUILTIN_ARBS --------------------------------------------------------------------------------------------


def test_builtin_arbs_table() -> None:
    assert len(BUILTIN_ARBS) == 197
    assert BUILTIN_ARBS[0] == "StairUp"
    assert BUILTIN_ARBS[18] == "Dlorentz"
    assert BUILTIN_ARBS[196] == "demo2_16kpts"
    assert BUILTIN_ARBS[17] == "Gussian"
    assert ARB_INDEX_MAX == 196


def test_builtin_arbs_equal_the_recorded_stl_reply() -> None:
    lines = (DATA / "hardware_arb_1.txt").read_text().splitlines()
    reply = next(line[3:] for line in lines if line.startswith("<- STL M0"))
    parsed = parse_stl_builtin(reply)
    assert tuple(parsed[i] for i in range(197)) == BUILTIN_ARBS


def test_groups_gain_arwv_and_srate() -> None:
    assert GROUPS[-2:] == ("ARWV", "SRATE")


# -- build_command -------------------------------------------------------------------------------------------


def test_build_arwv() -> None:
    assert build_command(1, "ARWV", "INDEX", 5) == "C1:ARWV INDEX,5"  # PG02 §3.9.1
    assert build_command(2, "ARWV", "NAME", "htf_test") == "C2:ARWV NAME,htf_test"  # PG02 §3.9.1
    with pytest.raises(ValueError):
        build_command(1, "ARWV", "PATH", "x")


def test_build_srate() -> None:
    assert build_command(1, "SRATE", "MODE", "TARB") == "C1:SRATE MODE,TARB"  # PG02 §3.40
    assert build_command(1, "SRATE", "MODE", "tarb") == "C1:SRATE MODE,TARB"  # PG02 §3.40
    assert build_command(1, "SRATE", "VALUE", 4096000) == "C1:SRATE VALUE,4096000"  # PG02 §3.40
    assert build_command(1, "SRATE", "VALUE", 4096000.0) == "C1:SRATE VALUE,4096000"  # PG02 §3.40
    with pytest.raises(ValueError):
        build_command(1, "SRATE", "INTER", "LINE")


# -- validation ----------------------------------------------------------------------------------------------


def test_valid_arb_setups() -> None:
    _ok(_arb(ARWV={"INDEX": 0}))
    _ok(_arb(ARWV={"INDEX": 196}))
    _ok(_arb(ARWV={"NAME": "htf_test-2"}))
    _ok(_arb(ARWV={"NAME": "a" * 40}))
    _ok(_arb(SRATE={"MODE": "DDS"}))
    _ok(_arb(SRATE={"MODE": "tarb", "VALUE": 4096000}))
    _ok(_arb(SRATE={"MODE": "TARB", "VALUE": 75e6}))
    _ok(_arb(ARWV={"NAME": "htf_test"}, SRATE={"MODE": "TARB"}))
    _ok({"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 1000.0, "AMP": 2.0}, "ARWV": {"INDEX": 3}, "SRATE": {"MODE": "TARB"}}})
    _ok({"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"INDEX": 3}, "MDWV": {"STATE": True, "TYPE": "AM"}}})


@pytest.mark.parametrize("group", ["ARWV", "SRATE"])
def test_arb_groups_need_wvtp_arb(group: str) -> None:
    table: dict[str, dict[str, object]] = {"ARWV": {"INDEX": 1}, "SRATE": {"MODE": "DDS"}}
    params = table[group]
    _bad({"C1": {group: params}}, "C1", group, "WVTP")
    _bad({"C1": {"BSWV": {"WVTP": "SINE"}, group: params}}, "C1", group, "ARB")
    _bad({"C2": {"BSWV": {"FRQ": 100}, group: params}}, "C2", group, "ARB")


def test_arwv_exactly_one_selector() -> None:
    _bad(_arb(ARWV={}), "C1 ARWV", "exactly one")
    _bad(_arb(ARWV={"INDEX": 1, "NAME": "x"}), "C1 ARWV", "exactly one")


@pytest.mark.parametrize("index", [-1, 197, 1.5, "2", True, None])
def test_arwv_index_rejected(index: object) -> None:
    _bad(_arb(ARWV={"INDEX": index}), "C1 ARWV INDEX")


@pytest.mark.parametrize("name", ["", "a b", "x.bin", "htf_test.bin", "a" * 41, "ä", 'a"b', "a,b", 5, None])
def test_arwv_name_rejected(name: object) -> None:
    _bad(_arb(ARWV={"NAME": name}), "C1 ARWV NAME")


def test_arwv_unknown_key() -> None:
    _bad(_arb(ARWV={"PATH": "x"}), "C1 ARWV PATH")


def test_srate_rules() -> None:
    _bad(_arb(SRATE={"VALUE": 1000}), "C1 SRATE VALUE", "MODE")
    _bad(_arb(SRATE={"MODE": "DDS", "VALUE": 1000}), "C1 SRATE VALUE", "TARB")
    _bad(_arb(SRATE={"MODE": "TRUE"}), "C1 SRATE MODE")
    _bad(_arb(SRATE={"MODE": 1}), "C1 SRATE MODE")
    _bad(_arb(SRATE={"INTER": "LINE"}), "C1 SRATE INTER")
    for value in (0, -1, 75e6 + 1, float("nan"), "1000", True):
        _bad(_arb(SRATE={"MODE": "TARB", "VALUE": value}), "C1 SRATE VALUE")


def test_srate_value_excludes_frq_and_peri() -> None:
    for key in ("FRQ", "PERI"):
        setup: Setup = {"C1": {"BSWV": {"WVTP": "ARB", key: 0.001}, "SRATE": {"MODE": "TARB", "VALUE": 1e6}}}
        _bad(setup, f"C1 BSWV {key}", "SRATE")
    # MODE TARB alone may be combined with FRQ: the frequency then sets the rate
    _ok({"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 2000}, "SRATE": {"MODE": "TARB"}}})
    _ok({"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 2000}, "SRATE": {"MODE": "DDS"}}})


def test_validation_names_the_channel() -> None:
    _bad({"C2": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"INDEX": 300}}}, "C2 ARWV INDEX")


def test_unknown_group_still_rejected() -> None:
    _bad({"C1": {"WVDT": {"NAME": "x"}}}, "WVDT")


# -- order_setup ---------------------------------------------------------------------------------------------


def test_order_arwv_srate_before_carrier_parameters() -> None:
    channel: dict[str, dict[str, object]] = {
        "OUTP": {"STATE": True, "LOAD": 50},
        "BSWV": {"AMP": 2.0, "WVTP": "ARB", "OFST": 0.1, "PHSE": 10, "FRQ": 1000.0},
        "SRATE": {"VALUE": 4096000, "MODE": "TARB"},
        "ARWV": {"NAME": "htf_test"},
    }
    assert order_setup(channel) == [
        ("OUTP", "LOAD", 50),
        ("BSWV", "WVTP", "ARB"),
        ("ARWV", "NAME", "htf_test"),
        ("SRATE", "MODE", "TARB"),
        ("SRATE", "VALUE", 4096000),
        ("BSWV", "FRQ", 1000.0),
        ("BSWV", "AMP", 2.0),
        ("BSWV", "OFST", 0.1),
        ("BSWV", "PHSE", 10),
        ("OUTP", "STATE", True),
    ]


def test_order_without_arb_groups_is_unchanged() -> None:
    channel: dict[str, dict[str, object]] = {"BSWV": {"FRQ": 1.0, "WVTP": "SINE"}}
    assert order_setup(channel) == [("BSWV", "WVTP", "SINE"), ("BSWV", "FRQ", 1.0)]


def test_order_arb_goes_before_modulation() -> None:
    channel: dict[str, dict[str, object]] = {
        "BSWV": {"WVTP": "ARB"},
        "MDWV": {"STATE": True, "TYPE": "AM"},
        "ARWV": {"INDEX": 4},
    }
    groups = [g for g, _, _ in order_setup(channel)]
    assert groups == ["BSWV", "ARWV", "MDWV", "MDWV"]


# -- read-back -----------------------------------------------------------------------------------------------


def test_parse_arwv_replies() -> None:
    builtin = parse_reply("C1:ARWV INDEX,2,NAME,StairUD", "C1:ARWV")
    assert builtin.fields == {"INDEX": "2", "NAME": "StairUD"}
    user = parse_reply("C1:ARWV NAME,htf_test.bin", "C1:ARWV")
    assert user.fields == {"NAME": "htf_test.bin"}
    assert typed_fields(builtin) == {"INDEX": 2.0, "NAME": "StairUD"}


def test_parse_srate_replies() -> None:
    assert parse_reply("C1:SRATE MODE,DDS", "C1:SRATE").fields == {"MODE": "DDS"}
    tarb = parse_reply("C1:SRATE MODE,TARB,VALUE,16384000Sa/s", "C1:SRATE")
    assert tarb.fields == {"MODE": "TARB", "VALUE": "16384000Sa/s"}
    assert typed_fields(tarb) == {"MODE": "TARB", "VALUE": 16384000.0}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("16384000Sa/s", 16384000.0), ("0.1Sa/s", 0.1), ("12345.678Sa/s", 12345.678), ("75000000Sa/s", 75e6)],
)
def test_strip_unit_sample_rate(text: str, expected: float) -> None:
    assert strip_unit(text) == expected


def test_values_match_name() -> None:
    assert values_match("NAME", "htf_test", "htf_test.bin")
    assert values_match("NAME", "htf_test", "htf_test")
    assert values_match("NAME", "StairUp", "StairUp")
    assert not values_match("NAME", "htf_test", "HTF_TEST.bin")  # case-sensitive (B3)
    assert not values_match("NAME", "htf_test", "htf_test2.bin")
    assert not values_match("NAME", "htf_test", 5.0)
    assert not values_match("NAME", 5, "5.bin")


def test_values_match_index_and_srate() -> None:
    assert values_match("INDEX", 2, 2.0)
    assert not values_match("INDEX", 2, 3.0)
    assert values_match("MODE", "tarb", "TARB")
    assert values_match("VALUE", 4096000, 4096000.0)
    assert not values_match("VALUE", 4096000, 75e6)


# -- encode_samples ------------------------------------------------------------------------------------------


def test_encode_samples_ints_little_endian() -> None:
    assert encode_samples([0, 1, -1, 32767]) == b"\x00\x00\x01\x00\xff\xff\xff\x7f"
    assert encode_samples([-32768, 256]) == b"\x00\x80\x00\x01"
    assert encode_samples(range(2, 4)) == b"\x02\x00\x03\x00"


def test_encode_samples_bytes_pass_through() -> None:
    data = b"\x0a\x0d\x0a\x0d"
    assert encode_samples(data) == data
    assert encode_samples(bytearray(data)) == data


@pytest.mark.parametrize(
    "samples",
    [b"\x00" * 5, [], [1], b"\x00\x00", [32768, 0], [0, -32769], [1.5, 2], [True, 1], b"\x00" * 16_000_002],
)
def test_encode_samples_rejected(samples: Any) -> None:
    with pytest.raises(ValueError):
        encode_samples(samples)


def test_encode_samples_limits() -> None:
    assert len(encode_samples(b"\x00" * 4)) == 4
    assert len(encode_samples(b"\x00" * 16_000_000)) == 16_000_000
    assert len(encode_samples([0] * 8_000_000)) == 16_000_000


# -- build_wvdt_header ---------------------------------------------------------------------------------------


def test_build_wvdt_header() -> None:
    assert build_wvdt_header(1, "htf_b1") == "C1:WVDT WVNM,htf_b1,WAVEDATA,"  # PG02 §3.32
    full = build_wvdt_header(2, "htf_test2", freq=2500, ampl=1.0, ofst=0.5, phase=90)
    assert full == "C2:WVDT WVNM,htf_test2,FREQ,2500,AMPL,1,OFST,0.5,PHASE,90,WAVEDATA,"  # PG02 §3.32
    assert build_wvdt_header(1, "x", freq=250) == "C1:WVDT WVNM,x,FREQ,250,WAVEDATA,"  # PG02 §3.32
    assert build_wvdt_header(1, "x", ofst=0.0) == "C1:WVDT WVNM,x,OFST,0,WAVEDATA,"  # PG02 §3.32


def test_build_wvdt_header_rejects() -> None:
    with pytest.raises(ValueError):
        build_wvdt_header(1, "bad name")
    with pytest.raises(ValueError):
        build_wvdt_header(1, "x.bin")
    with pytest.raises(ValueError):
        build_wvdt_header(3, "x")


# -- parse_wvdt_reply ----------------------------------------------------------------------------------------

USER_HEAD = b"WVDT POS, /Local, WVNM, htf_b1, LENGTH, 128B, TYPE, 6, WAVEDATA,"


def test_parse_wvdt_reply_user() -> None:
    data = bytes(range(128))
    fields, got = parse_wvdt_reply(USER_HEAD + data + b"\n")
    assert got == data
    assert fields["POS"] == "/Local"
    assert fields["WVNM"] == "htf_b1"
    assert fields["LENGTH"] == "128B"
    assert fields["TYPE"] == "6"
    assert fields["length"] == 128


def test_parse_wvdt_reply_builtin() -> None:
    head = b"WVDT POS, M2, WVNM, StairUD, LENGTH, 32768B, TYPE, 6, WAVEDATA,"
    fields, got = parse_wvdt_reply(head + b"\xff\x7f" * 16384 + b"\n")
    assert fields["POS"] == "M2"
    assert fields["length"] == 32768
    assert len(got) == 32768


def test_parse_wvdt_reply_data_with_newline_bytes() -> None:
    data = b"\x0a\x0d" * 64
    assert parse_wvdt_reply(USER_HEAD + data + b"\n")[1] == data
    data = bytes(127) + b"\x0a"  # a real 0x0A as the last data byte is kept, only the terminator goes
    assert parse_wvdt_reply(USER_HEAD + data + b"\n")[1] == data


def test_parse_wvdt_reply_without_terminator() -> None:
    data = bytes(128)
    assert parse_wvdt_reply(USER_HEAD + data)[1] == data


def test_parse_wvdt_reply_empty_is_no_such_wave() -> None:
    with pytest.raises(ProtocolError, match="no such wave"):
        parse_wvdt_reply(b"")


def test_parse_wvdt_reply_errors() -> None:
    with pytest.raises(ProtocolError, match="WAVEDATA"):
        parse_wvdt_reply(b"WVDT POS, /Local")
    with pytest.raises(ProtocolError, match=r"128.*127"):
        parse_wvdt_reply(USER_HEAD + bytes(127) + b"\n")
    with pytest.raises(ProtocolError, match=r"128.*130"):
        parse_wvdt_reply(USER_HEAD + bytes(130) + b"\n")
    with pytest.raises(ProtocolError, match="LENGTH"):
        parse_wvdt_reply(b"WVDT POS, /Local, WVNM, x, TYPE, 6, WAVEDATA," + bytes(4))


# -- parse_stl -----------------------------------------------------------------------------------------------


def test_parse_stl() -> None:
    assert parse_stl("STL WVNM") == []
    assert parse_stl("STL WVNM,htf_test2,htf_test") == ["htf_test2", "htf_test"]
    assert parse_stl("STL WVNM,\"htfb5-x\",a") == ['"htfb5-x"', "a"]
    with pytest.raises(ProtocolError):
        parse_stl("C1:STL WVNM")
    with pytest.raises(ProtocolError):
        parse_stl("STL M0, StairUp")


def test_parse_stl_builtin() -> None:
    assert parse_stl_builtin("STL M0, StairUp, M1, StairDn, M10, LogFall") == {0: "StairUp", 1: "StairDn", 10: "LogFall"}
    with pytest.raises(ProtocolError):
        parse_stl_builtin("STL M0, StairUp, M1")
    with pytest.raises(ProtocolError):
        parse_stl_builtin("STL X0, StairUp")
