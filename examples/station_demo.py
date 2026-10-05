"""Station demo: the SDG2042X feeds a signal into a Rigol DHO814 and an OpenHTF test checks it at the connector.

Wiring: generator CH1 to scope CH1, 1:1 BNC cable, no terminator (the scope input is 1 Mohm, so the generator
sees an open circuit and every step uses LOAD,HZ, except the one that checks that LOAD only changes the display).
The scope's own settings are saved when its plug is created and restored by its tearDown().

Usage:
    python examples/station_demo.py --fake                          # no hardware
    python examples/station_demo.py --generator 192.0.2.10          # generator over LAN, scope found on USB
    python examples/station_demo.py --generator 192.0.2.10 --scope USB0::6833::1101::SERIAL::0::INSTR
"""

import argparse
import re
import sys
import time
from typing import Any, NamedTuple

import openhtf as htf
from openhtf.util import units

from rigol_dho_openhtf import RigolDhoPlug
from rigol_dho_openhtf.fake_resource import FakeDhoResource
from rigol_dho_openhtf.plug import CONF as SCOPE_CONF
from siglent_sdg_openhtf import SiglentSdgPlug
from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.plug import CONF as GENERATOR_CONF

VPP_TOLERANCE = 0.05  # the scope reads 1 to 3 % high on Vpp at these scales
FREQUENCY_TOLERANCE = 0.01  # measured: 0.44 % off on frequency
MAX_INPUT_V = 5.0  # open-circuit peak the scope input may see
SETTLE_S = 1.5  # wait before the first query of the measurement items (the first answer is 9.9E+37)
MEASURE_S = 0.7  # wait between that first query and the reading
OFF_LIMIT_V = 0.2  # the scope read 0.05 V of noise with the output off

# Test conditions as data. generator: the C1 part of a generator setup (PG02 sections 3.3 and 3.4);
# scope: SCPI header -> value for RigolDhoPlug.apply_setup; expect: measurement name -> nominal value.
SCOPE_500MV_200US = {":TIM:MAIN:SCAL": 200e-6, ":CHAN1:SCAL": 0.5, ":CHAN1:OFFS": 0.0, ":TRIG:EDGE:LEV": 0.0}
STEPS: list[dict[str, Any]] = [
    {
        "name": "sine_1khz",
        "generator": {
            "OUTP": {"LOAD": "HZ", "STATE": True},
            "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
        },
        "scope": SCOPE_500MV_200US,
        "expect": {"vpp": 2.0, "frequency_hz": 1000.0},
    },
    {
        "name": "sine_1khz_load_50",  # LOAD only changes what the generator displays: the scope still sees 2 Vpp
        "generator": {
            "OUTP": {"LOAD": 50, "STATE": True},
            "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 1.0, "OFST": 0.0},
        },
        "scope": SCOPE_500MV_200US,
        "expect": {"vpp": 2.0, "frequency_hz": 1000.0},
    },
    {
        "name": "square_1khz",
        "generator": {
            "OUTP": {"LOAD": "HZ", "STATE": True},
            "BSWV": {"WVTP": "SQUARE", "FRQ": 1000.0, "AMP": 3.0, "OFST": 1.5, "DUTY": 30.0},
        },
        "scope": {":TIM:MAIN:SCAL": 200e-6, ":CHAN1:SCAL": 1.0, ":CHAN1:OFFS": -1.5, ":TRIG:EDGE:LEV": 1.5},
        "expect": {"vpp": 3.0, "frequency_hz": 1000.0},
    },
    {
        "name": "sine_1mhz",
        "generator": {
            "OUTP": {"LOAD": "HZ", "STATE": True},
            "BSWV": {"WVTP": "SINE", "FRQ": 1e6, "AMP": 4.0, "OFST": 0.0},
        },
        "scope": {":TIM:MAIN:SCAL": 200e-9, ":CHAN1:SCAL": 1.0, ":CHAN1:OFFS": 0.0, ":TRIG:EDGE:LEV": 0.0},
        "expect": {"vpp": 4.0, "frequency_hz": 1e6},
    },
]

# Common scope settings, applied once before the first step. The scope is not reset.
SCOPE_COMMON: dict[str, Any] = {
    ":CHAN1:DISP": True,
    ":CHAN1:PROB": 1,
    ":CHAN1:COUP": "DC",
    ":TRIG:MODE": "EDGE",
    ":TRIG:EDGE:SOUR": "CHAN1",
    ":TRIG:EDGE:SLOP": "POS",
    ":TRIG:SWE": "AUTO",
}

NO_READING = 9.9e37  # what the scope answers on the first query of an item and when the trace is clipped


def check_input_protection(step: dict[str, Any]) -> None:
    """Refuse a step whose open-circuit peak could exceed MAX_INPUT_V at the scope input (ValueError)."""
    name = step["name"]
    bswv = step["generator"].get("BSWV", {})
    if "AMP" not in bswv:
        raise ValueError(f"step {name!r}: no AMP in the generator setup; the demo only knows AMP and OFST")
    load = step["generator"].get("OUTP", {}).get("LOAD", "HZ")
    k = 1.0 if str(load).upper() == "HZ" else float(load) / (float(load) + 50.0)
    peak = (abs(float(bswv.get("OFST", 0.0))) + float(bswv["AMP"]) / 2) / k
    if peak > MAX_INPUT_V:
        raise ValueError(f"step {name!r}: open-circuit peak {peak:g} V exceeds the {MAX_INPUT_V:g} V input limit")


def read_scope(scope: Any, items: list[str], settle_s: float, measure_s: float) -> dict[str, float]:
    """Wait, query every item once and discard the answer, wait, read every item (no retry)."""
    time.sleep(settle_s)
    for item in items:
        scope.measure(item, 1)
    time.sleep(measure_s)
    return {item: scope.measure(item, 1) for item in items}


def make_step_phase(step: dict[str, Any], settle_s: float, measure_s: float) -> Any:
    """One phase from a step dict: set the generator and the scope, read the scope, record against limits."""
    expect = step["expect"]
    vpp, frequency = expect["vpp"], expect["frequency_hz"]

    @htf.PhaseOptions(name=step["name"])
    @htf.measures(
        htf.Measurement("vpp").with_units(units.VOLT).in_range(vpp * (1 - VPP_TOLERANCE), vpp * (1 + VPP_TOLERANCE)),
        htf.Measurement("frequency_hz")
        .with_units(units.HERTZ)
        .in_range(frequency * (1 - FREQUENCY_TOLERANCE), frequency * (1 + FREQUENCY_TOLERANCE)),
    )
    @htf.plug(generator=SiglentSdgPlug, scope=RigolDhoPlug)
    def phase(test: Any, generator: SiglentSdgPlug, scope: Any) -> None:
        generator.apply_setup({"C1": step["generator"]})
        scope.apply_setup(step["scope"])
        readings = read_scope(scope, ["VPP", "FREQ"], settle_s, measure_s)
        test.measurements.vpp = readings["VPP"]
        test.measurements.frequency_hz = readings["FREQ"]

    return phase


@htf.plug(scope=RigolDhoPlug)
def scope_setup(test: Any, scope: Any) -> None:
    scope.apply_setup(SCOPE_COMMON)


def make_output_off_phase(settle_s: float, measure_s: float) -> Any:
    """Switch C1 off and check that the scope sees only noise."""

    @htf.measures(htf.Measurement("vpp_off").with_units(units.VOLT).in_range(maximum=OFF_LIMIT_V))
    @htf.plug(generator=SiglentSdgPlug, scope=RigolDhoPlug)
    def output_off(test: Any, generator: SiglentSdgPlug, scope: Any) -> None:
        generator.apply_setup({"C1": {"OUTP": {"STATE": False}}})  # PG02 §3.3
        test.measurements.vpp_off = read_scope(scope, ["VPP"], settle_s, measure_s)["VPP"]

    return output_off


def build_phases(steps: list[dict[str, Any]], settle_s: float = SETTLE_S, measure_s: float = MEASURE_S) -> list[Any]:
    """Check every step before anything is sent, then build scope_setup, one phase per step and output_off."""
    for step in steps:
        check_input_protection(step)
    return [
        scope_setup,
        *(make_step_phase(step, settle_s, measure_s) for step in steps),
        make_output_off_phase(settle_s, measure_s),
    ]


_NUMBER = re.compile(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")


def _number(text: str) -> float:
    match = _NUMBER.match(text)
    assert match, text
    return float(match.group())


class WiredFakeScope(FakeDhoResource):  # type: ignore[misc]  # the scope package has no type information
    """A fake scope cabled to a fake generator: CH1 measurements follow what the generator is set to.

    The scope's 1 Mohm input sees the open-circuit voltage: VPP = AMP / k with k = 1 at LOAD,HZ and
    LOAD/(LOAD+50) at a numeric load. The first query of an item answers 9.9E+37, like the scope.
    """

    def __init__(self, generator: FakeSdgResource, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._generator = generator
        self._queried: set[str] = set()

    def query(self, cmd: str) -> str:
        answer: str = super().query(cmd)  # logs the command and keeps the dropped-block behaviour
        match = re.fullmatch(r":MEAS:ITEM\? (\w+),CHAN1", cmd.strip())
        if not match:
            return answer
        item = match.group(1)
        first = item not in self._queried
        self._queried.add(item)
        if first:
            return format(NO_READING, ".6E")
        value = self._cable_value(item)
        return answer if value is None else format(value, ".6E")

    def _cable_value(self, item: str) -> float | None:
        outp = self._generator.query("C1:OUTP?").split(" ", 1)[1].split(",")  # PG02 §3.3
        bswv = self._generator.query("C1:BSWV?").split(" ", 1)[1].split(",")  # PG02 §3.4
        if item == "VPP" and outp[0] == "OFF":
            return 0.05
        if outp[0] != "ON" or item not in ("VPP", "FREQ"):
            return None
        load = outp[outp.index("LOAD") + 1]
        k = 1.0 if load == "HZ" else float(load) / (float(load) + 50.0)
        fields = dict(zip(bswv[::2], bswv[1::2]))
        return _number(fields["AMP"]) / k if item == "VPP" else _number(fields["FRQ"])


class FakeStation(NamedTuple):
    """Two plug classes that share one fake generator and one fake scope, and the fakes themselves."""

    generator_plug: type[SiglentSdgPlug]
    scope_plug: type[RigolDhoPlug]
    generator: FakeSdgResource
    scope: WiredFakeScope


def make_fake_station(generator: FakeSdgResource | None = None, scope: WiredFakeScope | None = None) -> FakeStation:
    """Build the fakes (or take the given ones) and plug subclasses that inject them. One station per run."""
    fake_generator = generator if generator is not None else FakeSdgResource()
    fake_scope = scope if scope is not None else WiredFakeScope(fake_generator, signal="clock")

    class FakeGeneratorPlug(SiglentSdgPlug):
        def __init__(self) -> None:
            super().__init__(resource=fake_generator)

    class FakeScopePlug(RigolDhoPlug):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(resource=fake_scope)

    return FakeStation(FakeGeneratorPlug, FakeScopePlug, fake_generator, fake_scope)


def run_station(phases: list[Any], station: FakeStation | None = None) -> bool:
    """Run the phases as one OpenHTF test; with a station, on its fakes."""
    if station is not None:
        phases = [phase.with_plugs(generator=station.generator_plug, scope=station.scope_plug) for phase in phases]
    return bool(htf.Test(*phases).execute(test_start=lambda: "station_dut"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fake", action="store_true", help="use the fake generator and the fake scope (no hardware)")
    parser.add_argument("--generator", metavar="NAME", help="bare IP/hostname or VISA resource name of the generator")
    parser.add_argument("--scope", metavar="NAME", default="", help="VISA resource name of the scope (default: first USB scope)")
    args = parser.parse_args(argv)
    if not args.fake and not args.generator:
        parser.error("give --fake or --generator")
    if args.fake:
        passed = run_station(build_phases(STEPS, 0.0, 0.0), make_fake_station())
    else:
        # After importing the plug modules: a key loaded before it is declared is lost.
        GENERATOR_CONF.load(siglent_sdg_resource=args.generator)
        SCOPE_CONF.load(rigol_dho_resource=args.scope)
        passed = run_station(build_phases(STEPS))
    print("station: PASS" if passed else "station: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
