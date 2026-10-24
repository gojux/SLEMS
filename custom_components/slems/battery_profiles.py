"""Modbus profiles of batteries: format, loader and checks (no HA dependency).

A profile describes the registers of a battery and the steps to charge,
discharge and release it, so a battery can be added without a driver of its
own. The format is deliberately not a programming language: no conditions,
no loops, no free write commands; every key is known and anything else is
refused. What a device needs beyond it belongs into a Python subclass of the
profile driver. The transport (own Modbus client or a unit of Home
Assistant's modbus integration) is not part of the profile.

Example (shortened, see ``templates/battery_profiles``)::

    slems_battery_profile: 1
    id: example
    name: Example battery
    connection: {port: 502, unit_id: 1, message_spacing_s: 0.15, timeout_s: 3}
    limits: {max_charge_w: 2500, max_discharge_w: 2500}
    read:
      soc_pct:    {address: 34002, type: uint16, scale: 0.1}
      power_ac_w: {address: 30006, type: int16, positive: discharge}
    control:
      type: split
      apply:
        - {address: 42021, value: "{discharge_w}", at_rest: [0, 2500]}
        - {address: 42020, value: "{charge_w}", at_rest: [0, 2500]}
        - {address: 42010, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
      release:
        auto:
          - {address: 42010, value: 0, confirm: true}
    probe: {address: 34002, type: uint16, range: [0, 1000]}

Values in ``apply`` and ``release`` are raw register values; only the power
placeholders are in W and become raw values through ``scale`` (one raw unit
is ``scale`` W, as when reading). ``at_rest`` (or ``at_rest_values``) is the
raw range of a write register while the battery rests: a register outside it
is not written (a shifted address usually holds something else entirely).

Batteries with own PV modules (``own_pv``) read ``pv_w`` and the output into
the house (``output_w`` and ``input_w``, or one signed ``connection_w``). The
set point refers to the battery power (``setpoint: battery``, e.g. a hybrid
inverter) or to the power at the AC connection (``setpoint: connection``:
charge = input from the grid, discharge = output into the house).

Registers marked ``persistent`` are stored in the device's flash: they are
written only when their value changes, never by a keep-alive, and at most
``max_changes_per_day`` times in 24 h (see ``PersistentGuard``).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
import dataclasses
from dataclasses import dataclass, field
import functools
import hashlib
from pathlib import Path
import re
import struct
from typing import Any

import yaml

from .const import CONF_PROFILE_ID, CONF_PROFILE_YAML, ReleaseState

FORMAT_KEY = "slems_battery_profile"
FORMAT_VERSION = 1
BUNDLED_DIR = Path(__file__).parent / "templates" / "battery_profiles"

# Registers each data type occupies (char: given by ``count``).
REGISTER_TYPES: dict[str, int | None] = {
    "uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2, "char": None,
}
WRITE_TYPES = ("uint16", "int16", "uint32", "int32", "float32")
_TYPE_RANGE: dict[str, tuple[float, float]] = {
    "uint16": (0, 0xFFFF),
    "int16": (-0x8000, 0x7FFF),
    "uint32": (0, 0xFFFFFFFF),
    "int32": (-0x80000000, 0x7FFFFFFF),
    "float32": (-3.4e38, 3.4e38),
}
_STRUCT = {"uint16": ">H", "int16": ">h", "uint32": ">I", "int32": ">i", "float32": ">f"}

# Read keys -> key in BatteryTelemetry.extra (None: a field of BatteryTelemetry).
READ_KEYS: dict[str, str | None] = {
    "soc_pct": None,
    "power_dc_w": None,
    "power_ac_w": "ac_power",
    "pv_w": None,
    "output_w": None,
    "input_w": None,
    "connection_w": None,
    "charged_kwh": "total_charging_energy",
    "discharged_kwh": "total_discharging_energy",
    "voltage_v": "battery_voltage",
    "temperature_c": "internal_temperature",
    "max_cell_v": "max_cell_voltage",
    "min_cell_v": "min_cell_voltage",
    "cycles": "cycle_count",
}
STATE_EXTRA_KEY = "inverter_state"
# Read keys with a sign and its meaning (``positive``).
SIGNED_READ_KEYS: dict[str, tuple[str, str]] = {
    "power_dc_w": ("charge", "discharge"),
    "power_ac_w": ("charge", "discharge"),
    "connection_w": ("input", "output"),
}
# Power read keys that may be the sum of several registers (e.g. MPPT inputs).
SUM_READ_KEYS = frozenset({"power_dc_w", "power_ac_w", "pv_w", "output_w", "input_w", "connection_w"})
# Battery states of the profile -> value of the extra ``inverter_state``.
STATES: dict[str, str] = {
    "sleep": "sleep",
    "standby": "standby",
    "charge": "charge",
    "discharge": "discharge",
    "backup": "backup",
    "firmware_update": "ota_upgrade",
    "bypass": "bypass",
}
MODES = ("idle", "charge", "discharge")
PLACEHOLDER_CHARGE = "{charge_w}"
PLACEHOLDER_DISCHARGE = "{discharge_w}"
PLACEHOLDER_POWER = "{power_w}"
PLACEHOLDER_MODE = "{mode}"
POWER_PLACEHOLDERS = (PLACEHOLDER_CHARGE, PLACEHOLDER_DISCHARGE, PLACEHOLDER_POWER)
PLACEHOLDERS = (*POWER_PLACEHOLDERS, PLACEHOLDER_MODE)

DEFAULT_MESSAGE_SPACING_S = 0.0
DEFAULT_TIMEOUT_S = 3.0
DEFAULT_CONFIRM_DELAY_S = 0.2
DEFAULT_CONFIRM_TOLERANCE_W = 100.0
DEFAULT_MAX_CHANGES_PER_DAY = 100
# A persistent register changed again within this time is warned about.
PERSISTENT_WARN_S = 60.0
PERSISTENT_WINDOW_S = 24 * 3600.0

# Codes of ``ProfileError``.
ERROR_CODES = (
    "syntax", "not_profile", "version_newer", "duplicate_key", "unknown_key", "missing_key", "invalid",
    "own_pv", "control", "duplicate_address", "outside_at_rest",
)
# Keys that only describe a profile: changing them does not change its content hash.
DESCRIPTIVE_KEYS = ("name", "manufacturer", "notes")

_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_]*")
_INFO_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


class ProfileError(ValueError):
    """A profile that cannot be used: ``code``, where (``path``, ``line``) and what."""

    def __init__(self, code: str, path: str = "", line: int | None = None, detail: str = "") -> None:
        self.code = code
        self.path = path
        self.line = line
        self.detail = detail
        where = path + (f" (line {line})" if line is not None else "")
        super().__init__(" ".join(part for part in (where + ":" if where else "", code, detail) if part))


@dataclass(frozen=True)
class Register:
    """One value read from the battery."""

    address: int
    type: str
    scale: float = 1.0
    word_order: str = "big"
    input: bool = False
    # Registers of a char value.
    count: int = 1

    @property
    def size(self) -> int:
        return REGISTER_TYPES[self.type] or self.count


@dataclass(frozen=True)
class ReadValue:
    """A read key: one register or the sum of several, with an optional fallback."""

    registers: tuple[Register, ...]
    positive: str | None = None
    fallback: Register | None = None


@dataclass(frozen=True)
class StateRead:
    register: Register
    values: Mapping[int, str]


@dataclass(frozen=True)
class WriteStep:
    """One register written by ``apply`` or ``release``."""

    address: int
    value: int | float | str
    type: str = "uint16"
    scale: float = 1.0
    word_order: str = "big"
    at_rest: tuple[float, float] | None = None
    at_rest_values: tuple[float, ...] | None = None
    modes: Mapping[str, int] | None = None
    persistent: bool = False
    max_changes_per_day: int = DEFAULT_MAX_CHANGES_PER_DAY
    # Read back after a complete write (keep-alive, release).
    confirm: bool = False

    @property
    def size(self) -> int:
        return REGISTER_TYPES[self.type] or 1

    def rests_at(self, raw: float) -> bool:
        """Whether ``raw`` is a value of this register at rest (True without a range)."""
        if self.at_rest_values is not None:
            return raw in self.at_rest_values
        if self.at_rest is not None:
            return self.at_rest[0] <= raw <= self.at_rest[1]
        return True


@dataclass(frozen=True)
class Confirm:
    delay_s: float = DEFAULT_CONFIRM_DELAY_S
    tolerance_w: float = DEFAULT_CONFIRM_TOLERANCE_W
    tolerance_pct: float = 0.0


@dataclass(frozen=True)
class Control:
    type: str
    apply: tuple[WriteStep, ...]
    release: Mapping[ReleaseState, tuple[WriteStep, ...]]
    setpoint: str = "battery"
    # signed: the direction of a positive {power_w}.
    positive: str | None = None
    keepalive_s: float | None = None
    confirm: Confirm = field(default_factory=Confirm)

    def step(self, address: int) -> WriteStep | None:
        return next((step for step in self.apply if step.address == address), None)

    @property
    def confirmed(self) -> bool:
        """Whether a complete write is read back (else it counts as unconfirmed)."""
        return any(step.confirm for step in self.apply)


@dataclass(frozen=True)
class Connection:
    port: int = 502
    unit_id: int = 1
    message_spacing_s: float = DEFAULT_MESSAGE_SPACING_S
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class Limits:
    max_charge_w: int
    max_discharge_w: int
    # Smallest power the battery accepts; smaller commands become 0.
    min_power_w: int = 0


@dataclass(frozen=True)
class Timing:
    """The times of ``BatteryDriver`` (see drivers/base.py)."""

    start_delay_s: float | None = None
    start_extra_s: float = 0.0
    reversal_extra_s: float = 0.0
    min_command_interval_s: float = 0.0


@dataclass(frozen=True)
class OwnPv:
    """The traits of ``BatteryCapabilities`` for a battery with own PV."""

    ac_charge: bool = True
    max_output_w: int | None = None
    passes_pv_through: bool = False


@dataclass(frozen=True)
class Probe:
    register: Register
    range: tuple[float, float]


@dataclass(frozen=True)
class BatteryProfile:
    id: str
    name: str
    manufacturer: str
    revision: int
    notes: str
    connection: Connection
    limits: Limits
    timing: Timing
    own_pv: OwnPv | None
    read: Mapping[str, ReadValue]
    state: StateRead | None
    device_info: Mapping[str, Register]
    control: Control
    probe: Probe
    bundled: bool = False
    # Own profiles: hash of the content without the descriptive keys.
    content_hash: str = ""

    @property
    def key(self) -> str:
        """What a passed trial holds for: ``id@revision``, for an own profile with its content hash."""
        key = f"{self.id}@{self.revision}"
        return f"{key}#{self.content_hash}" if self.content_hash else key

    @property
    def write_addresses(self) -> tuple[int, ...]:
        return tuple(step.address for step in self.control.apply)


# --- values ------------------------------------------------------------------


def decode(words: list[int], data_type: str, word_order: str = "big") -> int | float | str:
    """Value of registers read (16 bit words) for a data type."""
    if data_type == "char":
        raw = b"".join(word.to_bytes(2, "big") for word in words)
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    if word_order == "little":
        words = list(reversed(words))
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    return struct.unpack(_STRUCT[data_type], raw)[0]


def encode(value: float, data_type: str, word_order: str = "big") -> list[int]:
    """Registers (16 bit words) of a value; ValueError outside the data type."""
    low, high = _TYPE_RANGE[data_type]
    if not low <= value <= high:
        raise ValueError(f"{value} outside {data_type}")
    raw = struct.pack(_STRUCT[data_type], value if data_type == "float32" else int(value))
    words = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
    return list(reversed(words)) if word_order == "little" else words


def split_power(profile: BatteryProfile, net_power_w: float) -> tuple[int, int]:
    """(charge, discharge) in W for a signed command (+charge), within the limits;
    a power below ``min_power_w`` becomes 0."""
    limits = profile.limits
    charge = min(max(net_power_w, 0.0), limits.max_charge_w)
    discharge = min(max(-net_power_w, 0.0), limits.max_discharge_w)
    charge, discharge = round(charge), round(discharge)
    if charge < limits.min_power_w:
        charge = 0
    if discharge < limits.min_power_w:
        discharge = 0
    return charge, discharge


def raw_value(step: WriteStep, *, charge_w: float = 0, discharge_w: float = 0, positive: str | None = None) -> float:
    """Raw value of a step for these powers (W at the set point's reference)."""
    if charge_w and discharge_w:
        raise ValueError("charge and discharge at once")
    value = step.value
    if not isinstance(value, str):
        return value
    if value == PLACEHOLDER_MODE:
        mode = "charge" if charge_w > 0 else "discharge" if discharge_w > 0 else "idle"
        return step.modes[mode]  # type: ignore[index]
    if value == PLACEHOLDER_CHARGE:
        watts = charge_w
    elif value == PLACEHOLDER_DISCHARGE:
        watts = discharge_w
    else:
        watts = charge_w - discharge_w
        if positive == "discharge":
            watts = -watts
    raw = watts / step.scale
    return raw if step.type == "float32" else round(raw)


def apply_values(profile: BatteryProfile, charge_w: float, discharge_w: float) -> list[tuple[WriteStep, float]]:
    """The steps of ``apply`` with their raw values, in the order of the profile."""
    control = profile.control
    return [
        (step, raw_value(step, charge_w=charge_w, discharge_w=discharge_w, positive=control.positive))
        for step in control.apply
    ]


def release_values(profile: BatteryProfile, state: ReleaseState) -> list[tuple[WriteStep, float]]:
    """The steps of a release state with their raw values."""
    return [(step, step.value) for step in profile.control.release.get(state, ())]  # type: ignore[misc]


@dataclass(frozen=True)
class RestViolation:
    address: int
    value: float | None
    step: WriteStep


def rest_violations(profile: BatteryProfile, values: Mapping[int, float | None]) -> list[RestViolation]:
    """Write registers whose value read is outside their range at rest or was not read;
    nothing may be written while there is one."""
    return [
        RestViolation(step.address, value, step)
        for step in profile.control.apply
        if (value := values.get(step.address)) is None or not step.rests_at(value)
    ]


class PersistentGuard:
    """Writes of the persistent registers (flash) of one battery.

    Only changes are written, never a keep-alive. A change within
    ``PERSISTENT_WARN_S`` of the previous one is written with a warning; more
    than ``max_changes_per_day`` in 24 h are not written until the oldest one
    leaves the window.
    """

    WRITE = "write"
    WARN = "warn"
    SKIP = "skip"
    BLOCKED = "blocked"

    def __init__(self, steps: Iterable[WriteStep]) -> None:
        self._steps = {step.address: step for step in steps if step.persistent}
        self._written: dict[int, float] = {}
        self._changes: dict[int, deque[float]] = {address: deque() for address in self._steps}

    def decide(self, address: int, raw: float, now: float) -> str:
        """What to do with ``raw`` for the register (``now``: monotonic seconds)."""
        step = self._steps.get(address)
        if step is None:
            return self.WRITE
        if self._written.get(address) == raw:
            return self.SKIP
        changes = self._changes[address]
        while changes and now - changes[0] >= PERSISTENT_WINDOW_S:
            changes.popleft()
        if len(changes) >= step.max_changes_per_day:
            return self.BLOCKED
        if changes and now - changes[-1] < PERSISTENT_WARN_S:
            return self.WARN
        return self.WRITE

    def written(self, address: int, raw: float, now: float) -> None:
        """A write of the register went through."""
        if address not in self._steps or self._written.get(address) == raw:
            return
        self._written[address] = raw
        self._changes[address].append(now)

    def forget(self) -> None:
        """The values in the device are unknown again (e.g. after a reconnect)."""
        self._written.clear()


# --- loading -----------------------------------------------------------------


class _LineDict(dict):
    """A mapping of the YAML document with the line of the mapping and of each key."""

    line: int = 0
    lines: dict[Any, int]


class _Loader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _Loader, node: yaml.MappingNode) -> _LineDict:
    loader.flatten_mapping(node)
    mapping = _LineDict()
    mapping.line = node.start_mark.line + 1
    mapping.lines = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise ProfileError("duplicate_key", str(key), key_node.start_mark.line + 1)
        mapping[key] = loader.construct_object(value_node, deep=True)
        mapping.lines[key] = key_node.start_mark.line + 1
    return mapping


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def parse_profile(text: str, *, bundled: bool = False) -> BatteryProfile:
    """Load and check a profile; raises ``ProfileError``.

    Profiles shipped with SLEMS (``bundled``) may leave out ``at_rest``.
    """
    try:
        document = yaml.load(text, Loader=_Loader)  # noqa: S506 - safe loader subclass
    except ProfileError:
        raise
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        line = mark.line + 1 if mark else None
        raise ProfileError("syntax", line=line, detail=str(getattr(err, "problem", ""))) from err
    if not isinstance(document, _LineDict) or FORMAT_KEY not in document:
        raise ProfileError("not_profile")
    profile = _Parser(bundled).profile(document)
    if bundled:
        return profile
    content = {key: value for key, value in document.items() if key not in DESCRIPTIVE_KEYS}
    digest = hashlib.sha256(_canonical(content).encode()).hexdigest()[:12]
    return dataclasses.replace(profile, content_hash=digest)


def _canonical(value: Any) -> str:
    """A text of a loaded YAML value that does not depend on order, layout or comments."""
    if isinstance(value, dict):
        items = sorted((_canonical(key), _canonical(item)) for key, item in value.items())
        return "{" + ",".join(f"{key}:{item}" for key, item in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    return f"{type(value).__name__}:{value!r}"


def profile_of(data: Mapping[str, Any], bundled: Mapping[str, BatteryProfile]) -> BatteryProfile:
    """The profile of a battery's subentry data: its own YAML or a shipped one by id;
    raises ``ProfileError``."""
    if text := data.get(CONF_PROFILE_YAML):
        return parse_profile(text)
    if (profile := bundled.get(data.get(CONF_PROFILE_ID, ""))) is None:
        raise ProfileError("unknown_profile", detail=str(data.get(CONF_PROFILE_ID)))
    return profile


def bundled_profile_text(profile_id: str) -> str:
    """The YAML of a shipped profile (reads files: not in the event loop)."""
    for path in sorted(BUNDLED_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if parse_profile(text, bundled=True).id == profile_id:
            return text
    raise KeyError(profile_id)


@functools.cache
def bundled_profiles() -> dict[str, BatteryProfile]:
    """The profiles shipped with SLEMS by id (reads files: not in the event loop)."""
    profiles = {}
    for path in sorted(BUNDLED_DIR.glob("*.yaml")):
        profile = parse_profile(path.read_text(encoding="utf-8"), bundled=True)
        profiles[profile.id] = profile
    return profiles


class _Parser:
    def __init__(self, bundled: bool) -> None:
        self._bundled = bundled

    # --- helpers

    @staticmethod
    def _fail(code: str, path: str, where: _LineDict | None, key: Any = None, detail: str = "") -> ProfileError:
        line = None
        if isinstance(where, _LineDict):
            line = where.lines.get(key, where.line) if key is not None else where.line
        return ProfileError(code, path, line, detail)

    def _mapping(self, value: Any, path: str, allowed: Iterable[str], parent: _LineDict | None, key: Any) -> _LineDict:
        if not isinstance(value, _LineDict):
            raise self._fail("invalid", path, parent, key, "mapping expected")
        unknown = [str(k) for k in value if k not in allowed]
        if unknown:
            raise self._fail("unknown_key", path, value, unknown[0], ", ".join(unknown))
        return value

    def _required(self, mapping: _LineDict, key: str, path: str) -> Any:
        if key not in mapping:
            raise self._fail("missing_key", f"{path}.{key}" if path else key, mapping)
        return mapping[key]

    def _number(
        self, mapping: _LineDict, key: str, path: str, *, default: Any = ..., minimum: float | None = 0,
        integer: bool = False, exclusive: bool = False,
    ) -> Any:
        if key not in mapping:
            if default is ...:
                raise self._fail("missing_key", f"{path}.{key}", mapping)
            return default
        value = mapping[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
            expected = "integer expected" if integer else "number expected"
            raise self._fail("invalid", f"{path}.{key}", mapping, key, expected)
        if minimum is not None and (value <= minimum if exclusive else value < minimum):
            bound = f"must be {'>' if exclusive else '≥'} {minimum}"
            raise self._fail("invalid", f"{path}.{key}", mapping, key, bound)
        return value

    def _text(self, mapping: _LineDict, key: str, path: str, *, default: Any = ...) -> Any:
        if key not in mapping:
            if default is ...:
                raise self._fail("missing_key", f"{path}.{key}" if path else key, mapping)
            return default
        value = mapping[key]
        if not isinstance(value, str) or not value.strip():
            raise self._fail("invalid", f"{path}.{key}" if path else key, mapping, key, "text expected")
        return value.strip()

    def _choice(self, mapping: _LineDict, key: str, path: str, choices: Iterable[str], *, default: Any = ...) -> Any:
        choices = tuple(choices)
        if key not in mapping and default is not ...:
            return default
        value = self._required(mapping, key, path)
        if value not in choices:
            raise self._fail("invalid", f"{path}.{key}", mapping, key, f"one of {', '.join(choices)}")
        return value

    def _flag(self, mapping: _LineDict, key: str, path: str, default: bool = False) -> bool:
        value = mapping.get(key, default)
        if not isinstance(value, bool):
            raise self._fail("invalid", f"{path}.{key}", mapping, key, "true or false expected")
        return value

    def _address(self, mapping: _LineDict, path: str) -> int:
        address = self._number(mapping, "address", path, integer=True)
        if address > 0xFFFF:
            raise self._fail("invalid", f"{path}.address", mapping, "address", "0-65535")
        return address

    def _range(self, mapping: _LineDict, key: str, path: str) -> tuple[float, float]:
        value = mapping[key]
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)
            or value[0] > value[1]
        ):
            raise self._fail("invalid", f"{path}.{key}", mapping, key, "[min, max] expected")
        return value[0], value[1]

    # --- sections

    def profile(self, doc: _LineDict) -> BatteryProfile:
        self._mapping(doc, "", (
            FORMAT_KEY, "id", "name", "manufacturer", "revision", "notes", "connection", "limits",
            "timing", "own_pv", "read", "device_info", "control", "probe",
        ), None, None)
        version = doc[FORMAT_KEY]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise self._fail("invalid", FORMAT_KEY, doc, FORMAT_KEY)
        if version > FORMAT_VERSION:
            raise self._fail("version_newer", FORMAT_KEY, doc, FORMAT_KEY, str(version))
        profile_id = self._text(doc, "id", "")
        if not _ID_PATTERN.fullmatch(profile_id):
            raise self._fail("invalid", "id", doc, "id", "lower case letters, digits and _")
        limits = self._limits(doc)
        own_pv = self._own_pv(doc)
        read = self._read(doc, own_pv)
        control = self._control(doc, limits, own_pv)
        return BatteryProfile(
            id=profile_id,
            name=self._text(doc, "name", ""),
            manufacturer=self._text(doc, "manufacturer", "", default=""),
            revision=self._number(doc, "revision", "", default=1, minimum=1, integer=True),
            notes=self._text(doc, "notes", "", default=""),
            connection=self._connection(doc),
            limits=limits,
            timing=self._timing(doc),
            own_pv=own_pv,
            read=read,
            state=self._state(doc),
            device_info=self._device_info(doc),
            control=control,
            probe=self._probe(doc),
            bundled=self._bundled,
        )

    def _connection(self, doc: _LineDict) -> Connection:
        if "connection" not in doc:
            return Connection()
        keys = ("port", "unit_id", "message_spacing_s", "timeout_s")
        m = self._mapping(doc["connection"], "connection", keys, doc, "connection")
        port = self._number(m, "port", "connection", default=502, minimum=1, integer=True)
        unit_id = self._number(m, "unit_id", "connection", default=1, integer=True)
        if port > 0xFFFF or unit_id > 255:
            raise self._fail("invalid", "connection", m)
        return Connection(
            port=port,
            unit_id=unit_id,
            message_spacing_s=self._number(m, "message_spacing_s", "connection", default=DEFAULT_MESSAGE_SPACING_S),
            timeout_s=self._number(m, "timeout_s", "connection", default=DEFAULT_TIMEOUT_S, exclusive=True),
        )

    def _limits(self, doc: _LineDict) -> Limits:
        keys = ("max_charge_w", "max_discharge_w", "min_power_w")
        m = self._mapping(self._required(doc, "limits", ""), "limits", keys, doc, "limits")
        return Limits(
            max_charge_w=self._number(m, "max_charge_w", "limits", integer=True, exclusive=True),
            max_discharge_w=self._number(m, "max_discharge_w", "limits", integer=True, exclusive=True),
            min_power_w=self._number(m, "min_power_w", "limits", default=0, integer=True),
        )

    def _timing(self, doc: _LineDict) -> Timing:
        if "timing" not in doc:
            return Timing()
        keys = ("start_delay_s", "start_extra_s", "reversal_extra_s", "min_command_interval_s")
        m = self._mapping(doc["timing"], "timing", keys, doc, "timing")
        return Timing(
            start_delay_s=self._number(m, "start_delay_s", "timing", default=None),
            start_extra_s=self._number(m, "start_extra_s", "timing", default=0.0),
            reversal_extra_s=self._number(m, "reversal_extra_s", "timing", default=0.0),
            min_command_interval_s=self._number(m, "min_command_interval_s", "timing", default=0.0),
        )

    def _own_pv(self, doc: _LineDict) -> OwnPv | None:
        if "own_pv" not in doc:
            return None
        m = self._mapping(doc["own_pv"], "own_pv", ("ac_charge", "max_output_w", "passes_pv_through"), doc, "own_pv")
        return OwnPv(
            ac_charge=self._flag(m, "ac_charge", "own_pv", default=True),
            max_output_w=self._number(m, "max_output_w", "own_pv", default=None, integer=True, exclusive=True),
            passes_pv_through=self._flag(m, "passes_pv_through", "own_pv"),
        )

    def _register(
        self, value: Any, path: str, parent: _LineDict, key: Any, *, extra: Iterable[str] = ()
    ) -> tuple[Register, _LineDict]:
        keys = ("address", "type", "scale", "word_order", "input", "count", *extra)
        m = self._mapping(value, path, keys, parent, key)
        data_type = self._choice(m, "type", path, REGISTER_TYPES)
        count = 1
        if data_type == "char":
            count = self._number(m, "count", path, integer=True, exclusive=True)
        elif "count" in m:
            raise self._fail("invalid", f"{path}.count", m, "count", "only for char")
        scale = self._number(m, "scale", path, default=1.0, minimum=None)
        if scale == 0:
            raise self._fail("invalid", f"{path}.scale", m, "scale", "must not be 0")
        register = Register(
            address=self._address(m, path),
            type=data_type,
            scale=scale,
            word_order=self._choice(m, "word_order", path, ("big", "little"), default="big"),
            input=self._flag(m, "input", path),
            count=count,
        )
        if register.address + register.size - 1 > 0xFFFF:
            raise self._fail("invalid", f"{path}.address", m, "address", "beyond 65535")
        return register, m

    def _read(self, doc: _LineDict, own_pv: OwnPv | None) -> dict[str, ReadValue]:
        m = self._mapping(self._required(doc, "read", ""), "read", (*READ_KEYS, "state"), doc, "read")
        read: dict[str, ReadValue] = {}
        for key, value in m.items():
            if key == "state":
                continue
            path = f"read.{key}"
            items = value if isinstance(value, list) else [value]
            if isinstance(value, list) and (key not in SUM_READ_KEYS or not value):
                raise self._fail("invalid", path, m, key, "a sum only for powers")
            registers = []
            positive = None
            fallback = None
            for index, item in enumerate(items):
                item_path = f"{path}[{index}]" if isinstance(value, list) else path
                register, rm = self._register(item, item_path, m, key, extra=("positive", "fallback"))
                if key in SIGNED_READ_KEYS:
                    sign = self._choice(rm, "positive", item_path, SIGNED_READ_KEYS[key])
                    if positive is not None and sign != positive:
                        raise self._fail("invalid", f"{item_path}.positive", rm, "positive", "same for all parts")
                    positive = sign
                elif "positive" in rm:
                    raise self._fail("invalid", f"{item_path}.positive", rm, "positive", "only for signed powers")
                if "fallback" in rm:
                    if isinstance(value, list):
                        raise self._fail("invalid", f"{item_path}.fallback", rm, "fallback", "not in a sum")
                    fallback, _ = self._register(rm["fallback"], f"{item_path}.fallback", rm, "fallback")
                if register.type == "char":
                    raise self._fail("invalid", f"{item_path}.type", rm, "type", "a number type")
                registers.append(register)
            read[key] = ReadValue(tuple(registers), positive, fallback)
        if "soc_pct" not in read:
            raise self._fail("missing_key", "read.soc_pct", m)
        if own_pv is None:
            if own := [key for key in ("pv_w", "output_w", "input_w", "connection_w") if key in read]:
                raise self._fail("own_pv", f"read.{own[0]}", m, own[0], "needs the section own_pv")
            if "power_dc_w" not in read and "power_ac_w" not in read:
                raise self._fail("missing_key", "read.power_ac_w", m, detail="power_dc_w or power_ac_w")
        else:
            if "pv_w" not in read:
                raise self._fail("own_pv", "read.pv_w", m, detail="own_pv needs pv_w")
            if ("output_w" in read) == ("connection_w" in read):
                raise self._fail("own_pv", "read.output_w", m, detail="output_w or connection_w")
            if "input_w" in read and "output_w" not in read:
                raise self._fail("own_pv", "read.input_w", m, "input_w", "only with output_w")
            if own_pv.ac_charge and "output_w" in read and "input_w" not in read:
                raise self._fail("own_pv", "own_pv.ac_charge", doc, "own_pv", "charging from the grid needs input_w")
        return read

    def _state(self, doc: _LineDict) -> StateRead | None:
        read = doc["read"]
        if "state" not in read:
            return None
        register, m = self._register(read["state"], "read.state", read, "state", extra=("values",))
        if register.type not in ("uint16", "int16"):
            raise self._fail("invalid", "read.state.type", m, "type", "uint16 or int16")
        values = self._required(m, "values", "read.state")
        if not isinstance(values, _LineDict) or not values:
            raise self._fail("invalid", "read.state.values", m, "values", "mapping value: state")
        for raw, name in values.items():
            if isinstance(raw, bool) or not isinstance(raw, int) or name not in STATES:
                raise self._fail("invalid", f"read.state.values.{raw}", values, raw, f"states: {', '.join(STATES)}")
        return StateRead(register, dict(values))

    def _device_info(self, doc: _LineDict) -> dict[str, Register]:
        if "device_info" not in doc:
            return {}
        m = doc["device_info"]
        if not isinstance(m, _LineDict):
            raise self._fail("invalid", "device_info", doc, "device_info", "mapping expected")
        info = {}
        for key, value in m.items():
            if not isinstance(key, str) or not _INFO_KEY_PATTERN.fullmatch(key):
                raise self._fail("invalid", f"device_info.{key}", m, key, "lower case name")
            info[key], _ = self._register(value, f"device_info.{key}", m, key)
        return info

    def _probe(self, doc: _LineDict) -> Probe:
        register, m = self._register(self._required(doc, "probe", ""), "probe", doc, "probe", extra=("range",))
        if register.type == "char":
            raise self._fail("invalid", "probe.type", m, "type", "a number type")
        self._required(m, "range", "probe")
        return Probe(register, self._range(m, "range", "probe"))

    # --- control

    def _control(self, doc: _LineDict, limits: Limits, own_pv: OwnPv | None) -> Control:
        m = self._mapping(
            self._required(doc, "control", ""), "control",
            ("type", "setpoint", "positive", "keepalive_s", "apply", "confirm", "release"), doc, "control",
        )
        control_type = self._choice(m, "type", "control", ("split", "signed"))
        setpoint = self._choice(m, "setpoint", "control", ("battery", "connection"), default="battery")
        if setpoint == "connection" and own_pv is None:
            raise self._fail("own_pv", "control.setpoint", m, "setpoint", "connection needs own_pv")
        positive = None
        if control_type == "signed":
            positive = self._choice(m, "positive", "control", ("charge", "discharge"))
        elif "positive" in m:
            raise self._fail("invalid", "control.positive", m, "positive", "only for signed")
        keepalive = self._number(m, "keepalive_s", "control", default=None, exclusive=True)
        confirm = Confirm()
        if "confirm" in m:
            keys = ("delay_s", "tolerance_w", "tolerance_pct")
            cm = self._mapping(m["confirm"], "control.confirm", keys, m, "confirm")
            confirm = Confirm(
                delay_s=self._number(cm, "delay_s", "control.confirm", default=DEFAULT_CONFIRM_DELAY_S),
                tolerance_w=self._number(cm, "tolerance_w", "control.confirm", default=DEFAULT_CONFIRM_TOLERANCE_W),
                tolerance_pct=self._number(cm, "tolerance_pct", "control.confirm", default=0.0),
            )

        steps = self._required(m, "apply", "control")
        if not isinstance(steps, list) or not steps:
            raise self._fail("invalid", "control.apply", m, "apply", "list of steps expected")
        apply = tuple(self._apply_step(item, f"control.apply[{i}]", m) for i, item in enumerate(steps))
        self._check_addresses(apply, m)
        self._check_placeholders(apply, m, control_type, setpoint, own_pv)
        for index, step in enumerate(apply):
            self._check_powers(step, f"control.apply[{index}]", steps[index], limits, own_pv, setpoint, positive)

        release = self._release(m, apply)
        return Control(
            type=control_type,
            apply=apply,
            release=release,
            setpoint=setpoint,
            positive=positive,
            keepalive_s=keepalive,
            confirm=confirm,
        )

    def _apply_step(self, item: Any, path: str, parent: _LineDict) -> WriteStep:
        m = self._mapping(item, path, (
            "address", "value", "type", "scale", "word_order", "at_rest", "at_rest_values", "modes",
            "persistent", "max_changes_per_day", "confirm",
        ), parent, "apply")
        address = self._address(m, path)
        data_type = self._choice(m, "type", path, WRITE_TYPES, default="uint16")
        value = self._required(m, "value", path)
        placeholder = isinstance(value, str)
        if placeholder and value not in PLACEHOLDERS:
            raise self._fail("invalid", f"{path}.value", m, "value", f"a number or one of {', '.join(PLACEHOLDERS)}")
        if not placeholder and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise self._fail("invalid", f"{path}.value", m, "value", "number expected")
        if not placeholder and data_type != "float32" and not isinstance(value, int):
            raise self._fail("invalid", f"{path}.value", m, "value", "integer expected")
        scale = self._number(m, "scale", path, default=1.0, minimum=0, exclusive=True)
        if "scale" in m and value not in POWER_PLACEHOLDERS:
            raise self._fail("invalid", f"{path}.scale", m, "scale", "only with a power placeholder")
        modes = None
        if value == PLACEHOLDER_MODE:
            modes_map = self._mapping(self._required(m, "modes", path), f"{path}.modes", MODES, m, "modes")
            if missing := [mode for mode in MODES if mode not in modes_map]:
                raise self._fail("missing_key", f"{path}.modes.{missing[0]}", modes_map)
            modes = {mode: self._number(modes_map, mode, f"{path}.modes", minimum=None, integer=True) for mode in MODES}
        elif "modes" in m:
            raise self._fail("invalid", f"{path}.modes", m, "modes", "only with {mode}")
        if "at_rest" in m and "at_rest_values" in m:
            raise self._fail("invalid", f"{path}.at_rest", m, "at_rest", "at_rest or at_rest_values")
        at_rest = self._range(m, "at_rest", path) if "at_rest" in m else None
        at_rest_values = None
        if "at_rest_values" in m:
            values = m["at_rest_values"]
            if (
                not isinstance(values, list)
                or not values
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values)
            ):
                raise self._fail("invalid", f"{path}.at_rest_values", m, "at_rest_values", "list of values expected")
            at_rest_values = tuple(values)
        if at_rest is None and at_rest_values is None and not self._bundled:
            raise self._fail("missing_key", f"{path}.at_rest", m)
        persistent = self._flag(m, "persistent", path)
        if persistent and value in POWER_PLACEHOLDERS:
            raise self._fail(
                "persistent_power", f"{path}.persistent", m, "persistent", "a power set point must not be in flash"
            )
        if "max_changes_per_day" in m and not persistent:
            raise self._fail("invalid", f"{path}.max_changes_per_day", m, "max_changes_per_day", "only with persistent")
        step = WriteStep(
            address=address,
            value=value,
            type=data_type,
            scale=scale,
            word_order=self._choice(m, "word_order", path, ("big", "little"), default="big"),
            at_rest=at_rest,
            at_rest_values=at_rest_values,
            modes=modes,
            persistent=persistent,
            max_changes_per_day=self._number(
                m, "max_changes_per_day", path, default=DEFAULT_MAX_CHANGES_PER_DAY, integer=True, exclusive=True
            ),
            confirm=self._flag(m, "confirm", path),
        )
        fixed = [value] if not placeholder else list(modes.values()) if modes else []
        for raw in fixed:
            self._check_raw(step, raw, path, m, "value" if not modes else "modes")
        return step

    def _check_raw(self, step: WriteStep, raw: float, path: str, where: _LineDict, key: str) -> None:
        low, high = _TYPE_RANGE[step.type]
        if not low <= raw <= high:
            raise self._fail("invalid", f"{path}.{key}", where, key, f"{raw} outside {step.type}")
        if not step.rests_at(raw):
            raise self._fail("outside_at_rest", f"{path}.{key}", where, key, f"{raw}")

    def _check_addresses(self, apply: tuple[WriteStep, ...], where: _LineDict) -> None:
        taken: dict[int, int] = {}
        for index, step in enumerate(apply):
            for address in range(step.address, step.address + step.size):
                if address in taken:
                    raise self._fail(
                        "duplicate_address", f"control.apply[{index}].address", where, "apply",
                        f"{address} also in control.apply[{taken[address]}]",
                    )
                taken[address] = index

    def _check_placeholders(
        self, apply: tuple[WriteStep, ...], where: _LineDict, control_type: str, setpoint: str, own_pv: OwnPv | None
    ) -> None:
        used = [step.value for step in apply if isinstance(step.value, str)]
        for placeholder in set(used):
            if used.count(placeholder) > 1:
                raise self._fail("control", "control.apply", where, "apply", f"{placeholder} more than once")
        if control_type == "signed":
            needed, forbidden = {PLACEHOLDER_POWER}, {PLACEHOLDER_CHARGE, PLACEHOLDER_DISCHARGE}
        else:
            needed, forbidden = {PLACEHOLDER_CHARGE, PLACEHOLDER_DISCHARGE}, {PLACEHOLDER_POWER}
            if setpoint == "connection" and own_pv is not None and not own_pv.ac_charge:
                # Only an output: no input from the grid to set.
                needed, forbidden = {PLACEHOLDER_DISCHARGE}, {PLACEHOLDER_POWER, PLACEHOLDER_CHARGE}
        if missing := needed - set(used):
            detail = f"{control_type} needs {', '.join(sorted(missing))}"
            raise self._fail("control", "control.apply", where, "apply", detail)
        if wrong := forbidden & set(used):
            detail = f"{', '.join(sorted(wrong))} not with {control_type}"
            raise self._fail("control", "control.apply", where, "apply", detail)

    def _check_powers(
        self, step: WriteStep, path: str, where: _LineDict, limits: Limits, own_pv: OwnPv | None, setpoint: str,
        positive: str | None,
    ) -> None:
        """The highest powers must fit the data type and the range at rest (catches a wrong scale)."""
        if step.value not in POWER_PLACEHOLDERS:
            return
        max_charge = limits.max_charge_w
        max_discharge = limits.max_discharge_w
        if setpoint == "connection" and own_pv is not None and own_pv.max_output_w is not None:
            max_discharge = own_pv.max_output_w
        cases = (
            [{"charge_w": max_charge}, {"discharge_w": max_discharge}]
            if step.value == PLACEHOLDER_POWER
            else [{"charge_w": max_charge}] if step.value == PLACEHOLDER_CHARGE
            else [{"discharge_w": max_discharge}]
        )
        key = next((key for key in ("at_rest", "at_rest_values") if key in where), "value")
        for powers in [{}, *cases]:
            self._check_raw(step, raw_value(step, positive=positive, **powers), path, where, key)

    def _release(self, m: _LineDict, apply: tuple[WriteStep, ...]) -> dict[ReleaseState, tuple[WriteStep, ...]]:
        states = [state.value for state in ReleaseState]
        rm = self._mapping(self._required(m, "release", "control"), "control.release", states, m, "release")
        if not rm:
            raise self._fail("missing_key", "control.release.auto", rm)
        by_address = {step.address: step for step in apply}
        release: dict[ReleaseState, tuple[WriteStep, ...]] = {}
        for state in ReleaseState:
            if state.value not in rm:
                continue
            items = rm[state.value]
            path = f"control.release.{state.value}"
            if not isinstance(items, list) or not items:
                raise self._fail("invalid", path, rm, state.value, "list of steps expected")
            steps = []
            for index, item in enumerate(items):
                item_path = f"{path}[{index}]"
                sm = self._mapping(item, item_path, ("address", "value", "confirm"), rm, state.value)
                address = self._address(sm, item_path)
                declared = by_address.get(address)
                if declared is None:
                    raise self._fail(
                        "undeclared_register", f"{item_path}.address", sm, "address", "not in control.apply"
                    )
                value = self._required(sm, "value", item_path)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise self._fail("invalid", f"{item_path}.value", sm, "value", "a fixed number")
                step = WriteStep(
                    address=address,
                    value=value,
                    type=declared.type,
                    scale=declared.scale,
                    word_order=declared.word_order,
                    at_rest=declared.at_rest,
                    at_rest_values=declared.at_rest_values,
                    persistent=declared.persistent,
                    max_changes_per_day=declared.max_changes_per_day,
                    confirm=self._flag(sm, "confirm", item_path),
                )
                self._check_raw(step, value, item_path, sm, "value")
                steps.append(step)
            if len({step.address for step in steps}) != len(steps):
                raise self._fail("duplicate_address", path, rm, state.value)
            release[state] = tuple(steps)
        return release
