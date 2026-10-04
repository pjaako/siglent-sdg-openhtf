# STATUS

Handoff state for whoever picks this up next, human or agent. Keep it current; it is committed.
Secrets (real IP, serial number, probe dumps) never go here; they live in the git-ignored `HANDOFF.md`.

## Where we are (2026-10-04)

- Research done: no OpenHTF plug exists for the SDG2000X family; the design mirrors `rigol-dho-openhtf`.
- Official programming guide PG02-E05C is in `docs/` (PDF + text). It is the only source of SCPI.
- Owner decisions: LAN primary, USB optional; v1 = `*IDN?`, `*RST`, `BSWV`, `OUTP`, `apply_setup`
  with read-back verification; `tearDown` = outputs off; proper package + CI; Python 3.13;
  Sonnet coder agents implement, owner agent specs/reviews/integrates.
- `SPEC.md` (v1 core) is written. Placeholders: `SPEC-arb.md`, `SPEC-modulation.md`,
  `SPEC-counter-sync.md`, `SPEC-station.md`.

## Task board

| # | Task | State |
|---|---|---|
| T0 | Branch, AGENTS.md, SPEC.md, placeholders, README stub, STATUS.md, LICENSE, docs text | done |
| T1 | Packaging: pyproject, uv.lock, package skeleton, CI, test_import | done |
| T2 | `scpi.py` + `models.py` + `tests/test_scpi.py` | todo (parallel with T3) |
| T3 | `fake_resource.py` + `tests/test_fake_resource.py` | todo (parallel with T2) |
| T4 | `plug.py` + `tests/test_plug.py` | todo |
| T5 | `example_test.py`, `tools/probe.py`, `tests/test_examples.py`, README usage | todo |
| STOP | First hardware session: owner runs `tools/probe.py` and `example_test.py` on the SDG2042X | blocked on hardware access |
| T6 | Fold hardware findings into the fake, README "Things the manual does not tell you", tolerances | after STOP |
| T7 | `SPEC-station.md` → `examples/station_demo.py` with rigol-dho-openhtf | after T6 |
| later | SPEC-arb, SPEC-modulation, SPEC-counter-sync | each needs a hardware session |

## Hardware access

Not available from the cloud session. Options agreed with the owner: forward the generator's raw socket
port 5025 to the internet (VXI-11 uses the portmapper and dynamic ports, which forward badly), or
continue on a machine on the generator's LAN. The first hardware session runs `tools/probe.py`
(items listed in `SPEC.md` §8) and records the results in README and in the fake.

## Open questions

- Does the SDG2042X accept `1E-06`-style exponents on write? (`format_value` uses `.9G`.)
- Does `C1:BSWV?` on real firmware include `MAX_OUTPUT_AMP` (PG02 §3.3 lists it in the response format
  without a value)?
- Does `SYST:ERR?` exist on this firmware? It is not in PG02. Probe item 11, risky.
- How to import `rigol-dho-openhtf` for the station demo (it has no `pyproject.toml`).
