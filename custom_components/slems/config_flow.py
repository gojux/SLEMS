"""Config flow for SLEMS.

The main entry holds the system level measurements (grid, PV, weather). Every
battery is a config subentry, so batteries can be added, edited and removed at
any time without touching the rest of the configuration.

A Marstek Venus can be searched in the network when adding a battery (see
discovery).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import socket
from collections.abc import Iterable, Mapping
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from modbus_connection import ModbusTcpParams

from .const import (
    CONF_GRID_EXPORT_ENERGY_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    SUBENTRY_TYPE_TARIFF,
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
    CONF_AVOID_CYCLING,
    CONF_TEMPERATURE_2_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    CONF_CAPACITY_WH,
    CONF_PURCHASE_PRICE_EUR,
    CONF_RATED_CYCLES,
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
from .energy_history import async_grid_energy
from .tariff import (
    Group,
    Role,
    Side,
    TariffItem,
    Unit,
    compute_bill,
    format_month_prices,
    parse_month_prices,
    tariff_from_data,
    vat_data,
)
from . import elcom
from .currency import SYMBOLS, currency_code, market_prices_usable, symbols
from .reference_values import MARKETS as REFERENCE_MARKETS
from .tariff_yaml import VERSION as YAML_VERSION
from .tariff_yaml import TariffYamlError, export_yaml, parse_yaml
from .tariff_updates import (
    CORRECTION,
    Candidate,
    UpdateChanges,
    apply_updates,
    find_candidates,
    reset_to_templates,
    template_differences,
    template_directories,
)
from .tariff_templates import (
    Template,
    TemplatePartTwice,
    combine as combine_templates,
    energy_choices,
    grid_choices,
    label as template_label,
    levies_choices,
    load_templates,
    suggest_grid,
    suggest_levies,
)

# Option "no template" of a template selection.
_NO_TEMPLATE = "-"
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
            optional(CONF_GRID_IMPORT_ENERGY_ENTITY): _ENERGY_SENSOR,
            optional(CONF_GRID_EXPORT_ENERGY_ENTITY): _ENERGY_SENSOR,
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
            SUBENTRY_TYPE_TARIFF: TariffSubentryFlow,
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
            skip_test = user_input.pop(CONF_SKIP_CONNECTION_TEST, False) or self._same_connection(user_input)
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

    def _limits_schema(
        self, defaults: dict[str, Any], max_power_w: int, efficiency_modes: list[EfficiencyMode]
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
            _optional(CONF_PURCHASE_PRICE_EUR, defaults): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100_000, step="any", unit_of_measurement=symbols(currency_code(self.hass))[0],
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            _optional(CONF_RATED_CYCLES, defaults): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100_000, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }

    def _same_connection(self, user_input: dict[str, Any]) -> bool:
        """Reconfigured with the same address: the running battery holds the
        connection, a second one for a test is refused (one Modbus client)."""
        if self.source != SOURCE_RECONFIGURE:
            return False
        data = self._get_reconfigure_subentry().data
        return all(data.get(key) == user_input.get(key) for key in (CONF_HOST, CONF_PORT, CONF_UNIT_ID))

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


async def _option_labels(hass: HomeAssistant, entity_id: str, options: list[str]) -> list[selector.SelectOptionDict]:
    """Options of a select with the names its integration shows for them (else the option itself)."""
    entry = er.async_get(hass).async_get(entity_id)
    strings: dict[str, str] = {}
    if entry is not None and entry.translation_key:
        strings = await async_get_translations(hass, hass.config.language, "entity", {entry.platform})
    prefix = (
        f"component.{entry.platform}.entity.{entry.domain}.{entry.translation_key}.state."
        if entry is not None
        else ""
    )
    return [
        selector.SelectOptionDict(value=option, label=strings.get(prefix + option, option)) for option in options
    ]


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
    if data.get(CONF_RATED_CYCLES) is not None:
        data[CONF_RATED_CYCLES] = int(data[CONF_RATED_CYCLES])
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
            # Set in earlier versions by hand, now observed; kept until the pauses are learned again.
            if self._existing().get(CONF_THERMOSTAT_CYCLES):
                self._data[CONF_THERMOSTAT_CYCLES] = True
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
                CONF_AVOID_CYCLING,
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
                selector.EntitySelectorConfig(domain=["number", "input_number", "select", "input_select"])
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
        fields[
            vol.Required(CONF_AVOID_CYCLING, default=defaults.get(CONF_AVOID_CYCLING, False))
        ] = selector.BooleanSelector()
        fields[_optional(CONF_BLOCK_ENTITY, defaults)] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "switch", "water_heater"]
            )
        )
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
            selector.SelectSelectorConfig(
                options=await _option_labels(self.hass, entity_id, options), custom_value=not options
            )
        )
        return self.async_show_form(
            step_id="start_options",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_START_ON, default=defaults.get(CONF_START_ON, vol.UNDEFINED)): option,
                    vol.Required(CONF_START_OFF, default=defaults.get(CONF_START_OFF, vol.UNDEFINED)): option,
                }
            ),
            description_placeholders={"entity": f"{state.name} ({entity_id})" if state else entity_id},
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


_TARIFF_VAT_FIELDS = {
    "vat_import": ((Side.IMPORT, Group.ENERGY), (Side.IMPORT, Group.GRID), (Side.IMPORT, Group.LEVIES)),
    "vat_export_energy": ((Side.EXPORT, Group.ENERGY),),
    "vat_export_other": ((Side.EXPORT, Group.GRID), (Side.EXPORT, Group.LEVIES)),
}
_PERCENT = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=100, step=0.1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
)


def _price_symbols(code: str) -> tuple[str, str]:
    """(symbol, symbol of a hundredth) for the item form; "1/100 XYZ" for an unknown currency."""
    major, minor = symbols(code)
    return (major, minor) if code in SYMBOLS else (code, f"1/100 {code}")


def _tariff_select(options: list[str], key: str, *, multiple: bool = False) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            multiple=multiple,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )



def tariff_prompt_url(language: str | None) -> str:
    """The guide to make a tariff from a bill with an AI, in German or English."""
    suffix = ".de" if (language or "").startswith("de") else ""
    return f"https://github.com/gojux/SLEMS/blob/main/docs/tariff-prompt{suffix}.md"

class TariffSubentryFlow(ConfigSubentryFlow):
    """Add or edit a tariff by entering the lines of a bill (see tariff).

    The items can be checked against a bill: SLEMS computes its period from
    the recorded grid import / export and shows the difference per group.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""
        self._edit_index: int | None = None
        self._check: dict[str, str] | None = None
        self._templates: list[Template] = []
        self._country: str | None = None
        self._energy_template: Template | None = None
        self._chosen_templates: list[Template] = []
        # ElCom (Switzerland): categories, found supplies and the chosen category.
        self._elcom_categories: dict[str, str] = {}
        self._elcom_found: list[elcom.Supply] = []
        self._elcom_category = "H4"
        # Newer price levels and successors per template of the tariff, and the choices made.
        self._candidates: dict[str, list[Candidate]] | None = None
        self._choice_queue: list[str] = []
        self._chosen: list[Candidate] = []
        self._declined: list[Candidate] = []

    @property
    def _items(self) -> list[dict[str, Any]]:
        return self._data.setdefault("items", [])

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """A new tariff: enter it, start from templates or paste a YAML file (see tariff_yaml)."""
        templates = await self.hass.async_add_executor_job(load_templates, template_directories(self.hass))
        # Only templates in the currency of Home Assistant (no exchange rates) and
        # of its country: a tariff applies to the country of the metering point.
        country = (self.hass.config.country or "").upper()
        self._templates = [
            t
            for t in templates
            if t.currency in (None, currency_code(self.hass)) and (not country or t.country in (country, "–"))
        ]
        options = ["details", "template", "import_yaml"] if self._templates else ["details", "import_yaml"]
        if currency_code(self.hass) == "CHF" or self.hass.config.country == "CH":
            options.insert(1, "elcom")
        return self.async_show_menu(step_id="user", menu_options=options)

    async def async_step_template(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Country of the templates: the one of Home Assistant, else chosen."""
        countries = sorted({template.country for template in self._templates} - {"–"}) or ["–"]
        if self.hass.config.country or len(countries) == 1:
            self._country = countries[0]
            return await self.async_step_template_energy()
        if user_input is not None:
            self._country = user_input["country"]
            return await self.async_step_template_energy()
        return self.async_show_form(
            step_id="template",
            data_schema=vol.Schema(
                {
                    vol.Required("country", default=self._country or vol.UNDEFINED): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=countries, mode=selector.SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )

    async def async_step_template_energy(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The energy template (supplier); complete templates include grid and levies."""
        templates = self._country_templates()
        choices = energy_choices(templates.values())
        if not choices:
            return await self.async_step_template_rest()
        by_label = self._labelled(choices)
        schema = vol.Schema({vol.Optional("energy"): self._template_selector(by_label)})
        placeholders = {"prompt_url": tariff_prompt_url(self.hass.config.language)}
        if user_input is not None and user_input.get("energy") and user_input["energy"] not in by_label:
            return self.async_show_form(
                step_id="template_energy",
                data_schema=schema,
                errors={"energy": "template_unknown"},
                description_placeholders=placeholders,
            )
        if user_input is not None:
            self._energy_template = by_label.get(user_input.get("energy") or "")
            if self._energy_template is not None and (self._energy_template.complete or self._energy_template.feed_in):
                # Complete, or a feed-in tariff of its own: no grid or levies.
                return await self._async_combine_templates([self._energy_template])
            return await self.async_step_template_rest()
        return self.async_show_form(step_id="template_energy", data_schema=schema, description_placeholders=placeholders)

    async def async_step_template_rest(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Grid and levies, preselected: the grid operator the energy template names
        (its household level) and the levies of the country, each in effect today."""
        templates = self._country_templates()
        errors: dict[str, str] = {}
        grids = self._labelled(grid_choices(templates.values()))
        levies_by_label = self._labelled(levies_choices(templates.values()))
        if user_input is not None and any(
            user_input.get(key) and user_input[key] not in options
            for key, options in (("grid", grids), ("levies", levies_by_label))
        ):
            errors["base"] = "template_unknown"
        elif user_input is not None:
            chosen = [
                template
                for template in (
                    self._energy_template,
                    grids.get(user_input.get("grid") or ""),
                    levies_by_label.get(user_input.get("levies") or ""),
                )
                if template is not None
            ]
            if not chosen:
                errors["base"] = "template_none"
            else:
                result = await self._async_combine_templates(chosen)
                if result is not None:
                    return result
                errors["base"] = "template_part_twice"
        today = dt_util.now().date()
        grid = suggest_grid(self._energy_template, templates.values(), today)
        levies = None if grid is not None and "levies" in grid.parts else suggest_levies(templates.values(), today)
        label_of = {template.key: label for options in (grids, levies_by_label) for label, template in options.items()}
        defaults = user_input or {
            "grid": label_of.get(grid.key) if grid else None,
            "levies": label_of.get(levies.key) if levies else None,
        }

        def field(key: str):
            # Empty: none of them.
            return vol.Optional(key, default=defaults[key]) if defaults.get(key) else vol.Optional(key)

        return self.async_show_form(
            step_id="template_rest",
            data_schema=vol.Schema(
                {
                    field("grid"): self._template_selector(grids),
                    field("levies"): self._template_selector(levies_by_label),
                }
            ),
            errors=errors,
        )

    def _country_templates(self) -> dict[str, Template]:
        # Own templates without a country are offered in every country.
        return {t.key: t for t in self._templates if t.country in (self._country, "–")}

    def _labelled(self, templates: Iterable[Template]) -> dict[str, Template]:
        """Templates by the label shown (unique; the picker shows the value it returns)."""
        words = _CHECK_WORDS["de" if self.hass.config.language.startswith("de") else "en"]
        part_names = {group.value: words[group].capitalize() for group in Group} | {"feed_in": words[Side.EXPORT]}
        result: dict[str, Template] = {}
        for template in templates:
            label = template_label(template, part_names, words["own"], words["offer"])
            if not template.feed_in:
                # The field names the part; only feed-in tariffs share the energy list.
                label = label.split(" · ", 1)[-1]
            if label in result:
                label = f"{label} [{template.key.rsplit('/', 1)[-1]}]"
            result[label] = template
        return result

    def _template_selector(self, templates: Mapping[str, Template]) -> selector.SelectSelector:
        """Templates to choose from by their label (left empty: none). A custom
        value makes the frontend show a searchable picker; unknown values are
        refused by the steps."""
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(templates), mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True)
        )

    async def _async_combine_templates(self, chosen: list[Template]) -> SubentryFlowResult | None:
        """One tariff of the chosen templates (their optional items chosen
        first), on to name, role and VAT; None if two of them cover the same part."""
        try:
            combine_templates(chosen)
        except TemplatePartTwice:
            return None
        self._chosen_templates = chosen
        if any(template.optional_items for template in chosen):
            return await self.async_step_template_options()
        self._title, self._data = combine_templates(chosen)
        return await self.async_step_details()

    async def async_step_template_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The optional items of the chosen templates (upgrades, bonuses with conditions)."""
        chosen = self._chosen_templates
        if user_input is not None:
            picked = set(user_input.get("options") or [])
            options = {
                t.key: [item["name"] for item in t.optional_items if f"{t.key}|{item['name']}" in picked] for t in chosen
            }
            self._title, self._data = combine_templates(chosen, options)
            return await self.async_step_details()
        language = self.hass.config.language
        choices = [
            selector.SelectOptionDict(
                value=f"{t.key}|{item['name']}",
                label=f"{item['name']} ({_describe_item(TariffItem.from_dict(item), language, currency_code(self.hass)).split(': ', 1)[1].split(' (')[0]})",
            )
            for t in chosen
            for item in t.optional_items
        ]
        return self.async_show_form(
            step_id="template_options",
            data_schema=vol.Schema(
                {
                    vol.Optional("options", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices, multiple=True, mode=selector.SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_elcom(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Swiss tariff from ElCom (see elcom): municipality and consumption category.
        Choosing this queries the open data of ElCom (ld.admin.ch)."""
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        if user_input is not None:
            try:
                self._elcom_found = await elcom.async_search(session, user_input["municipality"], dt_util.now().year)
            except elcom.ElcomError:
                errors["base"] = "elcom_unavailable"
            else:
                if self._elcom_found:
                    self._elcom_category = user_input["category"]
                    return await self.async_step_elcom_pick()
                errors["municipality"] = "elcom_none"
        if not self._elcom_categories:
            try:
                self._elcom_categories = await elcom.async_categories(session)
            except elcom.ElcomError:
                self._elcom_categories = {name: name for name in elcom.CATEGORIES}
        options = [
            selector.SelectOptionDict(value=name, label=f"{name}: {text}") for name, text in self._elcom_categories.items()
        ]
        return self.async_show_form(
            step_id="elcom",
            data_schema=vol.Schema(
                {
                    vol.Required("municipality", default=(user_input or {}).get("municipality", vol.UNDEFINED)): str,
                    vol.Required("category", default=(user_input or {}).get("category", "H4")): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_elcom_pick(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Municipality and grid operator of the search; the tariff of this year."""
        errors: dict[str, str] = {}
        found = {f"{s.municipality}/{s.operator}": s for s in self._elcom_found}
        if user_input is not None:
            supply = found[user_input["supply"]]
            year = dt_util.now().year
            try:
                levels = await elcom.async_levels(
                    async_get_clientsession(self.hass), supply, self._elcom_category, year - 1
                )
            except elcom.ElcomError:
                errors["base"] = "elcom_unavailable"
            else:
                current = [t for t in levels if t.meta["year"] <= year]
                if current:
                    # The later years are offered as newer prices (see tariff_updates).
                    self._templates = levels
                    self._title, self._data = combine_templates([current[-1]])
                    return await self.async_step_details()
                errors["base"] = "elcom_none"
        return self.async_show_form(
            step_id="elcom_pick",
            data_schema=vol.Schema(
                {
                    vol.Required("supply"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=key, label=f"{s.municipality_name} – {s.operator_name}")
                                for key, s in found.items()
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_import_yaml(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Paste a tariff as YAML (an export, a template or the answer of an AI)."""
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            try:
                title, data = parse_yaml(user_input["yaml"])
            except TariffYamlError as err:
                errors["base"], detail = err.key, err.detail
            else:
                currency = (data.get("meta") or {}).get("currency")
                if currency and currency != currency_code(self.hass):
                    # Prices are plain numbers in the currency of Home Assistant.
                    errors["base"], detail = "yaml_currency", f"{currency} / {currency_code(self.hass)}"
                else:
                    self._title, self._data = title, data
                    return await self.async_step_details()
        return self.async_show_form(
            step_id="import_yaml",
            data_schema=vol.Schema(
                {
                    vol.Required("yaml", default=(user_input or {}).get("yaml", vol.UNDEFINED)): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"detail": detail, "version": str(YAML_VERSION)},
        )

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, role and VAT."""
        if user_input is not None:
            self._title = user_input[CONF_NAME]
            self._data["role"] = user_input["role"]
            self._data["vat"] = vat_data(
                (side, group, float(user_input[key]))
                for key, keys in _TARIFF_VAT_FIELDS.items()
                for side, group in keys
            )
            return await self.async_step_items()
        if self.source == SOURCE_RECONFIGURE and not self._data:
            subentry = self._get_reconfigure_subentry()
            self._title = subentry.title
            self._data = {**subentry.data, "items": list(subentry.data.get("items") or [])}
        vat = self._data.get("vat") or {}

        def vat_default(key: str, fallback: float) -> float:
            side, group = _TARIFF_VAT_FIELDS[key][0]
            return vat.get(f"{side.value}.{group.value}", fallback)

        return self.async_show_form(
            step_id="details",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=self._title or vol.UNDEFINED): str,
                    vol.Required("role", default=self._data.get("role", Role.CURRENT.value)): _tariff_select(
                        [role.value for role in Role], "tariff_role"
                    ),
                    vol.Required("vat_import", default=vat_default("vat_import", 20.0)): _PERCENT,
                    vol.Required("vat_export_energy", default=vat_default("vat_export_energy", 0.0)): _PERCENT,
                    vol.Required("vat_export_other", default=vat_default("vat_export_other", 20.0)): _PERCENT,
                }
            ),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_details(user_input)

    async def async_step_items(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Menu: add, edit, check against a bill, finish."""
        if self._candidates is None:
            # Newer price levels and successors of the templates the tariff was made from.
            if not self._templates and (self._data.get("meta") or {}).get("templates"):
                self._templates = await self.hass.async_add_executor_job(
                    load_templates, template_directories(self.hass)
                )
                self._templates += await self._async_elcom_levels()
            self._candidates = find_candidates(self._data, self._templates)
        options = ["add_item"]
        if self._candidates:
            options.insert(0, "update_prices")
        if self._items:
            options += ["edit_item", "check", "export_yaml"]
        if template_differences(self._data):
            options.append("reset_template")
        if self._items:
            options.append("finish")
        lines = "\n".join(
            f"- {_describe_item(TariffItem.from_dict(item), self.hass.config.language, currency_code(self.hass))}"
            for item in self._items
        ) or "–"
        check = self._check or {}
        return self.async_show_menu(
            step_id="items",
            menu_options=options,
            description_placeholders={
                "items": lines,
                "meta": _describe_meta(self._data.get("meta") or {}),
                "check": check.get("text", ""),
            },
        )

    async def _async_elcom_levels(self) -> list[Template]:
        """The ElCom years of a Swiss tariff (only with the consent to fetch market prices)."""
        origin = elcom.supply_of(self._data.get("meta") or {})
        coordinator = getattr(self._get_entry(), "runtime_data", None)
        if origin is None or coordinator is None or not coordinator.market_prices.enabled:
            return []
        supply, category = origin
        year = int((self._data.get("meta") or {}).get("year") or dt_util.now().year)
        try:
            return await elcom.async_levels(async_get_clientsession(self.hass), supply, category, year)
        except elcom.ElcomError:
            return []

    async def async_step_update_prices(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """New prices: one choice per template of the tariff, then the changes to confirm."""
        self._choice_queue = list(self._candidates or {})
        self._chosen, self._declined = [], []
        return await self.async_step_update_choose()

    async def async_step_update_choose(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """For one template: its newer price levels or a successor, or none of them."""
        if not self._choice_queue:
            return await self.async_step_update_confirm()
        origin = self._choice_queue[0]
        candidates = (self._candidates or {})[origin]
        if user_input is not None:
            choice = user_input["candidate"]
            picked = next((c for c in candidates if f"{c.kind}:{c.family}" == choice), None)
            if picked is None:
                self._declined += candidates
            else:
                self._chosen.append(picked)
            self._choice_queue.pop(0)
            return await self.async_step_update_choose()
        words = _CHECK_WORDS["de" if self.hass.config.language.startswith("de") else "en"]
        def label(candidate: Candidate) -> str:
            text = words[candidate.kind].format(name=candidate.name, date=candidate.starts.isoformat())
            if candidate.kind != CORRECTION and candidate.corrected is not None:
                text += f" ({words['with_correction']})"
            return text

        options = [
            selector.SelectOptionDict(value=f"{c.kind}:{c.family}", label=label(c)) for c in candidates
        ] + [selector.SelectOptionDict(value=_NO_TEMPLATE, label=words["none_of_them"])]
        return self.async_show_form(
            step_id="update_choose",
            data_schema=vol.Schema(
                {
                    vol.Required("candidate", default=f"{candidates[0].kind}:{candidates[0].family}"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
                    )
                }
            ),
            description_placeholders={"template": self._template_name(origin)},
        )

    def _template_name(self, family: str) -> str:
        """Name of the template a tariff was made from (its family if it is gone)."""
        origin = next((o for o in (self._data.get("meta") or {}).get("templates") or [] if o["family"] == family), {})
        level = next(
            (t for t in self._templates if t.family == family and t.meta.get("valid_from") == origin.get("valid_from")),
            None,
        )
        return level.name if level is not None else family

    async def async_step_reset_template(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The template items back to the values taken over (own items stay)."""
        if user_input is not None:
            self._data = reset_to_templates(self._data)
            self._check = None
            return await self.async_step_items()
        return self.async_show_form(
            step_id="reset_template",
            data_schema=vol.Schema({}),
            description_placeholders={"names": ", ".join(template_differences(self._data))},
        )

    async def async_step_update_confirm(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The changes of the chosen candidates (the old prices stay for the past)."""
        data, changes = apply_updates(self._data, self._chosen, self._declined, self._templates)
        if user_input is not None or not self._chosen:
            self._data = data
            self._candidates = find_candidates(data, self._templates)
            self._check = None
            return await self.async_step_items()
        return self.async_show_form(
            step_id="update_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "date": ", ".join(sorted({c.starts.isoformat() for c in self._chosen})),
                "changes": _describe_changes(changes, self.hass.config.language, currency_code(self.hass)),
            },
        )

    async def async_step_export_yaml(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The tariff as YAML to copy (nothing is changed)."""
        if user_input is not None:
            return await self.async_step_items()
        return self.async_show_form(
            step_id="export_yaml",
            data_schema=vol.Schema(
                {
                    vol.Optional("yaml", default=export_yaml(self._title, self._data, currency_code(self.hass))): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
        )

    async def async_step_add_item(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._edit_index = None
        return await self.async_step_item()

    async def async_step_edit_item(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Which item to change or remove."""
        if user_input is not None:
            self._edit_index = int(user_input["item"])
            return await self.async_step_item()
        options = [
            selector.SelectOptionDict(value=str(index), label=_describe_item(TariffItem.from_dict(item), self.hass.config.language, currency_code(self.hass)))
            for index, item in enumerate(self._items)
        ]
        return self.async_show_form(
            step_id="edit_item",
            data_schema=vol.Schema(
                {vol.Required("item"): selector.SelectSelector(selector.SelectSelectorConfig(options=options))}
            ),
        )

    async def async_step_item(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """One line of the bill."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("delete") and self._edit_index is not None:
                del self._items[self._edit_index]
                return await self.async_step_items()
            try:
                item = _item_from_input(user_input)
            except ValueError:
                item = None
                errors["month_prices"] = "month_prices_invalid"
            if item is None:
                pass
            elif (item.time_from is None) != (item.time_to is None):
                errors["base"] = "time_window_incomplete"
            else:
                if self._edit_index is None:
                    self._items.append(item.as_dict())
                else:
                    self._items[self._edit_index] = item.as_dict()
                return await self.async_step_items()
        current = dict(
            (self._items[self._edit_index] if self._edit_index is not None else {})
            if user_input is None
            else user_input
        )
        if isinstance(current.get("month_prices"), dict):
            current["month_prices"] = format_month_prices(sorted(current["month_prices"].items())) or None
        major, minor = _price_symbols(currency_code(self.hass))
        schema: dict = {
            vol.Required("name", default=current.get("name", vol.UNDEFINED)): str,
            vol.Required("side", default=current.get("side", Side.IMPORT.value)): _tariff_select(
                [side.value for side in Side], "tariff_side"
            ),
            vol.Required("group", default=current.get("group", Group.ENERGY.value)): _tariff_select(
                [group.value for group in Group], "tariff_group"
            ),
            vol.Required("unit", default=current.get("unit", Unit.KWH.value)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=await self._unit_options(major, minor), mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            # Without a default the frontend fills a required number with its minimum.
            vol.Required("price", default=current.get("price", 0.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-10_000, max=10_000, step="any", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional("factor_pct", default=current.get("factor_pct") or 0.0): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-100, max=1000, step="any", unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX
                )
            ),
            _optional("month_prices", current): selector.TextSelector(),
            vol.Optional("market", default=current.get("market") or "own"): _tariff_select(
                ["own", *REFERENCE_MARKETS], "tariff_market"
            ),
            vol.Optional("zero_when_negative", default=bool(current.get("zero_when_negative"))): selector.BooleanSelector(),
            _optional("valid_from", current): selector.DateSelector(),
            _optional("valid_to", current): selector.DateSelector(),
            vol.Optional("months", default=[str(m) for m in current.get("months") or []]): _tariff_select(
                [str(m) for m in range(1, 13)], "tariff_months", multiple=True
            ),
            vol.Optional("weekdays", default=[str(d) for d in current.get("weekdays") or []]): _tariff_select(
                [str(d) for d in range(7)], "tariff_weekdays", multiple=True
            ),
            _optional("time_from", current): selector.TimeSelector(),
            _optional("time_to", current): selector.TimeSelector(),
        }
        if self._edit_index is not None:
            schema[vol.Optional("delete", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="item",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"major": major, "minor": minor},
        )

    async def _unit_options(self, major: str, minor: str) -> list[selector.SelectOptionDict]:
        """Units with the symbols of the currency of Home Assistant."""
        strings = await async_get_translations(self.hass, self.hass.config.language, "selector", {DOMAIN})
        options = []
        for unit in Unit:
            template = strings.get(f"component.{DOMAIN}.selector.tariff_unit.options.{unit.value}", unit.value)
            options.append(
                selector.SelectOptionDict(value=unit.value, label=template.format(major=major, minor=minor))
            )
        return options

    async def async_step_check(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Compare with a bill: its period and amounts (incl. VAT)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            start = date.fromisoformat(user_input["start"])
            end = date.fromisoformat(user_input["end"])
            if end < start:
                errors["base"] = "period_invalid"
            else:
                self._check = await self._async_compare(
                    start, end, user_input.get("import_amount"), user_input.get("export_amount")
                )
                return await self.async_step_items()
        return self.async_show_form(
            step_id="check",
            data_schema=vol.Schema(
                {
                    vol.Required("start"): selector.DateSelector(),
                    vol.Required("end"): selector.DateSelector(),
                    vol.Optional("import_amount"): selector.NumberSelector(
                        selector.NumberSelectorConfig(step="any", unit_of_measurement=symbols(currency_code(self.hass))[0], mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Optional("export_amount"): selector.NumberSelector(
                        selector.NumberSelectorConfig(step="any", unit_of_measurement=symbols(currency_code(self.hass))[0], mode=selector.NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_compare(
        self, start: date, end: date, import_amount: float | None, export_amount: float | None
    ) -> dict[str, str]:
        """Bill of the tariff for the period from the recorded energy, against the bill."""
        entry = self._get_entry()
        config = entry.options or entry.data
        zone = dt_util.get_default_time_zone()
        coordinator = getattr(entry, "runtime_data", None)
        energy = await async_grid_energy(
            self.hass,
            datetime.combine(start, time(), zone),
            datetime.combine(end + timedelta(days=1), time(), zone),
            import_entity=config.get(CONF_GRID_IMPORT_ENERGY_ENTITY),
            export_entity=config.get(CONF_GRID_EXPORT_ENERGY_ENTITY),
            grid_power_entity=config[CONF_GRID_POWER_ENTITY],
            grid_inverted=config.get(CONF_GRID_POWER_INVERTED, False),
            quarters=coordinator.grid_quarters if coordinator is not None else None,
        )
        tariff = tariff_from_data(self._title, self._data)
        language = self.hass.config.language
        words = _CHECK_WORDS["de" if language.startswith("de") else "en"]
        if tariff.market_priced and not market_prices_usable(self.hass):
            return {"text": words["foreign_currency"]}
        market = None
        if tariff.dynamic and coordinator is not None:
            market = coordinator.market_prices.period_means(energy.lengths)
        references = coordinator.market_prices.references if coordinator is not None else None
        bill = compute_bill(tariff, start, end, energy.imported, energy.exported, market, references)
        money = symbols(currency_code(self.hass))[0]
        lines = [
            _check_line(language, Side.IMPORT, bill.import_kwh, bill.side_gross(tariff, Side.IMPORT), import_amount, money),
            _check_line(language, Side.EXPORT, bill.export_kwh, -bill.side_gross(tariff, Side.EXPORT), export_amount, money),
        ]
        text = "\n\n".join(lines) + "\n\n" + _check_groups(language, bill.groups, money)
        if bill.unpriced_kwh > 0:
            text += "\n\n" + words["unpriced"].format(kwh=_number(language, bill.unpriced_kwh, 1))
        return {"text": text}

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        data = {key: self._data[key] for key in ("role", "vat", "items")}
        if self._data.get("meta"):
            data["meta"] = self._data["meta"]
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=self._title, data=data
            )
        return self.async_create_entry(title=self._title, data=data)


def _item_from_input(user_input: dict[str, Any]) -> TariffItem:
    def clock(value: str | None) -> time | None:
        return time.fromisoformat(value) if value else None

    return TariffItem(
        name=user_input["name"].strip(),
        side=Side(user_input["side"]),
        group=Group(user_input["group"]),
        unit=Unit(user_input["unit"]),
        price=float(user_input["price"]),
        valid_from=date.fromisoformat(user_input["valid_from"]) if user_input.get("valid_from") else None,
        months=frozenset(int(m) for m in user_input.get("months") or ()),
        weekdays=frozenset(int(d) for d in user_input.get("weekdays") or ()),
        time_from=clock(user_input.get("time_from")),
        time_to=clock(user_input.get("time_to")),
        factor_pct=float(user_input.get("factor_pct") or 0.0),
        month_prices=parse_month_prices(user_input.get("month_prices") or ""),
        valid_to=date.fromisoformat(user_input["valid_to"]) if user_input.get("valid_to") else None,
        market=None if user_input.get("market") in (None, "own") or user_input["unit"] != Unit.MARKET_MONTH else user_input["market"],
        zero_when_negative=bool(user_input.get("zero_when_negative")),
    )


def _describe_changes(changes: UpdateChanges, language: str, currency: str = "EUR") -> str:
    """'- Energy: 10 → 11 ct/kWh' per item, new and dropped items, own changes."""
    words = _CHECK_WORDS["de" if language.startswith("de") else "en"]

    def price(value: float, unit: str) -> str:
        major, minor = symbols(currency)
        suffix = {"year": f"{major}/a", "percent": "%"}.get(unit, f"{minor}/kWh")
        return f"{_number(language, value, 4).rstrip('0').rstrip(',.')} {suffix}"

    lines = []
    for name, (old, new, unit) in changes.prices.items():
        if old is None:
            lines.append(f"- {name}: {price(new, unit)} ({words['new_item']})")
        elif old != new:
            lines.append(f"- {name}: {price(old, unit).split(' ')[0]} → {price(new, unit)}")
        else:
            lines.append(f"- {name}: {price(new, unit)} ({words['unchanged']})")
    lines += [f"- {name}: {words['dropped']}" for name in changes.removed]
    if changes.own_changes:
        lines.append(words["own_changes"].format(names=", ".join(changes.own_changes)))
    if changes.own_kept:
        lines.append(words["own_kept"].format(names=", ".join(changes.own_kept)))
    return "\n".join(lines) or "–"


def _describe_meta(meta: Mapping[str, Any]) -> str:
    """'Example Energy Ltd · 2026-01-01 – 2026-12-31 · price sheet 01/2026' (from an import)."""
    validity = ""
    if meta.get("valid_from") or meta.get("valid_to"):
        validity = f"{meta.get('valid_from') or '…'} – {meta.get('valid_to') or '…'}"
    operator = meta.get("grid_operator") if meta.get("grid_operator") != meta.get("supplier") else None
    parts = [meta.get("supplier"), operator, validity, meta.get("source")]
    return " · ".join(str(part) for part in parts if part)


def _describe_item(item: TariffItem, language: str, currency: str = "EUR") -> str:
    """'Grid: 6 ct/kWh (import, grid) · M 4,5,6 · 10:00–16:00'."""
    words = _CHECK_WORDS["de" if language.startswith("de") else "en"]

    def short(value: float) -> str:
        return _number(language, value, 4).rstrip("0").rstrip(",.")

    if item.unit.dynamic:
        price = words[item.unit]
        if item.factor_pct:
            price += f" × {short(1 + item.factor_pct / 100)}"
        if item.price:
            price += f" {'+' if item.price > 0 else '−'} {short(abs(item.price))} {symbols(currency)[1]}/kWh"
    else:
        major, minor = symbols(currency)
        price = f"{short(item.price)} {({Unit.KWH: f'{minor}/kWh', Unit.PERCENT: '%'}).get(item.unit, f'{major}/a')}"
    parts = [f"{item.name}: {price} ({words[item.side]}, {words[item.group]})"]
    if item.months:
        parts.append(f"{words['months']} {_ranges(item.months, 1)}")
    if item.weekdays:
        parts.append(f"{words['weekdays']} {_ranges(item.weekdays, 1, offset=1)}")
    if item.time_from and item.time_to:
        parts.append(f"{item.time_from:%H:%M}–{item.time_to:%H:%M}")
    if item.valid_from:
        parts.append(f"≥ {item.valid_from.isoformat()}")
    if item.valid_to:
        parts.append(f"≤ {item.valid_to.isoformat()}")
    if item.zero_when_negative:
        parts.append(words["zero_when_negative"])
    return " · ".join(parts)


_CHECK_WORDS = {
    "de": {
        Side.IMPORT: "Bezug", Side.EXPORT: "Einspeisung", "computed": "berechnet", "bill": "Rechnung",
        Group.ENERGY: "Energie", Group.GRID: "Netz", Group.LEVIES: "Abgaben", "net": "netto",
        "months": "Monate", "weekdays": "Wochentage", "zero_when_negative": "0 bei negativem Börsenpreis", "own": "eigene Vorlage", "no_template": "keine", "offer": "Angebot für Vertragsbeginn {month}",
        "new_item": "neu", "unchanged": "unverändert", "dropped": "entfällt",
        "own_changes": "Von dir geändert, wird ebenfalls ersetzt: {names}",
        "update": "Neue Preise ab {date} ({name})", "successor": "Nachfolgetarif {name} ab {date}",
        "correction": "Korrektur der Preise ab {date} ({name})",
        "with_correction": "mit Korrektur der bisherigen Preise",
        "own_kept": "Von dir geändert, bleibt: {names}",
        "none_of_them": "Keiner davon (nicht mehr anbieten)",
        Unit.SPOT: "Börsenpreis", Unit.MARKET_MONTH: "Monatsmarktpreis",
        "unpriced": "{kwh} kWh ohne Börsenpreis nicht berechnet (Börsenpreise abrufen einschalten oder warten, bis sie geladen sind).",
        "foreign_currency": "SLEMS unterstützt zum aktuellen Zeitpunkt nur Börsenpreise in Euro. Dieser Tarif hängt vom Börsenpreis ab und wird deshalb nicht berechnet.",
    },
    "en": {
        Side.IMPORT: "Import", Side.EXPORT: "Export", "computed": "computed", "bill": "bill",
        Group.ENERGY: "energy", Group.GRID: "grid", Group.LEVIES: "levies", "net": "net",
        "months": "months", "weekdays": "weekdays", "zero_when_negative": "0 at a negative market price", "own": "own template", "no_template": "none", "offer": "offer for contracts starting {month}",
        "new_item": "new", "unchanged": "unchanged", "dropped": "dropped",
        "own_changes": "Changed by you, replaced as well: {names}",
        "update": "New prices from {date} ({name})", "successor": "Successor {name} from {date}",
        "correction": "Correction of the prices from {date} ({name})",
        "with_correction": "with the correction of the current prices",
        "own_kept": "Changed by you, kept: {names}",
        "none_of_them": "None of them (do not offer again)",
        Unit.SPOT: "spot price", Unit.MARKET_MONTH: "monthly market price",
        "unpriced": "{kwh} kWh without a market price not computed (switch on fetching the market prices or wait until they are loaded).",
        "foreign_currency": "At the moment SLEMS only supports market prices in euro. This tariff follows the market price and is therefore not computed.",
    },
}


def _number(language: str, value: float, digits: int) -> str:
    text = f"{value:,.{digits}f}"
    if language.startswith("de"):
        text = text.replace(",", "\u202f").replace(".", ",")
    return text


def _ranges(values: frozenset[int], step: int, offset: int = 0) -> str:
    """'4–9' or '1, 3, 5–7' (``offset`` shifts 0-based weekdays to 1–7)."""
    ordered = sorted(value + offset for value in values)
    parts: list[str] = []
    start = previous = ordered[0]
    for value in [*ordered[1:], None]:
        if value is not None and value == previous + step:
            previous = value
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        if value is not None:
            start = previous = value
    return ", ".join(parts)


def _check_line(
    language: str, side: Side, kwh: float, computed: float, billed: float | None, money: str = "€"
) -> str:
    """'Import: 412.3 kWh, computed 98.20 €, bill 97.90 € (+0.3 %)'."""
    words = _CHECK_WORDS["de" if language.startswith("de") else "en"]
    text = f"**{words[side]}**: {_number(language, kwh, 1)} kWh, {words['computed']} {_number(language, computed, 2)} {money}"
    if billed:
        deviation = (computed - billed) / abs(billed) * 100
        text += f", {words['bill']} {_number(language, billed, 2)} {money} ({'+' if deviation >= 0 else ''}{_number(language, deviation, 1)} %)"
    return text


def _check_groups(language: str, bill_groups: dict[tuple[Side, Group], float], money: str = "€") -> str:
    """'Import net: energy 54.27 €, grid 17.27 € · Export net: energy −24.07 €'."""
    words = _CHECK_WORDS["de" if language.startswith("de") else "en"]
    parts = []
    for side in Side:
        amounts = [
            f"{words[group]} {_number(language, amount, 2)} {money}"
            for (item_side, group), amount in sorted(bill_groups.items())
            if item_side is side
        ]
        if amounts:
            parts.append(f"{words[side]} {words['net']}: " + ", ".join(amounts))
    return " · ".join(parts)
