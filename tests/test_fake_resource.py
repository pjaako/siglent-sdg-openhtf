"""Tests for the fake instrument. Pure Python: no hardware, no sleeps, no network.

The fake is the oracle for everything else, so its expected strings are written out here literally (from
PG02 section 3.1-3.4 samples where PG02 has one) and are parsed by a helper local to this file, never by
the package's own parser. Expected values are the ones measured in hardware session 1 (README,
``tests/data/hardware_session_1.txt``) unless a test says otherwise.
"""

import ast
import inspect
import re
import subprocess
import sys

import pytest

from siglent_sdg_openhtf import fake_resource
from siglent_sdg_openhtf.fake_resource import FakeSdgResource

# Default reply of C1:BSWV? after *RST on the SDG2042X (measured, hardware session 1; the shape follows PG02 3.4)
PG02_BSWV_DEFAULT = (
    "C1:BSWV WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,4V,AMPVRMS,1.414Vrms,OFST,0V,HLEV,2V,LLEV,-2V,PHSE,0"
)
# PG02 3.3 shape of the C1:OUTP? reply (measured: the generator powers up OFF)
OUTP_DEFAULT = "C1:OUTP OFF,LOAD,HZ,PLRT,NOR"


def bswv(fake: FakeSdgResource, channel: str = "C1") -> dict[str, str]:
    """Query ``<ch>:BSWV?`` and split it into an ordered dict of raw field strings."""
    reply = fake.query(f"{channel}:BSWV?")
    header, _, body = reply.partition(" ")
    assert header == f"{channel}:BSWV"
    tokens = body.split(",")
    assert len(tokens) % 2 == 0
    return dict(zip(tokens[::2], tokens[1::2], strict=True))


def test_idn() -> None:
    # PG02 3.1.1 Format 2
    assert FakeSdgResource().query("*IDN?") == "Siglent Technologies,SDG2042X,SDG2XFAKE000001,0.00.00.00"


def test_idn_constructor_arguments() -> None:
    fake = FakeSdgResource(model="SDG2082X", serial="X1", firmware="1.2.3")
    assert fake.query("*IDN?") == "Siglent Technologies,SDG2082X,X1,1.2.3"


def test_opc_query_is_one() -> None:
    # PG02 3.1.2 Format 2: the bare character 1
    assert FakeSdgResource().query("*OPC?") == "1"


def test_opc_write_is_noop() -> None:
    fake = FakeSdgResource()
    fake.write("*OPC")  # PG02 3.1.2
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_default_bswv_matches_pg02_example_exactly() -> None:
    assert FakeSdgResource().query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_default_c2_bswv_has_c2_header() -> None:
    assert FakeSdgResource().query("C2:BSWV?") == PG02_BSWV_DEFAULT.replace("C1:", "C2:")


def test_default_outp() -> None:
    fake = FakeSdgResource()
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT
    assert fake.query("C2:OUTP?") == OUTP_DEFAULT.replace("C1:", "C2:")


def test_outp_on_and_off() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP ON")  # PG02 3.3
    assert fake.query("C1:OUTP?") == "C1:OUTP ON,LOAD,HZ,PLRT,NOR"
    assert fake.query("C2:OUTP?") == "C2:OUTP OFF,LOAD,HZ,PLRT,NOR"
    fake.write("C1:OUTP OFF")
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_outp_load_50_and_back_to_hz() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")  # PG02 3.3
    assert fake.query("C1:OUTP?") == "C1:OUTP OFF,LOAD,50,PLRT,NOR"
    fake.write("C1:OUTP LOAD,HZ")  # PG02 3.3
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_outp_polarity() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP PLRT,INVT")  # PG02 3.3
    assert fake.query("C1:OUTP?") == "C1:OUTP OFF,LOAD,HZ,PLRT,INVT"
    fake.write("C1:OUTP PLRT,NOR")  # PG02 3.3
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_outp_combined_command() -> None:
    fake = FakeSdgResource()
    fake.write("C2:OUTP ON,LOAD,75,PLRT,INVT")  # PG02 3.3 command syntax
    assert fake.query("C2:OUTP?") == "C2:OUTP ON,LOAD,75,PLRT,INVT"


@pytest.mark.parametrize(
    ("load", "reads"),
    [("10", "50"), ("49.9", "50"), ("0", "50"), ("-50", "50"), ("100001", "100000"), ("200000", "100000")],
)
def test_outp_load_out_of_range_is_clamped(load: str, reads: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:OUTP LOAD,{load}")  # PG02 3.3: SDG2000X 50~100000, HiZ; the generator clamps
    assert fake.query("C1:OUTP?") == f"C1:OUTP OFF,LOAD,{reads},PLRT,NOR"


@pytest.mark.parametrize("load", ["abc", "50OHM", "nan", ""])
def test_outp_load_unparsable_ignored(load: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:OUTP LOAD,{load}")  # PG02 3.3
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_outp_load_fraction_is_cut_off() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,1234.5")  # PG02 3.3
    assert fake.query("C1:OUTP?") == "C1:OUTP OFF,LOAD,1234,PLRT,NOR"


@pytest.mark.parametrize("load", ["50", "100", "100000"])
def test_outp_load_in_range_accepted(load: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:OUTP LOAD,{load}")
    assert fake.query("C1:OUTP?") == f"C1:OUTP OFF,LOAD,{load},PLRT,NOR"


def test_outp_invalid_polarity_ignored() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP PLRT,SIDEWAYS")
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_load_hz_to_50_halves_amp_and_offset() -> None:
    # measured: HZ -> 50 multiplies every level by k_new/k_old = 0.5
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,1")
    fake.write("C1:OUTP LOAD,50")
    fields = bswv(fake)
    assert fields["AMP"] == "2V"
    assert fields["OFST"] == "0.5V"
    assert fields["HLEV"] == "1.5V"
    assert fields["LLEV"] == "-0.5V"


def test_load_50_to_hz_doubles_and_round_trips() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake)["AMP"] == "2V"
    fake.write("C1:OUTP LOAD,HZ")
    assert bswv(fake)["AMP"] == "4V"


def test_load_50_to_75_rescales_by_k_ratio() -> None:
    # k = LOAD/(LOAD+50): 0.5 at 50 ohm, 0.6 at 75 ohm, so the levels grow by 1.2
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:OUTP LOAD,75")  # PG02 3.3
    fields = bswv(fake)
    assert (fields["AMP"], fields["HLEV"], fields["LLEV"]) == ("2.4V", "1.2V", "-1.2V")


def test_load_same_value_does_not_rescale() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,HZ")
    assert bswv(fake)["AMP"] == "4V"
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake)["AMP"] == "2V"


def test_load_rescale_is_per_channel() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake, "C2")["AMP"] == "4V"


def test_wvtp_ramp_and_frq_amp_examples() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,RAMP")  # PG02 3.4
    fake.write("C1:BSWV FRQ,2000")  # PG02 3.4
    fake.write("C1:BSWV AMP,3")  # PG02 3.4
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,RAMP,FRQ,2000HZ,PERI,0.0005S,AMP,3V,AMPVRMS,0.866051Vrms,OFST,0V,HLEV,1.5V,LLEV,-1.5V,"
        "PHSE,0,SYM,50"
    )


def test_peri_sets_frq() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV PERI,0.001")
    fields = bswv(fake)
    assert fields["FRQ"] == "1000HZ"
    assert fields["PERI"] == "0.001S"


def test_peri_zero_or_negative_ignored() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV PERI,0")
    fake.write("C1:BSWV PERI,-1")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_frq_exponent_notation_accepted() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,1E3")  # PG02 3.4 shows exponent notation on write (BANDWIDTH,100E6)
    assert bswv(fake)["FRQ"] == "1000HZ"


def test_frq_echo_has_ten_significant_digits() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,1234.5678901")  # PG02 3.4
    assert bswv(fake)["FRQ"] == "1234.56789HZ"
    fake.write("C1:BSWV FRQ,12345678.123456")
    assert bswv(fake)["FRQ"] == "12345678.12HZ"


def test_hlev_llev_set_amp_and_offset() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV HLEV,3")
    fields = bswv(fake)
    # low level stays at -2 V: AMP = 3 - (-2) = 5, OFST = 0.5
    assert (fields["AMP"], fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("5V", "0.5V", "3V", "-2V")
    fake.write("C1:BSWV LLEV,1")
    fields = bswv(fake)
    assert (fields["AMP"], fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("2V", "2V", "3V", "1V")


def test_hlev_below_llev_is_clamped_to_llev_plus_2mv() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV HLEV,-5")  # measured: not rejected, it becomes LLEV + 0.002
    fields = bswv(fake)
    assert (fields["AMP"], fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("0.002V", "-1.999V", "-1.998V", "-2V")


def test_ofst_and_hlev_follow_each_other() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,0.5")
    fields = bswv(fake)
    assert (fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("0.5V", "2.5V", "-1.5V")


def test_derived_levels_have_no_float_noise() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,0.1")
    fake.write("C1:BSWV AMP,0.3")
    fields = bswv(fake)
    assert (fields["HLEV"], fields["LLEV"]) == ("0.25V", "-0.05V")


def test_ampvrms_sine() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMPVRMS,1")  # PG02 3.4; measured: AMP = Vrms / 0.3535, echoed as written
    fields = bswv(fake)
    assert (fields["AMP"], fields["AMPVRMS"]) == ("2.82885V", "1Vrms")


def test_ampdbm_sine_at_numeric_load() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")  # PG02 3.3
    fake.write("C1:BSWV AMPDBM,3")  # PG02 3.4 example value; echoed as written
    fields = bswv(fake)
    assert (fields["AMP"], fields["AMPVRMS"], fields["AMPDBM"]) == ("0.893502V", "0.315853Vrms", "3dBm")
    fake.write("C1:BSWV AMPDBM,0")  # 0 dBm = 1 mW into 50 ohm = 0.2236 Vrms
    assert (bswv(fake)["AMPVRMS"], bswv(fake)["AMPDBM"]) == ("0.223607Vrms", "0dBm")


@pytest.mark.parametrize(("wvtp", "amp"), [("SQUARE", "2V"), ("PULSE", "2V"), ("RAMP", "3.464V")])
def test_ampvrms_uses_the_factor_of_the_wave_type(wvtp: str, amp: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV WVTP,{wvtp}")
    fake.write("C1:BSWV AMPVRMS,1")  # factors 0.5, 0.5, 1/3.464 (measured)
    assert bswv(fake)["AMP"] == amp


def test_wvtp_square_adds_duty() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,SQUARE,FRQ,1000HZ,PERI,0.001S,AMP,4V,AMPVRMS,2Vrms,OFST,0V,HLEV,2V,LLEV,-2V,PHSE,0,DUTY,50"
    )
    fake.write("C1:BSWV DUTY,25")
    assert bswv(fake)["DUTY"] == "25"


def test_wvtp_pulse_drops_phse() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,PULSE,FRQ,1000HZ,PERI,0.001S,AMP,4V,AMPVRMS,2Vrms,OFST,0V,HLEV,2V,LLEV,-2V,"
        "DUTY,20,WIDTH,0.0002,RISE,8.4e-09S,FALL,8.4e-09S,DLY,0"
    )
    assert "PHSE" not in bswv(fake)


def test_pulse_keys_settable() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV WIDTH,0.0001")
    fake.write("C1:BSWV RISE,0.000002")
    fake.write("C1:BSWV FALL,0.000003")
    fake.write("C1:BSWV DLY,0.001")
    fields = bswv(fake)
    assert (fields["WIDTH"], fields["RISE"], fields["FALL"], fields["DLY"]) == (
        "0.0001",
        "2e-06S",
        "3e-06S",
        "0.001",
    )


def test_pulse_duty_settable() -> None:
    # PG02 3.4: DUTY is settable for SQUARE or PULSE; measured: it sits between LLEV and WIDTH
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV DUTY,30")
    fields = bswv(fake)
    assert (fields["DUTY"], fields["WIDTH"]) == ("30", "0.0003")
    keys = list(fields)
    assert keys[keys.index("LLEV") + 1 : keys.index("LLEV") + 3] == ["DUTY", "WIDTH"]


def test_pulse_phse_ignored() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    before = fake.query("C1:BSWV?")
    fake.write("C1:BSWV PHSE,90")
    assert fake.query("C1:BSWV?") == before
    fake.write("C1:BSWV WVTP,SINE")
    assert bswv(fake)["PHSE"] == "0"


def test_wvtp_ramp_adds_sym() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,RAMP")
    fake.write("C1:BSWV SYM,80")
    assert list(bswv(fake))[-2:] == ["PHSE", "SYM"]
    assert bswv(fake)["SYM"] == "80"


def test_wvtp_noise_has_only_its_keys() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,NOISE,STDEV,0.23V,MEAN,0V,BANDSTATE,OFF"
    fake.write("C1:BSWV BANDSTATE,ON")
    fake.write("C1:BSWV BANDWIDTH,100E6")  # PG02 3.4 example
    fake.write("C1:BSWV STDEV,0.5")
    fake.write("C1:BSWV MEAN,-0.25")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,NOISE,STDEV,0.5V,MEAN,-0.25V,BANDSTATE,ON,BANDWIDTH,100000000HZ"
    )


@pytest.mark.parametrize("key", ["FRQ", "PERI", "AMP", "AMPVRMS", "AMPDBM", "OFST", "PHSE", "HLEV", "LLEV"])
def test_noise_ignores_waveform_keys(key: str) -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE")
    before = fake.query("C1:BSWV?")
    fake.write(f"C1:BSWV {key},1")
    assert fake.query("C1:BSWV?") == before
    fake.write("C1:BSWV WVTP,SINE")
    assert bswv(fake)["FRQ"] == "1000HZ"
    assert bswv(fake)["AMP"] == "4V"


def test_wvtp_dc_has_only_ofst() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,DC")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,0V"
    fake.write("C1:BSWV OFST,1.5")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,1.5V"
    fake.write("C1:BSWV FRQ,500")  # not valid for DC
    fake.write("C1:BSWV AMP,5")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,1.5V"


def test_wvtp_arb_has_no_ampvrms_and_no_ampdbm() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,ARB")
    expected = "C1:BSWV WVTP,ARB,FRQ,1000HZ,PERI,0.001S,AMP,4V,OFST,0V,HLEV,2V,LLEV,-2V,PHSE,0"
    assert fake.query("C1:BSWV?") == expected
    fake.write("C1:OUTP LOAD,50")  # also at a numeric load
    fake.write("C1:BSWV AMPVRMS,1")  # ignored for ARB (guess)
    assert "AMPDBM" not in bswv(fake)
    assert bswv(fake)["AMP"] == "2V"


def test_wvtp_change_keeps_common_and_type_specific_values() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,1000")
    fake.write("C1:BSWV AMP,3")
    fake.write("C1:BSWV OFST,0.5")
    fake.write("C1:BSWV PHSE,90")
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV DUTY,10")
    fake.write("C1:BSWV WVTP,RAMP")
    fields = bswv(fake)
    assert (fields["FRQ"], fields["AMP"], fields["OFST"], fields["PHSE"]) == ("1000HZ", "3V", "0.5V", "90")
    assert fields["SYM"] == "50"
    fake.write("C1:BSWV WVTP,SQUARE")
    assert bswv(fake)["DUTY"] == "10"  # measured: type-specific values survive a WVTP change


def test_wvtp_same_type_is_noop() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV DUTY,10")
    fake.write("C1:BSWV WVTP,SQUARE")
    assert bswv(fake)["DUTY"] == "10"


@pytest.mark.parametrize("wvtp", ["PRBS", "IQ", "TRIANGLE", "sine2", ""])
def test_unsupported_wvtp_ignored(wvtp: str) -> None:
    # PG02 3.4 lists PRBS and IQ, but they are not available on SDG2000X
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV WVTP,{wvtp}")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_frq_50mhz_clamped_on_sdg2042x() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,50E6")
    assert bswv(fake)["FRQ"] == "40000000HZ"
    fake.write("C1:BSWV FRQ,1000")
    fake.write("C1:BSWV FRQ,40E6")
    assert bswv(fake)["FRQ"] == "40000000HZ"


def test_frq_limits_per_model() -> None:
    fake = FakeSdgResource(model="SDG2082X")
    fake.write("C1:BSWV FRQ,80E6")
    assert bswv(fake)["FRQ"] == "80000000HZ"
    fake.write("C1:BSWV FRQ,90E6")
    assert bswv(fake)["FRQ"] == "80000000HZ"
    fake = FakeSdgResource(model="SDG2122X")
    fake.write("C1:BSWV FRQ,120E6")
    assert bswv(fake)["FRQ"] == "120000000HZ"


def test_unknown_model_gets_most_restrictive_limits() -> None:
    fake = FakeSdgResource(model="SDG2999X")
    fake.write("C1:BSWV FRQ,50E6")
    assert bswv(fake)["FRQ"] == "40000000HZ"


@pytest.mark.parametrize(("wvtp", "limit"), [("SQUARE", 25e6), ("PULSE", 25e6), ("RAMP", 1e6), ("ARB", 20e6)])
def test_frq_limits_per_wave_type(wvtp: str, limit: float) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV WVTP,{wvtp}")
    fake.write(f"C1:BSWV FRQ,{limit * 1.001}")
    assert bswv(fake)["FRQ"] == f"{int(limit)}HZ"  # measured: clamped to the maximum, not ignored
    fake.write("C1:BSWV FRQ,1000")
    fake.write(f"C1:BSWV FRQ,{limit}")
    assert bswv(fake)["FRQ"] == f"{int(limit)}HZ"


def test_wvtp_change_clamps_frq_to_new_limit() -> None:
    # measured, hardware session 1: a WVTP change clamps the frequency the same way
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,30E6")
    fake.write("C1:BSWV WVTP,RAMP")
    assert bswv(fake)["FRQ"] == "1000000HZ"


@pytest.mark.parametrize("value", ["abc", "1k", "nan", "inf"])
def test_frq_invalid_values_ignored(value: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV FRQ,{value}")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


@pytest.mark.parametrize("value", ["0", "-5"])
def test_frq_zero_or_negative_reads_0hz_and_inf_period(value: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV FRQ,{value}")  # PG02 3.4; measured: clamped to 0 Hz
    fields = bswv(fake)
    assert (fields["FRQ"], fields["PERI"]) == ("0HZ", "infS")


def test_square_duty_is_clamped_again_on_a_frequency_change_and_stays() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE,FRQ,10E6,DUTY,95")  # PG02 3.4
    assert bswv(fake)["DUTY"] == "83.7"
    fake.write("C1:BSWV FRQ,24E6")  # PG02 3.4
    assert bswv(fake)["DUTY"] == "60.88"
    fake.write("C1:BSWV FRQ,5000")  # PG02 3.4
    assert bswv(fake)["DUTY"] == "60.88"


def test_leaving_pulse_turns_the_delay_into_the_phase() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV PHSE,90")  # PG02 3.4
    fake.write("C1:BSWV WVTP,PULSE,DLY,0.0001,FRQ,2000")  # PG02 3.4
    fake.write("C1:BSWV WVTP,SINE")  # PG02 3.4
    assert bswv(fake)["PHSE"] == "-72"
    fake.write("C1:BSWV PHSE,45,WVTP,PULSE")  # PG02 3.4: the pulse keeps its own delay
    assert bswv(fake)["DLY"] == "0.0001"


def test_dly_is_clamped_to_one_period_either_way() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE,FRQ,4000,DLY,1")  # PG02 3.4
    assert bswv(fake)["DLY"] == "0.00025"
    fake.write("C1:BSWV DLY,-1")  # PG02 3.4
    assert bswv(fake)["DLY"] == "-0.00025"


def test_noise_bandwidth_is_clamped_to_20_and_120_mhz() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE,BANDSTATE,ON,BANDWIDTH,500E6")  # PG02 3.4
    assert bswv(fake)["BANDWIDTH"] == "120000000HZ"
    fake.write("C1:BSWV BANDWIDTH,1000")  # PG02 3.4
    assert bswv(fake)["BANDWIDTH"] == "20000000HZ"


def test_phse_keeps_its_sign_when_it_wraps() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV PHSE,-400")  # PG02 3.4
    assert bswv(fake)["PHSE"] == "-40"
    fake.write("C1:BSWV PHSE,725")  # PG02 3.4
    assert bswv(fake)["PHSE"] == "5"


def test_amp_limits_hiz_and_50_ohm() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,20")
    assert bswv(fake)["AMP"] == "20V"
    fake.write("C1:BSWV AMP,20.5")
    assert bswv(fake)["AMP"] == "20V"
    fake.write("C1:OUTP LOAD,50")  # halves to 10
    assert bswv(fake)["AMP"] == "10V"
    fake.write("C1:BSWV AMP,11")
    assert bswv(fake)["AMP"] == "10V"
    fake.write("C1:BSWV AMP,10")
    assert bswv(fake)["AMP"] == "10V"


@pytest.mark.parametrize("value", ["0", "-1", "0.0005"])
def test_amp_below_minimum_is_clamped_to_2mv(value: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV AMP,{value}")
    assert bswv(fake)["AMP"] == "0.002V"
    fake.write("C1:OUTP LOAD,50")
    fake.write(f"C1:BSWV AMP,{value}")
    assert bswv(fake)["AMP"] == "0.002V"


def test_offset_limit_depends_on_amp() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,0.002")
    fake.write("C1:BSWV OFST,12")  # measured: clamped to 10 - AMP/2
    assert bswv(fake)["OFST"] == "9.999V"
    fake.write("C1:BSWV OFST,-12")
    assert bswv(fake)["OFST"] == "-9.999V"
    fake.write("C1:BSWV AMP,20")  # AMP max follows the offset: min(20, 2*(10 - 9.999))
    assert bswv(fake)["AMP"] == "0.002V"
    fake.write("C1:BSWV OFST,0")
    fake.write("C1:BSWV AMP,20")
    fake.write("C1:BSWV OFST,5")  # 10 - 20/2 = 0
    assert bswv(fake)["OFST"] == "0V"


def test_duty_ignored_while_sine_and_sym_ignored_while_square() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV DUTY,25")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV SYM,25")
    assert "SYM" not in bswv(fake)
    assert bswv(fake)["DUTY"] == "50"
    fake.write("C1:BSWV WVTP,SINE")
    fake.write("C1:BSWV WVTP,SQUARE")  # duty must not have been changed behind our back
    assert bswv(fake)["DUTY"] == "50"


def test_sym_out_of_range_is_clamped() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,RAMP")
    fake.write("C1:BSWV SYM,-1")  # PG02 3.4
    assert bswv(fake)["SYM"] == "0"
    fake.write("C1:BSWV SYM,150")  # PG02 3.4
    assert bswv(fake)["SYM"] == "100"


def test_phse_above_360_wraps_and_negative_is_kept() -> None:
    fake = FakeSdgResource()
    for sent, reads in (("400", "40"), ("361", "1"), ("-90", "-90"), ("360", "360"), ("359.99999", "360")):
        fake.write(f"C1:BSWV PHSE,{sent}")
        assert bswv(fake)["PHSE"] == reads, sent


def test_square_duty_is_clamped_both_sides() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV DUTY,0.0001")  # 100 * 16.3e-9 * FRQ at 1 kHz
    assert bswv(fake)["DUTY"] == "0.00163"
    fake.write("C1:BSWV DUTY,99.99999")
    assert bswv(fake)["DUTY"] == "99.9984"
    fake.write("C1:BSWV FRQ,25E6")
    fake.write("C1:BSWV DUTY,0")
    assert bswv(fake)["DUTY"] == "40.75"
    fake.write("C1:BSWV DUTY,100")
    assert bswv(fake)["DUTY"] == "59.25"


def test_phse_accepted() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV PHSE,12.345")
    assert bswv(fake)["PHSE"] == "12.345"
    fake.write("C1:BSWV PHSE,360")
    assert bswv(fake)["PHSE"] == "360"


@pytest.mark.parametrize("key", ["LENGTH", "LOGICLEVEL", "EDGE", "FORMAT", "DIFFSTATE", "BITRATE", "COM_OFST"])
def test_pg02_keys_not_on_sdg2000x_are_silently_ignored(key: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV {key},TTL_CMOS")  # PG02 3.4 example uses LOGICLEVEL,TTL_CMOS
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_max_output_amp_accepted_but_not_echoed() -> None:
    # PG02 3.3: C1:BSWV MAX_OUTPUT_AMP,5. Measured: not echoed anywhere.
    fake = FakeSdgResource()
    fake.write("C1:BSWV MAX_OUTPUT_AMP,5")
    fake.write("C1:BSWV MAX_OUTPUT_AMP,0.5")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    fake.write("C1:BSWV AMP,10")  # measured: no visible effect, AMP 10 is still accepted
    assert bswv(fake)["AMP"] == "10V"


def test_multi_pair_bswv_command_applied_in_order() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE,FRQ,1000,AMP,3,DUTY,20")  # measured: multi-pair accepted
    fields = bswv(fake)
    assert (fields["WVTP"], fields["FRQ"], fields["AMP"], fields["DUTY"]) == ("SQUARE", "1000HZ", "3V", "20")


def test_multi_pair_invalid_pair_does_not_block_others() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,abc,AMP,3")  # an unparsable FRQ is ignored (hypothesis), the next pair still applies
    fields = bswv(fake)
    assert (fields["FRQ"], fields["AMP"]) == ("1000HZ", "3V")


def test_bswv_odd_token_count_ignores_dangling_key() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,3,FRQ")
    fields = bswv(fake)
    assert (fields["AMP"], fields["FRQ"]) == ("3V", "1000HZ")


def test_headers_and_keys_are_case_insensitive_and_long_forms_work() -> None:
    fake = FakeSdgResource()
    fake.write("c1:bswv amp,3")
    fake.write("C1:BaSic_WaVe FRQ,2000")  # PG02 3.4 long form of the header
    fake.write("C1:OUTPut ON")  # PG02 3.3 long form of the header
    assert fake.query("C1:BaSic_WaVe?").startswith("C1:BSWV WVTP,SINE,FRQ,2000HZ,PERI,0.0005S,AMP,3V")
    assert fake.query("C1:OUTPut?") == "C1:OUTP ON,LOAD,HZ,PLRT,NOR"


def test_whitespace_around_tokens_and_terminator() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV  FRQ , 2000 \n")
    assert bswv(fake)["FRQ"] == "2000HZ"


def test_channels_are_independent() -> None:
    fake = FakeSdgResource()
    fake.write("C2:BSWV WVTP,SQUARE")
    fake.write("C2:BSWV FRQ,5000")
    fake.write("C2:OUTP ON")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT
    assert bswv(fake, "C2")["WVTP"] == "SQUARE"
    assert fake.query("C2:OUTP?").startswith("C2:OUTP ON")


def test_rst_restores_defaults() -> None:
    fake = FakeSdgResource()
    for cmd in ("C1:OUTP ON", "C1:OUTP LOAD,50", "C1:OUTP PLRT,INVT", "C1:BSWV WVTP,SQUARE", "C1:BSWV FRQ,5000",
                "C2:BSWV AMP,5", "C2:OUTP ON"):  # fmt: skip
        fake.write(cmd)
    fake.write("*RST")  # PG02 3.1.3
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    assert fake.query("C2:BSWV?") == PG02_BSWV_DEFAULT.replace("C1:", "C2:")
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT
    assert fake.query("C2:OUTP?") == OUTP_DEFAULT.replace("C1:", "C2:")


def test_rst_keeps_log_and_configuration() -> None:
    fake = FakeSdgResource(reject=["C1:OUTP ON"])
    fake.write("*RST")
    fake.write("C1:OUTP ON")
    assert fake.log == ["*RST", "C1:OUTP ON"]
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT  # reject still active after *RST


def test_reject_logs_but_does_not_apply() -> None:
    fake = FakeSdgResource(reject=["C1:BSWV FRQ", "C1:OUTP ON"])
    fake.write("C1:BSWV FRQ,2000")
    fake.write("C1:OUTP ON")
    assert fake.log == ["C1:BSWV FRQ,2000", "C1:OUTP ON"]
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT
    fake.write("C1:BSWV AMP,3")  # other commands still work
    assert bswv(fake)["AMP"] == "3V"


def test_reject_matches_prefix_only() -> None:
    fake = FakeSdgResource(reject=["C1:BSWV FRQ"])
    fake.write("C2:BSWV FRQ,2000")  # other channel
    assert bswv(fake, "C2")["FRQ"] == "2000HZ"


def test_reject_swallows_even_undefined_commands() -> None:
    fake = FakeSdgResource(reject=["BOGUS"])
    fake.write("BOGUS 1")  # dropped before parsing: no ValueError
    assert fake.log == ["BOGUS 1"]


def test_raise_on_write_raises_and_does_not_apply() -> None:
    fake = FakeSdgResource(raise_on=["C1:OUTP OFF"])
    fake.write("C1:OUTP ON")
    with pytest.raises(RuntimeError):
        fake.write("C1:OUTP OFF")
    assert fake.log == ["C1:OUTP ON", "C1:OUTP OFF"]  # logged first
    assert fake.query("C1:OUTP?") == "C1:OUTP ON,LOAD,HZ,PLRT,NOR"
    fake.write("C2:OUTP OFF")  # other commands unaffected


def test_raise_on_query_raises() -> None:
    fake = FakeSdgResource(raise_on=["C1:BSWV?"])
    with pytest.raises(RuntimeError):
        fake.query("C1:BSWV?")
    assert fake.query("C2:BSWV?").startswith("C2:BSWV")
    assert fake.log == ["C1:BSWV?", "C2:BSWV?"]


def test_raise_on_accepts_any_collection() -> None:
    fake = FakeSdgResource(raise_on=("*IDN",), reject={"*OPC"})
    with pytest.raises(RuntimeError):
        fake.query("*IDN?")


@pytest.mark.parametrize(
    "cmd",
    [
        "BOGUS",
        "*CLS",  # not in PG02
        "SYST:ERR?",  # not in PG02, and a query
        "*IDN?",  # a query sent with write
        "C1:FOO 1",
        "C1:OUTP",
        "C1:BSWV",
        "C1:OUTP MAYBE",
        "C1:OUTP FOO,1",
        "C1:BSWV FOO,1",
        "C1:NOISE_ADD STATE,ON,RATIO,120",  # PG02 3.3, but outside the emulated subset
        "C3:OUTP ON",
        "C0:BSWV FRQ,1",
        "OUT_BOTHCH ON",  # PG02 3.3, but outside the emulated subset
        "CHDR LONG",  # PG02 3.2: not available on SDG2000X
        "",
    ],
)
def test_undefined_write_raises_value_error(cmd: str) -> None:
    fake = FakeSdgResource()
    with pytest.raises(ValueError, match="undefined command"):
        fake.write(cmd)
    assert fake.log == [cmd]


@pytest.mark.parametrize(
    "cmd",
    ["BOGUS?", "SYST:ERR?", "*ESR?", "*OPC", "*IDN", "C1:FOO?", "C3:BSWV?", "C1:BSWV", "C1:BSWV? FRQ", "C1:NOISE_ADD?", "CHDR?", ""],
)
def test_undefined_query_raises_value_error(cmd: str) -> None:
    fake = FakeSdgResource()
    with pytest.raises(ValueError, match="undefined query"):
        fake.query(cmd)
    assert fake.log == [cmd]


def test_undefined_write_applies_nothing() -> None:
    fake = FakeSdgResource()
    with pytest.raises(ValueError):
        fake.write("C1:BSWV AMP,5,FOO,1")  # grammar is checked before anything is applied
    with pytest.raises(ValueError):
        fake.write("C1:OUTP ON,FOO,1")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


def test_log_records_writes_and_queries_in_order() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    fake.query("C1:OUTP?")
    fake.write("*RST")
    fake.query("*OPC?")
    with pytest.raises(ValueError):
        fake.write("BOGUS")
    assert fake.log == ["C1:OUTP LOAD,50", "C1:OUTP?", "*RST", "*OPC?", "BOGUS"]


def test_log_keeps_raw_strings() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP ON\n")
    assert fake.log == ["C1:OUTP ON\n"]


def test_close() -> None:
    fake = FakeSdgResource()
    assert fake.closed is False
    fake.close()
    assert fake.closed is True
    fake.close()  # idempotent
    assert fake.closed is True


def test_resource_attributes_can_be_set() -> None:
    fake = FakeSdgResource()
    fake.timeout = 5000
    fake.read_termination = "\n"
    fake.write_termination = "\n"
    fake.chunk_size = 1024
    assert (fake.timeout, fake.read_termination, fake.write_termination, fake.chunk_size) == (5000, "\n", "\n", 1024)


def test_replies_use_c_g_format_with_lower_case_exponent() -> None:
    # measured: %g, 6 significant digits (10 for FRQ), lower-case exponent
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV RISE,0.0000000123")
    fake.write("C1:BSWV FRQ,1E3")
    reply = fake.query("C1:BSWV?")
    assert "RISE,1.23e-08S" in reply
    assert re.search(r"\d[E]", reply) is None
    fake.write("C1:BSWV WVTP,SINE")
    fake.write("C1:BSWV FRQ,1E-06")
    assert "FRQ,1e-06HZ,PERI,1e+06S" in fake.query("C1:BSWV?")


def test_module_does_not_pull_in_pyvisa_or_openhtf() -> None:
    # The fake is an independent oracle: loading it must not import pyvisa or openhtf. It is loaded by file
    # path so that the package __init__ (which imports the plug, hence openhtf and pyvisa) is not executed.
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('fake_resource_isolated', {fake_resource.__file__!r})\n"
        "assert spec is not None and spec.loader is not None\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['fake_resource_isolated'] = module\n"
        "spec.loader.exec_module(module)\n"
        "assert hasattr(module, 'FakeSdgResource')\n"
        "assert 'pyvisa' not in sys.modules, 'pyvisa imported'\n"
        "assert 'openhtf' not in sys.modules, 'openhtf imported'\n"
        "assert 'siglent_sdg_openhtf' not in sys.modules, 'package imported'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_module_source_imports_nothing_from_this_package() -> None:
    # Stronger than sys.modules (the package __init__ may import scpi): the fake's own import statements.
    tree = ast.parse(inspect.getsource(fake_resource))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            imported.add(node.module or "")
    assert imported
    for name in imported:
        root = name.split(".")[0]
        assert root not in {"pyvisa", "openhtf", "siglent_sdg_openhtf"}, name


def test_default_write_termination_is_newline() -> None:
    assert FakeSdgResource().write_termination == "\n"  # PG02 §5.2.1


@pytest.mark.parametrize("wvtp", ["SINE", "SQUARE", "RAMP", "NOISE", "DC"])
def test_dly_is_ignored_unless_pulse(wvtp: str) -> None:
    # measured, hardware session 1: DLY is a PULSE parameter and ignored elsewhere
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV WVTP,{wvtp}")
    before = fake.query("C1:BSWV?")
    fake.write("C1:BSWV DLY,0.001")
    assert fake.query("C1:BSWV?") == before
    fake.write("C1:BSWV WVTP,PULSE")
    assert bswv(fake)["DLY"] == "0"


# --- measured rules, hardware session 1 (one behaviour per test) ------------------------------------------


@pytest.mark.parametrize(("load", "top"), [("50", "10V"), ("75", "12V"), ("100000", "19.99V")])
def test_amp_maximum_follows_the_load_factor(load: str, top: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:OUTP LOAD,{load}")  # PG02 3.3: k = LOAD/(LOAD+50)
    fake.write("C1:BSWV AMP,25")
    assert bswv(fake)["AMP"] == top


def test_amp_maximum_follows_the_offset() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,2")
    fake.write("C1:BSWV AMP,25")  # min(20, 2 * (10 - 2))
    assert bswv(fake)["AMP"] == "16V"


def test_offset_limit_at_50_ohm() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:BSWV AMP,0.002")
    fake.write("C1:BSWV OFST,6")  # 5 - 0.001
    assert bswv(fake)["OFST"] == "4.999V"


def test_hlev_is_clamped_to_the_load_limit_and_llev_to_its_floor() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV HLEV,50")  # 10*k, the low level -2 stays
    fields = bswv(fake)
    assert (fields["HLEV"], fields["LLEV"], fields["AMP"]) == ("10V", "-2V", "12V")
    fake.write("C1:BSWV LLEV,-50")
    fields = bswv(fake)
    assert (fields["HLEV"], fields["LLEV"], fields["AMP"]) == ("10V", "-10V", "20V")
    fake.write("C1:BSWV LLEV,50")  # HLEV - 0.002
    fields = bswv(fake)
    assert (fields["HLEV"], fields["LLEV"], fields["AMP"]) == ("10V", "9.998V", "0.002V")


def test_pulse_rise_and_fall_are_clamped_to_8_4_ns() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV RISE,1E-10")
    fake.write("C1:BSWV FALL,0")
    fields = bswv(fake)
    assert (fields["RISE"], fields["FALL"]) == ("8.4e-09S", "8.4e-09S")


@pytest.mark.parametrize(("frq", "reads"), [("1E-07", "1e-07HZ"), ("0.123456789", "0.123456789HZ")])
def test_frq_has_no_lower_limit(frq: str, reads: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV FRQ,{frq}")
    assert bswv(fake)["FRQ"] == reads


def test_ampdbm_is_ignored_at_hiz() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMPDBM,3")  # PG02 3.4: ignored while LOAD is HZ
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_ampdbm_is_in_the_reply_only_at_a_numeric_load() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,2")
    assert "AMPDBM" not in bswv(fake)
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake)["AMPDBM"] == "3.97809dBm"  # AMP 1 V at 50 ohm: 10*log10(0.3535**2 / 50 / 0.001)


def test_hiz_to_50_to_hiz_round_trip_has_no_drift() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,2.4")
    for load in ("75", "100000", "50", "HZ"):
        fake.write(f"C1:OUTP LOAD,{load}")
    assert bswv(fake)["AMP"] == "2.4V"
    fake.write("C1:BSWV AMP,2")
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:OUTP LOAD,HZ")
    assert bswv(fake)["AMP"] == "2V"


def test_load_change_rescales_dc_offset() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,DC")
    fake.write("C1:BSWV OFST,4")
    fake.write("C1:OUTP LOAD,50")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,2V"


def test_pulse_width_and_duty_are_one_quantity() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV WIDTH,1E-04")
    assert (bswv(fake)["DUTY"], bswv(fake)["WIDTH"]) == ("10", "0.0001")
    fake.write("C1:BSWV DUTY,25")
    assert (bswv(fake)["DUTY"], bswv(fake)["WIDTH"]) == ("25", "0.00025")


def test_pulse_width_in_seconds_survives_a_frequency_change() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV FRQ,2000")
    assert (bswv(fake)["DUTY"], bswv(fake)["WIDTH"]) == ("40", "0.0002")


def test_pulse_width_is_clamped_to_the_period_and_the_minimum() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV FRQ,30E6")  # clamps to 25 MHz, the width to PERI - 16.3 ns
    fields = bswv(fake)
    assert (fields["FRQ"], fields["DUTY"], fields["WIDTH"]) == ("25000000HZ", "59.25", "2.37e-08")
    fake.write("C1:BSWV WIDTH,0")
    assert bswv(fake)["WIDTH"] == "1.63e-08"
    fake.write("C1:BSWV WIDTH,1")
    assert bswv(fake)["WIDTH"] == "2.37e-08"


def test_square_duty_and_pulse_duty_are_separate() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV DUTY,30")
    fake.write("C1:BSWV WVTP,PULSE")
    assert bswv(fake)["DUTY"] == "20"
    fake.write("C1:BSWV DUTY,60")
    fake.write("C1:BSWV WVTP,SQUARE")
    assert bswv(fake)["DUTY"] == "30"


def test_values_survive_a_wvtp_change_through_every_type() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,RAMP")
    fake.write("C1:BSWV SYM,12.34")
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV RISE,1E-06")
    fake.write("C1:BSWV WVTP,NOISE")
    fake.write("C1:BSWV WVTP,RAMP")
    assert bswv(fake)["SYM"] == "12.34"
    fake.write("C1:BSWV WVTP,PULSE")
    assert bswv(fake)["RISE"] == "1e-06S"


def test_noise_stdev_and_mean_are_views_of_amp_and_ofst() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE")
    fake.write("C1:BSWV STDEV,0.5")  # STDEV = 0.0575 * AMP
    fake.write("C1:BSWV MEAN,0.1")
    fake.write("C1:BSWV WVTP,SINE")
    fields = bswv(fake)
    assert (fields["AMP"], fields["OFST"]) == ("8.69565V", "0.1V")
    fake.write("C1:BSWV AMP,2")
    fake.write("C1:BSWV WVTP,NOISE")
    assert bswv(fake)["STDEV"] == "0.115V"


def test_noise_bandwidth_default_and_format() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE")
    fake.write("C1:BSWV BANDSTATE,ON")
    assert bswv(fake)["BANDWIDTH"] == "120000000HZ"
    fake.write("C1:BSWV BANDWIDTH,100E6")  # PG02 3.4
    assert bswv(fake)["BANDWIDTH"] == "100000000HZ"


def test_dc_has_its_own_offset() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,1")
    fake.write("C1:BSWV WVTP,DC")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,0V"
    fake.write("C1:BSWV OFST,12")  # clamped to 10*k
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,10V"
    fake.write("C1:BSWV WVTP,SINE")
    assert bswv(fake)["OFST"] == "1V"


def test_above_20_mhz_the_levels_are_clamped_to_5_v() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,20,FRQ,20E6")  # PG02 3.4
    assert bswv(fake)["AMP"] == "20V"
    fake.write("C1:BSWV FRQ,20000001")  # PG02 3.4: the amplitude is clamped by the frequency change
    assert bswv(fake)["AMP"] == "10V"
    fake.write("C1:BSWV AMP,1,OFST,12")  # PG02 3.4
    assert bswv(fake)["OFST"] == "4.5V"
    fake.write("C1:BSWV OFST,0,FRQ,1000")  # PG02 3.4: the clamped amplitude stays
    assert bswv(fake)["AMP"] == "1V"
