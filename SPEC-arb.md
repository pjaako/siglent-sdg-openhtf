# SPEC-arb: arbitrary waveforms (placeholder)

Not written yet. Scope when it is: upload from a NumPy array with `Cn:WVDT` (PG02 §3.32: `WVNM`,
`FREQ`, `AMPL`, `OFST`, `PHASE`, `WAVEDATA`; `LENGTH` unnecessary on the X series; `TYPE` unsupported
on SDG2000X; 4 B to 16 MB), selection with `Cn:ARWV NAME,<name>` / `INDEX,<n>` (§3.9.1; INDEX is
built-in only on SDG2000X), sample-rate mode `Cn:SRATE MODE,DDS|TARB` and `VALUE` (§2.5 table, SRATE
section), listing with `STL?` (§3.31). Needs its own hardware session first: binary write termination,
busy time after large uploads, and the reported `0x0A`-in-data problem are unmeasured.
Prerequisite: `SPEC.md` done and verified on hardware.
