"""Plug tests for modulation, sweep and burst on the fake (PG02 §3.5, §3.6.1, §3.7). No hardware."""

from typing import Any

import pytest

from siglent_sdg_openhtf import SetupError, SiglentSdgPlug
from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.scpi import Setup


def _plug(**fake_kwargs: Any) -> tuple[SiglentSdgPlug, FakeSdgResource]:
    fake = FakeSdgResource(**fake_kwargs)
    plug = SiglentSdgPlug(resource=fake)
    fake.log.clear()
    return plug, fake


@pytest.mark.parametrize(
    "setup",
    [
        {"C1": {"MDWV": {"STATE": True, "TYPE": "AM", "FRQ": 250.0, "DEPTH": 40, "MDSP": "SQUARE"}}},
        {"C1": {"MDWV": {"STATE": True, "TYPE": "FM", "FRQ": 150.0, "DEVI": 200}}},
        {"C1": {"MDWV": {"STATE": True, "TYPE": "FSK", "KFRQ": 50, "HFRQ": 2000.0}}},
        {"C1": {"MDWV": {"STATE": True, "TYPE": "PWM", "DEVI": 0.0001}, "BSWV": {"WVTP": "PULSE"}}},
        {"C1": {"SWWV": {"STATE": True, "TIME": 2, "START": 200.0, "STOP": 3000.0, "SWMD": "LOG", "DIR": "DOWN"}}},
        {"C1": {"BTWV": {"STATE": True, "GATE_NCYC": "NCYC", "TRSR": "INT", "TIME": 5, "PRD": 0.5, "STPS": 90}}},
        {"C1": {"BTWV": {"STATE": True, "GATE_NCYC": "GATE", "PRD": 0.03, "PLRT": "POS"}}},
        {"C1": {"BTWV": {"STATE": True, "TIME": "INF", "DLAY": 0.001}}},
    ],
)
def test_apply_setup_passes(setup: Setup) -> None:
    plug, _ = _plug()
    plug.apply_setup(setup)


def test_apply_setup_command_order_and_queries() -> None:
    plug, fake = _plug()
    plug.apply_setup(
        {"C1": {"OUTP": {"STATE": True}, "MDWV": {"STATE": True, "TYPE": "AM", "DEPTH": 40}, "BSWV": {"FRQ": 1000.0}}}
    )
    assert fake.log == [
        "C1:BSWV FRQ,1000",  # PG02 §3.4
        "C1:MDWV STATE,ON",  # PG02 §3.5
        "C1:MDWV AM",  # PG02 §3.5
        "C1:MDWV AM,DEPTH,40",  # PG02 §3.5
        "C1:OUTP ON",  # PG02 §3.3
        "C1:BSWV?",  # PG02 §3.4
        "C1:OUTP?",  # PG02 §3.3
        "C1:MDWV?",  # PG02 §3.5
    ]


def test_modulation_off_compares_only_state() -> None:
    plug, _ = _plug()
    plug.apply_setup({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}}})
    plug.apply_setup({"C1": {"MDWV": {"STATE": False}}})
    assert plug.get_modulation(1) == {"STATE": "OFF"}


@pytest.mark.parametrize(
    ("setup", "key"),
    [
        ({"C1": {"SWWV": {"STATE": True, "SWMD": "STEP"}}}, "SWMD"),
        ({"C1": {"SWWV": {"STATE": True, "DIR": "UP_DOWN"}}}, "DIR"),
        ({"C1": {"MDWV": {"STATE": True, "TYPE": "AM", "SRC": "CH2"}}}, "SRC"),
    ],
)
def test_values_the_generator_ignores_fail_verification(setup: Setup, key: str) -> None:
    plug, _ = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup(setup)
    assert len(info.value.failures) == 1
    assert key in info.value.failures[0]


def test_depth_after_reject_is_reported() -> None:
    plug, _ = _plug(reject=["C1:MDWV AM,DEPTH"])
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"MDWV": {"STATE": True, "TYPE": "AM", "DEPTH": 40}}})
    assert info.value.failures == ["C1:MDWV DEPTH: sent 40, read 100"]


def test_center_is_not_echoed() -> None:
    plug, _ = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"SWWV": {"STATE": True, "CENTER": 2000}}})
    assert info.value.failures == ["C1:SWWV CENTER: not echoed by the generator"]


def test_later_group_switches_the_earlier_one_off() -> None:
    plug, _ = _plug()
    plug.apply_setup({"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}}})
    assert plug.get_modulation(1)["STATE"] == "ON"
    plug.apply_setup({"C1": {"SWWV": {"STATE": True}}})
    assert plug.get_modulation(1) == {"STATE": "OFF"}
    assert plug.get_sweep(1)["STATE"] == "ON"


def test_validation_failure_sends_nothing() -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError, match="SWWV"):
        plug.apply_setup({"C1": {"SWWV": {"TIME": 2}}})
    assert fake.log == []


def test_manual_trigger() -> None:
    plug, fake = _plug()
    plug.manual_trigger(1, "SWWV")
    plug.manual_trigger(2, "BTWV")
    assert fake.log == ["C1:SWWV MTRIG", "C2:BTWV MTRIG"]  # PG02 §3.6.1, §3.7
    with pytest.raises(ValueError, match="MDWV"):
        plug.manual_trigger(1, "MDWV")
    assert fake.log == ["C1:SWWV MTRIG", "C2:BTWV MTRIG"]


def test_get_functions_return_typed_own_fields() -> None:
    plug, _ = _plug()
    assert plug.get_modulation(1) == {"STATE": "OFF"}
    plug.set_modulation(1, {"STATE": True, "TYPE": "AM"})
    assert plug.get_modulation(1) == {
        "STATE": "ON", "TYPE": "AM", "MDSP": "SINE", "SRC": "INT", "FRQ": 100.0, "DEPTH": 100.0,
    }  # fmt: skip
    plug.set_sweep(1, {"STATE": True})
    assert plug.get_sweep(1) == {
        "STATE": "ON", "TIME": 1.0, "STOP": 1500.0, "START": 500.0, "TRSR": "INT", "TRMD": "OFF",
        "SWMD": "LINE", "DIR": "UP",
    }  # fmt: skip
    plug.set_burst(1, {"STATE": True, "TIME": "INF"})
    assert plug.get_burst(1) == {
        "STATE": "ON", "STPS": 0.0, "TRSR": "EXT", "TIME": "INF", "DLAY": 6.04035e-07, "GATE_NCYC": "NCYC",
    }  # fmt: skip


def test_sweep_ends_with_the_requested_values_despite_clamping() -> None:
    plug, _ = _plug()
    plug.apply_setup({"C1": {"SWWV": {"STATE": True, "START": 200.0, "STOP": 3000.0}}})
    # START above the old STOP would be clamped when written alone; the repeated START fixes it
    plug.apply_setup({"C1": {"SWWV": {"STATE": True, "START": 5000.0, "STOP": 8000.0}}})
    sweep = plug.get_sweep(1)
    assert (sweep["START"], sweep["STOP"]) == (5000.0, 8000.0)
    # and the other way round: STOP below the old START
    plug.apply_setup({"C1": {"SWWV": {"STATE": True, "START": 50.0, "STOP": 100.0}}})
    sweep = plug.get_sweep(1)
    assert (sweep["START"], sweep["STOP"]) == (50.0, 100.0)


def test_carrier_in_a_modulation_setup_is_the_bswv_group() -> None:
    plug, fake = _plug()
    plug.apply_setup(
        {"C1": {"MDWV": {"STATE": True, "TYPE": "AM"}, "BSWV": {"WVTP": "SQUARE", "FRQ": 5000.0, "AMP": 2.0}}}
    )
    assert not any("CARR" in message for message in fake.log)
    assert plug.get_basic_wave(1)["FRQ"] == 5000.0


def test_burst_trmd_rise_verifies_under_int() -> None:
    plug, _ = _plug()
    plug.apply_setup({"C1": {"BTWV": {"STATE": True, "TRSR": "INT", "TRMD": "RISE"}}})
    assert plug.get_burst(1)["TRMD"] == "RISE"
