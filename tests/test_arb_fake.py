"""Tests for the arbitrary waveform part of FakeSdgResource (PG02 §3.9.1, §3.31, §3.32, §3.40)."""

import struct

import pytest

from siglent_sdg_openhtf import fake_resource
from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.scpi import BUILTIN_ARBS, parse_stl, parse_stl_builtin, parse_wvdt_reply

RAMP = struct.pack("<8h", -32768, -24576, -16384, -8192, 0, 8191, 16383, 32767)


def _fake() -> FakeSdgResource:
    fake = FakeSdgResource()
    fake.write("C1:BSWV WVTP,ARB")  # PG02 §3.4
    return fake


def _upload(fake: FakeSdgResource, name: str, data: bytes = RAMP, header: str = "") -> None:
    fake.write_raw(f"C1:WVDT WVNM,{name}{header},WAVEDATA,".encode() + data + b"\n")  # PG02 §3.32


def _read(fake: FakeSdgResource, order: str) -> bytes:
    fake.write(order)  # PG02 §3.32
    return fake.read_message()


def test_builtin_table_equals_scpi_table() -> None:
    assert fake_resource._BUILTIN_ARBS == BUILTIN_ARBS


def test_defaults_after_reset() -> None:
    fake = FakeSdgResource()
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,0,NAME,StairUp"  # PG02 §3.9.1
    assert fake.query("C2:ARWV?") == "C2:ARWV INDEX,0,NAME,StairUp"  # PG02 §3.9.1
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,DDS"  # PG02 §3.40
    assert fake.query("STL? USER") == "STL WVNM"  # PG02 §3.31


def test_select_builtin_by_index_switches_to_arb() -> None:
    fake = FakeSdgResource()
    fake.write("C1:ARWV INDEX,2")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,2,NAME,StairUD"  # PG02 §3.9.1
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,ARB,")  # PG02 §3.4
    assert fake.query("C2:ARWV?") == "C2:ARWV INDEX,0,NAME,StairUp"  # per channel


def test_index_out_of_range_and_builtin_names_are_ignored() -> None:
    fake = _fake()
    fake.write("C1:ARWV INDEX,5")  # PG02 §3.9.1
    for order in ("C1:ARWV INDEX,197", "C1:ARWV INDEX,-1", "C1:ARWV NAME,Trapezia", 'C1:ARWV NAME,"Trapezia"'):
        fake.write(order)  # PG02 §3.9.1
        assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,5,NAME,Trapezia"  # PG02 §3.9.1


def test_stl_lists() -> None:
    fake = FakeSdgResource()
    assert fake.query("STL?") == fake.query("STL? BUILDIN")  # PG02 §3.31
    assert parse_stl_builtin(fake.query("STL?")) == dict(enumerate(BUILTIN_ARBS))  # PG02 §3.31
    assert fake.query("STL?").startswith("STL M0, StairUp, M1, StairDn, M10, LogFall, M100, LFPulse")  # PG02 §3.31
    _upload(fake, "b")
    _upload(fake, "a")
    assert parse_stl(fake.query("STL? USER")) == ["b", "a"]  # PG02 §3.31, upload order
    with pytest.raises(ValueError):
        fake.query("STL? Local")  # PG02 §3.31 path forms are not modelled


def test_upload_does_not_select_and_round_trips() -> None:
    fake = _fake()
    _upload(fake, "htf_test")
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,0,NAME,StairUp"  # PG02 §3.9.1
    fake.write("C1:ARWV NAME,htf_test")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,htf_test.bin"  # PG02 §3.9.1
    fields, data = parse_wvdt_reply(_read(fake, "WVDT? USER,htf_test"))  # PG02 §3.32
    assert data == RAMP
    assert fields["POS"] == "/Local"
    assert fields["length"] == len(RAMP)


def test_data_with_newline_bytes_round_trips() -> None:
    fake = _fake()
    data = b"\x0a\x0d" * 8
    _upload(fake, "nl", data)
    assert parse_wvdt_reply(_read(fake, "WVDT? USER,nl"))[1] == data  # PG02 §3.32


def test_odd_upload_stores_the_newline() -> None:
    fake = _fake()
    _upload(fake, "odd", b"\xff\x7f\xff\x7f\xff")
    message = _read(fake, "WVDT? USER,odd")  # PG02 §3.32
    assert b"LENGTH, 6B" in message
    assert message.endswith(b"WAVEDATA,\xff\x7f\xff\x7f\xff\n\n")


def test_header_less_upload_changes_nothing_on_selection() -> None:
    fake = _fake()
    fake.write("C1:BSWV FRQ,5000")  # PG02 §3.4
    fake.write("C1:BSWV AMP,3")  # PG02 §3.4
    before = fake.query("C1:BSWV?")  # PG02 §3.4
    _upload(fake, "plain")
    fake.write("C1:ARWV NAME,plain")  # PG02 §3.9.1
    assert fake.query("C1:BSWV?") == before  # PG02 §3.4


def test_header_fields_apply_on_selection() -> None:
    fake = _fake()
    _upload(fake, "full", header=",FREQ,2500,AMPL,1,OFST,0.5,PHASE,90")
    fake.write("C1:ARWV NAME,full")  # PG02 §3.9.1
    assert fake.query("C1:BSWV?") == (  # PG02 §3.4
        "C1:BSWV WVTP,ARB,FRQ,2500HZ,PERI,0.0004S,AMP,1V,OFST,0.5V,HLEV,1V,LLEV,0V,PHSE,90"
    )
    _upload(fake, "freq", header=",LENGTH,128B,FREQ,250")
    fake.write("C1:ARWV NAME,freq")  # PG02 §3.9.1
    assert fake.query("C1:BSWV?") == (  # PG02 §3.4
        "C1:BSWV WVTP,ARB,FRQ,250HZ,PERI,0.004S,AMP,1V,OFST,0.5V,HLEV,1V,LLEV,0V,PHSE,90"
    )


def test_selection_switches_a_sine_channel_to_arb() -> None:
    fake = FakeSdgResource()
    _upload(fake, "w")
    fake.write("C2:ARWV NAME,w")  # PG02 §3.9.1
    assert fake.query("C2:BSWV?").startswith("C2:BSWV WVTP,ARB,")  # PG02 §3.4
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,SINE,")  # PG02 §3.4


def test_names_are_case_sensitive_and_bin_suffix_is_ignored() -> None:
    fake = _fake()
    _upload(fake, "htf_b1")
    _upload(fake, "HTF_B1")
    fake.write("C1:ARWV NAME,HTF_B1")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,HTF_B1.bin"  # PG02 §3.9.1
    fake.write("C1:ARWV NAME,htf_b1.bin")  # PG02 §3.9.1: a name ending in .bin is ignored (F3)
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,HTF_B1.bin"  # PG02 §3.9.1
    fake.write("C1:ARWV INDEX,196")  # PG02 §3.9.1
    fake.write("C1:ARWV NAME,htf_b1.bin")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,196,NAME,demo2_16kpts"  # PG02 §3.9.1
    fake.write("C1:ARWV NAME,htf_B1")  # PG02 §3.9.1: unknown, ignored
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,196,NAME,demo2_16kpts"  # PG02 §3.9.1


def test_unknown_user_name_is_ignored() -> None:
    fake = _fake()
    _upload(fake, "known")
    fake.write("C1:ARWV NAME,known")  # PG02 §3.9.1
    fake.write("C1:ARWV NAME,nothing")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,known.bin"  # PG02 §3.9.1


def test_reupload_replaces_data_and_keeps_selection_and_parameters() -> None:
    fake = _fake()
    _upload(fake, "w", header=",FREQ,100,AMPL,4")
    fake.write("C1:ARWV NAME,w")  # PG02 §3.9.1
    fake.write("C1:BSWV FRQ,1000")  # PG02 §3.4
    _upload(fake, "w", data=b"\x00\x80\xff\x7f")
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,w.bin"  # PG02 §3.9.1
    assert "FRQ,1000HZ" in fake.query("C1:BSWV?")  # PG02 §3.4
    assert parse_wvdt_reply(_read(fake, "WVDT? USER,w"))[1] == b"\x00\x80\xff\x7f"  # PG02 §3.32
    assert list(fake.user_arbs) == ["w"]


def test_quoted_name_is_kept_as_given() -> None:
    fake = _fake()
    _upload(fake, '"q x"')
    assert parse_stl(fake.query("STL? USER")) == ['"q x"']  # PG02 §3.31 (the space removal is not modelled)


def test_builtin_read() -> None:
    fake = _fake()
    fields, data = parse_wvdt_reply(_read(fake, "WVDT? M2"))  # PG02 §3.32
    assert fields["POS"] == "M2"
    assert fields["WVNM"] == "StairUD"
    assert fields["length"] == 32768
    assert len(data) == 32768


def test_unknown_wave_reads_empty() -> None:
    fake = _fake()
    assert _read(fake, "WVDT? USER,nothing") == b""  # PG02 §3.32
    assert fake.query("*IDN?")  # the next query answers normally


def test_read_message_once_and_only_when_pending() -> None:
    fake = _fake()
    with pytest.raises(ValueError):
        fake.read_message()
    _upload(fake, "w")
    fake.write("WVDT? USER,w")  # PG02 §3.32
    assert fake.read_message()
    with pytest.raises(ValueError):
        fake.read_message()
    fake.write("WVDT? USER,w")  # PG02 §3.32
    fake.query("*IDN?")  # a query drops the unread reply
    with pytest.raises(ValueError):
        fake.read_message()


def test_query_of_wvdt_raises() -> None:
    fake = _fake()
    with pytest.raises(ValueError, match="read_message"):
        fake.query("WVDT? USER,w")  # PG02 §3.32
    with pytest.raises(ValueError, match="read_message"):
        fake.query("WVDT? M0")  # PG02 §3.32


def test_write_raw_accepts_only_wvdt() -> None:
    fake = FakeSdgResource()
    for data in (b"C1:BSWV FRQ,1", b"*RST", b"C3:WVDT WVNM,x,WAVEDATA,\x00\x00\x00\x00\n", b"C1:WVDT FREQ,1,WAVEDATA,\x00\x00"):
        with pytest.raises(ValueError):
            fake.write_raw(data)
    assert fake.user_arbs == {}


def test_write_raw_logs_the_header() -> None:
    fake = FakeSdgResource()
    _upload(fake, "w", header=",FREQ,1000")
    assert fake.log == ["C1:WVDT WVNM,w,FREQ,1000,WAVEDATA,"]  # PG02 §3.32


def test_reset_keeps_the_store_and_resets_selection_and_mode() -> None:
    fake = _fake()
    _upload(fake, "w")
    fake.write("C1:ARWV NAME,w")  # PG02 §3.9.1
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    fake.write("*RST")  # PG02 §3.1.3
    assert fake.query("C1:ARWV?") == "C1:ARWV INDEX,0,NAME,StairUp"  # PG02 §3.9.1
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,DDS"  # PG02 §3.40
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,SINE,")  # PG02 §3.4
    assert parse_stl(fake.query("STL? USER")) == ["w"]  # PG02 §3.31


def test_channel_two_shares_the_store() -> None:
    fake = FakeSdgResource()
    fake.write_raw(b"C2:WVDT WVNM,htf_c2,WAVEDATA,\x00\x00\x01\x00\n")  # PG02 §3.32
    fake.write("C1:BSWV WVTP,ARB")  # PG02 §3.4
    fake.write("C1:ARWV NAME,htf_c2")  # PG02 §3.9.1
    assert fake.query("C1:ARWV?") == "C1:ARWV NAME,htf_c2.bin"  # PG02 §3.9.1
    fake.write("C2:ARWV NAME,htf_c2")  # PG02 §3.9.1
    assert fake.query("C2:ARWV?") == "C2:ARWV NAME,htf_c2.bin"  # PG02 §3.9.1


# -- SRATE ---------------------------------------------------------------------------------------------------


def test_tarb_builtin_frequency_coupling() -> None:
    fake = _fake()
    fake.write("C1:ARWV INDEX,3")  # PG02 §3.9.1
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,16384000Sa/s"  # PG02 §3.40
    assert "FRQ,1000HZ" in fake.query("C1:BSWV?")  # PG02 §3.4
    fake.write("C1:SRATE VALUE,1000000")  # PG02 §3.40
    assert "FRQ,61.03515625HZ,PERI,0.016384S" in fake.query("C1:BSWV?")  # PG02 §3.4
    fake.write("C1:BSWV FRQ,2000")  # PG02 §3.4
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,32768000Sa/s"  # PG02 §3.40


def test_tarb_user_wave_frequency_coupling() -> None:
    fake = _fake()
    _upload(fake, "w4096", bytes(8192), ",FREQ,2500")
    fake.write("C1:ARWV NAME,w4096")  # PG02 §3.9.1
    fake.write("C1:SRATE MODE,TARB,VALUE,4096000")  # PG02 §3.40
    assert "FRQ,1000HZ" in fake.query("C1:BSWV?")  # PG02 §3.4
    fake.write("C1:SRATE VALUE,409600")  # PG02 §3.40
    assert "FRQ,100HZ" in fake.query("C1:BSWV?")  # PG02 §3.4
    for points, shown in ((3, "5461333.333HZ"), (1000, "16384HZ"), (16385, "999.9389686HZ")):
        _upload(fake, "n", bytes(2 * points))
        fake.write("C1:SRATE MODE,DDS")  # PG02 §3.40
        fake.write("C1:SRATE VALUE,16384000")  # ignored in DDS
        fake.write("C1:ARWV NAME,n")  # PG02 §3.9.1
        fake.write("C1:SRATE MODE,TARB,VALUE,16384000")  # PG02 §3.40
        assert f"FRQ,{shown}," in fake.query("C1:BSWV?")  # PG02 §3.4


def test_value_clamp_and_dds_behaviour() -> None:
    fake = _fake()
    fake.write("C1:ARWV INDEX,3")  # PG02 §3.9.1
    fake.write("C1:SRATE VALUE,2000000")  # PG02 §3.40: ignored under DDS
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,DDS"  # PG02 §3.40
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    fake.write("C1:SRATE VALUE,1E9")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,75000000Sa/s"  # PG02 §3.40
    fake.write("C1:SRATE VALUE,0.1")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,0.1Sa/s"  # PG02 §3.40
    fake.write("C1:SRATE VALUE,12345.678")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,12345.678Sa/s"  # PG02 §3.40
    fake.write("C1:SRATE MODE,DDS")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,DDS"  # PG02 §3.40
    assert "FRQ,0.7535203857HZ,PERI,1.3271S" in fake.query("C1:BSWV?")  # PG02 §3.4


def test_tarb_switches_a_sine_channel_to_arb() -> None:
    fake = FakeSdgResource()
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,ARB,")  # PG02 §3.4


def test_srate_rate_is_per_channel_and_inter_is_ignored() -> None:
    fake = _fake()
    fake.write("C1:SRATE MODE,TARB,INTER,LINE")  # PG02 §3.40
    assert fake.query("C2:SRATE?") == "C2:SRATE MODE,DDS"  # PG02 §3.40
    assert fake.query("C1:SRATE?").startswith("C1:SRATE MODE,TARB")  # PG02 §3.40
    with pytest.raises(ValueError):
        fake.write("C1:SRATE FOO,1")


def test_arwv_grammar() -> None:
    fake = FakeSdgResource()
    with pytest.raises(ValueError):
        fake.write("C1:ARWV PATH,x")  # not a PG02 §3.9.1 key


def test_timeout_assignments_are_recorded() -> None:
    fake = FakeSdgResource()
    assert fake.timeout_history == []
    fake.timeout = 30000
    fake.timeout = 5000
    assert fake.timeout_history == [30000, 5000]
    assert fake.timeout == 5000


def test_reset_restores_the_truearb_rate() -> None:  # F1
    fake = _fake()
    fake.write("C1:SRATE MODE,TARB,VALUE,2000000")  # PG02 §3.40
    fake.write("C1:SRATE MODE,DDS")  # PG02 §3.40
    fake.write("*RST")  # PG02 §3.1.3
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,16384000Sa/s"  # PG02 §3.40
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,ARB,FRQ,1000HZ,")  # 16384000 / 16384, PG02 §3.4


def test_builtin_lengths() -> None:  # F2
    fake = _fake()
    lengths = {}
    for index in (0, 2, 18, 193, 194, 195, 196):
        fields, data = parse_wvdt_reply(_read(fake, f"WVDT? M{index}"))  # PG02 §3.32
        assert len(data) == fields["length"]
        lengths[index] = fields["length"]
    assert lengths == {0: 32768, 2: 32768, 18: 32768, 193: 750, 194: 32768, 195: 6000, 196: 32768}  # 195: hypothesis


def test_tarb_frequency_uses_the_builtin_length() -> None:  # F2
    fake = FakeSdgResource()
    fake.write("C1:ARWV INDEX,193")  # PG02 §3.9.1
    fake.write("C1:SRATE MODE,TARB,VALUE,375000")  # PG02 §3.40
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,ARB,FRQ,1000HZ,")  # 375 points, PG02 §3.4
    fake.write("C1:BSWV FRQ,2000")  # PG02 §3.4
    assert fake.query("C1:SRATE?") == "C1:SRATE MODE,TARB,VALUE,750000Sa/s"  # PG02 §3.40
    fake.write("C1:ARWV INDEX,0")  # PG02 §3.9.1: 16384 points, the rate is kept
    assert fake.query("C1:BSWV?").startswith("C1:BSWV WVTP,ARB,FRQ,45.77636719HZ,")  # 750000 / 16384, PG02 §3.4


def test_sweep_follows_tarb_recompute_with_the_sweep_off() -> None:  # F4
    fake = FakeSdgResource()
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40: FRQ becomes 16384000 / 16384 = 1000 Hz
    fake.write("C1:SRATE VALUE,32768000")  # PG02 §3.40: FRQ 2000 Hz
    fake.write("C1:SWWV STATE,ON")  # PG02 §3.6.1
    reply = fake.query("C1:SWWV?")  # PG02 §3.6.1
    assert reply.startswith("C1:SWWV STATE,ON,TIME,1S,STOP,2500HZ,START,1500HZ,"), reply


def test_sweep_follows_tarb_switch_with_large_carrier() -> None:  # F4, the acceptance case
    fake = FakeSdgResource()
    _upload(fake, "w", header=",PHASE,45")
    fake.write("C1:BSWV WVTP,ARB")  # PG02 §3.4
    fake.write("C1:ARWV NAME,w")  # PG02 §3.9.1: 8 points
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40: 16384000 / 8 = 2048000 Hz
    fake.write("C1:SWWV STATE,ON")  # PG02 §3.6.1
    reply = fake.query("C1:SWWV?")  # PG02 §3.6.1
    assert reply.startswith("C1:SWWV STATE,ON,TIME,1S,STOP,2.0485e+06HZ,START,2.0475e+06HZ,"), reply
    assert "FRQ,2048000HZ" in reply


def test_sweep_follows_a_stored_frequency_on_selection() -> None:  # F4
    fake = FakeSdgResource()
    _upload(fake, "w", header=",FREQ,5000")
    fake.write("C1:ARWV NAME,w")  # PG02 §3.9.1
    fake.write("C1:SWWV STATE,ON")  # PG02 §3.6.1
    assert fake.query("C1:SWWV?").startswith("C1:SWWV STATE,ON,TIME,1S,STOP,5500HZ,START,4500HZ,")  # PG02 §3.6.1
