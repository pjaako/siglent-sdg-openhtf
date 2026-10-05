# siglent-sdg-openhtf

OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator (SDG2000X family) over LAN
(VXI-11 or raw socket) or USB, built on PyVISA + pyvisa-py. Sibling of
[rigol-dho-openhtf](https://github.com/pjaako/rigol-dho-openhtf) and built the same way: a thin plug, test
conditions as data, a hardware-free fake for tests, and a README that records what the real instrument does.

**Status: verified on an SDG2042X, firmware 2.01.01.23R7, over VXI-11, 2026-10-05** (`*IDN?`, `*RST`, `OUTP`, `BSWV`,
`apply_setup`). USB and the other models are not verified. See `STATUS.md`.

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
`FALL`, `DLY`, `STDEV`, `MEAN`, `BANDSTATE`, `BANDWIDTH`, `MAX_OUTPUT_AMP`. `AMPDBM` needs a numeric `LOAD`
(it is ignored at `HZ`). `MAX_OUTPUT_AMP` has no visible effect on this firmware.

The plug has no setter per setting. `tearDown()` turns both outputs off and closes the connection; it does
not restore anything else.

## Setup

```bash
uv sync --all-extras --dev            # Python 3.13, openhtf, pyvisa, pyvisa-py, pyusb, pytest, mypy
uv run pytest -q && uv run mypy       # no hardware needed
uv run python example_test.py --fake  # the example against the fake generator
uv run python example_test.py --resource 192.0.2.10   # real generator over LAN (VXI-11)
```

Resource names: a bare IP or hostname becomes `TCPIP0::<ip>::inst0::INSTR` (VXI-11); a full VISA name is
used as is; an empty name means the first USB instrument with vendor id `0xF4EC`. VXI-11 is what was
verified. The raw socket `TCPIP0::<ip>::5025::SOCKET` is the alternative in PG02 §1.2.4; this unit refused
the connection on port 5025. Config key: `siglent_sdg_resource`.

## Station demo

`examples/station_demo.py` drives this plug and the scope plug of `rigol-dho-openhtf` (optional extra
`station`, installed by `uv sync --all-extras --dev`) in one OpenHTF test. The generator feeds a signal into a
Rigol DHO814, the scope measures Vpp and frequency, and the readings are recorded with limits (+-5 % on Vpp,
+-1 % on frequency). The steps are data (`STEPS` at the top of the file).

```bash
uv run python examples/station_demo.py --fake                   # fake generator and fake scope
uv run python examples/station_demo.py --generator 192.0.2.10 [--scope NAME]   # real instruments
```

Assumed wiring: generator CH1 to scope CH1, 1:1 BNC cable, no terminator; the scope input is 1 Mohm, so the
generator sees an open circuit and the steps use `LOAD,HZ`. Input guard: a step whose open-circuit peak
`(|OFST| + AMP/2) / k` exceeds 5 V, or that has no `AMP`, is refused with `ValueError` before anything is sent.
The scope's own settings are saved when its plug is created and restored at the end (expect a
`set_state: setup block not loaded` warning in the log: the scope drops the first block after a measurement).

## Facts (measured on the generator)

SDG2042X, firmware 2.01.01.23R7, VXI-11. The verbatim session is `tests/data/hardware_session_1.txt`; the fake
replays it in `tests/test_hardware_session_1.py`.

- `*IDN?` answers `Siglent Technologies,SDG2042X,<serial>,2.01.01.23R7`. `*OPC?` answers `1`.
- Port 5025 refuses the connection. Port 5024 (telnet) accepts one; it is not used.
- `*RST` returns at once. The next query shows the default state and takes about 120 ms. It switches both
  outputs off, `LOAD` to `HZ`, `PLRT` to `NOR`.
- Timing: a write takes 4 to 10 ms, a `WVTP` change 15 to 30 ms, `OUTP ON` 80 to 260 ms (relay), a query 2 to
  8 ms. `apply_setup` of the example setup: 240 ms.
- Default of both channels: `WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,4V,AMPVRMS,1.414Vrms,OFST,0V,HLEV,2V,LLEV,-2V,PHSE,0`
  and `OUTP OFF,LOAD,HZ,PLRT,NOR`. Type defaults: SQUARE `DUTY` 50; RAMP `SYM` 50; PULSE `DUTY` 20 (`WIDTH`
  0.0002 at 1 kHz), `RISE` and `FALL` 8.4e-09, `DLY` 0; NOISE `BANDSTATE` OFF, `BANDWIDTH` 120000000; DC `OFST` 0.
- Reply key order of `BSWV?`:
  - SINE: `WVTP,FRQ,PERI,AMP,AMPVRMS,[AMPDBM,]OFST,HLEV,LLEV,PHSE`; SQUARE adds `DUTY`, RAMP adds `SYM`.
  - PULSE: `WVTP,FRQ,PERI,AMP,AMPVRMS,[AMPDBM,]OFST,HLEV,LLEV,DUTY,WIDTH,RISE,FALL,DLY` (no `PHSE`).
  - ARB: `WVTP,FRQ,PERI,AMP,OFST,HLEV,LLEV,PHSE` (no `AMPVRMS`, no `AMPDBM`, also at 50 ohm).
  - NOISE: `WVTP,STDEV,MEAN,BANDSTATE`, plus `BANDWIDTH` only while `BANDSTATE` is `ON`. DC: `WVTP,OFST`.
  - There is no `MAX_OUTPUT_AMP` token and there are no dangling keys.
- `AMPDBM` is in the reply only while `LOAD` is numeric.
- Units: `FRQ` `HZ`, `PERI` `S`, `AMP`/`OFST`/`HLEV`/`LLEV`/`STDEV`/`MEAN` `V`, `AMPVRMS` `Vrms`, `AMPDBM`
  `dBm`, `RISE`/`FALL` `S`, `BANDWIDTH` `HZ`. `PHSE`, `DUTY`, `SYM`, `WIDTH`, `DLY` have no unit.
- Numbers are C `%g`: 6 significant digits, lower-case exponent (`8.4e-09`, `1e+06`). `FRQ` and `BANDWIDTH` show
  10 digits (`12345678.12HZ`, `40000000HZ`, `1e-06HZ`). `LOAD` is an integer; `LOAD,1234.5` reads `1234`.
- `AMPVRMS` is `AMP` times a fixed factor: SINE 0.3535 (4 V reads `1.414Vrms`), SQUARE and PULSE 0.5, RAMP
  1/3.464. `AMPDBM` is `10*log10(Vrms^2 / LOAD / 0.001)`; it is computed, so `AMPDBM,0` can read
  `9.64327e-16dBm`.
- Writes accept exponent forms (`1E3`, `1E-06`, `50E6`), more digits than the echo shows, and several pairs in
  one command (`C1:OUTP ON,LOAD,50,PLRT,INVT`), applied left to right.
- A value is echoed as written when it has at most 6 significant digits and is in range. `AMP,1.23456789`
  reads `1.23457V`.

## Things the manual does not tell you

- `SYST:ERR?` (not in PG02) answered `+0, No error` and `*CLS` was accepted, also right after clamped writes.
  So no reply says anything about a rejected value. Both stay undefined: the plug never sends them and the
  fake raises `ValueError` for them.
- Values are clamped without any error, so `apply_setup(..., verify=True)` is the only way to notice.
  Port 5025 refused the connection on this unit, use VXI-11.
- A key that is not valid for the current `WVTP` is ignored without any sign (`DLY`, `DUTY`, `SYM` on SINE;
  `PHSE` on PULSE; `FRQ`, `AMP` on NOISE; `AMP` on DC). `AMPDBM` is ignored while `LOAD` is `HZ`.
- `FRQ` above the type's maximum becomes the maximum: SINE 40 MHz, SQUARE and PULSE 25 MHz, RAMP 1 MHz, ARB
  20 MHz. A `WVTP` change clamps it the same way. `FRQ,0` and negative values are accepted and read
  `FRQ,0HZ,PERI,infS`; the plug's validator rejects them.
- Above 20 MHz (from 20000001 Hz) every level must stay within +-5 V: 10 Vpp at `HZ` instead of 20. Raising
  the frequency clamps an amplitude that is already set, and it stays clamped when the frequency goes back.
- `LOAD` below 50 becomes 50, above 100000 becomes 100000.
- `AMP` below 0.002 becomes 0.002. The maximum is `min(20*k, 2*(10*k - abs(OFST)))` with `k = 1` at `HZ` and
  `k = LOAD/(LOAD+50)` at a numeric load (10 Vpp at 50 ohm, 12 at 75 ohm, 19.99 at 100 kohm).
- `OFST` is clamped to `+-(10*k - AMP/2)`; for DC to `+-10*k`. `HLEV` is clamped to `LLEV + 0.002 .. 10*k` and
  `LLEV` to `-10*k .. HLEV - 0.002`, and the other level stays. So `HLEV` below `LLEV` is not rejected.
- SQUARE `DUTY` is clamped to `100*16.3e-9*FRQ .. 100 - that` (40.75 to 59.25 at 25 MHz), also when the
  frequency or the type changes, and it stays clamped. PULSE `RISE` and `FALL` below 8.4e-09 become 8.4e-09.
  `SYM` is clamped to 0..100, NOISE `BANDWIDTH` to 20 MHz..120 MHz, PULSE `DLY` to plus or minus one period.
  `PHSE` beyond 360 wraps and keeps its sign (`400` reads 40, `-400` reads -40); `-90` and `360` are kept.
- `LOAD` rescales what is displayed: every level (`AMP`, `OFST`, `HLEV`, `LLEV`, DC `OFST`, `STDEV`, `MEAN`) is
  multiplied by `k_new / k_old`. `HZ` to 50 halves, 50 to `HZ` doubles. Not modelled: with loads other than 50 the result is off
  by up to 0.3 mV and looks rounded to 0.1 mV (`LOAD,75` showed `AMP,2.4001V` for an exact 2.4, `LOAD,600`
  showed `3.6925V` for 3.6923). Set `LOAD` first, then the levels; `apply_setup` does.
- `AMPVRMS,<v>` sets `AMP = v / factor`; `AMPDBM,<d>` at a numeric load sets `Vrms = sqrt(LOAD*0.001*10^(d/10))`.
  Both are echoed as written. Measured for SINE, SQUARE and RAMP; ARB ignores `AMPVRMS`.
- PULSE `WIDTH` and `DUTY` are one quantity (`DUTY = 100*WIDTH*FRQ`). The width in seconds survives a frequency
  change. It is clamped to `16.3e-9 .. PERI - 16.3e-9`. Not modelled: with a long edge the maximum is lower
  (`WIDTH,0.002` at 1 kHz with `FALL` 1e-06 read `0.000999364`).
- Type-specific values survive a `WVTP` change (SQUARE `DUTY,30`, to SINE, back: still 30). SQUARE duty and
  PULSE duty are separate values.
- Parameters are views of shared state. NOISE `STDEV` is `0.0575 * AMP`, `MEAN` is `OFST`. DC has its own
  offset. Leaving PULSE overwrites the phase with `-360*DLY*FRQ` (`DLY,0.0002` at 1 kHz, then SINE reads
  `PHSE,-72`; with `DLY` 0 a phase of 90 comes back as 0). PULSE keeps its own delay.
- Small things the fake does not model: `PERI` is computed in single precision (20001220 Hz reads
  `4.99969e-08S`); a PULSE duty at the width limit read `99.992` where the formula gives 99.9919.
- `MAX_OUTPUT_AMP,5` was accepted, is not echoed and had no visible effect: `AMP,10` was still accepted.
- `PLRT,INVT` inverts the offset as well as the waveform (measured with a scope: +0.5 V set, -0.5 V out).
- PyVISA gives the whole process one ResourceManager per backend. The plug never closes it: closing it
  closes the sessions of every other instrument plug in the same test.
- Still open: USB via pyvisa-py (reports from other projects say it is unreliable); the other two models.

## Files

```
src/siglent_sdg_openhtf/   plug.py (SiglentSdgPlug), scpi.py (pure helpers), models.py (limits), fake_resource.py
tests/                     pytest suite, no hardware
example_test.py            minimal OpenHTF test, --fake
examples/station_demo.py   generator + scope station test, --fake
tools/probe.py             hardware probe run by the owner; results go to README
docs/                      official Siglent programming guides (PDF) and the text extraction used by agents
AGENTS.md SPEC*.md STATUS.md   behaviour protocol, contracts, handoff state
```

## Licence

MIT. Use for anything; keep the copyright notice.
