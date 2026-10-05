"""OpenHTF plug for the Siglent SDG2000X arbitrary waveform generator (SDG2042X).

The only source of SCPI is the official guide PG02-E05C (``docs/PG02-E05C.txt``, "PG02"). Every command
literal below carries its section. The plug sends nothing else, ever: on this family an undefined query can
hang the generator's VXI-11 service until a power cycle. All channel commands are built by ``scpi.build_command``
(``build_outp`` / ``build_bswv``) and all channel replies are parsed by ``scpi.parse_reply`` / ``scpi.typed_fields``.

The plug stays thin: test conditions are data (``Setup``), validated before anything is sent, written in a
safe order, read back and verified; every mismatch is reported at once. PG02 documents no error queue for
this family, so the read-back is the arbiter.
"""

from collections.abc import Mapping
from typing import Any, NamedTuple, Protocol

import pyvisa
from openhtf.plugs import BasePlug
from openhtf.util import configuration

from .models import ModelLimits, limits_for
from .scpi import (
    ProtocolError,
    Reply,
    Setup,
    SetupError,
    build_command,
    build_outp,
    channel_name,
    format_value,
    order_setup,
    parse_reply,
    typed_fields,
    validate_setup,
    values_match,
)

CONF = configuration.CONF

# Declared at import; set values with ``CONF.load(...)`` only after importing this module.
CONF.declare(
    "siglent_sdg_resource",
    default_value="",
    description="VISA resource name or bare IP/hostname of the generator; empty = first USB instrument with vendor 0xF4EC",
)
CONF.declare("siglent_sdg_timeout_ms", default_value=5000, description="VISA timeout in milliseconds")
CONF.declare(
    "siglent_sdg_check_limits",
    default_value=True,
    description="reject setups outside the model limits table before sending",
)

_SIGLENT_USB_VENDOR = 0xF4EC  # USB vendor id of Siglent (AGENTS.md, SPEC §4)
_USB_QUERY = "USB?*::INSTR"  # VISA resource-name pattern (not SCPI)
_TERMINATION = "\n"  # PG02 §5.2.1 (required for the raw SOCKET resource)


class _VisaResource(Protocol):
    """What the plug needs from a resource: a PyVISA message-based resource or ``FakeSdgResource``."""

    timeout: Any
    read_termination: str | None
    write_termination: str | None

    def write(self, message: str, /) -> object: ...

    def query(self, message: str, /) -> str: ...

    def close(self) -> None: ...


class Identity(NamedTuple):
    """Parsed ``*IDN?`` reply (PG02 §3.1.1, Format 2 for SDG2000X)."""

    manufacturer: str
    model: str
    serial: str
    firmware: str
    raw: str


def _resolve_resource_name(rm: Any, name: str) -> str:
    """VISA resource name from the ``siglent_sdg_resource`` setting.

    ``::`` in the name: used as is. Other non-empty name: a bare IP/hostname, VXI-11 (PG02 §1). Empty: the
    first USB instrument with vendor id ``0xF4EC`` whose serial starts with ``SDG`` (else the first with that
    vendor id); pyvisa-py may report the vendor in decimal, hence
    ``int(field, 0)``.
    """
    name = name.strip()
    if "::" in name:
        return name
    if name:
        return f"TCPIP0::{name}::inst0::INSTR"
    matches: list[str] = []
    for candidate in rm.list_resources(_USB_QUERY):
        fields = str(candidate).split("::")
        if len(fields) < 2:
            continue
        try:
            vendor = int(fields[1], 0)
        except ValueError:
            continue
        if vendor == _SIGLENT_USB_VENDOR:
            matches.append(str(candidate))
    # Siglent scopes (serial "SDS...") share the vendor id: prefer a generator ("SDG..."), else the first match.
    # The serial is the 4th field of USB0::<vendor>::<product>::<serial>::INSTR. hypothesis until hardware session 1
    for candidate_name in matches:
        fields = candidate_name.split("::")
        if len(fields) >= 4 and fields[3].upper().startswith("SDG"):
            return candidate_name
    if matches:
        return matches[0]
    raise RuntimeError(
        "no Siglent USB instrument (vendor 0xF4EC) found and no 'siglent_sdg_resource' configured; "
        "set CONF.siglent_sdg_resource to an IP address or a VISA resource name"
    )


class SiglentSdgPlug(BasePlug):  # type: ignore[misc]  # OpenHTF is untyped
    """Siglent SDG2000X generator over SCPI (PyVISA, or any object with the same surface)."""

    auto_placeholder = True

    def __init__(self, resource: _VisaResource | None = None) -> None:
        super().__init__()
        self._resource: _VisaResource | None = None
        self._torn_down = False
        try:
            if resource is None:
                # PyVISA hands out one ResourceManager per backend to the whole process. It is never closed
                # here: closing it closes the sessions of every other plug too (measured with a scope plug).
                rm: Any = pyvisa.ResourceManager("@py")
                name = _resolve_resource_name(rm, str(CONF.siglent_sdg_resource))
                resource = rm.open_resource(name)
            self._resource = resource
            resource.timeout = CONF.siglent_sdg_timeout_ms
            resource.read_termination = _TERMINATION
            resource.write_termination = _TERMINATION
            self.identity: Identity = self.idn()
            self.limits: ModelLimits = limits_for(self.identity.model)
        except BaseException:
            # OpenHTF does not call tearDown for a plug whose constructor failed: do not leak the session.
            self._release()
            raise
        self.logger.info("opened %s %s serial %s firmware %s", *self.identity[:4])

    # ------------------------------------------------------------------------------------------------
    # Raw access
    # ------------------------------------------------------------------------------------------------

    def _resource_or_raise(self) -> _VisaResource:
        if self._resource is None:
            raise RuntimeError("plug closed")
        return self._resource

    def write(self, cmd: str) -> None:
        """Send one command. Callers are responsible for sending only commands defined in PG02."""
        resource = self._resource_or_raise()
        self.logger.debug("-> %s", cmd)
        resource.write(cmd)

    def query(self, cmd: str) -> str:
        """Send one query and return the stripped reply. Any query is a completion barrier (PG02 §3.1.2)."""
        resource = self._resource_or_raise()
        self.logger.debug("-> %s", cmd)
        reply = str(resource.query(cmd)).strip()
        self.logger.debug("<- %s", reply)
        return reply

    # ------------------------------------------------------------------------------------------------
    # Common commands
    # ------------------------------------------------------------------------------------------------

    def idn(self) -> Identity:
        """``*IDN?`` (PG02 §3.1.1, Format 2: ``Siglent Technologies,<model>,<serial>,<firmware>``)."""
        raw = self.query("*IDN?")  # PG02 §3.1.1
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) < 4:
            raise ProtocolError(f"expected 4 comma-separated fields in the *IDN? reply, got: {raw!r}")
        return Identity(parts[0], parts[1], parts[2], parts[3], raw)

    def opc(self) -> bool:
        """``*OPC?`` (PG02 §3.1.2): the bare character ``1`` once all previous commands are processed."""
        return self.query("*OPC?") == "1"  # PG02 §3.1.2

    def reset(self) -> None:
        """``*RST`` (PG02 §3.1.3, recall the default setup) then ``*OPC?`` as the completion barrier."""
        self.write("*RST")  # PG02 §3.1.3
        if not self.opc():
            raise ProtocolError("*OPC? after *RST did not answer 1")

    # ------------------------------------------------------------------------------------------------
    # Channel commands
    # ------------------------------------------------------------------------------------------------

    def _query_bswv(self, channel: int) -> Reply:
        ch = channel_name(channel)
        raw = self.query(f"{ch}:BSWV?")  # PG02 §3.4
        return parse_reply(raw, f"{ch}:BSWV")

    def _query_outp(self, channel: int) -> Reply:
        ch = channel_name(channel)
        raw = self.query(f"{ch}:OUTP?")  # PG02 §3.3
        return parse_reply(raw, f"{ch}:OUTP", leading_key="STATE")

    def get_basic_wave(self, channel: int) -> dict[str, float | str]:
        """``<ch>:BSWV?`` (PG02 §3.4) with unit suffixes stripped: ``FRQ`` 100.0, ``WVTP`` ``'SINE'``."""
        return typed_fields(self._query_bswv(channel))

    def get_output(self, channel: int) -> dict[str, float | str]:
        """``<ch>:OUTP?`` (PG02 §3.3): ``STATE`` (``ON``/``OFF``), ``LOAD`` (``HZ`` or a float), ``PLRT``."""
        return typed_fields(self._query_outp(channel))

    def _send_group(self, channel: int, group: str, params: Mapping[str, object]) -> None:
        """Validate and write one group of one channel in ``order_setup`` order."""
        ch = channel_name(channel)
        setup: Setup = {ch: {group: params}}
        validate_setup(setup, self._limits_to_check())
        for _, key, value in order_setup({group: params}):
            self.write(build_command(channel, group, key, value))

    def set_basic_wave(self, channel: int, params: Mapping[str, object]) -> None:
        """One ``<ch>:BSWV <key>,<value>`` (PG02 §3.4) per key, in ``order_setup`` order; no verification.

        The parameters are validated first, like in ``apply_setup`` (a type-specific key such as ``DUTY``
        needs ``WVTP`` in the same call).
        """
        self._send_group(channel, "BSWV", params)

    def set_output(self, channel: int, params: Mapping[str, object]) -> None:
        """One ``<ch>:OUTP ...`` (PG02 §3.3) per key, in ``order_setup`` order; no verification."""
        self._send_group(channel, "OUTP", params)

    def _limits_to_check(self) -> ModelLimits | None:
        return self.limits if CONF.siglent_sdg_check_limits else None

    def apply_setup(self, setup: Setup, *, verify: bool = True) -> None:
        """Validate, write in a safe order, read back and verify.

        Nothing is sent when validation fails (``ValueError``). With ``verify`` every touched channel is
        queried once per touched group (``BSWV?`` / ``OUTP?``; the queries are also the completion barrier,
        PG02 §3.1.2) and all mismatches are reported in one ``SetupError``.
        """
        validate_setup(setup, self._limits_to_check())
        commands = 0
        touched: dict[int, dict[str, dict[str, object]]] = {}  # channel -> group -> key -> value sent
        for ch_name, groups in setup.items():
            channel = int(ch_name[1:])  # validated to be C1 or C2
            for group, key, value in order_setup(groups):
                self.write(build_command(channel, group, key, value))
                commands += 1
                touched.setdefault(channel, {}).setdefault(group, {})[key] = value
        failures: list[str] = []
        if verify:
            for channel, groups in touched.items():
                ch_name = channel_name(channel)
                for group in ("BSWV", "OUTP"):  # PG02 §3.4, §3.3; one query per touched group
                    sent = groups.get(group)
                    if not sent:
                        continue
                    reply = self._query_outp(channel) if group == "OUTP" else self._query_bswv(channel)
                    typed = typed_fields(reply)
                    for key, value in sent.items():
                        where = f"{ch_name}:{group} {key}"
                        if key not in typed:
                            failures.append(f"{where}: not echoed by the generator")
                        elif not values_match(key, value, typed[key]):
                            failures.append(f"{where}: sent {format_value(value)}, read {reply.fields[key]}")
        self.logger.info(
            "apply_setup: channels %s, %d commands, verification %s",
            ", ".join(channel_name(c) for c in touched) or "none",
            commands,
            ("failed" if failures else "ok") if verify else "skipped",
        )
        if failures:
            raise SetupError(failures)

    # ------------------------------------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------------------------------------

    def _release(self) -> None:
        """Close the resource. The shared PyVISA ResourceManager stays open. Never raises."""
        resource, self._resource = self._resource, None
        if resource is not None:
            try:
                resource.close()
            except Exception:
                self.logger.warning("closing the VISA resource failed", exc_info=True)

    def tearDown(self) -> None:
        """Both outputs off (``C1:OUTP OFF``, ``C2:OUTP OFF``, PG02 §3.3), then close. Idempotent, never raises.

        Other settings are not restored.
        """
        if self._torn_down:
            return
        self._torn_down = True
        if self._resource is not None:
            for channel in (1, 2):
                try:
                    self.write(build_outp(channel, "STATE", False))  # PG02 §3.3
                except Exception:
                    self.logger.warning("switching channel %d output off failed", channel, exc_info=True)
        self._release()
        self.logger.info("closed")
