"""An in-memory stand-in for a PyVISA resource connected to a Siglent SDG2000X generator.

The fake is an independent oracle: it imports nothing from ``pyvisa``, ``openhtf`` or any other module of
this package (a shared parser would hide parser bugs), and it is the only instrument coder agents may talk
to. It emulates the v1 command subset: ``*IDN?``, ``*OPC``/``*OPC?``, ``*RST``, ``<ch>:OUTP`` and
``<ch>:BSWV`` with their queries.

The only source of SCPI is the official Siglent guide, ``docs/PG02-E05C.txt`` ("PG02"). Section numbers in
the comments below refer to it, and the SDG2000X column of its availability tables is the one that counts.

Nothing has been measured on the real generator yet. Everything PG02 does not state outright (defaults beyond
the sample reply, key sets of the non-SINE waveform types, number formats, silent-ignore behaviour, load
rescaling, value ranges) is a hypothesis and is marked ``# hypothesis until hardware session 1``. The real
generator has no error queue (PG02 documents none), so an invalid value for a known key is silently
ignored here too; only commands that PG02 does not define raise ``ValueError``.
"""

import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

__all__ = ["FakeSdgResource"]

# --------------------------------------------------------------------------------------------------------
# Model limits. Copied from the SDG2000X datasheet summary (NOT from PG02, which only says "refer to the
# datasheet"); deliberately not imported from models.py.   # hypothesis until hardware session 1
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Limits:
    max_freq_hz: dict[str, float]  # per WVTP; a missing entry means "no frequency check" for that type
    max_amp_vpp_hiz: float
    max_amp_vpp_50: float
    max_offset_v_hiz: float


_BASE_FREQ: Final = {"SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6}  # hypothesis until hardware session 1
_LIMITS: Final[dict[str, _Limits]] = {  # hypothesis until hardware session 1
    "SDG2042X": _Limits({"SINE": 40e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),
    "SDG2082X": _Limits({"SINE": 80e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),
    "SDG2122X": _Limits({"SINE": 120e6, **_BASE_FREQ}, 20.0, 10.0, 10.0),
}
_FALLBACK_LIMITS: Final = "SDG2042X"  # unknown model: the most restrictive entry (hypothesis, mirrors models.py)

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
# "Only settable when WVTP is RAMP", ...). MAX_OUTPUT_AMP and WVTP carry no restriction in PG02; DLY is modelled for PULSE only.
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
    "DLY": frozenset({"PULSE"}),  # hypothesis until hardware session 1 (echoed for PULSE only)
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


def _num(x: float, unit: str = "") -> str:
    """Format a reply number: integers without decimal point (``100HZ``, ``2V``), otherwise the shortest
    decimal without trailing zeros and without exponent (``0.01S``, ``0.000001S``); 12 significant digits.

    hypothesis until hardware session 1 (PG02 §3.4 sample reply shows ``100HZ``, ``0.01S``, ``2V``, ``-1V``)
    """
    x = float(format(x, ".12g"))
    if x == 0:
        return f"0{unit}"
    if x.is_integer():
        return f"{int(x)}{unit}"
    return f"{format(Decimal(repr(x)), 'f')}{unit}"


@dataclass
class _Channel:
    """State of one channel. Field defaults are the power-on/``*RST`` state (PG02 §3.1.3 "default setup").

    Common fields equal the PG02 §3.4 sample reply (``SINE, 100 Hz, 2 Vpp, 0 V offset, 0 deg``) and the
    PG02 §3.3 sample reply (``OFF`` is this fake's choice, the sample shows ``ON``; ``HZ``, ``NOR``).
    Type-specific values are hypotheses.   # hypothesis until hardware session 1
    """

    output_on: bool = False
    load: float | None = None  # None = HiZ ("HZ" in PG02 §3.3)
    polarity: str = "NOR"
    wvtp: str = "SINE"
    frq: float = 100.0
    amp: float = 2.0
    ofst: float = 0.0
    phse: float = 0.0
    duty: float = 50.0
    sym: float = 50.0
    width: float = 0.000001
    rise: float = 1e-8
    fall: float = 1e-8
    dly: float = 0.0
    stdev: float = 0.5
    mean: float = 0.0
    bandstate: str = "OFF"
    bandwidth: float = 1e6
    max_output_amp: float = 20.0  # PG02 §3.3 {1-20}

    def reset_type_specific(self) -> None:
        """Type-specific keys go back to their defaults when WVTP changes. hypothesis until hardware session 1"""
        fresh = _Channel()
        for name in (
            "duty", "sym", "width", "rise", "fall", "dly", "stdev", "mean", "bandstate", "bandwidth",
        ):  # fmt: skip
            setattr(self, name, getattr(fresh, name))


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
        # PG02 §3.3 SDG2000X column: LOAD is 50~100000 or HiZ (written "HZ" in the examples). Anything
        # else is silently ignored (no error queue).
        if text.upper() == "HZ":
            new: float | None = None
        else:
            value = _parse_number(text)
            if value is None or not 50 <= value <= 100000:
                return
            new = value
        old = channel.load
        channel.load = new
        self._rescale_for_load_change(channel, old, new)

    @staticmethod
    def _rescale_for_load_change(channel: _Channel, old: float | None, new: float | None) -> None:
        """LOAD-RESCALE HYPOTHESIS (the one place to delete if the hardware disagrees).

        Third-party reports say that switching the load between HiZ and a numeric load makes the generator
        rescale the displayed amplitude and offset (the stored voltage into the load is kept). PG02 does not
        say so. Modelled as: HZ -> number halves AMP and OFST, number -> HZ doubles them, number -> number
        changes nothing.   # hypothesis until hardware session 1
        """
        if old is None and new is not None:
            channel.amp /= 2
            channel.ofst /= 2
        elif old is not None and new is None:
            channel.amp *= 2
            channel.ofst *= 2

    def _write_bswv(self, channel: _Channel, tokens: list[str], cmd: str) -> None:
        # PG02 §3.4: <channel>:BSWV <parameter>,<value>. One pair per command is the documented form; several
        # pairs in one command are accepted and applied left to right.   # hypothesis until hardware session 1
        pairs = _pairs(tokens)
        for key, _ in pairs:  # grammar check first: an undefined command applies nothing
            if key not in _BSWV_KEYS and key not in _BSWV_KEYS_OTHER_FAMILIES:
                raise ValueError(f"undefined command: {cmd}")
        for key, value in pairs:
            if value is None or key in _BSWV_KEYS_OTHER_FAMILIES:
                continue  # silently ignored: no value / parameter not available on SDG2000X (§3.4 table)
            if channel.wvtp not in _KEY_VALID_FOR[key]:
                continue  # silently ignored: key not valid for the current WVTP (§3.4 "Description")
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
            return  # invalid value for a known key: silently ignored
        if key == "FRQ":
            self._set_frq(channel, value)
        elif key == "PERI":  # PERI = 1/FRQ
            if value > 0:
                self._set_frq(channel, 1 / value)
        elif key == "AMP":
            self._set_amp_ofst(channel, value, channel.ofst)
        elif key == "AMPVRMS":
            # Vrms -> Vpp conversion is modelled for SINE only; other types are ignored.
            # hypothesis until hardware session 1
            if channel.wvtp == "SINE":
                self._set_amp_ofst(channel, value * 2 * math.sqrt(2), channel.ofst)
        elif key == "AMPDBM":
            # dBm into 50 ohm -> Vpp, SINE only. hypothesis until hardware session 1
            if channel.wvtp == "SINE":
                vrms = math.sqrt(50 * 10 ** (value / 10) / 1000)
                self._set_amp_ofst(channel, vrms * 2 * math.sqrt(2), channel.ofst)
        elif key == "OFST":
            self._set_amp_ofst(channel, channel.amp, value)
        elif key == "HLEV":  # high level = OFST + AMP/2
            low = channel.ofst - channel.amp / 2
            self._set_amp_ofst(channel, value - low, (value + low) / 2)
        elif key == "LLEV":  # low level = OFST - AMP/2
            high = channel.ofst + channel.amp / 2
            self._set_amp_ofst(channel, high - value, (high + value) / 2)
        else:
            self._set_simple(channel, key, value)

    def _set_wvtp(self, channel: _Channel, wvtp: str) -> None:
        if wvtp not in _WAVE_TYPES or wvtp == channel.wvtp:
            return  # PRBS/IQ are not SDG2000X; an unchanged type is a no-op (hypothesis)
        channel.wvtp = wvtp
        # FRQ/AMP/OFST/PHSE are kept, type-specific keys go back to defaults. hypothesis until hardware session 1
        channel.reset_type_specific()
        # A frequency above the new type's limit is clamped. hypothesis until hardware session 1
        limit = self._limits.max_freq_hz.get(wvtp)
        if limit is not None and channel.frq > limit:
            channel.frq = limit

    def _set_frq(self, channel: _Channel, frq: float) -> None:
        limit = self._limits.max_freq_hz.get(channel.wvtp)  # hypothesis until hardware session 1
        if frq > 0 and (limit is None or frq <= limit):
            channel.frq = frq

    def _set_amp_ofst(self, channel: _Channel, amp: float, ofst: float) -> None:
        # Amplitude and offset limits depend on the load: 20 Vpp HiZ, 10 Vpp at a numeric load; offset +-10 V
        # HiZ and half of that at a numeric load.   # hypothesis until hardware session 1
        hiz = channel.load is None
        max_amp = self._limits.max_amp_vpp_hiz if hiz else self._limits.max_amp_vpp_50
        max_ofst = self._limits.max_offset_v_hiz * (1 if hiz else 0.5)
        if 0 < amp <= max_amp + 1e-12 and abs(ofst) <= max_ofst + 1e-12:
            channel.amp = amp
            channel.ofst = ofst

    @staticmethod
    def _set_simple(channel: _Channel, key: str, value: float) -> None:
        # Value ranges from PG02 §3.4 where it gives them (PHSE 0..360, SYM/DUTY 0..100, MAX_OUTPUT_AMP 1..20
        # in §3.3); the rest is "refer to the datasheet", modelled as merely positive.
        # hypothesis until hardware session 1
        if key == "PHSE" and 0 <= value <= 360:
            channel.phse = value
        elif key == "SYM" and 0 <= value <= 100:
            channel.sym = value
        elif key == "DUTY" and 0 <= value <= 100:
            channel.duty = value
        elif key == "WIDTH" and value > 0:
            channel.width = value
        elif key == "RISE" and value > 0:
            channel.rise = value
        elif key == "FALL" and value > 0:
            channel.fall = value
        elif key == "DLY" and value >= 0:
            channel.dly = value
        elif key == "STDEV" and value > 0:
            channel.stdev = value
        elif key == "MEAN":
            channel.mean = value
        elif key == "BANDWIDTH" and value > 0:
            channel.bandwidth = value
        elif key == "MAX_OUTPUT_AMP" and 1 <= value <= 20:
            channel.max_output_amp = value

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
        # PG02 §3.3 response format and example: "C1:OUTP ON,LOAD,HZ,PLRT,NOR"
        state = "ON" if channel.output_on else "OFF"
        load = "HZ" if channel.load is None else _num(channel.load)  # numeric load format: hypothesis
        return f"{name}:OUTP {state},LOAD,{load},PLRT,{channel.polarity}"

    @staticmethod
    def _bswv_reply(name: str, channel: _Channel) -> str:
        # PG02 §3.4 sample reply (SINE, defaults):
        #   C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0
        # Every other key set below is derived from the §3.4 validity rules and is a hypothesis until the
        # first hardware session; so are the unit suffixes of DUTY (none), SYM (none), STDEV/MEAN (V).
        fields: list[tuple[str, str]] = [("WVTP", channel.wvtp)]

        def common() -> None:
            fields.append(("FRQ", _num(channel.frq, "HZ")))
            fields.append(("PERI", _num(1 / channel.frq, "S")))
            fields.append(("AMP", _num(channel.amp, "V")))
            fields.append(("OFST", _num(channel.ofst, "V")))
            fields.append(("HLEV", _num(channel.ofst + channel.amp / 2, "V")))
            fields.append(("LLEV", _num(channel.ofst - channel.amp / 2, "V")))

        wvtp = channel.wvtp
        if wvtp in ("SINE", "ARB"):
            common()
            fields.append(("PHSE", _num(channel.phse)))
        elif wvtp == "SQUARE":
            common()
            fields.append(("PHSE", _num(channel.phse)))
            fields.append(("DUTY", _num(channel.duty)))
        elif wvtp == "RAMP":
            common()
            fields.append(("PHSE", _num(channel.phse)))
            fields.append(("SYM", _num(channel.sym)))
        elif wvtp == "PULSE":  # no PHSE (§3.4: not valid for PULSE); DUTY is settable for SQUARE or PULSE (§3.4)
            # DUTY in the PULSE reply: hypothesis until hardware session 1
            common()
            fields.append(("DUTY", _num(channel.duty)))
            fields.append(("WIDTH", _num(channel.width, "S")))
            fields.append(("RISE", _num(channel.rise, "S")))
            fields.append(("FALL", _num(channel.fall, "S")))
            fields.append(("DLY", _num(channel.dly, "S")))
        elif wvtp == "NOISE":
            fields.append(("STDEV", _num(channel.stdev, "V")))
            fields.append(("MEAN", _num(channel.mean, "V")))
            fields.append(("BANDSTATE", channel.bandstate))
            if channel.bandstate == "ON":
                fields.append(("BANDWIDTH", _num(channel.bandwidth, "HZ")))
        else:  # DC
            fields.append(("OFST", _num(channel.ofst, "V")))
        body = ",".join(token for pair in fields for token in pair)
        return f"{name}:BSWV {body}"


def _pairs(tokens: list[str]) -> list[tuple[str, str | None]]:
    """``[k1, v1, k2, v2, k3]`` -> ``[(K1, v1), (K2, v2), (K3, None)]``; keys are upper-cased."""
    return [
        (tokens[i].upper(), tokens[i + 1] if i + 1 < len(tokens) else None)
        for i in range(0, len(tokens), 2)
    ]
