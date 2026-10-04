# SPEC: OpenHTF plug for the Siglent SDG2042X (v1, core)

Target instrument: Siglent SDG2042X (SDG2000X family, stock, 2 channels), controlled with SCPI over LAN
through **PyVISA** (`pyvisa` + `pyvisa-py` backend). USB (USBTMC, vendor id `0xF4EC`) is optional.
Python 3.13. Dependencies: `openhtf`, `pyvisa`, `pyvisa-py`; dev: `pytest`, `mypy`; optional extra
`usb`: `pyusb`. Nothing else.

**The real generator is NOT available to you.** Never open a real VISA resource in tests or scripts you
run. Everything you run uses `FakeSdgResource`. Scripts meant for hardware (`tools/probe.py`,
`example_test.py` without `--fake`) are written, imported in tests, and never executed by you.

**The only source of SCPI is `docs/PG02-E05C.txt`** (text of the official guide
`docs/SDG_Programming Guide_PG02-E05C.pdf`). Every SCPI string in code, tests and the fake carries a
comment naming its PG02 section. Use the SDG2000X column of the availability tables. If something you
need is not in PG02, do not invent it; write it down in your report instead.

Read first: `AGENTS.md`, `STATUS.md`, this file, and PG02 §1.2.4 (socket), §2.5 (command table),
§3.1.1–3.1.3 (`*IDN?`, `*OPC?`, `*RST`), §3.3 (OUTP), §3.4 (BSWV). Do not commit; the owner commits.

## Facts from PG02 this spec builds on

| Fact | PG02 |
|---|---|
| `*IDN?` on SDG2000X answers Format 2: `Siglent Technologies,<model>,<serial>,<firmware>` (no header) | §3.1.1 |
| `*OPC?` on SDG2000X answers the bare character `1`; the device parses the next command only after the previous one is completely processed, so **any query is a completion barrier; never sleep** | §3.1.2 |
| `*RST` recalls the default setup | §3.1.3 |
| `CHDR` is not available on SDG2000X; replies always start with the command header (`C1:BSWV ...`) | §3.2 |
| `<ch>:OUTP ON\|OFF`, `<ch>:OUTP LOAD,<50..100000\|HZ>`, `<ch>:OUTP PLRT,<NOR\|INVT>`; query `<ch>:OUTP?` answers `C1:OUTP ON,LOAD,HZ,PLRT,NOR` (first field is the bare state) | §3.3 |
| `<ch>:BSWV <param>,<value>` (one parameter per command is the documented form); query `<ch>:BSWV?` answers `C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0`; the key set depends on the current `WVTP`; values may carry unit suffixes; exponent notation is accepted on write (`BANDWIDTH,100E6`) | §3.4 |
| No error-queue command (`SYST:ERR?`, `*CLS`, `*ESR?`) appears anywhere in PG02 → v1 has no error queue; validation before sending and read-back after sending are the only checks | grep |
| Commands are terminated with `\n`; raw socket port 5025; VXI-11 over LAN | §5.2.1, §1.2.4 |

Nothing has been measured on the real generator yet. Where this spec goes beyond PG02 examples (fake
defaults, key sets for non-SINE types, number formats, tolerances, model limits) it says so, and the
code must say so too (`# hypothesis until hardware session 1`).

## Files

```
pyproject.toml                      hatchling; package siglent_sdg_openhtf; pytest + mypy config
uv.lock                             committed
.github/workflows/ci.yml            uv sync, pytest, mypy on push and pull request
src/siglent_sdg_openhtf/__init__.py exports SiglentSdgPlug, Identity, SetupError, ProtocolError, __version__
src/siglent_sdg_openhtf/py.typed
src/siglent_sdg_openhtf/scpi.py     pure functions: build, parse, units, tolerances, validation, ordering
src/siglent_sdg_openhtf/models.py   ModelLimits table and limits_for()
src/siglent_sdg_openhtf/plug.py     SiglentSdgPlug
src/siglent_sdg_openhtf/fake_resource.py  FakeSdgResource (no pyvisa, no openhtf, no scpi.py import)
tests/test_import.py  tests/test_scpi.py  tests/test_fake_resource.py  tests/test_plug.py  tests/test_examples.py
example_test.py                     runnable OpenHTF test, --fake
tools/probe.py                      hardware probe (written here, run only by the owner)
```

## 1. Packaging

- `pyproject.toml`: build backend hatchling; `name = "siglent-sdg-openhtf"`, `requires-python = ">=3.13"`,
  dependencies `openhtf>=1.6.1,<2`, `pyvisa>=1.14`, `pyvisa-py>=0.7`; `[project.optional-dependencies] usb = ["pyusb"]`;
  `[dependency-groups] dev = ["pytest", "mypy"]`; license MIT; `[tool.pytest.ini_options] testpaths = ["tests"]`;
  `[tool.mypy] strict = true, files = ["src", "tests"]` (T5 adds `"example_test.py"` and `"tools"`; mypy fails on a
  missing path), overrides `module = ["openhtf", "openhtf.*", "pyvisa", "pyvisa.*"] ignore_missing_imports = true` and
  `module = ["tests.*", "example_test"] disallow_untyped_decorators = false` (OpenHTF decorators are untyped; `tests/`
  is a package with `__init__.py` so the `tests.*` pattern applies).
- `uv.lock` is committed. `uv sync --all-extras --dev` must work offline-tolerant (no custom indexes).
- CI (`.github/workflows/ci.yml`): `astral-sh/setup-uv`, Python 3.13, `uv sync --all-extras --dev`,
  `uv run pytest -q`, `uv run mypy`.
- `tests/test_import.py`: `import siglent_sdg_openhtf` works and exposes `__version__`.

## 2. `scpi.py` (pure, no I/O, fully typed)

```python
Setup = Mapping[str, Mapping[str, Mapping[str, object]]]   # 'C1' -> 'BSWV'|'OUTP' -> key -> value
CHANNELS = ('C1', 'C2')                                     # PG02 §3.3/§3.4 <channel>:={C1,C2}
WAVE_TYPES = frozenset({'SINE', 'SQUARE', 'RAMP', 'PULSE', 'NOISE', 'ARB', 'DC'})   # §3.4; PRBS/IQ excluded: their parameters are "no" for SDG2000X
OUTP_KEYS = frozenset({'STATE', 'LOAD', 'PLRT'})            # §3.3; STATE is this project's name for the bare ON|OFF field
BSWV_KEYS = frozenset({'WVTP','FRQ','PERI','AMP','AMPVRMS','AMPDBM','OFST','SYM','DUTY','PHSE','STDEV','MEAN',
                       'WIDTH','RISE','FALL','DLY','HLEV','LLEV','BANDSTATE','BANDWIDTH','MAX_OUTPUT_AMP'})  # §3.4 SDG2000X column + §3.3
```

Validity of BSWV keys per `WVTP` (from the "Description" column of PG02 §3.4; encode as a table
`BSWV_KEY_WAVE_TYPES: Mapping[str, frozenset[str]]`):
`FRQ PERI AMP AMPVRMS AMPDBM HLEV LLEV`: not NOISE, not DC. `OFST`: not NOISE. `PHSE`: not NOISE, PULSE, DC.
`SYM`: RAMP only. `DUTY`: SQUARE, PULSE only. `WIDTH RISE FALL`: PULSE only. `STDEV MEAN BANDSTATE BANDWIDTH`: NOISE only.
`DLY`, `MAX_OUTPUT_AMP`, `WVTP`: any type (PG02 states no restriction).

| name | behaviour |
|---|---|
| `class Reply(NamedTuple)` | `header: str`, `fields: dict[str, str]` (insertion order as received), `raw: str` |
| `class ProtocolError(RuntimeError)` | reply header mismatch or unparsable reply; message includes the raw reply |
| `class SetupError(RuntimeError)` | attribute `failures: list[str]`; message lists them all, one per line |
| `channel_name(channel: int) -> str` | 1→`C1`, 2→`C2`, else `ValueError` |
| `format_value(value: object) -> str` | `bool`→`ON`/`OFF`; `int`→`str(int)`; `float`→`format(v, '.9G')` (PG02 accepts exponent form, §3.4 example `100E6`; whether `1E-06` is accepted is a hardware item); `str`→unchanged; other types `TypeError` |
| `build_bswv(channel: int, key: str, value: object) -> str` | `f'{ch}:BSWV {key},{format_value(value)}'` (§3.4) |
| `build_outp(channel: int, key: str, value: object) -> str` | `STATE` → `f'{ch}:OUTP {ON\|OFF}'`; `LOAD`/`PLRT` → `f'{ch}:OUTP {key},{format_value(value)}'` (§3.3 examples) |
| `parse_reply(raw: str, expect_header: str, leading_key: str \| None = None) -> Reply` | split at the first space; header must equal `expect_header` (e.g. `C1:BSWV`) else `ProtocolError`; body split on `,`; if `leading_key` is given the first token is stored under it (used for OUTP's bare `ON\|OFF`, §3.3); remaining tokens must pair up, else `ProtocolError` |
| `strip_unit(text: str) -> float \| str` | `^([-+]?(\d+\.?\d*\|\.\d+)([eE][-+]?\d+)?)\s*([A-Za-z%/]*)$` → `float` of the number; otherwise the string unchanged. Must handle `100HZ 0.01S 2V -1V 0 1.41421Vrms 3dBm 50% 100E6 2.4e-07S` → floats and `HZ SINE ON NOR` → str |
| `typed_fields(reply: Reply) -> dict[str, float \| str]` | `strip_unit` over every field |
| `values_match(key: str, sent: object, got: float \| str) -> bool` | `bool` sent: compare `ON`/`OFF` case-insensitively; `str` sent vs `str` got: case-insensitive equality; number sent vs number got: `math.isclose(sent, got, rel_tol=REL_TOL.get(key, 1e-6), abs_tol=ABS_TOL.get(key, 0.0))` with module-level tables `ABS_TOL = {AMP, AMPVRMS, OFST, HLEV, LLEV, STDEV, MEAN, MAX_OUTPUT_AMP: 1e-3; PHSE, DUTY, SYM: 1e-2}` marked provisional; number vs str or str vs number → `False` |
| `validate_setup(setup: Setup, limits: ModelLimits \| None) -> None` | raises `ValueError` naming the offending channel/group/key **before anything is sent**: channel not in `CHANNELS`; group not in `{OUTP, BSWV}`; key not in the group's key set; `WVTP` not in `WAVE_TYPES`; a BSWV key invalid for the `WVTP` of the same channel setup (if no `WVTP` is given, type-specific keys are rejected with a message saying `WVTP` is required alongside them); more than one of `AMP AMPVRMS AMPDBM`, or any of them together with `HLEV`/`LLEV`; both `FRQ` and `PERI`; `LOAD` not (number in 50..100000 or the string `HZ`, case-insensitive); `PLRT` not `NOR`/`INVT`; `STATE` not bool or `ON`/`OFF`; if `limits` is given: `FRQ` above `limits.max_freq_hz[WVTP]` when that entry exists, `AMP` above `limits.max_amp_vpp_hiz`, or above `limits.max_amp_vpp_50` when the same channel sets `LOAD` to 50 |
| `order_setup(channel_setup: Mapping[str, Mapping[str, object]]) -> list[tuple[str, str, object]]` | returns `(group, key, value)` triples in this order: 1 `OUTP STATE` if it is off; 2 `OUTP LOAD`; 3 `OUTP PLRT`; 4 `BSWV WVTP`; 5 `BSWV FRQ` or `PERI`; 6 `BSWV AMP`/`AMPVRMS`/`AMPDBM` then `OFST`, or `HLEV` then `LLEV`; 7 all other BSWV keys in the caller's order; 8 `OUTP STATE` if it is on. Reason for 2 before 6: reports say switching LOAD between HZ and 50 rescales the displayed amplitude (hypothesis, see README); the safe order costs nothing |

## 3. `models.py`

```python
class ModelLimits(NamedTuple):
    model: str
    channels: int
    max_freq_hz: Mapping[str, float]   # per WVTP; missing entry = no check
    max_amp_vpp_hiz: float
    max_amp_vpp_50: float
    max_offset_v_hiz: float
```
`MODELS: Mapping[str, ModelLimits]` with `SDG2042X` (SINE 40e6, SQUARE 25e6, PULSE 25e6, RAMP 1e6; 20 Vpp HiZ,
10 Vpp at 50 Ω, ±10 V), `SDG2082X` (SINE 80e6, same otherwise), `SDG2122X` (SINE 120e6). These numbers are from
the SDG2000X datasheet summary, not PG02; mark them so. `limits_for(model: str) -> ModelLimits`: exact match
(case-insensitive, stripped); a model starting with `SDG2` that is not in the table gets the SDG2042X limits
(the most restrictive); anything else raises `ValueError`.

## 4. `plug.py`

```python
from openhtf.plugs import BasePlug
from openhtf.util import configuration
CONF = configuration.CONF
CONF.declare('siglent_sdg_resource', default_value='', description='VISA resource name or bare IP/hostname of the generator; empty = first USB instrument with vendor 0xF4EC')
CONF.declare('siglent_sdg_timeout_ms', default_value=5000, description='VISA timeout in milliseconds')
CONF.declare('siglent_sdg_check_limits', default_value=True, description='reject setups outside the model limits table before sending')
```

`class Identity(NamedTuple)`: `manufacturer`, `model`, `serial`, `firmware`, `raw` (all `str`). Parsed from
`*IDN?` by splitting on `,` and stripping (PG02 §3.1.1 Format 2); fewer than 4 fields → `ProtocolError`.

`class SiglentSdgPlug(BasePlug)`, `auto_placeholder = True`.

`__init__(self, resource: object | None = None)`
- `resource` is any object with `write(str)`, `query(str) -> str`, `close()` and the attributes `timeout`,
  `read_termination`, `write_termination`. Tests pass a `FakeSdgResource`.
- If `resource is None`: `pyvisa.ResourceManager('@py')`; name = `CONF.siglent_sdg_resource`:
  contains `::` → used as is; non-empty without `::` → `f'TCPIP0::{name}::inst0::INSTR'` (PG02 §1, VXI-11);
  empty → first entry of `rm.list_resources('USB?*::INSTR')` whose second `::` field, parsed with
  `int(field, 0)`, equals `0xF4EC` (pyvisa-py may report the vendor in decimal); none → `RuntimeError` with
  a clear message. Keep the `ResourceManager` to close it in `tearDown`.
- Set `timeout = CONF.siglent_sdg_timeout_ms`, `read_termination = '\n'`, `write_termination = '\n'`
  (PG02 §5.2.1; required for the SOCKET resource).
- Call `self.idn()` once; store `self.identity` and `self.limits = limits_for(identity.model)`.
  Do **not** send `*RST`.
- Logging through `self.logger` (BasePlug): DEBUG for every command and reply, INFO on open (identity)
  and close, WARNING for failures in `tearDown`.

Methods (SCPI exactly as written):

| method | SCPI (PG02) | returns |
|---|---|---|
| `write(cmd: str)` | `cmd` | `None` |
| `query(cmd: str)` | `cmd` | stripped `str` |
| `idn()` | `*IDN?` (§3.1.1) | `Identity` |
| `opc()` | `*OPC?` (§3.1.2) | `bool`, `reply.strip() == '1'` |
| `reset()` | `*RST` then `*OPC?` (§3.1.3, §3.1.2) | `None`; `ProtocolError` if `*OPC?` is not `1` |
| `get_basic_wave(channel: int)` | `<ch>:BSWV?` (§3.4) | `dict[str, float \| str]` via `parse_reply(raw, f'{ch}:BSWV')` and `typed_fields` |
| `set_basic_wave(channel: int, params: Mapping[str, object])` | one `<ch>:BSWV <key>,<value>` per key (§3.4), in `order_setup` order | `None`; validated first like `apply_setup`, no read-back |
| `get_output(channel: int)` | `<ch>:OUTP?` (§3.3) | `dict` with `STATE` (`ON`/`OFF`), `LOAD` (`HZ` or float), `PLRT`, via `parse_reply(raw, f'{ch}:OUTP', leading_key='STATE')` |
| `set_output(channel: int, params: Mapping[str, object])` | `<ch>:OUTP ...` per key (§3.3), in `order_setup` order | `None`; validated first, no read-back |
| `apply_setup(setup: Setup, *, verify: bool = True)` | see below | `None` |
| `tearDown()` | `C1:OUTP OFF`, `C2:OUTP OFF` (§3.3) | `None` |

`apply_setup`:
1. `validate_setup(setup, self.limits if CONF.siglent_sdg_check_limits else None)`; on `ValueError` nothing is sent.
2. For each channel in the setup, write the `order_setup` triples with `build_outp` / `build_bswv`.
3. If `verify`: for each touched channel query `<ch>:BSWV?` if BSWV keys were sent and `<ch>:OUTP?` if OUTP
   keys were sent (these queries are also the completion barrier, §3.1.2). For each sent key: missing in the
   reply → failure `C1:BSWV FRQ: not echoed by the generator`; `values_match` false → failure
   `C1:BSWV FRQ: sent 1000, read 100HZ`. Collect all; if any, raise `SetupError`.
4. Log at INFO: channels touched, number of commands, verification result.

`tearDown()`: for each of `C1:OUTP OFF`, `C2:OUTP OFF`: `try: write; except Exception: logger.warning`.
Then close the resource (also under try/except with a warning), then the ResourceManager if the plug
opened one. Idempotent: a second call does nothing. Never raises.

## 5. `fake_resource.py`

`class FakeSdgResource` emulating the subset above. Imports nothing from `pyvisa`, `openhtf` or
`siglent_sdg_openhtf.scpi` (it is an independent oracle; a shared parser would hide parser bugs).

- Constructor: `FakeSdgResource(*, model='SDG2042X', serial='SDG2XFAKE000001', firmware='0.00.00.00',
  reject: Collection[str] = (), raise_on: Collection[str] = ())`.
- Attributes: `timeout`, `read_termination`, `write_termination`, `chunk_size`; `log: list[str]` with every
  string passed to `write` and `query`, in order; `closed: bool`.
- State per channel (`C1`, `C2`), defaults = the PG02 §3.4 example reply, marked hypothesis: output off,
  load `HZ`, polarity `NOR`, `WVTP SINE`, `FRQ 100`, `AMP 2`, `OFST 0`, `PHSE 0`; type-specific defaults
  `DUTY 50`, `SYM 50`, `WIDTH 0.000001`, `RISE 1e-8`, `FALL 1e-8`, `DLY 0`, `STDEV 0.5`, `MEAN 0`,
  `BANDSTATE OFF`, `BANDWIDTH 1e6`, `MAX_OUTPUT_AMP 20`. `PERI = 1/FRQ`, `HLEV = OFST + AMP/2`,
  `LLEV = OFST - AMP/2` are derived on read.
- `write(cmd)`: logs, then applies `raise_on` (any prefix match → `RuntimeError`) and `reject` (any prefix
  match → return without applying). Then:
  `*RST` → all channels to defaults (§3.1.3). `C1:OUTP ...` / `C2:OUTP ...` (§3.3): optional leading `ON|OFF`,
  then `LOAD,x` / `PLRT,x` pairs; `LOAD` outside 50..100000 or not `HZ` is silently ignored; switching
  between `HZ` and a numeric load halves (`HZ`→number) or doubles (number→`HZ`) `AMP` and `OFST`
  (hypothesis from third-party reports; keep it in one clearly marked method so it can be deleted). `Cn:BSWV K,V`
  (§3.4; also accept several `K,V` pairs in one command, marked hypothesis): a key that is not in the SDG2000X
  set, not valid for the current `WVTP`, or whose value is outside the model limits (reuse the numbers of
  `models.py` by copying them, not by importing) is **silently ignored**, because the real device has no error
  queue; `WVTP` change keeps `FRQ/AMP/OFST/PHSE` and resets type-specific keys to defaults; `PERI` sets
  `FRQ = 1/PERI`; `HLEV`/`LLEV` set `AMP`/`OFST`; `AMPVRMS`/`AMPDBM` are stored and echoed only as `AMP`-equivalents
  for SINE (`AMP = AMPVRMS * 2*sqrt(2)`; dBm into 50 Ω: `AMP = 2*sqrt(2) * sqrt(50 * 10**(dBm/10) / 1000)`).
  `*OPC` → no-op. Anything else → `ValueError('undefined command: ...')`.
- `query(cmd)`: logs, applies `raise_on`, then: `*IDN?` → `Siglent Technologies,{model},{serial},{firmware}`
  (§3.1.1 Format 2); `*OPC?` → `1` (§3.1.2); `Cn:OUTP?` → `Cn:OUTP ON|OFF,LOAD,<HZ|number>,PLRT,<NOR|INVT>`
  (§3.3); `Cn:BSWV?` → per `WVTP`: SINE/ARB `WVTP,FRQ<HZ>,PERI<S>,AMP<V>,OFST<V>,HLEV<V>,LLEV<V>,PHSE` (SINE string
  for defaults must equal `C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,OFST,0V,HLEV,1V,LLEV,-1V,PHSE,0`
  exactly, §3.4); SQUARE adds `DUTY` after `PHSE`; RAMP adds `SYM`; PULSE is `WVTP,FRQ,PERI,AMP,OFST,HLEV,LLEV,
  DUTY,WIDTH<S>,RISE<S>,FALL<S>,DLY<S>` (no `PHSE`; `DUTY` is settable for PULSE per §3.4); NOISE is `WVTP,STDEV<V>,MEAN<V>,BANDSTATE` plus `BANDWIDTH<HZ>`
  when `BANDSTATE` is `ON`; DC is `WVTP,OFST<V>`. Every non-SINE key set is a hypothesis derived from the
  §3.4 validity rules; say so in a comment. Anything else → `ValueError('undefined query: ...')`.
- Number formatting `_num(x, unit)`: integers without decimal point (`100HZ`, `2V`, `0V`), otherwise the
  shortest `repr`-style decimal without trailing zeros (`0.01S`, `0.000001S`); no exponent form (hypothesis).
- `close()` sets `closed = True`.

## 6. Tests (pytest, no hardware, no sleeps, no network)

`tests/test_scpi.py` — `channel_name`; `format_value` for bool/int/float/str and `TypeError`; `build_bswv`
reproduces PG02 §3.4 examples `C1:BSWV WVTP,RAMP`, `C1:BSWV FRQ,2000`, `C1:BSWV AMP,3`; `build_outp` reproduces
§3.3 examples `C1:OUTP ON`, `C1:OUTP LOAD,50`, `C1:OUTP LOAD,HZ`, `C1:OUTP PLRT,NOR`; `parse_reply` of the §3.4
example string gives 8 fields in order; `parse_reply` with `leading_key='STATE'` on `C1:OUTP ON,LOAD,HZ,PLRT,NOR`;
header mismatch raises `ProtocolError` carrying the raw text; odd token count raises; `strip_unit` parametrised
over the table in §2; `values_match` tolerances (`AMP` 2 vs 2.0005 True, 2 vs 2.01 False; `FRQ` 1000 vs 1000.0
True; bool vs `ON`; str vs number False); `order_setup` puts `LOAD` before `AMP`, `WVTP` before `DUTY`, `STATE`
off first and on last; `validate_setup` rejects unknown channel, unknown group, unknown key, key not on
SDG2000X (`LENGTH`, `LOGICLEVEL`), `DUTY` with `SINE`, `AMP` with `HLEV`, `FRQ` with `PERI`, `LOAD` 10, `PLRT`
`X`, `WVTP` `PRBS`, `FRQ` 50e6 for SINE with SDG2042X limits, and accepts the example setup; `validate_setup`
sends nothing (it is pure); `limits_for` known / unknown-SDG2 / other.

`tests/test_fake_resource.py` — `*IDN?` string; default `C1:BSWV?` equals the §3.4 example exactly; default
`C1:OUTP?`; `OUTP ON` and `LOAD,50` are reflected; `LOAD` 10 ignored; HZ→50 halves `AMP`; `PERI` sets `FRQ`;
`HLEV`/`LLEV` set `AMP`/`OFST`; `WVTP,SQUARE` adds `DUTY`, `WVTP,PULSE` drops `PHSE`, `WVTP,NOISE` has only its
keys, `WVTP,DC` only `OFST`; `FRQ` 50e6 ignored on SDG2042X; `DUTY` ignored while SINE; `reject` logs but does not
apply; `raise_on` raises; undefined write and query raise `ValueError`; `*RST` restores defaults; `*OPC?` is `1`;
`log` order; `close()`; a subprocess test that loads `fake_resource.py` **by file path** (`importlib.util.spec_from_file_location`,
so the package `__init__`, which imports the plug, is not executed) and asserts `'pyvisa' not in sys.modules and
'openhtf' not in sys.modules`; plus an `ast` test that the fake's source imports nothing from `pyvisa`, `openhtf` or
`siglent_sdg_openhtf`.

`tests/test_plug.py` (helper `_plug(**fake_kwargs) -> tuple[SiglentSdgPlug, FakeSdgResource]`) — `identity`
and `limits` after construction; `get_basic_wave` typed values (`FRQ` 100.0, `WVTP` `SINE`); `get_output`
(`STATE` `OFF`, `LOAD` `HZ`); `set_output` command order; `set_basic_wave` one command per key in order; `apply_setup`
happy path on both channels and the exact command log; `LOAD` before `AMP` and `STATE ON` last; `STATE OFF` first;
two rejected keys → one `SetupError` naming both with sent and read values; invalid setup → `ValueError` and an
empty log; `check_limits` off (`CONF.load(siglent_sdg_check_limits=False)` inside a `CONF.save_and_restore`) lets a
50 MHz request through and the fake's silent ignore turns into a `SetupError`; `verify=False` sends no queries;
`reset()` log is `*RST`, `*OPC?`; a stub resource answering `C2:BSWV ...` to a `C1:BSWV?` → `ProtocolError`;
`tearDown` log ends with `C1:OUTP OFF`, `C2:OUTP OFF` and the resource is closed; `raise_on=['C1:OUTP OFF']` still
closes and sends `C2:OUTP OFF`; `tearDown` twice is harmless; discovery with a stub `ResourceManager`
(monkeypatched `pyvisa.ResourceManager`) for hex and decimal vendor ids, bare IP expansion, `::SOCKET` passthrough,
nothing found → `RuntimeError`; resource attributes are set; `auto_placeholder` is `True`; OpenHTF integration:
a subclass injecting the fake, a phase with `@htf.plug(generator=...)` and measurements `frequency_hz`
(`in_range(999, 1001)`), `amplitude_vpp` (`in_range(1.99, 2.01)`), `output_state` (`equals('ON')`);
`htf.Test(phase).execute(test_start=lambda: 'dut1')` returns `True`; and the shared fake's log ends with both
`OUTP OFF` commands (OpenHTF called `tearDown`).

`tests/test_examples.py` — `subprocess.run([sys.executable, 'example_test.py', '--fake'])` from the repo root
exits 0 and prints `example: PASS`; `import example_test` and `import tools.probe` (or `runpy` with a guard) do not
open any resource.

## 7. `example_test.py`

Runnable OpenHTF test, same shape as the Rigol one (`--fake` swaps in a subclass whose `__init__` injects
`FakeSdgResource`). One phase `generate_sine(test, generator)`: `SETUP = {'C1': {'OUTP': {'LOAD': 50, 'STATE': True},
'BSWV': {'WVTP': 'SINE', 'FRQ': 1000.0, 'AMP': 2.0, 'OFST': 0.0}}}`; `generator.apply_setup(SETUP)`; read
`get_basic_wave(1)` and `get_output(1)`; measurements `frequency_hz` (HERTZ, in range 999..1001), `amplitude_vpp`
(VOLT, 1.99..2.01), `output_state` (equals `ON`); print `example: PASS` or `example: FAIL`; exit code 0/1. Accept
`--resource NAME` and load it into `CONF.siglent_sdg_resource` after importing the plug. No station server.

## 8. `tools/probe.py` (write only; you do NOT run it)

CLI: `python tools/probe.py --resource NAME [--out dumps] [--risky]`. Opens `SiglentSdgPlug` directly, wraps
everything in `try/finally: plug.tearDown()`, writes `dumps/probe-<YYYYMMDD-HHMMSS>.md` with one section per item,
each showing the exact commands sent and the exact replies, and prints the same to stdout. Items, in this order:
1. `*IDN?`. 2. `*RST`, `*OPC?` with elapsed time; then `C1:BSWV?`, `C1:OUTP?`, `C2:BSWV?`, `C2:OUTP?`.
3. For each `WVTP` in SINE SQUARE RAMP PULSE NOISE DC ARB: `C1:BSWV WVTP,<t>` then `C1:BSWV?` verbatim.
4. `C1:OUTP LOAD,50` → `C1:OUTP?`; `C1:OUTP LOAD,HZ` → `C1:OUTP?`. 5. With SINE and `AMP,2`: `LOAD,50` → `BSWV?`;
`LOAD,HZ` → `BSWV?` (amplitude rescale). 6. `FRQ,1234.5678901`, `AMP,2.0005`, `OFST,0.0005`, `PHSE,12.345` each
followed by `BSWV?`. 7. `FRQ,1E3`, `FRQ,1e3`, `FRQ,1E-03`, `FRQ,1000`. 8. `FRQ,50E6` (SINE), `C1:OUTP LOAD,10`,
each followed by the query. 9. `C1:BSWV WVTP,SINE,FRQ,1000,AMP,2` then `BSWV?`. 10. Latency: 50 × `*IDN?`,
median and p95 in ms; one `apply_setup` of the example setup, wall time. 11. Only with `--risky`, last:
`SYST:ERR?` then `*CLS` (not in PG02; may hang the service; the owner runs this over the raw socket with a power
cycle at hand). Every item catches exceptions, records them, and continues; a timeout on one item must not stop
the probe. The output file name and the resource name are printed; the file goes to `dumps/` (git-ignored).

## Documentation limits

Do not write README sections about hardware behaviour; that is the owner's job after the first hardware
session. You may add a "Usage" section to `README.md` with the example phase and the setup dict (under
40 lines) and update `STATUS.md`'s "Done" list for your task. Use `192.0.2.10` as the example address.

## Done means

```
uv sync --all-extras --dev
uv run pytest -q                      # all green
uv run mypy                           # clean
uv run python example_test.py --fake  # exit 0, prints "example: PASS"
git status --short                    # only the files this task lists
```

## Required report

Files changed; the final `pytest -q` line verbatim; the `mypy` summary line; every place where this spec was
wrong, ambiguous, or where you deviated and why; every SCPI string you used with its PG02 section; what is
untested; anything that looks like a bug in existing code that you did not touch.
