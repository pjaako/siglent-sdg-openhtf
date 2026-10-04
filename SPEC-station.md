# SPEC-station: generator + oscilloscope demo (T7)

Decision behind this work (human owner, 2026-10-05): one OpenHTF test drives this plug and the scope plug
of `rigol-dho-openhtf` together. The SDG2042X feeds a signal into the DHO814, the scope measures it, and
the readings are recorded with limits. This is the first check of the signal at the connector rather than
of the generator's read-back. The scope package is an optional dependency (extra `station`, pinned to
`829ff0d` through `[tool.uv.sources]`); `uv sync --all-extras --dev` installs it.

**The real instruments are NOT available to you.** Use `FakeSdgResource` and `FakeDhoResource`. Read
`AGENTS.md`, this file to the end (the facts come first, the numbered sections after them), `example_test.py`
and `tests/test_examples.py`. Do not commit.

## The scope package (as of `829ff0d`)

- `from rigol_dho_openhtf import RigolDhoPlug, Waveform`;
  `from rigol_dho_openhtf.fake_resource import FakeDhoResource`.
- Fake scope plug for `--fake`: `RigolDhoPlug(resource=FakeDhoResource(signal='clock'))`. The default
  sawtooth of the fake touches both ends of the code range and looks clipped; `clock` is a bounded square.
- Config keys `rigol_dho_resource`, `rigol_dho_restore_state`; set them after importing the package.
- `get_state()` returns `block` (bytes), `recording` (bool), `wav` (dict of five strings) and `check` (dict
  of eight settings that `set_state()` reads back). Save all four.
- `measure()` returns `9.9E+37` on the first query of an item.
- Its `tearDown()` no longer closes the shared ResourceManager.

## Facts about the station (measured)

Wiring on 2026-10-05: generator CH1 to scope CH1 with a 1:1 BNC cable, no probe, no terminator. The scope
input is 1 Mohm, so the generator sees an open circuit: use `LOAD,HZ`, then `BSWV?` shows the real volts.
The scope was on USB, the generator on LAN (VXI-11).

| Set on the generator | Read on the scope (`:MEAS:ITEM?`) |
|---|---|
| output off | Vpp 0.05 V (noise) |
| SINE 1 kHz, 2 Vpp, `LOAD,HZ` | Vpp 2.043 V, +1.023 / -1.020 V, 1000.0 Hz |
| the same output with `LOAD,50`, `AMP,1` | Vpp 2.041 V: `LOAD` only changes what is displayed |
| SQUARE 1 kHz, 3 Vpp, `OFST,1.5`, `DUTY,30` | 3.038 / -0.041 V, duty 0.300, 1000.0 Hz |
| SINE 1 MHz, 4 Vpp | Vpp 4.035 V, 1 000 000 Hz |
| RAMP 1 kHz, 2 Vpp, `OFST,0.5`, `SYM,20`, `PLRT,INVT` | +0.515 / -1.502 V, mean -0.496 V, 1004.4 Hz |

- `PLRT,INVT` inverts the offset as well as the waveform.
- The scope reads 1 to 3 % high on Vpp at these scales (1 V/div); limits in the demo must allow for it.
- The 1004.4 Hz on the ramp is the scope's frequency measurement, not checked further.
- Not measured: levels with `LOAD,50` and a real 50 ohm load (task V1 in `STATUS.md`).
- Both plugs open their instrument through PyVISA's one process-wide ResourceManager. A plug that closes
  it in `tearDown()` closes the other plug's session. This plug no longer does; the scope plug must not
  either.

## Facts about the scope the station relies on (DHO814, firmware 00.01.05)

Measured on the scope on 2026-09-27 and 2026-09-28 and recorded in the scope project's README; copied here
so that this project does not depend on reading it.

- pyvisa-py names it `USB0::6833::1101::<serial>::0::INSTR` (decimal vendor id, 0x1AB1). The scope plug
  finds the first USB instrument of that vendor by itself. USB access needs the udev rule of the scope
  project and membership in group `plugdev`.
- Every query costs about 22 ms, on USB and on LAN alike. `apply_setup()` of 15 settings takes 1.8 s.
- `*OPC?` always answers `0`: never wait on it. `*RST` restores factory defaults within 0.34 s.
- A header the scope does not know is never answered; the read times out (10 s by default).
- A value the scope clamps is visible only in the read-back, with no error (like the generator). A bad
  value gives `-222,"Data out of range"` in `:SYST:ERR?`.
- Send clean numbers: `200 * 1e-6` went out as `0.00019999999999999998` and the scope set 198 us/div.
- `:MEAS:ITEM? <item>,CHAN<n>` answers `9.9E+37` on the first query of an item and when the trace is
  clipped. Query each item once, wait, then read it. Items used so far: `VPP`, `VMAX`, `VMIN`, `VAVG`,
  `FREQ`, `PDUT`, `RTIM`, `FTIM`.
- Clipped data lies: when the trace leaves the screen the samples saturate (0 / 255, or 65535 for WORD) and
  their mean sits near mid-scale. Choose the vertical scale so that the signal fits, and check for
  saturation before trusting a waveform.
- `:TRIG:STAT?` answers `TD` while running; only `STOP` means stopped.
- `:ACQ:MDEP` accepts `10k`, `100k`, `1E5`, `100000`, but not the format its own query returns.
- State save and restore: `:SYST:SET?` returns the setup as one binary block (about 2.8 kB). The block does
  not carry the waveform-recording switch (`:REC:WREC:ENAB`) nor the `:WAV` read settings (`:WAV:SOUR`,
  `MODE`, `FORM`, `STAR`, `STOP`); the scope plug's `get_state()` / `set_state()` handle them. About 0.9 s
  after a block is loaded the scope switches recording off, so recording is switched back on after 1.2 s
  and checked. Restoring with recording on takes 2.6 to 4.4 s.
- Measured 2026-10-05: after `set_state()` the block read back was not byte-identical to the saved one (5 of
  its 29 compressed sections differed) although no error was reported and the visible settings were back.
  Do not verify a restore by comparing blocks. Save the whole `get_state()` result to disk before the first
  change, not only the block.
- Do not read deep memory (RAW) while waveform recording is enabled: it returns a record that is none of
  the recorded frames. The scope plug refuses to.
- `:DISP:DATA? PNG` returns a complete 1024 x 600 PNG in 0.2 to 0.3 s and changes no setting.

## Both plugs in one process (measured 2026-10-05, generator on LAN, scope on USB)

- Each plug survives the other's `tearDown()`: the scope answered after the generator plug was closed, and
  a generator session answered after the scope plug was closed.
- SINE 1 kHz 2 Vpp read 2.036 Vpp, 999.2 Hz at 500 mV/div.
- A scope measurement read right after `OUTP OFF` still showed the old value (2.02 Vpp): wait before
  reading.
- The scope drops a setup block when a `:MEAS:ITEM?` query was followed by any other query before the
  block; nothing is loaded and no error is queued (found here, cause measured by the scope project). Since
  `829ff0d` the scope plug's `set_state()` reads eight settings back (timebase scale and offset; display,
  scale and offset of channels 1 and 2), loads the block again up to three times and raises if they still
  differ. Checked here with both plugs, 3 runs: measure, then the scope plug's own `tearDown()`; the
  settings were back each time, each time after one extra load, logged as
  `set_state: setup block not loaded (attempt 1): [...]`. Expect that warning in a station log.
- The limit of that check: it sees a dropped block only if the test changed one of the eight settings. The
  station demo must change the timebase or the scale of channel 1 (it will), not only the trigger level.

The scope is a shared instrument with the user's own setup on it: a station test saves its state first,
restores it at the end and reports whether the restore worked.

## 1. `examples/station_demo.py`

`uv run python examples/station_demo.py --fake` runs without hardware. On hardware:
`uv run python examples/station_demo.py --generator 192.0.2.10 [--scope NAME]` (`--scope` empty: the scope
plug finds the first USB scope itself). `--generator` goes into `CONF.siglent_sdg_resource`, `--scope` into
`CONF.rigol_dho_resource`, both loaded after the plug modules are imported. Without `--fake` and without
`--generator` the script exits with a usage error. Last line printed: `station: PASS` or `station: FAIL`;
exit code 0 or 1. No station server, no web GUI. Same shape and style as `example_test.py`.

Test conditions are data, one list `STEPS` at the top of the file; each step is a dict:

| Key | Meaning |
|---|---|
| `name` | phase name, a Python identifier |
| `generator` | the `C1` part of a generator setup (`OUTP`, `BSWV`), as for `apply_setup` |
| `scope` | SCPI settings for `RigolDhoPlug.apply_setup`: `:TIM:MAIN:SCAL`, `:CHAN1:SCAL`, `:CHAN1:OFFS`, `:TRIG:EDGE:LEV` |
| `expect` | measurement name to nominal value: `vpp` (V) and `frequency_hz` |

The steps, from the measured table above (all with `OUTP STATE` on):

| name | generator | scope | expect |
|---|---|---|---|
| `sine_1khz` | `LOAD` `HZ`; SINE, `FRQ` 1000, `AMP` 2, `OFST` 0 | 200 us/div, 0.5 V/div, offset 0, level 0 | vpp 2, 1000 Hz |
| `sine_1khz_load_50` | `LOAD` 50; SINE, `FRQ` 1000, `AMP` 1, `OFST` 0 | the same | vpp 2, 1000 Hz |
| `square_1khz` | `LOAD` `HZ`; SQUARE, `FRQ` 1000, `AMP` 3, `OFST` 1.5, `DUTY` 30 | 200 us/div, 1 V/div, offset -1.5, level 1.5 | vpp 3, 1000 Hz |
| `sine_1mhz` | `LOAD` `HZ`; SINE, `FRQ` 1e6, `AMP` 4, `OFST` 0 | 200 ns/div, 1 V/div, offset 0, level 0 | vpp 4, 1e6 Hz |

- Limits: `vpp` within +-5 % of nominal, `frequency_hz` within +-1 % (the scope read up to 3 % high on Vpp
  and 0.44 % off on frequency). Two constants at the top of the file.
- One OpenHTF phase per step, built by one factory function from the step dict, with the measurements
  `vpp` (VOLT) and `frequency_hz` (HERTZ) and the phase name from `name`. A last phase `output_off`
  switches `C1` off with `apply_setup` and records `vpp_off` with limit `< 0.2` V (the scope read 0.05 V of
  noise).
- Before the first phase runs (at import of `STEPS`, or in a first phase `scope_setup`): the common scope
  settings `:CHAN1:DISP` on, `:CHAN1:PROB` 1, `:CHAN1:COUP` `DC`, `:TRIG:MODE` `EDGE`, `:TRIG:EDGE:SOUR`
  `CHAN1`, `:TRIG:EDGE:SLOP` `POS`, `:TRIG:SWE` `AUTO`. The scope is not reset: the scope plug restores the
  user's state in its own `tearDown()` (that is why every step changes the timebase or the scale).
- Input protection, checked for every step before anything is sent (`ValueError` naming the step): the
  open-circuit peak `(abs(OFST) + AMP/2) / k` must not exceed 5 V, with `k = 1` for `LOAD` `HZ` and
  `LOAD/(LOAD+50)` for a numeric load. A constant `MAX_INPUT_V = 5.0`. A step whose generator setup has no
  `AMP` (levels given another way) is refused too: the demo only knows `AMP` and `OFST`.
- Reading the scope, one helper: wait `SETTLE_S` (1.5 s), query every item once and discard the answer,
  wait `MEASURE_S` (0.7 s), read every item. Items: `VPP` and `FREQ`. A reading of `9.9E+37` is recorded as
  it is (it fails the limit); no retry loop. For `output_off` only `VPP`. With `--fake` both waits are 0.
- `--fake`: both plugs are subclasses whose `__init__` injects the fakes, as in `example_test.py`. The two
  fakes must be wired, or the scope fake would answer 3.3 to everything: section 2.

## 2. The wired fake scope (in `examples/station_demo.py`, used only with `--fake`)

`WiredFakeScope(FakeDhoResource)`, constructed with the `FakeSdgResource` it is cabled to. It overrides
`query`: it always calls the parent first (the parent logs the command and keeps the "setup block dropped
after a measurement" behaviour), then for `:MEAS:ITEM? <item>,CHAN1` replaces the answer:

- the first query of an item answers `9.9E+37`, like the scope; later ones the value;
- the value comes from the generator fake's own replies to `C1:OUTP?` (PG02 §3.3) and `C1:BSWV?` (PG02
  §3.4), asked through the generator fake's `query`: output off gives `VPP` 0.05; output on gives `VPP` =
  `AMP / k` and `FREQ` = `FRQ`, with `k` from the `LOAD` in the `OUTP?` reply (`HZ` is 1). This is the
  measured relation: the scope's 1 Mohm input sees the open-circuit voltage.
- any other item or channel: the parent's answer.

The generator fake's `log` therefore also holds these queries; tests that look at the end of the log must
look for the last write.

## 3. Tests (`tests/test_station_demo.py`; no hardware, no sleeps, no network)

1. `subprocess.run([sys.executable, 'examples/station_demo.py', '--fake'])` from the repo root exits 0 and
   the last line is `station: PASS`.
2. Without `--fake` and without `--generator`: exit code 2, nothing opened.
3. Importing the module opens no resource.
4. In process, with the fakes: the test passes; the generator fake's last two writes are `C1:OUTP OFF`,
   `C2:OUTP OFF`; the scope fake received a setup block write (`:SYST:SET`) after the last measurement, and
   its timebase and channel 1 scale read back as before the test (the restore worked through the dropped
   block).
5. A generator that ignores the amplitude (`FakeSdgResource(reject=['C1:BSWV AMP'])`): the test does not
   pass (the generator plug raises `SetupError`).
6. A cable fault, modelled by a wired fake scope that reports half the amplitude: the outcome is FAIL and
   the failed measurement is `vpp` of the first step.
7. Input protection: a step with `AMP` 12, `OFST` 0, `LOAD` `HZ` raises `ValueError` naming the step, and
   so does `AMP` 6 with `LOAD` 50 (6 V open-circuit peak); nothing is written to either fake. A step without
   `AMP` raises too.
8. `WiredFakeScope`: first query `9.9E+37`, then the value; `LOAD` 50 with `AMP` 1 reads 2; output off
   reads 0.05.
9. All existing tests keep passing unmodified.

## 4. Documentation and configuration

- `README.md`: a section "Station demo" after "Setup", at most 20 lines: what it does, the two commands,
  the wiring it assumes (generator CH1 to scope CH1, 1:1 cable, no terminator, 1 Mohm input), the 5 V
  input guard, and that the scope's own settings are restored at the end. Add the file to "Files".
- `pyproject.toml`: add `examples` to the mypy `files`; if the scope package has no type information, one
  more `[[tool.mypy.overrides]]` for `rigol_dho_openhtf` and `rigol_dho_openhtf.*` with
  `ignore_missing_imports = true` (and `follow_untyped_imports` is not to be used). Nothing else changes
  there; the dependency is already in place.
- Do not touch `STATUS.md`, `AGENTS.md`, `SPEC*.md`, `src/`.

## Done means

- `uv run pytest -q` passes; `uv run mypy` prints `Success`.
- `uv run python examples/station_demo.py --fake` ends with `station: PASS`.
- `uv run python example_test.py --fake` still prints `example: PASS`.
- `git status --short` shows only: `examples/station_demo.py`, `tests/test_station_demo.py`, `README.md`,
  `pyproject.toml`.

Report: files changed, the final test line verbatim, the output of the commands above, every place where
this spec was wrong or ambiguous or where you deviated and why, what is untested, and anything that looks
like a bug in existing code that you did not touch.

## Acceptance on the instruments (owner, 2026-10-05)

Generator on LAN, scope on USB, CH1 to CH1, no terminator. The demo as shipped: PASS, three runs.

| Phase | vpp | frequency_hz |
|---|---|---|
| `sine_1khz` | 2.018 V | 1001.2 |
| `sine_1khz_load_50` | 2.018 V | 1000.4 |
| `square_1khz` | 3.070 V | 1000.0 |
| `sine_1mhz` | 4.038 V | 1 000 700 |
| `output_off` | 0.06 V | |

- A generator set 20 % low on purpose (`AMP` 1.6 against an expected 2 Vpp): the scope read 1.62 V and the
  test failed on `vpp`. The limits bite on hardware.
- The scope's settings read back 2 s after each run were the ones from before the run; the generator's
  outputs were off.
- The spec's test 7 named `AMP` 4 at `LOAD` 50 as too high; that is a 4 V peak and passes the guard. The
  test uses `AMP` 6.
- Not checked: the limits just outside their edges, `--scope` with an explicit resource name, the scope on
  LAN.
