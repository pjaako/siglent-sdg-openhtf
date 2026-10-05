"""Tests for the MDWV, SWWV and BTWV parts of scpi.py (PG02 §3.5, §3.6.1, §3.7)."""

from typing import Any

import pytest

from siglent_sdg_openhtf.scpi import (
    GROUPS,
    ProtocolError,
    Setup,
    build_command,
    build_mdwv,
    order_setup,
    parse_mod_reply,
    validate_setup,
)

CARRIER = "CARR,WVTP,SINE,FRQ,1000HZ,AMP,4V,AMPVRMS,1.414Vrms,OFST,0V,PHSE,0"


def _ok(setup: Setup) -> None:
    validate_setup(setup, None)


def _bad(setup: Setup, *fragments: str) -> None:
    with pytest.raises(ValueError) as info:
        validate_setup(setup, None)
    for fragment in fragments:
        assert fragment in str(info.value)


def _mdwv(**params: Any) -> Setup:
    return {"C1": {"MDWV": {"STATE": True, "TYPE": "AM", **params}}}


# -- build ----------------------------------------------------------------------------------------------


def test_groups() -> None:
    assert GROUPS == ("OUTP", "BSWV", "MDWV", "SWWV", "BTWV")


def test_build_mdwv_strings() -> None:
    assert build_command(1, "MDWV", "STATE", True) == "C1:MDWV STATE,ON"  # PG02 §3.5
    assert build_command(2, "MDWV", "STATE", "OFF") == "C2:MDWV STATE,OFF"  # PG02 §3.5
    assert build_command(1, "MDWV", "TYPE", "AM") == "C1:MDWV AM"  # PG02 §3.5
    assert build_mdwv(1, "AM", "DEPTH", 40) == "C1:MDWV AM,DEPTH,40"  # PG02 §3.5
    assert build_command(1, "MDWV", "FRQ", 250.0, mdwv_type="FM") == "C1:MDWV FM,FRQ,250"  # PG02 §3.5
    with pytest.raises(ValueError, match="type"):
        build_command(1, "MDWV", "FRQ", 250)


def test_build_sweep_and_burst_strings() -> None:
    assert build_command(1, "SWWV", "TIME", 2) == "C1:SWWV TIME,2"  # PG02 §3.6.1
    assert build_command(1, "SWWV", "STATE", True) == "C1:SWWV STATE,ON"  # PG02 §3.6.1
    assert build_command(1, "SWWV", "TRMD", False) == "C1:SWWV TRMD,OFF"  # PG02 §3.6.1
    assert build_command(1, "BTWV", "TIME", "INF") == "C1:BTWV TIME,INF"  # PG02 §3.7
    assert build_command(2, "BTWV", "GATE_NCYC", "GATE") == "C2:BTWV GATE_NCYC,GATE"  # PG02 §3.7
    assert build_command(1, "BTWV", "STATE", "ON") == "C1:BTWV STATE,ON"  # PG02 §3.7


# -- validate: pass ----------------------------------------------------------------------------------------


def test_valid_groups_pass() -> None:
    _ok(_mdwv(SRC="INT", MDSP="SINE", FRQ=250.0, DEPTH=40))
    _ok({"C1": {"MDWV": {"STATE": True, "TYPE": "FSK", "KFRQ": 50, "HFRQ": 2000.0, "SRC": "EXT"}}})
    _ok({"C1": {"MDWV": {"STATE": False}}})
    _ok({"C1": {"SWWV": {"STATE": "ON", "TIME": 2, "START": 200.0, "STOP": 3000.0, "SWMD": "LOG", "DIR": "DOWN"}}})
    _ok({"C1": {"SWWV": {"STATE": True, "CENTER": 2000, "SPAN": 500, "MARK_STATE": True, "STARTTIME": 1}}})
    _ok({"C1": {"BTWV": {"STATE": True, "GATE_NCYC": "NCYC", "TRSR": "INT", "TIME": 5, "STPS": 90, "PRD": 0.5}}})
    _ok({"C1": {"BTWV": {"STATE": True, "TIME": "INF", "TRMD": "RISE", "EDGE": "FALL", "COUNT": 3}}})
    _ok({"C1": {"MDWV": {"STATE": True, "TYPE": "PWM"}, "BSWV": {"WVTP": "PULSE"}}})
    _ok({"C1": {"BTWV": {"STATE": True, "STPS": 90}, "BSWV": {"WVTP": "SINE", "PHSE": 90}}})


# -- validate: value rules --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("MDWV", "STATE", "maybe"),
        ("MDWV", "TYPE", "DSBSC"),
        ("MDWV", "SRC", "BOGUS"),
        ("MDWV", "MDSP", "SAW"),
        ("MDWV", "PLRT", "NOR"),
        ("MDWV", "DEPTH", 121),
        ("MDWV", "DEPTH", -1),
        ("MDWV", "FRQ", 0),
        ("MDWV", "KFRQ", float("nan")),
        ("MDWV", "HFRQ", "fast"),
        ("SWWV", "SWMD", "SAW"),
        ("SWWV", "DIR", "SIDEWAYS"),
        ("SWWV", "TRSR", "BUS"),
        ("SWWV", "TRMD", "RISE"),
        ("SWWV", "EDGE", "BOTH"),
        ("SWWV", "MARK_STATE", 3),
        ("SWWV", "SYM", 101),
        ("SWWV", "STARTTIME", 301),
        ("SWWV", "ENDTIME", -1),
        ("SWWV", "BACKTIME", 301),
        ("SWWV", "TIME", 0),
        ("SWWV", "START", 0),
        ("SWWV", "STOP", -5),
        ("BTWV", "PRD", 0),
        ("BTWV", "STPS", 361),
        ("BTWV", "GATE_NCYC", "BOTH"),
        ("BTWV", "TRSR", "BUS"),
        ("BTWV", "PLRT", "NOR"),
        ("BTWV", "TRMD", "ON"),
        ("BTWV", "EDGE", "BOTH"),
        ("BTWV", "TIME", 0),
        ("BTWV", "TIME", 2.5),
        ("BTWV", "TIME", "inf"),
        ("BTWV", "TIME", True),
        ("BTWV", "DLAY", float("inf")),
    ],
)
def test_invalid_values(group: str, key: str, value: object) -> None:
    params: dict[str, object] = {"STATE": True}
    if group == "MDWV":
        params["TYPE"] = {"KFRQ": "ASK", "HFRQ": "FSK", "PLRT": "PSK"}.get(key, "AM")
    params[key] = value
    setup: Setup = {"C1": {group: params}}
    _bad(setup, "C1", group, key)


def test_pm_devi_range() -> None:
    _ok({"C1": {"MDWV": {"STATE": True, "TYPE": "PM", "DEVI": 360}}})
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "PM", "DEVI": 361}}}, "C1", "MDWV", "DEVI")
    _ok({"C1": {"MDWV": {"STATE": True, "TYPE": "FM", "DEVI": 5000}}})  # FM: no fixed range


def test_unknown_key_in_new_groups() -> None:
    _bad({"C1": {"MDWV": {"STATE": True, "CARR": 1}}}, "C1", "MDWV", "CARR")
    _bad({"C1": {"SWWV": {"STATE": True, "MTRIG": 1}}}, "C1", "SWWV", "MTRIG")
    _bad({"C1": {"BTWV": {"STATE": True, "MTRIG": 1}}}, "C1", "BTWV", "MTRIG")


# -- validate: combinations ----------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [False, "OFF", None])
@pytest.mark.parametrize("group", ["SWWV", "BTWV"])
def test_keys_beside_state_off_or_absent(group: str, state: object) -> None:
    params: dict[str, object] = {"TIME": 2}
    if state is not None:
        params["STATE"] = state
    _bad({"C1": {group: params}}, "C1", group, "TIME", "STATE")


def test_mdwv_keys_beside_state_off() -> None:
    _bad({"C1": {"MDWV": {"STATE": False, "TYPE": "AM"}}}, "C1", "MDWV", "TYPE")
    _bad({"C1": {"MDWV": {"TYPE": "AM"}}}, "C1", "MDWV", "TYPE")


def test_mdwv_on_needs_type() -> None:
    _bad({"C1": {"MDWV": {"STATE": True, "FRQ": 100}}}, "C1", "MDWV", "TYPE")


@pytest.mark.parametrize(
    ("mtype", "key", "value", "valid"),
    [
        ("AM", "DEPTH", 50, True),
        ("DSBAM", "DEPTH", 50, False),
        ("FM", "DEPTH", 50, False),
        ("AM", "DEVI", 50, False),
        ("PM", "DEVI", 50, True),
        ("PWM", "DEVI", 0.0001, True),
        ("ASK", "FRQ", 50, False),
        ("ASK", "MDSP", "SINE", False),
        ("FSK", "KFRQ", 50, True),
        ("PSK", "HFRQ", 50, False),
        ("FSK", "HFRQ", 50, True),
        ("PSK", "PLRT", "NEG", True),
        ("FSK", "PLRT", "NEG", False),
        ("ASK", "SRC", "EXT", True),
        ("DSBAM", "MDSP", "NOISE", True),
        ("DSBAM", "FRQ", 10, True),
    ],
)
def test_mdwv_key_fits_type(mtype: str, key: str, value: object, valid: bool) -> None:
    setup: Setup = {"C1": {"MDWV": {"STATE": True, "TYPE": mtype, key: value}}}
    if mtype == "PWM":
        setup = {"C1": {**setup["C1"], "BSWV": {"WVTP": "PULSE"}}}
    if valid:
        _ok(setup)
    else:
        _bad(setup, "C1", "MDWV", key, mtype)


def test_one_group_on_per_channel() -> None:
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}, "SWWV": {"STATE": True}}}, "C1", "MDWV", "SWWV")
    _bad({"C1": {"SWWV": {"STATE": True}, "BTWV": {"STATE": True}}}, "C1", "SWWV", "BTWV")
    _ok({"C1": {"SWWV": {"STATE": True}, "BTWV": {"STATE": False}}})
    _ok({"C1": {"SWWV": {"STATE": True}}, "C2": {"BTWV": {"STATE": True}}})  # another channel


def test_carrier_and_modulation() -> None:
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}, "BSWV": {"WVTP": "NOISE"}}}, "C1", "MDWV", "NOISE")
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}, "BSWV": {"WVTP": "DC"}}}, "C1", "MDWV", "DC")
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "PWM"}, "BSWV": {"WVTP": "SINE"}}}, "C1", "MDWV", "PWM", "PULSE")
    _bad({"C1": {"MDWV": {"STATE": True, "TYPE": "FM"}, "BSWV": {"WVTP": "PULSE"}}}, "C1", "MDWV", "PULSE", "PWM")
    _ok({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}, "BSWV": {"WVTP": "RAMP"}}})
    _ok({"C1": {"MDWV": {"STATE": False}, "BSWV": {"WVTP": "NOISE"}}})


@pytest.mark.parametrize("wvtp", ["PULSE", "NOISE", "DC"])
def test_sweep_carrier(wvtp: str) -> None:
    _bad({"C1": {"SWWV": {"STATE": True}, "BSWV": {"WVTP": wvtp}}}, "C1", "SWWV", wvtp)
    _ok({"C1": {"SWWV": {"STATE": True}, "BSWV": {"WVTP": "SQUARE"}}})


def test_sweep_owns_frequency() -> None:
    _bad({"C1": {"SWWV": {"STATE": True}, "BSWV": {"FRQ": 1000.0}}}, "C1", "BSWV", "FRQ")
    _bad({"C1": {"SWWV": {"STATE": True}, "BSWV": {"PERI": 0.001}}}, "C1", "BSWV", "PERI")
    _ok({"C1": {"SWWV": {"STATE": False}, "BSWV": {"FRQ": 1000.0}}})


def test_sweep_start_above_stop() -> None:
    _bad({"C1": {"SWWV": {"STATE": True, "START": 5000, "STOP": 3000}}}, "C1", "SWWV", "START")
    _ok({"C1": {"SWWV": {"STATE": True, "START": 3000, "STOP": 3000}}})


def test_burst_phase_is_carrier_phase() -> None:
    _bad({"C1": {"BTWV": {"STATE": True, "STPS": 90}, "BSWV": {"PHSE": 45}}}, "C1", "BTWV", "STPS", "PHSE")


# -- order_setup -------------------------------------------------------------------------------------------


def test_order_modulation_between_bswv_and_outp_on() -> None:
    setup: dict[str, dict[str, object]] = {
        "OUTP": {"STATE": True, "LOAD": 50},
        "MDWV": {"DEPTH": 40, "SRC": "INT", "TYPE": "AM", "STATE": True},
        "BSWV": {"WVTP": "SINE", "FRQ": 1000.0},
    }
    assert order_setup(setup) == [
        ("OUTP", "LOAD", 50),
        ("BSWV", "WVTP", "SINE"),
        ("BSWV", "FRQ", 1000.0),
        ("MDWV", "STATE", True),
        ("MDWV", "TYPE", "AM"),
        ("MDWV", "SRC", "INT"),
        ("MDWV", "DEPTH", 40),
        ("OUTP", "STATE", True),
    ]


def test_order_sweep_start_stop_start() -> None:
    both = {"SWWV": {"TIME": 2, "STOP": 3000, "START": 200, "STATE": True}}
    assert order_setup(both) == [
        ("SWWV", "STATE", True),
        ("SWWV", "START", 200),
        ("SWWV", "STOP", 3000),
        ("SWWV", "START", 200),
        ("SWWV", "TIME", 2),
    ]
    single = {"SWWV": {"STATE": True, "STOP": 3000, "TIME": 2}}
    assert order_setup(single) == [("SWWV", "STATE", True), ("SWWV", "STOP", 3000), ("SWWV", "TIME", 2)]


def test_order_burst_mode_and_trigger_first() -> None:
    setup = {"BTWV": {"TIME": 5, "TRSR": "INT", "STATE": True, "GATE_NCYC": "NCYC"}}
    assert order_setup(setup) == [
        ("BTWV", "STATE", True),
        ("BTWV", "GATE_NCYC", "NCYC"),
        ("BTWV", "TRSR", "INT"),
        ("BTWV", "TIME", 5),
    ]


@pytest.mark.parametrize("group", ["MDWV", "SWWV", "BTWV"])
def test_order_state_off_is_the_only_command(group: str) -> None:
    assert order_setup({group: {"STATE": "OFF"}}) == [(group, "STATE", "OFF")]


# -- parse_mod_reply ---------------------------------------------------------------------------------------


def test_parse_mod_reply_am() -> None:
    raw = f"C1:MDWV STATE,ON,AM,MDSP,SINE,SRC,INT,FRQ,100HZ,DEPTH,100,{CARRIER}"  # PG02 §3.5, recon transcript
    fields, carrier = parse_mod_reply(raw, "C1:MDWV")
    assert list(fields.items()) == [
        ("STATE", "ON"), ("TYPE", "AM"), ("MDSP", "SINE"), ("SRC", "INT"), ("FRQ", "100HZ"), ("DEPTH", "100"),
    ]  # fmt: skip
    assert list(carrier)[:3] == ["WVTP", "FRQ", "AMP"]
    assert carrier["PHSE"] == "0"


@pytest.mark.parametrize("cmd", ["MDWV", "SWWV", "BTWV"])
def test_parse_mod_reply_state_off(cmd: str) -> None:
    assert parse_mod_reply(f"C1:{cmd} STATE,OFF", f"C1:{cmd}") == ({"STATE": "OFF"}, {})


def test_parse_mod_reply_sweep_ext() -> None:
    raw = (
        "C1:SWWV STATE,ON,TIME,1S,STOP,1500HZ,START,500HZ,TRSR,EXT,SWMD,LINE,DIR,UP,EDGE,RISE,"
        + CARRIER
    )  # PG02 §3.6.1, recon transcript
    fields, carrier = parse_mod_reply(raw, "C1:SWWV")
    assert "TRMD" not in fields
    assert fields["TRSR"] == "EXT"
    assert fields["EDGE"] == "RISE"
    assert carrier["WVTP"] == "SINE"


def test_parse_mod_reply_burst_gate() -> None:
    raw = f"C1:BTWV STATE,ON,PRD,0.5S,STPS,0,TRSR,INT,GATE_NCYC,GATE,PLRT,NEG,{CARRIER}"  # PG02 §3.7, recon transcript
    fields, carrier = parse_mod_reply(raw, "C1:BTWV")
    assert list(fields) == ["STATE", "PRD", "STPS", "TRSR", "GATE_NCYC", "PLRT"]
    assert fields["GATE_NCYC"] == "GATE"
    assert "FRQ" in carrier


def test_parse_mod_reply_errors() -> None:
    with pytest.raises(ProtocolError, match="header"):
        parse_mod_reply("C2:MDWV STATE,OFF", "C1:MDWV")
    with pytest.raises(ProtocolError, match="odd"):
        parse_mod_reply(f"C1:SWWV STATE,ON,TIME,{CARRIER}", "C1:SWWV")
    with pytest.raises(ProtocolError, match="odd"):
        parse_mod_reply("C1:SWWV STATE,ON,TIME,1S,CARR,WVTP", "C1:SWWV")


def test_order_setup_writes_switched_off_groups_before_the_one_switched_on() -> None:
    # measured: STATE,OFF of any of the three commands switches the active mode off
    triples = order_setup({"MDWV": {"STATE": True, "TYPE": "AM"}, "SWWV": {"STATE": False}, "BTWV": {"STATE": False}})
    assert [(g, k) for g, k, _ in triples] == [("SWWV", "STATE"), ("BTWV", "STATE"), ("MDWV", "STATE"), ("MDWV", "TYPE")]
