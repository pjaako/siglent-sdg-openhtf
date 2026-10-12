"""Pure SCPI helpers for the Siglent SDG2000X: build, parse, units, tolerances, validation, ordering.

No I/O. The only SCPI source is the official guide PG02-E05C (``docs/PG02-E05C.txt``); every command
literal and key table below names its PG02 section. Nothing that PG02 does not define is built here.
"""

import math
import re
import sys
from array import array
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from .models import ModelLimits

Setup = Mapping[str, Mapping[str, Mapping[str, object]]]  # 'C1' -> 'BSWV'|'OUTP'|'MDWV'|... -> key -> value

CHANNELS = ("C1", "C2")  # PG02 §3.3/§3.4 <channel>:={C1,C2}
# PG02 §3.3 (OUTPut), §3.4 (BSWV), §3.5 (MDWV), §3.6.1 (SWWV), §3.7 (BTWV)
# PG02 §3.9.1 (ARWV) and §3.40 (SRATE) come after them
GROUPS = ("OUTP", "BSWV", "MDWV", "SWWV", "BTWV", "ARWV", "SRATE")
MOD_GROUPS = ("MDWV", "SWWV", "BTWV")  # at most one is on per channel (measured, modulation recon, README)

# PG02 §3.4 WVTP; PRBS/IQ excluded: their parameters are "no" for SDG2000X in the §3.4 availability table
WAVE_TYPES = frozenset({"SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "ARB", "DC"})

# PG02 §3.3; STATE is this project's name for the bare ON|OFF field of the OUTP command and reply
OUTP_KEYS = frozenset({"STATE", "LOAD", "PLRT"})

# PG02 §3.4 parameter table, SDG2000X column of the availability notes (LENGTH, EDGE, DIFFSTATE, BITRATE and
# LOGICLEVEL are "no" for SDG2000X; FORMAT and COM_OFST are left out: not part of v1), plus MAX_OUTPUT_AMP
# from PG02 §3.3 (``<channel>:BSWV MAX_OUTPUT_AMP,<1-20>``).
BSWV_KEYS = frozenset(
    {
        "WVTP", "FRQ", "PERI", "AMP", "AMPVRMS", "AMPDBM", "OFST", "SYM", "DUTY", "PHSE", "STDEV", "MEAN",
        "WIDTH", "RISE", "FALL", "DLY", "HLEV", "LLEV", "BANDSTATE", "BANDWIDTH", "MAX_OUTPUT_AMP",
    }
)  # fmt: skip

# PG02 §3.5 <type> (SDG2000X); TYPE is this project's name for the bare type token of the command and reply
MDWV_TYPES = frozenset({"AM", "DSBAM", "FM", "PM", "PWM", "ASK", "FSK", "PSK"})
# PG02 §3.5 parameters. The carrier is the BSWV group, so no CARR key exists in the data model.
MDWV_KEYS = frozenset({"STATE", "TYPE", "SRC", "MDSP", "FRQ", "DEPTH", "DEVI", "KFRQ", "HFRQ", "PLRT"})
# PG02 §3.6.1 parameters (everything the table lists, without MTRIG and CARR)
SWWV_KEYS = frozenset(
    {
        "STATE", "TIME", "START", "STOP", "CENTER", "SPAN", "SWMD", "DIR", "SYM", "TRSR", "TRMD", "EDGE",
        "MARK_STATE", "MARK_FREQ", "STARTTIME", "ENDTIME", "BACKTIME",
    }
)  # fmt: skip
# PG02 §3.7 parameters (without MTRIG and CARR)
BTWV_KEYS = frozenset(
    {"STATE", "PRD", "STPS", "GATE_NCYC", "TRSR", "DLAY", "PLRT", "TRMD", "EDGE", "TIME", "COUNT"}
)
_MOD_KEYS: Mapping[str, frozenset[str]] = {"MDWV": MDWV_KEYS, "SWWV": SWWV_KEYS, "BTWV": BTWV_KEYS}
_GROUP_SECTION = {
    "OUTP": "§3.3", "BSWV": "§3.4", "MDWV": "§3.5", "SWWV": "§3.6.1", "BTWV": "§3.7", "ARWV": "§3.9.1", "SRATE": "§3.40",
}  # fmt: skip

# PG02 §3.9.1 ARWV: select the arbitrary waveform by built-in INDEX or by (user) NAME; exactly one per setup
ARWV_KEYS = frozenset({"INDEX", "NAME"})
# PG02 §3.40 SRATE: MODE (DDS|TARB) and VALUE (Sa/s, TrueArb only). INTER is not available on the SDG2000X (§3.40 table).
SRATE_KEYS = frozenset({"MODE", "VALUE"})
SRATE_MODES = frozenset({"DDS", "TARB"})  # PG02 §3.40 <mode>
# The built-in table of this unit, index = position. The unit's table is PG02 §3.9.1's table shifted by two (index 0 is
# StairUp, not Sine); the names are the ``STL? BUILDIN`` reply (PG02 §3.31) of tests/data/hardware_arb_1.txt.
# measured, arb recon A (README)
BUILTIN_ARBS: tuple[str, ...] = (
    "StairUp", "StairDn", "StairUD", "Ppulse", "Npulse", "Trapezia", "Upramp", "Dnramp", "ExpFal", "ExpRise",
    "LogFall", "LogRise", "Sqrt", "Root3", "X^2", "X^3", "Sinc", "Gussian", "Dlorentz", "Haversine",
    "Lorentz", "Gauspuls", "Gmonopuls", "Tripuls", "Cardiac", "Quake", "Chirp", "Twotone", "SNR", "Hamming",
    "Hanning", "kaiser", "Blackman", "Gausswin", "Triangle", "Bartlett-Hann", "Bartlett", "Tan", "Cot", "Sec",
    "Csc", "Asin", "Acos", "Atan", "Acot", "Square", "SineTra", "SineVer", "AmpALT", "AttALT", "RoundHalf",
    "RoundsPM", "BlaseiWave", "DampedOsc", "SwingOsc", "Discharge", "Pahcur", "Combin", "SCR", "Butterworth",
    "Chebyshev1", "Chebyshev2", "TV", "Voice", "Surge", "Radar", "Ripple", "Gamma", "StepResp", "BandLimited",
    "CPulse", "CWPulse", "GateVibr", "LFMPulse", "MCNoise", "AM", "FM", "PFM", "PM", "PWM", "EOG", "EEG",
    "EMG", "Pulseilogram", "ResSpeed", "ECG1", "ECG2", "ECG3", "ECG4", "ECG5", "ECG6", "ECG7", "ECG8", "ECG9",
    "ECG10", "ECG11", "ECG12", "ECG13", "ECG14", "ECG15", "LFPulse", "Tens1", "Tens2", "Tens3", "Airy",
    "Besselj", "Bessely", "Dirichlet", "Erf", "Erfc", "ErfcInv", "ErfInv", "Laguerre", "Legend", "Versiera",
    "Weibull", "LogNormal", "Laplace", "Maxwell", "Rayleigh", "Cauchy", "CosH", "CosInt", "CotH", "CscH",
    "SecH", "SinH", "SinInt", "TanH", "ACosH", "ASecH", "ASinH", "ATanH", "ACsch", "ACoth", "Bartlett",
    "BohmanWin", "ChebWin", "FlattopWin", "ParzenWin", "TaylorWin", "TukeyWin", "SquareDuty01",
    "SquareDuty02", "SquareDuty04", "SquareDuty06", "SquareDuty08", "SquareDuty10", "SquareDuty12",
    "SquareDuty14", "SquareDuty16", "SquareDuty18", "SquareDuty20", "SquareDuty22", "SquareDuty24",
    "SquareDuty26", "SquareDuty28", "SquareDuty30", "SquareDuty32", "SquareDuty34", "SquareDuty36",
    "SquareDuty38", "SquareDuty40", "SquareDuty42", "SquareDuty44", "SquareDuty46", "SquareDuty48",
    "SquareDuty50", "SquareDuty52", "SquareDuty54", "SquareDuty56", "SquareDuty58", "SquareDuty60",
    "SquareDuty62", "SquareDuty64", "SquareDuty66", "SquareDuty68", "SquareDuty70", "SquareDuty72",
    "SquareDuty74", "SquareDuty76", "SquareDuty78", "SquareDuty80", "SquareDuty82", "SquareDuty84",
    "SquareDuty86", "SquareDuty88", "SquareDuty90", "SquareDuty92", "SquareDuty94", "SquareDuty96",
    "SquareDuty98", "SquareDuty99", "demo1_375pts", "demo1_16kpts", "demo2_3kpts", "demo2_16kpts",
)
ARB_INDEX_MAX = len(BUILTIN_ARBS) - 1  # 196
ARB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")  # measured up to 40 characters (B3); the upper bound is a hypothesis
SRATE_VALUE_MAX = 75e6  # Sa/s; VALUE,1E9 reads 75000000Sa/s (A11)   # measured, arb recon A (README)
SRATE_VALUE_MIN = 0.0  # exclusive; 0.1 was accepted (A11), the lower bound is a hypothesis
WVDT_MIN_BYTES, WVDT_MAX_BYTES = 4, 16_000_000  # 2 points accepted (A8); 8 000 000 points is the PG02 maximum (B5)
# Which modulation types a MDWV key belongs to (PG02 §3.5 parameter table; measured reply shapes, README)
_MDWV_KEY_TYPES: Mapping[str, frozenset[str]] = {
    "SRC": MDWV_TYPES,
    "MDSP": frozenset({"AM", "DSBAM", "FM", "PM", "PWM"}),
    "FRQ": frozenset({"AM", "DSBAM", "FM", "PM", "PWM"}),
    "DEPTH": frozenset({"AM"}),
    "DEVI": frozenset({"FM", "PM", "PWM"}),
    "KFRQ": frozenset({"ASK", "FSK", "PSK"}),
    "HFRQ": frozenset({"FSK"}),
    "PLRT": frozenset({"PSK"}),
}
# Enumerations exactly as PG02 lists them
_MOD_ENUMS: Mapping[tuple[str, str], frozenset[str]] = {
    ("MDWV", "SRC"): frozenset({"INT", "EXT", "CH1", "CH2"}),  # PG02 §3.5
    ("MDWV", "MDSP"): frozenset({"SINE", "SQUARE", "TRIANGLE", "UPRAMP", "DNRAMP", "NOISE", "ARB"}),  # PG02 §3.5
    ("MDWV", "PLRT"): frozenset({"POS", "NEG"}),  # PG02 §3.5
    ("SWWV", "SWMD"): frozenset({"LINE", "LOG", "STEP"}),  # PG02 §3.6.1
    ("SWWV", "DIR"): frozenset({"UP", "DOWN", "UP_DOWN"}),  # PG02 §3.6.1
    ("SWWV", "TRSR"): frozenset({"EXT", "INT", "MAN"}),  # PG02 §3.6.1
    ("SWWV", "EDGE"): frozenset({"RISE", "FALL"}),  # PG02 §3.6.1
    ("BTWV", "GATE_NCYC"): frozenset({"GATE", "NCYC"}),  # PG02 §3.7
    ("BTWV", "TRSR"): frozenset({"EXT", "INT", "MAN"}),  # PG02 §3.7
    ("BTWV", "PLRT"): frozenset({"NEG", "POS"}),  # PG02 §3.7
    ("BTWV", "TRMD"): frozenset({"RISE", "FALL", "OFF"}),  # PG02 §3.7
    ("BTWV", "EDGE"): frozenset({"RISE", "FALL"}),  # PG02 §3.7
}
_MOD_RANGES: Mapping[tuple[str, str], tuple[float, float]] = {
    ("MDWV", "DEPTH"): (0.0, 120.0),  # PG02 §3.5, percent
    ("SWWV", "SYM"): (0.0, 100.0),  # PG02 §3.6.1, percent
    ("SWWV", "STARTTIME"): (0.0, 300.0),  # PG02 §3.6.1, seconds
    ("SWWV", "ENDTIME"): (0.0, 300.0),  # PG02 §3.6.1
    ("SWWV", "BACKTIME"): (0.0, 300.0),  # PG02 §3.6.1
    ("BTWV", "STPS"): (0.0, 360.0),  # PG02 §3.7, degrees
}
_MOD_POSITIVE = frozenset(
    {("MDWV", "FRQ"), ("MDWV", "KFRQ"), ("MDWV", "HFRQ"), ("SWWV", "TIME"), ("SWWV", "START"), ("SWWV", "STOP"),
     ("BTWV", "PRD")}
)  # fmt: skip

_ALL_TYPES = WAVE_TYPES
_NOT_NOISE_DC = WAVE_TYPES - {"NOISE", "DC"}

# Valid BSWV keys per WVTP, from the "Description" column of PG02 §3.4 ("Not valid when WVTP is ...",
# "Only settable when WVTP is ..."). MAX_OUTPUT_AMP and WVTP: PG02 states no restriction. DLY: PG02 states no
# restriction either, but only PULSE has a delay parameter in the reply; restricted to PULSE so that the
# validator and the fake agree (measured, hardware session 1: README).
BSWV_KEY_WAVE_TYPES: Mapping[str, frozenset[str]] = {
    "WVTP": _ALL_TYPES,  # PG02 §3.4
    "FRQ": _NOT_NOISE_DC,  # PG02 §3.4
    "PERI": _NOT_NOISE_DC,  # PG02 §3.4
    "AMP": _NOT_NOISE_DC,  # PG02 §3.4
    "AMPVRMS": _NOT_NOISE_DC,  # PG02 §3.4
    "AMPDBM": _NOT_NOISE_DC,  # PG02 §3.4
    "HLEV": _NOT_NOISE_DC,  # PG02 §3.4
    "LLEV": _NOT_NOISE_DC,  # PG02 §3.4
    "OFST": _ALL_TYPES - {"NOISE"},  # PG02 §3.4
    "PHSE": _ALL_TYPES - {"NOISE", "PULSE", "DC"},  # PG02 §3.4
    "SYM": frozenset({"RAMP"}),  # PG02 §3.4
    "DUTY": frozenset({"SQUARE", "PULSE"}),  # PG02 §3.4
    "WIDTH": frozenset({"PULSE"}),  # PG02 §3.4
    "RISE": frozenset({"PULSE"}),  # PG02 §3.4
    "FALL": frozenset({"PULSE"}),  # PG02 §3.4
    "STDEV": frozenset({"NOISE"}),  # PG02 §3.4
    "MEAN": frozenset({"NOISE"}),  # PG02 §3.4
    "BANDSTATE": frozenset({"NOISE"}),  # PG02 §3.4
    "BANDWIDTH": frozenset({"NOISE"}),  # PG02 §3.4
    "DLY": frozenset({"PULSE"}),  # PG02 §3.4; PULSE-only measured, hardware session 1 (README)
    "MAX_OUTPUT_AMP": _ALL_TYPES,  # PG02 §3.3
}

# Keys that only exist for a few waveform types. Without a ``WVTP`` in the same channel setup they are
# rejected (the plug cannot know the generator's current type without querying it).
_TYPE_SPECIFIC_KEYS = frozenset(
    {"SYM", "DUTY", "WIDTH", "RISE", "FALL", "DLY", "STDEV", "MEAN", "BANDSTATE", "BANDWIDTH"}
)
_AMPLITUDE_KEYS = ("AMP", "AMPVRMS", "AMPDBM")  # PG02 §3.4 (Vpp, Vrms, dBm)
_LEVEL_KEYS = ("HLEV", "LLEV")  # PG02 §3.4
_LOAD_MIN, _LOAD_MAX = 50.0, 100000.0  # PG02 §3.3 availability table, SDG2000X: 50~100000, HiZ
_AMP_MIN = 0.002  # Vpp, smallest amplitude; measured, hardware session 1 (README)
_LEVEL_GAP = 0.002  # V, smallest HLEV - LLEV; measured, hardware session 1 (README)
_HF_FRQ_HZ, _HF_LEVEL_V = 20e6, 5.0  # measured, hardware session 1 (README): above 20 MHz levels are clamped to +-5 V
_LOAD_HIZ = "HZ"  # PG02 §3.3 example ``C1:OUTP LOAD,HZ``
_POLARITIES = frozenset({"NOR", "INVT"})  # PG02 §3.3 <polarity>
_ON_OFF = frozenset({"ON", "OFF"})  # PG02 §3.3, §3.4 BANDSTATE
# Value ranges PG02 documents (the rest is "refer to the datasheet"): key -> (min, max)
_BSWV_RANGES: Mapping[str, tuple[float, float]] = {
    "PHSE": (0.0, 360.0),  # PG02 §3.4, degrees
    "SYM": (0.0, 100.0),  # PG02 §3.4, percent
    "DUTY": (0.0, 100.0),  # PG02 §3.4, percent
    "MAX_OUTPUT_AMP": (1.0, 20.0),  # PG02 §3.3, Vpp
}

# Comparison tolerances for read-back verification. The generator echoes numbers with 6 significant digits
# (10 for FRQ and BANDWIDTH); measured, hardware session 1 (README). A clamped value must not match.
DEFAULT_REL_TOL = 1e-5
REL_TOL: dict[str, float] = {"FRQ": 1e-9, "BANDWIDTH": 1e-9}
ABS_TOL: dict[str, float] = dict.fromkeys(
    ("AMP", "AMPVRMS", "AMPDBM", "OFST", "HLEV", "LLEV", "STDEV", "MEAN", "PHSE", "DUTY", "SYM"), 1e-6
)


class Reply(NamedTuple):
    """A parsed ``<ch>:<CMD> k,v,k,v`` reply."""

    header: str
    fields: dict[str, str]  # insertion order as received
    raw: str


class ProtocolError(RuntimeError):
    """Reply header mismatch or unparsable reply; the message includes the raw reply."""


class SetupError(RuntimeError):
    """Read-back of a setup did not match; ``failures`` lists every mismatch."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = list(failures)
        super().__init__("\n".join(self.failures))


def channel_name(channel: int) -> str:
    """1 -> ``C1``, 2 -> ``C2`` (PG02 §3.3/§3.4 <channel>)."""
    if isinstance(channel, bool) or not isinstance(channel, int) or channel not in (1, 2):
        raise ValueError(f"channel must be 1 or 2, got {channel!r}")
    return f"C{channel}"


def format_value(value: object) -> str:
    """Render a Python value for a command.

    bool -> ON/OFF; int -> decimal; float -> ``.10G`` (exponent forms such as ``100E6`` and ``1E-06`` are accepted on
    write, and so are more digits than the echo shows; measured, hardware session 1, README); str unchanged.
    """
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot send non-finite value {value!r}")
        return format(value, ".10G")
    if isinstance(value, str):
        return value
    raise TypeError(f"cannot format {type(value).__name__} value {value!r} for SCPI")


def build_bswv(channel: int, key: str, value: object) -> str:
    """``C1:BSWV FRQ,2000`` (PG02 §3.4 examples ``C1:BSWV WVTP,RAMP``, ``C1:BSWV AMP,3``)."""
    return f"{channel_name(channel)}:BSWV {key},{format_value(value)}"  # PG02 §3.4


def _on_off(value: object) -> str | None:
    """``'ON'``/``'OFF'`` for a bool or an ON/OFF string (strip + upper), else ``None``. One rule everywhere."""
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, str):
        text = value.strip().upper()
        if text in _ON_OFF:
            return text
    return None


def _is_on(value: object) -> bool:
    return _on_off(value) == "ON"


def is_on(value: object) -> bool:
    """True for ``True`` or an ON string (strip + upper), False for everything else."""
    return _is_on(value)


def build_outp(channel: int, key: str, value: object) -> str:
    """``C1:OUTP ON``, ``C1:OUTP LOAD,50``, ``C1:OUTP LOAD,HZ``, ``C1:OUTP PLRT,NOR`` (PG02 §3.3 examples)."""
    ch = channel_name(channel)
    if key == "STATE":
        return f"{ch}:OUTP {'ON' if _is_on(value) else 'OFF'}"  # PG02 §3.3
    if key == "LOAD":
        text = format_value(value)
        return f"{ch}:OUTP LOAD,{text.upper() if isinstance(value, str) else text}"  # PG02 §3.3
    if key == "PLRT":
        return f"{ch}:OUTP PLRT,{format_value(value)}"  # PG02 §3.3
    raise ValueError(f"unknown OUTP key {key!r}")


def build_mdwv(channel: int, mdwv_type: str, key: str, value: object) -> str:
    """``C1:MDWV STATE,ON``, ``C1:MDWV AM`` (key ``TYPE``), ``C1:MDWV AM,DEPTH,40`` (PG02 §3.5 examples).

    Every parameter other than ``STATE`` and ``TYPE`` is written with its type in front.
    """
    ch = channel_name(channel)
    if key == "STATE":
        return f"{ch}:MDWV STATE,{'ON' if _is_on(value) else 'OFF'}"  # PG02 §3.5
    if key == "TYPE":
        return f"{ch}:MDWV {format_value(value)}"  # PG02 §3.5
    return f"{ch}:MDWV {mdwv_type},{key},{format_value(value)}"  # PG02 §3.5


def _build_swwv_btwv(channel: int, group: str, key: str, value: object) -> str:
    """``C1:SWWV TIME,2``, ``C1:BTWV TIME,INF`` (PG02 §3.6.1, §3.7 examples); ``STATE`` is written ON/OFF."""
    text = ("ON" if _is_on(value) else "OFF") if key == "STATE" else format_value(value)
    return f"{channel_name(channel)}:{group} {key},{text}"  # PG02 §3.6.1, §3.7


def build_arwv(channel: int, key: str, value: object) -> str:
    """``C1:ARWV INDEX,5``, ``C1:ARWV NAME,htf_test`` (PG02 §3.9.1 Format1, Format2; unquoted, measured, arb recon A)."""
    if key not in ARWV_KEYS:
        raise ValueError(f"unknown ARWV key {key!r}")
    return f"{channel_name(channel)}:ARWV {key},{format_value(value)}"  # PG02 §3.9.1


def build_srate(channel: int, key: str, value: object) -> str:
    """``C1:SRATE MODE,TARB``, ``C1:SRATE VALUE,4096000`` (PG02 §3.40 examples); ``MODE`` is sent upper case."""
    if key not in SRATE_KEYS:
        raise ValueError(f"unknown SRATE key {key!r}")
    text = format_value(value)
    return f"{channel_name(channel)}:SRATE {key},{text.upper() if key == 'MODE' else text}"  # PG02 §3.40


def build_command(channel: int, group: str, key: str, value: object, *, mdwv_type: str | None = None) -> str:
    """The write command for one ``(group, key, value)`` triple (PG02 §3.3 to §3.7).

    ``mdwv_type`` is the modulation type the parameters of an ``MDWV`` command are written with.
    """
    if group == "OUTP":
        return build_outp(channel, key, value)
    if group == "BSWV":
        return build_bswv(channel, key, value)
    if group == "MDWV":
        if mdwv_type is None and key not in ("STATE", "TYPE"):
            raise ValueError(f"MDWV {key}: the modulation type is needed to build the command")
        return build_mdwv(channel, mdwv_type or "", key, value)
    if group in ("SWWV", "BTWV"):
        return _build_swwv_btwv(channel, group, key, value)
    if group == "ARWV":
        return build_arwv(channel, key, value)
    if group == "SRATE":
        return build_srate(channel, key, value)
    raise ValueError(f"unknown group {group!r}; expected one of {list(GROUPS)}")


def parse_reply(raw: str, expect_header: str, leading_key: str | None = None) -> Reply:
    """Parse ``<header> a,b,c,d`` into ordered fields (PG02 §3.3/§3.4 response formats).

    The header is everything before the first space and must equal ``expect_header``. With ``leading_key`` the
    first body token is stored under that key (the bare ``ON|OFF`` of ``C1:OUTP ON,LOAD,HZ,PLRT,NOR``); the
    remaining tokens must pair up as ``key,value``.
    """
    text = raw.strip()
    header, _, body = text.partition(" ")
    if header != expect_header:
        raise ProtocolError(f"expected reply header {expect_header!r}, got {header!r}: {raw!r}")
    tokens = [t.strip() for t in body.split(",")] if body.strip() else []
    fields: dict[str, str] = {}
    if leading_key is not None:
        if not tokens:
            raise ProtocolError(f"reply has no leading {leading_key!r} value: {raw!r}")
        fields[leading_key] = tokens.pop(0)
    if len(tokens) % 2:
        raise ProtocolError(f"odd number of key/value tokens in reply: {raw!r}")
    for key, val in zip(tokens[0::2], tokens[1::2], strict=True):
        if not key or key in fields:
            raise ProtocolError(f"empty or duplicate key {key!r} in reply: {raw!r}")
        fields[key] = val
    return Reply(header=header, fields=fields, raw=text)


def parse_mod_reply(raw: str, expect_header: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse ``<ch>:MDWV|SWWV|BTWV`` replies into ``(own fields, carrier fields)`` (PG02 §3.5, §3.6.1, §3.7).

    The reply is ``STATE,ON,<own fields>,CARR,<carrier fields>``; it is split at the bare ``CARR`` token. The
    bare type token of ``MDWV`` is stored under ``TYPE``. ``STATE,OFF`` gives ``({"STATE": "OFF"}, {})``.
    """
    text = raw.strip()
    header, _, body = text.partition(" ")
    if header != expect_header:
        raise ProtocolError(f"expected reply header {expect_header!r}, got {header!r}: {raw!r}")
    tokens = [t.strip() for t in body.split(",")] if body.strip() else []
    if "CARR" in tokens:
        split = tokens.index("CARR")
        own_tokens, carrier_tokens = tokens[:split], tokens[split + 1 :]
    else:
        own_tokens, carrier_tokens = tokens, []
    fields: dict[str, str] = {}
    if len(own_tokens) >= 2 and own_tokens[0] == "STATE":
        fields["STATE"] = own_tokens[1]
        own_tokens = own_tokens[2:]
    if expect_header.endswith(":MDWV") and len(own_tokens) % 2:
        fields["TYPE"] = own_tokens.pop(0)  # the bare type token
    return (_pairs_to_dict(own_tokens, raw, fields), _pairs_to_dict(carrier_tokens, raw, {}))


def _pairs_to_dict(tokens: list[str], raw: str, fields: dict[str, str]) -> dict[str, str]:
    if len(tokens) % 2:
        raise ProtocolError(f"odd number of key/value tokens in reply: {raw!r}")
    for key, val in zip(tokens[0::2], tokens[1::2], strict=True):
        if not key or key in fields:
            raise ProtocolError(f"empty or duplicate key {key!r} in reply: {raw!r}")
        fields[key] = val
    return fields


_NUMBER_WITH_UNIT = re.compile(r"^([-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)\s*([A-Za-z%/]*)$")


def strip_unit(text: str) -> float | str:
    """``100HZ`` -> 100.0, ``-1V`` -> -1.0, ``50%`` -> 50.0, ``100E6`` -> 1e8; non-numbers stay strings."""
    match = _NUMBER_WITH_UNIT.match(text.strip())
    if match is None:
        return text
    return float(match.group(1))


def typed_fields(reply: Reply) -> dict[str, float | str]:
    """``strip_unit`` over every field of a reply."""
    return {key: strip_unit(val) for key, val in reply.fields.items()}


def values_match(key: str, sent: object, got: float | str) -> bool:
    """Compare a value we sent with the typed value read back (read-back is the arbiter, AGENTS.md)."""
    if key == "NAME":
        # ARWV NAME reads back with ".bin" appended and is case-sensitive   # measured, arb recon B (README, B3)
        if not (isinstance(sent, str) and isinstance(got, str)):
            return False
        return got.removesuffix(".bin") == sent
    if isinstance(sent, bool):
        return isinstance(got, str) and got.strip().upper() == ("ON" if sent else "OFF")
    if isinstance(sent, str):
        return isinstance(got, str) and got.strip().upper() == sent.strip().upper()
    if isinstance(sent, int | float):
        if isinstance(got, str):
            return False
        return math.isclose(
            float(sent), got, rel_tol=REL_TOL.get(key, DEFAULT_REL_TOL), abs_tol=ABS_TOL.get(key, 0.0)
        )
    return False


def _number(value: object) -> float | None:
    """The value as a float if it is a real (non-bool) number, else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _where(channel: str, group: str, key: str | None = None) -> str:
    return f"{channel} {group}" if key is None else f"{channel} {group} {key}"


def _check_outp_value(where: str, key: str, value: object) -> None:
    if key == "STATE":
        if _on_off(value) is None:
            raise ValueError(f"{where}: must be a bool or 'ON'/'OFF', got {value!r}")  # PG02 §3.3
    elif key == "LOAD":
        num = _number(value)
        if num is not None:
            if not _LOAD_MIN <= num <= _LOAD_MAX:
                raise ValueError(f"{where}: {value!r} outside 50..100000 ohms")  # PG02 §3.3 table
        elif not (isinstance(value, str) and value.upper() == _LOAD_HIZ):
            raise ValueError(f"{where}: must be a number 50..100000 or 'HZ', got {value!r}")
    elif key == "PLRT":
        if not (isinstance(value, str) and value in _POLARITIES):
            raise ValueError(f"{where}: must be 'NOR' or 'INVT', got {value!r}")  # PG02 §3.3


def _check_bswv_value(where: str, key: str, value: object) -> None:
    if key == "WVTP":
        if not (isinstance(value, str) and value in WAVE_TYPES):
            raise ValueError(f"{where}: {value!r} is not one of {sorted(WAVE_TYPES)}")  # PG02 §3.4
    elif key == "BANDSTATE":
        if _on_off(value) is None:
            raise ValueError(f"{where}: must be a bool or 'ON'/'OFF', got {value!r}")  # PG02 §3.4
    else:
        num = _number(value)
        if num is None or not math.isfinite(num):
            raise ValueError(f"{where}: must be a finite number, got {value!r}")
        if key in ("FRQ", "PERI") and num <= 0:
            # measured, hardware session 1 (README): FRQ,0 is accepted and then reads FRQ,0HZ,PERI,infS
            raise ValueError(f"{where}: must be greater than 0, got {num:g}")
        if key in _BSWV_RANGES:
            low, high = _BSWV_RANGES[key]
            if not low <= num <= high:
                raise ValueError(f"{where}: {num:g} outside {low:g}..{high:g} (PG02 §3.3/§3.4)")


def validate_setup(setup: Setup, limits: ModelLimits | None) -> None:
    """Raise ``ValueError`` naming channel/group/key for anything invalid. Pure: sends and mutates nothing."""
    for channel, groups in setup.items():
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel {channel!r}; expected one of {list(CHANNELS)}")
        if not isinstance(groups, Mapping):
            raise ValueError(f"{channel}: expected a mapping of groups, got {type(groups).__name__}")
        for group, params in groups.items():
            if group not in GROUPS:
                raise ValueError(f"{channel}: unknown group {group!r}; expected one of {list(GROUPS)}")
            if not isinstance(params, Mapping):
                raise ValueError(f"{_where(channel, group)}: expected a mapping of keys, got {type(params).__name__}")
            allowed = {"OUTP": OUTP_KEYS, "BSWV": BSWV_KEYS, "ARWV": ARWV_KEYS, "SRATE": SRATE_KEYS, **_MOD_KEYS}[group]
            for key in params:
                if key not in allowed:
                    raise ValueError(
                        f"{_where(channel, group, key)}: not a {group} parameter of the SDG2000X "
                        f"(PG02 {_GROUP_SECTION[group]}); allowed: {sorted(allowed)}"
                    )
        outp = groups.get("OUTP", {})
        bswv = groups.get("BSWV", {})
        for key, value in outp.items():
            _check_outp_value(_where(channel, "OUTP", key), key, value)
        for key, value in bswv.items():
            _check_bswv_value(_where(channel, "BSWV", key), key, value)
        _validate_bswv_combination(channel, bswv)
        for group in MOD_GROUPS:
            for key, value in groups.get(group, {}).items():
                _check_mod_value(_where(channel, group, key), group, key, value)
        _validate_mod_combination(channel, groups)
        for group in ("ARWV", "SRATE"):
            for key, value in groups.get(group, {}).items():
                _check_arb_value(_where(channel, group, key), group, key, value)
        _validate_arb_combination(channel, groups)
        if limits is not None:
            _validate_limits(channel, outp, bswv, limits)


def _check_arb_name(where: str, name: object) -> str:
    """The user wave name as the plug accepts it: ``[A-Za-z0-9_-]{1,40}``, no ``.bin`` (measured, arb recon B, B3)."""
    if not isinstance(name, str):
        raise ValueError(f"{where}: the wave name must be a string, got {name!r}")
    if name.endswith(".bin"):
        raise ValueError(f"{where}: {name!r} ends with '.bin'; give the name without it (the plug keeps names canonical)")
    if ARB_NAME_PATTERN.match(name) is None:
        raise ValueError(f"{where}: {name!r} does not match [A-Za-z0-9_-]{{1,40}}")
    return name


def _check_arb_value(where: str, group: str, key: str, value: object) -> None:
    """Value type and range of one ARWV or SRATE key (PG02 §3.9.1, §3.40)."""
    if group == "ARWV":
        if key == "INDEX":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ARB_INDEX_MAX:
                raise ValueError(f"{where}: must be an int in 0..{ARB_INDEX_MAX}, got {value!r}")  # PG02 §3.9.1
        else:
            _check_arb_name(where, value)
    elif key == "MODE":
        if not (isinstance(value, str) and value.upper() in SRATE_MODES):
            raise ValueError(f"{where}: must be 'DDS' or 'TARB', got {value!r}")  # PG02 §3.40
    else:
        num = _number(value)
        if num is None or not math.isfinite(num) or not SRATE_VALUE_MIN < num <= SRATE_VALUE_MAX:
            raise ValueError(f"{where}: must be a number above 0 and up to {SRATE_VALUE_MAX:g} Sa/s, got {value!r}")


def _validate_arb_combination(channel: str, groups: Mapping[str, Mapping[str, object]]) -> None:
    """ARWV and SRATE need an ARB carrier in the same setup; one selector; VALUE only in TARB; one quantity with FRQ."""
    bswv = groups.get("BSWV", {})
    arwv = groups.get("ARWV")
    srate = groups.get("SRATE")
    if arwv is not None:
        chosen = [k for k in ("INDEX", "NAME") if k in arwv]
        if len(chosen) != 1:
            raise ValueError(f"{_where(channel, 'ARWV')}: give exactly one of INDEX and NAME, got {sorted(arwv)}")
    for group, params in (("ARWV", arwv), ("SRATE", srate)):
        if params and bswv.get("WVTP") != "ARB":
            # the generator would switch the type silently; the setup must say it   # measured, arb recon A (A4)
            raise ValueError(f"{_where(channel, group)}: needs BSWV WVTP 'ARB' in the same channel setup")
    if srate:
        mode = srate.get("MODE")
        tarb = isinstance(mode, str) and mode.upper() == "TARB"
        if "VALUE" in srate:
            if mode is None:
                raise ValueError(f"{_where(channel, 'SRATE', 'VALUE')}: needs MODE in the same group (be explicit)")
            if not tarb:
                raise ValueError(f"{_where(channel, 'SRATE', 'VALUE')}: only valid with MODE 'TARB'")  # A10
            for key in ("FRQ", "PERI"):
                if key in bswv:
                    raise ValueError(
                        f"{_where(channel, 'BSWV', key)}: cannot be combined with SRATE VALUE; in TARB the "
                        "frequency and the sample rate are one quantity"  # A11, B6
                    )


def _check_mod_value(where: str, group: str, key: str, value: object) -> None:
    """Value type, enumeration and range of one MDWV, SWWV or BTWV key (PG02 §3.5, §3.6.1, §3.7)."""
    if key in ("STATE", "MARK_STATE") or (group == "SWWV" and key == "TRMD"):
        if _on_off(value) is None:
            raise ValueError(f"{where}: must be a bool or 'ON'/'OFF', got {value!r}")
    elif group == "MDWV" and key == "TYPE":
        if not (isinstance(value, str) and value in MDWV_TYPES):
            raise ValueError(f"{where}: {value!r} is not one of {sorted(MDWV_TYPES)}")  # PG02 §3.5
    elif (group, key) in _MOD_ENUMS:
        options = _MOD_ENUMS[(group, key)]
        if not (isinstance(value, str) and value in options):
            raise ValueError(f"{where}: {value!r} is not one of {sorted(options)}")
    elif group == "BTWV" and key == "TIME":
        # PG02 §3.7: INF or a positive cycle count
        if not (value == "INF" or (isinstance(value, int) and not isinstance(value, bool) and value > 0)):
            raise ValueError(f"{where}: must be a positive int or 'INF', got {value!r}")
    else:
        num = _number(value)
        if num is None or not math.isfinite(num):
            raise ValueError(f"{where}: must be a finite number, got {value!r}")
        if (group, key) in _MOD_POSITIVE and num <= 0:
            raise ValueError(f"{where}: must be greater than 0, got {num:g}")
        if (group, key) in _MOD_RANGES:
            low, high = _MOD_RANGES[(group, key)]
            if not low <= num <= high:
                raise ValueError(f"{where}: {num:g} outside {low:g}..{high:g} (PG02 {_GROUP_SECTION[group]})")


def _validate_mod_combination(channel: str, groups: Mapping[str, Mapping[str, object]]) -> None:
    """Rules across keys: STATE on needed, key fits the type, exclusivity, carrier and frequency coupling."""
    bswv = groups.get("BSWV", {})
    wvtp = bswv.get("WVTP")
    on_groups: list[str] = []
    for group in MOD_GROUPS:
        params = groups.get(group)
        if not params:
            continue
        state_on = "STATE" in params and _is_on(params["STATE"])
        others = [k for k in params if k != "STATE"]
        if others and not state_on:
            # measured, modulation recon (README): while STATE is OFF every other parameter is ignored
            raise ValueError(
                f"{_where(channel, group, others[0])}: needs STATE on in the same group "
                "(parameters written while STATE is OFF are ignored by the generator)"
            )
        if state_on:
            on_groups.append(group)
    if len(on_groups) > 1:
        raise ValueError(
            f"{channel}: {' and '.join(on_groups)} cannot be on together (switching one on switches the others off)"
        )
    mdwv = groups.get("MDWV")
    if mdwv and "STATE" in mdwv and _is_on(mdwv["STATE"]):
        mtype = mdwv.get("TYPE")
        if mtype is None:
            raise ValueError(f"{_where(channel, 'MDWV', 'TYPE')}: required when STATE is on")
        for key in mdwv:
            if key in _MDWV_KEY_TYPES and mtype not in _MDWV_KEY_TYPES[key]:
                raise ValueError(
                    f"{_where(channel, 'MDWV', key)}: not a parameter of {mtype} (valid for "
                    f"{sorted(_MDWV_KEY_TYPES[key])}; PG02 §3.5)"
                )
        if mtype == "PM" and "DEVI" in mdwv:
            devi = _number(mdwv["DEVI"])
            if devi is not None and not 0.0 <= devi <= 360.0:
                raise ValueError(f"{_where(channel, 'MDWV', 'DEVI')}: {devi:g} outside 0..360 for PM (PG02 §3.5)")
        if isinstance(wvtp, str):
            if wvtp in ("NOISE", "DC"):
                raise ValueError(f"{_where(channel, 'MDWV')}: modulation is not available with a {wvtp} carrier")
            if mtype == "PWM" and wvtp != "PULSE":
                raise ValueError(f"{_where(channel, 'MDWV', 'TYPE')}: PWM needs a PULSE carrier, BSWV WVTP is {wvtp}")
            if wvtp == "PULSE" and mtype != "PWM":
                raise ValueError(f"{_where(channel, 'MDWV', 'TYPE')}: a PULSE carrier allows only PWM, not {mtype}")
    swwv = groups.get("SWWV")
    if swwv and "STATE" in swwv and _is_on(swwv["STATE"]):
        if isinstance(wvtp, str) and wvtp in ("PULSE", "NOISE", "DC"):
            raise ValueError(f"{_where(channel, 'SWWV')}: a sweep is not available with a {wvtp} carrier")
        for key in ("FRQ", "PERI"):
            if key in bswv:
                raise ValueError(
                    f"{_where(channel, 'BSWV', key)}: cannot be combined with a sweep, "
                    "which owns the carrier frequency (set START and STOP)"
                )
        start, stop = _number(swwv.get("START")), _number(swwv.get("STOP"))
        if start is not None and stop is not None and start > stop:
            raise ValueError(f"{_where(channel, 'SWWV', 'START')}: {start:g} is above STOP {stop:g}")
    btwv = groups.get("BTWV")
    if btwv and "STPS" in btwv and "PHSE" in bswv:
        stps, phse = _number(btwv["STPS"]), _number(bswv["PHSE"])
        if stps is not None and phse is not None and stps != phse:
            raise ValueError(
                f"{_where(channel, 'BTWV', 'STPS')}: {stps:g} differs from BSWV PHSE {phse:g}; "
                "the burst start phase is the carrier phase"
            )


def _validate_bswv_combination(channel: str, bswv: Mapping[str, object]) -> None:
    wvtp = bswv.get("WVTP")
    for key in bswv:
        valid = BSWV_KEY_WAVE_TYPES[key]
        if wvtp is None:
            if key in _TYPE_SPECIFIC_KEYS:
                raise ValueError(
                    f"{_where(channel, 'BSWV', key)}: valid only for WVTP {sorted(valid)}; "
                    "WVTP is required alongside it"
                )
        elif wvtp not in valid:
            raise ValueError(
                f"{_where(channel, 'BSWV', key)}: not valid when WVTP is {wvtp} "
                f"(valid for {sorted(valid)}; PG02 §3.4)"
            )
    amps = [k for k in _AMPLITUDE_KEYS if k in bswv]
    if len(amps) > 1:
        raise ValueError(f"{_where(channel, 'BSWV')}: set only one of {amps} (they are the same quantity)")
    levels = [k for k in _LEVEL_KEYS if k in bswv]
    if amps and levels:
        raise ValueError(f"{_where(channel, 'BSWV')}: {amps[0]} cannot be combined with {levels[0]}")
    if "OFST" in bswv and levels:
        # HLEV/LLEV redefine the offset (offset = (HLEV + LLEV) / 2), so OFST would contradict them
        raise ValueError(f"{_where(channel, 'BSWV')}: OFST cannot be combined with {levels[0]}")
    if "FRQ" in bswv and "PERI" in bswv:
        raise ValueError(f"{_where(channel, 'BSWV')}: set only one of FRQ and PERI")


def _validate_limits(
    channel: str, outp: Mapping[str, object], bswv: Mapping[str, object], limits: ModelLimits
) -> None:
    # Measured on the SDG2042X, hardware session 1 (README); models.py says which figures are only datasheet.
    wvtp = bswv.get("WVTP")
    frq = _number(bswv.get("FRQ"))
    if frq is not None and isinstance(wvtp, str) and wvtp in limits.max_freq_hz:
        if frq > limits.max_freq_hz[wvtp]:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'FRQ')}: {frq:g} Hz exceeds {limits.model} {wvtp} "
                f"limit {limits.max_freq_hz[wvtp]:g} Hz"
            )
    # Load factor k: 1 at HiZ (no numeric LOAD in this setup, or 'HZ'), LOAD/(LOAD+50) at a numeric load.
    load = _number(outp.get("LOAD"))
    k = 1.0 if load is None else load / (load + 50)
    load_text = "into HiZ" if load is None else f"into {load:g} ohm"
    max_amp = limits.max_amp_vpp_hiz * k
    max_ofst = limits.max_offset_v_hiz * k
    if frq is not None and frq > _HF_FRQ_HZ:
        # measured, hardware session 1 (README): above 20 MHz the levels must stay within +-5 V
        max_ofst = min(max_ofst, _HF_LEVEL_V)
        max_amp = min(max_amp, 2 * _HF_LEVEL_V)
        load_text += f" above {_HF_FRQ_HZ:g} Hz"
    amp = _number(bswv.get("AMP"))
    if amp is not None and not _AMP_MIN <= amp <= max_amp:
        raise ValueError(
            f"{_where(channel, 'BSWV', 'AMP')}: {amp:g} Vpp outside {_AMP_MIN:g}..{max_amp:g} Vpp "
            f"({limits.model} {load_text})"
        )
    ofst = _number(bswv.get("OFST"))
    if ofst is not None:
        if abs(ofst) > max_ofst:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'OFST')}: {ofst:g} V exceeds {limits.model} limit +-{max_ofst:g} V {load_text}"
            )
        if amp is not None and abs(ofst) + amp / 2 > max_ofst:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'OFST')}: {ofst:g} V with AMP {amp:g} Vpp exceeds "
                f"{limits.model} limit +-{max_ofst:g} V {load_text}"
            )
    hlev = _number(bswv.get("HLEV"))
    llev = _number(bswv.get("LLEV"))
    if hlev is not None and hlev > max_ofst:
        raise ValueError(
            f"{_where(channel, 'BSWV', 'HLEV')}: {hlev:g} V exceeds {limits.model} limit {max_ofst:g} V {load_text}"
        )
    if llev is not None and llev < -max_ofst:
        raise ValueError(
            f"{_where(channel, 'BSWV', 'LLEV')}: {llev:g} V below {limits.model} limit -{max_ofst:g} V {load_text}"
        )
    if hlev is not None and llev is not None and hlev - llev < _LEVEL_GAP:
        raise ValueError(
            f"{_where(channel, 'BSWV', 'HLEV')}: HLEV {hlev:g} V minus LLEV {llev:g} V is below {_LEVEL_GAP:g} V "
            f"({limits.model})"
        )


def _order_mod_group(group: str, params: Mapping[str, object]) -> list[tuple[str, str, object]]:
    """One MDWV, SWWV or BTWV group: ``STATE`` on first, then its leading keys, then the rest as given."""
    state = params.get("STATE")
    if "STATE" in params and not _is_on(state):
        return [(group, "STATE", state)]  # a STATE off is the only command of the group
    leading = {"MDWV": ("TYPE", "SRC"), "SWWV": ("START", "STOP"), "BTWV": ("GATE_NCYC", "TRSR")}[group]
    triples: list[tuple[str, str, object]] = [(group, "STATE", state)] if "STATE" in params else []
    triples.extend((group, k, params[k]) for k in leading if k in params)
    if group == "SWWV" and all(k in params for k in leading):
        # START is clamped by the old STOP and the other way round, so START again after STOP (README)
        triples.append((group, "START", params["START"]))
    triples.extend((group, k, v) for k, v in params.items() if k != "STATE" and k not in leading)
    return triples


def order_setup(channel_setup: Mapping[str, Mapping[str, object]]) -> list[tuple[str, str, object]]:
    """``(group, key, value)`` triples in a safe write order for one channel.

    1 OUTP STATE if off; 2 OUTP LOAD; 3 OUTP PLRT; 4 BSWV WVTP, then ARWV, SRATE MODE, SRATE VALUE (selecting an
    arbitrary wave applies its stored parameters, so it precedes everything below); 5 BSWV FRQ or PERI; 6 BSWV AMP/AMPVRMS/AMPDBM
    then OFST, or HLEV then LLEV; 7 other BSWV keys in the caller's order; 8 OUTP STATE if on.
    LOAD precedes the amplitude because switching LOAD rescales the displayed levels (measured, hardware
    session 1, README).
    """
    outp = channel_setup.get("OUTP", {})
    bswv = channel_setup.get("BSWV", {})
    first: list[tuple[str, str, object]] = []
    last: list[tuple[str, str, object]] = []
    if "STATE" in outp:
        (last if _is_on(outp["STATE"]) else first).append(("OUTP", "STATE", outp["STATE"]))
    triples = list(first)
    for key in ("LOAD", "PLRT"):  # PG02 §3.3
        if key in outp:
            triples.append(("OUTP", key, outp[key]))
    bswv_order = ("WVTP", "FRQ", "PERI", *_AMPLITUDE_KEYS, "OFST", *_LEVEL_KEYS)  # PG02 §3.4
    if "WVTP" in bswv:
        triples.append(("BSWV", "WVTP", bswv["WVTP"]))
    # The selection of an ARB applies the parameters stored with the file, so it precedes the carrier parameters;
    # then the mode and the sample rate, which set the frequency   # measured, arb recon A (A5, A11)
    arwv = channel_setup.get("ARWV", {})
    srate = channel_setup.get("SRATE", {})
    triples.extend(("ARWV", k, arwv[k]) for k in ("INDEX", "NAME") if k in arwv)
    triples.extend(("SRATE", k, srate[k]) for k in ("MODE", "VALUE") if k in srate)
    for key in bswv_order[1:]:
        if key in bswv:
            triples.append(("BSWV", key, bswv[key]))
    triples.extend(("BSWV", k, v) for k, v in bswv.items() if k not in bswv_order)
    # Groups that are switched off go first: STATE,OFF of any of the three commands switches the active
    # mode off, whichever it is (measured, modulation recon, README).
    present = [group for group in MOD_GROUPS if group in channel_setup]
    for group in sorted(present, key=lambda g: is_on(channel_setup[g].get("STATE"))):
        triples.extend(_order_mod_group(group, channel_setup[group]))
    triples.extend(last)
    return triples


# ------------------------------------------------------------------------------------------------------------
# Arbitrary waveform data and lists (PG02 §3.31 STL, §3.32 WVDT). Pure functions, no numpy.
# ------------------------------------------------------------------------------------------------------------


def encode_samples(samples: bytes | Sequence[int]) -> bytes:
    """Samples as the bytes ``WVDT`` wants: little-endian signed 16-bit (PG02 §3.32 example; measured, arb recon A, A6).

    ``bytes`` pass through; a sequence of ints in -32768..32767 is packed. The result must have an even length (an
    odd count corrupts the data, B2), at least 4 bytes (2 points, A8) and at most 16 000 000 bytes (8 000 000
    points, the PG02 maximum, B5), else ``ValueError``.
    """
    if isinstance(samples, bytes | bytearray):
        data = bytes(samples)
    else:
        values = list(samples)
        for index, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"sample {index} is not an int: {value!r}")
        if values and (min(values) < -32768 or max(values) > 32767):
            raise ValueError(f"samples must be in -32768..32767, got {min(values)}..{max(values)}")
        packed = array("h", values)
        if sys.byteorder == "big":
            packed.byteswap()
        data = packed.tobytes()
    if len(data) % 2:
        raise ValueError(f"odd byte count {len(data)}: the generator corrupts the data (a point is two bytes)")
    if len(data) < WVDT_MIN_BYTES:
        raise ValueError(f"{len(data)} bytes is too short: at least {WVDT_MIN_BYTES} bytes (2 points)")
    if len(data) > WVDT_MAX_BYTES:
        raise ValueError(f"{len(data)} bytes is too long: at most {WVDT_MAX_BYTES} bytes (8 000 000 points)")
    return data


def build_wvdt_header(
    channel: int,
    name: str,
    *,
    freq: float | None = None,
    ampl: float | None = None,
    ofst: float | None = None,
    phase: float | None = None,
) -> str:
    """``C1:WVDT WVNM,<name>[,FREQ,<f>][,AMPL,<vpp>][,OFST,<v>][,PHASE,<deg>],WAVEDATA,`` (PG02 §3.32).

    Only the parameters given are written; a header-less upload is accepted (measured, arb recon B, B1). The data
    follows the header, then ``\\n``.
    """
    ch = channel_name(channel)
    parts = [f"WVNM,{_check_arb_name(f'{ch} WVDT WVNM', name)}"]  # PG02 §3.32
    for key, value in (("FREQ", freq), ("AMPL", ampl), ("OFST", ofst), ("PHASE", phase)):  # PG02 §3.32
        if value is not None:
            parts.append(f"{key},{format_value(value)}")
    return f"{ch}:WVDT " + ",".join(parts) + ",WAVEDATA,"  # PG02 §3.32


def parse_wvdt_reply(raw: bytes) -> tuple[dict[str, str | int], bytes]:
    """Split a ``WVDT?`` reply into its header fields and the data (PG02 §3.32; layout measured, arb recon A, A8).

    The reply is ``WVDT POS, /Local, WVNM, htf_b1, LENGTH, 128B, TYPE, 6, WAVEDATA,`` + ``LENGTH`` bytes + ``\\n``.
    The keys ``POS``, ``WVNM``, ``LENGTH``, ``TYPE`` are kept as strings and ``'length'`` is the byte count as an
    ``int``. An empty reply means the wave does not exist (B8).
    """
    if not raw:
        raise ProtocolError("no such wave")  # measured, arb recon B (B8)
    marker = b"WAVEDATA,"
    cut = raw.find(marker)
    if cut < 0:
        raise ProtocolError(f"no WAVEDATA in the WVDT reply: {raw[:80]!r}")
    head = raw[:cut].decode("ascii", errors="replace").strip()
    tokens = [t.strip() for t in head.removeprefix("WVDT").split(",")]
    fields: dict[str, str | int] = {}
    for key, val in zip(tokens[0::2], tokens[1::2], strict=False):
        fields[key] = val
    length_text = fields.get("LENGTH")
    if not isinstance(length_text, str) or re.fullmatch(r"\d+B", length_text) is None:
        raise ProtocolError(f"no LENGTH in the WVDT reply header: {head!r}")
    length = int(length_text[:-1])
    fields["length"] = length
    data = raw[cut + len(marker) :]
    if data.endswith(b"\n"):
        data = data[:-1]  # exactly one terminator
    if len(data) != length:
        raise ProtocolError(f"WVDT reply header says {length} bytes, got {len(data)}")
    return fields, data


def parse_stl(reply: str) -> list[str]:
    """``STL WVNM`` -> ``[]``, ``STL WVNM,a,b`` -> ``['a', 'b']`` (PG02 §3.31 ``STL? USER``; measured, arb recon A, A7)."""
    header, _, body = reply.strip().partition(" ")
    tokens = [t.strip() for t in body.split(",")]
    if header != "STL" or tokens[0] != "WVNM":
        raise ProtocolError(f"expected 'STL WVNM,...', got {reply!r}")
    return [t for t in tokens[1:] if t]


def parse_stl_builtin(reply: str) -> dict[int, str]:
    """``STL M0, StairUp, M1, StairDn, ...`` -> ``{0: 'StairUp', 1: 'StairDn', ...}`` (PG02 §3.31 ``STL? BUILDIN``)."""
    header, _, body = reply.strip().partition(" ")
    if header != "STL":
        raise ProtocolError(f"expected 'STL M0, name, ...', got {reply!r}")
    tokens = [t.strip() for t in body.split(",")]
    if len(tokens) % 2:
        raise ProtocolError(f"odd number of tokens in the STL reply: {reply[:80]!r}")
    result: dict[int, str] = {}
    for key, name in zip(tokens[0::2], tokens[1::2], strict=True):
        if re.fullmatch(r"M\d+", key) is None:
            raise ProtocolError(f"expected M<n> in the STL reply, got {key!r}")
        result[int(key[1:])] = name
    return result
