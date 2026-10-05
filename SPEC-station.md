# SPEC-station: generator + oscilloscope demo (placeholder)

Not written yet. Goal: `examples/station_demo.py`, an OpenHTF test that drives this plug and the scope plug
of `rigol-dho-openhtf` (https://github.com/pjaako/rigol-dho-openhtf) together: the SDG2042X feeds a signal
into the DHO814, the scope measures Vpp and frequency, and both are recorded with limits. Must run with
both fakes (`--fake`).

Owner decision 2026-10-05: `rigol-dho-openhtf` is a proper package and an optional dependency here.
Done: extra `station` in `pyproject.toml`, pinned to commit `d6cb29d` of its `main` through
`[tool.uv.sources]`; `uv sync --all-extras --dev` installs it. The rest of this spec is still to be written.

## The scope package (as of `d6cb29d`)

- `from rigol_dho_openhtf import RigolDhoPlug, Waveform`;
  `from rigol_dho_openhtf.fake_resource import FakeDhoResource`.
- Fake scope plug for `--fake`: `RigolDhoPlug(resource=FakeDhoResource(signal='clock'))`. The default
  sawtooth of the fake touches both ends of the code range and looks clipped; `clock` is a bounded square.
- Config keys `rigol_dho_resource`, `rigol_dho_restore_state`; set them after importing the package.
- `get_state()` returns `block` (bytes), `recording` (bool), `wav` (dict of five strings). Save all three.
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
- **`set_state()` after a `:MEAS:ITEM?` query does nothing, and reports nothing.** Scope alone, one
  session: `reset()`, `apply_setup()`, `set_state(saved)` restores the settings. With one `measure('VPP', 1)`
  before `set_state()` the timebase and the scale stay as set by the test, `:SYST:ERR?` is empty, and
  `set_state()` raises nothing. A 2 s pause does not help. A second `set_state()` works, and so does a
  restore from a fresh session. 5 runs, the generator plug present or not makes no difference. So a station
  test that measures and then relies on the scope plug's restore leaves the scope changed. Until the scope
  plug handles it: restore, read a setting back, restore again if it did not take.

The scope is a shared instrument with the user's own setup on it: a station test saves its state first,
restores it at the end and reports whether the restore worked.
