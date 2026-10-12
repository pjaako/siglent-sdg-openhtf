"""Tests for the arbitrary waveform methods of SiglentSdgPlug on FakeSdgResource (PG02 §3.9.1, §3.31, §3.32, §3.40)."""

import struct
from typing import Any

import pytest

from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.plug import SiglentSdgPlug
from siglent_sdg_openhtf.scpi import BUILTIN_ARBS, ProtocolError, SetupError, Setup

SAMPLES = [-32768, -16384, 0, 16383, 32767, 16383, 0, -16384]
DATA = struct.pack("<8h", *SAMPLES)


def _plug(**fake_kwargs: Any) -> tuple[SiglentSdgPlug, FakeSdgResource]:
    fake = FakeSdgResource(**fake_kwargs)
    plug = SiglentSdgPlug(resource=fake)
    fake.log.clear()
    fake.timeout_history.clear()
    return plug, fake


class _TamperedFake(FakeSdgResource):
    """A fake whose stored data differs from what was written."""

    def write_raw(self, data: bytes, /) -> int:
        count = super().write_raw(data)
        name = next(reversed(self.user_arbs))
        header, stored = self.user_arbs[name]
        self.user_arbs[name] = (header, stored[:-2] + b"\x01\x01" if len(stored) % 2 == 0 else stored)
        return count


class _ShortFake(FakeSdgResource):
    """A fake that loses the last point."""

    def write_raw(self, data: bytes, /) -> int:
        count = super().write_raw(data)
        name = next(reversed(self.user_arbs))
        header, stored = self.user_arbs[name]
        self.user_arbs[name] = (header, stored[:-2])
        return count


class _ForgetfulFake(FakeSdgResource):
    """A fake that does not keep uploads."""

    def write_raw(self, data: bytes, /) -> int:
        return len(data)


# -- upload / read -------------------------------------------------------------------------------------------


def test_upload_round_trip_with_verification() -> None:
    plug, fake = _plug()
    points = plug.upload_arb(1, "htf_test", SAMPLES, freq=1000, ampl=2.0, ofst=0.0, phase=0)
    assert points == 8
    assert fake.user_arbs["htf_test"][1] == DATA
    assert fake.user_arbs["htf_test"][0] == {"FREQ": "1000", "AMPL": "2", "OFST": "0", "PHASE": "0"}
    assert fake.log == [
        "C1:WVDT WVNM,htf_test,FREQ,1000,AMPL,2,OFST,0,PHASE,0,WAVEDATA,",  # PG02 §3.32
        "STL? USER",  # PG02 §3.31
        "WVDT? USER,htf_test",  # PG02 §3.32
    ]
    fields, data = plug.read_arb("htf_test")
    assert data == DATA
    assert fields["length"] == 16
    assert plug.list_user_arbs() == ["htf_test"]


def test_upload_does_not_select() -> None:
    plug, _ = _plug()
    plug.upload_arb(1, "w", DATA)
    assert plug.get_arb(1) == {"INDEX": 0.0, "NAME": "StairUp"}


def test_upload_bytes_and_channel_two() -> None:
    plug, fake = _plug()
    assert plug.upload_arb(2, "b", b"\x0a\x0d\x0a\x0d") == 2
    assert fake.user_arbs["b"][1] == b"\x0a\x0d\x0a\x0d"
    assert fake.log[0] == "C2:WVDT WVNM,b,WAVEDATA,"  # PG02 §3.32


def test_upload_without_verification_sends_no_query() -> None:
    plug, fake = _plug()
    plug.upload_arb(1, "w", DATA, verify=False)
    assert fake.log == ["C1:WVDT WVNM,w,WAVEDATA,"]  # PG02 §3.32


@pytest.mark.parametrize(
    ("name", "samples"),
    [("bad name", DATA), ("x.bin", DATA), ("", DATA), ("w", b"\x00" * 5), ("w", [1]), ("w", [40000, 1]), ("w", [])],
)
def test_upload_invalid_sends_nothing(name: str, samples: Any) -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError):
        plug.upload_arb(1, name, samples)
    assert fake.log == []
    assert fake.user_arbs == {}


def test_upload_invalid_channel() -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError):
        plug.upload_arb(3, "w", DATA)
    assert fake.log == []


def test_upload_verification_reports_tampered_data() -> None:
    fake = _TamperedFake()
    plug = SiglentSdgPlug(resource=fake)
    with pytest.raises(SetupError) as info:
        plug.upload_arb(1, "w", DATA)
    assert info.value.failures == ["w: first differing byte at offset 14"]


def test_upload_verification_reports_length() -> None:
    fake = _ShortFake()
    plug = SiglentSdgPlug(resource=fake)
    with pytest.raises(SetupError) as info:
        plug.upload_arb(1, "w", DATA)
    assert info.value.failures == ["w: sent 16 bytes, read 14"]


def test_upload_verification_reports_missing_name() -> None:
    plug = SiglentSdgPlug(resource=_ForgetfulFake())
    with pytest.raises(SetupError) as info:
        plug.upload_arb(1, "w", DATA)
    assert info.value.failures[0] == "w: missing from STL? USER after the upload"
    assert "no such wave" in info.value.failures[1]


def test_read_arb_by_name_and_index() -> None:
    plug, _ = _plug()
    plug.upload_arb(1, "w", DATA)
    fields, data = plug.read_arb("w")
    assert data == DATA
    assert fields["WVNM"] == "w"
    fields, data = plug.read_arb(2)
    assert fields["POS"] == "M2"
    assert fields["WVNM"] == "StairUD"
    assert len(data) == 32768


def test_read_arb_unknown_and_invalid() -> None:
    plug, fake = _plug()
    with pytest.raises(ProtocolError, match="no such wave"):
        plug.read_arb("nothing")
    assert plug.idn().model == "SDG2042X"  # the next query answers normally
    fake.log.clear()
    for bad in (-1, 197, "a b", "x.bin", ""):
        with pytest.raises(ValueError):
            plug.read_arb(bad)
    with pytest.raises(TypeError):
        plug.read_arb(True)
    assert fake.log == []


def test_lists_and_getters() -> None:
    plug, fake = _plug()
    assert plug.list_arbs() == dict(enumerate(BUILTIN_ARBS))
    assert plug.list_user_arbs() == []
    assert plug.get_sample_rate(1) == {"MODE": "DDS"}
    fake.write("C1:ARWV INDEX,5")  # PG02 §3.9.1
    fake.write("C1:SRATE MODE,TARB")  # PG02 §3.40
    assert plug.get_arb(1) == {"INDEX": 5.0, "NAME": "Trapezia"}
    assert plug.get_sample_rate(1) == {"MODE": "TARB", "VALUE": 16384000.0}


# -- apply_setup ---------------------------------------------------------------------------------------------


def test_apply_setup_builtin_by_index() -> None:
    plug, fake = _plug()
    setup: Setup = {
        "C1": {
            "OUTP": {"LOAD": "HZ"},
            "BSWV": {"WVTP": "ARB", "FRQ": 2000.0, "AMP": 3.0},
            "ARWV": {"INDEX": 5},
        }
    }
    plug.apply_setup(setup)
    assert fake.log[:5] == [
        "C1:OUTP LOAD,HZ",  # PG02 §3.3
        "C1:BSWV WVTP,ARB",  # PG02 §3.4
        "C1:ARWV INDEX,5",  # PG02 §3.9.1
        "C1:BSWV FRQ,2000",  # PG02 §3.4
        "C1:BSWV AMP,3",  # PG02 §3.4
    ]
    assert plug.get_arb(1)["NAME"] == "Trapezia"


def test_apply_setup_user_wave_in_tarb_with_value() -> None:
    plug, fake = _plug()
    plug.upload_arb(1, "htf_test", bytes(8192), freq=2500, ampl=1.0, ofst=0.5, phase=90)
    fake.log.clear()
    plug.apply_setup(
        {
            "C1": {
                "BSWV": {"WVTP": "ARB", "AMP": 2.0, "OFST": 0.0},
                "ARWV": {"NAME": "htf_test"},
                "SRATE": {"MODE": "TARB", "VALUE": 4096000},
            }
        }
    )
    assert fake.log[:5] == [
        "C1:BSWV WVTP,ARB",  # PG02 §3.4
        "C1:ARWV NAME,htf_test",  # PG02 §3.9.1
        "C1:SRATE MODE,TARB",  # PG02 §3.40
        "C1:SRATE VALUE,4096000",  # PG02 §3.40
        "C1:BSWV AMP,2",  # PG02 §3.4
    ]
    assert plug.get_arb(1) == {"NAME": "htf_test.bin"}
    assert plug.get_sample_rate(1) == {"MODE": "TARB", "VALUE": 4096000.0}
    assert plug.get_basic_wave(1)["FRQ"] == 1000.0
    assert plug.get_basic_wave(1)["PHSE"] == 90.0  # from the header


def test_apply_setup_tarb_then_dds() -> None:
    plug, _ = _plug()
    base = {"BSWV": {"WVTP": "ARB"}}
    plug.apply_setup({"C1": {**base, "SRATE": {"MODE": "tarb"}}})
    assert plug.get_sample_rate(1)["MODE"] == "TARB"
    plug.apply_setup({"C1": {**base, "SRATE": {"MODE": "DDS"}}})
    assert plug.get_sample_rate(1) == {"MODE": "DDS"}


def test_apply_setup_tarb_with_frq_sets_the_rate() -> None:
    plug, _ = _plug()
    plug.apply_setup({"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 2000.0}, "SRATE": {"MODE": "TARB"}}})
    assert plug.get_sample_rate(1)["VALUE"] == 2000.0 * 16384


def test_apply_setup_second_channel() -> None:
    plug, _ = _plug()
    plug.upload_arb(1, "w", DATA)
    plug.apply_setup({"C2": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"NAME": "w"}}})
    assert plug.get_arb(2) == {"NAME": "w.bin"}


def test_apply_setup_unknown_user_name_fails_verification() -> None:
    plug, _ = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"NAME": "nothing"}}})
    assert info.value.failures == ["C1:ARWV NAME: sent nothing, read StairUp"]


def test_apply_setup_builtin_name_is_not_a_selection() -> None:
    plug, _ = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"NAME": "StairUp"}}})
    assert "C1:ARWV NAME: sent StairUp, read a built-in (StairUp)" in info.value.failures


def test_apply_setup_reject_is_reported() -> None:
    fake = FakeSdgResource(reject=["C1:SRATE VALUE"])
    plug = SiglentSdgPlug(resource=fake)
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "ARB"}, "SRATE": {"MODE": "TARB", "VALUE": 1000000}}})
    assert info.value.failures == ["C1:SRATE VALUE: sent 1000000, read 16384000Sa/s"]


@pytest.mark.parametrize(
    "setup",
    [
        {"C1": {"ARWV": {"INDEX": 1}}},
        {"C1": {"BSWV": {"WVTP": "SINE"}, "SRATE": {"MODE": "DDS"}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"INDEX": 197}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"NAME": "x.bin"}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"INDEX": 1, "NAME": "x"}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "SRATE": {"VALUE": 1000}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "SRATE": {"MODE": "DDS", "VALUE": 1000}}},
        {"C1": {"BSWV": {"WVTP": "ARB", "FRQ": 10.0}, "SRATE": {"MODE": "TARB", "VALUE": 1000}}},
        {"C1": {"BSWV": {"WVTP": "ARB"}, "SRATE": {"MODE": "TARB", "VALUE": 1e9}}},
        {"C2": {"BSWV": {"WVTP": "ARB"}, "ARWV": {"INDEX": 1}}, "C1": {"ARWV": {"INDEX": -1}}},
    ],
)
def test_validator_errors_send_nothing(setup: Setup) -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError):
        plug.apply_setup(setup)
    assert fake.log == []


# -- timeouts and the raw read -------------------------------------------------------------------------------


def test_timeout_is_raised_and_restored_around_upload_and_read() -> None:
    plug, fake = _plug()
    assert fake.timeout == 5000
    plug.upload_arb(1, "w", DATA)
    assert fake.timeout_history == [30000, 5000]
    assert fake.timeout == 5000
    fake.timeout_history.clear()
    plug.read_arb("w")
    assert fake.timeout_history == [30000, 5000]
    fake.timeout_history.clear()
    plug.read_arb(3)
    assert fake.timeout_history == [30000, 5000]


def test_timeout_is_not_lowered() -> None:
    plug, fake = _plug()
    fake.timeout = 60000
    fake.timeout_history.clear()
    plug.upload_arb(1, "w", DATA)
    plug.read_arb("w")
    assert fake.timeout_history == []
    assert fake.timeout == 60000


def test_timeout_is_restored_after_a_failure() -> None:
    fake = FakeSdgResource(raise_on=["STL? USER"])
    plug = SiglentSdgPlug(resource=fake)
    fake.timeout_history.clear()
    with pytest.raises(RuntimeError):
        plug.upload_arb(1, "w", DATA)
    assert fake.timeout_history == [30000, 5000]
    assert fake.timeout == 5000


class _VisaLib:
    def __init__(self, owner: "_PyVisaLike") -> None:
        self.owner = owner

    def read(self, session: int, count: int) -> tuple[bytes, int]:
        self.owner.reads.append((session, count, self.owner.read_termination))
        return self.owner.reply, 0


class _PyVisaLike:
    """A resource shaped like a PyVISA one: ``visalib.read(session, 1)`` returns one message."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.reads: list[tuple[int, int, str | None]] = []
        self.log: list[str] = []
        self.session = 42
        self.visalib = _VisaLib(self)
        self.timeout: Any = 5000
        self.read_termination: str | None = "\n"
        self.write_termination: str | None = "\n"

    def write(self, message: str, /) -> int:
        self.log.append(message)
        return len(message)

    def query(self, message: str, /) -> str:
        self.log.append(message)
        return "Siglent Technologies,SDG2042X,SDG2XFAKE000001,0.00.00.00"

    def close(self) -> None:
        pass


def test_read_message_without_read_message_attribute_uses_one_low_level_read() -> None:
    head = b"WVDT POS, /Local, WVNM, w, LENGTH, 4B, TYPE, 6, WAVEDATA,"
    resource = _PyVisaLike(head + b"\x0a\x0d\x0a\x0d\n")
    plug = SiglentSdgPlug(resource=resource)
    fields, data = plug.read_arb("w")
    assert data == b"\x0a\x0d\x0a\x0d"
    assert fields["length"] == 4
    assert resource.reads == [(42, 1, None)]  # termination disabled during the read
    assert resource.read_termination == "\n"  # and restored
    assert resource.log[-1] == "WVDT? USER,w"  # PG02 §3.32
    assert resource.timeout == 5000


def test_upload_needs_write_raw() -> None:
    class Minimal(_PyVisaLike):
        pass

    resource = Minimal(b"")
    plug = SiglentSdgPlug(resource=resource)
    with pytest.raises(RuntimeError, match="write_raw"):
        plug.upload_arb(1, "w", DATA)


def test_tear_down_is_unchanged() -> None:
    plug, fake = _plug()
    plug.tearDown()
    assert fake.log == ["C1:OUTP OFF", "C2:OUTP OFF"]  # PG02 §3.3
    assert fake.closed
