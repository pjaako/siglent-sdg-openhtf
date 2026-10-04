"""Tests for ``examples/station_demo.py``. No hardware, no sleeps, no network: the generator and the scope are
fakes wired to each other, and importing the module opens nothing.
"""

import copy
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import openhtf as htf
import pyvisa
import pytest

from siglent_sdg_openhtf.fake_resource import FakeSdgResource

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "examples"))
import station_demo  # noqa: E402


def _run(station: station_demo.FakeStation, steps: list[dict[str, Any]] | None = None) -> bool:
    phases = station_demo.build_phases(station_demo.STEPS if steps is None else steps, 0.0, 0.0)
    return station_demo.run_station(phases, station)


def _step(**generator: dict[str, Any]) -> dict[str, Any]:
    return {"name": "bad_step", "generator": generator, "scope": {}, "expect": {"vpp": 1.0, "frequency_hz": 1.0}}


def test_demo_runs_with_fake() -> None:
    result = subprocess.run(
        [sys.executable, "examples/station_demo.py", "--fake"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "station: PASS"


def test_usage_error_without_fake_or_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a VISA ResourceManager was opened")

    monkeypatch.setattr(pyvisa, "ResourceManager", refuse)
    with pytest.raises(SystemExit) as excinfo:
        station_demo.main([])
    assert excinfo.value.code == 2
    result = subprocess.run(
        [sys.executable, "examples/station_demo.py"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 2
    assert "--generator" in result.stderr


def test_import_opens_no_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("importing opened a VISA ResourceManager")

    monkeypatch.setattr(pyvisa, "ResourceManager", refuse)
    monkeypatch.delitem(sys.modules, "station_demo", raising=False)  # fresh import; restored after the test
    importlib.import_module("station_demo")


def test_passes_and_restores_the_scope() -> None:
    station = station_demo.make_fake_station()
    scope = station.scope
    before = {h: scope.query(h + "?") for h in (":TIM:MAIN:SCAL", ":CHAN1:SCAL")}
    assert _run(station)
    assert station.generator.log[-2:] == ["C1:OUTP OFF", "C2:OUTP OFF"]  # PG02 §3.3
    last_measure = max(i for i, cmd in enumerate(scope.log) if cmd.startswith(":MEAS:ITEM?"))
    loads = [cmd for cmd in scope.log[last_measure:] if cmd.startswith(":SYST:SET")]
    assert len(loads) >= 2  # the first block after a measurement is dropped, the plug loads it again
    after = {h: scope.query(h + "?") for h in before}
    assert after == before
    assert scope.closed


def test_generator_that_ignores_the_amplitude_does_not_pass() -> None:
    station = station_demo.make_fake_station(generator=FakeSdgResource(reject=["C1:BSWV AMP"]))  # PG02 §3.4
    assert not _run(station)


class HalfAmplitudeScope(station_demo.WiredFakeScope):
    """A cable fault: the scope sees half of the amplitude."""

    def query(self, cmd: str) -> str:
        answer: str = super().query(cmd)
        if cmd.startswith(":MEAS:ITEM? VPP") and not answer.startswith("9.9"):
            return format(float(answer) / 2, ".6E")
        return answer


def test_cable_fault_fails_vpp_of_the_first_step() -> None:
    generator = FakeSdgResource()
    station = station_demo.make_fake_station(generator, HalfAmplitudeScope(generator, signal="clock"))
    records: list[Any] = []
    phases = station_demo.build_phases(station_demo.STEPS, 0.0, 0.0)
    phases = [p.with_plugs(generator=station.generator_plug, scope=station.scope_plug) for p in phases]
    test = htf.Test(*phases)
    test.add_output_callbacks(records.append)
    assert not test.execute(test_start=lambda: "station_dut")
    failed = [
        (phase.name, name)
        for phase in records[0].phases
        for name, measurement in phase.measurements.items()
        if measurement.outcome.name == "FAIL"
    ]
    assert failed[0] == ("sine_1khz", "vpp")


@pytest.mark.parametrize(
    "generator",
    [
        {"OUTP": {"LOAD": "HZ", "STATE": True}, "BSWV": {"WVTP": "SINE", "AMP": 12.0, "OFST": 0.0}},
        {"OUTP": {"LOAD": 50, "STATE": True}, "BSWV": {"WVTP": "SINE", "AMP": 6.0, "OFST": 0.0}},  # 6 V open circuit
        {"OUTP": {"LOAD": "HZ", "STATE": True}, "BSWV": {"WVTP": "SINE", "HLEV": 1.0, "LLEV": -1.0}},  # no AMP
    ],
)
def test_input_protection_refuses_before_sending(generator: dict[str, Any]) -> None:
    station = station_demo.make_fake_station()
    steps = copy.deepcopy(station_demo.STEPS) + [_step(**generator)]
    with pytest.raises(ValueError, match="bad_step"):
        _run(station, steps)
    assert station.generator.log == []
    assert station.scope.log == []


def test_wired_scope() -> None:
    generator = FakeSdgResource()
    scope = station_demo.WiredFakeScope(generator, signal="clock")
    generator.write("C1:OUTP LOAD,HZ")  # PG02 §3.3
    generator.write("C1:OUTP ON")  # PG02 §3.3
    generator.write("C1:BSWV WVTP,SINE,FRQ,1000,AMP,2")  # PG02 §3.4
    assert float(scope.query(":MEAS:ITEM? VPP,CHAN1")) == 9.9e37
    assert float(scope.query(":MEAS:ITEM? VPP,CHAN1")) == pytest.approx(2.0)
    assert float(scope.query(":MEAS:ITEM? FREQ,CHAN1")) == 9.9e37
    assert float(scope.query(":MEAS:ITEM? FREQ,CHAN1")) == pytest.approx(1000.0)
    generator.write("C1:OUTP LOAD,50")  # PG02 §3.3
    generator.write("C1:BSWV AMP,1")  # PG02 §3.4
    assert float(scope.query(":MEAS:ITEM? VPP,CHAN1")) == pytest.approx(2.0)
    generator.write("C1:OUTP OFF")  # PG02 §3.3
    assert float(scope.query(":MEAS:ITEM? VPP,CHAN1")) == pytest.approx(0.05)
