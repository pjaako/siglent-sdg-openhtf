# siglent-sdg-openhtf

OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator (SDG2000X family) over LAN
(VXI-11 or raw socket) or USB, built on PyVISA + pyvisa-py. Sibling of
[rigol-dho-openhtf](https://github.com/pjaako/rigol-dho-openhtf) and built the same way: a thin plug, test
conditions as data, a hardware-free fake for tests, and a README that records what the real instrument does.

**Status: under construction, not yet run on hardware.** See `STATUS.md`.

```python
import openhtf as htf
from openhtf.util import units
from siglent_sdg_openhtf import SiglentSdgPlug

SETUP = {'C1': {'OUTP': {'LOAD': 50, 'STATE': True},
                'BSWV': {'WVTP': 'SINE', 'FRQ': 1000.0, 'AMP': 2.0, 'OFST': 0.0}}}

@htf.measures(htf.Measurement('frequency_hz').with_units(units.HERTZ).in_range(999, 1001))
@htf.plug(generator=SiglentSdgPlug)
def generate_sine(test, generator):
    generator.apply_setup(SETUP)          # validates, writes in a safe order, reads back, raises on any mismatch
    test.measurements.frequency_hz = generator.get_basic_wave(1)['FRQ']
```

Keys are the SCPI mnemonics of the official programming guide (`docs/`), so every key can be looked up:
`OUTP` (PG02 §3.3): `STATE`, `LOAD` (50..100000 or `HZ`), `PLRT` (`NOR`/`INVT`); `BSWV` (§3.4): `WVTP`,
`FRQ`/`PERI`, `AMP`/`AMPVRMS`/`AMPDBM` + `OFST` or `HLEV` + `LLEV`, `PHSE`, `DUTY`, `SYM`, `WIDTH`, `RISE`,
`FALL`, `DLY`, `STDEV`, `MEAN`, `BANDSTATE`, `BANDWIDTH`, `MAX_OUTPUT_AMP`.

The plug has no setter per setting. `tearDown()` turns both outputs off and closes the connection; it does
not restore anything else.

## Setup

```bash
uv sync --all-extras --dev            # Python 3.13, openhtf, pyvisa, pyvisa-py, pyusb, pytest, mypy
uv run pytest -q && uv run mypy       # no hardware needed
uv run python example_test.py --fake  # the example against the fake generator
uv run python example_test.py --resource 192.0.2.10   # real generator over LAN (VXI-11)
```

Resource names: a bare IP or hostname becomes `TCPIP0::<ip>::inst0::INSTR` (VXI-11); use
`TCPIP0::<ip>::5025::SOCKET` for the raw socket (PG02 §1.2.4); a full VISA name is used as is; an empty name
means the first USB instrument with vendor id `0xF4EC`. Config key: `siglent_sdg_resource`.

## Facts (measured on the generator)

Nothing yet. The plug has not been run on hardware. Everything in the fake and in the tests comes from the
examples in the official programming guide `docs/SDG_Programming Guide_PG02-E05C.pdf`.

## Things the manual does not tell you

Nothing yet. Candidates to verify in the first hardware session (from other projects' reports, unverified):
switching `LOAD` between `HZ` and `50` rescales the displayed amplitude; the generator has no error queue
and silently ignores values it cannot apply; `*OPC?` returns `1` immediately; undefined queries can hang
the VXI-11 service until a power cycle; USB via pyvisa-py on Linux is unreliable.

## Files

```
src/siglent_sdg_openhtf/   plug.py (SiglentSdgPlug), scpi.py (pure helpers), models.py (limits), fake_resource.py
tests/                     pytest suite, no hardware
example_test.py            minimal OpenHTF test, --fake
tools/probe.py             hardware probe run by the owner; results go to README
docs/                      official Siglent programming guides (PDF) and the text extraction used by agents
AGENTS.md SPEC*.md STATUS.md   behaviour protocol, contracts, handoff state
```

## Licence

MIT. Use for anything; keep the copyright notice.
