"""Config flow for SLEMS.

The main entry holds the system level measurements (grid, PV, weather). Every
battery is a config subentry, so batteries can be added, edited and removed at
any time without touching the rest of the configuration.

A Marstek Venus can be searched in the network when adding a battery (see
discovery).
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import voluptuous as vol

from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SOURCE_RECONFIGURE,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector
from modbus_connection import ModbusTcpParams

from .const import (
    CONF_MAX_CURRENT_A,
    CONF_MIN_CURRENT_A,
    CONF_PHASES,
    CONF_PHASES_ENTITY,
    CONF_START_ENTITY,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_VOLTAGE_V,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_PHASES,
    DEFAULT_VOLTAGE_V,
    WALLBOX_MIN_OFF_MINUTES,
    WALLBOX_MIN_ON_MINUTES,
    CONF_GRID_MODBUS,
    CONF_GRID_MODBUS_HOST,
    CONF_GRID_MODBUS_INTERVAL_S,
    CONF_GRID_MODBUS_INVERTED,
    CONF_GRID_MODBUS_PORT,
    CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_SIGN,
    CONF_GRID_MODBUS_UNIT_ID,
    DEFAULT_GRID_MODBUS_INTERVAL_S,
    DEFAULT_GRID_MODBUS_PORT,
    CONF_BATTERY_CONTROL,
    CONF_BATTERY_TEMPERATURE_ENTITY,
    CONF_CHARGE_ENTITY,
    CONF_CHARGED_ENERGY_ENTITY,
    CONF_DEVICE,
    CONF_DISCHARGE_ENTITY,
    CONF_DISCHARGED_ENERGY_ENTITY,
    CONF_KEEPALIVE_S,
    CONF_MAX_CELL_VOLTAGE_ENTITY,
    CONF_MIN_CELL_VOLTAGE_ENTITY,
    CONF_MIN_COMMAND_INTERVAL_S,
    CONF_MODE_AUTO,
    CONF_MODE_CHARGE,
    CONF_MODE_DISCHARGE,
    CONF_MODE_ENTITY,
    CONF_MODE_STANDBY,
    CONF_POWER_SCRIPT,
    CONF_RELEASE_SCRIPT,
    CONF_RELEASE_STATE,
    CONF_REMOTE_ENTITY,
    CONF_REMOTE_OFF,
    CONF_REMOTE_ON,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_INVERTED,
    BatteryControl,
    ReleaseState,
    CONF_BLOCK_ENTITY,
    CONF_SHOW_IN_FLOW,
    CONF_THERMOSTAT_CYCLES,
    CONF_TEMPERATURE_2_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    CONF_CAPACITY_WH,
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_EFFICIENCY_MODE,
    CONF_ENERGY_ENTITY,
    CONF_MIN_OFF_MINUTES,
    CONF_MIN_ON_MINUTES,
    CONF_ROUND_TRIP_EFFICIENCY_PCT,
    DEFAULT_ROUND_TRIP_EFFICIENCY_PCT,
    CONF_INCLUDED_IN_METER,
    CONF_MAX_POWER_W,
    CONF_MIN_POWER_W,
    CONF_NOMINAL_POWER_W,
    CONF_PRIORITY,
    DEFAULT_PRIORITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HOUSE_HISTORY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_HOST,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MODEL,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_PV_FORECAST_ENTRIES,
    CONF_PV_POWER_ENTITY,
    CONF_SKIP_CONNECTION_TEST,
    CONF_SOC_ENTITY,
    CONF_UNIT_ID,
    CONF_WEATHER_ENTITY,
    DEFAULT_MODBUS_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    SUBENTRY_TYPE_BATTERY,
    SUBENTRY_TYPE_CONSUMER,
    BatteryModel,
    ConsumerType,
    ControlMode,
    EfficiencyMode,
)
from .discovery import async_find_batteries
from .drivers.ha_entities import EntityBatteryConfig
from .drivers.marstek_venus_e3 import HARDWARE_MAX_POWER_W, MarstekVenusE3Driver
from .entity_match import (
    CAPACITY,
    EntityInfo,
    match_battery_entities,
    suggest_control,
    suggest_mode_options,
    suggest_remote_options,
)
from .grid_meter import (
    Reader,
    SignMismatchError,
    SignUndecidableError,
    SunSpecError,
    SunSpecMeter,
    detect_inversion,
    find_meters,
    read_power,
)
from .pv_forecast import async_forecast_provider_entries
from .util import state_as_kwh, state_as_watts

# Venus E 3.0 usable capacity.
DEFAULT_CAPACITY_WH = 5120

_POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)
)
_ENERGY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.ENERGY)
)
_BATTERY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.BATTERY)
)


_NUMBER_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["number", "input_number"])
)
_SELECT_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["select", "input_select"])
)
_REMOTE_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "input_boolean", "select", "input_select"])
)
_SCRIPT_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain="script"))
_TEMPERATURE_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
)
_VOLTAGE_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.VOLTAGE)
)
_SECONDS = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=600, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
    )
)

# Keys of the control types; data of the other types is dropped on saving.
_CONTROL_KEYS: dict[BatteryControl, tuple[str, ...]] = {
    BatteryControl.SETPOINT: (CONF_SETPOINT_ENTITY, CONF_SETPOINT_INVERTED),
    BatteryControl.SPLIT: (
        CONF_CHARGE_ENTITY, CONF_DISCHARGE_ENTITY, CONF_MODE_ENTITY,
        CONF_MODE_CHARGE, CONF_MODE_DISCHARGE, CONF_MODE_STANDBY, CONF_MODE_AUTO,
    ),
    BatteryControl.SCRIPT: (CONF_POWER_SCRIPT, CONF_RELEASE_SCRIPT),
}
_REMOTE_KEYS = (CONF_REMOTE_ENTITY, CONF_REMOTE_ON, CONF_REMOTE_OFF)
_CONTROLLED_KEYS = (CONF_RELEASE_STATE, CONF_MIN_COMMAND_INTERVAL_S, CONF_KEEPALIVE_S)


def _release_state_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[state.value for state in ReleaseState], translation_key=CONF_RELEASE_STATE
        )
    )


def _watts(maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=maximum,
            step=50,
            unit_of_measurement="W",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


_CAPACITY = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=100, max=100_000, step=10, unit_of_measurement="Wh",
        mode=selector.NumberSelectorMode.BOX,
    )
)


def _optional(key: str, defaults: dict[str, Any]) -> vol.Optional:
    """Optional field that can be cleared again in the UI."""
    if defaults.get(key) is not None:
        return vol.Optional(key, description={"suggested_value": defaults[key]})
    return vol.Optional(key)


async def _async_system_schema(
    hass: HomeAssistant, defaults: dict[str, Any]
) -> vol.Schema:
    """Schema for the system settings, shared by config and options flow."""
    providers = await async_forecast_provider_entries(hass)
    forecast_field: dict = {}
    if providers:
        forecast_field[_optional(CONF_PV_FORECAST_ENTRIES, defaults)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=entry.entry_id, label=f"{entry.title} ({entry.domain})"
                        )
                        for entry in providers
                    ],
                    multiple=True,
                )
            )
        )

    def optional(key: str) -> vol.Optional:
        return _optional(key, defaults)

    return vol.Schema(
        {
            vol.Required(
                CONF_GRID_POWER_ENTITY, default=defaults.get(CONF_GRID_POWER_ENTITY, vol.UNDEFINED)
            ): _POWER_SENSOR,
            vol.Required(
                CONF_GRID_POWER_INVERTED,
                default=defaults.get(CONF_GRID_POWER_INVERTED, False),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_GRID_MODBUS, default=defaults.get(CONF_GRID_MODBUS, False)
            ): selector.BooleanSelector(),
            optional(CONF_PV_POWER_ENTITY): _POWER_SENSOR,
            **forecast_field,
            optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            optional(CONF_OUTDOOR_TEMPERATURE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class=SensorDeviceClass.TEMPERATURE
                )
            ),
            optional(CONF_HOUSE_HISTORY_ENTITY): _POWER_SENSOR,
        }
    )


_GRID_MODBUS_KEYS = (
    CONF_GRID_MODBUS_HOST,
    CONF_GRID_MODBUS_PORT,
    CONF_GRID_MODBUS_UNIT_ID,
    CONF_GRID_MODBUS_INTERVAL_S,
    CONF_GRID_MODBUS_SIGN,
    CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_INVERTED,
)
GRID_MODBUS_TIMEOUT_S = 3.0
# Readings compared with the grid entity to find the sign.
SIGN_SAMPLES = 6
SIGN_SAMPLE_S = 0.5


def _with_timeout(read: Reader) -> Reader:
    async def timed(address: int, count: int) -> list[int]:
        return await asyncio.wait_for(read(address, count), GRID_MODBUS_TIMEOUT_S)

    return timed


def _meter_label(meter: SunSpecMeter, power: float | None) -> str:
    name = " ".join(part for part in (meter.manufacturer, meter.model, meter.option) if part)
    reading = f"{power:.0f} W" if power is not None else "–"
    return f"{name or 'Meter'} (SunSpec {meter.model_id}, {reading})"


class _GridModbusSteps:
    """Steps for reading the grid power over Modbus, shared by config and options flow."""

    hass: HomeAssistant
    _system: dict[str, Any]
    _meters: list[tuple[SunSpecMeter, float | None]]

    def _async_finish_system(self) -> ConfigFlowResult:
        raise NotImplementedError

    async def _async_system_done(
        self, user_input: dict[str, Any], previous: dict[str, Any]
    ) -> ConfigFlowResult:
        # The Modbus settings stay when it is switched off, as defaults for later.
        self._system = {
            **{key: previous[key] for key in _GRID_MODBUS_KEYS if key in previous},
            **user_input,
        }
        if user_input.get(CONF_GRID_MODBUS):
            return await self.async_step_grid_modbus()
        return self._async_finish_system()

    def _params(self) -> tuple[ModbusTcpParams, int]:
        data = self._system
        return (
            ModbusTcpParams(host=data[CONF_GRID_MODBUS_HOST], port=data[CONF_GRID_MODBUS_PORT]),
            data[CONF_GRID_MODBUS_UNIT_ID],
        )

    async def async_step_grid_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connection of the SunSpec meter; finds the meters in the model chain."""
        errors: dict[str, str] = {}
        placeholders = {"error": "", "modbus": "", "entity": ""}
        if user_input is not None:
            self._system |= {
                CONF_GRID_MODBUS_HOST: user_input[CONF_GRID_MODBUS_HOST].strip(),
                CONF_GRID_MODBUS_PORT: int(user_input[CONF_GRID_MODBUS_PORT]),
                CONF_GRID_MODBUS_UNIT_ID: int(user_input[CONF_GRID_MODBUS_UNIT_ID]),
                CONF_GRID_MODBUS_INTERVAL_S: float(user_input[CONF_GRID_MODBUS_INTERVAL_S]),
                CONF_GRID_MODBUS_SIGN: user_input[CONF_GRID_MODBUS_SIGN],
            }
            params, unit_id = self._params()
            try:
                async with async_get_temporary_unit(self.hass, params, unit_id) as unit:
                    read = _with_timeout(unit.read_holding_registers)
                    meters = await find_meters(read)
                    self._meters = []
                    for meter in meters:
                        try:
                            power = await read_power(read, meter.power_register)
                        except Exception:  # noqa: BLE001 - only shown as a hint
                            power = None
                        self._meters.append((meter, power))
            except SunSpecError:
                errors["base"] = "no_sunspec"
            except Exception as err:  # noqa: BLE001 - shown in the form
                errors["base"] = "grid_modbus_failed"
                placeholders["error"] = f"{type(err).__name__}: {err}"
            else:
                if not self._meters:
                    errors["base"] = "no_meter"
                elif len(self._meters) == 1:
                    self._system[CONF_GRID_MODBUS_REGISTER] = self._meters[0][0].power_register
                    return await self._async_check_sign()
                else:
                    return await self.async_step_grid_meter()
        return self._show_grid_modbus(errors, placeholders)

    def _show_grid_modbus(
        self, errors: dict[str, str], placeholders: dict[str, str]
    ) -> ConfigFlowResult:
        data = self._system
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GRID_MODBUS_HOST, default=data.get(CONF_GRID_MODBUS_HOST, vol.UNDEFINED)
                ): str,
                vol.Required(
                    CONF_GRID_MODBUS_PORT,
                    default=data.get(CONF_GRID_MODBUS_PORT, DEFAULT_GRID_MODBUS_PORT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_GRID_MODBUS_UNIT_ID, default=data.get(CONF_GRID_MODBUS_UNIT_ID, 1)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=247, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_GRID_MODBUS_INTERVAL_S,
                    default=data.get(CONF_GRID_MODBUS_INTERVAL_S, DEFAULT_GRID_MODBUS_INTERVAL_S),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.2, max=5, step=0.1, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_GRID_MODBUS_SIGN, default=data.get(CONF_GRID_MODBUS_SIGN, "auto")
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["auto", "normal", "inverted"],
                        translation_key="grid_modbus_sign",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="grid_modbus",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_grid_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choice between several meters."""
        if user_input is not None:
            self._system[CONF_GRID_MODBUS_REGISTER] = int(user_input[CONF_GRID_MODBUS_REGISTER])
            return await self._async_check_sign()
        options = [
            selector.SelectOptionDict(
                value=str(meter.power_register), label=_meter_label(meter, power)
            )
            for meter, power in self._meters
        ]
        current = str(self._system.get(CONF_GRID_MODBUS_REGISTER, options[0]["value"]))
        if current not in {option["value"] for option in options}:
            current = options[0]["value"]
        return self.async_show_form(
            step_id="grid_meter",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GRID_MODBUS_REGISTER, default=current): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    def _entity_grid(self) -> float | None:
        grid = state_as_watts(self.hass.states.get(self._system[CONF_GRID_POWER_ENTITY]))
        if grid is not None and self._system.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        return grid

    async def _async_check_sign(self) -> ConfigFlowResult:
        """Sign of the Modbus value: chosen, or found by comparing with the entity."""
        sign = self._system[CONF_GRID_MODBUS_SIGN]
        if sign != "auto":
            self._system[CONF_GRID_MODBUS_INVERTED] = sign == "inverted"
            return self._async_finish_system()
        placeholders = {"error": "", "modbus": "–", "entity": "–"}
        params, unit_id = self._params()
        register = self._system[CONF_GRID_MODBUS_REGISTER]
        pairs: list[tuple[float, float]] = []
        try:
            async with async_get_temporary_unit(self.hass, params, unit_id) as unit:
                read = _with_timeout(unit.read_holding_registers)
                for sample in range(SIGN_SAMPLES):
                    if sample:
                        await asyncio.sleep(SIGN_SAMPLE_S)
                    modbus = await read_power(read, register)
                    entity = self._entity_grid()
                    if modbus is not None and entity is not None:
                        pairs.append((modbus, entity))
            if pairs:
                placeholders["modbus"] = f"{pairs[-1][0]:.0f}"
                placeholders["entity"] = f"{pairs[-1][1]:.0f}"
            self._system[CONF_GRID_MODBUS_INVERTED] = detect_inversion(pairs)
        except SignUndecidableError:
            return self._show_grid_modbus({"base": "sign_undecidable"}, placeholders)
        except SignMismatchError:
            return self._show_grid_modbus({"base": "sign_mismatch"}, placeholders)
        except Exception as err:  # noqa: BLE001 - shown in the form
            placeholders["error"] = f"{type(err).__name__}: {err}"
            return self._show_grid_modbus({"base": "grid_modbus_failed"}, placeholders)
        return self._async_finish_system()


class SlemsConfigFlow(_GridModbusSteps, ConfigFlow, domain=DOMAIN):
    """Initial setup of the SLEMS system."""

    VERSION = 1

    def _async_finish_system(self) -> ConfigFlowResult:
        return self.async_create_entry(title="SLEMS", data=self._system)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_system_done(user_input, {})
        return self.async_show_form(
            step_id="user", data_schema=await _async_system_schema(self.hass, {})
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SlemsOptionsFlow:
        return SlemsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_TYPE_BATTERY: BatterySubentryFlow,
            SUBENTRY_TYPE_CONSUMER: ConsumerSubentryFlow,
        }


class SlemsOptionsFlow(_GridModbusSteps, OptionsFlow):
    """Change the system settings (the update listener reloads the entry)."""

    def _async_finish_system(self) -> ConfigFlowResult:
        return self.async_create_entry(data=self._system)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Options fully replace the initial data, so a cleared optional field
        # does not fall back to the value from the first setup.
        current = dict(self.config_entry.options or self.config_entry.data)
        if user_input is not None:
            return await self._async_system_done(user_input, current)
        return self.async_show_form(
            step_id="init", data_schema=await _async_system_schema(self.hass, current)
        )


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a battery."""

    def __init__(self) -> None:
        self._model: BatteryModel | None = None
        self._found: list[tuple[str, float]] = []
        self._host: str | None = None
        # Battery from HA entities: data collected over the steps.
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the battery model."""
        if user_input is not None:
            self._model = BatteryModel(user_input[CONF_MODEL])
            return await self._async_step_model(None)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL, default=BatteryModel.MARSTEK_VENUS_E3.value
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[model.value for model in BatteryModel],
                            translation_key=CONF_MODEL,
                        )
                    )
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing battery; the model cannot be changed."""
        self._model = BatteryModel(self._get_reconfigure_subentry().data[CONF_MODEL])
        return await self._async_step_model(None)

    async def _async_step_model(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        if self._model is BatteryModel.MARSTEK_VENUS_E3:
            if self.source == SOURCE_RECONFIGURE:
                return await self.async_step_marstek_venus_e3(user_input)
            return await self.async_step_search()
        if self.source == SOURCE_RECONFIGURE:
            self._data = dict(self._defaults(None))
            return await self.async_step_ha_entities()
        return await self.async_step_ha_device()

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Search the network for batteries and pick one (or enter it by hand)."""
        if user_input is not None:
            choice = user_input[CONF_HOST]
            self._host = None if choice == MANUAL else choice
            return await self.async_step_marstek_venus_e3()
        self._found = await async_find_batteries(
            self.hass, await _async_battery_hosts(self.hass, self._get_entry())
        )
        if not self._found:
            return await self.async_step_marstek_venus_e3(None, errors={"base": "none_found"})
        options = [
            selector.SelectOptionDict(value=host, label=f"{host} ({soc:.0f} %)")
            for host, soc in self._found
        ]
        options.append(selector.SelectOptionDict(value=MANUAL, label=MANUAL))
        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._found[0][0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, translation_key="search")
                    )
                }
            ),
            description_placeholders={"count": str(len(self._found))},
        )

    async def async_step_marstek_venus_e3(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> SubentryFlowResult:
        """Connection and limits of a Marstek Venus E 3.0."""
        errors = dict(errors or {})
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            user_input[CONF_UNIT_ID] = int(user_input[CONF_UNIT_ID])
            skip_test = user_input.pop(CONF_SKIP_CONNECTION_TEST, False)
            if skip_test or await MarstekVenusE3Driver.probe(
                user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_UNIT_ID]
            ):
                return self._async_finish(user_input)
            errors["base"] = "cannot_connect"

        defaults = self._defaults(user_input)
        if self._host and CONF_HOST not in defaults:
            defaults = {**defaults, CONF_HOST: self._host}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Venus E 3.0")): str,
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
                vol.Required(
                    CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_MODBUS_PORT)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=247, mode=selector.NumberSelectorMode.BOX)
                ),
                **self._limits_schema(defaults, HARDWARE_MAX_POWER_W, list(EfficiencyMode)),
                vol.Required(
                    CONF_RELEASE_STATE,
                    default=defaults.get(CONF_RELEASE_STATE, ReleaseState.AUTO.value),
                ): _release_state_selector(),
                vol.Optional(CONF_SKIP_CONNECTION_TEST, default=False): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="marstek_venus_e3", data_schema=schema, errors=errors
        )

    async def async_step_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the battery's device; its entities are suggested for the next steps."""
        if user_input is not None:
            if device_id := user_input.get(CONF_DEVICE):
                self._data = _suggest_from_device(self.hass, device_id)
            return await self.async_step_ha_entities()
        return self.async_show_form(
            step_id="ha_device",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE): selector.DeviceSelector(
                        selector.DeviceSelectorConfig(
                            entity=[
                                selector.EntityFilterSelectorConfig(
                                    domain="sensor", device_class=SensorDeviceClass.BATTERY
                                )
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_ha_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Sensors of the battery and the way SLEMS controls it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = _merge(self._data, user_input, _ENTITY_STEP_KEYS)
            control = BatteryControl(self._data[CONF_BATTERY_CONTROL])
            if control is not BatteryControl.NONE and not self._data.get(CONF_POWER_ENTITY):
                errors[CONF_POWER_ENTITY] = "power_required"
            elif control is BatteryControl.SETPOINT:
                return await self.async_step_ha_setpoint()
            elif control is BatteryControl.SPLIT:
                return await self.async_step_ha_split()
            elif control is BatteryControl.SCRIPT:
                return await self.async_step_ha_script()
            else:
                return await self.async_step_ha_limits()

        data = self._data
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=data.get(CONF_NAME, "Battery")): str,
                vol.Required(
                    CONF_SOC_ENTITY, default=data.get(CONF_SOC_ENTITY, vol.UNDEFINED)
                ): _BATTERY_SENSOR,
                _optional(CONF_POWER_ENTITY, data): _POWER_SENSOR,
                vol.Required(
                    CONF_POWER_INVERTED, default=data.get(CONF_POWER_INVERTED, False)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_BATTERY_CONTROL,
                    default=data.get(CONF_BATTERY_CONTROL, BatteryControl.NONE.value),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[control.value for control in BatteryControl],
                        translation_key=CONF_BATTERY_CONTROL,
                    )
                ),
                _optional(CONF_BATTERY_TEMPERATURE_ENTITY, data): _TEMPERATURE_SENSOR,
                _optional(CONF_MAX_CELL_VOLTAGE_ENTITY, data): _VOLTAGE_SENSOR,
                _optional(CONF_MIN_CELL_VOLTAGE_ENTITY, data): _VOLTAGE_SENSOR,
                _optional(CONF_CHARGED_ENERGY_ENTITY, data): _ENERGY_SENSOR,
                _optional(CONF_DISCHARGED_ENERGY_ENTITY, data): _ENERGY_SENSOR,
            }
        )
        return self.async_show_form(
            step_id="ha_entities",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_ha_setpoint(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """One number entity with the signed power."""
        if user_input is not None:
            self._data = _merge(
                self._data, user_input, (CONF_SETPOINT_ENTITY, CONF_SETPOINT_INVERTED, CONF_REMOTE_ENTITY)
            )
            return await self._async_step_after_control()
        data = self._data
        return self.async_show_form(
            step_id="ha_setpoint",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SETPOINT_ENTITY, default=data.get(CONF_SETPOINT_ENTITY, vol.UNDEFINED)
                    ): _NUMBER_ENTITY,
                    vol.Required(
                        CONF_SETPOINT_INVERTED, default=data.get(CONF_SETPOINT_INVERTED, False)
                    ): selector.BooleanSelector(),
                    _optional(CONF_REMOTE_ENTITY, data): _REMOTE_ENTITY,
                }
            ),
        )

    async def async_step_ha_split(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Numbers for charging and discharging, optionally a mode select."""
        if user_input is not None:
            self._data = _merge(
                self._data,
                user_input,
                (CONF_CHARGE_ENTITY, CONF_DISCHARGE_ENTITY, CONF_MODE_ENTITY, CONF_REMOTE_ENTITY),
            )
            return await self._async_step_after_control()
        data = self._data
        return self.async_show_form(
            step_id="ha_split",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHARGE_ENTITY, default=data.get(CONF_CHARGE_ENTITY, vol.UNDEFINED)
                    ): _NUMBER_ENTITY,
                    vol.Required(
                        CONF_DISCHARGE_ENTITY, default=data.get(CONF_DISCHARGE_ENTITY, vol.UNDEFINED)
                    ): _NUMBER_ENTITY,
                    _optional(CONF_MODE_ENTITY, data): _SELECT_ENTITY,
                    _optional(CONF_REMOTE_ENTITY, data): _REMOTE_ENTITY,
                }
            ),
        )

    async def async_step_ha_script(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Scripts that receive the power (``power_w``) and release the battery."""
        if user_input is not None:
            self._data = _merge(self._data, user_input, (CONF_POWER_SCRIPT, CONF_RELEASE_SCRIPT))
            return await self.async_step_ha_limits()
        data = self._data
        return self.async_show_form(
            step_id="ha_script",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POWER_SCRIPT, default=data.get(CONF_POWER_SCRIPT, vol.UNDEFINED)
                    ): _SCRIPT_ENTITY,
                    _optional(CONF_RELEASE_SCRIPT, data): _SCRIPT_ENTITY,
                }
            ),
        )

    async def _async_step_after_control(self) -> SubentryFlowResult:
        if self._option_fields():
            return await self.async_step_ha_options()
        return await self.async_step_ha_limits()

    def _option_fields(self) -> dict[str, list[str]]:
        """Option fields (key -> options of the select) for the chosen selects."""
        data = self._data
        fields: dict[str, list[str]] = {}
        control = BatteryControl(data[CONF_BATTERY_CONTROL])
        mode = data.get(CONF_MODE_ENTITY) if control is BatteryControl.SPLIT else None
        if mode and (options := _select_options(self.hass, mode)):
            for key in (CONF_MODE_CHARGE, CONF_MODE_DISCHARGE, CONF_MODE_STANDBY, CONF_MODE_AUTO):
                fields[key] = options
        remote = data.get(CONF_REMOTE_ENTITY)
        if remote and remote.split(".", 1)[0] in ("select", "input_select"):
            if options := _select_options(self.hass, remote):
                fields[CONF_REMOTE_ON] = options
                fields[CONF_REMOTE_OFF] = options
        return fields

    async def async_step_ha_options(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Which option of the mode / remote control select means what."""
        fields = self._option_fields()
        if user_input is not None:
            self._data = _merge(self._data, user_input, tuple(fields))
            return await self.async_step_ha_limits()
        data = self._data
        mode = data.get(CONF_MODE_ENTITY)
        if mode and CONF_MODE_CHARGE in fields and not any(
            key in data for key in (CONF_MODE_CHARGE, CONF_MODE_DISCHARGE)
        ):
            data.update(suggest_mode_options(fields[CONF_MODE_CHARGE]))
        if CONF_REMOTE_ON in fields and CONF_REMOTE_ON not in data:
            data.update(suggest_remote_options(fields[CONF_REMOTE_ON]))
        schema: dict = {}
        for key, options in fields.items():
            field = (
                vol.Required(key, default=data.get(key, vol.UNDEFINED))
                if key in (CONF_REMOTE_ON, CONF_REMOTE_OFF)
                else _optional(key, data)
            )
            schema[field] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options, mode=selector.SelectSelectorMode.DROPDOWN
                )
            )
        return self.async_show_form(step_id="ha_options", data_schema=vol.Schema(schema))

    async def async_step_ha_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Capacity, power limits, efficiency and, if controlled, release and timing."""
        data = self._data
        control = BatteryControl(data.get(CONF_BATTERY_CONTROL, BatteryControl.NONE))
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = _entity_battery_data({**data, **user_input})
            if (
                control is not BatteryControl.NONE
                and candidate.get(CONF_RELEASE_STATE) == ReleaseState.AUTO
                and not EntityBatteryConfig.from_data(_battery_data(candidate, BatteryModel.HA_ENTITIES)).can_release_to_auto
            ):
                errors[CONF_RELEASE_STATE] = "no_auto_release"
            else:
                return self._async_finish(candidate)
            data = {**data, **user_input}

        modes = [EfficiencyMode.LEARNED, EfficiencyMode.MANUAL]
        if data.get(CONF_CHARGED_ENERGY_ENTITY) and data.get(CONF_DISCHARGED_ENERGY_ENTITY):
            modes.insert(0, EfficiencyMode.BATTERY_COUNTERS)
        if data.get(CONF_EFFICIENCY_MODE) not in [mode.value for mode in modes]:
            data.pop(CONF_EFFICIENCY_MODE, None)
        schema: dict = self._limits_schema(data, 100_000, modes)
        if control is not BatteryControl.NONE:
            auto = EntityBatteryConfig.from_data(
                _battery_data({**_LIMIT_PLACEHOLDERS, **data}, BatteryModel.HA_ENTITIES)
            ).can_release_to_auto
            default_release = ReleaseState.AUTO if auto else ReleaseState.STANDBY
            schema.update(
                {
                    vol.Required(
                        CONF_RELEASE_STATE,
                        default=data.get(CONF_RELEASE_STATE, default_release.value),
                    ): _release_state_selector(),
                    vol.Required(
                        CONF_MIN_COMMAND_INTERVAL_S,
                        default=data.get(CONF_MIN_COMMAND_INTERVAL_S, 0),
                    ): _SECONDS,
                    vol.Required(
                        CONF_KEEPALIVE_S, default=data.get(CONF_KEEPALIVE_S, 60)
                    ): _SECONDS,
                }
            )
        return self.async_show_form(
            step_id="ha_limits", data_schema=vol.Schema(schema), errors=errors
        )

    @staticmethod
    def _limits_schema(
        defaults: dict[str, Any], max_power_w: int, efficiency_modes: list[EfficiencyMode]
    ) -> dict:
        default_power = min(HARDWARE_MAX_POWER_W, max_power_w)
        return {
            vol.Required(
                CONF_CAPACITY_WH, default=defaults.get(CONF_CAPACITY_WH, DEFAULT_CAPACITY_WH)
            ): _CAPACITY,
            vol.Required(
                CONF_MAX_CHARGE_POWER_W,
                default=defaults.get(CONF_MAX_CHARGE_POWER_W, default_power),
            ): _watts(max_power_w),
            vol.Required(
                CONF_MAX_DISCHARGE_POWER_W,
                default=defaults.get(CONF_MAX_DISCHARGE_POWER_W, default_power),
            ): _watts(max_power_w),
            vol.Required(
                CONF_EFFICIENCY_MODE,
                default=defaults.get(CONF_EFFICIENCY_MODE, efficiency_modes[0].value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[mode.value for mode in efficiency_modes],
                    translation_key=CONF_EFFICIENCY_MODE,
                )
            ),
            vol.Required(
                CONF_ROUND_TRIP_EFFICIENCY_PCT,
                default=defaults.get(
                    CONF_ROUND_TRIP_EFFICIENCY_PCT, DEFAULT_ROUND_TRIP_EFFICIENCY_PCT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=100, step=1, unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }

    def _defaults(self, user_input: dict[str, Any] | None) -> dict[str, Any]:
        """Form defaults: last input, else the subentry being reconfigured."""
        if user_input is not None:
            return user_input
        if self.source == SOURCE_RECONFIGURE:
            subentry = self._get_reconfigure_subentry()
            return {CONF_NAME: subentry.title, **subentry.data}
        return {}

    def _async_finish(self, user_input: dict[str, Any]) -> SubentryFlowResult:
        data = _battery_data(user_input, self._model)
        title = data.pop(CONF_NAME)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)


# Choice in the search step for entering the address by hand.
MANUAL = "manual"


async def _async_battery_hosts(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Hosts of the Modbus batteries configured in ``entry`` and their IPv4 addresses."""
    hosts = {
        host
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BATTERY
        and (host := subentry.data.get(CONF_HOST))
    }
    result = set(hosts)
    for host in hosts:
        try:
            infos = await hass.loop.getaddrinfo(host, None, family=socket.AF_INET)
        except OSError:
            continue
        result |= {info[4][0] for info in infos}
    return result


# Fields of the entities step (a cleared optional field is left out of the input).
_ENTITY_STEP_KEYS = (
    CONF_NAME,
    CONF_SOC_ENTITY,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_BATTERY_CONTROL,
    CONF_BATTERY_TEMPERATURE_ENTITY,
    CONF_MAX_CELL_VOLTAGE_ENTITY,
    CONF_MIN_CELL_VOLTAGE_ENTITY,
    CONF_CHARGED_ENERGY_ENTITY,
    CONF_DISCHARGED_ENERGY_ENTITY,
)
# Stand-ins for the limits while they are not entered yet.
_LIMIT_PLACEHOLDERS = {
    CONF_CAPACITY_WH: 0,
    CONF_MAX_CHARGE_POWER_W: 0,
    CONF_MAX_DISCHARGE_POWER_W: 0,
    CONF_ROUND_TRIP_EFFICIENCY_PCT: DEFAULT_ROUND_TRIP_EFFICIENCY_PCT,
}


def _merge(data: dict[str, Any], user_input: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Take the fields ``keys`` of a step; fields missing in the input were cleared."""
    result = {key: value for key, value in data.items() if key not in keys}
    result.update({key: value for key, value in user_input.items() if value not in (None, "")})
    return result


def _entity_battery_data(data: dict[str, Any]) -> dict[str, Any]:
    """Data of a battery from HA entities without the keys of other control types."""
    control = BatteryControl(data.get(CONF_BATTERY_CONTROL, BatteryControl.NONE))
    result = {key: value for key, value in data.items() if value not in (None, "")}
    for other, keys in _CONTROL_KEYS.items():
        if other is not control:
            for key in keys:
                result.pop(key, None)
    if control in (BatteryControl.NONE, BatteryControl.SCRIPT):
        for key in _REMOTE_KEYS:
            result.pop(key, None)
    if control is BatteryControl.NONE:
        for key in _CONTROLLED_KEYS:
            result.pop(key, None)
    if not result.get(CONF_MODE_ENTITY):
        for key in (CONF_MODE_CHARGE, CONF_MODE_DISCHARGE, CONF_MODE_STANDBY, CONF_MODE_AUTO):
            result.pop(key, None)
    remote = result.get(CONF_REMOTE_ENTITY)
    if not remote or remote.split(".", 1)[0] not in ("select", "input_select"):
        result.pop(CONF_REMOTE_ON, None)
        result.pop(CONF_REMOTE_OFF, None)
    result.pop(CONF_DEVICE, None)
    return result


def _select_options(hass: HomeAssistant, entity_id: str) -> list[str]:
    state = hass.states.get(entity_id)
    return list(state.attributes.get("options") or []) if state else []


def _device_entities(hass: HomeAssistant, device_id: str) -> list[EntityInfo]:
    """The enabled entities of a device as the matcher sees them."""
    infos = []
    for entry in er.async_entries_for_device(er.async_get(hass), device_id):
        if entry.disabled_by is not None:
            continue
        state = hass.states.get(entry.entity_id)
        attributes = state.attributes if state else {}
        infos.append(
            EntityInfo.create(
                entry.entity_id,
                entry.translation_key,
                entry.original_name,
                entry.name,
                device_class=entry.device_class or entry.original_device_class
                or attributes.get("device_class"),
                unit=attributes.get("unit_of_measurement") or entry.unit_of_measurement,
                state_class=attributes.get("state_class"),
                options=tuple(attributes.get("options") or ()),
                minimum=attributes.get("min"),
                maximum=attributes.get("max"),
                state=state.state if state else None,
            )
        )
    return infos


def _suggest_from_device(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Form data suggested from the entities of a device."""
    infos = _device_entities(hass, device_id)
    by_id = {info.entity_id: info for info in infos}
    matches = match_battery_entities(infos)
    data: dict[str, Any] = {
        key: value for key, value in matches.items() if key != CAPACITY
    }
    data[CONF_BATTERY_CONTROL] = suggest_control(matches).value
    if device := dr.async_get(hass).async_get(device_id):
        data[CONF_NAME] = device.name_by_user or device.name or "Battery"
    if capacity := matches.get(CAPACITY):
        kwh = state_as_kwh(hass.states.get(capacity))
        if kwh:
            data[CONF_CAPACITY_WH] = round(kwh * 1000)

    def watts(value: float | None, info: EntityInfo) -> int | None:
        if value is None:
            return None
        return round(abs(value) * (1000 if info.unit == "kW" else 1))

    charge = by_id.get(matches.get(CONF_CHARGE_ENTITY, ""))
    discharge = by_id.get(matches.get(CONF_DISCHARGE_ENTITY, ""))
    setpoint = by_id.get(matches.get(CONF_SETPOINT_ENTITY, ""))
    if charge is not None and (value := watts(charge.maximum, charge)):
        data[CONF_MAX_CHARGE_POWER_W] = value
    elif setpoint is not None and (value := watts(setpoint.maximum, setpoint)):
        data[CONF_MAX_CHARGE_POWER_W] = value
    if discharge is not None and (value := watts(discharge.maximum, discharge)):
        data[CONF_MAX_DISCHARGE_POWER_W] = value
    elif setpoint is not None and (value := watts(setpoint.minimum, setpoint)):
        data[CONF_MAX_DISCHARGE_POWER_W] = value
    if mode := by_id.get(matches.get(CONF_MODE_ENTITY, "")):
        data.update(suggest_mode_options(mode.options))
    if remote := by_id.get(matches.get(CONF_REMOTE_ENTITY, "")):
        data.update(suggest_remote_options(remote.options))
    return data


def _battery_data(user_input: dict[str, Any], model: BatteryModel) -> dict[str, Any]:
    """Subentry data of a battery (name still included) from the form input."""
    data = dict(user_input)
    data[CONF_MODEL] = model.value
    for key in (
        CONF_CAPACITY_WH,
        CONF_MAX_CHARGE_POWER_W,
        CONF_MAX_DISCHARGE_POWER_W,
        CONF_ROUND_TRIP_EFFICIENCY_PCT,
    ):
        data[key] = int(data[key])
    return data


class ConsumerSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a consumer.

    Every consumer must provide its own power and energy sensor. Control via a
    switch or a power set point is optional.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def _existing(self) -> dict[str, Any]:
        if self.source == SOURCE_RECONFIGURE:
            subentry = self._get_reconfigure_subentry()
            return {CONF_NAME: subentry.title, **subentry.data}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Measurement and control mode."""
        if user_input is not None:
            self._data = user_input
            if ControlMode(user_input[CONF_CONTROL_MODE]) is ControlMode.NONE:
                return self._async_finish()
            return await self.async_step_control()

        defaults = self._existing()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
                vol.Required(
                    CONF_CONSUMER_TYPE,
                    default=defaults.get(CONF_CONSUMER_TYPE, ConsumerType.OTHER.value),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[t.value for t in ConsumerType],
                        translation_key=CONF_CONSUMER_TYPE,
                    )
                ),
                vol.Required(
                    CONF_POWER_ENTITY, default=defaults.get(CONF_POWER_ENTITY, vol.UNDEFINED)
                ): _POWER_SENSOR,
                vol.Required(
                    CONF_ENERGY_ENTITY, default=defaults.get(CONF_ENERGY_ENTITY, vol.UNDEFINED)
                ): _ENERGY_SENSOR,
                vol.Required(
                    CONF_INCLUDED_IN_METER, default=defaults.get(CONF_INCLUDED_IN_METER, True)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_SHOW_IN_FLOW, default=defaults.get(CONF_SHOW_IN_FLOW, True)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_CONTROL_MODE,
                    default=defaults.get(CONF_CONTROL_MODE, ControlMode.NONE.value),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[m.value for m in ControlMode],
                        translation_key=CONF_CONTROL_MODE,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing consumer."""
        return await self.async_step_user(user_input)

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Control entity, power or current range, external block and priority."""
        if user_input is not None:
            # Cleared optional fields must not keep their old value.
            for key in (CONF_PHASES_ENTITY, CONF_START_ENTITY, CONF_START_ON, CONF_START_OFF):
                self._data.pop(key, None)
            self._data.update(user_input)
            start = user_input.get(CONF_START_ENTITY)
            if start and start.split(".", 1)[0] in ("select", "input_select"):
                return await self.async_step_start_options()
            return self._async_finish()

        defaults = self._existing()
        # Control entity and power fields are only reused if the mode is unchanged.
        mode = ControlMode(self._data[CONF_CONTROL_MODE])
        if defaults.get(CONF_CONTROL_MODE) != mode.value:
            keep = (
                CONF_BLOCK_ENTITY,
                CONF_PRIORITY,
                CONF_MIN_ON_MINUTES,
                CONF_MIN_OFF_MINUTES,
                CONF_THERMOSTAT_CYCLES,
                CONF_TEMPERATURE_ENTITY,
                CONF_TEMPERATURE_2_ENTITY,
            )
            defaults = {k: v for k, v in defaults.items() if k in keep}

        wallbox = ConsumerType(self._data[CONF_CONSUMER_TYPE]) is ConsumerType.WALLBOX
        if wallbox and CONF_MIN_ON_MINUTES not in defaults:
            defaults = {
                **defaults,
                CONF_MIN_ON_MINUTES: WALLBOX_MIN_ON_MINUTES,
                CONF_MIN_OFF_MINUTES: WALLBOX_MIN_OFF_MINUTES,
            }
        fields: dict = {}
        if mode is ControlMode.CURRENT:
            amperes = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=125, step=1, unit_of_measurement="A",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            fields[
                vol.Required(CONF_CONTROL_ENTITY, default=defaults.get(CONF_CONTROL_ENTITY, vol.UNDEFINED))
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "input_number"])
            )
            fields[
                vol.Required(CONF_MIN_CURRENT_A, default=defaults.get(CONF_MIN_CURRENT_A, DEFAULT_MIN_CURRENT_A))
            ] = amperes
            fields[
                vol.Required(CONF_MAX_CURRENT_A, default=defaults.get(CONF_MAX_CURRENT_A, DEFAULT_MAX_CURRENT_A))
            ] = amperes
            fields[
                vol.Required(CONF_PHASES, default=str(defaults.get(CONF_PHASES, DEFAULT_PHASES)))
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=["1", "3"], translation_key=CONF_PHASES)
            )
            fields[_optional(CONF_PHASES_ENTITY, defaults)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            )
            fields[
                vol.Required(CONF_VOLTAGE_V, default=defaults.get(CONF_VOLTAGE_V, DEFAULT_VOLTAGE_V))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=100, max=400, step=1, unit_of_measurement="V",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            fields[_optional(CONF_START_ENTITY, defaults)] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["switch", "input_boolean", "select", "input_select"]
                )
            )
        elif mode is ControlMode.SWITCH:
            fields[
                vol.Required(CONF_CONTROL_ENTITY, default=defaults.get(CONF_CONTROL_ENTITY, vol.UNDEFINED))
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
            fields[
                vol.Required(CONF_NOMINAL_POWER_W, default=defaults.get(CONF_NOMINAL_POWER_W, vol.UNDEFINED))
            ] = _watts(100_000)
        else:
            fields[
                vol.Required(CONF_CONTROL_ENTITY, default=defaults.get(CONF_CONTROL_ENTITY, vol.UNDEFINED))
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "input_number"])
            )
            fields[
                vol.Required(CONF_MIN_POWER_W, default=defaults.get(CONF_MIN_POWER_W, 0))
            ] = _watts(100_000)
            fields[
                vol.Required(CONF_MAX_POWER_W, default=defaults.get(CONF_MAX_POWER_W, vol.UNDEFINED))
            ] = _watts(100_000)
        minutes = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=1440, step=1, unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        fields[_optional(CONF_MIN_ON_MINUTES, defaults)] = minutes
        fields[_optional(CONF_MIN_OFF_MINUTES, defaults)] = minutes
        fields[_optional(CONF_BLOCK_ENTITY, defaults)] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "switch", "water_heater"]
            )
        )
        fields[
            vol.Required(CONF_THERMOSTAT_CYCLES, default=defaults.get(CONF_THERMOSTAT_CYCLES, False))
        ] = selector.BooleanSelector()
        temperature = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        )
        fields[_optional(CONF_TEMPERATURE_ENTITY, defaults)] = temperature
        fields[_optional(CONF_TEMPERATURE_2_ENTITY, defaults)] = temperature
        fields[
            vol.Required(CONF_PRIORITY, default=defaults.get(CONF_PRIORITY, DEFAULT_PRIORITY))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )
        return self.async_show_form(step_id="control", data_schema=vol.Schema(fields))

    async def async_step_start_options(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Options of a select start entity for on and off (e.g. evcc: fast / off)."""
        if user_input is not None:
            self._data.update(user_input)
            return self._async_finish()
        entity_id = self._data[CONF_START_ENTITY]
        state = self.hass.states.get(entity_id)
        options = list(state.attributes.get("options", [])) if state else []
        defaults = self._existing()
        option = selector.SelectSelector(
            selector.SelectSelectorConfig(options=options, custom_value=not options)
        )
        return self.async_show_form(
            step_id="start_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_START_ON, default=defaults.get(CONF_START_ON, vol.UNDEFINED)): option,
                    vol.Required(CONF_START_OFF, default=defaults.get(CONF_START_OFF, vol.UNDEFINED)): option,
                }
            ),
            description_placeholders={"entity": entity_id},
        )

    def _async_finish(self) -> SubentryFlowResult:
        data = dict(self._data)
        title = data.pop(CONF_NAME)
        for key in (
            CONF_NOMINAL_POWER_W,
            CONF_MIN_POWER_W,
            CONF_MAX_POWER_W,
            CONF_PRIORITY,
            CONF_MIN_ON_MINUTES,
            CONF_MIN_OFF_MINUTES,
            CONF_PHASES,
            CONF_VOLTAGE_V,
        ):
            if key in data:
                data[key] = int(data[key])
        for key in (CONF_MIN_CURRENT_A, CONF_MAX_CURRENT_A):
            if key in data:
                data[key] = float(data[key])
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)
