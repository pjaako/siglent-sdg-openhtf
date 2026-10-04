"""Hardware probe for the Siglent SDG2042X. Written for the owner; coder agents never run it.

Usage:
    python tools/probe.py --resource 192.0.2.10 [--out dumps] [--risky]

Sends the items of SPEC section 8 in order, records every command sent and every reply received (or the
exception), and writes ``<out>/probe-<YYYYMMDD-HHMMSS>.md``, mirrored on stdout. Every item catches its
exceptions, records them and lets the next item run. Outputs are switched off at the end (``tearDown``).

Every SCPI literal carries its PG02 section (``docs/PG02-E05C.txt``). Items 7 to 9 deliberately send raw
forms (exponent notation, several pairs in one BSWV command, an out-of-range LOAD) that the plug itself
would never send: finding out whether the generator accepts them is the point. ``--risky`` adds commands
that PG02 does not define; they may hang the generator's VXI-11 service until a power cycle.
"""

import argparse
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from siglent_sdg_openhtf import SiglentSdgPlug
from siglent_sdg_openhtf.plug import CONF
from siglent_sdg_openhtf.scpi import Setup

# Example setup of SPEC section 7 (item 10).
EXAMPLE_SETUP: Setup = {
    "C1": {
        "OUTP": {"LOAD": 50, "STATE": True},
        "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
    }
}
WAVE_TYPES = ("SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "DC", "ARB")  # PG02 §3.4 WVTP, SDG2000X column
LATENCY_SAMPLES = 50


class Recorder:
    """Logs every command and reply that passes through the plug's ``write`` and ``query``.

    ``install`` replaces both methods on the plug instance with wrappers, so commands sent by the plug's
    own methods (``apply_setup``, ``tearDown``) are recorded as well. Lines: ``-> command``, ``<- reply``,
    ``!! ExceptionType: message``.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.muted = False

    def _log(self, line: str) -> None:
        if not self.muted:
            self.lines.append(line)

    def install(self, plug: SiglentSdgPlug) -> None:
        plain_write, plain_query = plug.write, plug.query

        def write(cmd: str) -> None:
            self._log(f"-> {cmd}")
            try:
                plain_write(cmd)
            except Exception as exc:
                self._log(f"!! {type(exc).__name__}: {exc}")
                raise

        def query(cmd: str) -> str:
            self._log(f"-> {cmd}")
            try:
                reply = plain_query(cmd)
            except Exception as exc:
                self._log(f"!! {type(exc).__name__}: {exc}")
                raise
            self._log(f"<- {reply}")
            return reply

        setattr(plug, "write", write)
        setattr(plug, "query", query)

    def take(self) -> list[str]:
        lines, self.lines = self.lines, []
        return lines


class Report:
    """Appends to the report file and echoes to stdout, section by section (a hang loses nothing)."""

    def __init__(self, path: Path, stream: TextIO) -> None:
        self.path = path
        self._stream = stream

    def emit(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        self._stream.write(text)
        self._stream.flush()

    def section(self, title: str, lines: Sequence[str], notes: Sequence[str] = ()) -> None:
        body = "\n".join(lines) if lines else "(nothing sent)"
        text = f"## {title}\n\n```\n{body}\n```\n\n"
        if notes:
            text += "\n".join(f"- {note}" for note in notes) + "\n\n"
        self.emit(text)


class Probe:
    """The items. ``send`` / ``ask`` swallow the exception (the recorder has logged it) and return ``None``."""

    def __init__(self, plug: SiglentSdgPlug, recorder: Recorder) -> None:
        self.plug = plug
        self.rec = recorder

    def send(self, cmd: str) -> None:
        try:
            self.plug.write(cmd)
        except Exception:
            pass

    def ask(self, cmd: str) -> str | None:
        try:
            return self.plug.query(cmd)
        except Exception:
            return None

    def item_1_idn(self) -> list[str]:
        self.ask("*IDN?")  # PG02 §3.1.1
        return []

    def item_2_reset_and_defaults(self) -> list[str]:
        start = time.perf_counter()
        self.send("*RST")  # PG02 §3.1.3
        reply = self.ask("*OPC?")  # PG02 §3.1.2
        elapsed_ms = (time.perf_counter() - start) * 1000
        notes = [f"*RST + *OPC? took {elapsed_ms:.1f} ms; *OPC? reply: {reply!r}"]
        for channel in ("C1", "C2"):
            self.ask(f"{channel}:BSWV?")  # PG02 §3.4
            self.ask(f"{channel}:OUTP?")  # PG02 §3.3
        return notes

    def item_3_wave_types(self) -> list[str]:
        for wave_type in WAVE_TYPES:
            self.send(f"C1:BSWV WVTP,{wave_type}")  # PG02 §3.4
            self.ask("C1:BSWV?")  # PG02 §3.4
        return []

    def item_4_load(self) -> list[str]:
        self.send("C1:OUTP LOAD,50")  # PG02 §3.3
        self.ask("C1:OUTP?")  # PG02 §3.3
        self.send("C1:OUTP LOAD,HZ")  # PG02 §3.3
        self.ask("C1:OUTP?")  # PG02 §3.3
        return []

    def item_5_amplitude_rescale(self) -> list[str]:
        self.send("C1:BSWV WVTP,SINE")  # PG02 §3.4
        self.send("C1:BSWV AMP,2")  # PG02 §3.4
        self.ask("C1:BSWV?")  # PG02 §3.4 (baseline)
        self.send("C1:OUTP LOAD,50")  # PG02 §3.3
        self.ask("C1:BSWV?")  # PG02 §3.4
        self.send("C1:OUTP LOAD,HZ")  # PG02 §3.3
        self.ask("C1:BSWV?")  # PG02 §3.4
        return []

    def item_6_precision(self) -> list[str]:
        for key, value in (("FRQ", "1234.5678901"), ("AMP", "2.0005"), ("OFST", "0.0005"), ("PHSE", "12.345")):
            self.send(f"C1:BSWV {key},{value}")  # PG02 §3.4
            self.ask("C1:BSWV?")  # PG02 §3.4
        return []

    def item_7_exponent_forms(self) -> list[str]:
        for value in ("1E3", "1e3", "1E-03", "1000"):
            self.send(f"C1:BSWV FRQ,{value}")  # PG02 §3.4 (exponent form: BANDWIDTH,100E6 example)
            self.ask("C1:BSWV?")  # PG02 §3.4
        return []

    def item_8_out_of_range(self) -> list[str]:
        self.send("C1:BSWV FRQ,50E6")  # PG02 §3.4 (SINE; above the SDG2042X limit, hypothesis: silently ignored)
        self.ask("C1:BSWV?")  # PG02 §3.4
        self.send("C1:OUTP LOAD,10")  # PG02 §3.3 (LOAD range is 50..100000: deliberately out of range)
        self.ask("C1:OUTP?")  # PG02 §3.3
        return []

    def item_9_multi_pair(self) -> list[str]:
        self.send("C1:BSWV WVTP,SINE,FRQ,1000,AMP,2")  # PG02 §3.4 (several pairs in one command: syntax list, no example)
        self.ask("C1:BSWV?")  # PG02 §3.4
        return []

    def item_10_latency(self) -> list[str]:
        samples_ms: list[float] = []
        failure: str | None = None
        self.rec.muted = True  # 50 identical exchanges would drown the report; the summary below replaces them
        try:
            for _ in range(LATENCY_SAMPLES):
                start = time.perf_counter()
                try:
                    self.plug.query("*IDN?")  # PG02 §3.1.1
                except Exception as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    break
                samples_ms.append((time.perf_counter() - start) * 1000)
        finally:
            self.rec.muted = False
        notes: list[str] = []
        if samples_ms:
            ordered = sorted(samples_ms)
            p95 = ordered[max(0, -(-95 * len(ordered) // 100) - 1)]  # nearest rank
            notes.append(
                f"*IDN? x {len(samples_ms)}: median {statistics.median(ordered):.2f} ms, p95 {p95:.2f} ms, "
                f"min {ordered[0]:.2f} ms, max {ordered[-1]:.2f} ms"
            )
        if failure is not None:
            notes.append(f"*IDN? latency loop stopped after {len(samples_ms)} samples: {failure}")
        start = time.perf_counter()
        outcome = "ok"
        try:
            self.plug.apply_setup(EXAMPLE_SETUP)  # PG02 §3.3, §3.4 (commands as logged below)
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
        notes.append(f"apply_setup(example setup) wall time {(time.perf_counter() - start) * 1000:.1f} ms, result: {outcome}")
        return notes

    def item_11_risky(self) -> list[str]:
        self.ask("SYST:ERR?")  # NOT in PG02 — probe only, --risky
        self.send("*CLS")  # NOT in PG02 — probe only, --risky
        return []


def _items(probe: Probe, risky: bool) -> list[tuple[str, Callable[[], list[str]]]]:
    items: list[tuple[str, Callable[[], list[str]]]] = [
        ("1. *IDN?", probe.item_1_idn),
        ("2. *RST, *OPC? and the default state", probe.item_2_reset_and_defaults),
        ("3. Waveform types: BSWV WVTP,<t> then BSWV?", probe.item_3_wave_types),
        ("4. OUTP LOAD 50 and HZ", probe.item_4_load),
        ("5. Amplitude rescale when LOAD changes (SINE, AMP 2)", probe.item_5_amplitude_rescale),
        ("6. Number precision on write", probe.item_6_precision),
        ("7. Exponent forms of FRQ", probe.item_7_exponent_forms),
        ("8. Out-of-range values (FRQ 50E6, LOAD 10)", probe.item_8_out_of_range),
        ("9. Several BSWV pairs in one command", probe.item_9_multi_pair),
        ("10. Latency and apply_setup wall time", probe.item_10_latency),
    ]
    if risky:
        items.append(("11. RISKY, not in PG02: SYST:ERR? and *CLS", probe.item_11_risky))
    return items


def _run_items(probe: Probe, report: Report, risky: bool) -> None:
    for title, item in _items(probe, risky):
        notes: list[str] = []
        probe.rec.take()
        try:
            notes = item()
        except Exception as exc:  # item bodies already swallow command errors; this is the safety net
            probe.rec.lines.append(f"!! item failed: {type(exc).__name__}: {exc}")
        report.section(title, probe.rec.take(), notes)


def main(argv: Sequence[str] | None = None, *, resource: Any = None, stream: TextIO | None = None) -> int:
    """Run the probe; returns 0 when it ran to the end (item failures are in the report), 1 if the generator could not be opened.

    ``resource`` is a hook for tests: an object with the PyVISA resource surface (``FakeSdgResource``) used
    instead of opening ``--resource``.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resource", required=True, metavar="NAME", help="bare IP/hostname or VISA resource name")
    parser.add_argument("--out", default="dumps", help="directory for the report (default: dumps, git-ignored)")
    parser.add_argument("--risky", action="store_true", help="also send commands that PG02 does not define (item 11)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    path = out_dir / f"probe-{started:%Y%m%d-%H%M%S}.md"
    report = Report(path, stream if stream is not None else sys.stdout)
    report.emit(
        f"# Siglent SDG probe {started:%Y-%m-%d %H:%M:%S}\n\n"
        f"- resource: `{args.resource}`\n- report file: `{path}`\n- risky item 11: {'yes' if args.risky else 'no'}\n\n"
    )

    plug: SiglentSdgPlug | None = None
    recorder = Recorder()
    try:
        try:
            if resource is None:
                CONF.load(siglent_sdg_resource=args.resource)  # after importing the plug module
            plug = SiglentSdgPlug(resource=resource)
        except Exception as exc:
            report.section("0. Open the generator", [f"!! {type(exc).__name__}: {exc}"])
            return 1
        recorder.install(plug)
        report.emit(f"- identity: `{plug.identity.raw}`\n- limits table: `{plug.limits.model}`\n\n")
        _run_items(Probe(plug, recorder), report, args.risky)
        return 0
    finally:
        if plug is not None:
            plug.tearDown()  # C1:OUTP OFF, C2:OUTP OFF (PG02 §3.3), then close; never raises
            report.section("Teardown", recorder.take())


if __name__ == "__main__":
    sys.exit(main())
