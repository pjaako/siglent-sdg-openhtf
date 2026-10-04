"""Pure SCPI helpers for the Siglent SDG2000X: build, parse, units, tolerances, validation, ordering.

No I/O. The only SCPI source is the official guide PG02-E05C (``docs/PG02-E05C.txt``); every command
literal and key table below names its PG02 section. Nothing that PG02 does not define is built here.
"""

import math
import re
from collections.abc import Mapping
from typing import NamedTuple

from .models import ModelLimits

Setup = Mapping[str, Mapping[str, Mapping[str, object]]]  # 'C1' -> 'BSWV'|'OUTP' -> key -> value

CHANNELS = ("C1", "C2")  # PG02 §3.3/§3.4 <channel>:={C1,C2}
GROUPS = ("OUTP", "BSWV")  # PG02 §3.3 (OUTPut), §3.4 (BSWV)

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

_ALL_TYPES = WAVE_TYPES
_NOT_NOISE_DC = WAVE_TYPES - {"NOISE", "DC"}

# Valid BSWV keys per WVTP, from the "Description" column of PG02 §3.4 ("Not valid when WVTP is ...",
# "Only settable when WVTP is ..."). MAX_OUTPUT_AMP and WVTP: PG02 states no restriction. DLY: PG02 states no
# restriction either, but only PULSE has a delay parameter in the reply; restricted to PULSE so that the
# validator and the fake agree (hypothesis until hardware session 1).
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
    "DLY": frozenset({"PULSE"}),  # PG02 §3.4; PULSE-only is a hypothesis until hardware session 1
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

# Comparison tolerances for read-back verification.
# hypothesis until hardware session 1: the generator's rounding of echoed values is not measured yet.
DEFAULT_REL_TOL = 1e-6  # hypothesis until hardware session 1
REL_TOL: dict[str, float] = {"FRQ": 1e-6, "PERI": 1e-6}  # hypothesis until hardware session 1
ABS_TOL: dict[str, float] = {
    **dict.fromkeys(("AMP", "AMPVRMS", "OFST", "HLEV", "LLEV", "STDEV", "MEAN", "MAX_OUTPUT_AMP"), 1e-3),
    **dict.fromkeys(("PHSE", "DUTY", "SYM"), 1e-2),
}  # hypothesis until hardware session 1 (provisional)


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

    bool -> ON/OFF; int -> decimal; float -> ``.9G`` (exponent form such as ``100E6`` is accepted on write,
    PG02 §3.4 example ``BANDWIDTH,100E6``; whether ``1E-06`` is accepted is a hardware item, hypothesis
    until hardware session 1); str unchanged.
    """
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot send non-finite value {value!r}")
        return format(value, ".9G")
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


def build_command(channel: int, group: str, key: str, value: object) -> str:
    """The write command for one ``(group, key, value)`` triple: ``build_outp`` or ``build_bswv`` (PG02 §3.3, §3.4)."""
    if group == "OUTP":
        return build_outp(channel, key, value)
    if group == "BSWV":
        return build_bswv(channel, key, value)
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
            allowed = OUTP_KEYS if group == "OUTP" else BSWV_KEYS
            for key in params:
                if key not in allowed:
                    raise ValueError(
                        f"{_where(channel, group, key)}: not a {group} parameter of the SDG2000X "
                        f"(PG02 {'§3.3' if group == 'OUTP' else '§3.4'}); allowed: {sorted(allowed)}"
                    )
        outp = groups.get("OUTP", {})
        bswv = groups.get("BSWV", {})
        for key, value in outp.items():
            _check_outp_value(_where(channel, "OUTP", key), key, value)
        for key, value in bswv.items():
            _check_bswv_value(_where(channel, "BSWV", key), key, value)
        _validate_bswv_combination(channel, bswv)
        if limits is not None:
            _validate_limits(channel, outp, bswv, limits)


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
    # hypothesis until hardware session 1: limits are datasheet figures (see models.py)
    wvtp = bswv.get("WVTP")
    frq = _number(bswv.get("FRQ"))
    if frq is not None and isinstance(wvtp, str) and wvtp in limits.max_freq_hz:
        if frq > limits.max_freq_hz[wvtp]:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'FRQ')}: {frq:g} Hz exceeds {limits.model} {wvtp} "
                f"limit {limits.max_freq_hz[wvtp]:g} Hz"
            )
    # The reduced limits apply at any numeric load; no LOAD in this setup or 'HZ' means the HiZ limits.
    numeric_load = _number(outp.get("LOAD")) is not None
    load_text = "into a numeric load" if numeric_load else "into HiZ"
    amp = _number(bswv.get("AMP"))
    if amp is not None:
        max_amp = limits.max_amp_vpp_50 if numeric_load else limits.max_amp_vpp_hiz
        if amp > max_amp:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'AMP')}: {amp:g} Vpp exceeds {limits.model} limit {max_amp:g} Vpp {load_text}"
            )
    ofst = _number(bswv.get("OFST"))
    if ofst is not None:
        # hypothesis until hardware session 1: the offset limit halves at a numeric load, like the amplitude
        max_ofst = limits.max_offset_v_hiz / 2 if numeric_load else limits.max_offset_v_hiz
        if abs(ofst) > max_ofst:
            raise ValueError(
                f"{_where(channel, 'BSWV', 'OFST')}: {ofst:g} V exceeds {limits.model} limit +-{max_ofst:g} V {load_text}"
            )


def order_setup(channel_setup: Mapping[str, Mapping[str, object]]) -> list[tuple[str, str, object]]:
    """``(group, key, value)`` triples in a safe write order for one channel.

    1 OUTP STATE if off; 2 OUTP LOAD; 3 OUTP PLRT; 4 BSWV WVTP; 5 BSWV FRQ or PERI; 6 BSWV AMP/AMPVRMS/AMPDBM
    then OFST, or HLEV then LLEV; 7 other BSWV keys in the caller's order; 8 OUTP STATE if on.
    LOAD precedes the amplitude because switching LOAD between HZ and 50 is reported to rescale the displayed
    amplitude (hypothesis until hardware session 1; the safe order costs nothing).
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
    for key in bswv_order:
        if key in bswv:
            triples.append(("BSWV", key, bswv[key]))
    triples.extend(("BSWV", k, v) for k, v in bswv.items() if k not in bswv_order)
    triples.extend(last)
    return triples
