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
# Read-back after a complete write: settle time and allowed deviation.
CONFIRM_DELAY_S = 0.2
CONFIRM_TOLERANCE_W = 100
CONFIRM_TOLERANCE_RATIO = 0.1

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
        link: ModbusTcpLink | None = None,
    ) -> None:
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

    async def connect(self) -> None:
        self._written.clear()
        if not await self._link.connect():
            raise BatteryDriverError("Cannot connect to Marstek Venus E 3.0")

    async def close(self) -> None:
        await self._link.close()

    async def _ensure_connected(self) -> None:
        if not self._link.connected:
            await self.connect()

    async def read_telemetry(self) -> BatteryTelemetry:
        await self._ensure_connected()
        values: dict[str, float] = {}
        for spec in TELEMETRY_REGISTERS:
            words = await self._link.read(spec.address, register_count(spec.data_type))
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

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        await self._ensure_connected()
        if refresh:
            self._written.clear()
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

        # RS485 control can drop after a reconnect or a BMS cut-off; it is
        # enabled again with every refresh. The set points come before the
        # force mode so a new direction starts with valid values.
        ok = True
        for register, value in (
            (REG_RS485_CONTROL, RS485_ENABLE),
            (REG_SET_DISCHARGE_POWER, discharge),
            (REG_SET_CHARGE_POWER, charge),
            (REG_FORCE_MODE, force_mode),
        ):
            if self._written.get(register) == value:
                continue
            if await self._link.write(register, value):
                self._written[register] = value
            else:
                self._written.pop(register, None)
                ok = False
        if not ok:
            _LOGGER.warning("Setting battery power to %d W failed", net_power_w)
            return False
        if refresh and not await self._confirm(force_mode, charge, discharge):
            # Written again completely with the next command.
            self._written.clear()
            return False
        return True

    async def _confirm(self, force_mode: int, charge: int, discharge: int) -> bool:
        """Read the control registers back after a complete write (as in Omnibattery)."""
        await asyncio.sleep(CONFIRM_DELAY_S)
        control = await self._link.read(REG_RS485_CONTROL, 1)
        mode = await self._link.read(REG_FORCE_MODE, 1)
        powers = await self._link.read(REG_SET_CHARGE_POWER, 2)
        if control is None or mode is None or powers is None:
            _LOGGER.warning("Battery set point could not be read back")
            return False
        tolerance = max(CONFIRM_TOLERANCE_W, CONFIRM_TOLERANCE_RATIO * max(charge, discharge))
        confirmed = (
            control[0] == RS485_ENABLE
            and mode[0] == force_mode
            and abs(powers[0] - charge) <= tolerance
            and abs(powers[1] - discharge) <= tolerance
        )
        if not confirmed:
            _LOGGER.warning(
                "Battery set point not confirmed: RS485 %#x, force mode %d, charge %d W, "
                "discharge %d W (written: force mode %d, charge %d W, discharge %d W)",
                control[0], mode[0], powers[0], powers[1], force_mode, charge, discharge,
            )
        return confirmed

    async def release_control(self) -> None:
        self._written.clear()
        if not self._link.connected:
            return
        await self._link.write(REG_SET_DISCHARGE_POWER, 0)
        await self._link.write(REG_SET_CHARGE_POWER, 0)
        await self._link.write(REG_FORCE_MODE, FORCE_NONE)
        await self._link.write(REG_RS485_CONTROL, RS485_DISABLE)

    @classmethod
    async def probe(cls, host: str, port: int, unit_id: int) -> bool:
        """Return True if a battery answers the SoC register at host:port."""
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
                return False
            return await link.read(TELEMETRY_REGISTERS[0].address, 1) is not None
        finally:
            await link.close()
