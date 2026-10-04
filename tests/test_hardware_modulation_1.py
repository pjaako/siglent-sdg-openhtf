"""Replay of the modulation transcripts against the fake: every reply must match verbatim."""

from pathlib import Path

from siglent_sdg_openhtf.fake_resource import FakeSdgResource

DATA = Path(__file__).parent / "data"

# Reply lines (1-based) the fake does not reproduce, per transcript file, each with its reason.
# At most 12 entries in total.
UNMODELLED: dict[str, dict[int, str]] = {
    "hardware_modulation_1.txt": {
        547: "PULSE carrier at 7 kHz: DUTY 99.9888 means a maximum width of PERI - 16.0 ns, the fake uses 16.3 ns",
        557: "RAMP AMPVRMS at AMP 5.21739 reads 1.50617, 1/3.464 of the stored amplitude gives 1.50618; rounding the amplitude first breaks the session 1 replay",
    },
    "hardware_modulation_2.txt": {},
    "hardware_modulation_3.txt": {},
}


def _replay(name: str) -> int:
    fake = FakeSdgResource()
    compared = 0
    pending: tuple[int, str] | None = None  # (line number, query) waiting for its reply
    pending_skip = False
    last_reply = ""
    skipped = UNMODELLED[name]
    for number, raw in enumerate((DATA / name).read_text().splitlines(), start=1):
        skip = raw.startswith("#! ")
        line = raw[3:] if skip else raw
        if line.startswith("-> "):
            command = line[3:]
            if command.endswith("?"):
                last_reply = fake.query(command)  # PG02 §3.5, §3.6.1, §3.7 queries as in the transcript
                pending, pending_skip = (number, command), skip
            else:
                fake.write(command)  # PG02 §3.5, §3.6.1, §3.7 writes as in the transcript
                pending = None
        elif line.startswith("<- "):
            assert pending is not None, f"line {number}: reply without a query"
            if not pending_skip and number not in skipped:
                expected = line[3:]
                assert last_reply == expected, (
                    f"{name} line {number}: {pending[1]!r}\n  expected {expected!r}\n  got      {last_reply!r}"
                )
                compared += 1
            pending = None
    return compared


def test_unmodelled_list_is_short() -> None:
    assert sum(len(entries) for entries in UNMODELLED.values()) <= 12


def test_transcript_1_replays_verbatim() -> None:
    assert _replay("hardware_modulation_1.txt") >= 155


def test_transcript_2_replays_verbatim() -> None:
    assert _replay("hardware_modulation_2.txt") >= 300


def test_transcript_3_replays_verbatim() -> None:
    assert _replay("hardware_modulation_3.txt") >= 150
