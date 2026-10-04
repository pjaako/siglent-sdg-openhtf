"""Model limits for the SDG2000X family.

The numbers below come from the SDG2000X datasheet summary, NOT from PG02 (the programming guide
only says "refer to the datasheet for the range of valid values", PG02 §3.4). They are used to reject
requests the generator would silently clamp.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple


class ModelLimits(NamedTuple):
    """Setting limits of one generator model."""

    model: str
    channels: int
    max_freq_hz: Mapping[str, float]  # per WVTP; missing entry = no check
    max_amp_vpp_hiz: float
    max_amp_vpp_50: float
    max_offset_v_hiz: float


# Datasheet figures, not PG02 (PG02 §3.4 FRQ/AMP/OFST only say "Refer to the datasheet for the range of
# valid values"). The SDG2042X figures were measured in hardware session 1 (README); the SINE limits of the
# other two models are hypotheses until a session on those models.
_SDG2042X = ModelLimits(
    model="SDG2042X",
    channels=2,
    max_freq_hz=MappingProxyType({"SINE": 40e6, "SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6, "ARB": 20e6}),
    max_amp_vpp_hiz=20.0,
    max_amp_vpp_50=10.0,
    max_offset_v_hiz=10.0,
)
_SDG2082X = _SDG2042X._replace(
    model="SDG2082X",
    max_freq_hz=MappingProxyType({"SINE": 80e6, "SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6, "ARB": 20e6}),
)  # hypothesis until hardware session 1 (SINE limit)
_SDG2122X = _SDG2042X._replace(
    model="SDG2122X",
    max_freq_hz=MappingProxyType({"SINE": 120e6, "SQUARE": 25e6, "PULSE": 25e6, "RAMP": 1e6, "ARB": 20e6}),
)  # hypothesis until hardware session 1 (SINE limit)

# SDG2042X measured, the others hypotheses (see above)
MODELS: Mapping[str, ModelLimits] = MappingProxyType(
    {m.model: m for m in (_SDG2042X, _SDG2082X, _SDG2122X)}
)


def limits_for(model: str) -> ModelLimits:
    """Limits for an ``*IDN?`` model string (PG02 §3.1.1 <model>).

    Exact match (case-insensitive, stripped). An unlisted ``SDG2...`` model gets the SDG2042X limits
    (the most restrictive; hypothesis until a session on that model). Anything else raises ``ValueError``.
    """
    key = model.strip().upper()
    if key in MODELS:
        return MODELS[key]
    if key.startswith("SDG2"):
        return MODELS["SDG2042X"]
    raise ValueError(f"unsupported generator model {model!r}; this package supports the SDG2000X family")
