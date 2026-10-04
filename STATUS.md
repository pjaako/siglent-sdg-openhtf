# STATUS

Handoff state for whoever picks this up next, human or agent. Keep it current; it is committed.
Secrets (real IP, serial number, probe dumps) never go here; they live in the git-ignored `HANDOFF.md`.

## Where we are (2026-10-05)

**The first hardware session is done; v1 core works on the real SDG2042X (firmware 2.01.01.23R7, VXI-11).**
`tools/probe.py` (plain and `--risky`) and `example_test.py --resource <ip>` ran on the generator from a
local session, branch `hardware-session-1`. Measured facts: `SPEC-hardware-1.md`; verbatim transcript:
`tests/data/hardware_session_1.txt`; T6 folds them into the fake, the validator and README.

- Research done: no OpenHTF plug exists for the SDG2000X family; the design mirrors `rigol-dho-openhtf`.
- Official programming guide PG02-E05C is in `docs/` (PDF + text). It is the only source of SCPI.
- Owner decisions: LAN primary, USB optional; v1 = `*IDN?`, `*RST`, `BSWV`, `OUTP`, `apply_setup`
  with read-back verification; `tearDown` = outputs off; proper package + CI; Python 3.13;
  Sonnet coder agents implement, owner agent specs/reviews/integrates.
- R1 (review fix-up) is done: validator tightened (OFST with HLEV/LLEV, PHSE/SYM/DUTY/MAX_OUTPUT_AMP ranges, reduced
  amplitude limit at any numeric load, OFST limit), `build_command`, `plug closed` error after `tearDown`, USB
  discovery prefers `SDG` serials, version from package metadata, fake `write_termination` `\n`. Hypotheses added
  (all `# hypothesis until hardware session 1`): `DLY` is PULSE-only; the offset limit halves at a numeric load.
- `SPEC.md` (v1 core) is written. Placeholders: `SPEC-arb.md`, `SPEC-modulation.md`,
  `SPEC-counter-sync.md`, `SPEC-station.md`.

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
| T7 | `SPEC-station.md` → `examples/station_demo.py` with rigol-dho-openhtf | **next** |
| later | SPEC-arb, SPEC-modulation, SPEC-counter-sync | each needs a hardware session |

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

## Open questions

- How to import `rigol-dho-openhtf` for the station demo (it has no `pyproject.toml`).
- Not modelled or not measured in session 1 (README "Things the manual does not tell you",
  `SPEC-hardware-1.md` addendum): the load rescale for loads other than 50 ohm, the PULSE width limit with
  long edges, the level window above 20 MHz at a numeric load, SDG2082X/SDG2122X limits, USB.
- Is the raw socket on port 5025 switched off by a setting or missing in firmware 2.01.01.23R7?
- Nothing was measured at the BNC connectors: session 1 verified the read-back, not the signal. T7 (scope
  on the output) closes that.
