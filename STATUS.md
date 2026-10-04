# STATUS

Handoff state for whoever picks this up next, human or agent. Keep it current; it is committed.
Secrets (real IP, serial number, probe dumps) never go here; they live in the git-ignored `HANDOFF.md`.

## Where we are (2026-10-04, evening)

**v1 core is complete against the fake and waits for the first hardware session.** 464 tests, mypy
strict clean, `example_test.py --fake` passes. Nothing has touched a real generator yet. Next action is
the owner's: give an agent access to the SDG2042X and run `tools/probe.py` (see "Hardware access").


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
| STOP | First hardware session: owner runs `tools/probe.py` and `example_test.py` on the SDG2042X | **next, blocked on hardware access** |
| T6 | Fold hardware findings into the fake, README "Things the manual does not tell you", tolerances | after STOP |
| T7 | `SPEC-station.md` → `examples/station_demo.py` with rigol-dho-openhtf | after T6 |
| later | SPEC-arb, SPEC-modulation, SPEC-counter-sync | each needs a hardware session |

## Hardware access

First session, step by step (agent or human):
```bash
uv sync --all-extras --dev
uv run python tools/probe.py --resource 'TCPIP0::192.0.2.10::5025::SOCKET'   # raw socket; or TCPIP0::<ip>::inst0::INSTR on the LAN
# read dumps/probe-*.md; if the generator survived everything, once more with --risky (SYST:ERR?, *CLS)
uv run python example_test.py --resource 192.0.2.10
```
The probe never changes LAN settings and ends with both outputs off. Keep the generator's power switch
within reach for the `--risky` run.

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
- Does `C1:BSWV?` echo `AMPVRMS`/`AMPDBM` (PG02 §3.3 response format lists `AMPVRMS`, the §3.4 example
  does not)? Until measured, `apply_setup` with `verify=True` reports those keys as "not echoed"; use `AMP`.
- Does the generator reject `HLEV` below the current `LLEV`? The fake assumes so (hypothesis); the plug
  writes `HLEV` then `LLEV`.
