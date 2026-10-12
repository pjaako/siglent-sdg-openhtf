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
`MDWV` (§3.5): `STATE`, `TYPE` (`AM`, `DSBAM`, `FM`, `PM`, `PWM`, `ASK`, `FSK`, `PSK`), `SRC`, `MDSP`, `FRQ`,
`DEPTH`, `DEVI`, `KFRQ`, `HFRQ`, `PLRT`; the keys must fit the type. `SWWV` (§3.6.1): `STATE`, `TIME`, `START`,
`STOP`, `SWMD`, `DIR`, `TRSR`, `TRMD`, `EDGE`, `CENTER`, `SPAN`, `SYM`, `MARK_STATE`, `MARK_FREQ`, `STARTTIME`,
`ENDTIME`, `BACKTIME`. `BTWV` (§3.7): `STATE`, `GATE_NCYC`, `TRSR`, `TRMD`, `PRD`, `STPS`, `DLAY`, `PLRT`, `TIME`
(an int or `INF`), `EDGE`, `COUNT`. The carrier of all three is the `BSWV` group; the plug never sends `CARR`.
Only one of the three can be on per channel, and a group with keys needs `STATE` on.
`manual_trigger(channel, 'SWWV' | 'BTWV')` sends `MTRIG`.
`ARWV` (§3.9.1): `INDEX` (a built-in, 0..196) or `NAME` (a user wave), exactly one. `SRATE` (§3.40): `MODE`
(`DDS` or `TARB`) and `VALUE` (Sa/s, only with `MODE` `TARB`). Both need `BSWV` `WVTP` `ARB` in the same channel
setup, and `SRATE` `VALUE` cannot be combined with `BSWV` `FRQ`/`PERI` (in TrueArb they are one quantity).
`ARWV` is written right after `WVTP`, before `FRQ`, `AMP`, `OFST`, `PHSE`, because selecting a user wave applies
the parameters stored in its file.

```python
SETUP_BUILTIN = {'C1': {'BSWV': {'WVTP': 'ARB', 'FRQ': 1000.0, 'AMP': 2.0},
                        'ARWV': {'INDEX': 5}}}                # index 5 is Trapezia on this unit (see below)
generator.apply_setup(SETUP_BUILTIN)
generator.get_arb(1)                      # {'INDEX': 5.0, 'NAME': 'Trapezia'}

generator.upload_arb(1, 'my_ramp', samples, freq=1000, ampl=2.0, ofst=0.0, phase=0)   # samples: ints -32768..32767
SETUP_TARB = {'C1': {'BSWV': {'WVTP': 'ARB', 'AMP': 2.0},
                     'ARWV': {'NAME': 'my_ramp'},
                     'SRATE': {'MODE': 'TARB', 'VALUE': 4096000}}}   # 4096 points: 1 kHz
generator.apply_setup(SETUP_TARB)
generator.get_sample_rate(1)              # {'MODE': 'TARB', 'VALUE': 4096000.0}
```

`upload_arb(channel, name, samples, *, freq, ampl, ofst, phase, verify=True)` writes the wave (little-endian
int16, 2 to 8 000 000 points) and, with `verify`, checks `STL? USER` and reads the data back. It does not select
the wave; `apply_setup` with `ARWV` does. Names are 1 to 40 characters of `A-Za-z0-9_-`, without `.bin`.
`read_arb(name_or_index)` returns the header fields and the data bytes, `list_arbs()` the built-ins by index,
`list_user_arbs()` the user wave names (use it as a set: the order is the generator's). The plug raises the VISA
timeout to at least 30 s around uploads and read-backs. There is no delete: only the front panel removes a wave.

```python
SETUP_AM = {'C1': {'BSWV': {'WVTP': 'SINE', 'FRQ': 10000.0, 'AMP': 2.0},
                   'MDWV': {'STATE': True, 'TYPE': 'AM', 'MDSP': 'SINE', 'FRQ': 100.0, 'DEPTH': 80},
                   'OUTP': {'LOAD': 50, 'STATE': True}}}
generator.apply_setup(SETUP_AM)           # carrier first, then the modulation, the output last
generator.get_modulation(1)               # {'STATE': 'ON', 'TYPE': 'AM', 'FRQ': 100.0, 'DEPTH': 80.0, ...}
```

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

- Reply of `MDWV?`, `SWWV?` and `BTWV?`: `<ch>:<CMD> STATE,ON,<own fields>,CARR,<carrier fields>`. The carrier
  fields are the `BSWV?` fields without `PERI`, `HLEV`, `LLEV`, `WIDTH` and the NOISE band fields (`AMPDBM` stays at a numeric `LOAD`).
  With `STATE` off the reply is only `STATE,OFF`.
- `MDWV?` has the type as a bare token: `STATE,ON,AM,MDSP,SINE,SRC,INT,FRQ,100HZ,DEPTH,100,CARR,...`. Own fields:
  AM `MDSP,SRC,FRQ,DEPTH`; DSBAM `MDSP,SRC,FRQ`; FM `MDSP,SRC,FRQ,DEVI` (`DEVI,100HZ`); PM the same (`DEVI,100`);
  PWM the same (`DEVI,0.00019S`); ASK `SRC,KFRQ`; FSK `SRC,KFRQ,HFRQ`; PSK `SRC,KFRQ,PLRT`. `FRQ`, `KFRQ`, `HFRQ`
  print like the carrier frequency (10 digits, `HZ`), the rest with 6 digits.
- `SWWV?`: `STATE,ON,TIME,1S,STOP,1500HZ,START,500HZ,TRSR,INT,TRMD,OFF,SWMD,LINE,DIR,UP,CARR,...`. With
  `TRSR,EXT` there is no `TRMD` and `EDGE,RISE` follows `DIR`.
- `BTWV?`, NCYC: `STATE,ON,PRD,0.01S,STPS,0,TRSR,INT,TRMD,OFF,TIME,1,DLAY,6.04035e-07S,GATE_NCYC,NCYC,CARR,...`.
  `PRD` only with `TRSR,INT`, `TRMD` (`OFF`, `RISE`, `FALL`) not with `EXT`. GATE: `PRD,STPS,TRSR,GATE_NCYC,PLRT` (default `PLRT,NEG`).
  A PULSE carrier has no `STPS`. A NOISE carrier: `STATE,ON,PLRT,POS,CARR,WVTP,NOISE,STDEV,..,MEAN,..`.

## Facts (measured on the generator): arbitrary waveforms (2026-10-12)

SDG2042X, firmware 2.01.01.23R7, VXI-11. Transcripts `tests/data/hardware_arb_1.txt` (recon A) and
`hardware_arb_2.txt` (recon B); the fake replays them in `tests/test_hardware_arb.py` (75 and 69 replies
compared, one listed as not modelled).

- A1. `*RST` leaves both channels at `C1:ARWV INDEX,0,NAME,StairUp`, `WVTP,SINE`, `SRATE MODE,DDS`. The selection is per channel.
- A2. The index table of this unit is PG02's table shifted by two: `STL? BUILDIN` lists 197 entries `M0` to `M196`
  (sorted as strings, `M0, StairUp, M1, StairDn, M10, LogFall, ...`); 0 is StairUp, 1 StairDn, 2 StairUD, 3 Ppulse,
  5 Trapezia, 18 Dlorentz, 196 demo2_16kpts. `STL?` answers like `STL? BUILDIN`. `scpi.BUILTIN_ARBS` has the table.
- A3. `ARWV INDEX,<n>` selects; 198 and 199 are ignored. `NAME` with a built-in name (`StairDn`, `"StairUp"`, `sinc`) is
  ignored. A user wave is selected with `NAME,<name>` and reads `C1:ARWV NAME,<name>.bin` (no `INDEX`). An unknown name is ignored.
- A4. Selecting a wave, or `SRATE MODE,TARB`, switches a SINE channel to `WVTP,ARB`.
- A5. Selecting a user wave applies the parameters stored in its file (`FREQ`, `AMPL`, `OFST`, `PHASE` as uploaded)
  whatever the channel had before. So `ARWV` goes before `FRQ`/`AMP`/`OFST`/`PHSE`.
- A6. `C1:WVDT WVNM,<name>,FREQ,<f>,AMPL,<vpp>,OFST,<v>,PHASE,<deg>,WAVEDATA,` + little-endian int16 + `\n`. The write
  returns in 70 to 110 ms (32 B, 8 kB) and the next query answers after 2 ms. The upload does not select the wave.
- A7. `STL? USER` is `STL WVNM` when empty, else `STL WVNM,<name>,...`. A re-upload under a name replaces the wave.
  Names are listed without `.bin`.
- A8. `WVDT? USER,<name>` answers `WVDT POS, /Local, WVNM, <name>, LENGTH, <n>B, TYPE, 6, WAVEDATA,` + `n` bytes +
  `\n` as one message; built-ins: `WVDT? M<i>` with `POS, M<i>`, `LENGTH, 32768B` (16384 points). The bytes equal
  the bytes sent, `0x0A` and `0x0D` included. 4 bytes (2 points) are accepted.
- A9. The reply never carries the VXI-11 END flag and is re-sent whole on every further read, so `read_raw()`
  never returns. One low-level read returns exactly one message; the next query is normal.
- A10. `SRATE?` is `C1:SRATE MODE,DDS` or `C1:SRATE MODE,TARB,VALUE,16384000Sa/s`. `VALUE` is accepted only in TARB;
  `MODE,TARB,VALUE,500000` works in one command.
- A11. In TARB, `FRQ = VALUE / points` (one quantity): 1 kHz at 16384 points is `VALUE,16384000Sa/s`; `BSWV FRQ,2000`
  makes `VALUE` 32768000. `VALUE,1E9` reads 75000000; 0.1 and 12345.678 are kept. Back in DDS the frequency is kept.
  `MODE,TARB` keeps `VALUE` and recomputes `FRQ` (3 points: `5461333.333HZ`; 16385 points: `999.9389686HZ`).
- A12. `FRQ` prints 10 significant digits (`61.03515625HZ`), `PERI` 6, `VALUE` 10.
- A13. At the output (scope, 1 Mohm): DDS plays the table resampled (a 6-sample spike in a 4096-point ramp is absent),
  TARB plays every point (the spike is there, +0.10 V). DDS at `FRQ,1000`: 1.000 ms sawtooth, 2.03 Vpp for `AMP,2`.
- B1. A header-less upload (`C1:WVDT WVNM,<name>,WAVEDATA,`) is accepted; selecting it changes none of `FRQ`, `AMP`,
  `OFST`, `PHSE`. A header with only `FREQ,250` changes only `FRQ`. `LENGTH,128B` in the header changes nothing.
- B2. An odd byte count corrupts the data (5 bytes read back as 6, the last one the terminator). The plug refuses it.
- B3. Names are case-sensitive (`HTF_B1` is another file than `htf_b1`); `NAME,htf_b1.bin` selects `htf_b1`; 40
  characters work; the quoted `"htf b5-x"` is stored as `"htfb5-x"`, so the plug refuses names outside `[A-Za-z0-9_-]`.
- B4. Re-uploading the selected wave replaces its data and keeps the selection and the channel parameters.
- B5. 200 kB take 107 ms, 2 MB 444 ms, 16 MB (8 000 000 points) 3.6 s; the next query answers after 2 to 4 ms.
  `WVDT?` of 16 MB arrives as one message in 2.6 s.
- B6. See A11 for the point-count coupling. Any point count from 2 up works.
- B7. The user store is shared by both channels (`C2:ARWV NAME,htf_b1`; `C2:WVDT` uploads into the same list). The
  order of `STL? USER` is neither alphabetical nor by upload time.
- B8. `WVDT? USER,<unknown>` answers an empty message (no hang). `*RST` resets the selection to index 0 and keeps the user store.

## Measured at the output: modulation, sweep, burst (2026-10-12)

Generator CH1 to DHO814 CH1 over a BNC cable, 1 Mohm, `LOAD,HZ`, `AMP,2`; both instruments over LAN (VXI-11).
Each case is one deep-memory capture of 100k points, analysed offline: zero crossings for frequency and cycle
counts, peak-to-peak per carrier period for the envelope. The scope reads about 1.5 % high on Vpp.

- AM (carrier SINE 10 kHz, `MDSP,SINE`, `FRQ,100`): the envelope is `AMP * (1 +- DEPTH/100) / 2` peak-to-peak.
  `DEPTH,80` gave 1.83 / 0.21 Vpp (nominal 1.8 / 0.2, depth 79.2 %); `DEPTH,100` gave 2.03 / 0.03 Vpp. So `AMP`
  is the envelope maximum at 100 % depth and the unmodulated carrier is `AMP/2`; at `DEPTH,120` the peak is
  `1.1 * AMP`. Modulation frequency 100.0 Hz.
- FM (carrier 10 kHz, `DEVI,2000`, `FRQ,100`): instantaneous frequency 7981 to 12017 Hz, carrier +- `DEVI` within 0.25 %.
- Sweep `SWMD,LINE`, `DIR,UP`, `START,500`, `STOP,1500`, `TIME,0.1`: the frequency rises linearly (10 kHz/s) from
  516 to 1491 Hz, measured per cycle, and jumps back every 100.0 ms. `SWMD,LOG`, `DIR,DOWN`, 2000 to 200 Hz:
  1986 to 212 Hz with the log shape, period 99.9 ms. The carrier `FRQ` reads `(START+STOP)/2` in both modes.
- Burst `GATE_NCYC,NCYC`, `TRSR,INT`, `TIME,5`, `PRD,0.01`, `STPS,0`, SINE 1 kHz: exactly 5 cycles per burst
  (5.00 ms active), one burst every 10.00 ms, 0 V between bursts (34 mVpp noise). SQUARE 2 kHz, `TIME,12`,
  `PRD,0.02`: 12 cycles, 6.0 ms active, 20.0 ms period; between bursts the output sits at the low level
  (-1 V at `AMP,2`), not at 0 V.

## Measured at the output with a 50 ohm load (2026-10-12, task V1)

A 50 ohm feed-through terminator at the scope input, generator CH1 to it over a BNC cable; the scope's
`:MEAS:ITEM?` readings, which run 1.5 to 4 % high at these scales (the square the most, edge overshoot).

- `LOAD,50`, `AMP,1`: 1.017 Vpp at the load. `LOAD,HZ`, `AMP,2`: 1.016 Vpp. So the generator is a 50 ohm source
  whose open-circuit level is `AMP * (LOAD+50) / LOAD` at a numeric load and `AMP` at `HZ`; `LOAD` only tells it
  what you will connect. Into a real 50 ohm the scope reads what `BSWV?` shows at `LOAD,50`.
- `LOAD,75`, `AMP,1` into the 50 ohm load: 0.848 Vpp (model `1 * 125/75 * 50/100 = 0.833`, plus the scope's bias).
- `OFST,0.25` at `LOAD,50`: +0.765 / -0.253 V at the load; the offset scales like the amplitude. With `PLRT,INVT`:
  +0.259 / -0.757 V, the offset inverted too, as without a load.
- SQUARE `AMP,2`, `DUTY,30` at `LOAD,50`: 2.07 Vpp, duty 0.300. SINE 1 MHz `AMP,2`: 2.04 Vpp, 999.3 kHz.
- Output off into 50 ohm: 15 mVpp of noise (50 mVpp open).

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
- Modulation, sweep, burst (recon 2026-10-05, outputs off). While `STATE` is `OFF` the query answers only
  `STATE,OFF` and every other parameter written is ignored (`MDWV`, `BTWV`; the sweep applies them); values set earlier survive `STATE,OFF`/`ON`. `*RST`
  switches all three off. Switching one on switches the other two off.
- `C1:MDWV AM` selects the type; a parameter written with a type selects it too. Each type keeps its own values.
  `SRC,INT` and `SRC,EXT` work, `CH1` and `CH2` are ignored. A value written under `SRC,EXT` is kept.
- PWM exists only with a PULSE carrier, and a PULSE carrier reads as PWM (back to SINE: the earlier type again).
  A NOISE or DC carrier switches all three off and refuses `STATE,ON`. A `WVTP` written through `BSWV` switches
  all three off whatever the type; `CARR,WVTP` keeps them on. `CARR,<key>,<value>` through any of the
  three commands writes the carrier even while `STATE` is `OFF`.
- Sweep: the carrier frequency is `(START+STOP)/2`; a carrier `FRQ` write keeps the sweep centred with
  `half = min(old span/2, FRQ - 1e-06)`. `START`
  above `STOP` becomes `STOP` and the other way round, so the plug writes `START`, `STOP`, `START`. `STOP` up to the
  carrier maximum, `START` down to 1e-06, `TIME` 0.001..500. `SWMD,STEP`, `DIR,UP_DOWN`, `EDGE,FALL` are ignored.
  `CENTER`, `SPAN`, `SYM`, `MARK_*`, `STARTTIME`, `ENDTIME`, `BACKTIME` are accepted and never echoed; `MTRIG` too.
  `TRMD` while `TRSR` is `EXT` switches `TRSR` to `INT`; `TRSR,EXT` resets `TRMD` to `OFF` (sweep and burst). A PULSE carrier switches the sweep off.
- Burst: `TIME,INF` switches `TRSR` to `EXT`; `TIME` is 1..1000000. `STPS` is the carrier `PHSE` (400 reads 40).
  `DLAY` above 100 becomes 100. `PRD` written under `EXT` or `MAN` is ignored; its minimum depends on the carrier.
  In GATE mode `DLAY` and `TIME` writes are ignored. `TRMD,RISE`/`FALL` show under `INT` and `MAN`; `EDGE`, `COUNT` never show.
  `TRSR,INT` is refused while `TIME` is `INF`.
- FM: `FRQ` is limited to the carrier frequency and `DEVI` follows a falling carrier. FSK `HFRQ,0` reads `0.001HZ`.
- FM `FRQ` and `DEVI` both follow a falling carrier. The sweep centring also respects the carrier maximum
  (`BSWV FRQ,45E6`: carrier 40 MHz, `START` = `STOP`), and a carrier type change that clamps the carrier
  frequency moves the sweep the same way. `GATE_NCYC,GATE` while `TRSR` is `MAN` switches `TRSR` to `INT`; in
  GATE mode `TRMD` writes are ignored too. Not modelled: after `BSWV WVTP` switched modulation off, `STATE,ON`
  once showed FM instead of the last selected PSK; PWM `DEVI,5` read `0.000199985S` (a limit near the pulse
  width); the burst `DLAY` default at 25 MHz is 6.76e-07.
- A NOISE carrier through `CARR` leaves the burst in GATE mode for good. Not modelled: the default and minimum
  burst `DLAY` depends on the carrier frequency; a sweep `START` of 1e-06 reads `1.00001e-06HZ` after off and on.
- `STATE,OFF` of `MDWV`, `SWWV` or `BTWV` switches the active mode off, whichever of the three it is. The
  plug writes the groups that are switched off before the one that is switched on.
- Arbitrary waves (recons 2026-10-12). The index table is not PG02's: this unit's index 0 is StairUp, PG02's
  index 0 is Sine, so an index taken from the manual selects the wave two places away. Read the table with
  `list_arbs()` (`STL? BUILDIN`) or use `scpi.BUILTIN_ARBS`.
- `WVDT?` never ends. The generator sets no END flag, ignores the requested size and sends the same whole message
  again on every read, so PyVISA's `read_raw()` collects gigabytes. The plug reads once, with the termination
  character off (the data contains `0x0A`). Do not read it any other way.
- An odd number of data bytes is stored with the terminating newline as the last byte. The plug refuses odd counts.
- Names: case-sensitive; `.bin` is added on read-back and accepted on input; a space in a quoted name is dropped.
  Only `A-Za-z0-9_-`, 1 to 40 characters, are used by the plug (longer names are not measured).
- PG02 defines no command to delete a user wave. Uploaded waves stay until removed at the front panel, so use fixed
  names and re-upload instead of creating new ones. A re-upload overwrites.
- DDS and TrueArb are different signals. DDS resamples the wave table at the frequency, which smooths narrow
  features; TrueArb plays every point at the sample rate. Choose `SRATE MODE,TARB` when each point matters.
- A wave's own `FREQ`, `AMPL`, `OFST`, `PHASE` are applied at selection and silently override what the channel had.
  A wave without them leaves the channel alone. A re-upload of the selected wave updates the stored data and not the
  channel parameters; whether the output follows without a new selection is checked on the scope at acceptance.
- Not modelled: the space removal in quoted names (the one listed gap in the replay), the order of `STL? USER`, the
  content of the built-ins (the fake serves a ramp), and whether `*RST` keeps the TrueArb `VALUE` (the second
  transcript shows 16384000 after `*RST`, but the unit may have been power cycled in between; the fake keeps it).
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
