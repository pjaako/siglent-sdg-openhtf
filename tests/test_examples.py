"""Tests for ``example_test.py`` and ``tools/probe.py``. No hardware, no network: the example runs with
``--fake`` and the probe runs against a ``FakeSdgResource`` injected through its ``resource`` hook.
"""

import importlib
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyvisa
import pytest

from siglent_sdg_openhtf.fake_resource import FakeSdgResource
from tools import probe

REPO_ROOT = Path(__file__).resolve().parent.parent

ITEM_TITLES = ["## 1. ", "## 2. ", "## 3. ", "## 4. ", "## 5. ", "## 6. ", "## 7. ", "## 8. ", "## 9. ", "## 10. "]


def test_example_runs_with_fake() -> None:
    result = subprocess.run(
        [sys.executable, "example_test.py", "--fake"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "example: PASS" in result.stdout
    assert "example: FAIL" not in result.stdout


@pytest.mark.parametrize("module", ["example_test", "tools.probe"])
def test_import_opens_no_resource(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("importing opened a VISA ResourceManager")

    monkeypatch.setattr(pyvisa, "ResourceManager", refuse)
    monkeypatch.delitem(sys.modules, module, raising=False)  # fresh import; restored after the test
    importlib.import_module(module)


def test_example_setup_is_the_spec_setup() -> None:
    import example_test

    assert example_test.SETUP == {
        "C1": {
            "OUTP": {"LOAD": 50, "STATE": True},
            "BSWV": {"WVTP": "SINE", "FRQ": 1000.0, "AMP": 2.0, "OFST": 0.0},
        }
    }


# -- probe ------------------------------------------------------------------------------------------------


def _run_probe(tmp_path: Path, fake: FakeSdgResource, *flags: str) -> tuple[int, str, Path]:
    out = tmp_path / "nested" / "dumps"  # the probe creates the directory
    stream = io.StringIO()
    code = probe.main(["--resource", "fake", "--out", str(out), *flags], resource=fake, stream=stream)
    reports = list(out.glob("probe-*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert stream.getvalue() == text  # stdout mirrors the file
    return code, text, reports[0]


def test_probe_writes_report_with_every_item(tmp_path: Path) -> None:
    fake = FakeSdgResource()
    code, text, path = _run_probe(tmp_path, fake)
    assert code == 0
    assert path.name.startswith("probe-") and path.suffix == ".md"
    assert "`fake`" in text  # resource name
    assert str(path) in text  # output file name
    for title in ITEM_TITLES:
        assert title in text
    assert "## 11." not in text and "SYST:ERR?" not in text
    # exact commands and replies
    assert "-> *IDN?\n<- Siglent Technologies,SDG2042X," in text
    assert "-> C1:BSWV WVTP,SQUARE\n-> C1:BSWV?\n<- C1:BSWV WVTP,SQUARE," in text
    assert "-> C1:BSWV WVTP,SINE,FRQ,1000,AMP,2" in text
    assert "-> C1:BSWV FRQ,1E-03" in text
    assert "-> C1:OUTP LOAD,10" in text
    for wave_type in ("SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "DC", "ARB"):
        assert f"-> C1:BSWV WVTP,{wave_type}\n" in text
    assert "median" in text and "p95" in text and "apply_setup(example setup) wall time" in text
    assert "result: ok" in text
    # nothing outside PG02 was sent, outputs were switched off last, the resource was closed
    assert not any(cmd.startswith(("SYST", "*CLS")) for cmd in fake.log)
    assert fake.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed
    assert "## Teardown" in text


def test_probe_latency_sends_fifty_idn(tmp_path: Path) -> None:
    fake = FakeSdgResource()
    _run_probe(tmp_path, fake)
    # constructor + item 1 + 50 latency samples
    assert fake.log.count("*IDN?") == 52


def test_probe_failing_item_does_not_stop_the_run(tmp_path: Path) -> None:
    fake = FakeSdgResource(raise_on=["C1:BSWV?"])  # every C1:BSWV? query fails, all else works
    code, text, _ = _run_probe(tmp_path, fake)
    assert code == 0
    assert "!! RuntimeError: simulated I/O failure on 'C1:BSWV?'" in text
    for title in ITEM_TITLES:
        assert title in text
    # items after the failures still ran
    assert "-> C1:OUTP LOAD,10" in text
    assert "*IDN? x 50" in text
    assert "result: RuntimeError: simulated I/O failure" in text  # item 10 read-back failed too, recorded
    assert fake.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed


def test_probe_failing_write_is_recorded_and_next_commands_run(tmp_path: Path) -> None:
    fake = FakeSdgResource(raise_on=["C1:BSWV WVTP,PULSE"])
    code, text, _ = _run_probe(tmp_path, fake)
    assert code == 0
    assert "-> C1:BSWV WVTP,PULSE\n!! RuntimeError" in text
    assert "-> C1:BSWV WVTP,NOISE\n" in text  # the next wave type was still tried
    assert "## 10. " in text


def test_probe_risky_item_survives_undefined_commands(tmp_path: Path) -> None:
    fake = FakeSdgResource()  # raises ValueError('undefined ...') on SYST:ERR? and *CLS
    code, text, _ = _run_probe(tmp_path, fake, "--risky")
    assert code == 0
    section = text.split("## 11.", 1)[1].split("## Teardown", 1)[0]
    assert "-> SYST:ERR?\n!! ValueError: undefined query: SYST:ERR?" in section
    assert "-> *CLS\n!! ValueError: undefined command: *CLS" in section
    # risky commands are the last thing before teardown
    assert fake.log[-4:] == ["SYST:ERR?", "*CLS", "C1:OUTP OFF", "C2:OUTP OFF"]
    assert fake.closed


def test_probe_unopenable_generator_still_writes_a_report(tmp_path: Path) -> None:
    fake = FakeSdgResource(raise_on=["*IDN?"])  # the plug's constructor fails
    code, text, _ = _run_probe(tmp_path, fake)
    assert code == 1
    assert "## 0. Open the generator" in text
    assert "!! RuntimeError" in text
    assert fake.closed  # the plug does not leak the session


def test_probe_requires_resource(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        probe.main([])
    assert excinfo.value.code == 2
    assert "--resource" in capsys.readouterr().err
