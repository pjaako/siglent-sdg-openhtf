"""One test per rule A1 to A9 of the acceptance addendum (SPEC-modulation.md), on the fake only.

The replies are the ones the generator gave (tests/data/hardware_modulation_2.txt); the commands are
PG02 §3.5 (MDWV), §3.6.1 (SWWV), §3.7 (BTWV) and §3.4 (BSWV).
"""

from siglent_sdg_openhtf.fake_resource import FakeSdgResource


def _fake(*commands: str) -> FakeSdgResource:
    fake = FakeSdgResource()
    for command in commands:
        fake.write(command)
    return fake


def _own(reply: str) -> str:
    """The reply without its header and without the carrier part."""
    return reply.split(" ", 1)[1].split(",CARR,")[0]


def test_a1_sweep_parameters_apply_while_off() -> None:
    fake = _fake("C1:SWWV TIME,3", "C1:SWWV START,100", "C1:SWWV STATE,ON")  # PG02 §3.6.1
    assert _own(fake.query("C1:SWWV?")).startswith("STATE,ON,TIME,3S,STOP,1500HZ,START,100HZ")  # PG02 §3.6.1
    assert "CARR,WVTP,SINE,FRQ,800HZ" in fake.query("C1:SWWV?")  # PG02 §3.6.1
    # MDWV and BTWV still ignore them while off
    fake = _fake("C1:MDWV AM,DEPTH,50", "C1:MDWV STATE,ON", "C1:BTWV PRD,0.5")  # PG02 §3.5, §3.7
    assert "DEPTH,100" in fake.query("C1:MDWV?")  # PG02 §3.5


def test_a2_bswv_wvtp_switches_modulation_and_sweep_off() -> None:
    fake = _fake("C1:MDWV STATE,ON", "C1:BSWV WVTP,SQUARE")  # PG02 §3.5, §3.4
    assert fake.query("C1:MDWV?") == "C1:MDWV STATE,OFF"  # PG02 §3.5
    fake = _fake("C1:SWWV STATE,ON", "C1:BSWV WVTP,RAMP")  # PG02 §3.6.1, §3.4
    assert fake.query("C1:SWWV?") == "C1:SWWV STATE,OFF"  # PG02 §3.6.1
    fake = _fake("C1:MDWV STATE,ON", "C1:MDWV CARR,WVTP,SQUARE")  # PG02 §3.5: CARR keeps it on
    assert fake.query("C1:MDWV?").startswith("C1:MDWV STATE,ON")  # PG02 §3.5


def test_a3_carrier_has_ampdbm_at_a_numeric_load() -> None:
    fake = _fake("C1:OUTP LOAD,50", "C1:MDWV STATE,ON")  # PG02 §3.3, §3.5
    carrier = fake.query("C1:MDWV?").split(",CARR,")[1]  # PG02 §3.5
    keys = carrier.split(",")[0::2]
    assert keys.index("AMPDBM") == keys.index("AMPVRMS") + 1
    assert "AMPDBM" not in _fake("C1:MDWV STATE,ON").query("C1:MDWV?")  # HiZ: none, PG02 §3.5


def test_a4_fm_limited_by_the_carrier_frequency() -> None:
    fake = _fake("C1:MDWV STATE,ON", "C1:MDWV FM,FRQ,5E6")  # PG02 §3.5
    assert "FM,MDSP,SINE,SRC,INT,FRQ,1000HZ" in fake.query("C1:MDWV?")  # PG02 §3.5
    fake = _fake("C1:MDWV STATE,ON", "C1:MDWV FM,DEVI,700", "C1:BSWV FRQ,500")  # PG02 §3.5, §3.4
    assert "DEVI,500HZ" in fake.query("C1:MDWV?")  # PG02 §3.5


def test_a5_fsk_hfrq_zero_reads_minimum() -> None:
    fake = _fake("C1:MDWV STATE,ON", "C1:MDWV FSK,HFRQ,0")  # PG02 §3.5
    assert "HFRQ,0.001HZ" in fake.query("C1:MDWV?")  # PG02 §3.5


def test_a6_carrier_frequency_keeps_the_sweep_centred() -> None:
    fake = _fake("C1:SWWV STATE,ON", "C1:SWWV START,10000", "C1:SWWV STOP,90000", "C1:BSWV FRQ,20000")  # PG02 §3.6.1, §3.4
    assert "STOP,40000HZ,START,1e-06HZ" in fake.query("C1:SWWV?")  # PG02 §3.6.1
    fake.write("C1:BSWV FRQ,100")  # PG02 §3.4
    assert "STOP,200HZ,START,1e-06HZ" in fake.query("C1:SWWV?")  # PG02 §3.6.1


def test_a7_ext_resets_trmd() -> None:
    fake = _fake("C1:SWWV STATE,ON", "C1:SWWV TRMD,ON", "C1:SWWV TRSR,EXT", "C1:SWWV TRSR,MAN")  # PG02 §3.6.1
    assert "TRSR,MAN,TRMD,OFF" in fake.query("C1:SWWV?")  # PG02 §3.6.1
    fake = _fake("C1:BTWV STATE,ON", "C1:BTWV TRMD,RISE", "C1:BTWV TRSR,EXT", "C1:BTWV TRSR,MAN")  # PG02 §3.7
    assert "TRSR,MAN,TRMD,OFF" in fake.query("C1:BTWV?")  # PG02 §3.7


def test_a8_burst_trmd_rise_and_fall_echo_under_int_and_man() -> None:
    fake = _fake("C1:BTWV STATE,ON", "C1:BTWV TRMD,RISE")  # PG02 §3.7
    assert "TRSR,INT,TRMD,RISE" in fake.query("C1:BTWV?")  # PG02 §3.7
    fake.write("C1:BTWV TRMD,FALL")  # PG02 §3.7
    fake.write("C1:BTWV TRSR,MAN")  # PG02 §3.7
    assert "TRSR,MAN,TRMD,FALL" in fake.query("C1:BTWV?")  # PG02 §3.7


def test_a9_trsr_int_refused_while_time_is_inf() -> None:
    fake = _fake("C1:BTWV STATE,ON", "C1:BTWV TIME,INF", "C1:BTWV TRSR,INT")  # PG02 §3.7
    assert "TRSR,EXT,TIME,INF" in fake.query("C1:BTWV?")  # PG02 §3.7
    fake.write("C1:BTWV TIME,4")  # PG02 §3.7
    fake.write("C1:BTWV TRSR,INT")  # PG02 §3.7
    assert "TRSR,INT" in fake.query("C1:BTWV?")  # PG02 §3.7


def test_a10_fm_frq_follows_a_falling_carrier() -> None:
    fake = _fake("C1:BSWV FRQ,2500", "C1:MDWV STATE,ON", "C1:MDWV FM,FRQ,9000")  # PG02 §3.4, §3.5
    assert "FM,MDSP,SINE,SRC,INT,FRQ,2500HZ" in fake.query("C1:MDWV?")  # PG02 §3.5
    fake.write("C1:BSWV FRQ,1500")  # PG02 §3.4
    assert "FM,MDSP,SINE,SRC,INT,FRQ,1500HZ" in fake.query("C1:MDWV?")  # PG02 §3.5


def test_a11_sweep_centring_respects_the_carrier_maximum() -> None:
    fake = _fake("C1:SWWV STATE,ON", "C1:BSWV FRQ,45E6")  # PG02 §3.6.1, §3.4
    assert "STOP,4e+07HZ,START,4e+07HZ" in fake.query("C1:SWWV?")  # PG02 §3.6.1


def test_a12_sweep_follows_a_clamped_carrier_type_change() -> None:
    fake = _fake("C1:SWWV STATE,ON", "C1:BSWV FRQ,40E6", "C1:SWWV CARR,WVTP,SQUARE")  # PG02 §3.6.1, §3.4
    reply = fake.query("C1:SWWV?")  # PG02 §3.6.1
    assert "STOP,2.5e+07HZ,START,2.5e+07HZ" in reply
    assert "CARR,WVTP,SQUARE,FRQ,25000000HZ" in reply


def test_a13_gate_with_man_switches_trsr_to_int() -> None:
    fake = _fake("C1:BTWV STATE,ON", "C1:BTWV TRSR,MAN", "C1:BTWV GATE_NCYC,GATE")  # PG02 §3.7
    assert "TRSR,INT,GATE_NCYC,GATE" in fake.query("C1:BTWV?")  # PG02 §3.7


def test_a14_trmd_is_ignored_in_gate_mode() -> None:
    fake = _fake("C1:BTWV STATE,ON", "C1:BTWV TRMD,RISE", "C1:BTWV GATE_NCYC,GATE", "C1:BTWV TRMD,FALL")  # PG02 §3.7
    fake.write("C1:BTWV GATE_NCYC,NCYC")  # PG02 §3.7
    assert "TRSR,INT,TRMD,RISE" in fake.query("C1:BTWV?")  # PG02 §3.7


def test_a15_state_off_of_any_command_switches_the_active_mode_off() -> None:
    fake = FakeSdgResource()
    fake.write("C1:MDWV STATE,ON")  # PG02 §3.5
    fake.write("C1:SWWV STATE,OFF")  # PG02 §3.6.1
    assert fake.query("C1:MDWV?") == "C1:MDWV STATE,OFF"  # PG02 §3.5
    fake.write("C1:BTWV STATE,ON")  # PG02 §3.7
    fake.write("C1:MDWV STATE,OFF")  # PG02 §3.5
    assert fake.query("C1:BTWV?") == "C1:BTWV STATE,OFF"  # PG02 §3.7
