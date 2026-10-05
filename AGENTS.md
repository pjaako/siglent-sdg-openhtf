# AGENTS.md

OpenHTF plug for the Siglent SDG2042X arbitrary waveform generator (SDG2000X family). Read `SPEC.md`;
it is the contract. Read `STATUS.md`; it says where the project stands and what comes next.

- **The only source of SCPI is the official Siglent guide** `docs/SDG_Programming Guide_PG02-E05C.pdf`
  (text extraction for grep: `docs/PG02-E05C.txt`, made with `pdftotext -layout`). Every SCPI string in
  code, tests and the fake carries a comment naming its section, e.g. `# PG02 §3.4 BSWV`. Use the
  SDG2000X column of the per-model availability tables. Third-party drivers and forum posts are hints
  for what to measure on hardware, never a source of commands. Nothing that PG02 does not define is
  ever sent: on this family an undefined query can hang the generator's VXI-11 service until a power
  cycle. `docs/SDG1000X_Plus_ProgrammingGuide_EN01B.pdf` is a different family; do not take commands
  from it.
- Coder agents never touch the real generator; they use `siglent_sdg_openhtf.fake_resource.FakeSdgResource`.
  The project-owner agent runs hardware sessions; findings go into `README.md`
  ("Things the manual does not tell you") and back into the fake. `SPEC.md` is the original contract;
  where it disagrees with README, README wins: it records what the real generator does.
- Everything must run without hardware: `uv run pytest -q`, `uv run mypy`,
  `uv run python example_test.py --fake`. CI runs the same.
- Python env: `uv sync --all-extras --dev` creates `.venv/` from `pyproject.toml` / `uv.lock`
  (Python 3.13, openhtf 1.6.1, pyvisa, pyvisa-py, pytest, mypy). Do not add dependencies without a
  SPEC saying so.
- Do not reinvent transports: PyVISA handles VXI-11, raw sockets and USBTMC. The plug accepts any
  PyVISA message-based resource (or the fake) through its constructor.
- LAN is the primary transport (`TCPIP0::<ip>::inst0::INSTR`, or `TCPIP0::<ip>::5025::SOCKET`,
  PG02 §1.2.4). USB is optional and auto-discovered by vendor id `0xF4EC`. Reports from other projects
  say USB via pyvisa-py is unreliable on this family; treat USB findings as unverified until measured.
- The plug stays thin: no setter per generator setting. Test conditions are data (a dict keyed by
  PG02 mnemonics: channel → `OUTP`/`BSWV` → parameter → value); the plug validates them before
  sending anything, writes them in a safe order, reads back and verifies, and reports every mismatch
  at once. The read-back is the arbiter: PG02 documents no error queue for this family.
- `tearDown()` turns both outputs off (`C1:OUTP OFF`, `C2:OUTP OFF`) and closes the resource. It
  never restores other settings and never raises. Hand-written hardware scripts wrap their work in
  `try: ... finally: plug.tearDown()`.
- A fake instrument only knows what we told it. Until the first hardware session the fake's defaults,
  key sets per waveform type, number formats and tolerances are hypotheses built from PG02 examples;
  they are marked as such in the code. Every feature gets a run on hardware before it is called done,
  and each finding goes back into the fake.
- A fake is accepted only against the instrument: send a command sequence the fake was not built from to
  the fake and to the generator at once and compare every reply. Replaying the recon transcript proves only
  that the fake matches what was already asked. Sanitized transcripts live in `tests/data/`; `#!` marks
  what was measured but is not modelled.
- Before sending anything to an address, identify the instrument with a single `*IDN?`: other Siglent
  instruments on the same LAN answer SCPI too, and the probe starts with `*RST`.
- Other instruments in the same process share PyVISA's ResourceManager; never close it. An instrument that
  is not ours (the scope) gets its state saved to disk before the first change and restored at the end.
- Set OpenHTF config keys after importing the plug module (`CONF.load(...)` before the key is declared
  is lost).
- `mypy --strict` (configured in `pyproject.toml`) must stay clean.
- Commits: author is the owner; every commit message ends with
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_016T6mysSFTNJPE4srnUXug4`. No model identifiers in
  code or comments.
- Workflow: the project-owner agent writes `SPEC*.md`, delegates implementation to coder agents, reviews,
  integrates, commits and pushes after each task, and keeps `STATUS.md`, `README.md` and the SPECs
  current so a cold agent can take over at any moment. Hardware is needed only after the fake-backed
  suite is green; the owner agent stops and asks the human before the first hardware session.
- This repository is public. Addresses in the text are documentation examples (`192.0.2.x`); serial
  numbers are placeholders. Real addresses, serial numbers, probe dumps (`dumps/`) and `HANDOFF.md`
  stay out of git.
