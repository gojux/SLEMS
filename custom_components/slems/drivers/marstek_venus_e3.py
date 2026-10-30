"""Driver for the Marstek Venus E 3.0 over Modbus TCP.

Register map and control sequence are based on the Omnibattery project
(https://github.com/ffunes/Omnibattery, GPL-3.0).

Control works by switching the battery into RS485 (external) control mode and
then writing a force mode (charge / discharge / none) plus a charge and a
discharge set point.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from ..const import ReleaseState
from .base import (
    BatteryCapabilities,
    BatteryDriver,
    BatteryDriverError,
    BatteryTelemetry,
)
from .modbus_client import ModbusTcpLink, decode_registers, register_count

_LOGGER = logging.getLogger(__name__)

# Timing required by the v3 firmware.
MESSAGE_WAIT_S = 0.15
TIMEOUT_S = 3.0

# Hardware ceiling of the set point registers.
HARDWARE_MAX_POWER_W = 2500

# Control registers.
REG_RS485_CONTROL = 42000
REG_FORCE_MODE = 42010
REG_SET_CHARGE_POWER = 42020
REG_SET_DISCHARGE_POWER = 42021
# The telemetry reads these control registers back with every poll; a value
# confirmed there within VERIFIED_MAX_AGE_S is not written again by a
# keep-alive, a deviating one is written again with the next command.
VERIFIED_MAX_AGE_S = 30.0

# Time from the first register write until the grid meter shows 60 % of a
# command out of standby. Measured on a Venus E 3.0 (2026-10-12): the inverter
# does nothing for about 2 s, independent of the power and of how long it was
# idle (10 s or 90 s), then ramps up within about 1 s; other steps show up
# after about 0.9 s.
START_DELAY_S = 2.7
# Least extra time over a step of the same battery (2026-10-13, three runs:
# starts 2.5-2.9 s, reversals 1.6-2.1 s, steps of 50-1000 W 0.7-1.1 s at the
# meter, also with set points changing every 0.5 s meanwhile).
START_EXTRA_S = 1.6
REVERSAL_EXTRA_S = 0.6
# Earliest time the grid meter can show a step (steps 0.7-1.1 s, meter
# reports about every 0.5 s); a measurement whose grid moved before was moved
# by something else. Used until the battery's probe step measured it (see response_probe).
EARLIEST_RESPONSE_S = 0.45
# A Venus refuses connections for about 0.5 s after the last one closed
# (measured 2026-10-23), e.g. right after the battery search read it: a probe
# tries again once after this time.
PROBE_RETRY_S = 1.0

RS485_ENABLE = 0x55AA
RS485_DISABLE = 0x55BB

FORCE_NONE = 0
FORCE_CHARGE = 1
FORCE_DISCHARGE = 2


@dataclass(frozen=True)
class RegisterSpec:
    """A single telemetry register."""

    key: str
    address: int
    data_type: str
    scale: float = 1.0


# Static device information: key -> (register, data type, register count).
DEVICE_INFO_REGISTERS: dict[str, tuple[int, str, int]] = {
    "device_name": (31000, "char", 10),
    "ems_version": (30200, "uint16", 1),
    "vms_version": (30202, "uint16", 1),
    "bms_version": (30204, "uint16", 1),
    "comm_module_firmware": (30350, "char", 6),
    "mac_address": (30304, "char", 6),
}

# Read when 34002 does not answer.
SOC_FALLBACK = RegisterSpec("battery_soc", 37005, "uint16")

TELEMETRY_REGISTERS: tuple[RegisterSpec, ...] = (
    # 0.1 % resolution; 37005 has the same value in whole percent.
    RegisterSpec("battery_soc", 34002, "uint16", 0.1),
    # +charge / -discharge.
    RegisterSpec("battery_power", 30001, "int16"),
    # AC side power, opposite sign: +discharge / -charge.
    RegisterSpec("ac_power", 30006, "int16"),
    RegisterSpec("battery_voltage", 30100, "uint16", 0.01),
    RegisterSpec("internal_temperature", 35000, "int16", 0.1),
    RegisterSpec("max_cell_voltage", 37007, "int16", 0.001),
    RegisterSpec("min_cell_voltage", 37008, "int16", 0.001),
    RegisterSpec("inverter_state", 35100, "uint16"),
    RegisterSpec("cycle_count", 34003, "uint16"),
    RegisterSpec("total_charging_energy", 33000, "uint32", 0.01),
    RegisterSpec("total_discharging_energy", 33002, "int32", 0.01),
    RegisterSpec("rs485_control", REG_RS485_CONTROL, "uint16"),
    RegisterSpec("force_mode", REG_FORCE_MODE, "uint16"),
)

# Adjacent telemetry registers read in one frame (start, count). Measured on
# a Venus E 3.0 (2026-10-09): a block answers as fast as a single register;
# ranges with unassigned registers (e.g. 42000-42010) are refused.
TELEMETRY_BLOCKS: tuple[tuple[int, int], ...] = ((30001, 6), (34002, 2), (37007, 2), (33000, 4))

# Slowly changing values: read at most every SLOW_READ_S (each read costs
# 150 ms on the v3 firmware). The cell voltages stay in every read, the top
# taper and the cell balancing follow them.
SLOW_KEYS = frozenset(
    {"battery_voltage", "internal_temperature", "cycle_count", "total_charging_energy", "total_discharging_energy"}
)
SLOW_READ_S = 60.0

INVERTER_STATES: dict[int, str] = {
    0: "sleep",
    1: "standby",
    2: "charge",
    3: "discharge",
    4: "backup",
    5: "ota_upgrade",
    6: "bypass",
}


class MarstekVenusE3Driver(BatteryDriver):
    """Modbus TCP driver for one Marstek Venus E 3.0."""

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        *,
        capacity_wh: float,
        max_charge_power_w: int,
        max_discharge_power_w: int,
        release_state: ReleaseState = ReleaseState.AUTO,
        link: ModbusTcpLink | None = None,
    ) -> None:
        self._release_state = release_state
        self._link = link or ModbusTcpLink(
            host,
            port,
            unit_id,
            message_wait_s=MESSAGE_WAIT_S,
            timeout_s=TIMEOUT_S,
            patch_v3_frames=True,
        )
        # Last value written per control register; unchanged values are not
        # written again (each write costs 150 ms on the v3 firmware).
        self._written: dict[int, int] = {}
        # When each control register was last written and last confirmed by
        # the telemetry (monotonic).
        self._written_at: dict[int, float] = {}
        self._verified_at: dict[int, float] = {}
        # Last slowly changing values and when they were read (monotonic).
        self._slow: dict[str, float] = {}
        self._slow_read_at: float | None = None
        # Blocks this firmware refuses (their registers answered one by one).
        self._refused_blocks: set[tuple[int, int]] = set()
        self._capabilities = BatteryCapabilities(
            capacity_wh=capacity_wh,
            max_charge_power_w=min(max_charge_power_w, HARDWARE_MAX_POWER_W),
            max_discharge_power_w=min(max_discharge_power_w, HARDWARE_MAX_POWER_W),
            controllable=True,
        )

    @property
    def capabilities(self) -> BatteryCapabilities:
        return self._capabilities

    @property
    def model_name(self) -> str:
        return "Marstek Venus E 3.0"

    @property
    def extra_telemetry_keys(self) -> frozenset[str]:
        return frozenset(
            spec.key
            for spec in TELEMETRY_REGISTERS
            if spec.key not in ("battery_soc", "battery_power")
        )

    @property
    def release_state(self) -> ReleaseState:
        return self._release_state

    @property
    def has_connection(self) -> bool:
        return True

    @property
    def start_delay_s(self) -> float | None:
        return START_DELAY_S

    @property
    def start_extra_s(self) -> float:
        return START_EXTRA_S

    @property
    def reversal_extra_s(self) -> float:
        return REVERSAL_EXTRA_S

    @property
    def earliest_response_s(self) -> float | None:
        return EARLIEST_RESPONSE_S

    async def read_device_info(self) -> dict[str, str]:
        await self._ensure_connected()
        info: dict[str, str] = {}
        for key, (address, data_type, count) in DEVICE_INFO_REGISTERS.items():
            words = await self._link.read(address, count)
            value = decode_registers(words, data_type) if words else None
            if value not in (None, ""):
                info[key] = str(value)
        return info

    async def connect(self) -> None:
        self._written.clear()
        self._verified_at.clear()
        if not await self._link.connect():
            raise BatteryDriverError("Cannot connect to Marstek Venus E 3.0")

    async def close(self) -> None:
        await self._link.close()

    async def _ensure_connected(self) -> None:
        if not self._link.connected:
            await self.connect()

    async def read_telemetry(self, *, complete: bool = False) -> BatteryTelemetry:
        await self._ensure_connected()
        now = time.monotonic()
        slow_due = complete or self._slow_read_at is None or now - self._slow_read_at >= SLOW_READ_S
        values: dict[str, float] = {}
        specs = [spec for spec in TELEMETRY_REGISTERS if slow_due or spec.key not in SLOW_KEYS]
        for spec, words in (await self._read_registers(specs)).items():
            raw = decode_registers(words, spec.data_type) if words else None
            if raw is not None:
                values[spec.key] = round(raw * spec.scale, 3) if spec.scale != 1.0 else raw
        if "battery_soc" not in values and values:
            words = await self._link.read(SOC_FALLBACK.address, register_count(SOC_FALLBACK.data_type))
            raw = decode_registers(words, SOC_FALLBACK.data_type) if words else None
            if raw is not None:
                values["battery_soc"] = raw
        if not values:
            # Nothing answered: drop the socket so the next poll reconnects.
            await self._link.close()
            raise BatteryDriverError("Marstek Venus E 3.0 did not answer")
        self._check_control(values, now)
        if slow_due:
            self._slow = {key: value for key, value in values.items() if key in SLOW_KEYS}
            self._slow_read_at = now
        else:
            values.update(self._slow)

        extra = {
            key: value
            for key, value in values.items()
            if key not in ("battery_soc", "battery_power")
        }
        if "inverter_state" in values:
            extra["inverter_state"] = INVERTER_STATES.get(
                int(values["inverter_state"]), "unknown"
            )
        if "rs485_control" in values:
            extra["rs485_control"] = values["rs485_control"] == RS485_ENABLE
        ac_power = values.get("ac_power")
        return BatteryTelemetry(
            soc_pct=values.get("battery_soc"),
            power_w=values.get("battery_power"),
            ac_power_w=-ac_power if ac_power is not None else None,
            extra=extra,
        )

    async def _read_registers(self, specs: list[RegisterSpec]) -> dict[RegisterSpec, list[int] | None]:
        """Read the registers, adjacent ones of a block in one frame (each frame costs 150 ms)."""
        words_at: dict[int, int] = {}
        failed: dict[tuple[int, int], list[RegisterSpec]] = {}
        for block in TELEMETRY_BLOCKS:
            if block in self._refused_blocks:
                continue
            start, count = block
            inside = [
                spec
                for spec in specs
                if start <= spec.address and spec.address + register_count(spec.data_type) <= start + count
            ]
            if len(inside) < 2:
                continue
            first = min(spec.address for spec in inside)
            end = max(spec.address + register_count(spec.data_type) for spec in inside)
            if (words := await self._link.read(first, end - first)) is not None:
                words_at.update(zip(range(first, end), words, strict=False))
            else:
                failed[block] = inside
        result: dict[RegisterSpec, list[int] | None] = {}
        for spec in specs:
            addresses = range(spec.address, spec.address + register_count(spec.data_type))
            if all(address in words_at for address in addresses):
                result[spec] = [words_at[address] for address in addresses]
            else:
                result[spec] = await self._link.read(spec.address, len(addresses))
        # A block whose registers answer one by one is refused by this firmware.
        for block, inside in failed.items():
            if all(result[spec] is not None for spec in inside):
                self._refused_blocks.add(block)
        return result

    def _check_control(self, values: dict[str, float], read_start: float) -> None:
        """Compare the control registers read by the telemetry with the written values.

        Only registers written before the read started count; a deviating one
        (e.g. RS485 control lost after a BMS cut-off) is written again with the
        next command (see ``needs_write``).
        """
        now = time.monotonic()
        for register, key in ((REG_RS485_CONTROL, "rs485_control"), (REG_FORCE_MODE, "force_mode")):
            expected = self._written.get(register)
            read = values.get(key)
            if expected is None or read is None or self._written_at.get(register, now) >= read_start:
                continue
            if int(read) == expected:
                self._verified_at[register] = now
                continue
            _LOGGER.warning(
                "Control register %d reads %#x instead of %#x, written again with the next command",
                register, int(read), expected,
            )
            self._written.pop(register, None)
            self._verified_at.pop(register, None)

    def _control_values(self, net_power_w: int) -> tuple[int, list[tuple[int, int]]]:
        """Force mode and register values for a net power, in write order.

        RS485 control can drop after a reconnect or a BMS cut-off, so it is
        part of every complete write. The set points come before the force
        mode so a new direction starts with valid values.
        """
        if net_power_w > 0:
            charge = min(net_power_w, self._capabilities.max_charge_power_w)
            discharge = 0
            force_mode = FORCE_CHARGE
        elif net_power_w < 0:
            charge = 0
            discharge = min(-net_power_w, self._capabilities.max_discharge_power_w)
            force_mode = FORCE_DISCHARGE
        else:
            charge = discharge = 0
            force_mode = FORCE_NONE
        return force_mode, [
            (REG_RS485_CONTROL, RS485_ENABLE),
            (REG_SET_DISCHARGE_POWER, discharge),
            (REG_SET_CHARGE_POWER, charge),
            (REG_FORCE_MODE, force_mode),
        ]

    def _keepalive_write(self, register: int, force_mode: int, now: float) -> bool:
        """Whether a keep-alive writes a register whose value did not change.

        RS485 control and force mode only when the telemetry did not confirm
        them lately; the set point only of the active direction (in standby
        none: without a force mode they have no effect).
        """
        if register in (REG_RS485_CONTROL, REG_FORCE_MODE):
            verified = self._verified_at.get(register)
            return verified is None or now - verified > VERIFIED_MAX_AGE_S
        if force_mode == FORCE_CHARGE:
            return register == REG_SET_CHARGE_POWER
        if force_mode == FORCE_DISCHARGE:
            return register == REG_SET_DISCHARGE_POWER
        return False

    def needs_write(self, net_power_w: int) -> bool:
        _, values = self._control_values(net_power_w)
        return any(self._written.get(register) != value for register, value in values)

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        await self._ensure_connected()
        force_mode, values = self._control_values(net_power_w)
        ok = True
        for register, value in values:
            now = time.monotonic()
            if self._written.get(register) == value and not (
                refresh and self._keepalive_write(register, force_mode, now)
            ):
                continue
            if await self._link.write(register, value):
                self._written[register] = value
                self._written_at[register] = now
                self._verified_at.pop(register, None)
            else:
                self._written.pop(register, None)
                ok = False
        if not ok:
            _LOGGER.warning("Setting battery power to %d W failed", net_power_w)
        return ok

    async def release_control(self) -> None:
        self._written.clear()
        self._verified_at.clear()
        await self._ensure_connected()
        writes = [
            (REG_SET_DISCHARGE_POWER, 0),
            (REG_SET_CHARGE_POWER, 0),
            (REG_FORCE_MODE, FORCE_NONE),
        ]
        if self._release_state is ReleaseState.AUTO:
            writes.append((REG_RS485_CONTROL, RS485_DISABLE))
        failed = [register for register, value in writes if not await self._link.write(register, value)]
        if failed:
            raise BatteryDriverError(f"release: writing {failed} failed")
        # Read back: only a battery out of forced mode is released.
        mode = await self._link.read(REG_FORCE_MODE, 1)
        control = await self._link.read(REG_RS485_CONTROL, 1)
        if mode is None or control is None:
            raise BatteryDriverError("release: control registers could not be read back")
        if mode[0] != FORCE_NONE or (
            self._release_state is ReleaseState.AUTO and control[0] != RS485_DISABLE
        ):
            raise BatteryDriverError(
                f"release not confirmed: force mode {mode[0]}, RS485 {control[0]:#x}"
            )

    @classmethod
    async def probe(cls, host: str, port: int, unit_id: int) -> bool:
        """Return True if a battery answers the SoC register at host:port."""
        return await cls.read_soc(host, port, unit_id) is not None

    @classmethod
    async def read_soc(cls, host: str, port: int, unit_id: int) -> float | None:
        """State of charge (%) read once from host:port, None without an answer."""
        spec = TELEMETRY_REGISTERS[0]
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(PROBE_RETRY_S)
            link = ModbusTcpLink(
                host,
                port,
                unit_id,
                message_wait_s=MESSAGE_WAIT_S,
                timeout_s=TIMEOUT_S,
                patch_v3_frames=True,
            )
            try:
                if not await link.connect():
                    continue
                words = await link.read(spec.address, 1)
                return None if words is None else words[0] * spec.scale
            finally:
                await link.close()
        return None
