# STATUS

Handoff state for whoever picks this up next, human or agent. Keep it current; it is committed.
Secrets (real IP, serial number, probe dumps) never go here; they live in the git-ignored `HANDOFF.md`.

## Where we are (2026-10-12)

`main` holds everything. Work now runs from an LXC container on the instruments' LAN; both instruments are
reached over VXI-11 from there (the scope on LAN too, no USB passthrough needed). Done so far:

- **v1 core** (`*IDN?`, `*RST`, `OUTP`, `BSWV`, `apply_setup` with read-back verification) works on the real
  SDG2042X, firmware 2.01.01.23R7, over VXI-11. Facts: `SPEC-hardware-1.md`, README.
- **Station demo** (`examples/station_demo.py`): generator into a Rigol DHO814 in one OpenHTF test; passes on
  both instruments (2026-10-05 over USB, 2026-10-12 over LAN) and with `--fake`. The scope plug is the package
  `rigol-dho-openhtf`, extra `station`, pinned in `[tool.uv.sources]`. Facts: `SPEC-station.md`.
- **Levels into a real 50 ohm load (V1, 2026-10-12)**: the scope reads what `BSWV?` shows at `LOAD,50`, half
  of `AMP` at `LOAD,HZ`, and `LOAD,75` follows the same source model. Facts: README.
- **Modulation, sweep, burst** (`MDWV`, `SWWV`, `BTWV` groups): verified by read-back on the generator
  (2026-10-05) and **at the output with the scope (V2, 2026-10-12)**: AM depth, FM deviation, sweep range and
  period, burst cycle count and period all match the settings. Facts: README "Measured at the output:
  modulation, sweep, burst", `SPEC-modulation.md` addendum 4.
- **Arbitrary waveforms** (`SPEC-arb.md`, 2026-10-12): `ARWV` and `SRATE` setup groups, `upload_arb`, `read_arb`,
  `list_arbs`; two recons, the implementation against the fake, and an acceptance run on the generator with the
  scope (six facts fed back, among them: `*RST` resets the TrueArb rate, built-ins differ in length, a re-upload of
  the selected wave does not change the output until it is selected again). Three transcripts in `tests/data/`.
- 798 tests, `mypy --strict` clean. Branch `v2-signal` holds today's work; CI runs on push.

## Start here

1. `uv sync --all-extras --dev && uv run pytest -q && uv run mypy`.
2. Addresses: the git-ignored `HANDOFF.md` has the generator's and the scope's address and the wiring when it
   exists on this machine; otherwise ask the human. Identify each address with one `*IDN?` first.
3. Next: `SPEC-counter-sync.md` (placeholder): recon on the generator first, then the spec, then a coder, then
   acceptance (the sync pulse can be checked on the scope). Also open: the sweep re-centring question at the end of
   `SPEC-arb.md`, one short experiment.

How a feature gets done here (it worked three times on 2026-10-05): recon on the instrument with a script
that logs every command and reply; the sanitized log becomes a transcript in `tests/data/` that the fake
must replay verbatim; spec; coder against the fake only; then acceptance by the owner agent: a fresh
command sequence sent to the fake and the instrument at once, every reply compared. Each such run found
rules the recon had missed (10, 14 and 5 of them). Differences go back to the coder as numbered facts plus
a new transcript. Last, the plug itself on the instrument: valid setups must verify, wrong ones must raise.

How the owner works since 2026-10-12: briefs to cheaper subagents for scripts, implementation, sanitizing and
docs; the owner reviews, runs the hardware and writes the facts (AGENTS.md, Roles).

How a signal check gets done (V2, 2026-10-12): a one-off script in `dumps/` opens both plugs, saves the scope's
`get_state()` to disk first, applies one setup per case, takes a 100k-point deep-memory capture (`:SING`,
`wait_stopped`, `read_waveform(1, mode='RAW')`), analyses it offline (zero crossings, per-period envelope) and
ends with `tearDown()` on both. Keep the open-circuit peak under 5 V at the scope input.

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
| V1 | Hardware check: signal levels with `LOAD,50` and a real 50 ohm load at the output (a feed-through terminator at the scope input). | done 2026-10-12: the scope reads what `BSWV?` shows at `LOAD,50`; facts in README |
| T8 | `SPEC-modulation.md`: `MDWV`, `SWWV`, `BTWV` as setup groups, fake, tests | done; read-back accepted on the generator 2026-10-05, signal not measured |
| V2 | Hardware check: modulation, sweep and burst at the output with the scope (AM depth, sweep range, burst cycle count). | done 2026-10-12, facts in README and `SPEC-modulation.md` addendum 4 |
| T9 | `SPEC-arb.md`: arbitrary waveforms (`ARWV`, `WVDT`, `STL`, `SRATE`), recon A and B, implementation, acceptance with the scope | done 2026-10-12 |
| later | SPEC-counter-sync | needs a hardware session |

## Hardware access

Hardware sessions run in a Claude Code session on a machine on the instruments' LAN; since 2026-10-12 that is
an LXC container with one NIC on that LAN (a cloud session cannot reach it: only HTTPS leaves the cloud
container). The real addresses and serials are in the git-ignored `HANDOFF.md`; raw captures, one-off scripts
and the saved scope states are in the git-ignored `dumps/`. The sibling projects under `/opt` pass USB devices
or a second NIC into the container with kits in their `host/` directories; this project needs none of that
while both instruments sit on the container's LAN.

```bash
uv sync --all-extras --dev && uv run pytest -q && uv run mypy      # must be green before touching hardware
uv run python tools/probe.py --resource 'TCPIP0::192.0.2.10::inst0::INSTR'   # real IP instead of 192.0.2.10
uv run python example_test.py --resource 192.0.2.10
uv run python examples/station_demo.py --generator 192.0.2.10 --scope 'TCPIP0::192.0.2.11::inst0::INSTR'
```

Use VXI-11 for both. The generator refuses port 5025 (raw socket) on this unit; the scope offers VXI-11 and
port 5555. The probe starts with `*RST`, so the generator's previous settings are lost; it ends with both
outputs off. Check each address with a single `*IDN?` first: other Siglent and Rigol instruments on the same
LAN answer SCPI too.

## Measured at the output (2026-10-05, generator CH1 -> BNC cable -> DHO814 CH1, 1 Mohm, no load)

SINE 1 kHz 2 Vpp reads 2.04 Vpp, 1000.0 Hz; SQUARE 3 Vpp, offset 1.5 V, duty 30 % reads 3.04 / -0.04 V and
duty 0.300; SINE 1 MHz 4 Vpp reads 4.03 Vpp. `LOAD,50` with `AMP,1` gives the same 2 Vpp into the open
input: `LOAD` only changes what is displayed. `PLRT,INVT` inverts the offset too (+0.5 V set, -0.5 V out).
Found there: `tearDown()` closed PyVISA's process-wide ResourceManager and with it the scope's session;
fixed, the manager is no longer closed.

## Open questions

- Not modelled or not measured (README "Things the manual does not tell you", the addenda of
  `SPEC-hardware-1.md` and `SPEC-modulation.md`): the sub-millivolt rounding of the display rescale for loads
  other than 50 ohm (the source model itself held at 75 ohm on 2026-10-12), the PULSE
  width limit with long edges, the level window above 20 MHz at a numeric load, the minimum burst delay,
  the PWM deviation limit, SDG2082X/SDG2122X limits, USB on the generator.
- Is the raw socket on port 5025 switched off by a setting or missing in firmware 2.01.01.23R7?
- The scope plug has two wishes open on its side (not blocking): a helper that writes a whole state to a
  file, and a `measure()` that hides the first `9.9E+37`.
- `examples/station_demo.py` guards the scope input from `AMP` and `OFST` only. A step with `MDWV` AM at
  `DEPTH` above 100 peaks higher (up to `1.1 * AMP`, README); the guard would have to know the groups before
  such steps are added to the demo.
- The generator keeps 11 user waves named `htf_*` from the arb sessions (16 MB `htf_big` among them); PG02 has no
  delete command, the front panel does. Tests and examples reuse `htf_test`.
- The scope drops a setup block while it is in STOP; `:RUN` first (SPEC-station). Handed to the scope project.
- The V2 measurement script lives only in `dumps/` (it carries the addresses). A sanitized, tested version
  under `examples/` would make the signal checks repeatable; not asked for yet.
- Housekeeping for the human: the merged remote branches `station-demo`, `hardware-session-1`, `modulation`
  and `claude/modest-carson-349c1k` can be deleted.
