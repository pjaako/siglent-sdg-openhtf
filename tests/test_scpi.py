"""Tests for scpi.py and models.py. Every SCPI literal names its PG02 section (docs/PG02-E05C.txt)."""

import copy
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from siglent_sdg_openhtf import models, scpi
from siglent_sdg_openhtf.scpi import (
    ABS_TOL,
    BSWV_KEY_WAVE_TYPES,
    BSWV_KEYS,
    CHANNELS,
    OUTP_KEYS,
    WAVE_TYPES,
    ProtocolError,
    Reply,
    Setup,
    SetupError,
    build_bswv,
    build_outp,
    channel_name,
    format_value,
    order_setup,
    parse_reply,
    strip_unit,
    typed_fields,
    validate_setup,
    values_match,
)

BSWV_REPLY = "C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0"  # PG02 §3.4
OUTP_REPLY = "C1:OUTP ON,LOAD,HZ,PLRT,NOR"  # PG02 §3.3

EXAMPLE_SETUP: Setup = {
    "C1": {
        "OUTP": {"LOAD": 50, "STATE": True},
        "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
    }
}


# --- tables ---------------------------------------------------------------------------------------------


def test_constants() -> None:
    assert CHANNELS == ("C1", "C2")  # PG02 §3.3/§3.4
    assert WAVE_TYPES == {"SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "ARB", "DC"}  # PG02 §3.4
    assert not {"PRBS", "IQ"} & WAVE_TYPES
    assert OUTP_KEYS == {"STATE", "LOAD", "PLRT"}  # PG02 §3.3
    assert not {"LENGTH", "LOGICLEVEL", "EDGE", "BITRATE", "DIFFSTATE"} & BSWV_KEYS  # PG02 §3.4 notes: "no"
    assert set(BSWV_KEY_WAVE_TYPES) == BSWV_KEYS


@pytest.mark.parametrize(
    ("key", "types"),
    [
        ("FRQ", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),  # PG02 §3.4: not NOISE, not DC
        ("PERI", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("AMP", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("AMPVRMS", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("AMPDBM", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("HLEV", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("LLEV", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB"}),
        ("OFST", {"SINE", "SQUARE", "RAMP", "PULSE", "ARB", "DC"}),  # not NOISE
        ("PHSE", {"SINE", "SQUARE", "RAMP", "ARB"}),  # not NOISE, PULSE, DC
        ("SYM", {"RAMP"}),
        ("DUTY", {"SQUARE", "PULSE"}),
        ("WIDTH", {"PULSE"}),
        ("RISE", {"PULSE"}),
        ("FALL", {"PULSE"}),
        ("STDEV", {"NOISE"}),
        ("MEAN", {"NOISE"}),
        ("BANDSTATE", {"NOISE"}),
        ("BANDWIDTH", {"NOISE"}),
        ("DLY", set(WAVE_TYPES)),  # PG02 states no restriction
        ("WVTP", set(WAVE_TYPES)),
        ("MAX_OUTPUT_AMP", set(WAVE_TYPES)),  # PG02 §3.3
    ],
)
def test_bswv_key_wave_types(key: str, types: set[str]) -> None:
    assert BSWV_KEY_WAVE_TYPES[key] == types


# --- channel_name / format_value / build_* ----------------------------------------------------------------


@pytest.mark.parametrize(("channel", "name"), [(1, "C1"), (2, "C2")])
def test_channel_name(channel: int, name: str) -> None:
    assert channel_name(channel) == name


@pytest.mark.parametrize("channel", [0, 3, -1, True, 1.0])
def test_channel_name_rejects(channel: int) -> None:
    with pytest.raises(ValueError):
        channel_name(channel)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (True, "ON"),
        (False, "OFF"),
        (50, "50"),
        (-3, "-3"),
        (2.0, "2"),
        (1000.0, "1000"),
        (0.01, "0.01"),
        (1e-6, "1E-06"),  # exponent acceptance on write is a hardware item
        (100e6, "100000000"),
        (1234.5678901, "1234.56789"),  # .9G keeps 9 significant digits: a hypothesis, see STATUS open questions
        ("HZ", "HZ"),
        ("SINE", "SINE"),
    ],
)
def test_format_value(value: object, text: str) -> None:
    assert format_value(value) == text


@pytest.mark.parametrize("value", [None, [1], (1,), {"a": 1}, b"x", 1 + 2j])
def test_format_value_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        format_value(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_format_value_rejects_non_finite(value: float) -> None:
    with pytest.raises(ValueError):
        format_value(value)


@pytest.mark.parametrize(
    ("channel", "key", "value", "expected"),
    [
        (1, "WVTP", "RAMP", "C1:BSWV WVTP,RAMP"),  # PG02 §3.4 example
        (1, "FRQ", 2000, "C1:BSWV FRQ,2000"),  # PG02 §3.4 example
        (1, "AMP", 3, "C1:BSWV AMP,3"),  # PG02 §3.4 example
        (1, "AMP", 3.0, "C1:BSWV AMP,3"),
        (2, "OFST", -0.5, "C2:BSWV OFST,-0.5"),
        (1, "BANDWIDTH", 100000000, "C1:BSWV BANDWIDTH,100000000"),  # PG02 §3.4 example
        (1, "AMPDBM", 3, "C1:BSWV AMPDBM,3"),  # PG02 §3.4 example
        (1, "MAX_OUTPUT_AMP", 5, "C1:BSWV MAX_OUTPUT_AMP,5"),  # PG02 §3.3 example
        (2, "BANDSTATE", True, "C2:BSWV BANDSTATE,ON"),
    ],
)
def test_build_bswv(channel: int, key: str, value: object, expected: str) -> None:
    assert build_bswv(channel, key, value) == expected


@pytest.mark.parametrize(
    ("channel", "key", "value", "expected"),
    [
        (1, "STATE", True, "C1:OUTP ON"),  # PG02 §3.3 example
        (1, "STATE", False, "C1:OUTP OFF"),  # PG02 §3.3
        (2, "STATE", "OFF", "C2:OUTP OFF"),
        (2, "STATE", "on", "C2:OUTP ON"),
        (1, "LOAD", 50, "C1:OUTP LOAD,50"),  # PG02 §3.3 example
        (1, "LOAD", 50.0, "C1:OUTP LOAD,50"),
        (1, "LOAD", "HZ", "C1:OUTP LOAD,HZ"),  # PG02 §3.3 example
        (1, "LOAD", "hz", "C1:OUTP LOAD,HZ"),
        (1, "PLRT", "NOR", "C1:OUTP PLRT,NOR"),  # PG02 §3.3 example
        (2, "PLRT", "INVT", "C2:OUTP PLRT,INVT"),  # PG02 §3.3
    ],
)
def test_build_outp(channel: int, key: str, value: object, expected: str) -> None:
    assert build_outp(channel, key, value) == expected


def test_build_outp_unknown_key() -> None:
    with pytest.raises(ValueError):
        build_outp(1, "RATIO", 1)


# --- parse_reply -----------------------------------------------------------------------------------------


def test_parse_reply_bswv_example() -> None:
    reply = parse_reply(BSWV_REPLY, "C1:BSWV")  # PG02 §3.4
    assert isinstance(reply, Reply)
    assert reply.header == "C1:BSWV"
    assert reply.raw == BSWV_REPLY
    assert len(reply.fields) == 8
    assert list(reply.fields.items()) == [
        ("WVTP", "SINE"),
        ("FRQ", "100HZ"),
        ("PERI", "0.01S"),
        ("AMP", "2V"),
        ("OFST", "0V"),
        ("HLEV", "1V"),
        ("LLEV", "-1V"),
        ("PHSE", "0"),
    ]


def test_parse_reply_outp_leading_key() -> None:
    reply = parse_reply(OUTP_REPLY, "C1:OUTP", leading_key="STATE")  # PG02 §3.3
    assert list(reply.fields.items()) == [("STATE", "ON"), ("LOAD", "HZ"), ("PLRT", "NOR")]


def test_parse_reply_tolerates_surrounding_whitespace() -> None:
    reply = parse_reply("  " + OUTP_REPLY + "\r\n", "C1:OUTP", leading_key="STATE")
    assert reply.fields["PLRT"] == "NOR"
    assert reply.raw == OUTP_REPLY


def test_parse_reply_header_mismatch_carries_raw() -> None:
    raw = BSWV_REPLY.replace("C1:", "C2:")
    with pytest.raises(ProtocolError) as excinfo:
        parse_reply(raw, "C1:BSWV")
    assert raw in str(excinfo.value)


def test_parse_reply_wrong_command_header() -> None:
    with pytest.raises(ProtocolError):
        parse_reply(OUTP_REPLY, "C1:BSWV")


@pytest.mark.parametrize("raw", ["C1:BSWV WVTP,SINE,FRQ", "C1:BSWV WVTP,SINE,FRQ,100HZ,PERI"])
def test_parse_reply_odd_token_count(raw: str) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_reply(raw, "C1:BSWV")
    assert raw in str(excinfo.value)


def test_parse_reply_odd_with_leading_key() -> None:
    with pytest.raises(ProtocolError):
        parse_reply("C1:OUTP ON,LOAD,HZ,PLRT", "C1:OUTP", leading_key="STATE")


def test_parse_reply_missing_leading_value() -> None:
    with pytest.raises(ProtocolError):
        parse_reply("C1:OUTP", "C1:OUTP", leading_key="STATE")


def test_parse_reply_duplicate_key() -> None:
    with pytest.raises(ProtocolError):
        parse_reply("C1:BSWV FRQ,1,FRQ,2", "C1:BSWV")


def test_protocol_error_is_runtime_error() -> None:
    assert issubclass(ProtocolError, RuntimeError)


# --- strip_unit / typed_fields -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100HZ", 100.0),
        ("0.01S", 0.01),
        ("2V", 2.0),
        ("-1V", -1.0),
        ("0", 0.0),
        ("1.41421Vrms", 1.41421),
        ("3dBm", 3.0),
        ("50%", 50.0),
        ("100E6", 100e6),
        ("2.4e-07S", 2.4e-7),
        (".5V", 0.5),
        ("+3V", 3.0),
        ("5.", 5.0),
        ("1 kHz", 1.0),
    ],
)
def test_strip_unit_numbers(text: str, expected: float) -> None:
    result = strip_unit(text)
    assert isinstance(result, float)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize("text", ["HZ", "SINE", "ON", "NOR", "", "V2", "1,2", "--1V"])
def test_strip_unit_strings(text: str) -> None:
    assert strip_unit(text) == text


def test_typed_fields() -> None:
    typed = typed_fields(parse_reply(BSWV_REPLY, "C1:BSWV"))
    assert typed == {
        "WVTP": "SINE",
        "FRQ": 100.0,
        "PERI": 0.01,
        "AMP": 2.0,
        "OFST": 0.0,
        "HLEV": 1.0,
        "LLEV": -1.0,
        "PHSE": 0.0,
    }
    outp = typed_fields(parse_reply(OUTP_REPLY, "C1:OUTP", leading_key="STATE"))
    assert outp == {"STATE": "ON", "LOAD": "HZ", "PLRT": "NOR"}


# --- values_match ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "sent", "got", "expected"),
    [
        ("AMP", 2, 2.0005, True),  # hypothesis until hardware session 1 (ABS_TOL 1e-3)
        ("AMP", 2, 2.01, False),
        ("AMP", 2.0, 2.0, True),
        ("OFST", 0.0, 0.0005, True),
        ("OFST", 0.0, 0.01, False),
        ("PHSE", 12.345, 12.34, True),  # ABS_TOL 1e-2
        ("PHSE", 12.345, 12.5, False),
        ("DUTY", 50, 50.005, True),
        ("SYM", 50, 50.5, False),
        ("FRQ", 1000, 1000.0, True),
        ("FRQ", 1000.0, 1000.0, True),
        ("FRQ", 1000, 1001.0, False),
        ("FRQ", 1e6, 1e6 * (1 + 5e-7), True),  # REL_TOL 1e-6
        ("FRQ", 1e6, 1e6 * (1 + 5e-6), False),
        ("FRQ", 1000, 0.0, False),
        ("STATE", True, "ON", True),
        ("STATE", True, "on", True),
        ("STATE", True, "OFF", False),
        ("STATE", False, "OFF", True),
        ("STATE", False, "ON", False),
        ("BANDSTATE", True, "ON", True),
        ("STATE", True, 1.0, False),
        ("WVTP", "SINE", "SINE", True),
        ("WVTP", "SINE", "sine", True),
        ("WVTP", "SINE", "SQUARE", False),
        ("LOAD", "HZ", "HZ", True),
        ("LOAD", "hz", "HZ", True),
        ("LOAD", 50, 50.0, True),
        ("LOAD", 50, "HZ", False),  # number vs str
        ("LOAD", "HZ", 50.0, False),  # str vs number
        ("FRQ", "1000", 1000.0, False),
        ("FRQ", 1000, "HZ", False),
        ("PLRT", "NOR", "NOR", True),
        ("PLRT", "NOR", "INVT", False),
    ],
)
def test_values_match(key: str, sent: object, got: float | str, expected: bool) -> None:
    assert values_match(key, sent, got) is expected


def test_tolerance_tables() -> None:
    for key in ("AMP", "AMPVRMS", "OFST", "HLEV", "LLEV", "STDEV", "MEAN", "MAX_OUTPUT_AMP"):
        assert ABS_TOL[key] == 1e-3
    for key in ("PHSE", "DUTY", "SYM"):
        assert ABS_TOL[key] == 1e-2
    assert "FRQ" not in ABS_TOL


# --- order_setup -----------------------------------------------------------------------------------------


def _keys(triples: list[tuple[str, str, object]]) -> list[tuple[str, str]]:
    return [(g, k) for g, k, _ in triples]


def test_order_setup_load_before_amp_and_state_on_last() -> None:
    # caller's order is deliberately the worst one
    channel_setup: dict[str, dict[str, object]] = {
        "OUTP": {"STATE": True, "PLRT": "NOR", "LOAD": 50},
        "BSWV": {"OFST": 0.0, "AMP": 2.0, "FRQ": 1000.0, "WVTP": "SINE"},
    }
    assert order_setup(channel_setup) == [
        ("OUTP", "LOAD", 50),
        ("OUTP", "PLRT", "NOR"),
        ("BSWV", "WVTP", "SINE"),
        ("BSWV", "FRQ", 1000.0),
        ("BSWV", "AMP", 2.0),
        ("BSWV", "OFST", 0.0),
        ("OUTP", "STATE", True),
    ]


def test_order_setup_state_off_first() -> None:
    triples = order_setup({"OUTP": {"LOAD": "HZ", "STATE": False}, "BSWV": {"AMP": 1.0}})
    assert _keys(triples) == [("OUTP", "STATE"), ("OUTP", "LOAD"), ("BSWV", "AMP")]
    triples = order_setup({"OUTP": {"STATE": "OFF"}, "BSWV": {"AMP": 1.0}})
    assert _keys(triples)[0] == ("OUTP", "STATE")


def test_order_setup_state_string_on_last() -> None:
    triples = order_setup({"OUTP": {"STATE": "ON", "LOAD": 50}})
    assert _keys(triples) == [("OUTP", "LOAD"), ("OUTP", "STATE")]


def test_order_setup_wvtp_before_duty() -> None:
    triples = order_setup({"BSWV": {"DUTY": 30.0, "WVTP": "SQUARE", "FRQ": 10.0}})
    assert _keys(triples) == [("BSWV", "WVTP"), ("BSWV", "FRQ"), ("BSWV", "DUTY")]


def test_order_setup_peri_and_levels() -> None:
    triples = order_setup({"BSWV": {"LLEV": -1.0, "HLEV": 1.0, "PERI": 0.01, "WVTP": "SINE"}})
    assert _keys(triples) == [("BSWV", "WVTP"), ("BSWV", "PERI"), ("BSWV", "HLEV"), ("BSWV", "LLEV")]


@pytest.mark.parametrize("amp_key", ["AMP", "AMPVRMS", "AMPDBM"])
def test_order_setup_amp_variants_then_offset(amp_key: str) -> None:
    triples = order_setup({"BSWV": {"OFST": 0.1, amp_key: 1.0, "PHSE": 10.0}})
    assert _keys(triples) == [("BSWV", amp_key), ("BSWV", "OFST"), ("BSWV", "PHSE")]


def test_order_setup_other_keys_keep_caller_order() -> None:
    triples = order_setup({"BSWV": {"WVTP": "PULSE", "RISE": 1e-8, "DLY": 0.0, "WIDTH": 1e-6, "FALL": 1e-8}})
    assert _keys(triples) == [
        ("BSWV", "WVTP"),
        ("BSWV", "RISE"),
        ("BSWV", "DLY"),
        ("BSWV", "WIDTH"),
        ("BSWV", "FALL"),
    ]


def test_order_setup_empty_and_single_group() -> None:
    assert order_setup({}) == []
    assert order_setup({"OUTP": {"STATE": True}}) == [("OUTP", "STATE", True)]
    assert order_setup({"BSWV": {"FRQ": 1.0}}) == [("BSWV", "FRQ", 1.0)]


def test_order_setup_does_not_mutate() -> None:
    channel_setup: dict[str, dict[str, object]] = {
        "OUTP": {"STATE": True, "LOAD": 50},
        "BSWV": {"AMP": 2.0, "WVTP": "SINE"},
    }
    before = copy.deepcopy(channel_setup)
    order_setup(channel_setup)
    assert channel_setup == before


# --- validate_setup --------------------------------------------------------------------------------------


def test_validate_accepts_example_setup() -> None:
    validate_setup(EXAMPLE_SETUP, None)
    validate_setup(EXAMPLE_SETUP, models.limits_for("SDG2042X"))


def test_validate_accepts_both_channels_and_all_types() -> None:
    setup: Setup = {
        "C1": {"OUTP": {"STATE": "ON", "LOAD": "HZ", "PLRT": "INVT"}, "BSWV": {"WVTP": "RAMP", "SYM": 30.0}},
        "C2": {"OUTP": {"STATE": False, "LOAD": 75.5}, "BSWV": {"WVTP": "PULSE", "WIDTH": 1e-6, "DUTY": 10.0}},
    }
    validate_setup(setup, models.limits_for("SDG2042X"))
    validate_setup({"C1": {"BSWV": {"WVTP": "NOISE", "STDEV": 0.1, "MEAN": 0.0, "BANDSTATE": True}}}, None)
    validate_setup({"C1": {"BSWV": {"WVTP": "DC", "OFST": 1.5}}}, None)
    validate_setup({"C1": {"BSWV": {"FRQ": 100.0, "AMP": 1.0}}}, None)  # no WVTP: generic keys are fine
    validate_setup({"C1": {"BSWV": {"HLEV": 1.0, "LLEV": -1.0}}}, None)
    validate_setup({"C1": {"BSWV": {"PERI": 0.01, "AMPDBM": 3}}}, None)
    validate_setup({}, None)
    validate_setup({"C1": {}}, None)


@pytest.mark.parametrize(
    ("setup", "needle"),
    [
        ({"C3": {"BSWV": {"FRQ": 1.0}}}, "C3"),
        ({"c1": {"BSWV": {"FRQ": 1.0}}}, "c1"),
        ({"C1": {"MODU": {"STATE": True}}}, "MODU"),
        ({"C1": {"bswv": {"FRQ": 1.0}}}, "bswv"),
        ({"C1": {"BSWV": {"BOGUS": 1.0}}}, "BOGUS"),
        ({"C1": {"BSWV": {"LENGTH": 5, "WVTP": "SINE"}}}, "LENGTH"),  # PG02 §3.4: not on SDG2000X
        ({"C1": {"BSWV": {"LOGICLEVEL": "TTL_CMOS"}}}, "LOGICLEVEL"),  # PG02 §3.4: not on SDG2000X
        ({"C1": {"OUTP": {"FRQ": 1.0}}}, "FRQ"),
        ({"C1": {"BSWV": {"STATE": True}}}, "STATE"),
        ({"C1": {"BSWV": {"WVTP": "SINE", "DUTY": 50.0}}}, "DUTY"),
        ({"C1": {"BSWV": {"WVTP": "SINE", "SYM": 50.0}}}, "SYM"),
        ({"C1": {"BSWV": {"WVTP": "RAMP", "DUTY": 50.0}}}, "DUTY"),
        ({"C1": {"BSWV": {"WVTP": "NOISE", "FRQ": 1.0}}}, "FRQ"),
        ({"C1": {"BSWV": {"WVTP": "NOISE", "OFST": 1.0}}}, "OFST"),
        ({"C1": {"BSWV": {"WVTP": "DC", "AMP": 1.0}}}, "AMP"),
        ({"C1": {"BSWV": {"WVTP": "PULSE", "PHSE": 1.0}}}, "PHSE"),
        ({"C1": {"BSWV": {"WVTP": "SQUARE", "WIDTH": 1e-6}}}, "WIDTH"),
        ({"C1": {"BSWV": {"WVTP": "SINE", "STDEV": 0.1}}}, "STDEV"),
        ({"C1": {"BSWV": {"WVTP": "SINE", "BANDWIDTH": 1e6}}}, "BANDWIDTH"),
        ({"C1": {"BSWV": {"DUTY": 50.0}}}, "WVTP"),  # type-specific key without WVTP
        ({"C1": {"BSWV": {"STDEV": 0.1}}}, "WVTP"),
        ({"C1": {"BSWV": {"AMP": 1.0, "HLEV": 1.0}}}, "HLEV"),
        ({"C1": {"BSWV": {"AMP": 1.0, "LLEV": -1.0}}}, "LLEV"),
        ({"C1": {"BSWV": {"AMPVRMS": 1.0, "HLEV": 1.0}}}, "AMPVRMS"),
        ({"C1": {"BSWV": {"AMP": 1.0, "AMPVRMS": 1.0}}}, "AMPVRMS"),
        ({"C1": {"BSWV": {"AMPDBM": 1.0, "AMPVRMS": 1.0}}}, "AMPDBM"),
        ({"C1": {"BSWV": {"FRQ": 1.0, "PERI": 1.0}}}, "PERI"),
        ({"C1": {"OUTP": {"LOAD": 10}}}, "LOAD"),
        ({"C1": {"OUTP": {"LOAD": 100001}}}, "LOAD"),
        ({"C1": {"OUTP": {"LOAD": "INF"}}}, "LOAD"),
        ({"C1": {"OUTP": {"LOAD": True}}}, "LOAD"),
        ({"C1": {"OUTP": {"PLRT": "X"}}}, "PLRT"),
        ({"C1": {"OUTP": {"PLRT": "nor"}}}, "PLRT"),
        ({"C1": {"OUTP": {"STATE": 1}}}, "STATE"),
        ({"C1": {"OUTP": {"STATE": "MAYBE"}}}, "STATE"),
        ({"C1": {"BSWV": {"WVTP": "PRBS"}}}, "PRBS"),  # PG02 §3.4: not on SDG2000X
        ({"C1": {"BSWV": {"WVTP": "IQ"}}}, "IQ"),
        ({"C1": {"BSWV": {"WVTP": "sine"}}}, "sine"),
        ({"C1": {"BSWV": {"FRQ": "1000"}}}, "FRQ"),
        ({"C1": {"BSWV": {"FRQ": float("nan")}}}, "FRQ"),
        ({"C1": {"BSWV": {"FRQ": True}}}, "FRQ"),
        ({"C1": {"BSWV": {"WVTP": "SINE,FRQ,1"}}}, "WVTP"),  # no command injection through a value
        ({"C1": {"BSWV": {"WVTP": "SINE"}}, "C2": {"BSWV": {"WVTP": "SINE", "DUTY": 1.0}}}, "C2"),
    ],
)
def test_validate_rejects(setup: Setup, needle: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_setup(setup, None)
    assert needle in str(excinfo.value)


def test_validate_error_names_channel_group_key() -> None:
    with pytest.raises(ValueError, match=r"C2 BSWV DUTY"):
        validate_setup({"C2": {"BSWV": {"WVTP": "SINE", "DUTY": 50.0}}}, None)
    with pytest.raises(ValueError, match=r"C1 OUTP LOAD"):
        validate_setup({"C1": {"OUTP": {"LOAD": 10}}}, None)


def test_validate_frq_limit_sdg2042x() -> None:
    limits = models.limits_for("SDG2042X")
    setup: Setup = {"C1": {"BSWV": {"WVTP": "SINE", "FRQ": 50e6}}}
    with pytest.raises(ValueError, match="FRQ"):
        validate_setup(setup, limits)
    validate_setup(setup, None)  # limits check off
    validate_setup(setup, models.limits_for("SDG2082X"))  # fits a bigger model
    validate_setup({"C1": {"BSWV": {"WVTP": "SINE", "FRQ": 40e6}}}, limits)  # at the limit
    with pytest.raises(ValueError, match="FRQ"):
        validate_setup({"C1": {"BSWV": {"WVTP": "SQUARE", "FRQ": 30e6}}}, limits)
    validate_setup({"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 50e6}}}, limits)  # no table entry = no check
    validate_setup({"C1": {"BSWV": {"FRQ": 50e6}}}, limits)  # type unknown = no check


def test_validate_amp_limit_hiz_and_50() -> None:
    limits = models.limits_for("SDG2042X")
    with pytest.raises(ValueError, match="AMP"):
        validate_setup({"C1": {"BSWV": {"AMP": 20.5}}}, limits)
    validate_setup({"C1": {"BSWV": {"AMP": 20.0}}}, limits)
    validate_setup({"C1": {"OUTP": {"LOAD": "HZ"}, "BSWV": {"AMP": 15.0}}}, limits)
    with pytest.raises(ValueError, match="AMP"):
        validate_setup({"C1": {"OUTP": {"LOAD": 50}, "BSWV": {"AMP": 15.0}}}, limits)
    with pytest.raises(ValueError, match="AMP"):
        validate_setup({"C1": {"OUTP": {"LOAD": 50.0}, "BSWV": {"AMP": 10.5}}}, limits)
    validate_setup({"C1": {"OUTP": {"LOAD": 50}, "BSWV": {"AMP": 10.0}}}, limits)
    validate_setup({"C1": {"OUTP": {"LOAD": 100}, "BSWV": {"AMP": 15.0}}}, limits)  # only 50 ohm is limited
    # LOAD of another channel does not count
    validate_setup({"C1": {"OUTP": {"LOAD": 50}}, "C2": {"BSWV": {"AMP": 15.0}}}, limits)


def test_validate_setup_is_pure() -> None:
    setup: dict[str, dict[str, dict[str, object]]] = {
        "C1": {"OUTP": {"LOAD": 50, "STATE": True}, "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0}},
        "C2": {"BSWV": {"WVTP": "SQUARE", "DUTY": 30.0}},
    }
    before = copy.deepcopy(setup)
    limits = models.limits_for("SDG2042X")
    validate_setup(setup, limits)
    assert setup == before
    assert list(setup["C1"]["OUTP"]) == ["LOAD", "STATE"]  # key order untouched too
    bad: dict[str, dict[str, dict[str, object]]] = {"C1": {"BSWV": {"WVTP": "SINE", "DUTY": 30.0}}}
    bad_before = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        validate_setup(bad, limits)
    assert bad == bad_before
    # a read-only mapping works as input
    frozen: Mapping[str, Mapping[str, Mapping[str, object]]] = MappingProxyType(
        {"C1": MappingProxyType({"BSWV": MappingProxyType({"FRQ": 1.0})})}
    )
    validate_setup(frozen, limits)


def test_scpi_module_is_pure() -> None:
    # no I/O stack imported by scpi.py (among project modules it may only use models.py)
    assert scpi.__file__ is not None
    source = Path(scpi.__file__).read_text(encoding="utf-8")
    for forbidden in ("import pyvisa", "import openhtf", "from pyvisa", "from openhtf", "import socket"):
        assert forbidden not in source
    assert "from .models import ModelLimits" in source
    assert source.count("from .") == 1


# --- SetupError ------------------------------------------------------------------------------------------


def test_setup_error_lists_all_failures() -> None:
    failures = ["C1:BSWV FRQ: sent 1000, read 100HZ", "C1:BSWV AMP: not echoed by the generator"]
    err = SetupError(failures)
    assert isinstance(err, RuntimeError)
    assert err.failures == failures
    assert str(err).splitlines() == failures


# --- models.limits_for -----------------------------------------------------------------------------------


def test_limits_for_known_models() -> None:
    lim = models.limits_for("SDG2042X")
    assert lim.model == "SDG2042X"
    assert lim.channels == 2
    assert lim.max_freq_hz == {"SINE": 40e6, "SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6}
    assert (lim.max_amp_vpp_hiz, lim.max_amp_vpp_50, lim.max_offset_v_hiz) == (20.0, 10.0, 10.0)
    assert models.limits_for("SDG2082X").max_freq_hz["SINE"] == 80e6
    assert models.limits_for("SDG2122X").max_freq_hz["SINE"] == 120e6
    assert models.limits_for("SDG2082X").max_freq_hz["SQUARE"] == 25e6
    assert set(models.MODELS) == {"SDG2042X", "SDG2082X", "SDG2122X"}
    assert models.MODELS["SDG2042X"] is lim


def test_limits_for_is_case_insensitive_and_stripped() -> None:
    assert models.limits_for("  sdg2082x ") is models.MODELS["SDG2082X"]


def test_limits_for_unknown_sdg2_gets_most_restrictive() -> None:
    assert models.limits_for("SDG2162X") is models.MODELS["SDG2042X"]
    assert models.limits_for("sdg2000x") is models.MODELS["SDG2042X"]


@pytest.mark.parametrize("model", ["SDG1032X", "DHO804", "", "SDG6022X", "XSDG2042X"])
def test_limits_for_other_raises(model: str) -> None:
    with pytest.raises(ValueError):
        models.limits_for(model)


def test_package_exports() -> None:
    import siglent_sdg_openhtf

    assert siglent_sdg_openhtf.SetupError is SetupError
    assert siglent_sdg_openhtf.ProtocolError is ProtocolError
    assert siglent_sdg_openhtf.__version__
