# SPEC-station: generator + oscilloscope demo (placeholder)

Not written yet. Goal: `examples/station_demo.py`, an OpenHTF test that drives this plug and
`rigol-dho-openhtf` (https://github.com/pjaako/rigol-dho-openhtf) together: the SDG2042X feeds a
signal into the DHO814, the scope measures Vpp and frequency, and both are recorded with limits.
Must run with both fakes (`--fake`). Open point to settle before writing it: `rigol-dho-openhtf` is a
set of flat modules without `pyproject.toml`; either that repository gains a minimal `pyproject.toml`
(preferred, then it becomes an optional dependency here) or the demo takes its path from an
environment variable. Prerequisite: `SPEC.md` done and verified on hardware.
