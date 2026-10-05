"""Replay of the transcript of hardware session 1 against the fake: every reply must match verbatim."""

from pathlib import Path

from siglent_sdg_openhtf.fake_resource import FakeSdgResource

TRANSCRIPT = Path(__file__).parent / "data" / "hardware_session_1.txt"


def test_transcript_replays_verbatim() -> None:
    fake = FakeSdgResource()
    compared = 0
    pending: tuple[int, str] | None = None  # (line number, query) waiting for its reply
    pending_skip = False
    last_reply = ""
    for number, raw in enumerate(TRANSCRIPT.read_text().splitlines(), start=1):
        skip = raw.startswith("#! ")
        line = raw[3:] if skip else raw
        if line.startswith("-> "):
            command = line[3:]
            if command.endswith("?"):
                last_reply = fake.query(command)  # PG02 §3.1, §3.3, §3.4 queries as in the transcript
                pending, pending_skip = (number, command), skip
            else:
                fake.write(command)  # PG02 §3.1, §3.3, §3.4 writes as in the transcript
                pending = None
        elif line.startswith("<- "):
            assert pending is not None, f"line {number}: reply without a query"
            if not pending_skip:
                expected = line[3:]
                assert last_reply == expected, (
                    f"line {number}: {pending[1]!r}\n  expected {expected!r}\n  got      {last_reply!r}"
                )
                compared += 1
            pending = None
    # every '<- ' line of the file that is not marked '#! ' is compared
    assert compared == sum(1 for raw in TRANSCRIPT.read_text().splitlines() if raw.startswith("<- "))
    assert compared >= 380
