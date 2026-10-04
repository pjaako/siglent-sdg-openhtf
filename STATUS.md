# STATUS

Handoff state for whoever picks this up next, human or agent. Keep it current; it is committed.
Secrets (real IP, serial number, probe dumps) never go here; they live in the git-ignored `HANDOFF.md`.

## Where we are (2026-10-05, end of day)

`main` holds everything; nothing relevant is left only on a local machine (see "What is not on GitHub").
The day in short:

- **v1 core** (`*IDN?`, `*RST`, `OUTP`, `BSWV`, `apply_setup` with read-back verification) works on the real
  SDG2042X, firmware 2.01.01.23R7, over VXI-11. Facts: `SPEC-hardware-1.md`, README.
- **Station demo** (`examples/station_demo.py`): generator into a Rigol DHO814 in one OpenHTF test; passes on
  both instruments and with `--fake`. The scope plug is the package `rigol-dho-openhtf`, extra `station`,
  pinned in `[tool.uv.sources]`. Facts: `SPEC-station.md`.
- **Modulation, sweep, burst** (`MDWV`, `SWWV`, `BTWV` groups): verified by read-back on the generator with
  outputs off. The signal was not measured. Facts: `SPEC-modulation.md`.
- 652 tests, `mypy --strict` clean, CI green on `main`.

## Start here tomorrow

1. `uv sync --all-extras --dev && uv run pytest -q && uv run mypy`.
2. Ask the human two things before any hardware work: the generator's address (it is not in git), and
   whether a 50 ohm feed-through terminator is at hand (task V1 has been waiting for one since 2026-10-05;
   remind him if it is not).
3. Next task by default: **V2** (below). It needs the generator and the scope cabled CH1 to CH1.
4. Then `SPEC-arb.md` or `SPEC-counter-sync.md` (placeholders): recon on the generator first, then the
   spec, then a coder, then acceptance. The owner picks.

How a feature gets done here (it worked three times on 2026-10-05): recon on the instrument with a script
that logs every command and reply; the sanitized log becomes a transcript in `tests/data/` that the fake
must replay verbatim; spec; coder against the fake only; then acceptance by the owner agent: a fresh
command sequence sent to the fake and the instrument at once, every reply compared. Each such run found
rules the recon had missed (10, 14 and 5 of them). Differences go back to the coder as numbered facts plus
a new transcript. Last, the plug itself on the instrument: valid setups must verify, wrong ones must raise.

## What is not on GitHub (by design)

- `HANDOFF.md`: the real address and serial number of the generator, and notes on private dumps.
- `dumps/`: raw probe logs and the owner's one-off scripts (they contain the address). Everything measured
  is in the committed transcripts and SPEC files, without the address.
- On a new machine: the generator's address comes from the human; the scope on USB needs the udev rule of
  the `rigol-dho-openhtf` repository and membership in group `plugdev`; pushing needs working GitHub
  credentials (`gh auth login`), which the previous machine's agent did not have.

## Task board

| # | Task | State |
|---|---|---|
| T0 | Branch, AGENTS.md, SPEC.md, placeholders, README stub, STATUS.md, LICENSE, docs text | done |
| T1 | Packaging: pyproject, uv.lock, package skeleton, CI, test_import | done |
| T2 | `scpi.py` + `models.py` + `tests/test_scpi.py` | done |
| T3 | `fake_resource.py` + `tests/test_fake_resource.py` | done |
| T4 | `plug.py` + `tests/test_plug.py` | done |
| T5 | `example_test.py`, `tools/probe.py`, `tests/test_examples.py`, README usage | done |
| R1 | Review fix-up round (validation ranges, limits at numeric load, closed-plug errors, USB preference) | done |
| STOP | First hardware session: `tools/probe.py` and `example_test.py` on the SDG2042X, from a local session on the LAN | done 2026-10-05 |
| T6 | `SPEC-hardware-1.md`: hardware findings into the fake, validator, tolerances, README | done, accepted on hardware |
| T7 | `SPEC-station.md` → `examples/station_demo.py` with rigol-dho-openhtf | done, accepted on both instruments 2026-10-05 |
| V1 | Hardware check: signal levels with `LOAD,50` and a real 50 ohm load at the output (a feed-through terminator at the scope input). Expect the scope to read what `BSWV?` shows. The agent must ask the human to fit the terminator first, and to remove it afterwards. | open, needs the human |
| T8 | `SPEC-modulation.md`: `MDWV`, `SWWV`, `BTWV` as setup groups, fake, tests | done; read-back accepted on the generator 2026-10-05, signal not measured |
| V2 | Hardware check: modulation, sweep and burst at the output with the scope (AM depth, sweep range, burst cycle count). Keep the open-circuit peak under 5 V at the scope input, save the scope state first, outputs off at the end. Start from `examples/station_demo.py`. | **next** |
| later | SPEC-arb, SPEC-counter-sync | each needs a hardware session |

## Hardware access

Hardware sessions run in a local Claude Code session on a machine on the generator's LAN (a cloud session
cannot reach it: only HTTPS leaves the cloud container). The real address and serial are in the git-ignored
`HANDOFF.md`; raw probe reports are in the git-ignored `dumps/`.

```bash
uv sync --all-extras --dev && uv run pytest -q && uv run mypy      # must be green before touching hardware
uv run python tools/probe.py --resource 'TCPIP0::192.0.2.10::inst0::INSTR'   # real IP instead of 192.0.2.10
uv run python example_test.py --resource 192.0.2.10
```

Use VXI-11. Port 5025 (raw socket, PG02 §1.2.4) refused the connection on this unit. The probe starts with
`*RST`, so the generator's previous settings are lost; it ends with both outputs off. Check the address
with a single `*IDN?` first: other Siglent instruments on the same LAN answer SCPI too.

## Measured at the output (2026-10-05, generator CH1 -> BNC cable -> DHO814 CH1, 1 Mohm, no load)

SINE 1 kHz 2 Vpp reads 2.04 Vpp, 1000.0 Hz; SQUARE 3 Vpp, offset 1.5 V, duty 30 % reads 3.04 / -0.04 V and
duty 0.300; SINE 1 MHz 4 Vpp reads 4.03 Vpp. `LOAD,50` with `AMP,1` gives the same 2 Vpp into the open
input: `LOAD` only changes what is displayed. `PLRT,INVT` inverts the offset too (+0.5 V set, -0.5 V out).
Found there: `tearDown()` closed PyVISA's process-wide ResourceManager and with it the scope's session;
fixed, the manager is no longer closed.

## Open questions

- Not modelled or not measured (README "Things the manual does not tell you", the addenda of
  `SPEC-hardware-1.md` and `SPEC-modulation.md`): the load rescale for loads other than 50 ohm, the PULSE
  width limit with long edges, the level window above 20 MHz at a numeric load, the minimum burst delay,
  the PWM deviation limit, SDG2082X/SDG2122X limits, USB on the generator.
- Is the raw socket on port 5025 switched off by a setting or missing in firmware 2.01.01.23R7?
- The scope plug has two wishes open on its side (not blocking): a helper that writes a whole state to a
  file, and a `measure()` that hides the first `9.9E+37`.
- Housekeeping for the human: the merged remote branches `station-demo`, `hardware-session-1`, `modulation`
  and `claude/modest-carson-349c1k` can be deleted.
