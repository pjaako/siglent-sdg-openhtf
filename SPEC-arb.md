# SPEC-arb: arbitrary waveforms (`ARWV`, `WVDT`, `STL`, `SRATE`)

Owner's spec, 2026-10-12. Status: recons A and B done on the generator (2026-10-12); sections 1 to 6 are the brief for the coder.

**The real generator is NOT available to the coder.** Use `FakeSdgResource`. Read `AGENTS.md`, `STATUS.md`,
`SPEC.md`, `SPEC-modulation.md` (the pattern of a setup group), PG02 §3.9.1 (ARWV, line 3161 of
`docs/PG02-E05C.txt`), §3.31 (STL, 5197), §3.32 (WVDT, 5377), §3.40 (SRATE, 7455). Do not commit.

## Facts measured on the generator (SDG2042X, firmware 2.01.01.23R7, VXI-11, 2026-10-12, recon A)

Transcripts: `tests/data/hardware_arb_1.txt` (recon A) and `hardware_arb_2.txt` (recon B), sanitized from the recon
logs. Every `<-` line not marked `#!` must replay verbatim on the fake; `#!` lines are neither sent nor compared
(binary payloads appear as `<N bytes sha1=.. first=..>`, and `STL? USER` orders are not modelled).

Selection (`ARWV`, PG02 §3.9.1):
- A1. `*RST` leaves `C1:ARWV?` and `C2:ARWV?` at `INDEX,0,NAME,StairUp` and `WVTP,SINE`. The selection is per channel.
- A2. **The index table of this unit is PG02's table shifted by two**: `STL? BUILDIN` answers `STL M0, StairUp, M1,
  StairDn, M10, LogFall, M100, LFPulse, ...` (197 entries, `M0` to `M196`, sorted as strings), i.e. index 0 is
  StairUp, 1 StairDn, 2 StairUD, 3 Ppulse, 4 Npulse, 5 Trapezia, 6 Upramp, 7 Dnramp, 8 ExpFal, 9 ExpRise, 10 LogFall,
  11 LogRise, 12 Sqrt, 13 Root3, 14 X^2, 15 X^3, 16 Sinc, 17 Gussian (sic), 18 Dlorentz, ..., 193 demo1_375pts,
  194 demo1_16kpts, 195 demo2_3kpts, 196 demo2_16kpts. The complete map is the `STL? BUILDIN` reply in the transcript;
  the fake carries it as a table (`BUILTIN_ARBS: tuple[str, ...]`, index = position). `STL?` answers the same as `STL? BUILDIN`.
- A3. `C1:ARWV INDEX,<n>` selects a built-in; the reply is `C1:ARWV INDEX,<n>,NAME,<name>`. An index outside 0..196
  (`198`, `199`) is ignored, the selection stays. `NAME,<built-in name>` (`StairDn`, `"StairUp"`, `sinc`) is ignored
  on this model (PG02: NAME is for user waves only). A user wave is selected with `NAME,<name>` and the reply is
  `C1:ARWV NAME,<name>.bin` (no `INDEX` field). An unknown user name is ignored.
- A4. `ARWV INDEX,<n>` (and `NAME,<user>`) **switches the channel to `WVTP,ARB`** when it was SINE; so does
  `SRATE MODE,TARB`. The BSWV key set of ARB is `WVTP,FRQ,PERI,AMP,OFST,HLEV,LLEV,PHSE` (no AMPVRMS, README).
- A5. Selecting a user wave applies the parameters stored in its file header: after `C1:ARWV NAME,htf_test2`
  (uploaded with `FREQ,2500,AMPL,1,OFST,0.5,PHASE,90`), `BSWV?` reads `FRQ,2500HZ,...,AMP,1V,OFST,0.5V,...,PHSE,90`,
  whatever the channel had before. So in a setup `ARWV` must be written before `FRQ`/`AMP`/`OFST`/`PHSE`.

Data (`WVDT`, PG02 §3.32):
- A6. Upload: `C1:WVDT WVNM,<name>,FREQ,<f>,AMPL,<vpp>,OFST,<v>,PHASE,<deg>,WAVEDATA,` followed by the samples as
  **little-endian signed 16-bit** (PG02 example p.175), then `\n`. The write of 32 B or 8 kB returns in 70 to 110 ms and
  the next query answers after 2 ms (no busy time at these sizes). The upload does not select the wave.
- A7. `STL? USER` answers `STL WVNM` when empty, else `STL WVNM,<newest>,...,<oldest>`. A re-upload under an existing
  name overwrites it (same list, new `LENGTH`). Names are listed without `.bin`. PG02 defines no delete command: a
  user wave stays until removed at the front panel. The recon left `htf_test` and `htf_test2` on the unit.
- A8. Read-back: `WVDT? USER,<name>` answers **one VXI-11 message** `WVDT POS, /Local, WVNM, <name>, LENGTH, <n>B, TYPE, 6,
  WAVEDATA,` + `n` bytes + `\n`. Built-ins: `WVDT? M<i>` answers `WVDT POS, M<i>, WVNM, <name>, LENGTH, 32768B, TYPE, 6,
  WAVEDATA,` + 32768 bytes + `\n` (16384 points; StairUD starts at 32767, Dlorentz at 98). The bytes read back equal the
  bytes sent, including samples that contain `0x0A` and `0x0D`. 4 bytes (2 points) are accepted.
- A9. **The generator never sets the VXI-11 END flag on a `WVDT?` reply, ignores the requested size, and re-sends the
  same whole message on every further read.** `read_raw()` therefore never returns (it collected 5 GB in 3 minutes).
  A single low-level read (`visalib.read(session, 1)`, one `device_read`) returns exactly one message, and the next
  query (`*IDN?`) answers normally: no stale data. The termination character must be disabled for that read (the data
  contains `0x0A`). A 16 MB reply arrives as one message too (B6).

Sample rate (`SRATE`, PG02 §3.40):
- A10. `C1:SRATE?` answers `C1:SRATE MODE,DDS` in DDS mode and `C1:SRATE MODE,TARB,VALUE,<rate>Sa/s` in TrueArb mode.
  `VALUE` is accepted only in TARB (`VALUE,2000000` under DDS is ignored); `MODE,TARB,VALUE,500000` in one command works.
- A11. In TARB, `FRQ = VALUE / points` and the two are one quantity: switching to TARB at `FRQ,1000` with the 16384-point
  built-in shows `VALUE,16384000Sa/s`; `VALUE,1000000` makes `FRQ` read `61.03515625HZ`; `BSWV FRQ,2000` makes `VALUE`
  read `32768000Sa/s`. With the 4096-point user wave, `VALUE,4096000` is 1 kHz and `VALUE,409600` is 100 Hz (`FRQ,100HZ`).
  Clamps: `VALUE,1E9` reads `75000000Sa/s`; `VALUE,0.1` and `VALUE,12345.678` are kept as written. Back in DDS the
  frequency is kept (`FRQ,0.7535203857HZ` after 12345.678 Sa/s on 16384 points).
- A12. `FRQ` prints with up to 10 significant digits here (`61.03515625HZ`); `PERI` with 6 (`0.016384S`).

At the output (DHO814 on CH1, 1 Mohm, `AMP,2`, 4096-point ramp `htf_test2`):
- A13. DDS at `FRQ,1000`: a 1.000 ms sawtooth, slope 1999 V/s against 2000 ideal, 6 mV rms from the uploaded shape,
  2.03 Vpp. TARB at `VALUE,4096000`: the same period and shape. A 6-sample spike written into the ramp (points 10 to
  15) **is absent in DDS mode and present in TARB mode** (+0.10 V as uploaded): DDS resamples the table, TrueArb plays
  every point. TARB at `VALUE,409600`: 100 Hz.

## Facts from recon B (sizes, names, live update; transcript `hardware_arb_2.txt`)

- B1. A header-less upload (`C1:WVDT WVNM,<name>,WAVEDATA,` + data, PG02 §3.32 example 2) is accepted. Selecting such
  a wave changes none of `FRQ`, `AMP`, `OFST`, `PHSE`; a header with only `FREQ,250` changes only `FRQ`. So on
  selection the generator applies exactly the header fields the file was uploaded with (A5 generalised). `LENGTH,128B`
  in the header is accepted and changes nothing.
- B2. **An odd byte count corrupts the data**: 5 bytes uploaded read back as `LENGTH, 6B` with the data
  `ff7fff7fff0a`: the terminating `\n` became the sixth byte. The plug refuses odd counts. Even counts never include
  the terminator (`LENGTH, 128B` for 128 bytes).
- B3. Names are **case-sensitive** (`HTF_B1` is a second file next to `htf_b1`); `NAME,htf_b1.bin` selects `htf_b1`
  too; a 40-character name works; a quoted name with a space, `"htf b5-x"`, is stored as `"htfb5-x"`, quotes included
  and the space dropped. Names outside `[A-Za-z0-9_-]` are therefore refused by the plug; the maximum length is not
  measured beyond 40 (hypothesis: the plug allows 1 to 40).
- B4. Re-uploading the currently selected wave replaces its data (`WVDT?` reads the new bytes) and keeps the selection
  and the channel parameters; whether the output changes without a re-select is checked at acceptance with the scope.
- B5. Sizes: 200 kB write 107 ms, 2 MB 444 ms, 16 MB (8 000 000 points, the PG02 maximum) 3.6 s; the next query answers
  after 2 to 4 ms in every case (no busy time). `WVDT?` of 16 MB arrives as **one** message of 16 000 001 bytes in
  2.6 s. So the one-message read of A9 is the whole story; no chunking. The plug raises the VISA timeout to at least
  30 s around uploads and read-backs.
- B6. `SRATE MODE,TARB` keeps the TrueArb `VALUE` (16384000 Sa/s after `*RST`, else the last value) and recomputes
  `FRQ = VALUE / points` for the selected wave: 3 points read `FRQ,5461333.333HZ`, 1000 points `16384HZ`, 16385
  points `999.9389686HZ`. Back in DDS that frequency is kept. Any point count from 2 up works (3, 1000, 16385).
- B7. The user store is shared by both channels: `C2:ARWV NAME,htf_b1` selects it on C2 (`C2:ARWV NAME,htf_b1.bin`),
  `C2:WVDT` uploads into the same list. `STL? USER` lists names in an order that is neither alphabetical nor by
  upload time (not modelled; the fake lists in upload order and the plug returns a list to be used as a set).
- B8. `WVDT? USER,<unknown>` answers an **empty message** (0 bytes), no hang, the next query is normal. `*RST` resets
  the selection to `INDEX,0` and keeps the user store.
- B9. State of the unit after the recons: 11 user waves `htf_test`, `htf_test2`, `htf_b1`, `HTF_B1`, `htf_b2`, `htf_b3`,
  `htf_b4_0123456789012345678901234567`, `"htfb5-x"`, `htf_b7`, `htf_c2` and `htf_big` (16 MB). Only the front panel
  can delete them (PG02 defines no delete). The acceptance run reuses `htf_test` and `htf_big`; nothing new is created
  without the human's say-so.

## 1. `scpi.py`: groups, validation, order, binary helpers

- `BUILTIN_ARBS: tuple[str, ...]`: the 197 names of A2 in index order, taken from the `STL? BUILDIN` reply in
  `hardware_arb_1.txt` (the tokens are `M<n>, name` pairs sorted as strings; sort by `int(n)`). A test checks
  `len == 197`, `[0] == 'StairUp'`, `[18] == 'Dlorentz'`, `[196] == 'demo2_16kpts'`.
- Setup groups: `ARWV` with keys `INDEX` (int 0..196) or `NAME` (str), exactly one of them; `SRATE` with keys `MODE`
  (`DDS`|`TARB`, case-insensitive on input, sent upper) and `VALUE` (number, 0 < v <= 75e6, hypothesis for the lower
  bound: 0.1 was accepted). `GROUPS` gains both; `Setup` typing unchanged (group -> key -> value).
- `validate_setup` rules (all raise `ValueError` naming channel/group/key before anything is sent):
  - `ARWV` or `SRATE` in a channel requires `BSWV` with `WVTP,ARB` in the same channel setup (A4: the generator would
    switch the type silently; the setup must say it).
  - `ARWV`: exactly one of `INDEX`/`NAME`; `INDEX` int in 0..196; `NAME` matches `^[A-Za-z0-9_-]{1,40}$` and does not
    end with `.bin` (B3; the plug keeps names canonical).
  - `SRATE`: `MODE` required when `VALUE` is given (the setup is explicit); `VALUE` only with `MODE,TARB`; with
    `MODE,TARB` and `VALUE`, the same channel may not set `BSWV` `FRQ` or `PERI` (A11, B6: one quantity).
  - Modulation, sweep and burst with an ARB carrier are allowed as before (no new rule).
- `order_setup`: `OUTP STATE off`, `LOAD`, `PLRT`, `BSWV WVTP`, `ARWV`, `SRATE MODE`, `SRATE VALUE`, `BSWV FRQ`/`PERI`,
  the amplitude/offset or level keys, other BSWV keys, the modulation groups as today, `OUTP STATE on` (A5: selection
  applies the file header, so it precedes the carrier parameters).
- `build_command` for the new groups: `C1:ARWV INDEX,5`, `C1:ARWV NAME,htf_test` (PG02 §3.9.1); `C1:SRATE MODE,TARB`,
  `C1:SRATE VALUE,4096000` (§3.40; `VALUE` formatted with `format_value`).
- Read-back and `values_match`: `ARWV?` -> `parse_reply(raw, 'C1:ARWV')` gives `{'INDEX': '2', 'NAME': 'StairUD'}` or
  `{'NAME': 'htf_test.bin'}`; `values_match('NAME', sent, got)` compares `got` with a trailing `.bin` removed,
  case-sensitively (B3); `INDEX` numerically. `SRATE?` -> `{'MODE': 'DDS'}` or `{'MODE': 'TARB', 'VALUE': '16384000Sa/s'}`;
  `strip_unit` already turns `16384000Sa/s` into a float (unit class includes `/`); add a test for it.
- Binary helpers (pure, no numpy):
  - `encode_samples(samples: bytes | Sequence[int]) -> bytes`: ints must be in -32768..32767 (else `ValueError`), packed
    little-endian int16 (`struct` / `array('h')` with byteswap on big-endian hosts); `bytes` pass through. The result
    must have an even length (B2), at least 4 bytes (A8) and at most 16 000 000 bytes (B5), else `ValueError`.
  - `build_wvdt_header(channel, name, *, freq=None, ampl=None, ofst=None, phase=None) -> str`:
    `C1:WVDT WVNM,<name>` + `,FREQ,<f>` ... only for the parameters given (B1) + `,WAVEDATA,` (PG02 §3.32). Name
    validated as above. Numbers through `format_value`.
  - `parse_wvdt_reply(raw: bytes) -> tuple[dict[str, str], bytes]`: empty `raw` -> `ProtocolError('no such wave')`
    (B8); split at the first `b'WAVEDATA,'` (missing -> `ProtocolError`); header fields from
    `WVDT POS, /Local, WVNM, htf_b1, LENGTH, 128B, TYPE, 6, WAVEDATA,` (tokens separated by `, `; keys `POS`, `WVNM`,
    `LENGTH` kept as strings, the dict also gets `'length'` as `int` from `128B`); data = the rest with exactly one
    trailing `\n` removed when present; `len(data) != length` -> `ProtocolError` with both numbers.
  - `parse_stl(reply: str) -> list[str]`: `STL WVNM` -> `[]`, `STL WVNM,a,b` -> `['a', 'b']`;
    `parse_stl_builtin(reply: str) -> dict[int, str]` from the `M<n>, name` pairs (A2).

## 2. `plug.py`

- The resource protocol gains `write_raw(data: bytes) -> object` and the read of one message. The plug's
  `_read_message() -> bytes`: if the resource has a `read_message` attribute (the fake), call it; else (PyVISA) set
  `read_termination = None`, call `self._resource.visalib.read(self._resource.session, 1)[0]`, restore the termination
  in a `finally` (A9). Both paths wrapped in the usual logging (DEBUG with the byte count, never the bytes).
- `list_arbs() -> dict[int, str]`: `STL? BUILDIN` parsed (PG02 §3.31). `list_user_arbs() -> list[str]`: `STL? USER`.
- `get_arb(channel) -> dict[str, float | str]` (`ARWV?`), `get_sample_rate(channel) -> dict[str, float | str]` (`SRATE?`),
  typed like `get_output`.
- `read_arb(name_or_index: str | int) -> tuple[dict[str, str], bytes]`: `WVDT? USER,<name>` or `WVDT? M<index>`
  (PG02 §3.32), then `_read_message()`, then `parse_wvdt_reply`. VISA timeout raised to `max(current, 30000)` for the
  call and restored (B5).
- `upload_arb(channel, name, samples, *, freq=None, ampl=None, ofst=None, phase=None, verify=True) -> int`:
  `encode_samples`, `build_wvdt_header`, `write_raw(header.encode('ascii') + data + b'\n')` with the timeout raised as
  above; returns the number of points. With `verify` (default): `name in list_user_arbs()` and `read_arb(name)[1] ==
  data`, else `SetupError` listing what differs (missing name, length, first differing offset). The upload does not
  select the wave (A6); `apply_setup` with `ARWV NAME` does.
- `apply_setup` handles the two groups through the generic path (build, write, read back, compare), using the
  read-back of `ARWV?` and `SRATE?` per channel.
- `tearDown()` unchanged.

## 3. `fake_resource.py`

The fake imports nothing from the package; it carries its own copy of the 197 names (a test asserts equality with
`scpi.BUILTIN_ARBS`). New state per channel: `arb_index` (0 after `*RST`), `arb_user: str | None`, `srate_mode` (`DDS`),
`srate_value` (16384000.0, retained across mode changes). Shared: `user_arbs: dict[str, tuple[dict[str, str], bytes]]`
in upload order, kept across `*RST` (B8).
- `C<n>:ARWV INDEX,<i>`: i in 0..196 selects (A3), else ignored; `NAME,<x>`: `x` with a trailing `.bin` removed must be
  a user name (case-sensitive) else ignored (A3, B3); both set `WVTP` to `ARB` (A4) and a user selection applies the
  header fields stored with the file (A5, B1) through the same code path as the corresponding `BSWV` writes. `ARWV?`
  answers `C1:ARWV INDEX,<i>,NAME,<name>` or `C1:ARWV NAME,<x>.bin`.
- `C<n>:SRATE MODE,DDS|TARB`, `VALUE,<v>`, and the combined `MODE,TARB,VALUE,<v>` form: `TARB` sets `WVTP,ARB` and
  `FRQ = value / points` (points = 16384 for a built-in, `len(data) // 2` for a user wave); `VALUE` in TARB is clamped
  to at most 75e6 and recomputes `FRQ`; `VALUE` under DDS is ignored (A10, A11, B6); `BSWV FRQ` under TARB sets
  `value = FRQ * points`; switching to DDS keeps `FRQ`. `SRATE?` answers `C1:SRATE MODE,DDS` or
  `C1:SRATE MODE,TARB,VALUE,<v>Sa/s` with `v` in the 10-significant-digit format already used for `FRQ`.
  `FRQ` in TARB prints the same way (`61.03515625HZ`, `5461333.333HZ`); `PERI` as today.
- `STL?` and `STL? BUILDIN` answer the reply recorded in `hardware_arb_1.txt` verbatim (build it from the table in the
  same string order as the generator: pairs sorted by the index as a string); `STL? USER` answers `STL WVNM` or
  `STL WVNM,<names in upload order>`.
- `write_raw(data: bytes)`: only `C<n>:WVDT ` uploads are accepted; parse the header up to `WAVEDATA,` (keys `WVNM`,
  `LENGTH`, `FREQ`, `AMPL`, `OFST`, `PHASE` in any order; `LENGTH` ignored); the payload is the rest; strip one trailing
  `\n` only when the payload length is odd after keeping it (B2: an odd upload stores the `\n` as data, so: payload
  = rest without the final `\n` if `len(rest) - 1` is even, else the rest as is). Store under the name as given
  (quotes and all; B3's space removal is not modelled). Re-upload replaces the data and keeps selections (B4).
  Anything else in `write_raw` raises `ValueError`.
- `write('WVDT? ...')` / `query('WVDT? ...')`: `query` raises `ValueError('binary reply: use read_message()')`;
  `write` stores a pending reply that `read_message()` returns once: `WVDT POS, /Local, WVNM, <name>, LENGTH, <n>B,
  TYPE, 6, WAVEDATA,` + data + `\n` for a user wave, `WVDT POS, M<i>, WVNM, <name>, LENGTH, 32768B, TYPE, 6,
  WAVEDATA,` + 32768 bytes + `\n` for a built-in (content: a 16384-point ramp for every built-in, marked
  `# hypothesis: only M2 and M18 were read`), `b''` for an unknown user name (B8). `read_message()` without a pending
  reply raises `ValueError`.
- `*RST` resets both channels' selection to index 0 and modes to DDS, keeps `user_arbs` and `srate_value` (hypothesis
  for the value; mark it).

## 4. Tests

- `tests/test_hardware_arb.py`: replays `hardware_arb_1.txt` and `hardware_arb_2.txt` like `tests/test_hardware_modulation_1.py`,
  with two differences. (1) `#! ` lines are not compared and, with one exception, not sent. (2) The exception: a line
  `#! -> C<n>:WVDT <header>WAVEDATA,<N bytes sha1=.. first=..>` is replayed as `fake.write_raw(header.encode() + bytes(N)
  + b'\n')`, i.e. an upload of N zero bytes under the logged name and header, so that the selections, `STL? USER`
  replies and `LENGTH` values that follow replay (the data itself is never compared; the `WVDT?` replies are `#!`).
  `# `, `## ` and blank lines are ignored. Assert at least 120 and 90 replies compared. An `UNMODELLED` dict as in the modulation test, at most 8
  entries in total, each with a reason (expected: the quoted name `"htf b5-x"` that the generator stores as `"htfb5-x"`).
- `tests/test_arb_scpi.py`: `BUILTIN_ARBS` checks; validation of every rule in section 1 (positive and negative);
  `order_setup` with `ARWV`/`SRATE`; `encode_samples` (ints, bytes, odd, too short, too long, out of range, negative
  values little-endian); `build_wvdt_header` with and without parameters; `parse_wvdt_reply` (user, built-in, empty,
  length mismatch, trailing newline, data containing `0x0A`); `parse_stl` and `parse_stl_builtin`; `strip_unit('16384000Sa/s')`;
  `values_match('NAME', 'htf_test', 'htf_test.bin')`.
- `tests/test_arb_fake.py`: upload/select/read back round trip; odd upload stores the newline; header application on
  selection; TARB frequency coupling for built-in and user waves; `VALUE` clamp; unknown name; `*RST` keeps the store;
  C2 shares the store; `query('WVDT? ...')` raises.
- `tests/test_arb_plug.py`: `upload_arb` with verify (round trip, and a fake whose stored data is tampered ->
  `SetupError`), `read_arb` by name and index, unknown -> `ProtocolError`, `apply_setup` with `ARWV` and `SRATE` in
  both modes (verified), validator errors before anything is sent (the fake's log stays empty), timeout raised and
  restored around uploads (the fake records `timeout` assignments).
- `uv run pytest -q`, `uv run mypy` (strict) green; `uv run python example_test.py --fake` unchanged.

## 5. Documentation (`README.md` only)

Usage: the `ARWV` and `SRATE` groups in a setup (one example with a built-in by `INDEX`, one with a user wave in TARB
with `VALUE`), `upload_arb`/`read_arb`/`list_arbs`. Facts: a new block under "Facts (measured on the generator)" with
A1 to A13 and B1 to B8 in short, and under "Things the manual does not tell you": the index shift, the missing END flag
and the one-message read, the odd-count corruption, names, no delete command, DDS smoothing versus TrueArb.

## 6. Done means

All of section 4 green, `mypy --strict` clean, README updated, no numpy import under `src/`, every new SCPI string
with its PG02 section comment, no command outside §3.9.1, §3.31, §3.32, §3.40 and the ones already used. Report:
files changed, the final `pytest` line verbatim, the `mypy` line, the `UNMODELLED` entries with reasons, and every
place where you departed from this spec and why. Then the owner runs the acceptance on the generator: a fresh sequence
to the fake and the instrument at once, and the uploaded wave at the output with the scope (B4 live update).

## Addendum 1: acceptance on the generator (owner, 2026-10-12)

A fresh sequence (`dumps/arb_accept_plan.md`, 66 commands in S1 to S5, the plug calls of S6, the scope in S7) was
sent to the fake and the generator at once. S6 matched on both (two `apply_setup` verified, an unknown `NAME`
raised `SetupError` with the same message on both, `upload_arb` with verify and `read_arb(16)` succeeded, the
validator refused `SRATE VALUE` with `FRQ` before sending anything). Ten reply differences, six facts:

- F1. **`*RST` resets the TrueArb `VALUE` to 16384000 Sa/s** (`MODE,TARB,VALUE,2000000` set, `MODE,DDS`, `*RST`,
  `MODE,TARB` reads `VALUE,16384000Sa/s`). The fake's hypothesis (kept) is wrong; five of the ten differences follow
  from it (`FRQ,2048000HZ` for 8 points = 16384000/8, carried into SINE and back, D1 to D3, D5, D6).
- F2. **Built-ins have different lengths**: `WVDT? M193` (demo1_375pts) answers `LENGTH, 750B` (375 points); `M0`, `M2`,
  `M18`, `M196` answer `32768B`. The fake gets a length table: 750 for M193, 32768 for the rest; hypothesis for
  M195 (demo2_3kpts) which was not read: 6000 bytes, mark it. TARB `FRQ = VALUE / points` uses that length.
- F3. **`ARWV NAME,<name>.bin` is ignored** (with the built-in 196 selected it stayed selected). Recon B4 showed it
  "working" only because `htf_b1` was already selected. The fake ignores names ending in `.bin`; the README and the
  docstrings drop the claim that `.bin` is accepted on input (the reply still carries it; `values_match` unchanged).
- F4. **The sweep follows every change of the carrier frequency, also while the sweep is off and also when the
  change comes from `SRATE MODE,TARB` (recompute) or from a wave selection**: with the carrier at 2048000 Hz,
  `SWWV STATE,ON` showed `STOP,2.0485e+06HZ,START,2.0475e+06HZ` (the span of 1000 Hz kept around the new carrier).
  The fake's centring rule for `BSWV FRQ` writes applies to every carrier frequency change; `START`/`STOP` print
  like `%g` (`2.0485e+06HZ`), as the existing sweep fields do.
- F5. Not arb, known and still unmodelled: the modulation type survives `*RST` (`MDWV STATE,ON` came back as PSK from
  the 2026-10-05 session) and the burst `DLAY` default depends on the carrier frequency (`5.4303e-07S` at 2048000 Hz).
  Leave both in the unmodelled lists with these reasons.
- F6. **At the output a re-upload of the selected wave does not change the signal; a new `ARWV NAME` does.** The
  8-point rising ramp and its re-uploaded inversion gave identical captures (correlation 1.000); after re-selecting,
  the output was the inverted ramp (0.79 against the previous capture, 0.91 against the inverted model). So after
  `upload_arb` of a wave that is selected, `apply_setup` with `ARWV NAME` must be sent again; the plug documents it,
  and `upload_arb` does not do it by itself (the upload may target a wave the test does not want selected).

Also measured on the scope (not this project's plug): a saved setup block is dropped while the scope is in STOP
(after `:SING`); after `:RUN` it loads at once. Recorded in `SPEC-station.md` and handed to the scope project.

Fix-up brief (coder): apply F1 to F4 to the fake with tests; F3 and F6 to README, plug docstrings and the
"Things the manual does not tell you" list; add `tests/data/hardware_arb_3.txt` sanitized from the acceptance log
(`dumps/arb_accept_20261012-124752.txt`: drop the `#! fake:` lines, placeholder serial `SDG2XCAQ000000`, delete
nothing else except `#t` lines; mark `#! ` the WVDT exchanges and the `STL WVNM` lists with two or more names as
before) and replay it in `tests/test_hardware_arb.py` with the same rules; the S7 scope notes are `# ` lines and
are ignored. Expected unmodelled entries after the fix-up: the `"htf b5-x"` name, the PSK type and the burst `DLAY`
of F5. Everything in section 6 still applies.

Fix-up outcome (owner, 2026-10-12): applied, 798 tests, three transcripts replay with three unmodelled replies.
F4 was narrowed by the coder with good reason: `hardware_modulation_2.txt` line 338 and `hardware_modulation_3.txt`
line 167 show a `BSWV FRQ` write leaving `START`/`STOP` alone while the sweep is off. The fake now moves the sweep
on the TARB recompute, `SRATE VALUE` and a selection with a stored `FREQ` whether the sweep is on or off, and on
`BSWV FRQ` only while it is on. Open: the generator may instead re-centre at `STATE,ON` only when the carrier lies
outside `START..STOP` (both measurements fit that rule too); one sweep experiment on the generator decides it.
