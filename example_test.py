"""Minimal OpenHTF test using the Siglent SDG2000X plug.

Usage:
    python example_test.py --fake                   # no hardware
    python example_test.py --resource 192.0.2.10    # real generator over LAN (VXI-11)
    python example_test.py --resource TCPIP0::192.0.2.10::5025::SOCKET   # raw socket (PG02 section 1.2.4)
"""

import argparse
import sys
from typing import Any

import openhtf as htf
from openhtf.util import units

from siglent_sdg_openhtf import SiglentSdgPlug
from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from siglent_sdg_openhtf.plug import CONF
from siglent_sdg_openhtf.scpi import Setup

# Test conditions as data: channel -> OUTP / BSWV -> PG02 mnemonic -> value (PG02 sections 3.3 and 3.4).
SETUP: Setup = {
    "C1": {
        "OUTP": {"LOAD": 50, "STATE": True},
        "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
    }
}


@htf.measures(
    htf.Measurement("frequency_hz").with_units(units.HERTZ).in_range(999, 1001),
    htf.Measurement("amplitude_vpp").with_units(units.VOLT).in_range(1.99, 2.01),
    htf.Measurement("output_state").equals("ON"),
)
@htf.plug(generator=SiglentSdgPlug)
def generate_sine(test: Any, generator: SiglentSdgPlug) -> None:
    """Apply the setup (validate, write, read back, raise on any mismatch), then record what the generator reports."""
    generator.apply_setup(SETUP)
    wave = generator.get_basic_wave(1)
    test.measurements.frequency_hz = wave["FRQ"]
    test.measurements.amplitude_vpp = wave["AMP"]
    test.measurements.output_state = generator.get_output(1)["STATE"]


class FakePlug(SiglentSdgPlug):
    """The plug on an in-memory fake generator, for runs without hardware."""

    def __init__(self) -> None:
        super().__init__(resource=FakeSdgResource())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fake", action="store_true", help="use the fake generator (no hardware)")
    parser.add_argument("--resource", metavar="NAME", help="bare IP/hostname or VISA resource name of the generator")
    args = parser.parse_args(argv)
    if args.resource:
        # After importing the plug module: a key loaded before it is declared is lost.
        CONF.load(siglent_sdg_resource=args.resource)
    phase = generate_sine.with_plugs(generator=FakePlug) if args.fake else generate_sine
    passed = bool(htf.Test(phase).execute(test_start=lambda: "example_dut"))
    print("example: PASS" if passed else "example: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
