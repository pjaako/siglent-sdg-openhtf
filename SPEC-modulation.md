# SPEC-modulation: modulation, sweep, burst (placeholder)

Not written yet. Scope when it is: `Cn:MDWV` (PG02 §3.5), `Cn:SWWV` (§3.6), `Cn:BTWV` (§3.7), each as
additional groups in the `apply_setup` data model with PG02 key sets and the SDG2000X availability
column. Third-party reports to verify on hardware: sweep START/STOP must be written in an order that
never inverts them; burst delay cannot be set in GATE mode. Prerequisite: `SPEC.md` done and verified
on hardware.
