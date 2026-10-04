"""Tests for the plug. No hardware, no sleeps, no network: every resource is a ``FakeSdgResource`` or a stub,
and ``pyvisa.ResourceManager`` is replaced by a stub in the discovery tests.
"""

import logging
import re
from typing import Any

import openhtf as htf
import pyvisa
import pytest
from openhtf.util import units

from siglent_sdg_openhtf import Identity, ProtocolError, SetupError, SiglentSdgPlug
from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.plug import CONF
from siglent_sdg_openhtf.scpi import Setup

# Example setup of SPEC section 7
EXAMPLE_SETUP: Setup = {
    "C1": {
        "OUTP": {"LOAD": 50, "STATE": True},
        "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
    }
}
EXAMPLE_COMMANDS = [
    "C1:OUTP LOAD,50",  # PG02 §3.3
    "C1:BSWV WVTP,SINE",  # PG02 §3.4
    "C1:BSWV FRQ,1000",
    "C1:BSWV AMP,2",
    "C1:BSWV OFST,0",
    "C1:OUTP ON",  # PG02 §3.3, last
]


def _plug(**fake_kwargs: Any) -> tuple[SiglentSdgPlug, FakeSdgResource]:
    """A plug on a fresh fake; the fake's log is cleared after the constructor's ``*IDN?``."""
    fake = FakeSdgResource(**fake_kwargs)
    plug = SiglentSdgPlug(resource=fake)
    fake.log.clear()
    return plug, fake


class _StubResource:
    """Scripted resource: ``replies`` maps a query to its answer; every write and query is logged."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.log: list[str] = []
        self.closed = False
        self.timeout: Any = None
        self.read_termination: str | None = None
        self.write_termination: str | None = None

    def write(self, message: str, /) -> int:
        self.log.append(message)
        return len(message)

    def query(self, message: str, /) -> str:
        self.log.append(message)
        return self.replies[message]

    def close(self) -> None:
        self.closed = True


IDN = "Siglent Technologies,SDG2042X,SDG2XFAKE000001,0.00.00.00"


# -- construction -----------------------------------------------------------------------------------------


def test_constructor_sends_only_idn_and_sets_identity_and_limits() -> None:
    fake = FakeSdgResource(model="SDG2082X", serial="SN123", firmware="1.2.3")
    plug = SiglentSdgPlug(resource=fake)
    assert fake.log == ["*IDN?"]  # PG02 §3.1.1; in particular no *RST
    assert plug.identity == Identity(
        "Siglent Technologies", "SDG2082X", "SN123", "1.2.3", "Siglent Technologies,SDG2082X,SN123,1.2.3"
    )
    assert plug.limits.model == "SDG2082X"
    assert plug.limits.max_freq_hz["SINE"] == 80e6


def test_unlisted_sdg2_model_gets_sdg2042x_limits() -> None:
    plug, _ = _plug(model="SDG2999X")
    assert plug.identity.model == "SDG2999X"
    assert plug.limits.model == "SDG2042X"


def test_resource_attributes_are_set() -> None:
    fake = FakeSdgResource()
    assert fake.write_termination == "\r\n"
    SiglentSdgPlug(resource=fake)
    assert fake.timeout == 5000
    assert fake.read_termination == "\n"  # PG02 §5.2.1
    assert fake.write_termination == "\n"


@CONF.save_and_restore
def test_timeout_comes_from_conf_at_construction() -> None:
    CONF.load(siglent_sdg_timeout_ms=1234)
    fake = FakeSdgResource()
    SiglentSdgPlug(resource=fake)
    assert fake.timeout == 1234


def test_auto_placeholder_is_true() -> None:
    assert SiglentSdgPlug.auto_placeholder is True


def test_logger_works_outside_openhtf(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        plug, _ = _plug()
        plug.query("*OPC?")  # PG02 §3.1.2
    assert isinstance(plug.logger, logging.Logger)
    messages = [r.getMessage() for r in caplog.records]
    assert "-> *OPC?" in messages
    assert "<- 1" in messages


@pytest.mark.parametrize("reply", ["Siglent Technologies,SDG2042X,SN", ""])
def test_short_idn_reply_is_a_protocol_error_and_closes(reply: str) -> None:
    stub = _StubResource({"*IDN?": reply})
    with pytest.raises(ProtocolError, match="IDN"):
        SiglentSdgPlug(resource=stub)
    assert stub.closed


def test_unsupported_model_fails_construction_and_closes() -> None:
    stub = _StubResource({"*IDN?": "Rigol Technologies,DG1022Z,SN,1.0"})
    with pytest.raises(ValueError, match="unsupported"):
        SiglentSdgPlug(resource=stub)
    assert stub.closed


# -- reads ------------------------------------------------------------------------------------------------


def test_get_basic_wave_typed_values() -> None:
    plug, fake = _plug()
    wave = plug.get_basic_wave(1)
    assert fake.log == ["C1:BSWV?"]  # PG02 §3.4
    assert wave["FRQ"] == 100.0
    assert wave["WVTP"] == "SINE"
    assert wave["AMP"] == 2.0
    assert wave["LLEV"] == -1.0
    assert list(wave)[:3] == ["WVTP", "FRQ", "PERI"]


def test_get_basic_wave_channel_two_and_bad_channel() -> None:
    plug, fake = _plug()
    plug.get_basic_wave(2)
    assert fake.log == ["C2:BSWV?"]
    with pytest.raises(ValueError, match="channel"):
        plug.get_basic_wave(3)
    assert fake.log == ["C2:BSWV?"]


def test_get_output() -> None:
    plug, fake = _plug()
    out = plug.get_output(1)
    assert fake.log == ["C1:OUTP?"]  # PG02 §3.3
    assert out == {"STATE": "OFF", "LOAD": "HZ", "PLRT": "NOR"}


def test_get_output_numeric_load() -> None:
    plug, fake = _plug()
    fake.write("C1:OUTP ON")
    fake.write("C1:OUTP LOAD,50")
    assert plug.get_output(1) == {"STATE": "ON", "LOAD": 50.0, "PLRT": "NOR"}


def test_opc() -> None:
    plug, fake = _plug()
    assert plug.opc() is True
    assert fake.log == ["*OPC?"]
    assert _plug_with({"*OPC?": "0"}).opc() is False


def _plug_with(replies: dict[str, str]) -> SiglentSdgPlug:
    return SiglentSdgPlug(resource=_StubResource({"*IDN?": IDN, **replies}))


def test_reset_log_and_effect() -> None:
    plug, fake = _plug()
    fake.write("C1:BSWV FRQ,5000")
    fake.log.clear()
    plug.reset()
    assert fake.log == ["*RST", "*OPC?"]  # PG02 §3.1.3, §3.1.2
    assert plug.get_basic_wave(1)["FRQ"] == 100.0


def test_reset_with_bad_opc_is_a_protocol_error() -> None:
    plug = _plug_with({"*OPC?": "0"})
    with pytest.raises(ProtocolError, match="OPC"):
        plug.reset()


def test_header_mismatch_is_a_protocol_error() -> None:
    plug = _plug_with({"C1:BSWV?": "C2:BSWV WVTP,SINE,FRQ,100HZ"})
    with pytest.raises(ProtocolError, match="C2:BSWV"):
        plug.get_basic_wave(1)
    with pytest.raises(ProtocolError):
        _plug_with({"C1:OUTP?": "C2:OUTP ON,LOAD,HZ,PLRT,NOR"}).get_output(1)


def test_header_mismatch_during_apply_setup_verification() -> None:
    plug = _plug_with({"C1:BSWV?": "C2:BSWV WVTP,SINE,FRQ,100HZ"})
    with pytest.raises(ProtocolError):
        plug.apply_setup({"C1": {"BSWV": {"FRQ": 1000}}})


# -- raw setters ------------------------------------------------------------------------------------------


def test_set_output_command_order() -> None:
    plug, fake = _plug()
    plug.set_output(1, {"STATE": True, "PLRT": "INVT", "LOAD": 50})
    assert fake.log == ["C1:OUTP LOAD,50", "C1:OUTP PLRT,INVT", "C1:OUTP ON"]  # PG02 §3.3
    fake.log.clear()
    plug.set_output(2, {"LOAD": "HZ", "STATE": False})
    assert fake.log == ["C2:OUTP OFF", "C2:OUTP LOAD,HZ"]


def test_set_basic_wave_one_command_per_key_in_order() -> None:
    plug, fake = _plug()
    plug.set_basic_wave(2, {"OFST": 0.5, "AMP": 3, "FRQ": 2000, "WVTP": "RAMP", "SYM": 25})
    assert fake.log == [  # PG02 §3.4
        "C2:BSWV WVTP,RAMP",
        "C2:BSWV FRQ,2000",
        "C2:BSWV AMP,3",
        "C2:BSWV OFST,0.5",
        "C2:BSWV SYM,25",
    ]


def test_setters_validate_before_sending() -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError, match="SYSTEM"):
        plug.set_basic_wave(1, {"SYSTEM": 1})
    with pytest.raises(ValueError, match="DUTY"):
        plug.set_basic_wave(1, {"WVTP": "SINE", "DUTY": 20})
    with pytest.raises(ValueError, match="PLRT"):
        plug.set_output(1, {"PLRT": "X"})
    assert fake.log == []


# -- apply_setup ------------------------------------------------------------------------------------------


def test_apply_setup_happy_path_exact_log() -> None:
    plug, fake = _plug()
    plug.apply_setup(EXAMPLE_SETUP)
    assert fake.log == [*EXAMPLE_COMMANDS, "C1:BSWV?", "C1:OUTP?"]
    assert plug.get_output(1) == {"STATE": "ON", "LOAD": 50.0, "PLRT": "NOR"}
    wave = plug.get_basic_wave(1)
    assert (wave["FRQ"], wave["AMP"], wave["OFST"]) == (1000.0, 2.0, 0.0)


def test_apply_setup_both_channels() -> None:
    plug, fake = _plug()
    setup: Setup = {
        "C1": {"BSWV": {"WVTP": "SQUARE", "FRQ": 500, "DUTY": 30}, "OUTP": {"STATE": True}},
        "C2": {"BSWV": {"WVTP": "RAMP", "AMP": 1.5, "SYM": 70}},
    }
    plug.apply_setup(setup)
    assert fake.log == [
        "C1:BSWV WVTP,SQUARE",
        "C1:BSWV FRQ,500",
        "C1:BSWV DUTY,30",
        "C1:OUTP ON",
        "C2:BSWV WVTP,RAMP",
        "C2:BSWV AMP,1.5",
        "C2:BSWV SYM,70",
        "C1:BSWV?",
        "C1:OUTP?",
        "C2:BSWV?",
    ]


def test_apply_setup_load_before_amp_and_state_on_last() -> None:
    plug, fake = _plug()
    plug.apply_setup(
        {"C1": {"BSWV": {"AMP": 2, "FRQ": 1000}, "OUTP": {"STATE": True, "PLRT": "NOR", "LOAD": 50}}}
    )
    writes = [c for c in fake.log if "?" not in c]
    assert writes == [
        "C1:OUTP LOAD,50",
        "C1:OUTP PLRT,NOR",
        "C1:BSWV FRQ,1000",
        "C1:BSWV AMP,2",
        "C1:OUTP ON",
    ]


def test_apply_setup_state_off_first() -> None:
    plug, fake = _plug()
    fake.write("C1:OUTP ON")
    fake.log.clear()
    plug.apply_setup({"C1": {"BSWV": {"FRQ": 1000, "AMP": 3}, "OUTP": {"STATE": False, "LOAD": 50}}})
    writes = [c for c in fake.log if "?" not in c]
    assert writes == ["C1:OUTP OFF", "C1:OUTP LOAD,50", "C1:BSWV FRQ,1000", "C1:BSWV AMP,3"]
    assert plug.get_output(1)["STATE"] == "OFF"


def test_apply_setup_queries_only_touched_groups() -> None:
    plug, fake = _plug()
    plug.apply_setup({"C2": {"OUTP": {"STATE": False}}})
    assert fake.log == ["C2:OUTP OFF", "C2:OUTP?"]
    fake.log.clear()
    plug.apply_setup({"C1": {"BSWV": {"FRQ": 10}}})
    assert fake.log == ["C1:BSWV FRQ,10", "C1:BSWV?"]


def test_apply_setup_empty_setup_sends_nothing() -> None:
    plug, fake = _plug()
    plug.apply_setup({})
    plug.apply_setup({"C1": {}})
    plug.apply_setup({"C1": {"BSWV": {}}})
    assert fake.log == []


def test_two_rejected_keys_give_one_setup_error_naming_both() -> None:
    plug, fake = _plug(reject=["C1:BSWV FRQ", "C1:BSWV AMP"])
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "SINE", "FRQ": 1000, "AMP": 5, "OFST": 0.5}}})
    assert info.value.failures == [
        "C1:BSWV FRQ: sent 1000, read 100HZ",
        "C1:BSWV AMP: sent 5, read 2V",
    ]
    assert str(info.value) == "\n".join(info.value.failures)
    assert fake.log.count("C1:BSWV?") == 1  # one query, all mismatches at once


def test_mismatches_on_bswv_and_outp_are_collected_together() -> None:
    plug, _ = _plug(reject=["C1:BSWV FRQ", "C1:OUTP ON"])
    with pytest.raises(SetupError) as info:
        plug.apply_setup(EXAMPLE_SETUP)
    assert info.value.failures == [
        "C1:BSWV FRQ: sent 1000, read 100HZ",
        "C1:OUTP STATE: sent ON, read OFF",
    ]


def test_key_not_echoed_is_reported() -> None:
    # The fake echoes AMPVRMS only as AMP (hypothesis until hardware session 1), so it is "not echoed".
    plug, _ = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "SINE", "AMPVRMS": 1.0}}})
    assert info.value.failures == ["C1:BSWV AMPVRMS: not echoed by the generator"]


def test_invalid_setup_raises_value_error_and_sends_nothing() -> None:
    plug, fake = _plug()
    bad_setups: list[dict[str, Any]] = [
        {"C3": {"BSWV": {"FRQ": 1}}},
        {"C1": {"XXXX": {"FRQ": 1}}},
        {"C1": {"BSWV": {"LENGTH": 1}}},
        {"C1": {"BSWV": {"WVTP": "SINE", "DUTY": 10}}},
        {"C1": {"OUTP": {"STATE": True, "LOAD": 10}}},
        # the first channel is valid, the second is not: still nothing may be sent
        {"C1": {"BSWV": {"FRQ": 1000}}, "C2": {"OUTP": {"PLRT": "X"}}},
    ]
    for setup in bad_setups:
        with pytest.raises(ValueError):
            plug.apply_setup(setup)
    assert fake.log == []


def test_check_limits_on_rejects_50_mhz_sine_before_sending() -> None:
    plug, fake = _plug()
    with pytest.raises(ValueError, match="exceeds"):
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "SINE", "FRQ": 50e6}}})
    assert fake.log == []


@CONF.save_and_restore
def test_check_limits_off_lets_50_mhz_through_and_read_back_catches_it() -> None:
    CONF.load(siglent_sdg_check_limits=False)
    plug, fake = _plug()
    with pytest.raises(SetupError) as info:
        plug.apply_setup({"C1": {"BSWV": {"WVTP": "SINE", "FRQ": 50e6}}})
    assert fake.log[0:2] == ["C1:BSWV WVTP,SINE", "C1:BSWV FRQ,50000000"]
    assert info.value.failures == ["C1:BSWV FRQ: sent 50000000, read 100HZ"]


def test_verify_false_sends_no_queries() -> None:
    plug, fake = _plug()
    plug.apply_setup(EXAMPLE_SETUP, verify=False)
    assert fake.log == EXAMPLE_COMMANDS
    assert not any("?" in cmd for cmd in fake.log)


def test_verify_false_does_not_notice_a_rejected_key() -> None:
    plug, _ = _plug(reject=["C1:BSWV FRQ"])
    plug.apply_setup(EXAMPLE_SETUP, verify=False)


def test_apply_setup_logs_at_info(caplog: pytest.LogCaptureFixture) -> None:
    plug, _ = _plug()
    with caplog.at_level(logging.INFO):
        plug.apply_setup(EXAMPLE_SETUP)
    assert any(
        r.levelno == logging.INFO and "6 commands" in r.getMessage() and "C1" in r.getMessage()
        for r in caplog.records
    )


# -- teardown ---------------------------------------------------------------------------------------------


def test_teardown_turns_both_outputs_off_and_closes() -> None:
    plug, fake = _plug()
    plug.apply_setup(EXAMPLE_SETUP)
    fake.log.clear()
    plug.tearDown()
    assert fake.log == ["C1:OUTP OFF", "C2:OUTP OFF"]  # PG02 §3.3
    assert fake.closed
    assert fake._channels["C1"].output_on is False  # noqa: SLF001  (white-box check of the oracle)


def test_teardown_survives_a_failing_write(caplog: pytest.LogCaptureFixture) -> None:
    plug, fake = _plug(raise_on=["C1:OUTP OFF"])
    with caplog.at_level(logging.WARNING):
        plug.tearDown()
    assert fake.log == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed
    assert any(r.levelno == logging.WARNING and "channel 1" in r.getMessage() for r in caplog.records)


def test_teardown_survives_a_failing_close(caplog: pytest.LogCaptureFixture) -> None:
    plug, fake = _plug()

    def broken_close() -> None:
        raise OSError("link down")

    fake.close = broken_close  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING):
        plug.tearDown()
    assert fake.log == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert any(r.levelno == logging.WARNING and "closing" in r.getMessage() for r in caplog.records)


def test_teardown_twice_is_harmless() -> None:
    plug, fake = _plug()
    plug.tearDown()
    log = list(fake.log)
    plug.tearDown()
    assert fake.log == log == ["C1:OUTP OFF", "C2:OUTP OFF"]


def test_teardown_without_any_other_traffic_never_restores_settings() -> None:
    plug, fake = _plug()
    plug.apply_setup({"C1": {"BSWV": {"FRQ": 1234}}})
    fake.log.clear()
    plug.tearDown()
    assert fake.log == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.query("C1:BSWV?").split(",")[3] == "1234HZ"


# -- only PG02 SCPI is ever sent --------------------------------------------------------------------------

ALLOWED_SCPI = [
    r"\*IDN\?",  # PG02 §3.1.1
    r"\*OPC\?",  # PG02 §3.1.2
    r"\*RST",  # PG02 §3.1.3
    r"C[12]:OUTP (ON|OFF|LOAD,[0-9.]+|LOAD,HZ|PLRT,(NOR|INVT))",  # PG02 §3.3
    r"C[12]:OUTP\?",  # PG02 §3.3
    r"C[12]:BSWV [A-Z_]+,[A-Za-z0-9.+-]+",  # PG02 §3.4
    r"C[12]:BSWV\?",  # PG02 §3.4
]


def test_every_string_sent_is_a_pg02_command() -> None:
    plug, fake = _plug()
    plug.reset()
    plug.apply_setup(EXAMPLE_SETUP)
    plug.apply_setup({"C2": {"BSWV": {"WVTP": "PULSE", "DUTY": 20, "WIDTH": 1e-6, "FRQ": 1e3}}})
    plug.set_output(2, {"STATE": True, "LOAD": "HZ", "PLRT": "INVT"})
    plug.set_basic_wave(1, {"WVTP": "NOISE", "STDEV": 0.1, "BANDSTATE": True})
    plug.get_basic_wave(2)
    plug.get_output(2)
    plug.opc()
    plug.tearDown()
    assert len(fake.log) > 20
    for cmd in fake.log:
        assert any(re.fullmatch(pattern, cmd) for pattern in ALLOWED_SCPI), cmd


# -- resource discovery (pyvisa.ResourceManager is replaced by a stub; nothing real is opened) ------------


class _StubRM:
    def __init__(self, resources: list[str], instrument: FakeSdgResource | None = None) -> None:
        self.resources = resources
        self.instrument = instrument or FakeSdgResource()
        self.list_queries: list[str] = []
        self.opened: list[str] = []
        self.closed = False

    def list_resources(self, query: str = "?*::INSTR") -> tuple[str, ...]:
        self.list_queries.append(query)
        return tuple(self.resources)

    def open_resource(self, name: str) -> FakeSdgResource:
        self.opened.append(name)
        return self.instrument

    def close(self) -> None:
        self.closed = True


def _install_rm(monkeypatch: pytest.MonkeyPatch, rm: _StubRM) -> list[tuple[Any, ...]]:
    """Replace ``pyvisa.ResourceManager``; returns the list of argument tuples it was called with."""
    calls: list[tuple[Any, ...]] = []

    def factory(*args: Any) -> _StubRM:
        calls.append(args)
        return rm

    monkeypatch.setattr(pyvisa, "ResourceManager", factory)
    return calls


@pytest.mark.parametrize(
    "usb_name",
    [
        "USB0::0xF4EC::0x1011::SDG2XCAF000001::INSTR",  # hexadecimal vendor id
        "USB0::62700::4113::SDG2XCAF000001::INSTR",  # the same vendor id in decimal
        "USB0::0xf4ec::0x1011::SDG2XCAF000001::INSTR",  # lower-case hex
    ],
)
@CONF.save_and_restore
def test_usb_discovery_matches_vendor_in_hex_and_decimal(monkeypatch: pytest.MonkeyPatch, usb_name: str) -> None:
    CONF.load(siglent_sdg_resource="")
    rm = _StubRM(["USB0::0x1AB1::0x0588::DS1ZA000000::INSTR", "ASRL1::INSTR", "USB0::bad::x::INSTR", usb_name])
    calls = _install_rm(monkeypatch, rm)
    plug = SiglentSdgPlug()
    assert calls == [("@py",)]
    assert rm.list_queries == ["USB?*::INSTR"]
    assert rm.opened == [usb_name]
    assert plug.identity.model == "SDG2042X"
    plug.tearDown()


@CONF.save_and_restore
def test_usb_discovery_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    CONF.load(siglent_sdg_resource="")
    rm = _StubRM(["USB0::0x1AB1::0x0588::DS1ZA000000::INSTR"])
    _install_rm(monkeypatch, rm)
    with pytest.raises(RuntimeError, match="F4EC"):
        SiglentSdgPlug()
    assert rm.opened == []
    assert rm.closed  # the ResourceManager opened for the search is not leaked


@CONF.save_and_restore
def test_bare_ip_is_expanded_to_a_vxi11_resource_name(monkeypatch: pytest.MonkeyPatch) -> None:
    CONF.load(siglent_sdg_resource="192.0.2.10")
    rm = _StubRM([])
    _install_rm(monkeypatch, rm)
    plug = SiglentSdgPlug()
    assert rm.opened == ["TCPIP0::192.0.2.10::inst0::INSTR"]
    assert rm.list_queries == []
    plug.tearDown()


@CONF.save_and_restore
def test_full_resource_names_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "TCPIP0::192.0.2.10::5025::SOCKET"  # PG02 §1.2.4
    CONF.load(siglent_sdg_resource=name)
    rm = _StubRM([])
    _install_rm(monkeypatch, rm)
    plug = SiglentSdgPlug()
    assert rm.opened == [name]
    assert plug.logger is not None
    plug.tearDown()


@CONF.save_and_restore
def test_opened_resource_is_configured_and_everything_closed_by_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    CONF.load(siglent_sdg_resource="192.0.2.10", siglent_sdg_timeout_ms=7000)
    rm = _StubRM([])
    _install_rm(monkeypatch, rm)
    plug = SiglentSdgPlug()
    fake = rm.instrument
    assert (fake.timeout, fake.read_termination, fake.write_termination) == (7000, "\n", "\n")
    assert not fake.closed and not rm.closed
    plug.tearDown()
    assert fake.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed and rm.closed
    plug.tearDown()  # idempotent


def test_injected_resource_does_not_touch_pyvisa(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any) -> None:
        raise AssertionError("pyvisa.ResourceManager must not be used for an injected resource")

    monkeypatch.setattr(pyvisa, "ResourceManager", boom)
    plug, fake = _plug()
    plug.tearDown()
    assert fake.closed


@CONF.save_and_restore
def test_failed_construction_closes_resource_and_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    CONF.load(siglent_sdg_resource="192.0.2.10")
    rm = _StubRM([], instrument=FakeSdgResource(raise_on=["*IDN?"]))
    _install_rm(monkeypatch, rm)
    with pytest.raises(RuntimeError, match="simulated"):
        SiglentSdgPlug()
    assert rm.instrument.closed and rm.closed


# -- OpenHTF ----------------------------------------------------------------------------------------------


def test_openhtf_integration() -> None:
    fake = FakeSdgResource()

    class InjectedPlug(SiglentSdgPlug):
        def __init__(self) -> None:
            super().__init__(resource=fake)

    @htf.plug(generator=InjectedPlug)
    @htf.measures(
        htf.Measurement("frequency_hz").with_units(units.HERTZ).in_range(999, 1001),
        htf.Measurement("amplitude_vpp").in_range(1.99, 2.01),
        htf.Measurement("output_state").equals("ON"),
    )
    def generate_sine(test: Any, generator: InjectedPlug) -> None:
        generator.apply_setup(EXAMPLE_SETUP)
        wave = generator.get_basic_wave(1)
        test.measurements.frequency_hz = wave["FRQ"]
        test.measurements.amplitude_vpp = wave["AMP"]
        test.measurements.output_state = generator.get_output(1)["STATE"]

    outcome = htf.Test(generate_sine).execute(test_start=lambda: "dut1")
    assert outcome is True
    assert fake.log[0] == "*IDN?"
    assert fake.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]  # OpenHTF called tearDown
    assert fake.closed


def test_openhtf_failing_measurement_still_tears_down() -> None:
    fake = FakeSdgResource(reject=["C1:BSWV AMP"])

    class InjectedPlug(SiglentSdgPlug):
        def __init__(self) -> None:
            super().__init__(resource=fake)

    @htf.plug(generator=InjectedPlug)
    def apply(test: Any, generator: InjectedPlug) -> None:
        generator.apply_setup(EXAMPLE_SETUP)  # AMP is rejected by the fake: SetupError fails the phase

    assert htf.Test(apply).execute(test_start=lambda: "dut1") is False
    assert fake.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed


