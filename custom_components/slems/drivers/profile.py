"""Driver for a battery described by a Modbus profile (see battery_profiles).

The profile gives the registers, the steps to charge, discharge and release
the battery and its limits; this driver only carries them out. The transport
is chosen apart from the profile: SLEMS' own Modbus client
(``ModbusTcpLink``, with the fixes for the Venus v3 firmware, which do not
touch other devices) or a unit of Home Assistant's Modbus backend
(``async_get_unit``, shared with other integrations on the same device).

Reading: the registers of the telemetry are read in blocks, registers at most
``BLOCK_GAP`` registers apart in one frame; a block the device refuses (e.g.
because of unassigned registers between them) is remembered and its registers
are read one by one, like the Venus driver does. Slowly changing values are
read at most every ``SLOW_READ_S``.

Checks before writing (see the profile concept, section 6):

* Range at rest: on every take-over (the first write after connecting, after
  a release and after a failed write or read-back) the write registers are
  read; while one is outside its range at rest (or cannot be read) nothing is
  written and the repair ``profile_outside_rest`` is shown. Every read-back
  checks the range as well.
* Read-back: after a complete write (keep-alive) the steps with ``confirm``
  are read back; a set point within the tolerance of the profile.
* Persistent registers (flash): written only when they change, never by a
  keep-alive; a change within a minute of the previous one is written with
  a warning (repair ``profile_persistent_frequent`` for an hour), more than
  ``max_changes_per_day`` in 24 h are not written (``profile_persistent_blocked``).

A battery with own PV whose set point refers to its AC connection
(``setpoint: connection``) gets the input and the output from own_pv, like
the control Output of a battery from entities.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import time
from typing import Any, Protocol

from homeassistant.exceptions import HomeAssistantError
from modbus_connection import ModbusError, ModbusExceptionError

from ..battery_profiles import (
    PLACEHOLDER_CHARGE,
    PLACEHOLDER_DISCHARGE,
    PLACEHOLDER_POWER,
    NEGATIVE_DIRECTIONS,
    READ_KEYS,
    STATE_EXTRA_KEY,
    STATES,
    BatteryProfile,
    PersistentGuard,
    ReadValue,
    Register,
    WriteStep,
    apply_values,
    decode,
    encode,
    release_values,
    rest_violations,
    split_power,
)
from ..const import ReleaseState
from ..own_pv import OwnPvPorts
from ..profile_check import ProbeReading
from ..self_test import SMALL_W, TrialRegister, TrialSpec
from .base import BatteryCapabilities, BatteryDriver, BatteryDriverError, BatteryTelemetry
from .modbus_client import ModbusTcpLink

_LOGGER = logging.getLogger(__name__)

# Registers at most this far apart are read in one frame (the Venus answers
# 30001-30006 in one frame, but refuses ranges over unassigned registers).
BLOCK_GAP = 5
MAX_BLOCK = 125
# Slowly changing values, read at most every SLOW_READ_S (like the Venus driver).
SLOW_READ_KEYS = frozenset({"voltage_v", "temperature_c", "cycles", "charged_kwh", "discharged_kwh"})
SLOW_READ_S = 60.0
# A device may refuse connections shortly after the last one closed (a Venus
# for about 0.5 s): a probe tries again once after this time.
PROBE_RETRY_S = 1.0
# Wait before using a connection again that HaUnitTransport dropped (s).
RECONNECT_SETTLE_S = 1.0
# Time the repair of a frequently changed persistent register stays (s).
PERSISTENT_WARN_SHOW_S = 3600.0

ISSUE_OUTSIDE_REST = "profile_outside_rest"
ISSUE_PERSISTENT_FREQUENT = "profile_persistent_frequent"
ISSUE_PERSISTENT_BLOCKED = "profile_persistent_blocked"
ISSUES = (ISSUE_OUTSIDE_REST, ISSUE_PERSISTENT_FREQUENT, ISSUE_PERSISTENT_BLOCKED)


class ProfileTransport(Protocol):
    """Modbus access of a profile driver (``ModbusTcpLink`` or ``HaUnitTransport``)."""

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> bool: ...

    async def close(self) -> None: ...

    async def read(self, address: int, count: int, *, input_registers: bool = False) -> list[int] | None: ...

    async def write(self, address: int, value: int | list[int]) -> bool: ...


class HaUnitTransport:
    """A unit of Home Assistant's Modbus backend (``async_get_unit``).

    The backend connects by itself and serializes the requests of all users of
    the device; ``close`` drops the shared connection (e.g. to pause the
    communication), the next request opens it again. A request without an
    answer drops it as well and fails the remaining requests until the next
    ``connect`` (the backend lets every request wait for its time-out, a
    silent battery cost up to 90 s per read); ``connect`` waits until
    ``RECONNECT_SETTLE_S`` after such a drop (a Venus refuses connections for
    about 0.5 s after the last one closed).
    """

    def __init__(self, unit_factory: Callable[[], Any], *, message_spacing_s: float, timeout_s: float) -> None:
        self._unit_factory = unit_factory
        self._unit: Any = None
        self._message_spacing_s = message_spacing_s
        self._timeout_s = timeout_s
        self._open = False
        # When this transport dropped the connection (monotonic).
        self._dropped_at: float | None = None

    @property
    def connected(self) -> bool:
        return self._open

    async def connect(self) -> bool:
        if self._unit is None:
            try:
                unit = self._unit_factory()
            except HomeAssistantError as err:
                # E.g. the device is already used over other link settings.
                _LOGGER.warning("Modbus unit not available: %s", err)
                return False
            unit.set_message_spacing(self._message_spacing_s)
            unit.require_timeout(self._timeout_s)
            self._unit = unit
        if self._dropped_at is not None:
            await asyncio.sleep(max(0.0, self._dropped_at + RECONNECT_SETTLE_S - time.monotonic()))
            self._dropped_at = None
        self._open = True
        return True

    async def close(self) -> None:
        if self._unit is not None and self._open:
            await self._disconnect()
        self._open = False

    async def _disconnect(self) -> None:
        try:
            await self._unit.disconnect()
        except ModbusError as err:
            _LOGGER.debug("Dropping the Modbus connection failed: %s", err)
        self._dropped_at = time.monotonic()

    async def _failed(self, what: str, address: int, err: Exception) -> None:
        """A failed request; without an answer the connection is dropped (see above)."""
        _LOGGER.debug("%s of register %d failed: %s", what, address, err)
        if not isinstance(err, ModbusExceptionError) and self._open:
            self._open = False
            await self._disconnect()

    async def read(self, address: int, count: int, *, input_registers: bool = False) -> list[int] | None:
        if not self._open:
            return None
        method = self._unit.read_input_registers if input_registers else self._unit.read_holding_registers
        try:
            return list(await method(address, count))
        except (ModbusError, OSError) as err:
            await self._failed("Read", address, err)
            return None

    async def write(self, address: int, value: int | list[int]) -> bool:
        if not self._open:
            return False
        try:
            if isinstance(value, list):
                await self._unit.write_registers(address, value)
            else:
                await self._unit.write_register(address, value)
        except (ModbusError, OSError) as err:
            await self._failed("Write", address, err)
            return False
        return True


def own_link(host: str, port: int, unit_id: int, profile: BatteryProfile) -> ModbusTcpLink:
    """SLEMS' own Modbus client for a profile."""
    return ModbusTcpLink(
        host,
        port,
        unit_id,
        message_wait_s=profile.connection.message_spacing_s,
        timeout_s=profile.connection.timeout_s,
        patch_v3_frames=True,
    )


async def connect_new(new: Callable[[], ProfileTransport]) -> ProfileTransport | None:
    """A connected new transport for the setup; a refused connection is tried once
    more with another one (see ``PROBE_RETRY_S``; a failed one waits its backoff)."""
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(PROBE_RETRY_S)
        transport = new()
        if await transport.connect():
            return transport
    return None


async def read_profile(profile: BatteryProfile, transport: ProfileTransport) -> ProbeReading | None:
    """Every register of the profile read once for the setup (nothing written); None if
    the battery does not answer the probe register.

    A transport that is not connected (that of a paused battery) is connected
    for it and closed again; a connected one is only used.
    """
    opened = False
    if not transport.connected:
        if not await transport.connect():
            return None
        opened = True
    try:
        register = profile.probe.register
        words = await transport.read(register.address, register.size, input_registers=register.input)
        if words is None:
            return None
        reading = ProbeReading(probe=decode(words, register.type, register.word_order))  # type: ignore[arg-type]
        # A driver of its own for the blocks; it neither connects nor writes here.
        driver = ProfileDriver(profile, transport, capacity_wh=0, max_charge_power_w=0, max_discharge_power_w=0)
        registers = [register for read in profile.read.values() for register in read.registers]
        if profile.state is not None:
            registers.append(profile.state.register)
        read = await driver._read(registers)
        for key, value in profile.read.items():
            total, sides = _value(value, read)
            if total is None and value.fallback is not None:
                fallback = value.fallback
                if (part := await transport.read(fallback.address, fallback.size, input_registers=fallback.input)):
                    total = _signed(value, _scaled(part, fallback))
            reading.values[key] = total
            if sides:
                reading.sides[key] = sides
        if profile.state is not None and (part := read[profile.state.register]) is not None:
            state = profile.state.register
            reading.state = decode(part, state.type, state.word_order)  # type: ignore[assignment]
        for key, info in profile.device_info.items():
            part = await transport.read(info.address, info.size, input_registers=info.input)
            reading.device_info[key] = (str(decode(part, info.type, info.word_order)) or None) if part else None
        reading.writes = await driver._read_writes(list(profile.control.apply))
        return reading
    finally:
        if opened:
            await transport.close()


def read_blocks(registers: list[Register]) -> list[tuple[int, int, bool]]:
    """Blocks (start, count, input registers) of registers close together; only
    blocks of at least two registers (a single one is read on its own)."""
    blocks: list[tuple[int, int, bool]] = []
    for kind in (False, True):
        spans = sorted({(r.address, r.address + r.size) for r in registers if r.input is kind})
        groups: list[list[tuple[int, int]]] = []
        for start, end in spans:
            group = groups[-1] if groups else None
            if group is not None:
                group_end = max(e for _, e in group)
                if start - group_end <= BLOCK_GAP and max(end, group_end) - group[0][0] <= MAX_BLOCK:
                    group.append((start, end))
                    continue
            groups.append([(start, end)])
        for group in groups:
            if len(group) >= 2:
                start = group[0][0]
                blocks.append((start, max(e for _, e in group) - start, kind))
    return blocks


class ProfileDriver(BatteryDriver):
    """A battery controlled through the registers of a Modbus profile."""

    def __init__(
        self,
        profile: BatteryProfile,
        transport: ProfileTransport,
        *,
        capacity_wh: float,
        max_charge_power_w: int,
        max_discharge_power_w: int,
        release_state: ReleaseState = ReleaseState.AUTO,
        device: str = "",
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._device = device
        if release_state not in profile.control.release:
            release_state = next(iter(profile.control.release))
        self._release_state = release_state
        own_pv = profile.own_pv
        self._capabilities = BatteryCapabilities(
            capacity_wh=capacity_wh,
            max_charge_power_w=min(max_charge_power_w, profile.limits.max_charge_w),
            max_discharge_power_w=min(max_discharge_power_w, profile.limits.max_discharge_w),
            controllable=True,
            own_pv=own_pv is not None,
            ac_charge=own_pv is None or own_pv.ac_charge,
            max_output_w=own_pv.max_output_w if own_pv else None,
            passes_pv_through=bool(own_pv and own_pv.passes_pv_through),
        )
        self._by_connection = profile.control.setpoint == "connection"
        self._own_pv = (
            OwnPvPorts(own_pv.max_output_w, own_pv.ac_charge, own_pv.passes_pv_through) if own_pv else None
        )
        # Raw words written per register; unchanged values are not written again.
        self._written: dict[int, list[int]] = {}
        self._write_count = 0
        # The write registers are checked against their range at rest before the next write.
        self._take_over = True
        self._guard = PersistentGuard(profile.control.apply)
        self._last: BatteryTelemetry | None = None
        self._slow: dict[str, Any] = {}
        self._slow_read_at: float | None = None
        self._refused_blocks: set[tuple[int, int, bool]] = set()
        # Blocks whose failure dropped the connection: read one by one next time.
        self._retry_single: set[tuple[int, int, bool]] = set()
        # Repairs: registers outside their range at rest (address -> text),
        # persistent registers blocked (address -> text) and warned (address -> monotonic time).
        self._outside_rest: dict[int, str] = {}
        self._blocked: dict[int, str] = {}
        self._frequent: dict[int, float] = {}

    # --- static traits -------------------------------------------------------

    @property
    def profile(self) -> BatteryProfile:
        return self._profile

    @property
    def transport(self) -> ProfileTransport:
        return self._transport

    @property
    def capabilities(self) -> BatteryCapabilities:
        return self._capabilities

    @property
    def model_name(self) -> str:
        return f"{self._profile.name} (profile)"

    @property
    def extra_telemetry_keys(self) -> frozenset[str]:
        keys = {extra for key, extra in READ_KEYS.items() if extra and key in self._profile.read}
        if self._profile.state is not None:
            keys.add(STATE_EXTRA_KEY)
        if self._own_pv is not None:
            keys |= {"ac_power", "own_pv_power", "output_power"}
            sides = self._profile.read["ac_power_w"].sides
            if sides is None or "input" in sides:
                keys.add("input_power")
        return frozenset(keys)

    @property
    def release_state(self) -> ReleaseState:
        return self._release_state

    @property
    def has_connection(self) -> bool:
        return True

    @property
    def min_command_interval_s(self) -> float:
        return self._profile.timing.min_command_interval_s

    @property
    def keepalive_s(self) -> float | None:
        return self._profile.control.keepalive_s

    @property
    def start_delay_s(self) -> float | None:
        return self._profile.timing.start_delay_s

    @property
    def start_extra_s(self) -> float:
        return self._profile.timing.start_extra_s

    @property
    def reversal_extra_s(self) -> float:
        return self._profile.timing.reversal_extra_s

    @property
    def trial_spec(self) -> TrialSpec:
        control = self._profile.control
        released = {step.address: raw for step, raw in release_values(self._profile, self._release_state)}
        fallback: dict[int, set[float]] = {}
        for state in control.release:
            for step, raw in release_values(self._profile, state):
                fallback.setdefault(step.address, set()).add(raw)
        return TrialSpec(
            tuple(
                TrialRegister(
                    step.address,
                    step.rests_at,
                    released.get(step.address),
                    frozenset(fallback.get(step.address, ())),
                    power=step.value in (PLACEHOLDER_CHARGE, PLACEHOLDER_DISCHARGE, PLACEHOLDER_POWER),
                    scale=step.scale,
                )
                for step in control.apply
            ),
            small_w=max(SMALL_W, self._profile.limits.min_power_w),
            keepalive_s=control.keepalive_s,
            probe_range=self._profile.probe.range,
        )

    @property
    def trial_key(self) -> str:
        return self._profile.key

    @property
    def trial_device(self) -> str:
        return self._device

    async def read_control_registers(self) -> dict[int, float | None]:
        await self._ensure_connected()
        return await self._read_writes(list(self._profile.control.apply))

    async def read_probe(self) -> float | None:
        await self._ensure_connected()
        register = self._profile.probe.register
        words = await self._transport.read(register.address, register.size, input_registers=register.input)
        return decode(words, register.type, register.word_order) if words else None  # type: ignore[return-value]

    @property
    def write_count(self) -> int:
        return self._write_count

    def written_values(self) -> dict[int, float]:
        return {
            step.address: decode(words, step.type, step.word_order)  # type: ignore[misc]
            for step in self._profile.control.apply
            if (words := self._written.get(step.address)) is not None
        }

    def issues(self) -> dict[str, dict[str, str]]:
        now = time.monotonic()
        result: dict[str, dict[str, str]] = {}
        if self._outside_rest:
            result[ISSUE_OUTSIDE_REST] = {"registers": ", ".join(self._outside_rest.values())}
        if self._blocked:
            result[ISSUE_PERSISTENT_BLOCKED] = {"registers": ", ".join(self._blocked.values())}
        frequent = [str(address) for address, at in self._frequent.items() if now - at < PERSISTENT_WARN_SHOW_S]
        if frequent:
            result[ISSUE_PERSISTENT_FREQUENT] = {"registers": ", ".join(frequent)}
        return result

    # --- connection ----------------------------------------------------------

    async def connect(self) -> None:
        self._written.clear()
        self._take_over = True
        self._guard.forget()
        if not await self._transport.connect():
            raise BatteryDriverError(f"Cannot connect to {self._profile.name}")

    async def close(self) -> None:
        await self._transport.close()

    async def _ensure_connected(self) -> None:
        if not self._transport.connected:
            await self.connect()

    async def read_device_info(self) -> dict[str, str]:
        await self._ensure_connected()
        info: dict[str, str] = {}
        for key, register in self._profile.device_info.items():
            words = await self._transport.read(register.address, register.size, input_registers=register.input)
            value = decode(words, register.type, register.word_order) if words else None
            if value not in (None, ""):
                info[key] = str(value)
        return info

    # --- reading -------------------------------------------------------------

    async def _read(self, registers: list[Register]) -> dict[Register, list[int] | None]:
        """Read the registers, those close together in one frame (see the module docstring)."""
        words_at: dict[tuple[int, bool], int] = {}
        failed: dict[tuple[int, int, bool], list[Register]] = {}
        # The block whose read dropped the connection.
        dropped: tuple[int, int, bool] | None = None
        for block in read_blocks(registers):
            if block in self._refused_blocks:
                continue
            start, count, kind = block
            inside = [r for r in registers if r.input is kind and start <= r.address and r.address + r.size <= start + count]
            if block in self._retry_single:
                failed[block] = inside
                continue
            was_connected = self._transport.connected
            if (words := await self._transport.read(start, count, input_registers=kind)) is not None:
                words_at.update(((address, kind), word) for address, word in zip(range(start, start + count), words, strict=False))
            else:
                failed[block] = inside
                if was_connected and not self._transport.connected:
                    dropped = block
        result: dict[Register, list[int] | None] = {}
        for register in registers:
            keys = [(address, register.input) for address in range(register.address, register.address + register.size)]
            if all(key in words_at for key in keys):
                result[register] = [words_at[key] for key in keys]
            else:
                result[register] = await self._transport.read(
                    register.address, register.size, input_registers=register.input
                )
        # A block whose registers answer one by one is refused by this device.
        # A refusal may come without an answer the transport understands (an
        # exception frame of the Venus v3 over Home Assistant's backend times
        # out and drops the connection, like a device that went silent): such
        # a block is read one by one the next time and, if its registers
        # answer, asked once more; only failing again proves the refusal.
        for block, inside in failed.items():
            retried = block in self._retry_single
            self._retry_single.discard(block)
            if all(result[register] is not None for register in inside):
                start, count, kind = block
                if not retried or await self._transport.read(start, count, input_registers=kind) is None:
                    self._refused_blocks.add(block)
            elif block == dropped and not retried:
                self._retry_single.add(block)
        return result

    async def _read_writes(self, steps: list[WriteStep]) -> dict[int, float | None]:
        """Raw values of write registers by address (None: not read)."""
        registers = {step.address: Register(step.address, step.type, word_order=step.word_order) for step in steps}
        words = await self._read(list(registers.values()))
        return {
            address: decode(value, register.type, register.word_order) if (value := words[register]) else None
            for address, register in registers.items()
        }

    async def read_telemetry(self, *, complete: bool = False) -> BatteryTelemetry:
        await self._ensure_connected()
        profile = self._profile
        now = time.monotonic()
        slow_due = complete or self._slow_read_at is None or now - self._slow_read_at >= SLOW_READ_S
        keys = [key for key in profile.read if slow_due or key not in SLOW_READ_KEYS]
        registers = [register for key in keys for register in profile.read[key].registers]
        if profile.state is not None:
            registers.append(profile.state.register)
        words = await self._read(registers)

        values: dict[str, float] = {}
        sides: dict[str, dict[str, float | None]] = {}
        for key in keys:
            value, key_sides = _value(profile.read[key], words)
            if value is not None:
                values[key] = value
            if key_sides:
                sides[key] = key_sides
        soc = profile.read["soc_pct"]
        if "soc_pct" not in values and soc.fallback is not None and any(w is not None for w in words.values()):
            register = soc.fallback
            if (part := await self._transport.read(register.address, register.size, input_registers=register.input)):
                values["soc_pct"] = _scaled(part, register)
        state = None
        if profile.state is not None and (part := words.get(profile.state.register)) is not None:
            raw = decode(part, profile.state.register.type, profile.state.register.word_order)
            name = profile.state.values.get(raw)  # type: ignore[call-overload]
            state = STATES[name] if name else "unknown"
        if not values and state is None:
            # Nothing answered: drop the connection so the next poll reconnects.
            await self._transport.close()
            raise BatteryDriverError(f"{profile.name} did not answer")
        if slow_due:
            self._slow = {key: value for key, value in values.items() if key in SLOW_READ_KEYS}
            self._slow_read_at = now
        else:
            values.update(self._slow)
        self._last = self._telemetry(values, sides, state)
        return self._last

    def _telemetry(
        self, values: dict[str, float], sides: dict[str, dict[str, float | None]], state: str | None
    ) -> BatteryTelemetry:
        """Telemetry of the values read (powers with the sign of SLEMS) and the sides of pairs."""
        extra: dict[str, Any] = {
            extra: round(values[key], 3) for key, extra in READ_KEYS.items() if extra and key in values
        }
        if state is not None:
            extra[STATE_EXTRA_KEY] = state
        power = values.get("battery_power_w")
        ac_power = values.get("ac_power_w")
        pv = output = grid_input = None
        if self._own_pv is not None:
            pv = max(0.0, values.get("pv_power_w", 0.0))
            if self._profile.read["ac_power_w"].sides is None:
                connection = ac_power or 0.0
                grid_input, output = max(0.0, connection), max(0.0, -connection)
            else:
                pair = sides.get("ac_power_w", {})
                output = max(0.0, pair.get("output") or 0.0)
                grid_input = max(0.0, value) if (value := pair.get("input")) is not None else None
            ac_power = pv + (grid_input or 0.0) - output
        if ac_power is not None:
            # The sensor AC power keeps the sign of the energy dashboard (+discharge).
            extra["ac_power"] = round(0 - ac_power, 3)
        return BatteryTelemetry(
            soc_pct=values.get("soc_pct"),
            power_w=power if power is not None else ac_power,
            ac_power_w=ac_power,
            pv_w=pv,
            output_w=output,
            input_w=grid_input,
            extra=extra,
        )

    # --- own PV --------------------------------------------------------------

    def set_own_pv_state(self, *, at_top: bool, curtail_w: float) -> None:
        if self._own_pv is not None:
            self._own_pv.set_state(at_top=at_top, curtail_w=curtail_w)

    def own_pv_available_w(self, pv_w: float) -> float:
        return self._own_pv.available_w(pv_w) if self._own_pv is not None else pv_w

    def own_pv_diagnostics(self) -> dict | None:
        return self._own_pv.diagnostics() if self._own_pv is not None else None

    def needs_write(self, net_power_w: int) -> bool:
        if not self._by_connection or self._own_pv is None or self._own_pv.output_written is None:
            return False
        try:
            _, output = self._ports(self._clamp(net_power_w))
        except BatteryDriverError:
            return False
        return self._own_pv.needs_write(output)

    def _clamp(self, net_power_w: int) -> int:
        caps = self._capabilities
        return max(-caps.max_discharge_power_w, min(caps.max_charge_power_w, net_power_w))

    def _ports(self, power: int) -> tuple[float, float]:
        """(input, output) at the AC connection for the battery power ``power``."""
        assert self._own_pv is not None
        last = self._last
        if last is None or last.pv_w is None or last.output_w is None:
            raise BatteryDriverError("own PV not read yet")
        output_read = last.output_w
        return self._own_pv.ports(power, last.pv_w, lambda: output_read)

    # --- writing -------------------------------------------------------------

    def _powers(self, net_power_w: int) -> tuple[float, float]:
        """(charge, discharge) in W at the reference of the set point."""
        if self._by_connection:
            grid_input, output = self._ports(self._clamp(net_power_w))
            charge, discharge = split_power(self._profile, grid_input - output)
            if self._own_pv is not None:
                self._own_pv.output_written = discharge
            return charge, discharge
        return split_power(self._profile, self._clamp(net_power_w))

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        await self._ensure_connected()
        if refresh:
            self._written.clear()
        try:
            charge, discharge = self._powers(net_power_w)
        except BatteryDriverError as err:
            _LOGGER.warning("Setting battery power to %d W failed: %s", net_power_w, err)
            return False
        steps = apply_values(self._profile, charge, discharge)
        if self._take_over and not await self._check_rest():
            return False
        ok = True
        now = time.monotonic()
        for step, raw in steps:
            words = encode(raw, step.type, step.word_order)
            if self._written.get(step.address) == words:
                continue
            if step.persistent and not self._persistent_allows(step, raw, now):
                if step.address in self._blocked:
                    ok = False
                continue
            self._write_count += 1
            if await self._transport.write(step.address, words if len(words) > 1 else words[0]):
                self._written[step.address] = words
                self._guard.written(step.address, raw, now)
            else:
                self._written.pop(step.address, None)
                ok = False
        if not ok:
            _LOGGER.warning("Setting battery power to %d W failed", net_power_w)
            self._take_over = True
            return False
        if refresh and self._profile.control.confirmed and not await self._confirm(steps, charge, discharge):
            # Written again completely (and checked first) with the next command.
            self._written.clear()
            self._take_over = True
            return False
        return True

    def _persistent_allows(self, step: WriteStep, raw: float, now: float) -> bool:
        decision = self._guard.decide(step.address, raw, now)
        if decision == PersistentGuard.SKIP:
            return False
        if decision == PersistentGuard.BLOCKED:
            if step.address not in self._blocked:
                _LOGGER.error(
                    "%s: persistent register %d changed more than %d times in 24 h, not written",
                    self._profile.name, step.address, step.max_changes_per_day,
                )
            self._blocked[step.address] = f"{step.address} ({step.max_changes_per_day}/24 h)"
            return False
        self._blocked.pop(step.address, None)
        if decision == PersistentGuard.WARN:
            _LOGGER.warning(
                "%s: persistent register %d changed again within a minute (flash wear)",
                self._profile.name, step.address,
            )
            self._frequent[step.address] = now
        return True

    async def _check_rest(self) -> bool:
        """Take-over: every write register within its range at rest."""
        values = await self._read_writes(list(self._profile.control.apply))
        if all(value is None for value in values.values()):
            # Nothing answered: a connection problem (reported as not
            # responding), not a register outside its range.
            await self._transport.close()
            _LOGGER.warning("%s: not written, the registers to write could not be read", self._profile.name)
            return False
        violations = rest_violations(self._profile, values)
        self._outside_rest = {v.address: _rest_text(v.step, v.value) for v in violations}
        if violations:
            _LOGGER.warning(
                "%s: not written, registers outside their range at rest: %s",
                self._profile.name, ", ".join(self._outside_rest.values()),
            )
            return False
        self._take_over = False
        return True

    async def _confirm(self, steps: list[tuple[WriteStep, float]], charge: float, discharge: float) -> bool:
        """Read the steps with ``confirm`` back after a complete write."""
        confirm = self._profile.control.confirm
        checked = [(step, raw) for step, raw in steps if step.confirm]
        await asyncio.sleep(confirm.delay_s)
        values = await self._read_writes([step for step, _ in checked])
        tolerance_w = max(confirm.tolerance_w, confirm.tolerance_pct / 100 * max(charge, discharge))
        wrong = []
        for step, raw in checked:
            value = values[step.address]
            power = step.value in (PLACEHOLDER_CHARGE, PLACEHOLDER_DISCHARGE, PLACEHOLDER_POWER)
            tolerance = tolerance_w / step.scale if power else 0
            if value is None or abs(value - raw) > tolerance or not step.rests_at(value):
                wrong.append(f"{step.address} = {value} (written {raw})")
        self._outside_rest = {
            step.address: _rest_text(step, value)
            for step, _ in checked
            if (value := values[step.address]) is not None and not step.rests_at(value)
        }
        if wrong:
            _LOGGER.warning("%s: set point not confirmed: %s", self._profile.name, ", ".join(wrong))
        return not wrong

    async def release_control(self) -> None:
        self._written.clear()
        self._take_over = True
        await self._ensure_connected()
        steps = release_values(self._profile, self._release_state)
        now = time.monotonic()
        failed = []
        for step, raw in steps:
            if step.persistent and not self._persistent_allows(step, raw, now):
                if step.address in self._blocked:
                    failed.append(step.address)
                continue
            words = encode(raw, step.type, step.word_order)
            self._write_count += 1
            if await self._transport.write(step.address, words if len(words) > 1 else words[0]):
                self._guard.written(step.address, raw, now)
            else:
                failed.append(step.address)
        if failed:
            raise BatteryDriverError(f"release: writing {failed} failed")
        if self._own_pv is not None:
            self._own_pv.released()
        checked = [(step, raw) for step, raw in steps if step.confirm]
        if not checked:
            return
        values = await self._read_writes([step for step, _ in checked])
        wrong = [
            f"{step.address} = {values[step.address]}"
            for step, raw in checked
            if values[step.address] is None or values[step.address] != raw
        ]
        if wrong:
            raise BatteryDriverError(f"release not confirmed: {', '.join(wrong)}")


def _signed(read: ReadValue, value: float) -> float:
    """A power read with the sign of SLEMS (+charge, +input)."""
    # 0 - x instead of -x: no -0.0 for a power of 0.
    return 0 - value if read.positive in NEGATIVE_DIRECTIONS else value


def _sum(registers: tuple[Register, ...], words: dict[Register, list[int] | None]) -> float | None:
    """The sum of registers read (None if one was not read)."""
    parts = [words[register] for register in registers]
    if any(part is None for part in parts):
        return None
    # Rounded once more: a sum of registers (e.g. MPPT inputs) adds float errors again.
    return round(sum(_scaled(part, register) for part, register in zip(parts, registers, strict=True)), 3)  # type: ignore[arg-type]


def _value(
    read: ReadValue, words: dict[Register, list[int] | None]
) -> tuple[float | None, dict[str, float | None]]:
    """The value of a read key (a power with the sign of SLEMS) and, of a pair, the value of
    each side (None: not read)."""
    if read.sides is None:
        total = _sum(read.registers, words)
        return (None if total is None else _signed(read, total)), {}
    sides = {side: _sum(registers, words) for side, registers in read.sides.items()}
    if any(value is None for value in sides.values()):
        return None, sides
    return round(sum(0 - v if side in NEGATIVE_DIRECTIONS else v for side, v in sides.items()), 3), sides  # type: ignore[operator]


def _scaled(words: list[int], register: Register) -> float:
    """A read value with its scale, rounded like the Venus driver (a scale of
    0.1 gives 16.900000000000002); without a scale an integer stays one."""
    value = decode(words, register.type, register.word_order)
    if register.scale == 1:
        return value  # type: ignore[return-value]
    return round(value * register.scale, 3)  # type: ignore[operator]


def _rest_text(step: WriteStep, value: float | None) -> str:
    if step.at_rest_values is not None:
        allowed = "/".join(_raw_text(v) for v in step.at_rest_values)
    elif step.at_rest is not None:
        allowed = f"{_raw_text(step.at_rest[0])}–{_raw_text(step.at_rest[1])}"
    else:
        allowed = "–"
    return f"{step.address} = {'?' if value is None else _raw_text(value)} ({allowed})"


def _raw_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
