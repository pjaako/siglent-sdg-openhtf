"""Replay of the arb recon transcripts against the fake: every reply not marked ``#!`` must match verbatim.

``#! `` lines are neither compared nor sent, with one exception: ``#! -> C<n>:WVDT <header>WAVEDATA,<N bytes ...>``
is replayed as an upload of N zero bytes under the logged name and header (``write_raw``), so that the selections,
the ``STL? USER`` replies and the file headers that follow replay. The data itself is never compared.
"""

import re
from pathlib import Path

from siglent_sdg_openhtf.fake_resource import FakeSdgResource

DATA = Path(__file__).parent / "data"

# Reply lines (1-based) the fake does not reproduce, per transcript file, each with its reason.
# At most 8 entries in total.
UNMODELLED: dict[str, dict[int, str]] = {
    "hardware_arb_1.txt": {},
    "hardware_arb_2.txt": {
        97: 'the generator stores the quoted name "htf b5-x" as "htfb5-x" (space dropped, quotes kept); the fake keeps the name as given',
    },
}

_UPLOAD = re.compile(r"^(?P<header>C[12]:WVDT .*WAVEDATA,)<(?P<n>\d+) bytes sha1=\S+ first=\S*>$")


def _replay(name: str) -> int:
    fake = FakeSdgResource(serial="SDG2XCAQ000000", firmware="2.01.01.23R7")  # the sanitized identity of the transcripts
    compared = 0
    pending: tuple[int, str] | None = None  # (line number, query) waiting for its reply
    pending_skip = False
    last_reply = ""
    skipped = UNMODELLED[name]
    for number, raw in enumerate((DATA / name).read_text().splitlines(), start=1):
        if raw.startswith("#! "):
            upload = _UPLOAD.match(raw[3:].removeprefix("-> "))
            if raw.startswith("#! -> ") and upload is not None:
                # a synthetic upload of N zero bytes under the logged header (PG02 §3.32)
                fake.write_raw(upload["header"].encode() + bytes(int(upload["n"])) + b"\n")
            continue  # every other '#! ' line is neither sent nor compared
        if not raw.strip() or raw.startswith("#"):
            continue  # notes, section titles, blank lines
        if raw.startswith("-> "):
            command = raw[3:]
            if command.split(" ")[0].endswith("?"):  # a query: "C1:ARWV?", "STL? USER"
                last_reply = fake.query(command)  # PG02 §3.1, §3.9.1, §3.31, §3.40, §3.3, §3.4 as in the transcript
                pending = (number, command)
            else:
                fake.write(command)  # PG02 §3.1, §3.9.1, §3.40, §3.3, §3.4 as in the transcript
                pending = None
        elif raw.startswith("<- "):
            assert pending is not None, f"line {number}: reply without a query"
            if number not in skipped:
                expected = raw[3:]
                assert last_reply == expected, (
                    f"{name} line {number}: {pending[1]!r}\n  expected {expected!r}\n  got      {last_reply!r}"
                )
                compared += 1
            pending = None
        else:
            raise AssertionError(f"{name} line {number}: unrecognised line {raw!r}")
    return compared


def test_unmodelled_list_is_short() -> None:
    assert sum(len(entries) for entries in UNMODELLED.values()) <= 8
    assert all(reason for entries in UNMODELLED.values() for reason in entries.values())


def _expected_replies(name: str) -> int:
    """Every ``<- `` line that is not marked ``#! `` and not listed as unmodelled."""
    lines = (DATA / name).read_text().splitlines()
    return sum(1 for raw in lines if raw.startswith("<- ")) - len(UNMODELLED[name])


# The transcripts hold 75 and 70 unmarked replies (the spec expected 120 and 90, which the files cannot supply).
def test_transcript_a_replays_verbatim() -> None:
    compared = _replay("hardware_arb_1.txt")
    assert compared == _expected_replies("hardware_arb_1.txt")
    assert compared >= 75


def test_transcript_b_replays_verbatim() -> None:
    compared = _replay("hardware_arb_2.txt")
    assert compared == _expected_replies("hardware_arb_2.txt")
    assert compared >= 69


def test_unmodelled_lines_are_replies_in_the_transcript() -> None:
    for name, entries in UNMODELLED.items():
        lines = (DATA / name).read_text().splitlines()
        for number in entries:
            assert lines[number - 1].startswith("<- "), f"{name} line {number} is not a reply"
