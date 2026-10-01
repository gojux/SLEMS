"""Battery backed by existing Home Assistant entities.

Read-only (e.g. while another integration such as Omnibattery still owns the
Modbus connection, so SLEMS can observe the battery in simulation mode), or
controlled through the entities of the battery's own integration:

* ``setpoint``: one number entity with the signed power (+charge / -discharge,
  optionally inverted),
* ``split``: one number for charging and one for discharging, optionally a
  mode select (charge / discharge / standby / automatic),
* ``script``: a script receives the signed power as variable ``power_w``.

An optional remote control entity (switch or select) is switched on before the
first set point and off again when the battery is released to its automatic.

Every service call waits until the battery's integration has finished it (at
most ``SERVICE_TIMEOUT_S``): a rejected or hanging call is a communication
error, and with ``split`` the direction that stops is really stopped before the
other one starts. A script is called directly (``script.<name>``) so it runs
to its end; it should finish quickly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_UNAVAILABLE,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.util.unit_conversion import TemperatureConverter

from ..const import (
    CONF_BATTERY_CONTROL,
    CONF_BATTERY_TEMPERATURE_ENTITY,
    CONF_CAPACITY_WH,
    CONF_CHARGE_ENTITY,
    CONF_CHARGED_ENERGY_ENTITY,
    CONF_DISCHARGE_ENTITY,
    CONF_DISCHARGED_ENERGY_ENTITY,
    CONF_KEEPALIVE_S,
    CONF_MAX_CELL_VOLTAGE_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MIN_CELL_VOLTAGE_ENTITY,
    CONF_MIN_COMMAND_INTERVAL_S,
    CONF_MODE_AUTO,
    CONF_MODE_CHARGE,
    CONF_MODE_DISCHARGE,
    CONF_MODE_ENTITY,
    CONF_MODE_STANDBY,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_POWER_SCRIPT,
    CONF_RELEASE_SCRIPT,
    CONF_RELEASE_STATE,
    CONF_REMOTE_ENTITY,
    CONF_REMOTE_OFF,
    CONF_REMOTE_ON,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_INVERTED,
    CONF_SOC_ENTITY,
    BatteryControl,
    ReleaseState,
)
from ..util import (
    ServiceCallError,
    async_call_service,
    clamp_to_entity,
    state_as_float,
    state_as_kwh,
    state_as_watts,
)
from .base import BatteryCapabilities, BatteryDriver, BatteryDriverError, BatteryTelemetry

_LOGGER = logging.getLogger(__name__)

# Extra telemetry key -> subentry key of the entity it is read from.
EXTRA_ENTITIES: dict[str, str] = {
    "internal_temperature": CONF_BATTERY_TEMPERATURE_ENTITY,
    "max_cell_voltage": CONF_MAX_CELL_VOLTAGE_ENTITY,
    "min_cell_voltage": CONF_MIN_CELL_VOLTAGE_ENTITY,
    "total_charging_energy": CONF_CHARGED_ENERGY_ENTITY,
    "total_discharging_energy": CONF_DISCHARGED_ENERGY_ENTITY,
}


@dataclass(frozen=True)
class EntityBatteryConfig:
    """Entities and options of a battery backed by Home Assistant entities."""

    soc_entity_id: str
    power_entity_id: str | None = None
    power_inverted: bool = False
    capacity_wh: float = 0.0
    max_charge_power_w: int = 0
    max_discharge_power_w: int = 0
    control: BatteryControl = BatteryControl.NONE
    setpoint_entity_id: str | None = None
    setpoint_inverted: bool = False
    charge_entity_id: str | None = None
    discharge_entity_id: str | None = None
    mode_entity_id: str | None = None
    mode_charge: str | None = None
    mode_discharge: str | None = None
    mode_standby: str | None = None
    mode_auto: str | None = None
    remote_entity_id: str | None = None
    remote_on: str | None = None
    remote_off: str | None = None
    power_script: str | None = None
    release_script: str | None = None
    release_state: ReleaseState = ReleaseState.AUTO
    min_command_interval_s: float = 0.0
    keepalive_s: float | None = None
    extra_entities: Mapping[str, str] | None = None

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> EntityBatteryConfig:
        keepalive = data.get(CONF_KEEPALIVE_S)
        return cls(
            soc_entity_id=data[CONF_SOC_ENTITY],
            power_entity_id=data.get(CONF_POWER_ENTITY),
            power_inverted=data.get(CONF_POWER_INVERTED, False),
            capacity_wh=data[CONF_CAPACITY_WH],
            max_charge_power_w=data[CONF_MAX_CHARGE_POWER_W],
            max_discharge_power_w=data[CONF_MAX_DISCHARGE_POWER_W],
            control=BatteryControl(data.get(CONF_BATTERY_CONTROL, BatteryControl.NONE)),
            setpoint_entity_id=data.get(CONF_SETPOINT_ENTITY),
            setpoint_inverted=data.get(CONF_SETPOINT_INVERTED, False),
            charge_entity_id=data.get(CONF_CHARGE_ENTITY),
            discharge_entity_id=data.get(CONF_DISCHARGE_ENTITY),
            mode_entity_id=data.get(CONF_MODE_ENTITY),
            mode_charge=data.get(CONF_MODE_CHARGE),
            mode_discharge=data.get(CONF_MODE_DISCHARGE),
            mode_standby=data.get(CONF_MODE_STANDBY),
            mode_auto=data.get(CONF_MODE_AUTO),
            remote_entity_id=data.get(CONF_REMOTE_ENTITY),
            remote_on=data.get(CONF_REMOTE_ON),
            remote_off=data.get(CONF_REMOTE_OFF),
            power_script=data.get(CONF_POWER_SCRIPT),
            release_script=data.get(CONF_RELEASE_SCRIPT),
            release_state=ReleaseState(data.get(CONF_RELEASE_STATE, ReleaseState.AUTO)),
            min_command_interval_s=float(data.get(CONF_MIN_COMMAND_INTERVAL_S) or 0.0),
            keepalive_s=float(keepalive) if keepalive else None,
            extra_entities={
                key: entity_id
                for key, conf in EXTRA_ENTITIES.items()
                if (entity_id := data.get(conf))
            },
        )

    @property
    def can_release_to_auto(self) -> bool:
        """Whether the battery can be handed back to its own logic."""
        return bool(
            (self.control is BatteryControl.SCRIPT and self.release_script)
            or (self.remote_entity_id and (self.remote_off or _domain(self.remote_entity_id) != "select"))
            or (self.mode_entity_id and self.mode_auto)
        )


class HomeAssistantEntityDriver(BatteryDriver):
    """Battery whose state comes from, and whose set points go to, HA entities."""

    def __init__(self, hass: HomeAssistant, config: EntityBatteryConfig) -> None:
        self._hass = hass
        self._config = config
        self._capabilities = BatteryCapabilities(
            capacity_wh=config.capacity_wh,
            max_charge_power_w=config.max_charge_power_w,
            max_discharge_power_w=config.max_discharge_power_w,
            controllable=config.control is not BatteryControl.NONE,
        )
        # Last value written per entity; unchanged values are not written again.
        self._written: dict[str, Any] = {}

    @property
    def capabilities(self) -> BatteryCapabilities:
        return self._capabilities

    @property
    def model_name(self) -> str:
        if self._capabilities.controllable:
            return "Home Assistant entities"
        return "Home Assistant entities (read-only)"

    @property
    def extra_telemetry_keys(self) -> frozenset[str]:
        return frozenset(self._config.extra_entities or {})

    @property
    def min_command_interval_s(self) -> float:
        return self._config.min_command_interval_s

    @property
    def keepalive_s(self) -> float | None:
        return self._config.keepalive_s

    async def connect(self) -> None:
        """Nothing to connect."""

    async def close(self) -> None:
        """Nothing to close."""

    async def read_telemetry(self) -> BatteryTelemetry:
        config = self._config
        soc = state_as_float(self._hass.states.get(config.soc_entity_id))
        if soc is None:
            raise BatteryDriverError(f"{config.soc_entity_id} is unavailable")
        power = None
        if config.power_entity_id:
            power = state_as_watts(self._hass.states.get(config.power_entity_id))
            if power is not None and config.power_inverted:
                power = -power
        extra: dict[str, Any] = {}
        for key, entity_id in (config.extra_entities or {}).items():
            value = _extra_value(key, self._hass.states.get(entity_id))
            if value is not None:
                extra[key] = value
        return BatteryTelemetry(soc_pct=soc, power_w=power, extra=extra)

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        config = self._config
        if not self._capabilities.controllable:
            return await super().apply_power(net_power_w, refresh=refresh)
        power = max(
            -config.max_discharge_power_w, min(config.max_charge_power_w, net_power_w)
        )
        try:
            if config.control is BatteryControl.SCRIPT:
                await self._run_script(config.power_script, power)
                return True
            await self._remote(on=True, refresh=refresh)
            if config.control is BatteryControl.SETPOINT:
                value = -power if config.setpoint_inverted else power
                await self._set_watts(config.setpoint_entity_id, value, refresh)
            else:
                await self._apply_split(power, refresh)
        except BatteryDriverError as err:
            _LOGGER.warning("Setting battery power to %d W failed: %s", net_power_w, err)
            self._written.clear()
            return False
        return True

    async def _apply_split(self, power: int, refresh: bool) -> None:
        config = self._config
        charge = max(0, power)
        discharge = max(0, -power)
        # The direction that stops is set first, so both never run together.
        steps = [
            (config.discharge_entity_id, discharge),
            (config.charge_entity_id, charge),
        ]
        if power < 0:
            steps.reverse()
        for entity_id, value in steps:
            if entity_id:
                await self._set_watts(entity_id, value, refresh)
        if power > 0:
            option = config.mode_charge
        elif power < 0:
            option = config.mode_discharge
        else:
            option = config.mode_standby
        if config.mode_entity_id and option:
            await self._select(config.mode_entity_id, option, refresh)

    async def release_control(self) -> None:
        config = self._config
        self._written.clear()
        if not self._capabilities.controllable:
            return
        auto = config.release_state is ReleaseState.AUTO
        try:
            if config.control is BatteryControl.SCRIPT:
                if auto and config.release_script:
                    await self._run_script(config.release_script, 0)
                else:
                    await self._run_script(config.power_script, 0)
                return
            if config.control is BatteryControl.SETPOINT:
                await self._set_watts(config.setpoint_entity_id, 0, True)
            else:
                await self._apply_split(0, True)
                if auto and config.mode_entity_id and config.mode_auto:
                    await self._select(config.mode_entity_id, config.mode_auto, True)
            if auto:
                await self._remote(on=False, refresh=True)
        finally:
            # A failure is raised to the coordinator, which tries again.
            self._written.clear()

    # --- entity access ---------------------------------------------------------

    def _state(self, entity_id: str | None) -> State:
        """State of an entity SLEMS writes to; ``unknown`` is fine (e.g. a select never set)."""
        if not entity_id:
            raise BatteryDriverError("no entity configured")
        state = self._hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            raise BatteryDriverError(f"{entity_id} is unavailable")
        return state

    async def _call(self, entity_id: str, service: str, key: Any, data: dict[str, Any], refresh: bool) -> None:
        if not refresh and self._written.get(entity_id) == key:
            return
        try:
            await async_call_service(
                self._hass, _domain(entity_id), service, {ATTR_ENTITY_ID: entity_id, **data}
            )
        except ServiceCallError as err:
            self._written.pop(entity_id, None)
            raise BatteryDriverError(str(err)) from err
        self._written[entity_id] = key

    async def _set_watts(self, entity_id: str | None, watts: float, refresh: bool) -> None:
        state = self._state(entity_id)
        unit = state.attributes.get("unit_of_measurement")
        value = watts / 1000 if unit == UnitOfPower.KILO_WATT else watts
        value = clamp_to_entity(value, state)
        await self._call(state.entity_id, "set_value", value, {"value": value}, refresh)

    async def _select(self, entity_id: str, option: str, refresh: bool) -> None:
        state = self._state(entity_id)
        if not refresh and state.state == option:
            self._written[entity_id] = option
            return
        await self._call(entity_id, "select_option", option, {"option": option}, refresh)

    async def _remote(self, *, on: bool, refresh: bool) -> None:
        config = self._config
        entity_id = config.remote_entity_id
        if not entity_id:
            return
        if _domain(entity_id) in ("select", "input_select"):
            option = config.remote_on if on else config.remote_off
            if option:
                await self._select(entity_id, option, refresh)
            return
        state = self._state(entity_id)
        if not refresh and state.state == ("on" if on else "off"):
            self._written[entity_id] = on
            return
        await self._call(entity_id, "turn_on" if on else "turn_off", on, {}, refresh)

    async def _run_script(self, entity_id: str | None, power: int) -> None:
        """Run a script and wait until it is done (so its errors count)."""
        self._state(entity_id)
        try:
            await async_call_service(
                self._hass, "script", entity_id.split(".", 1)[1], {"power_w": power}
            )
        except ServiceCallError as err:
            raise BatteryDriverError(str(err)) from err


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _extra_value(key: str, state: State | None) -> float | None:
    """Value of an optional sensor in the unit the extra telemetry uses."""
    if key in ("total_charging_energy", "total_discharging_energy"):
        return state_as_kwh(state)
    value = state_as_float(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if key == "internal_temperature" and unit == UnitOfTemperature.FAHRENHEIT:
        return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
    if key.endswith("cell_voltage") and unit == UnitOfElectricPotential.MILLIVOLT:
        return value / 1000
    return value
