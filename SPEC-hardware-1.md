# SPEC-hardware-1: fold the first hardware session into the fake, the validator and the README (T6)

Decision behind this work: the owner ran `tools/probe.py` (plain and `--risky`), `example_test.py` and a
follow-up probe on the real SDG2042X on 2026-10-05. The fake was built from PG02 examples; many of its
hypotheses turned out wrong. The fake must now answer like the generator did, so that the same defect
cannot pass on the fake again.

**The real generator is NOT available to you.** Use `siglent_sdg_openhtf.fake_resource.FakeSdgResource`.
Read `AGENTS.md`, this file, `tests/data/hardware_session_1.txt`, `SPEC.md` §2 to §6, then the code. Do not
commit. Where this file disagrees with `SPEC.md`, this file wins: it records what the generator does.

`tests/data/hardware_session_1.txt` is the verbatim transcript of the session (timings and `*IDN?`
removed). It is the oracle. Do not edit it. If the model below cannot reproduce a line of it, stop
polishing and report the line; do not special-case single values to make a line pass.

## Facts measured on the real target (SDG2042X, firmware 2.01.01.23R7, VXI-11, 2026-10-05)

Transport and common commands

- F1. `*IDN?` answers `Siglent Technologies,SDG2042X,<serial>,2.01.01.23R7` (PG02 §3.1.1 Format 2).
- F2. VXI-11 (`TCPIP0::<ip>::inst0::INSTR`) works. Port 5025 (raw socket, PG02 §1.2.4) refuses the
  connection on this unit; port 5024 (telnet) accepts a connection (not used further).
- F3. `*RST` returns at once; the next query already shows the default state and takes about 120 ms.
  `*OPC?` answers `1`. `*RST` also switches both outputs off, `LOAD` to `HZ`, `PLRT` to `NOR`.
- F4. Timing: a write takes 4 to 10 ms, a `WVTP` change 15 to 30 ms, `OUTP ON` 80 to 260 ms (relay), a
  query 2 to 8 ms, `*IDN?` 1.3 ms median. `apply_setup` of the example setup: 240 ms.
- F5. `SYST:ERR?` (not in PG02) answered `+0, No error` and `*CLS` was accepted, also right after clamped
  out-of-range writes. So the reply says nothing about rejected values. They stay undefined: the plug never
  sends them and the fake keeps raising `ValueError` for them.

Default state (both channels, after `*RST`)

- F6. `C1:BSWV WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,4V,AMPVRMS,1.414Vrms,OFST,0V,HLEV,2V,LLEV,-2V,PHSE,0`
  and `C1:OUTP OFF,LOAD,HZ,PLRT,NOR`. Type-specific defaults: SQUARE `DUTY` 50; RAMP `SYM` 50; PULSE
  `DUTY` 20 (`WIDTH` 0.0002 at 1 kHz), `RISE` and `FALL` 8.4e-09, `DLY` 0; NOISE `BANDSTATE` OFF,
  `BANDWIDTH` 120000000 (seen when `BANDSTATE` was first switched on); DC `OFST` 0.

Reply format of `BSWV?` (key order as listed; no `MAX_OUTPUT_AMP` token, no dangling key)

- F7. SINE: `WVTP,FRQ,PERI,AMP,AMPVRMS,[AMPDBM,]OFST,HLEV,LLEV,PHSE`. SQUARE: the same plus `DUTY`.
  RAMP: the same plus `SYM`. PULSE: `WVTP,FRQ,PERI,AMP,AMPVRMS,[AMPDBM,]OFST,HLEV,LLEV,DUTY,WIDTH,RISE,FALL,DLY`
  (no `PHSE`). ARB: `WVTP,FRQ,PERI,AMP,OFST,HLEV,LLEV,PHSE` (no `AMPVRMS`, no `AMPDBM`, also at 50 ohm).
  NOISE: `WVTP,STDEV,MEAN,BANDSTATE` and `BANDWIDTH` only while `BANDSTATE` is `ON`. DC: `WVTP,OFST`.
- F8. `AMPDBM` is in the reply only while `LOAD` is numeric (measured for SINE, SQUARE, PULSE; GUESS for
  RAMP: same).
- F9. Units: `FRQ` `HZ`, `PERI` `S`, `AMP`/`OFST`/`HLEV`/`LLEV`/`STDEV`/`MEAN` `V`, `AMPVRMS` `Vrms`,
  `AMPDBM` `dBm`, `RISE`/`FALL` `S`, `BANDWIDTH` `HZ`; `PHSE`, `DUTY`, `SYM`, `WIDTH`, `DLY` carry no unit.
- F10. Numbers are C `%g` (6 significant digits, lower-case exponent: `8.4e-09`, `1e+06`, `0.000999364`),
  which is Python `format(x, ".6g")`. Exceptions: `FRQ` and `BANDWIDTH` show 10 significant digits
  (`format(x, ".10g")`: `12345678.12HZ`, `1234.56789HZ`, `40000000HZ`, `1e-06HZ`); `LOAD` is an integer,
  a fraction is cut off (`LOAD,1234.5` reads `1234`).
- F11. `AMPVRMS` is `AMP` times a fixed factor: SINE 0.3535 (not 0.35355: 4 V reads `1.414Vrms`, 20 V
  reads `7.07Vrms`), SQUARE and PULSE 0.5 (whatever the duty), RAMP 0.2886825 (one data point: 4 V reads
  `1.15473Vrms`). `AMPDBM` is `10*log10(Vrms**2 / LOAD / 0.001)` with that `Vrms` and the numeric load.

Writes

- F12. Exponent forms are accepted: `1E3`, `1e3`, `1E-03`, `1E-06`, `50E6`. More digits than the echo
  shows are accepted (`FRQ,12345678.123456`).
- F13. Several pairs in one command are accepted and applied left to right
  (`C1:BSWV WVTP,SINE,FRQ,1000,AMP,2`, `C1:OUTP ON,LOAD,50,PLRT,INVT`).
- F14. A key that is not valid for the current `WVTP` is ignored without any sign (`DLY`, `DUTY`, `SYM`
  on SINE; `PHSE` on PULSE; `FRQ`, `AMP` on NOISE; `AMP` on DC). `AMPDBM` is ignored while `LOAD` is `HZ`.
- F15. Out-of-range values are **clamped, not ignored** (PG02 does not say so):
  - `FRQ` above the type's maximum becomes the maximum: SINE 40 MHz, SQUARE 25 MHz, PULSE 25 MHz,
    RAMP 1 MHz, ARB 20 MHz. A `WVTP` change clamps the frequency the same way. 1e-07 Hz was accepted;
    no lower limit was found.
  - `LOAD` below 50 becomes 50, above 100000 becomes 100000.
  - `AMP` below 0.002 becomes 0.002 (at `HZ` and at 50 ohm). `AMP` above the maximum becomes the maximum;
    the maximum is `min(20*k, 2*(10*k - abs(OFST)))` with `k = 1` at `HZ` and `k = LOAD/(LOAD+50)` at a
    numeric load (10 Vpp at 50 ohm, 12 Vpp at 75 ohm, 19.99 Vpp at 100 kohm).
  - `OFST` is clamped to `+-(10*k - AMP/2)` (`OFST,5` at 20 Vpp reads 0; `OFST,12` at 0.002 Vpp reads
    9.999). For DC the limit is `+-10*k`.
  - `HLEV` is clamped to `LLEV + 0.002 .. 10*k`, `LLEV` to `-10*k .. HLEV - 0.002`; the other level stays.
    So `HLEV` below the current `LLEV` is not rejected: it becomes `LLEV + 0.002`.
  - SQUARE `DUTY` is clamped to `100*16.3e-9*FRQ .. 100 - 100*16.3e-9*FRQ` (40.75 to 59.25 at 25 MHz,
    0.00163 to 99.9984 at 1 kHz).
  - PULSE `RISE` and `FALL` below 8.4e-09 become 8.4e-09.
  - `PHSE,400` reads 40; `PHSE,-90` reads -90; `PHSE,360` reads 360.
- F16. `LOAD` rescales what is displayed: every level (`AMP`, `OFST`, `HLEV`, `LLEV`, DC `OFST`, and with
  them `STDEV`, `MEAN`) is multiplied by `k_new / k_old` (`HZ` to 50 halves, 50 to `HZ` doubles).
  Not modelled: at `LOAD,75` the generator showed `AMP,2.4001V` for an exact 2.4 (firmware rounding).
- F17. `AMPVRMS,<v>` sets `AMP = v / factor` (SINE: `AMPVRMS,1` reads `AMP,2.82885V`). `AMPDBM,<d>` at a
  numeric load sets `Vrms = sqrt(LOAD * 0.001 * 10**(d/10))`. Both are then echoed exactly as written
  (`AMPVRMS,1Vrms`, `AMPDBM,3dBm`). Measured for SINE only; GUESS for the other types: the same with their
  factor; ARB ignores both (GUESS, it echoes neither).
- F18. PULSE `WIDTH` and `DUTY` are one quantity (`DUTY = 100*WIDTH*FRQ`); the width in seconds is what
  survives a frequency change. Width is clamped to `16.3e-9 .. PERI - 16.3e-9` on a `WIDTH` or `DUTY`
  write and on a frequency change. Not modelled: with a long edge the maximum is lower (`WIDTH,0.002` at
  1 kHz with `FALL` 1e-06 read `0.000999364`).
- F19. Type-specific values **survive** a `WVTP` change (SQUARE `DUTY,30`, to SINE, back to SQUARE: still
  30). SQUARE duty and PULSE duty are separate values.
- F20. Parameters are views of shared state: NOISE `STDEV` is `0.0575 * AMP` and `MEAN` is `OFST` (after
  `STDEV,0.5` and `MEAN,0.1` the SINE read `AMP,8.69565V`, `OFST,0.1V`). DC has its own offset, separate
  from `OFST`/`MEAN`. Not modelled: PULSE `DLY` and `PHSE` are coupled too (`DLY,0.0002` at 1 kHz, then
  SINE read `PHSE,-72`); one data point, so the fake keeps them independent.
- F21. `MAX_OUTPUT_AMP,5` (PG02 §3.3) was accepted, is not echoed anywhere and had no visible effect:
  `AMP,10` was still accepted afterwards.
- F22. A value is echoed as written when it has at most 6 significant digits and is in range
  (`AMP,2.0005`, `OFST,0.0005`, `PHSE,12.345`, `SYM,12.34`); `AMP,1.23456789` reads `1.23457V`.

## 1. `fake_resource.py`

Make the fake reproduce the transcript: every `<-` line not marked `#!` must be returned verbatim when the
`->` lines are sent in order. Model F3 (state only, no timing), F5 to F21 as rules, not as a lookup table.

- Replace the hypotheses the session refuted: defaults (F6), reply key sets and order (F7, F8), units
  (F9), number format (F10; delete the old `_num`), "ignored" by "clamped" (F15), the load rescale (F16),
  `reset_type_specific` (F19), `AMPVRMS`/`AMPDBM` (F11, F17), SINE-only conversion.
- The frequency table gets `ARB` 20e6 for all three models. `SDG2082X`/`SDG2122X` SINE limits stay
  hypotheses (only the SDG2042X was measured) and keep their marker.
- `MAX_OUTPUT_AMP`: parsed, then nothing (F21).
- Unparsable values, a `WVTP` of another family, `FRQ <= 0`: still ignored (not measured; keep the
  hypothesis marker on exactly these).
- The constructor signature, `log`, `reject`, `raise_on`, `closed` and the `ValueError` for commands PG02
  does not define stay as they are.
- Comments: a rule that was measured carries `# measured, hardware session 1 (README)` instead of
  `# hypothesis until hardware session 1`; what was not measured keeps the hypothesis marker. Rewrite the
  module docstring accordingly. Every SCPI literal keeps its `# PG02 §x.y` reference.
- The fake still imports nothing from the package.

## 2. `scpi.py`, `models.py`

- `format_value`: floats as `.10G` (F10, F12: the frequency echo has 10 digits). Drop the hypothesis text.
- Tolerances (F10, F22): `DEFAULT_REL_TOL = 1e-5`; `REL_TOL = {"FRQ": 1e-9, "BANDWIDTH": 1e-9}`;
  `ABS_TOL`: 1e-6 for the volt keys (`AMP, AMPVRMS, AMPDBM, OFST, HLEV, LLEV, STDEV, MEAN`), 1e-6 for
  `PHSE, DUTY, SYM`. Drop `MAX_OUTPUT_AMP` from `ABS_TOL`. A clamped value must fail verification:
  `AMP` 0.0015 sent, 0.002 read is a mismatch.
- `models.py`: `ARB: 20e6` in `max_freq_hz` of all three models. SDG2042X figures are now measured; the
  comment says so; the other two models keep the hypothesis marker.
- `_validate_limits` (limits on, as before):
  - `k = 1` without a numeric `LOAD` in the same channel setup, else `LOAD/(LOAD+50)`.
  - `AMP` outside `0.002 .. max_amp_vpp_hiz * k`: `ValueError`.
  - `OFST`: `abs(OFST) > max_offset_v_hiz * k` is an error; with `AMP` in the same setup,
    `abs(OFST) + AMP/2 > max_offset_v_hiz * k` is an error.
  - `HLEV > max_offset_v_hiz * k`, `LLEV < -max_offset_v_hiz * k`, and, when both are given,
    `HLEV - LLEV < 0.002`: errors.
  - `ModelLimits` keeps its fields (`max_amp_vpp_50` stays, equal to `max_amp_vpp_hiz * 50/100`).
  - Messages keep the existing style: channel, group, key, the value, the limit, the load.
- `BSWV_KEY_WAVE_TYPES["DLY"]`: PULSE-only is measured (F14); fix the comment.
- No other validator change. `MAX_OUTPUT_AMP` and `AMPDBM` at `HZ` stay valid input; with `verify=True`
  the plug reports them as "not echoed by the generator", which is what the generator does.

## 3. `plug.py`, `example_test.py`, `tools/probe.py`

- No behaviour change in `plug.py`. Only comments that state a refuted or confirmed hypothesis.
- `tools/probe.py`: the comment on `FRQ,50E6` says "silently ignored"; it is clamped. Nothing else.
- `example_test.py`: unchanged unless a docstring states something F2 contradicts.

## 4. Tests

- New `tests/test_hardware_session_1.py`: replays `tests/data/hardware_session_1.txt` against one
  `FakeSdgResource()`. A `->` line ending in `?` is a query, any other `->` line a write; the `<-` line
  after a query must equal the reply exactly. `#!` lines: the command is still sent (to keep the state),
  the reply is not compared. On a mismatch the failure names the line number, the command, expected and
  got. Also assert that at least 200 replies were compared.
- Existing tests that assert a refuted hypothesis (old defaults, old formats, "ignored" where the
  generator clamps, type-specific reset, halving at any numeric load, the old limits) are rewritten to the
  measured value, one for one; do not delete a behaviour test without a replacement. Tests of anything
  else stay untouched.
- New tests, each one behaviour with the expected value: every clamp of F15 (both sides where there are
  two); F16 for `HZ` to 50, 50 to `HZ`, 50 to 75; F17 at 50 ohm and `AMPDBM` ignored at `HZ`; F18; F19;
  F20; F21; `values_match` rejects a clamped value and accepts a 6-digit echo of a 9-digit value
  (`AMP` 1.23456789 against 1.23457); each new `_validate_limits` rule, pass and fail;
  `apply_setup` on the fake raises `SetupError` naming the key for: `FRQ` 30e6 with `SQUARE` and limits
  check off (`CONF.siglent_sdg_check_limits` false), and reports `MAX_OUTPUT_AMP` as not echoed.
- No sleeps, no network.

## 5. Documentation (`README.md` only; `STATUS.md`, `AGENTS.md`, `SPEC*.md` are the owner's)

- First paragraph: replace the "not yet run on hardware" status by: verified on an SDG2042X, firmware
  2.01.01.23R7, over VXI-11, 2026-10-05 (`*IDN?`, `*RST`, `OUTP`, `BSWV`, `apply_setup`).
- "Facts (measured on the generator)": F1 to F4, F6 to F13, F22 as a compact list or table, at most 45
  lines. No address, no serial number.
- "Things the manual does not tell you": F5, F14 to F21 and the three "not modelled" items, at most 45
  lines. Say plainly: values are clamped without any error, so `apply_setup(..., verify=True)` is the only
  way to notice; port 5025 refused the connection on this unit, use VXI-11.
- The resource-name paragraph: VXI-11 is what was verified; the raw socket is PG02's alternative and was
  refused on this unit.
- The key list near the top: note that `AMPDBM` needs a numeric `LOAD` and `MAX_OUTPUT_AMP` has no visible
  effect on this firmware.
- Plain English, short sentences, no em dashes.

## Done means

- `uv run pytest -q` passes; `uv run mypy` prints `Success`.
- `uv run python example_test.py --fake` prints `example: PASS`.
- `grep -rn "hypothesis" src/` lists only items this spec calls not measured.
- `git status --short` shows only: `src/siglent_sdg_openhtf/{fake_resource,scpi,models,plug}.py`,
  `tools/probe.py`, `tests/*.py`, `README.md` (and the owner's untracked `SPEC-hardware-1.md`,
  `tests/data/hardware_session_1.txt`).

Report: files changed, the final test line verbatim, the output of the commands above, every transcript
line the model could not reproduce, every place where this spec was wrong or ambiguous or where you
deviated and why, what is untested, and anything that looks like a bug in existing code that you did not
touch.

## Addendum: acceptance on the generator (owner, 2026-10-05)

The coder delivered sections 1 to 5 (503 tests). Acceptance sent a fresh command sequence to the fake and to
the generator at once and compared every reply; that found facts the first probes had missed. The owner
measured them (probes 4 to 8, all appended to the transcript), fixed the fake, the validator and README, and
added tests. These supersede the text above where they differ:

- F23. `FRQ,0` and negative values are accepted and read `FRQ,0HZ,PERI,infS`. `validate_setup` now rejects
  `FRQ` and `PERI` of 0 or less.
- F24. Above 20 MHz (first at 20000001 Hz) all levels are clamped to +-5 V (10 Vpp at `HZ`); an amplitude
  already set is clamped by the frequency change and stays clamped. `_validate_limits` checks it when `FRQ`
  is in the setup. At a numeric load above 20 MHz only `AMP,25` at 50 ohm was measured (reads 10 V).
- F25. SQUARE `DUTY` is clamped again on a frequency change and on entering SQUARE, and stays clamped
  (replaces the coder's deviation 4).
- F26. `PHSE` beyond +-360 wraps keeping its sign (`-400` reads -40, `725` reads 5).
- F27. `SYM` is clamped to 0..100; NOISE `BANDWIDTH` to 20 MHz..120 MHz; PULSE `DLY` to +- one period.
- F28. RAMP `AMPVRMS` factor is 1/3.464 (three data points), not 0.2886825. `AMPVRMS` and `AMPDBM` writes
  work on SQUARE and RAMP with their factor; RAMP echoes `AMPDBM`; ARB ignores `AMPVRMS` (F8, F17 guesses
  confirmed).
- F29. Leaving PULSE sets `PHSE = -360*DLY*FRQ`; PULSE keeps its own `DLY` (replaces "not modelled" in F20;
  the `PHSE,-72` lines of the transcript are compared now).
- F30. F16 holds exactly only between `HZ` and 50 ohm. With other loads the rescaled values are off by up
  to 0.3 mV and look rounded to 0.1 mV; not modelled. This is why 46 replies of the acceptance run are
  marked `#!`: they carry such a value forward.
- Not modelled, one line each in the transcript: `PERI` in single precision (`4.99969e-08S`), `AMPDBM,0`
  reading `9.64327e-16dBm`, a PULSE duty of `99.992` at the width limit.
- Section 4 said "at least 200 replies"; the first transcript had 166. It now has 382 compared replies.
