"""An in-memory stand-in for a PyVISA resource connected to a Siglent SDG2000X generator.

The fake is an independent oracle: it imports nothing from ``pyvisa``, ``openhtf`` or any other module of
this package (a shared parser would hide parser bugs), and it is the only instrument coder agents may talk
to. It emulates the v1 command subset: ``*IDN?``, ``*OPC``/``*OPC?``, ``*RST``, ``<ch>:OUTP`` and
``<ch>:BSWV`` with their queries.

The only source of SCPI is the official Siglent guide, ``docs/PG02-E05C.txt`` ("PG02"). Section numbers in
the comments below refer to it, and the SDG2000X column of its availability tables is the one that counts.

Hardware session 1 (2026-10-05, SDG2042X, firmware 2.01.01.23R7, VXI-11) measured most of the behaviour
below; rules that were measured carry ``# measured, hardware session 1 (README)``. What was not measured
(SDG2082X/SDG2122X limits, unparsable values, a ``WVTP`` of another family, ``FRQ <= 0``, unit suffixes on
write, the long header spellings, a ``PERI`` of 0 or less) is still a hypothesis and
is marked ``# hypothesis until hardware session 1``. The real generator has no error queue and clamps
out-of-range values without any sign; only commands that PG02 does not define raise ``ValueError``.
"""

import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

__all__ = ["FakeSdgResource"]

# --------------------------------------------------------------------------------------------------------
# Model limits. Copied from the SDG2000X datasheet summary (NOT from PG02, which only says "refer to the
# datasheet"); deliberately not imported from models.py. The SDG2042X figures are measured; the SINE limits
# of the other two models are not.
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Limits:
    max_freq_hz: dict[str, float]  # per WVTP; a missing entry means "no frequency check" for that type
    max_amp_vpp_hiz: float
    max_amp_vpp_50: float
    max_offset_v_hiz: float


_BASE_FREQ: Final = {"SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6, "ARB": 20e6}  # measured, hardware session 1 (README)
_LIMITS: Final[dict[str, _Limits]] = {
    "SDG2042X": _Limits({"SINE": 40e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),  # measured, hardware session 1 (README)
    "SDG2082X": _Limits({"SINE": 80e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),  # hypothesis until hardware session 1 (SINE limit)
    "SDG2122X": _Limits({"SINE": 120e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),  # hypothesis until hardware session 1 (SINE limit)
}
_FALLBACK_LIMITS: Final = "SDG2042X"  # unknown model: the most restrictive entry (mirrors models.py)

# Measured constants, hardware session 1 (README): the generator's own figures, not derived from PG02.
_AMP_MIN: Final = 0.002  # Vpp, at HiZ and at 50 ohm
_LEVEL_GAP: Final = 0.002  # smallest HLEV - LLEV
_LOAD_MIN: Final = 50  # PG02 §3.3 SDG2000X column; a smaller value is clamped to it
_LOAD_MAX: Final = 100000
_MIN_EDGE_S: Final = 8.4e-9  # PULSE RISE/FALL minimum
_MIN_PULSE_S: Final = 16.3e-9  # PULSE width and SQUARE duty margin
_PHSE_MAX: Final = 360.0
_HF_FRQ_HZ: Final = 20e6  # above this frequency the levels are clamped to +-5 V (20000001 Hz: 10 Vpp at HiZ)
_HF_LEVEL_V: Final = 5.0
_BANDWIDTH_MIN: Final = 20e6  # NOISE BANDWIDTH is clamped to 20 MHz .. 120 MHz
_BANDWIDTH_MAX: Final = 120e6
_STDEV_PER_AMP: Final = 0.0575  # NOISE STDEV = 0.0575 * AMP
# AMPVRMS = AMP * factor, per WVTP (SINE is 0.3535, not 0.35355; RAMP is 1/3.464, not 1/sqrt(12))
_VRMS_FACTOR: Final = {"SINE": 0.3535, "SQUARE": 0.5, "PULSE": 0.5, "RAMP": 1 / 3.464}

# --------------------------------------------------------------------------------------------------------
# Grammar tables
# --------------------------------------------------------------------------------------------------------

# PG02 §3.4 WVTP: {SINE, SQUARE, RAMP, PULSE, NOISE, ARB, DC, PRBS, IQ}. PRBS and IQ are "no" for SDG2000X
# (their parameters are unavailable in the §3.4 availability table), so a WVTP of PRBS/IQ is ignored.
_WAVE_TYPES: Final = frozenset({"SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "ARB", "DC"})

# PG02 §3.4 parameter table, SDG2000X column, plus MAX_OUTPUT_AMP from §3.3.
_BSWV_KEYS: Final = frozenset(
    {
        "WVTP", "FRQ", "PERI", "AMP", "AMPVRMS", "AMPDBM", "OFST", "SYM", "DUTY", "PHSE", "STDEV", "MEAN",
        "WIDTH", "RISE", "FALL", "DLY", "HLEV", "LLEV", "BANDSTATE", "BANDWIDTH", "MAX_OUTPUT_AMP",
    }
)  # fmt: skip
# PG02 §3.4 parameters that exist for other SDG families only (SDG2000X column "no", or SDG6000X/7000A
# only): defined by PG02, so not "undefined", but silently ignored on this family.
# hypothesis until hardware session 1 (the real device may answer differently)
_BSWV_KEYS_OTHER_FAMILIES: Final = frozenset(
    {"COM_OFST", "LENGTH", "EDGE", "FORMAT", "DIFFSTATE", "BITRATE", "LOGICLEVEL"}
)

_ALL_TYPES: Final = _WAVE_TYPES
# Which WVTP a key is valid for: the "Description" column of PG02 §3.4 ("Not valid when WVTP is NOISE or DC",
# "Only settable when WVTP is RAMP", ...). MAX_OUTPUT_AMP and WVTP carry no restriction in PG02; DLY is PULSE only (measured).
_KEY_VALID_FOR: Final[dict[str, frozenset[str]]] = {
    "WVTP": _ALL_TYPES,
    "FRQ": _ALL_TYPES - {"NOISE", "DC"},
    "PERI": _ALL_TYPES - {"NOISE", "DC"},
    "AMP": _ALL_TYPES - {"NOISE", "DC"},
    "AMPVRMS": _ALL_TYPES - {"NOISE", "DC"},
    "AMPDBM": _ALL_TYPES - {"NOISE", "DC"},
    "HLEV": _ALL_TYPES - {"NOISE", "DC"},
    "LLEV": _ALL_TYPES - {"NOISE", "DC"},
    "OFST": _ALL_TYPES - {"NOISE"},
    "PHSE": _ALL_TYPES - {"NOISE", "PULSE", "DC"},
    "SYM": frozenset({"RAMP"}),
    "DUTY": frozenset({"SQUARE", "PULSE"}),
    "WIDTH": frozenset({"PULSE"}),
    "RISE": frozenset({"PULSE"}),
    "FALL": frozenset({"PULSE"}),
    "STDEV": frozenset({"NOISE"}),
    "MEAN": frozenset({"NOISE"}),
    "BANDSTATE": frozenset({"NOISE"}),
    "BANDWIDTH": frozenset({"NOISE"}),
    "DLY": frozenset({"PULSE"}),  # measured, hardware session 1 (README) (PULSE only)
    "MAX_OUTPUT_AMP": _ALL_TYPES,
}

# PG02 §3.3 <channel>:={C1, C2}; SDG2042X has two channels.
_CHANNELS: Final = ("C1", "C2")
# Header spellings. PG02 §3.3 writes the long form "OUTPut" (short OUTP, long OUTPUT) and §3.4 writes
# "BaSic_WaVe" (short BSWV, long BASIC_WAVE); both spellings are accepted, replies always use the short one
# (PG02 §3.4 sample reply). Case-insensitive.   # hypothesis until hardware session 1
_OUTP_HEADERS: Final = frozenset({"OUTP", "OUTPUT"})
_BSWV_HEADERS: Final = frozenset({"BSWV", "BASIC_WAVE"})

_WRITE_RE: Final = re.compile(r"^(C[12]):([A-Za-z_]+)(?:\s+(.*))?$", re.IGNORECASE)
_QUERY_RE: Final = re.compile(r"^(C[12]):([A-Za-z_]+)\?$", re.IGNORECASE)
_NUMBER_RE: Final = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _parse_number(text: str) -> float | None:
    """A plain decimal or exponent number (PG02 §3.4: ``100E6`` is accepted on write); no unit suffix.

    Unit suffixes on write are not documented by PG02, so they are not accepted.   # hypothesis until hardware session 1
    """
    if not _NUMBER_RE.match(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _g(x: float, digits: int = 6) -> str:
    """C ``%g`` as the generator prints numbers: 6 significant digits, lower-case exponent. # measured, hardware session 1 (README)"""
    return format(x + 0.0, f".{digits}g")  # + 0.0: no "-0"


def _snap(x: float, decimals: int = 12) -> float:
    """Round to 12 significant digits and ``decimals`` decimals. This is the one place where floating-point
    drift (rescaling by k_new/k_old, Vrms conversions, level arithmetic) is removed, so that a
    HiZ -> 50 -> HiZ round trip reads ``2V`` again and cancellations give 0, not 1e-17. Stored values use
    12 decimals; the computed dBm figure uses 9 (``AMPDBM,0`` must not read ``-5e-12dBm``)."""
    return round(float(format(x, ".12g")), decimals)


def _k(load: int | None) -> float:
    """Load factor: 1 at HiZ, LOAD/(LOAD+50) at a numeric load. # measured, hardware session 1 (README)"""
    return 1.0 if load is None else load / (load + 50)


@dataclass
class _Channel:
    """State of one channel. Field defaults are the ``*RST`` state, measured in hardware session 1 (README).

    Parameters are views of shared state (NOISE ``STDEV``/``MEAN`` are ``AMP``/``OFST``); type-specific
    values survive a ``WVTP`` change; PULSE stores its width in seconds (its duty is derived).
    """

    output_on: bool = False
    load: int | None = None  # None = HiZ ("HZ" in PG02 §3.3)
    polarity: str = "NOR"
    wvtp: str = "SINE"
    frq: float = 1000.0
    amp: float = 4.0
    ofst: float = 0.0
    dc_ofst: float = 0.0  # DC has its own offset
    phse: float = 0.0
    duty: float = 50.0  # SQUARE duty
    sym: float = 50.0
    width: float = 0.0002  # PULSE width in seconds (DUTY 20 at 1 kHz)
    rise: float = 8.4e-9
    fall: float = 8.4e-9
    dly: float = 0.0
    bandstate: str = "OFF"
    bandwidth: float = 120e6


class FakeSdgResource:
    """Duck-typed PyVISA message-based resource emulating a Siglent SDG2000X (default SDG2042X)."""

    def __init__(
        self,
        *,
        model: str = "SDG2042X",
        serial: str = "SDG2XFAKE000001",
        firmware: str = "0.00.00.00",
        reject: Collection[str] = (),
        raise_on: Collection[str] = (),
    ) -> None:
        self.model = model
        self.serial = serial
        self.firmware = firmware
        # Attributes the plug sets on a real resource.
        self.timeout: float | None = 2000.0
        self.read_termination: str | None = "\n"
        self.write_termination: str | None = "\n"  # PG02 §5.2.1
        self.chunk_size: int = 20480
        self.log: list[str] = []  # every string given to write() and query(), in order
        self.closed: bool = False
        self._reject = tuple(reject)
        self._raise_on = tuple(raise_on)
        self._limits = _LIMITS.get(model.strip().upper(), _LIMITS[_FALLBACK_LIMITS])
        self._channels: dict[str, _Channel] = {name: _Channel() for name in _CHANNELS}

    # ----------------------------------------------------------------------------------------------------
    # PyVISA-like surface
    # ----------------------------------------------------------------------------------------------------

    def write(self, message: str, /) -> int:
        """Apply a command. ``raise_on`` prefixes raise ``RuntimeError``; ``reject`` prefixes are logged
        and then dropped without being applied; undefined commands raise ``ValueError``."""
        self.log.append(message)
        self._maybe_raise(message)
        if any(message.startswith(prefix) for prefix in self._reject):
            return len(message)
        self._execute(message.strip())
        return len(message)

    def query(self, message: str, /, delay: float | None = None) -> str:
        """Answer a query. ``delay`` is accepted for PyVISA compatibility and ignored (never sleeps)."""
        del delay
        self.log.append(message)
        self._maybe_raise(message)
        return self._answer(message.strip())

    def close(self) -> None:
        self.closed = True

    def _maybe_raise(self, message: str) -> None:
        if any(message.startswith(prefix) for prefix in self._raise_on):
            raise RuntimeError(f"simulated I/O failure on {message!r}")

    # ----------------------------------------------------------------------------------------------------
    # Writes
    # ----------------------------------------------------------------------------------------------------

    def _execute(self, cmd: str) -> None:
        upper = cmd.upper()
        if upper == "*RST":  # PG02 §3.1.3: recall the default setup
            self._channels = {name: _Channel() for name in _CHANNELS}
            return
        if upper == "*OPC":  # PG02 §3.1.2: sets the OPC bit, "no other effect"
            return
        match = _WRITE_RE.match(cmd)
        if match is not None:
            channel = self._channels[match.group(1).upper()]
            header = match.group(2).upper()
            args = match.group(3)
            if args is not None and args.strip():
                tokens = [token.strip() for token in args.split(",")]
                if header in _OUTP_HEADERS:  # PG02 §3.3
                    self._write_outp(channel, tokens, cmd)
                    return
                if header in _BSWV_HEADERS:  # PG02 §3.4
                    self._write_bswv(channel, tokens, cmd)
                    return
        raise ValueError(f"undefined command: {cmd}")

    def _write_outp(self, channel: _Channel, tokens: list[str], cmd: str) -> None:
        # PG02 §3.3: <channel>:OUTPut ON|OFF,LOAD,<load>,PLRT,<polarity>; each part may be sent on its own
        # (examples C1:OUTP ON, C1:OUTP LOAD,50, C1:OUTP PLRT,NOR).
        state: str | None = None
        if tokens[0].upper() in ("ON", "OFF"):
            state = tokens.pop(0).upper()
        pairs = _pairs(tokens)
        for key, _ in pairs:  # grammar check first: an undefined command applies nothing
            if key not in ("LOAD", "PLRT"):
                raise ValueError(f"undefined command: {cmd}")
        if state is not None:
            channel.output_on = state == "ON"
        for key, value in pairs:
            if value is None:
                continue  # known key without a value: silently ignored
            if key == "LOAD":
                self._set_load(channel, value)
            elif value.upper() in ("NOR", "INVT"):  # PLRT, PG02 §3.3 <polarity>:={NOR, INVT}
                channel.polarity = value.upper()

    def _set_load(self, channel: _Channel, text: str) -> None:
        # PG02 §3.3 SDG2000X column: LOAD is 50~100000 or HiZ (written "HZ" in the examples). Out of range is
        # clamped, a fraction is cut off.   # measured, hardware session 1 (README)
        if text.upper() == "HZ":
            new: int | None = None
        else:
            value = _parse_number(text)
            if value is None:
                return  # hypothesis until hardware session 1 (unparsable value ignored)
            new = int(min(max(value, _LOAD_MIN), _LOAD_MAX))
        old = channel.load
        channel.load = new
        # A load change rescales every displayed level by k_new / k_old.   # measured, hardware session 1 (README)
        factor = _k(new) / _k(old)
        channel.amp = _snap(channel.amp * factor)
        channel.ofst = _snap(channel.ofst * factor)
        channel.dc_ofst = _snap(channel.dc_ofst * factor)

    def _write_bswv(self, channel: _Channel, tokens: list[str], cmd: str) -> None:
        # PG02 §3.4: <channel>:BSWV <parameter>,<value>. Several pairs in one command are applied left to
        # right.   # measured, hardware session 1 (README)
        pairs = _pairs(tokens)
        for key, _ in pairs:  # grammar check first: an undefined command applies nothing
            if key not in _BSWV_KEYS and key not in _BSWV_KEYS_OTHER_FAMILIES:
                raise ValueError(f"undefined command: {cmd}")
        for key, value in pairs:
            if value is None or key in _BSWV_KEYS_OTHER_FAMILIES:
                continue  # silently ignored: no value / parameter not available on SDG2000X (§3.4 table)
            if channel.wvtp not in _KEY_VALID_FOR[key]:
                continue  # silently ignored: key not valid for the current WVTP. measured, hardware session 1 (README)
            self._set_bswv(channel, key, value)

    def _set_bswv(self, channel: _Channel, key: str, text: str) -> None:
        if key == "WVTP":
            self._set_wvtp(channel, text.upper())
            return
        if key == "BANDSTATE":
            if text.upper() in ("ON", "OFF"):
                channel.bandstate = text.upper()
            return
        value = _parse_number(text)
        if value is None:
            return  # invalid value for a known key: silently ignored. hypothesis until hardware session 1
        k = _k(channel.load)
        factor = _VRMS_FACTOR.get(channel.wvtp)
        if key == "FRQ":
            self._set_frq(channel, value)
        elif key == "PERI":  # PERI = 1/FRQ
            if value > 0:
                self._set_frq(channel, 1 / value)
        elif key == "AMP":
            self._set_amp(channel, value)
        elif key == "AMPVRMS":
            if factor is not None:  # ARB ignores it (guess: it echoes neither)   # measured for SINE only
                self._set_amp(channel, value / factor)
        elif key == "AMPDBM":
            # Ignored while LOAD is HZ. # measured, hardware session 1 (README); SINE only, other types guessed
            if factor is not None and channel.load is not None:
                self._set_amp(channel, math.sqrt(channel.load * 0.001 * 10 ** (value / 10)) / factor)
        elif key == "OFST":
            if channel.wvtp == "DC":
                channel.dc_ofst = _snap(min(max(value, -10 * k), 10 * k))
            else:
                self._set_ofst(channel, value)
        elif key == "HLEV":  # high level = OFST + AMP/2, the low level stays
            low = channel.ofst - channel.amp / 2
            high = min(max(value, low + _LEVEL_GAP), self._window(channel))
            channel.amp, channel.ofst = _snap(high - low), _snap((high + low) / 2)
        elif key == "LLEV":  # low level = OFST - AMP/2, the high level stays
            high = channel.ofst + channel.amp / 2
            low = min(max(value, -self._window(channel)), high - _LEVEL_GAP)
            channel.amp, channel.ofst = _snap(high - low), _snap((high + low) / 2)
        elif key == "STDEV":  # view of AMP
            self._set_amp(channel, value / _STDEV_PER_AMP)
        elif key == "MEAN":  # view of OFST
            self._set_ofst(channel, value)
        else:
            self._set_simple(channel, key, value)

    def _set_wvtp(self, channel: _Channel, wvtp: str) -> None:
        if wvtp not in _WAVE_TYPES or wvtp == channel.wvtp:
            return  # PRBS/IQ are not SDG2000X; an unchanged type is a no-op. hypothesis until hardware session 1
        if channel.wvtp == "PULSE":  # leaving PULSE turns its delay into the phase   # measured, hardware session 1 (README)
            channel.phse = _snap(-360 * channel.dly * channel.frq)
        channel.wvtp = wvtp  # every other value survives the change   # measured, hardware session 1 (README)
        self._set_frq(channel, channel.frq)  # frequency (and with it SQUARE duty, PULSE width) clamped for the new type

    def _set_frq(self, channel: _Channel, frq: float) -> None:
        # Clamped to 0 .. the type's maximum; 0 Hz is accepted (PERI then reads inf).   # measured, hardware session 1 (README)
        limit = self._limits.max_freq_hz.get(channel.wvtp)
        channel.frq = max(frq, 0.0) if limit is None else min(max(frq, 0.0), limit)
        if channel.wvtp == "PULSE":  # the width in seconds survives, clamped to the new period
            self._set_width(channel, channel.width)
        elif channel.wvtp == "SQUARE":  # the duty is clamped again and stays clamped
            self._set_square_duty(channel, channel.duty)
        if channel.amp > 2 * self._window(channel):  # above 20 MHz the amplitude is clamped and stays clamped
            channel.amp = 2 * self._window(channel)  # measured at OFST 0 only; the offset is then dropped (guess)
            channel.ofst = 0.0

    @staticmethod
    def _set_square_duty(channel: _Channel, duty: float) -> None:
        # Clamped to 100*16.3e-9*FRQ .. 100 - that.   # measured, hardware session 1 (README)
        margin = 100 * _MIN_PULSE_S * channel.frq
        channel.duty = min(max(duty, margin), 100 - margin)

    @staticmethod
    def _set_width(channel: _Channel, width: float) -> None:
        # measured, hardware session 1 (README); not modelled: a long edge lowers the maximum
        if channel.frq > 0:  # at 0 Hz: not measured, the width is left alone
            width = min(max(width, _MIN_PULSE_S), 1 / channel.frq - _MIN_PULSE_S)
        channel.width = width

    def _window(self, channel: _Channel) -> float:
        """Largest level in volts: 10*k, and 5 above 20 MHz (DC excepted). # measured, hardware session 1 (README)"""
        window = self._limits.max_offset_v_hiz * _k(channel.load)
        return min(window, _HF_LEVEL_V) if channel.frq > _HF_FRQ_HZ else window

    def _set_amp(self, channel: _Channel, amp: float) -> None:
        # Clamped to 0.002 .. 2*(window - |OFST|).   # measured, hardware session 1 (README)
        top = 2 * (self._window(channel) - abs(channel.ofst))
        channel.amp = _snap(min(max(amp, _AMP_MIN), top))

    def _set_ofst(self, channel: _Channel, ofst: float) -> None:
        # Clamped to +-(window - AMP/2).   # measured, hardware session 1 (README)
        limit = self._window(channel) - channel.amp / 2
        channel.ofst = _snap(min(max(ofst, -limit), limit))

    def _set_simple(self, channel: _Channel, key: str, value: float) -> None:
        if key == "PHSE":  # 400 reads 40, 360 stays 360, -90 stays   # measured, hardware session 1 (README)
            # 725 reads 5, -400 reads -40: the sign stays
            channel.phse = math.fmod(value, _PHSE_MAX) if abs(value) > _PHSE_MAX else value
        elif key == "SYM":  # clamped to 0..100   # measured, hardware session 1 (README)
            channel.sym = min(max(value, 0.0), 100.0)
        elif key == "DUTY":
            if channel.wvtp == "PULSE":  # one quantity with WIDTH (DUTY = 100*WIDTH*FRQ)
                self._set_width(channel, value / 100 / channel.frq)
            else:
                self._set_square_duty(channel, value)
        elif key == "WIDTH":
            self._set_width(channel, value)
        elif key == "RISE":
            channel.rise = max(value, _MIN_EDGE_S)
        elif key == "FALL":
            channel.fall = max(value, _MIN_EDGE_S)
        elif key == "DLY":  # clamped to +- one period   # measured, hardware session 1 (README)
            period = 1 / channel.frq if channel.frq > 0 else math.inf
            channel.dly = min(max(value, -period), period)
        elif key == "BANDWIDTH":  # clamped   # measured, hardware session 1 (README)
            channel.bandwidth = min(max(value, _BANDWIDTH_MIN), _BANDWIDTH_MAX)
        # MAX_OUTPUT_AMP (PG02 §3.3): accepted, no visible effect   # measured, hardware session 1 (README)

    # ----------------------------------------------------------------------------------------------------
    # ----------------------------------------------------------------------------------------------------
    # Queries
    # ----------------------------------------------------------------------------------------------------

    def _answer(self, cmd: str) -> str:
        upper = cmd.upper()
        if upper == "*IDN?":  # PG02 §3.1.1, response Format 2 for SDG2000X
            return f"Siglent Technologies,{self.model},{self.serial},{self.firmware}"
        if upper == "*OPC?":  # PG02 §3.1.2, response Format 2: the bare character 1
            return "1"
        match = _QUERY_RE.match(cmd)
        if match is not None:
            name = match.group(1).upper()
            header = match.group(2).upper()
            if header in _OUTP_HEADERS:  # PG02 §3.3 query <channel>:OUTPut?
                return self._outp_reply(name, self._channels[name])
            if header in _BSWV_HEADERS:  # PG02 §3.4 query <channel>:BaSic_WaVe?
                return self._bswv_reply(name, self._channels[name])
        raise ValueError(f"undefined query: {cmd}")

    @staticmethod
    def _outp_reply(name: str, channel: _Channel) -> str:
        # PG02 §3.3 response format and example: "C1:OUTP ON,LOAD,HZ,PLRT,NOR"; a numeric load is an integer
        state = "ON" if channel.output_on else "OFF"
        load = "HZ" if channel.load is None else str(channel.load)  # measured, hardware session 1 (README)
        return f"{name}:OUTP {state},LOAD,{load},PLRT,{channel.polarity}"

    @staticmethod
    def _bswv_reply(name: str, channel: _Channel) -> str:
        # Key sets, order, units and number formats: measured, hardware session 1 (README). PG02 §3.4 sample
        # reply is the shape: C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0
        wvtp = channel.wvtp
        fields: list[tuple[str, str]] = [("WVTP", wvtp)]
        if wvtp == "NOISE":
            fields.append(("STDEV", _g(_snap(channel.amp * _STDEV_PER_AMP)) + "V"))
            fields.append(("MEAN", _g(channel.ofst) + "V"))
            fields.append(("BANDSTATE", channel.bandstate))
            if channel.bandstate == "ON":
                fields.append(("BANDWIDTH", _g(channel.bandwidth, 10) + "HZ"))
        elif wvtp == "DC":
            fields.append(("OFST", _g(channel.dc_ofst) + "V"))
        else:
            fields.append(("FRQ", _g(channel.frq, 10) + "HZ"))
            fields.append(("PERI", (_g(1 / channel.frq) if channel.frq > 0 else "inf") + "S"))
            fields.append(("AMP", _g(channel.amp) + "V"))
            factor = _VRMS_FACTOR.get(wvtp)  # ARB has neither AMPVRMS nor AMPDBM
            if factor is not None:
                vrms = channel.amp * factor
                fields.append(("AMPVRMS", _g(vrms) + "Vrms"))
                if channel.load is not None:  # AMPDBM only at a numeric load (RAMP: guess)
                    fields.append(("AMPDBM", _g(_snap(10 * math.log10(vrms**2 / channel.load / 0.001), 9)) + "dBm"))
            fields.append(("OFST", _g(channel.ofst) + "V"))
            fields.append(("HLEV", _g(_snap(channel.ofst + channel.amp / 2)) + "V"))
            fields.append(("LLEV", _g(_snap(channel.ofst - channel.amp / 2)) + "V"))
            if wvtp == "PULSE":  # no PHSE
                fields.append(("DUTY", _g(100 * channel.width * channel.frq)))
                fields.append(("WIDTH", _g(channel.width)))
                fields.append(("RISE", _g(channel.rise) + "S"))
                fields.append(("FALL", _g(channel.fall) + "S"))
                fields.append(("DLY", _g(channel.dly)))
            else:
                fields.append(("PHSE", _g(channel.phse)))
                if wvtp == "SQUARE":
                    fields.append(("DUTY", _g(channel.duty)))
                elif wvtp == "RAMP":
                    fields.append(("SYM", _g(channel.sym)))
        body = ",".join(token for pair in fields for token in pair)
        return f"{name}:BSWV {body}"


def _pairs(tokens: list[str]) -> list[tuple[str, str | None]]:
    """``[k1, v1, k2, v2, k3]`` -> ``[(K1, v1), (K2, v2), (K3, None)]``; keys are upper-cased."""
    return [
        (tokens[i].upper(), tokens[i + 1] if i + 1 < len(tokens) else None)
        for i in range(0, len(tokens), 2)
    ]
