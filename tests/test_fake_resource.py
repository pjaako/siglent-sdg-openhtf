"""Tests for the fake instrument. Pure Python: no hardware, no sleeps, no network.

The fake is the oracle for everything else, so its expected strings are written out here literally (from
PG02 section 3.1-3.4 samples where PG02 has one) and are parsed by a helper local to this file, never by
the package's own parser. Expected values for non-SINE key sets are hypotheses until hardware session 1.
"""

import ast
import inspect
import re
import subprocess
import sys

import pytest

from siglent_sdg_openhtf import fake_resource
from siglent_sdg_openhtf.fake_resource import FakeSdgResource

# PG02 3.4 sample reply of C1:BSWV?
PG02_BSWV_DEFAULT = "C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0"
# PG02 3.3 shape of the C1:OUTP? reply (the sample there shows ON; the fake powers up OFF)
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


@pytest.mark.parametrize("load", ["10", "49.9", "100001", "0", "-50", "abc", "50OHM", "nan", ""])
def test_outp_load_out_of_range_ignored(load: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:OUTP LOAD,{load}")  # PG02 3.3: SDG2000X 50~100000, HiZ
    assert fake.query("C1:OUTP?") == OUTP_DEFAULT


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
    # LOAD-rescale hypothesis (third-party reports): HZ -> number halves AMP and OFST
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,4")
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
    assert bswv(fake)["AMP"] == "1V"
    fake.write("C1:OUTP LOAD,HZ")
    assert bswv(fake)["AMP"] == "2V"


def test_load_number_to_number_does_not_rescale() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:OUTP LOAD,75")
    assert bswv(fake)["AMP"] == "1V"


def test_load_same_value_does_not_rescale() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,HZ")
    assert bswv(fake)["AMP"] == "2V"
    fake.write("C1:OUTP LOAD,50")
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake)["AMP"] == "1V"


def test_load_rescale_is_per_channel() -> None:
    fake = FakeSdgResource()
    fake.write("C1:OUTP LOAD,50")
    assert bswv(fake, "C2")["AMP"] == "2V"


def test_wvtp_ramp_and_frq_amp_examples() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,RAMP")  # PG02 3.4
    fake.write("C1:BSWV FRQ,2000")  # PG02 3.4
    fake.write("C1:BSWV AMP,3")  # PG02 3.4
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,RAMP,FRQ,2000HZ,PERI,0.0005S,AMP,3V,OFST,0V,HLEV,1.5V,LLEV,-1.5V,PHSE,0,SYM,50"
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


def test_frq_fractional_format() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,1234.5678901")
    fields = bswv(fake)
    assert fields["FRQ"] == "1234.5678901HZ"


def test_hlev_llev_set_amp_and_offset() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV HLEV,3")
    fields = bswv(fake)
    # low level stays at -1 V: AMP = 3 - (-1) = 4, OFST = 1
    assert (fields["AMP"], fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("4V", "1V", "3V", "-1V")
    fake.write("C1:BSWV LLEV,0")
    fields = bswv(fake)
    assert (fields["AMP"], fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("3V", "1.5V", "3V", "0V")


def test_hlev_below_llev_ignored() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV HLEV,-2")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_ofst_and_hlev_follow_each_other() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,0.5")
    fields = bswv(fake)
    assert (fields["OFST"], fields["HLEV"], fields["LLEV"]) == ("0.5V", "1.5V", "-0.5V")


def test_derived_levels_have_no_float_noise() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,0.1")
    fake.write("C1:BSWV AMP,0.3")
    fields = bswv(fake)
    assert (fields["HLEV"], fields["LLEV"]) == ("0.25V", "-0.05V")


def test_ampvrms_sine() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMPVRMS,1")  # PG02 3.4: AMPVRMS = AMP / (2*sqrt(2)) for a sine
    assert bswv(fake)["AMP"] == "2.82842712475V"


def test_ampdbm_sine() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMPDBM,0")  # 0 dBm = 1 mW into 50 ohm = 0.2236 Vrms = 0.6325 Vpp
    assert bswv(fake)["AMP"] == "0.632455532034V"
    fake.write("C1:BSWV AMPDBM,3")  # PG02 3.4 example value
    assert bswv(fake)["AMP"] == "0.893367184302V"


def test_ampvrms_ignored_on_non_sine() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    fake.write("C1:BSWV AMPVRMS,1")
    assert bswv(fake)["AMP"] == "2V"


def test_wvtp_square_adds_duty() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,SQUARE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0,DUTY,50"
    )
    fake.write("C1:BSWV DUTY,25")
    assert bswv(fake)["DUTY"] == "25"


def test_wvtp_pulse_drops_phse() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,PULSE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,"
        "WIDTH,0.000001S,RISE,0.00000001S,FALL,0.00000001S,DLY,0S"
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
        "0.0001S",
        "0.000002S",
        "0.000003S",
        "0.001S",
    )


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
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,NOISE,STDEV,0.5V,MEAN,0V,BANDSTATE,OFF"
    fake.write("C1:BSWV BANDSTATE,ON")
    fake.write("C1:BSWV BANDWIDTH,100E6")  # PG02 3.4 example
    fake.write("C1:BSWV STDEV,1.5")
    fake.write("C1:BSWV MEAN,-0.25")
    assert fake.query("C1:BSWV?") == (
        "C1:BSWV WVTP,NOISE,STDEV,1.5V,MEAN,-0.25V,BANDSTATE,ON,BANDWIDTH,100000000HZ"
    )


@pytest.mark.parametrize("key", ["FRQ", "PERI", "AMP", "AMPVRMS", "AMPDBM", "OFST", "PHSE", "HLEV", "LLEV"])
def test_noise_ignores_waveform_keys(key: str) -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,NOISE")
    before = fake.query("C1:BSWV?")
    fake.write(f"C1:BSWV {key},1")
    assert fake.query("C1:BSWV?") == before
    fake.write("C1:BSWV WVTP,SINE")
    assert bswv(fake)["FRQ"] == "100HZ"
    assert bswv(fake)["AMP"] == "2V"


def test_wvtp_dc_has_only_ofst() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,DC")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,0V"
    fake.write("C1:BSWV OFST,1.5")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,1.5V"
    fake.write("C1:BSWV FRQ,500")  # not valid for DC
    fake.write("C1:BSWV AMP,5")
    assert fake.query("C1:BSWV?") == "C1:BSWV WVTP,DC,OFST,1.5V"


def test_wvtp_arb_has_sine_key_set() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,ARB")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT.replace("SINE", "ARB")


def test_wvtp_change_keeps_common_values_and_resets_type_specific() -> None:
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
    assert bswv(fake)["DUTY"] == "50"


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


def test_frq_50mhz_ignored_on_sdg2042x() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,50E6")
    assert bswv(fake)["FRQ"] == "100HZ"
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
    assert bswv(fake)["FRQ"] == "100HZ"


@pytest.mark.parametrize(("wvtp", "limit"), [("SQUARE", 25e6), ("PULSE", 25e6), ("RAMP", 1e6)])
def test_frq_limits_per_wave_type(wvtp: str, limit: float) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV WVTP,{wvtp}")
    fake.write(f"C1:BSWV FRQ,{limit * 1.001}")
    assert bswv(fake)["FRQ"] == "100HZ"
    fake.write(f"C1:BSWV FRQ,{limit}")
    assert bswv(fake)["FRQ"] == f"{int(limit)}HZ"


def test_wvtp_change_clamps_frq_to_new_limit() -> None:
    # clamp is a hypothesis until hardware session 1
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,30E6")
    fake.write("C1:BSWV WVTP,RAMP")
    assert bswv(fake)["FRQ"] == "1000000HZ"


@pytest.mark.parametrize("value", ["0", "-5", "abc", "1k", "nan", "inf"])
def test_frq_invalid_values_ignored(value: str) -> None:
    fake = FakeSdgResource()
    fake.write(f"C1:BSWV FRQ,{value}")
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


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


def test_amp_zero_or_negative_ignored() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,0")
    fake.write("C1:BSWV AMP,-1")
    assert bswv(fake)["AMP"] == "2V"


def test_offset_limit() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV OFST,10")
    assert bswv(fake)["OFST"] == "10V"
    fake.write("C1:BSWV OFST,10.5")
    assert bswv(fake)["OFST"] == "10V"
    fake.write("C1:BSWV OFST,-10")
    assert bswv(fake)["OFST"] == "-10V"


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


@pytest.mark.parametrize(("key", "value"), [("PHSE", "361"), ("PHSE", "-1"), ("DUTY", "101"), ("SYM", "-1")])
def test_range_limited_keys_ignore_out_of_range(key: str, value: str) -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE" if key == "DUTY" else "C1:BSWV WVTP,RAMP" if key == "SYM" else "C1:BSWV FRQ,100")
    before = fake.query("C1:BSWV?")
    fake.write(f"C1:BSWV {key},{value}")
    assert fake.query("C1:BSWV?") == before


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
    # PG02 3.3: C1:BSWV MAX_OUTPUT_AMP,5. Whether the BSWV? reply carries it is open (STATUS.md).
    fake = FakeSdgResource()
    fake.write("C1:BSWV MAX_OUTPUT_AMP,5")
    fake.write("C1:BSWV MAX_OUTPUT_AMP,0.5")  # outside {1-20}: ignored, nothing to observe either way
    assert fake.query("C1:BSWV?") == PG02_BSWV_DEFAULT


def test_multi_pair_bswv_command_applied_in_order() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,SQUARE,FRQ,1000,AMP,3,DUTY,20")  # hypothesis: multi-pair accepted
    fields = bswv(fake)
    assert (fields["WVTP"], fields["FRQ"], fields["AMP"], fields["DUTY"]) == ("SQUARE", "1000HZ", "3V", "20")


def test_multi_pair_invalid_pair_does_not_block_others() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV FRQ,50E6,AMP,3")
    fields = bswv(fake)
    assert (fields["FRQ"], fields["AMP"]) == ("100HZ", "3V")


def test_bswv_odd_token_count_ignores_dangling_key() -> None:
    fake = FakeSdgResource()
    fake.write("C1:BSWV AMP,3,FRQ")
    fields = bswv(fake)
    assert (fields["AMP"], fields["FRQ"]) == ("3V", "100HZ")


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


def test_no_exponent_notation_in_replies() -> None:
    # number format hypothesis: plain decimals, never 1E-08
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,PULSE")
    fake.write("C1:BSWV RISE,0.0000000123")
    fake.write("C1:BSWV FRQ,1E3")
    reply = fake.query("C1:BSWV?")
    assert "RISE,0.0000000123S" in reply
    assert re.search(r"\d[eE][-+]?\d", reply) is None


def test_module_does_not_pull_in_pyvisa_or_openhtf() -> None:
    # The fake is an independent oracle: importing it must not import pyvisa or openhtf.
    code = (
        "import sys\n"
        "import siglent_sdg_openhtf.fake_resource\n"
        "assert 'pyvisa' not in sys.modules, 'pyvisa imported'\n"
        "assert 'openhtf' not in sys.modules, 'openhtf imported'\n"
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
