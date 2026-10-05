# SPEC-modulation: modulation, sweep and burst as setup groups

Decision behind this work (human owner, 2026-10-05): after the v1 core and the station demo, the next
feature is modulation, sweep and burst. Design by the owner agent: three more groups in the `apply_setup`
data model (`MDWV`, `SWWV`, `BTWV`, PG02 §3.5, §3.6.1, §3.7), validated before anything is sent, written
in a safe order, read back and verified, like `OUTP` and `BSWV`. The carrier is not a new concept: it is
the `BSWV` group, so the plug never sends a `CARR` parameter.

**The real generator is NOT available to you.** Use `FakeSdgResource`. Read `AGENTS.md`, this file,
`tests/data/hardware_modulation_1.txt`, then `scpi.py`, `plug.py`, `fake_resource.py` and their tests.
Do not commit.

`tests/data/hardware_modulation_1.txt` is the verbatim transcript of the recon (173 replies). It is the
oracle. Do not edit it. Lines marked `#!` were measured but are not to be modelled.

## Facts measured on the real target (SDG2042X, firmware 2.01.01.23R7, VXI-11, 2026-10-05, outputs off)

Common to the three commands

- M1. While `STATE` is `OFF` the query answers only `C1:MDWV STATE,OFF` (same for `SWWV`, `BTWV`), and every
  other parameter written is ignored (`C1:MDWV AM,DEPTH,77` while off: depth unchanged after `STATE,ON`).
  The values set earlier survive `STATE,OFF` / `STATE,ON`. `*RST` switches all three off.
- M2. The three are exclusive per channel: switching one on switches the other two off.
- M3. A reply is `<ch>:<CMD> STATE,ON,<own fields>,CARR,<carrier fields>`. The carrier fields are the
  `BSWV?` fields of the current wave without `PERI`, `HLEV`, `LLEV` (and without `AMPDBM`; not measured at a
  numeric load). The carrier is the basic wave: a `BSWV` write shows in the `CARR` part and a `CARR` write
  shows in `BSWV?`.
- M4. Several pairs in one command work (`C1:MDWV AM,FRQ,250,DEPTH,40`, `C1:MDWV STATE,ON,AM,DEPTH,55`).
- M5. Out-of-range values are clamped without any sign, as in `BSWV`.

`MDWV` (PG02 §3.5)

- M6. After `STATE,ON` the reply carries the type as a bare token:
  `C1:MDWV STATE,ON,AM,MDSP,SINE,SRC,INT,FRQ,100HZ,DEPTH,100,CARR,...`. Own fields per type, in order:
  - AM: `MDSP,SRC,FRQ,DEPTH`; DSBAM: `MDSP,SRC,FRQ`; FM: `MDSP,SRC,FRQ,DEVI` (`DEVI` with `HZ`);
    PM: `MDSP,SRC,FRQ,DEVI` (no unit); PWM: `MDSP,SRC,FRQ,DEVI` (`DEVI` with `S`: `0.00019S`);
  - ASK: `SRC,KFRQ`; FSK: `SRC,KFRQ,HFRQ`; PSK: `SRC,KFRQ,PLRT`.
  - With `SRC,EXT` only `SRC,EXT` remains of the own fields. With `MDSP,NOISE` the `FRQ` field is absent.
- M7. Defaults: type AM, `MDSP` SINE, `SRC` INT, `FRQ` 100 (all types share one modulating frequency? no:
  see M9), `DEPTH` 100, FM `DEVI` 100 Hz, PM `DEVI` 100, PWM `DEVI` 0.00019 s, `KFRQ` 100, `HFRQ` 1 MHz,
  `PLRT` POS.
- M8. `C1:MDWV <TYPE>` selects the type. Writing a parameter of a type selects that type too
  (`C1:MDWV FM,FRQ,175` while AM was shown: the reply shows FM).
- M9. Each type keeps its own values (`AM,FRQ,250` and `FM,FRQ,150` coexist).
- M10. Clamps: AM `DEPTH` 0..120; AM `FRQ` 0.001 Hz..1 MHz (measured for AM only; GUESS: the same for the
  other `FRQ` and `KFRQ`); FM `DEVI` 0..carrier frequency; PM `DEVI` up to 360; FSK `HFRQ` up to the carrier
  type's maximum (40 MHz for SINE).
- M11. `SRC`: `INT` and `EXT` are accepted; `CH1` and `CH2` are not (the source stays as it was). A value
  written while `SRC` is `EXT` is stored (`AM,FRQ,300` shows after `SRC,INT`).
- M12. PWM exists only with a PULSE carrier, and a PULSE carrier allows only PWM:
  - `C1:MDWV PWM` with a SINE carrier does nothing;
  - carrier set to PULSE while PSK was shown: the reply shows PWM; carrier back to SINE: PSK again;
  - not modelled: in another run (AM shown, carrier to NOISE and then PULSE) modulation went off instead.
- M13. A NOISE or DC carrier switches modulation off (`STATE,OFF`), also when set through `BSWV`.
- M14. Number formats: modulating `FRQ`, `KFRQ`, `HFRQ` like the carrier frequency (`format(x, ".10g")` +
  `HZ`); everything else `format(x, ".6g")`.

`SWWV` (PG02 §3.6.1)

- S1. Reply with `TRSR,INT` or `MAN`: `STATE,ON,TIME,1S,STOP,1500HZ,START,500HZ,TRSR,INT,TRMD,OFF,SWMD,LINE,
  DIR,UP,CARR,...`. With `TRSR,EXT`: no `TRMD`, and `EDGE,RISE` after `DIR`. Those are the defaults
  (`TIME` 1, `START` 500, `STOP` 1500).
- S2. `START`, `STOP` are printed with `format(x, ".6g")` (`4e+07HZ`, `1e-06HZ`), `TIME` too (`0.123457S`).
- S3. The sweep owns the carrier frequency: after `START` or `STOP` is written the carrier `FRQ` is
  `(START+STOP)/2`. A carrier `FRQ` write (`CARR,FRQ` or `BSWV FRQ`) moves both: `START = FRQ - span/2`,
  `STOP = FRQ + span/2`.
- S4. Clamps: `START` above `STOP` becomes `STOP`; `STOP` below `START` becomes `START`; `STOP` up to the
  carrier type's maximum; `START` down to 1e-06; `TIME` 0.001..500.
- S5. Accepted: `SWMD` `LINE`, `LOG` (`STEP` is ignored); `DIR` `UP`, `DOWN` (`UP_DOWN` is ignored); `TRSR`
  `INT`, `EXT`, `MAN`; `TRMD` `ON`, `OFF`. Writing `TRMD` while `TRSR` is `EXT` switches `TRSR` to `INT`.
  `EDGE,FALL` was not accepted (the reply kept `EDGE,RISE`).
- S6. Ignored, never echoed: `CENTER`, `SPAN`, `SYM`, `MARK_STATE`, `MARK_FREQ`, `STARTTIME`, `ENDTIME`,
  `BACKTIME`. `MTRIG` is accepted and changes nothing in the reply.
- S7. Carrier SQUARE and RAMP work. A PULSE carrier switches the sweep off (GUESS: NOISE and DC too).

`BTWV` (PG02 §3.7)

- B1. Reply, `GATE_NCYC,NCYC`, `TRSR,INT`: `STATE,ON,PRD,0.01S,STPS,0,TRSR,INT,TRMD,OFF,TIME,1,
  DLAY,6.04035e-07S,GATE_NCYC,NCYC,CARR,...` (the defaults). `TRSR,EXT`: no `PRD`, no `TRMD`. `TRSR,MAN`:
  no `PRD`. `GATE_NCYC,GATE`: `PRD,STPS,TRSR,GATE_NCYC,PLRT` (no `PRD` with `EXT`); default `PLRT` NEG.
- B2. `TIME,INF` switches `TRSR` to `EXT`. `TIME` is clamped to 1..1000000. `STPS` beyond 360 wraps like
  `PHSE` and is the carrier phase: `STPS,90` shows `PHSE,90` in the carrier and the other way round.
  `DLAY` above 100 becomes 100; `DLAY,0` becomes a minimum of about 0.6 us that varies (not modelled, the
  reply is marked `#!`). `PRD` is stored while `TRSR` is `EXT` or not (0.5 written under `INT`, shown again
  after `EXT` and back). `PRD` has a minimum that depends on the carrier (not modelled, marked `#!`).
- B3. `TRMD` `RISE`/`FALL` and `EDGE` were only written while `TRSR` was `EXT` or `MAN` and never showed;
  `TRMD` under `INT` is not measured. `COUNT` is never echoed. `MTRIG` changes nothing in the reply.
- B4. In GATE mode `DLAY` and `TIME` writes are ignored (their values are back after `NCYC`).
- B5. A PULSE carrier: no `STPS` in the reply. A NOISE carrier: the reply is only
  `STATE,ON,PLRT,POS,CARR,WVTP,NOISE,STDEV,..,MEAN,..` and the mode stays GATE afterwards.

## 1. Data model and `scpi.py`

- `GROUPS` becomes `("OUTP", "BSWV", "MDWV", "SWWV", "BTWV")`. Keys (PG02 mnemonics; one frozenset each):
  - `MDWV`: `STATE`, `TYPE`, `SRC`, `MDSP`, `FRQ`, `DEPTH`, `DEVI`, `KFRQ`, `HFRQ`, `PLRT`.
    `TYPE` is one of `AM, DSBAM, FM, PM, PWM, ASK, FSK, PSK` (PG02 §3.5, SDG2000X).
  - `SWWV`: `STATE`, `TIME`, `START`, `STOP`, `CENTER`, `SPAN`, `SWMD`, `DIR`, `SYM`, `TRSR`, `TRMD`, `EDGE`,
    `MARK_STATE`, `MARK_FREQ`, `STARTTIME`, `ENDTIME`, `BACKTIME` (everything PG02 §3.6.1 lists; S6 keys are
    valid input and fail verification as "not echoed", which is what the generator does).
  - `BTWV`: `STATE`, `PRD`, `STPS`, `GATE_NCYC`, `TRSR`, `DLAY`, `PLRT`, `TRMD`, `EDGE`, `TIME`, `COUNT`.
  - No `CARR` keys and no `MTRIG` in the data model.
- `build_command(channel, group, key, value)` for the new groups (each literal with its PG02 section):
  - `C1:MDWV STATE,ON`; `TYPE` → `C1:MDWV AM`; any other `MDWV` key needs the type:
    `build_mdwv(channel, mdwv_type, key, value)` → `C1:MDWV AM,DEPTH,40`. Keep `build_command`'s signature;
    give it an optional keyword `mdwv_type`.
  - `C1:SWWV TIME,2`, `C1:BTWV TIME,INF` (`TIME` of `BTWV` is an int or the string `INF`).
- `validate_setup` (all errors `ValueError` naming channel, group, key; nothing is sent):
  - value types: `STATE`, `MARK_STATE` bool or ON/OFF; enumerations exactly as PG02 lists them (`SRC`
    `INT, EXT, CH1, CH2`; `MDSP` `SINE, SQUARE, TRIANGLE, UPRAMP, DNRAMP, NOISE, ARB`; `PLRT` of `MDWV`
    `POS, NEG`; `SWMD` `LINE, LOG, STEP`; `DIR` `UP, DOWN, UP_DOWN`; `TRSR` `EXT, INT, MAN`; `SWWV TRMD`
    `ON, OFF` (bool accepted); `EDGE` `RISE, FALL`; `GATE_NCYC` `GATE, NCYC`; `BTWV PLRT` `NEG, POS`;
    `BTWV TRMD` `RISE, FALL, OFF`); numbers finite; PG02 ranges: `DEPTH` 0..120, PM `DEVI` 0..360, `STPS`
    0..360, `SYM` 0..100, `STARTTIME`/`ENDTIME`/`BACKTIME` 0..300; `TIME`, `PRD`, `FRQ`, `KFRQ`, `HFRQ`,
    `START`, `STOP` greater than 0; `BTWV TIME` a positive int or `INF`.
  - a group with other keys needs `STATE` on in the same group (M1): keys beside `STATE` with `STATE` off
    or absent are an error.
  - `MDWV` with `STATE` on needs `TYPE`; keys must fit the type (M6): `MDSP`, `FRQ` for AM, DSBAM, FM, PM,
    PWM; `DEPTH` AM; `DEVI` FM, PM, PWM; `KFRQ` ASK, FSK, PSK; `HFRQ` FSK; `PLRT` PSK; `SRC` all.
  - at most one of the three groups may have `STATE` on in one channel setup (M2).
  - with `BSWV` `WVTP` in the same channel setup: `MDWV` on with `NOISE` or `DC` is an error (M13); `PWM`
    needs `PULSE` and `PULSE` allows only `PWM` (M12); `SWWV` on with `PULSE`, `NOISE` or `DC` is an error
    (S7).
  - `SWWV` on together with `BSWV` `FRQ` or `PERI` is an error (S3: the sweep owns the frequency);
    `START` greater than `STOP` is an error; `BTWV` `STPS` together with a different `BSWV` `PHSE` is an
    error (B2).
- `order_setup`: `OUTP` off-state, `LOAD`, `PLRT`, then `BSWV` as today, then the modulation group, then
  `OUTP` on-state last. Inside a group: `STATE` on first (a `STATE` off is the only command of that group);
  `MDWV`: `TYPE`, `SRC`, then the rest in the caller's order; `SWWV`: `START`, `STOP`, `START` again when
  both are given (S4: either order alone can be clamped by the old values), then the rest; `BTWV`:
  `GATE_NCYC`, `TRSR`, then the rest. `order_setup` keeps returning `(group, key, value)` triples.
- `parse_mod_reply(raw, expect_header)` → `(fields, carrier)`: two ordered dicts of strings, split at the
  bare `CARR` token; in `fields` the bare type token of `MDWV` is stored under `TYPE`. `ProtocolError` on a
  wrong header or an odd token count. A reply `STATE,OFF` gives `({"STATE": "OFF"}, {})`.
- `values_match`: unchanged rules; `INF` is compared as a string; tolerances for the new numeric keys are
  the default ones.

## 2. `plug.py`

- `get_modulation(channel)`, `get_sweep(channel)`, `get_burst(channel)` → typed own fields (units
  stripped), without the carrier. Queries `C1:MDWV?` (PG02 §3.5), `C1:SWWV?` (§3.6.1), `C1:BTWV?` (§3.7).
- `set_modulation`, `set_sweep`, `set_burst(channel, params)`: validate and write, no verification, like
  `set_basic_wave`.
- `apply_setup` handles the new groups: one query per touched group for verification; every key sent is
  compared with the own fields (`TYPE` included); a key not in the reply is "not echoed by the generator".
  When a group was switched off, only `STATE` is compared.
- `manual_trigger(channel, group)`: `C1:SWWV MTRIG` or `C1:BTWV MTRIG` (PG02 §3.6.1, §3.7); `ValueError`
  for another group. No verification possible (S6, B3).
- `tearDown()` unchanged.

## 3. `fake_resource.py`

Model M1 to M14, S1 to S7, B1 to B5 as rules, so that the transcript replays verbatim (every `<-` line not
marked `#!`). Accept `CARR,<key>,<value>` writes in all three commands (the transcript uses them; they act
like the `BSWV` write, restricted to the keys PG02 lists for that command). What is marked GUESS or "not
measured" above carries `# hypothesis until a hardware session`; measured rules carry
`# measured, modulation recon (README)`. The fake still imports nothing from the package.

If a reply cannot be reproduced by a rule, do not special-case values: add its line number to a list
`UNMODELLED` in the replay test with a one-line reason, at most 12 entries, and report each one.

## 4. Tests

- `tests/test_hardware_modulation_1.py`: replay as in `tests/test_hardware_session_1.py`; `#!` lines and
  `UNMODELLED` lines are sent but not compared; assert at least 155 replies compared.
- `scpi`: every validation rule of section 1, pass and fail; `build_command` strings; `order_setup` for
  each group including the `START, STOP, START` rule and `STATE` off; `parse_mod_reply` on four replies
  taken from the transcript (AM, `STATE,OFF`, sweep with `EXT`, burst in GATE mode).
- `plug` on the fake: `apply_setup` passes for AM, FM, FSK, PWM on a PULSE carrier, a sweep, an NCYC burst
  and a GATE burst; raises `SetupError` naming the key for `SWMD` `STEP`, `DIR` `UP_DOWN`, `SRC` `CH2`,
  `DEPTH` verified after a `reject`; reports `CENTER` as not echoed; a setup that switches `MDWV` on and a
  later one that switches `SWWV` on leaves `get_modulation` at `STATE` `OFF`; `manual_trigger` sends the
  right string; the three `get_*` return typed values; the sweep written with old values that would clamp
  (`START` above the old `STOP`) ends with the requested values.
- All existing tests keep passing unmodified.

## 5. Documentation (`README.md` only)

- The key list near the top: add the three groups with their keys, one line each.
- "Facts (measured on the generator)": reply shapes M3, M6, S1, B1, at most 15 lines.
- "Things the manual does not tell you": M1, M2, M8, M11, M12, M13, S3 to S6, B2 to B5, at most 25 lines.
- A short example of a setup with `MDWV` next to the existing example. Plain English, no em dashes.

## Done means

- `uv run pytest -q` passes; `uv run mypy` prints `Success`.
- `uv run python example_test.py --fake` prints `example: PASS`;
  `uv run python examples/station_demo.py --fake` ends with `station: PASS`.
- `git status --short` shows only: `src/siglent_sdg_openhtf/{scpi,plug,fake_resource}.py`, `tests/*.py`,
  `README.md` (and the owner's `SPEC-modulation.md`, `tests/data/hardware_modulation_1.txt`).

Report: files changed, the final test line verbatim, the output of the commands above, every transcript
line in `UNMODELLED` with its reason, every place where this spec was wrong or ambiguous or where you
deviated and why, what is untested, and anything that looks like a bug in existing code that you did not
touch.

## Addendum: acceptance on the generator, round 1 (owner, 2026-10-05)

The first implementation replayed transcript 1. A fresh sequence sent to the fake and the generator at
once (`tests/data/hardware_modulation_2.txt`, 352 replies) found these facts. They supersede the text
above where they differ.

- A1. `SWWV` parameters written while the sweep is off ARE applied (`TIME,3` and `START,100` while off show
  after `STATE,ON`, and the carrier frequency moved to the new centre at once). M1 holds for `MDWV` and
  `BTWV` only. (This also explains line 290 of transcript 1.)
- A2. A `WVTP` written through `BSWV` switches modulation and the sweep off, whatever the new type
  (`BSWV WVTP,SQUARE` with AM on: `MDWV STATE,OFF`; `BSWV WVTP,RAMP` with the sweep on: `SWWV STATE,OFF`).
  The type change through `CARR,WVTP` keeps them on (transcript 1). GUESS: the same for the burst.
- A3. The carrier part of a reply does contain `AMPDBM` while `LOAD` is numeric (after `AMPVRMS`).
- A4. FM: `FM,FRQ,5E6` with a 1 kHz carrier read `FRQ,1000HZ` (one data point: the modulating frequency
  is limited to the carrier frequency). FM `DEVI` is clamped again when the carrier frequency drops
  (`DEVI` 700, carrier to 500 Hz: `DEVI,500HZ`).
- A5. FSK `HFRQ,0` reads `0.001HZ`.
- A6. Sweep and carrier frequency: a carrier `FRQ` write keeps the sweep centred on it with
  `half = min(old span / 2, FRQ - 1e-06)`, `START = FRQ - half`, `STOP = FRQ + half`
  (`START` 10000, `STOP` 90000, then `BSWV FRQ,20000`: `START,1e-06HZ`, `STOP,40000HZ`; then `FRQ,100`:
  `STOP,200HZ`).
- A7. Switching `TRSR` to `EXT` resets `TRMD` to `OFF`, for the sweep and for the burst.
- A8. Burst `TRMD` `RISE` and `FALL` are accepted and echoed while `TRSR` is `INT` or `MAN`.
- A9. While burst `TIME` is `INF`, `TRSR,INT` is refused (stays `EXT`); after a numeric `TIME` it works.
- Not modelled, marked `#!` in transcript 2: the default and minimum burst `DLAY` depends on the carrier
  frequency (6.04035e-07 at 1 kHz, 5.43122e-07 at 500 kHz); `DLAY,0.5` with `PRD` 0.25 read `0.250001S`;
  a sweep `START` of 1e-06 read `1.00001e-06HZ` after the sweep was switched off and on.
- Confirmed guesses: `MDWV STATE,ON` is refused with a NOISE or DC carrier; the sweep and the burst are off
  after a NOISE or DC carrier and `STATE,ON` is refused; `PRD` written under `MAN` is ignored.

## Addendum 2: acceptance round 2 (owner, 2026-10-05)

After the fixes of round 1 a third fresh sequence (`tests/data/hardware_modulation_3.txt`, 172 replies)
left 9 differing replies. The facts behind them:

- A10. The FM modulating `FRQ` is clamped again when the carrier frequency drops, like `DEVI`
  (`FM,FRQ,9000` with a 2.5 kHz carrier reads 2500; carrier to 1.5 kHz: `FRQ,1500HZ`).
- A11. Sweep centring (A6) also respects the upper limit: `half = min(old span / 2, FRQ - 1e-06,
  fmax - FRQ)` with `fmax` the carrier type's maximum (`BSWV FRQ,45E6` with a SINE carrier: carrier
  40 MHz, `START` and `STOP` both `4e+07HZ`).
- A12. When a carrier type change clamps the carrier frequency, the sweep follows by the same rule
  (`SWWV CARR,WVTP,SQUARE` at 40 MHz: carrier 25 MHz, `START` and `STOP` both `2.5e+07HZ`).
- A13. `GATE_NCYC,GATE` while `TRSR` is `MAN` switches `TRSR` to `INT`.
- A14. In GATE mode a `TRMD` write is ignored (like `DLAY` and `TIME`, B4).
- Not modelled, marked `#!` in transcript 3: after `BSWV WVTP,RAMP` had switched modulation off, `STATE,ON`
  showed FM although PSK had been selected last; PWM `DEVI,5` read `0.000199985S` (a limit near the pulse
  width); the burst `DLAY` default at 25 MHz (6.76e-07).

## Addendum 3: acceptance through the plug (owner, 2026-10-05)

- A15. `STATE,OFF` of any of the three commands switches the active mode off, whichever it is
  (`MDWV STATE,ON`, then `SWWV STATE,OFF`: `MDWV STATE,OFF`). Found when a setup that switched the sweep off
  and modulation on left modulation off. `order_setup` now writes the groups that are switched off before
  the one that is switched on; the fake follows the rule. Fixed by the owner, with tests.
- Round 3 of the coder (A10 to A14) replays all three transcripts: 171, 329 and 160 replies compared.
  `UNMODELLED` holds two lines of transcript 1 (a pulse width margin of 16.0 instead of 16.3 ns; an
  `AMPVRMS` last digit).
- Live comparison after the fixes, third sequence: 172 replies, 2 differ, both listed as not modelled.
- Through the plug on the generator, outputs off: `apply_setup` verified AM, FM, FSK, PSK, PWM on a PULSE
  carrier, modulation off, three sweeps (including values that the old `START`/`STOP` would have clamped),
  bursts in NCYC, GATE and MAN, burst off. It raised `SetupError` naming the key for `SWMD` `STEP`, `DIR`
  `UP_DOWN`, `CENTER` (not echoed), `SRC` `CH2`, an FM `DEVI` above the carrier frequency, and `TIME` `INF`
  written while the burst was still in GATE mode (not echoed).
- Not verified: the signal itself. Nothing was measured at the output for modulation, sweep or burst;
  only the generator's read-back. Channel 2 was exercised only in transcript 2 and one burst setup.
