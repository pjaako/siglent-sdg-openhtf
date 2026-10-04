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
| STOP | First hardware session: `tools/probe.py` and `example_test.py` on the SDG2042X, from a local session on the LAN | **next: local session (see Hardware access)** |
| T6 | Fold hardware findings into the fake, README "Things the manual does not tell you", tolerances | after STOP |
| T7 | `SPEC-station.md` → `examples/station_demo.py` with rigol-dho-openhtf | after T6 |
| later | SPEC-arb, SPEC-modulation, SPEC-counter-sync | each needs a hardware session |

## Hardware access

**Decision (2026-10-04): the first hardware session runs in a local Claude Code session on a machine on
the generator's LAN.** A cloud session cannot reach the generator: the cloud container only passes
HTTPS on port 443 through an inspecting egress gateway; raw TCP (port 5025) and non-TLS tunnels are
blocked by design, which was tested with a forwarded port and ruled out. PR #1
(https://github.com/pjaako/siglent-sdg-openhtf/pull/1) stays watched by the cloud owner agent; the owner merges.

Kickoff for the local session (agent or human), on the LAN machine:
```bash
git clone https://github.com/pjaako/siglent-sdg-openhtf && cd siglent-sdg-openhtf
git checkout claude/modest-carson-349c1k
uv sync --all-extras --dev && uv run pytest -q && uv run mypy      # must be green before touching hardware
uv run python tools/probe.py --resource 'TCPIP0::192.0.2.10::5025::SOCKET'   # real IP instead of 192.0.2.10
# read dumps/probe-*.md; if the generator survived everything, once more with --risky (SYST:ERR?, *CLS)
uv run python example_test.py --resource 192.0.2.10
```
The probe only sends PG02-documented commands (the two `--risky` ones excepted), never changes LAN
settings, and ends with both outputs off. Keep the power switch within reach for `--risky`. Expect the
first `example_test.py` run to fail if real replies differ from the PG02 examples (for instance a bare
`MAX_OUTPUT_AMP` token or `AMPVRMS` fields); the probe report shows the raw replies either way.

Then T6: with the probe report in hand, write `SPEC-hardware-1.md` (Rigol SPEC template, "measured
facts" section filled from the report, real address and serial replaced by placeholders) and delegate
to a coder: fake defaults, key sets per WVTP, number formats, tolerances, `parse_reply` tolerance if a
dangling key is real; README "Facts (measured on the generator)" and "Things the manual does not tell
you"; firmware version in README's first paragraph. Real addresses, serial and the dumps stay in
git-ignored `HANDOFF.md` / `dumps/`. Commit with the attribution lines from AGENTS.md and push to this branch.

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
