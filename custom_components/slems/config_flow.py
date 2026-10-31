"""Config flow for SLEMS.

The main entry holds the system level measurements (grid, PV, weather). Every
battery is a config subentry, so batteries can be added, edited and removed at
any time without touching the rest of the configuration.

A Marstek Venus can be searched in the network when adding a battery (see
discovery).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
import os
import socket
from typing import Any

import voluptuous as vol

from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SOURCE_RECONFIGURE,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from modbus_connection import ModbusError, ModbusTcpParams

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
    CONF_INPUT_ENTITY,
    CONF_INPUT_SETPOINT_ENTITY,
    CONF_MAX_OUTPUT_W,
    CONF_OUTPUT_ENTITY,
    CONF_OUTPUT_SETPOINT_ENTITY,
    CONF_OWN_PV,
    CONF_OWN_PV_ENTITY,
    CONF_OWN_PV_FORECAST_ENTRY,
    CONF_PASSES_PV_THROUGH,
    CONF_REMOVE_FROM_PV,
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
    CONF_MODE_ENTITY,
    CONF_HOT_WATER,
    CONF_HOT_WATER_STATES,
    CONF_METER_DEVICE,
    CONF_GRID_DEVICE,
    CONF_GRID_PHASE_ENTITIES,
    CONF_PV_DEVICES,
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
    CONF_SELF_TEST_ON_SETUP,
    CONF_SOC_ENTITY,
    CONF_UNIT_ID,
    CONF_PROFILE_ID,
    CONF_PROFILE_YAML,
    CONF_TRANSPORT,
    CONF_WEATHER_ENTITY,
    DEFAULT_MODBUS_PORT,
    DEV_ONLY_PROFILES,
    DEV_PROFILES_ENV,
    ModbusTransport,
    DEFAULT_UNIT_ID,
    DOMAIN,
    SUBENTRY_TYPE_BATTERY,
    SUBENTRY_TYPE_CONSUMER,
    BatteryModel,
    ConsumerType,
    ControlMode,
    EfficiencyMode,
)
from .battery_profiles import (
    BatteryProfile,
    ProfileError,
    bundled_profile_text,
    bundled_profiles,
    parse_profile,
    profile_of,
    profile_text,
)
from .discovery import async_find_batteries
from .drivers.ha_entities import EntityBatteryConfig
from .drivers.marstek_venus_e3 import HARDWARE_MAX_POWER_W, MarstekVenusE3Driver
from .drivers.profile import (
    HaUnitTransport,
    ProfileDriver,
    ProfileTransport,
    connect_new,
    own_link,
    read_profile,
)
from .profile_check import BLOCK, ProbeReading, blocked, check, describe_preview, describe_reading, preview
from .self_test import self_test_power_w
from .entity_match import (
    CAPACITY,
    EntityInfo,
    battery_device_fit,
    consumer_device_fit,
    grid_device_fit,
    match_battery_entities,
    match_consumer_entities,
    match_grid_entities,
    match_outdoor_temperature,
    match_pv_power,
    means_hot_water,
    meter_device_fit,
    pv_device_fit,
    suggest_consumer_control,
    suggest_control,
    suggest_control_mode,
    suggest_hot_water,
    suggest_mode_options,
    suggest_remote_options,
    suggest_start_options,
)
from .energy_dashboard import EnergyDashboard, async_energy_dashboard
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
from .util import entity_ids, state_as_kwh, state_as_watts

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
    BatteryControl.OUTPUT: (
        CONF_OUTPUT_SETPOINT_ENTITY, CONF_INPUT_SETPOINT_ENTITY, CONF_MODE_ENTITY,
        CONF_MODE_CHARGE, CONF_MODE_DISCHARGE, CONF_MODE_STANDBY, CONF_MODE_AUTO,
    ),
}
_OWN_PV_KEYS = (
    CONF_OWN_PV_ENTITY, CONF_OUTPUT_ENTITY, CONF_INPUT_ENTITY, CONF_MAX_OUTPUT_W,
    CONF_PASSES_PV_THROUGH, CONF_OWN_PV_FORECAST_ENTRY,
)
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
    defaults = {**defaults}
    if pv := entity_ids(defaults.get(CONF_PV_POWER_ENTITY)):
        # Stored as one entity id before several PV systems were possible.
        defaults[CONF_PV_POWER_ENTITY] = list(pv)
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
            **{optional(key): _POWER_SENSOR for key in CONF_GRID_PHASE_ENTITIES},
            vol.Required(
                CONF_GRID_MODBUS, default=defaults.get(CONF_GRID_MODBUS, False)
            ): selector.BooleanSelector(),
            optional(CONF_PV_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER, multiple=True)
            ),
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
    """System steps shared by config and options flow: the devices of the smart meter and
    the PV systems, the system settings suggested from them, and reading the grid power
    over Modbus."""

    hass: HomeAssistant
    _system: dict[str, Any]
    _meters: list[tuple[SunSpecMeter, float | None]]
    # The stored system settings (empty at the first setup) and the suggestions.
    _current: dict[str, Any]
    _suggested: dict[str, Any]
    _devices_changed: bool

    def _async_finish_system(self) -> ConfigFlowResult:
        raise NotImplementedError

    def _entry(self) -> ConfigEntry | None:
        return None

    async def _async_devices_step(
        self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any]
    ) -> ConfigFlowResult:
        """Smart meter and PV devices; the next step shows what SLEMS suggests from them."""
        dashboard = await async_energy_dashboard(self.hass)
        grid, pv = _system_devices(self.hass, current, dashboard)
        if user_input is not None:
            chosen_grid = user_input.get(CONF_GRID_DEVICE)
            chosen_pv = list(user_input.get(CONF_PV_DEVICES) or [])
            self._current = current
            self._devices_changed = (chosen_grid, sorted(chosen_pv)) != (grid, sorted(pv))
            self._suggested = await _async_suggest_system(self.hass, self._entry(), chosen_grid, chosen_pv, dashboard)
            return await self.async_step_system()
        used = _used_entities(self._entry(), None, system=False)
        candidates = _device_candidates(self.hass, used, (grid, *pv))
        fields: dict = {}
        if grids := _device_select(candidates, grid_device_fit, grid, power_of=_grid_power_of):
            fields[vol.Optional(CONF_GRID_DEVICE, description={"suggested_value": grid})] = grids
        if pvs := _device_select(candidates, pv_device_fit, pv, power_of=match_pv_power, multiple=True):
            fields[vol.Optional(CONF_PV_DEVICES, description={"suggested_value": pv})] = pvs
        if not fields:
            # No devices to choose from: straight to the settings.
            return await self._async_devices_step(step_id, {}, current)
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema(fields))

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The system settings, suggested from the devices and the energy dashboard."""
        errors: dict[str, str] = {}
        twice: list[str] = []
        if user_input is not None:
            owners = _own_pv_owners(self._entry())
            twice = [
                f"{entity_id} ({owners[entity_id]})"
                for entity_id in entity_ids(user_input.get(CONF_PV_POWER_ENTITY))
                if entity_id in owners
            ]
            if not twice:
                return await self._async_system_done(user_input, self._current)
            errors[CONF_PV_POWER_ENTITY] = "pv_is_own_pv"
            defaults = user_input
        elif self._devices_changed:
            defaults = {**self._current, **self._suggested}
        else:
            defaults = {**self._suggested, **self._current}
        return self.async_show_form(
            step_id="system",
            data_schema=await _async_system_schema(self.hass, defaults),
            errors=errors,
            description_placeholders={
                **_now_placeholders(self.hass, defaults),
                "own_pv": ", ".join(twice) or "-",
            },
        )

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
        return await self._async_devices_step("user", user_input, {})

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
        return await self._async_devices_step("init", user_input, current)

    def _entry(self) -> ConfigEntry | None:
        return self.config_entry


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a battery."""

    def __init__(self) -> None:
        self._model: BatteryModel | None = None
        self._found: list[tuple[str, float]] = []
        self._host: str | None = None
        # Battery from HA entities: data collected over the steps.
        self._data: dict[str, Any] = {}
        # PV systems to remove on saving: they are the battery's own PV.
        self._remove_from_pv: list[str] = []
        # Battery from a Modbus profile: the profile, its YAML (own profiles),
        # address and transport, what was read there and name and limits.
        self._profile: BatteryProfile | None = None
        self._yaml: str | None = None
        self._connection: dict[str, Any] = {}
        self._reading: ProbeReading | None = None
        self._limits: dict[str, Any] = {}
        # Reconfigured: only name and limits change (nothing read, the trial stays).
        self._limits_only = False
        # The address shared with a paused battery the user was told about.
        self._shared_address: tuple[Any, ...] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the battery model."""
        if user_input is not None:
            self._model = BatteryModel(user_input[CONF_MODEL])
            return await self._async_step_model(None)
        # A battery of a hybrid inverter or of the energy dashboard is one from entities.
        _candidates, preferred, _dashboard = await self._async_battery_devices()
        model = BatteryModel.HA_ENTITIES if preferred else BatteryModel.MARSTEK_VENUS_E3
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL, default=model.value
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
        if self._model is BatteryModel.MODBUS_PROFILE:
            if self.source == SOURCE_RECONFIGURE:
                return await self.async_step_profile_reconfigure()
            return await self.async_step_profile()
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
                **self._self_test_schema(),
            }
        )
        return self.async_show_form(
            step_id="marstek_venus_e3", data_schema=schema, errors=errors
        )

    # --- battery from a Modbus profile ------------------------------------------

    async def async_step_profile(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose a shipped profile, paste an own one or copy that of another battery;
        with nothing to choose straight to the own profile."""
        bundled = self._offered_profiles(await self.hass.async_add_executor_job(bundled_profiles))
        copies = self._profile_copies()
        if user_input is None and not bundled and not copies:
            user_input = {CONF_PROFILE: OWN_PROFILE}
        if user_input is not None:
            choice = user_input[CONF_PROFILE]
            if choice == OWN_PROFILE:
                if self._profile is None or self._profile.bundled:
                    self._yaml = None
                return await self.async_step_profile_yaml()
            if choice in copies:
                self._yaml = copies[choice][0]
                return await self.async_step_profile_yaml()
            self._profile, self._yaml = bundled[choice], None
            return await self.async_step_profile_connection()
        words = _profile_words(self.hass.config.language)
        options = [
            selector.SelectOptionDict(
                value=profile.id, label=f"{profile.name}{words['unverified'] if profile.unverified else ''}"
            )
            for profile in bundled.values()
        ]
        options += [
            selector.SelectOptionDict(value=key, label=words["copy"].format(battery=title, profile=profile.name))
            for key, (_, title, profile) in copies.items()
        ]
        options.append(selector.SelectOptionDict(value=OWN_PROFILE, label=OWN_PROFILE))
        if self._profile is not None:
            default = OWN_PROFILE if self._yaml else self._profile.id
        else:
            default = next(iter(bundled), OWN_PROFILE)
        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE, default=default): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, translation_key=CONF_PROFILE)
                    )
                }
            ),
            description_placeholders={"prompt_url": battery_profile_prompt_url(self.hass.config.language)},
        )

    async def async_step_profile_yaml(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Paste an own profile; errors with line and key."""
        errors: dict[str, str] = {}
        placeholders = {"where": "", "detail": ""}
        if user_input is not None:
            text = profile_text(user_input[CONF_PROFILE_YAML])
            self._yaml = text
            bundled = await self.hass.async_add_executor_job(bundled_profiles)
            try:
                profile = parse_profile(text)
            except ProfileError as err:
                errors["base"] = f"profile_{err.code}"
                placeholders = {"where": _profile_where(err, self.hass.config.language), "detail": err.detail}
                if (shipped := _shipped_id(text)) in bundled:
                    # A shipped profile pasted unchanged fails only on what own profiles must not have.
                    errors["base"] = "profile_bundled_id"
                    placeholders = {"where": "", "detail": shipped}
            else:
                if profile.id in bundled:
                    errors["base"] = "profile_bundled_id"
                    placeholders["detail"] = profile.id
                else:
                    self._profile = profile
                    return await self.async_step_profile_connection()
        return self.async_show_form(
            step_id="profile_yaml",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE_YAML, default=self._yaml or vol.UNDEFINED): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                **placeholders, "prompt_url": battery_profile_prompt_url(self.hass.config.language)
            },
        )

    async def async_step_profile_connection(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> SubentryFlowResult:
        """Address and transport; then every register of the profile is read once."""
        assert self._profile is not None
        errors = dict(errors or {})
        placeholders = {"battery": ""}
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            user_input[CONF_UNIT_ID] = int(user_input[CONF_UNIT_ID])
            self._connection = user_input
            address = tuple(user_input[key] for key in (CONF_HOST, CONF_PORT, CONF_UNIT_ID))
            if (other := self._battery_at(user_input)) is not None and not (
                self._may_share(other) and self._shared_address == address
            ):
                placeholders["battery"] = other.title
                if self._may_share(other):
                    # Shown once; the same address again goes on.
                    errors["base"] = "address_shared"
                    self._shared_address = address
                else:
                    errors["base"] = "address_in_use"
            elif await self._async_read_profile():
                return await self.async_step_profile_read()
            else:
                errors["base"] = "cannot_connect"
        defaults = self._connection or self._defaults(None)
        connection = self._profile.connection
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, connection.port)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, connection.unit_id)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=247, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_TRANSPORT, default=defaults.get(CONF_TRANSPORT, ModbusTransport.OWN.value)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[transport.value for transport in ModbusTransport], translation_key=CONF_TRANSPORT
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="profile_connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={"profile": self._profile.name, **placeholders},
        )

    async def async_step_profile_read(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The values read; go on only if nothing blocks saving."""
        assert self._profile is not None and self._reading is not None
        marks = check(self._profile, self._reading)
        options = [] if blocked(marks) else ["profile_limits"]
        if not self._profile.bundled:
            options.append("profile_yaml")
        if await self._async_profile_choice():
            options.append("profile")
        options += ["profile_connection", "profile_reread"]
        return self.async_show_menu(
            step_id="profile_read",
            menu_options=options,
            description_placeholders={
                "profile": self._profile.name,
                "reading": describe_reading(self._profile, self._reading, marks, self.hass.config.language),
            },
        )

    async def async_step_profile_reread(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Read the registers again with the same connection."""
        if await self._async_read_profile():
            return await self.async_step_profile_read()
        return await self.async_step_profile_connection(None, errors={"base": "cannot_connect"})

    async def async_step_profile_limits(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, limits and release state; after it the write preview (not when only these change)."""
        assert self._profile is not None
        profile = self._profile
        if user_input is not None:
            self._limits = user_input
            if self._limits_only:
                return self._async_finish_profile()
            return await self.async_step_profile_preview()
        defaults = self._limits or self._defaults(None)
        states = [state for state in ReleaseState if state in profile.control.release]
        default_state = defaults.get(CONF_RELEASE_STATE, ReleaseState.AUTO.value)
        if ReleaseState(default_state) not in states:
            default_state = states[0].value
        limit = max(profile.limits.max_charge_w, profile.limits.max_discharge_w)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, profile.name)): str,
                **self._limits_schema(
                    {
                        CONF_MAX_CHARGE_POWER_W: profile.limits.max_charge_w,
                        CONF_MAX_DISCHARGE_POWER_W: profile.limits.max_discharge_w,
                        **defaults,
                    },
                    limit,
                    list(EfficiencyMode),
                ),
                vol.Required(CONF_RELEASE_STATE, default=default_state): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[state.value for state in states], translation_key=CONF_RELEASE_STATE
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="profile_limits", data_schema=schema, description_placeholders={"profile": profile.name}
        )

    async def async_step_profile_preview(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """What SLEMS would write; an own profile needs the confirmation that its write
        registers were compared with the manual, an unverified shipped one that this is known."""
        assert self._profile is not None and self._reading is not None
        profile = self._profile
        errors: dict[str, str] = {}
        if user_input is not None:
            if (checked := _profile_check(profile)) is None or user_input.get(checked):
                if self._other_profile_users():
                    return await self.async_step_profile_others()
                return self._async_finish_profile()
            errors["base"] = f"not_{checked}"
        state = ReleaseState(self._limits[CONF_RELEASE_STATE])
        test_w = self_test_power_w(
            min(self._limits[CONF_MAX_CHARGE_POWER_W], profile.limits.max_charge_w),
            min(self._limits[CONF_MAX_DISCHARGE_POWER_W], profile.limits.max_discharge_w),
        )
        phases = preview(profile, self._reading.writes, test_w, state)
        checked = _profile_check(profile)
        fields = {} if checked is None else {vol.Required(checked, default=False): selector.BooleanSelector()}
        return self.async_show_form(
            step_id="profile_preview",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "profile": profile.name,
                "preview": describe_preview(profile, phases, test_w, state, self.hass.config.language),
            },
        )

    async def async_step_profile_others(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the same profile of other batteries as well; each is read first and
        left as it is if a write register is not at rest."""
        assert self._profile is not None and self._yaml is not None
        others = self._other_profile_users()
        errors: dict[str, str] = {}
        placeholders = {"batteries": ""}
        selected = [key for key, (_, same) in others.items() if same]
        if user_input is not None:
            selected = [key for key in user_input[CONF_BATTERIES] if key in others]
            failed = [key for key in selected if not await self._async_still_at_rest(others[key][0])]
            if not failed:
                entry = self._get_entry()
                for key in selected:
                    subentry = others[key][0]
                    self.hass.config_entries.async_update_subentry(
                        entry,
                        subentry,
                        data={**subentry.data, CONF_PROFILE_ID: self._profile.id, CONF_PROFILE_YAML: self._yaml},
                    )
                return self._async_finish_profile()
            errors["base"] = "others_not_at_rest"
            placeholders["batteries"] = ", ".join(others[key][0].title for key in failed)
            selected = [key for key in selected if key not in failed]
        words = _profile_words(self.hass.config.language)
        options = [
            selector.SelectOptionDict(value=key, label=f"{subentry.title} ({words['same' if same else 'differs']})")
            for key, (subentry, same) in others.items()
        ]
        return self.async_show_form(
            step_id="profile_others",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_BATTERIES, default=selected): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"profile": self._profile.name, **placeholders},
        )

    async def async_step_profile_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the profile (read again, preview, trial), only name and limits, or show the profile."""
        data = self._get_reconfigure_subentry().data
        self._yaml = data.get(CONF_PROFILE_YAML)
        bundled = await self.hass.async_add_executor_job(bundled_profiles)
        try:
            self._profile = profile_of(data, bundled)
        except ProfileError:
            # E.g. checked more strictly by a newer SLEMS: to be corrected first.
            self._profile = None
            if self._yaml:
                return await self.async_step_profile_yaml({CONF_PROFILE_YAML: self._yaml})
            return await self.async_step_profile()
        return self.async_show_menu(
            step_id="profile_reconfigure",
            menu_options=["profile", "profile_settings", "profile_export"],
            description_placeholders={"profile": self._profile.name},
        )

    async def async_step_profile_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Only name and limits: nothing is read and the trial stays."""
        self._limits_only = True
        return await self.async_step_profile_limits()

    async def async_step_profile_export(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The profile as YAML to copy (nothing is changed)."""
        assert self._profile is not None
        if user_input is not None:
            return await self.async_step_profile_reconfigure()
        text = self._yaml or await self.hass.async_add_executor_job(bundled_profile_text, self._profile.id)
        return self.async_show_form(
            step_id="profile_export",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PROFILE_YAML, default=text): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
                }
            ),
            description_placeholders={"profile": self._profile.name},
        )

    async def _async_profile_choice(self) -> bool:
        """Whether there is a shipped profile or a copy to choose (else only an own profile)."""
        bundled = await self.hass.async_add_executor_job(bundled_profiles)
        return bool(self._offered_profiles(bundled) or self._profile_copies())

    def _offered_profiles(self, bundled: dict[str, BatteryProfile]) -> dict[str, BatteryProfile]:
        """The shipped profiles to choose from: those for dev only while DEV_PROFILES_ENV
        is set, and always the one the reconfigured battery has."""
        current = (
            self._get_reconfigure_subentry().data.get(CONF_PROFILE_ID) if self.source == SOURCE_RECONFIGURE else None
        )
        dev = bool(os.environ.get(DEV_PROFILES_ENV))
        return {
            profile_id: profile
            for profile_id, profile in bundled.items()
            if dev or profile_id not in DEV_ONLY_PROFILES or profile_id == current
        }

    def _profile_copies(self) -> dict[str, tuple[str, str, BatteryProfile]]:
        """Own profiles of the other batteries: choice -> (YAML, battery, profile)."""
        copies = {}
        own = self._get_reconfigure_subentry().subentry_id if self.source == SOURCE_RECONFIGURE else None
        for subentry in self._get_entry().subentries.values():
            text = subentry.data.get(CONF_PROFILE_YAML)
            if subentry.subentry_type != SUBENTRY_TYPE_BATTERY or not text or subentry.subentry_id == own:
                continue
            try:
                profile = parse_profile(text)
            except ProfileError:
                continue
            copies[f"{COPY_PREFIX}{subentry.subentry_id}"] = (text, subentry.title, profile)
        return copies

    def _other_profile_users(self) -> dict[str, tuple[ConfigSubentry, bool]]:
        """When the own profile of a reconfigured battery changed: the other batteries
        with a profile of the same id (subentry id -> subentry, same content as before)."""
        if self.source != SOURCE_RECONFIGURE or self._profile is None or self._profile.bundled:
            return {}
        own = self._get_reconfigure_subentry()
        before = own.data.get(CONF_PROFILE_YAML)
        try:
            old = parse_profile(before) if before else None
        except ProfileError:
            old = None
        if old is None or old.key == self._profile.key:
            return {}
        others = {}
        for subentry in self._get_entry().subentries.values():
            text = subentry.data.get(CONF_PROFILE_YAML)
            if subentry.subentry_type != SUBENTRY_TYPE_BATTERY or not text or subentry.subentry_id == own.subentry_id:
                continue
            try:
                profile = parse_profile(text)
            except ProfileError:
                continue
            if profile.id == old.id:
                others[subentry.subentry_id] = (subentry, profile.key == old.key)
        return others

    def _battery_at(self, connection: dict[str, Any]) -> ConfigSubentry | None:
        """Another battery at the same address (host, port, unit)."""
        own = self._get_reconfigure_subentry().subentry_id if self.source == SOURCE_RECONFIGURE else None
        for subentry in self._get_entry().subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_BATTERY or subentry.subentry_id == own:
                continue
            if all(subentry.data.get(key) == connection[key] for key in (CONF_HOST, CONF_PORT, CONF_UNIT_ID)):
                return subentry
        return None

    def _may_share(self, other: ConfigSubentry) -> bool:
        """For a comparison of drivers: with the debug log of SLEMS on, a battery from
        a profile may use the address of a paused battery of another model; the
        coordinator keeps one of the two paused while the other communicates."""
        if not _LOGGER.isEnabledFor(logging.DEBUG) or other.data.get(CONF_MODEL) == self._model:
            return False
        coordinator = getattr(self._get_entry(), "runtime_data", None)
        return any(
            battery.subentry_id == other.subentry_id and battery.communication_paused
            for battery in getattr(coordinator, "batteries", ())
        )

    def _running_transport(self, subentry_id: str) -> ProfileTransport | None:
        """The transport of a running battery from a profile (it holds the connection)."""
        coordinator = getattr(self._get_entry(), "runtime_data", None)
        for battery in getattr(coordinator, "batteries", ()):
            if battery.subentry_id == subentry_id and isinstance(battery.driver, ProfileDriver):
                return battery.driver.transport
        return None

    async def _async_read(
        self, profile: BatteryProfile, connection: Mapping[str, Any], subentry_id: str | None
    ) -> ProbeReading | None:
        """Read every register of ``profile``; over the running battery's transport
        when ``subentry_id`` is that battery at the same address, else over a
        connection of its own with the chosen transport."""
        if subentry_id is not None and (transport := self._running_transport(subentry_id)) is not None:
            return await read_profile(profile, transport)
        host, port, unit_id = connection[CONF_HOST], connection[CONF_PORT], connection[CONF_UNIT_ID]
        if ModbusTransport(connection.get(CONF_TRANSPORT, ModbusTransport.OWN)) is ModbusTransport.OWN:
            if (link := await connect_new(lambda: own_link(host, port, unit_id, profile))) is None:
                return None
            try:
                return await read_profile(profile, link)
            finally:
                await link.close()
        try:
            async with async_get_temporary_unit(self.hass, ModbusTcpParams(host=host, port=port), unit_id) as unit:
                return await read_profile(
                    profile,
                    HaUnitTransport(
                        lambda: unit,
                        message_spacing_s=profile.connection.message_spacing_s,
                        timeout_s=profile.connection.timeout_s,
                    ),
                )
        except (HomeAssistantError, ModbusError, OSError) as err:
            _LOGGER.debug("Reading the profile %s failed: %s", profile.id, err)
            return None

    async def _async_read_profile(self) -> bool:
        """Read the chosen profile at the chosen address (False: no answer)."""
        assert self._profile is not None
        running = None
        if self._same_connection(self._connection):
            running = self._get_reconfigure_subentry().subentry_id
        self._reading = await self._async_read(self._profile, self._connection, running)
        return self._reading is not None

    async def _async_still_at_rest(self, subentry: ConfigSubentry) -> bool:
        """Whether another battery answers the new profile with its write registers at rest."""
        assert self._profile is not None
        reading = await self._async_read(self._profile, subentry.data, subentry.subentry_id)
        if reading is None:
            return False
        marks = check(self._profile, reading)
        return "probe" not in marks and not any(
            key.startswith("write_") and mark.level == BLOCK for key, mark in marks.items()
        )

    def _async_finish_profile(self) -> SubentryFlowResult:
        assert self._profile is not None
        if self.source == SOURCE_RECONFIGURE:
            current = self._get_reconfigure_subentry()
            base: dict[str, Any] = {CONF_NAME: current.title, **current.data}
        else:
            base = {}
        user_input = {**base, **self._connection, **self._limits, CONF_PROFILE_ID: self._profile.id}
        user_input.pop(CONF_PROFILE_YAML, None)
        if self._yaml and not self._profile.bundled:
            user_input[CONF_PROFILE_YAML] = self._yaml
        return self._async_finish(user_input)

    async def _async_battery_devices(self) -> tuple[dict[str, _DeviceCandidate], set[str], EnergyDashboard]:
        """Devices with entities, those that are a battery SLEMS does not have yet and are a PV system of the
        setup (a hybrid inverter) or a battery of the energy dashboard, and the dashboard."""
        dashboard = await async_energy_dashboard(self.hass)
        entry = self._get_entry()
        candidates = _device_candidates(self.hass, _used_entities(entry, None, system=False), ())
        system = entry.options or entry.data
        sources = [
            *entity_ids(system.get(CONF_PV_POWER_ENTITY)),
            *(entity_id for battery in dashboard.batteries for entity_id in (battery.soc, battery.power) if entity_id),
        ]
        preferred = {
            device
            for entity_id in sources
            if (device := _device_of(self.hass, entity_id)) in candidates
            and battery_device_fit(candidates[device].infos) > 0
        }
        return candidates, preferred, dashboard

    async def async_step_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the battery's device; its entities are suggested for the next steps."""
        candidates, preferred, dashboard = await self._async_battery_devices()
        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE)
            if device_id:
                self._data = _suggest_from_device(self.hass, device_id)
            used = _used_entities(self._get_entry(), None, system=True)
            self._data.update(_battery_from_dashboard(self.hass, dashboard, device_id, self._data, used))
            return await self.async_step_ha_entities()
        devices = _device_select(
            candidates, battery_device_fit, None, power_of=_battery_power_of, boost=preferred
        )
        if devices is None:
            return await self.async_step_ha_device({})
        # The battery of a hybrid inverter or of the energy dashboard first.
        best = next((option["value"] for option in devices.config["options"] if option["value"] in preferred), None)
        return self.async_show_form(
            step_id="ha_device",
            data_schema=vol.Schema({vol.Optional(CONF_DEVICE, description={"suggested_value": best}): devices}),
        )

    async def async_step_ha_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Sensors of the battery and the way SLEMS controls it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = _merge(self._data, user_input, _ENTITY_STEP_KEYS)
            control = BatteryControl(self._data[CONF_BATTERY_CONTROL])
            own_pv = self._data.get(CONF_OWN_PV, False)
            if control is BatteryControl.OUTPUT and not own_pv:
                errors[CONF_BATTERY_CONTROL] = "output_needs_own_pv"
            elif control is not BatteryControl.NONE and not own_pv and not self._data.get(CONF_POWER_ENTITY):
                errors[CONF_POWER_ENTITY] = "power_required"
            elif own_pv:
                return await self.async_step_ha_own_pv()
            else:
                return await self._async_step_control()

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
                    CONF_OWN_PV, default=data.get(CONF_OWN_PV, False)
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

    async def _async_step_control(self) -> SubentryFlowResult:
        control = BatteryControl(self._data[CONF_BATTERY_CONTROL])
        if control is BatteryControl.SETPOINT:
            return await self.async_step_ha_setpoint()
        if control is BatteryControl.SPLIT:
            return await self.async_step_ha_split()
        if control is BatteryControl.SCRIPT:
            return await self.async_step_ha_script()
        if control is BatteryControl.OUTPUT:
            return await self.async_step_ha_output()
        return await self.async_step_ha_limits()

    async def async_step_ha_own_pv(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Own PV modules behind the battery: their power, the output and its limit.

        Entered as a PV system as well, their production would count twice;
        the step shows that and offers to remove them there.
        """
        errors: dict[str, str] = {}
        twice = []
        if user_input is not None:
            self._data = _merge(self._data, user_input, _OWN_PV_KEYS)
            if not self._data.get(CONF_PASSES_PV_THROUGH):
                self._data.pop(CONF_PASSES_PV_THROUGH, None)
            twice = self._counted_as_pv()
            if twice and not user_input.get(CONF_REMOVE_FROM_PV):
                errors["base"] = "own_pv_counted_twice"
            else:
                self._remove_from_pv = twice
                return await self._async_step_control()
        else:
            twice = self._counted_as_pv()
        data = self._data
        schema: dict = {
            vol.Required(
                CONF_OWN_PV_ENTITY, default=data.get(CONF_OWN_PV_ENTITY, vol.UNDEFINED)
            ): _POWER_SENSOR,
            vol.Required(
                CONF_OUTPUT_ENTITY, default=data.get(CONF_OUTPUT_ENTITY, vol.UNDEFINED)
            ): _POWER_SENSOR,
            _optional(CONF_INPUT_ENTITY, data): _POWER_SENSOR,
            vol.Required(
                CONF_MAX_OUTPUT_W, default=data.get(CONF_MAX_OUTPUT_W, 800)
            ): _watts(100_000),
            vol.Required(
                CONF_PASSES_PV_THROUGH, default=data.get(CONF_PASSES_PV_THROUGH, False)
            ): selector.BooleanSelector(),
        }
        if providers := await async_forecast_provider_entries(self.hass):
            schema[_optional(CONF_OWN_PV_FORECAST_ENTRY, data)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=entry.entry_id, label=f"{entry.title} ({entry.domain})"
                        )
                        for entry in providers
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        if twice:
            schema[vol.Required(CONF_REMOVE_FROM_PV, default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="ha_own_pv",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"pv_systems": ", ".join(twice) or "-"},
        )

    def _counted_as_pv(self) -> list[str]:
        """The battery's own PV or output entities entered as a PV system."""
        entry = self._get_entry()
        system = entry.options or entry.data
        own = {self._data.get(CONF_OWN_PV_ENTITY), self._data.get(CONF_OUTPUT_ENTITY)}
        return [entity_id for entity_id in entity_ids(system.get(CONF_PV_POWER_ENTITY)) if entity_id in own]

    def _remove_pv_systems(self) -> None:
        """Remove the PV systems that are the battery's own PV."""
        if not self._remove_from_pv:
            return
        entry = self._get_entry()
        field = "options" if entry.options else "data"
        system = dict(getattr(entry, field))
        system[CONF_PV_POWER_ENTITY] = [
            entity_id
            for entity_id in entity_ids(system.get(CONF_PV_POWER_ENTITY))
            if entity_id not in self._remove_from_pv
        ]
        self.hass.config_entries.async_update_entry(entry, **{field: system})
        self._remove_from_pv = []

    async def async_step_ha_output(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Number entities with the output (and the input) of a battery with own
        PV, optionally a mode select."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = _merge(
                self._data,
                user_input,
                (CONF_OUTPUT_SETPOINT_ENTITY, CONF_INPUT_SETPOINT_ENTITY, CONF_MODE_ENTITY, CONF_REMOTE_ENTITY),
            )
            if self._data.get(CONF_INPUT_SETPOINT_ENTITY) and not self._data.get(CONF_INPUT_ENTITY):
                errors[CONF_INPUT_SETPOINT_ENTITY] = "input_needs_sensor"
            else:
                return await self._async_step_after_control()
        data = self._data
        return self.async_show_form(
            step_id="ha_output",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OUTPUT_SETPOINT_ENTITY,
                        default=data.get(CONF_OUTPUT_SETPOINT_ENTITY, vol.UNDEFINED),
                    ): _NUMBER_ENTITY,
                    _optional(CONF_INPUT_SETPOINT_ENTITY, data): _NUMBER_ENTITY,
                    _optional(CONF_MODE_ENTITY, data): _SELECT_ENTITY,
                    _optional(CONF_REMOTE_ENTITY, data): _REMOTE_ENTITY,
                }
            ),
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
        mode = data.get(CONF_MODE_ENTITY) if control in (BatteryControl.SPLIT, BatteryControl.OUTPUT) else None
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
                self._remove_pv_systems()
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
                    **self._self_test_schema(),
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

    def _self_test_schema(self) -> dict:
        """Offer a self-test after adding a controlled battery."""
        if self.source == SOURCE_RECONFIGURE:
            return {}
        return {vol.Optional(CONF_SELF_TEST_ON_SETUP, default=True): selector.BooleanSelector()}

    def _async_finish(self, user_input: dict[str, Any]) -> SubentryFlowResult:
        data = _battery_data(user_input, self._model)
        title = data.pop(CONF_NAME)
        if self.source == SOURCE_RECONFIGURE or not data.get(CONF_SELF_TEST_ON_SETUP):
            data.pop(CONF_SELF_TEST_ON_SETUP, None)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)


_LOGGER = logging.getLogger(__name__)

# Choice in the search step for entering the address by hand.
MANUAL = "manual"
# Profile step: the field, the choice to paste an own profile and the prefix
# of the choices copying the own profile of another battery.
CONF_PROFILE = "profile"
OWN_PROFILE = "own"
COPY_PREFIX = "copy_"
# Write preview: an own profile's write registers compared with the manual; known that a
# shipped profile has not been verified on a real battery yet.
CONF_CHECKED = "checked"
CONF_CHECKED_UNVERIFIED = "checked_unverified"
# Other batteries to change to the same profile.
CONF_BATTERIES = "batteries"

_PROFILE_WORDS = {
    "en": {
        "copy": "Copy of {battery}: {profile}", "same": "same profile", "differs": "changed since", "line": "line",
        "unverified": " (unverified)",
    },
    "de": {
        "copy": "Kopie von {battery}: {profile}", "same": "gleiches Profil", "differs": "inzwischen geändert",
        "line": "Zeile", "unverified": " (ungeprüft)",
    },
}


def _profile_words(language: str) -> dict[str, str]:
    return _PROFILE_WORDS["de" if language.startswith("de") else "en"]


def _profile_check(profile: BatteryProfile) -> str | None:
    """The confirmation the write preview of a profile needs, if any."""
    if not profile.bundled:
        return CONF_CHECKED
    return CONF_CHECKED_UNVERIFIED if profile.unverified else None


def _shipped_id(text: str) -> str | None:
    """The id of a profile that loads with the rules of shipped profiles, else None."""
    try:
        return parse_profile(text, bundled=True).id
    except ProfileError:
        return None


def _profile_where(err: ProfileError, language: str) -> str:
    """Where a profile error is: '`control.apply[1]` (line 42)'."""
    line = f"{_profile_words(language)['line']} {err.line}" if err.line is not None else ""
    if err.path:
        return f"`{err.path}` ({line})" if line else f"`{err.path}`"
    return line or "–"


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
    CONF_OWN_PV,
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
    kept = _CONTROL_KEYS.get(control, ())
    for other, keys in _CONTROL_KEYS.items():
        if other is not control:
            for key in keys:
                if key not in kept:
                    result.pop(key, None)
    if control in (BatteryControl.NONE, BatteryControl.SCRIPT):
        for key in _REMOTE_KEYS:
            result.pop(key, None)
    if control is BatteryControl.NONE:
        for key in _CONTROLLED_KEYS:
            result.pop(key, None)
    if not result.get(CONF_OWN_PV):
        result.pop(CONF_OWN_PV, None)
        for key in _OWN_PV_KEYS:
            result.pop(key, None)
    else:
        result[CONF_MAX_OUTPUT_W] = int(result[CONF_MAX_OUTPUT_W])
    result.pop(CONF_REMOVE_FROM_PV, None)
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


def _entity_info(hass: HomeAssistant, entry: er.RegistryEntry) -> EntityInfo:
    """An entity as the matcher sees it."""
    state = hass.states.get(entry.entity_id)
    attributes = state.attributes if state else {}
    return EntityInfo.create(
        entry.entity_id,
        entry.translation_key,
        entry.original_name,
        entry.name,
        device_class=entry.device_class or entry.original_device_class or attributes.get("device_class"),
        unit=attributes.get("unit_of_measurement") or entry.unit_of_measurement,
        state_class=attributes.get("state_class"),
        options=tuple(str(option) for option in attributes.get("options") or ()),
        minimum=attributes.get("min"),
        maximum=attributes.get("max"),
        state=state.state if state else None,
        platform=entry.platform,
    )


def _device_entities(hass: HomeAssistant, device_id: str) -> list[EntityInfo]:
    """The enabled entities of a device as the matcher sees them."""
    return [
        _entity_info(hass, entry)
        for entry in er.async_entries_for_device(er.async_get(hass), device_id)
        if entry.disabled_by is None
    ]


@dataclass
class _DeviceCandidate:
    name: str
    platform: str
    infos: list[EntityInfo]


def _used_entities(entry: ConfigEntry | None, own_subentry: str | None, *, system: bool) -> set[str]:
    """Entity ids SLEMS uses: of the subentries except ``own_subentry``, with ``system`` also the system's."""
    if entry is None:
        return set()
    configs = [subentry.data for subentry_id, subentry in entry.subentries.items() if subentry_id != own_subentry]
    if system:
        configs += [entry.data, entry.options]
    used: set[str] = set()
    for config in configs:
        for value in config.values():
            values = value if isinstance(value, list) else [value]
            used.update(v for v in values if isinstance(v, str) and "." in v)
    return used


def _device_of(hass: HomeAssistant, entity_id: str | None) -> str | None:
    entry = er.async_get(hass).async_get(entity_id) if entity_id else None
    return entry.device_id if entry is not None else None


def _device_candidates(hass: HomeAssistant, used: set[str], keep: Iterable[str | None]) -> dict[str, _DeviceCandidate]:
    """Devices with entities, without SLEMS's own and those with an entity in ``used`` (unless in ``keep``)."""
    keep = set(keep)
    by_device: dict[str, list[er.RegistryEntry]] = {}
    for entity in er.async_get(hass).entities.values():
        if entity.device_id and entity.disabled_by is None:
            by_device.setdefault(entity.device_id, []).append(entity)
    devices = dr.async_get(hass)
    result: dict[str, _DeviceCandidate] = {}
    for device_id, entities in by_device.items():
        platforms = Counter(entity.platform for entity in entities)
        if DOMAIN in platforms:
            continue
        if device_id not in keep and any(entity.entity_id in used for entity in entities):
            continue
        if (device := devices.async_get(device_id)) is None:
            continue
        result[device_id] = _DeviceCandidate(
            name=device.name_by_user or device.name or device_id,
            platform=platforms.most_common(1)[0][0],
            infos=[_entity_info(hass, entity) for entity in entities],
        )
    return result


def _consumer_power(infos: list[EntityInfo]) -> str | None:
    return match_consumer_entities(infos, ConsumerType.OTHER, ControlMode.NONE).get(CONF_POWER_ENTITY)


def _device_select(
    candidates: Mapping[str, _DeviceCandidate],
    fit: Callable[[list[EntityInfo]], float],
    keep: str | Iterable[str] | None,
    *,
    power_of: Callable[[list[EntityInfo]], str | None] = _consumer_power,
    boost: set[str] = frozenset(),  # type: ignore[assignment]
    multiple: bool = False,
) -> selector.SelectSelector | None:
    """Devices that fit, the best first, with the power they measure now; None if none fits.

    ``boost``: devices to rank higher, e.g. those of the energy dashboard.
    """
    keep_ids = {keep} if isinstance(keep, str) else set(keep or ())
    scored = []
    for device_id, candidate in candidates.items():
        score = fit(candidate.infos)
        if score > 0 and device_id in boost:
            score += 3
        if score > 0 or device_id in keep_ids:
            scored.append((-score, candidate.name.lower(), device_id, candidate))
    if not scored:
        return None
    options = []
    for _score, _name, device_id, candidate in sorted(scored):
        label = f"{candidate.name} ({candidate.platform})"
        power = power_of(candidate.infos)
        info = next((info for info in candidate.infos if info.entity_id == power), None)
        if info is not None and info.has_value:
            label += f" – {info.state} {info.unit}"
        options.append(selector.SelectOptionDict(value=device_id, label=label))
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN, multiple=multiple)
    )


def _suggest_consumer(
    hass: HomeAssistant,
    consumer_type: ConsumerType,
    device_id: str | None,
    infos: list[EntityInfo],
    meter_infos: list[EntityInfo],
) -> dict[str, Any]:
    """Name, measurement and control mode suggested from the consumer's device and its meter device."""
    data: dict[str, Any] = {}
    if device_id and (device := dr.async_get(hass).async_get(device_id)):
        data[CONF_NAME] = device.name_by_user or device.name
    data.update(match_consumer_entities(meter_infos, ConsumerType.OTHER, ControlMode.NONE))
    hot_water = suggest_hot_water(infos) if infos else True
    if consumer_type is ConsumerType.HEAT_PUMP:
        data[CONF_HOT_WATER] = hot_water
    if infos:
        data[CONF_CONTROL_MODE] = suggest_control_mode(infos, consumer_type, hot_water=hot_water).value
    return data


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


def _battery_power_of(infos: list[EntityInfo]) -> str | None:
    return match_battery_entities(infos).get(CONF_POWER_ENTITY)


def _battery_from_dashboard(
    hass: HomeAssistant, dashboard: EnergyDashboard, device_id: str | None, data: dict[str, Any], used: set[str]
) -> dict[str, Any]:
    """The energy dashboard's sensors of the battery on ``device_id`` (without a device: of the first one
    SLEMS does not use yet). Its power only if the device suggests none or the same."""
    for battery in dashboard.batteries:
        sensors = [e for e in (battery.soc, battery.power, battery.charged, battery.discharged) if e]
        if device_id is not None and device_id not in {_device_of(hass, e) for e in sensors}:
            continue
        if device_id is None and any(e in used for e in sensors):
            continue
        result: dict[str, Any] = {}
        for key, entity_id in (
            (CONF_SOC_ENTITY, battery.soc),
            (CONF_CHARGED_ENERGY_ENTITY, battery.charged),
            (CONF_DISCHARGED_ENERGY_ENTITY, battery.discharged),
        ):
            if entity_id:
                result[key] = entity_id
        if battery.power and data.get(CONF_POWER_ENTITY) in (None, battery.power):
            result[CONF_POWER_ENTITY] = battery.power
            result[CONF_POWER_INVERTED] = battery.power_discharge_positive
        return result
    return {}


def _grid_power_of(infos: list[EntityInfo]) -> str | None:
    return match_grid_entities(infos).get(CONF_GRID_POWER_ENTITY)


def _system_devices(
    hass: HomeAssistant, current: dict[str, Any], dashboard: EnergyDashboard
) -> tuple[str | None, list[str]]:
    """Devices of the stored grid and PV sensors, at the first setup those of the energy dashboard."""
    grid_sources = [current.get(CONF_GRID_POWER_ENTITY)] if current else [dashboard.grid_power, dashboard.grid_import]
    grid = next((device for entity_id in grid_sources if (device := _device_of(hass, entity_id))), None)
    pv_sources = entity_ids(current.get(CONF_PV_POWER_ENTITY)) if current else dashboard.pv_power + dashboard.pv_energy
    pv: list[str] = []
    for entity_id in pv_sources:
        if (device := _device_of(hass, entity_id)) and device not in pv:
            pv.append(device)
    return grid, pv


async def _async_suggest_system(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    grid_device: str | None,
    pv_devices: list[str],
    dashboard: EnergyDashboard,
) -> dict[str, Any]:
    """System settings suggested from the chosen devices, the energy dashboard and the setup."""
    data: dict[str, Any] = {}
    if grid_device:
        data.update(match_grid_entities(_device_entities(hass, grid_device)))
    for key, entity_id in (
        (CONF_GRID_POWER_ENTITY, dashboard.grid_power),
        (CONF_GRID_IMPORT_ENERGY_ENTITY, dashboard.grid_import),
        (CONF_GRID_EXPORT_ENERGY_ENTITY, dashboard.grid_export),
    ):
        if not entity_id:
            continue
        device = _device_of(hass, entity_id)
        # What the user chose in the dashboard, if it belongs to the chosen device or to none.
        if grid_device is None or device == grid_device or (device is None and key not in data):
            data[key] = entity_id
    if dashboard.grid_power and data.get(CONF_GRID_POWER_ENTITY) == dashboard.grid_power:
        data[CONF_GRID_POWER_INVERTED] = dashboard.grid_power_inverted

    pv_power: list[str] = []
    for device in pv_devices:
        power = next((e for e in dashboard.pv_power if _device_of(hass, e) == device), None)
        power = power or match_pv_power(_device_entities(hass, device))
        if power and power not in pv_power:
            pv_power.append(power)
    if not pv_devices:
        pv_power = list(dashboard.pv_power)
    # The own PV of a battery counts already; as a PV system it would count twice.
    owners = _own_pv_owners(entry)
    pv_power = [entity_id for entity_id in pv_power if entity_id not in owners]
    if pv_power:
        data[CONF_PV_POWER_ENTITY] = pv_power

    providers = [provider.entry_id for provider in await async_forecast_provider_entries(hass)]
    forecasts = [e for e in dashboard.pv_forecast_entries if e in providers] or providers
    if forecasts:
        data[CONF_PV_FORECAST_ENTRIES] = forecasts
    if len(weather := hass.states.async_entity_ids("weather")) == 1:
        data[CONF_WEATHER_ENTITY] = weather[0]
    if entry is not None:
        # The outdoor sensor of a heat pump has the history the forecast needs.
        for subentry in entry.subentries.values():
            device = subentry.data.get(CONF_DEVICE)
            if subentry.data.get(CONF_CONSUMER_TYPE) == ConsumerType.HEAT_PUMP and device:
                if outdoor := match_outdoor_temperature(_device_entities(hass, device)):
                    data[CONF_OUTDOOR_TEMPERATURE_ENTITY] = outdoor
                    break
    return data


def _own_pv_owners(entry: ConfigEntry | None) -> dict[str, str]:
    """Own PV modules and outputs of the batteries (entity id: battery name);
    their production counts with the battery, as a PV system it would count twice."""
    owners: dict[str, str] = {}
    for subentry in entry.subentries.values() if entry is not None else ():
        if subentry.data.get(CONF_OWN_PV):
            for key in (CONF_OWN_PV_ENTITY, CONF_OUTPUT_ENTITY):
                if entity_id := subentry.data.get(key):
                    owners.setdefault(entity_id, subentry.title)
    return owners


def _now_placeholders(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Current grid and PV power of the suggested sensors, to check the sign."""

    def watts(entity_ids_: Iterable[str]) -> str:
        values = [w for e in entity_ids_ if (w := state_as_watts(hass.states.get(e))) is not None]
        return f"{sum(values):.0f} W" if values else "–"

    grid = data.get(CONF_GRID_POWER_ENTITY)
    return {
        "grid_now": watts([grid] if grid else []),
        "pv_now": watts(entity_ids(data.get(CONF_PV_POWER_ENTITY))),
    }


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
    switch, a power set point or a release is optional; the release of a heat
    pump that heats hot water is one for its hot water charge (operating mode and
    its hot water states). The first step picks the consumer's device (and one
    that measures it, if another); the later steps show the entities suggested
    from them.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._hot_water_labels: list[selector.SelectOptionDict] = []
        # Entities of the consumer's device and what SLEMS suggests from them.
        self._infos: list[EntityInfo] = []
        self._suggested: dict[str, Any] = {}
        # Another device than before: its suggestions replace the stored values.
        self._device_changed = False

    def _existing(self) -> dict[str, Any]:
        if self.source == SOURCE_RECONFIGURE:
            subentry = self._get_reconfigure_subentry()
            return {CONF_NAME: subentry.title, **subentry.data}
        return {}

    def _with_suggestions(self, existing: dict[str, Any], suggested: dict[str, Any]) -> dict[str, Any]:
        if self._device_changed:
            return {**existing, **suggested}
        return {**suggested, **existing}

    def _existing_devices(self) -> tuple[str | None, str | None]:
        """(device, meter device) of the consumer being reconfigured, also from its entities."""
        existing = self._existing()
        if not existing:
            return None, None
        devices = dr.async_get(self.hass)
        device = existing.get(CONF_DEVICE)
        if device is None or devices.async_get(device) is None:
            device = _device_of(self.hass, existing.get(CONF_CONTROL_ENTITY)) or _device_of(
                self.hass, existing.get(CONF_POWER_ENTITY)
            )
        meter = existing.get(CONF_METER_DEVICE)
        if meter is None or devices.async_get(meter) is None:
            meter = _device_of(self.hass, existing.get(CONF_POWER_ENTITY))
        return device, (meter if meter != device else None)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Type and device of the consumer; SLEMS suggests its entities in the next steps."""
        known = self._existing_devices()
        if user_input is not None:
            consumer_type = ConsumerType(user_input[CONF_CONSUMER_TYPE])
            device = user_input.get(CONF_DEVICE)
            meter = user_input.get(CONF_METER_DEVICE)
            self._device_changed = (device, meter) != known
            self._infos = _device_entities(self.hass, device) if device else []
            self._data = {CONF_CONSUMER_TYPE: consumer_type.value}
            if device:
                self._data[CONF_DEVICE] = device
            if meter:
                self._data[CONF_METER_DEVICE] = meter
            meter_infos = _device_entities(self.hass, meter) if meter else self._infos
            self._suggested = _suggest_consumer(self.hass, consumer_type, device, self._infos, meter_infos)
            # The energy dashboard's sensors of the measuring device are what the user chose there.
            measuring = meter or device
            dashboard = await async_energy_dashboard(self.hass)
            for energy, power in dashboard.devices.items():
                if measuring and _device_of(self.hass, energy) == measuring:
                    self._suggested[CONF_ENERGY_ENTITY] = energy
                    if power:
                        self._suggested[CONF_POWER_ENTITY] = power
                    break
            return await self.async_step_consumer()

        defaults = self._existing()
        device, meter = known
        dashboard = await async_energy_dashboard(self.hass)
        in_dashboard = {
            device_id
            for energy, power in dashboard.devices.items()
            for entity_id in (energy, power)
            if (device_id := _device_of(self.hass, entity_id))
        }
        used = _used_entities(self._get_entry(), self._subentry_id(), system=True)
        candidates = _device_candidates(self.hass, used, (device, meter))
        fields: dict = {
            vol.Required(
                CONF_CONSUMER_TYPE,
                default=defaults.get(CONF_CONSUMER_TYPE, ConsumerType.OTHER.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[t.value for t in ConsumerType],
                    translation_key=CONF_CONSUMER_TYPE,
                )
            ),
        }
        # The type is chosen in the same form: a device ranks by the type it fits best.
        devices = _device_select(
            candidates,
            lambda infos: max(consumer_device_fit(infos, t) for t in ConsumerType),
            device,
            boost=in_dashboard,
        )
        if devices is not None:
            fields[vol.Optional(CONF_DEVICE, description={"suggested_value": device})] = devices
        meters = _device_select(candidates, meter_device_fit, meter, boost=in_dashboard)
        if meters is not None:
            fields[vol.Optional(CONF_METER_DEVICE, description={"suggested_value": meter})] = meters
        return self.async_show_form(step_id="user", data_schema=vol.Schema(fields))

    def _subentry_id(self) -> str | None:
        return self._get_reconfigure_subentry().subentry_id if self.source == SOURCE_RECONFIGURE else None

    async def async_step_consumer(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, measurement and control mode, suggested from the device."""
        consumer_type = ConsumerType(self._data[CONF_CONSUMER_TYPE])
        if user_input is not None:
            self._data.update(user_input)
            if ControlMode(user_input[CONF_CONTROL_MODE]) is ControlMode.NONE:
                return self._async_finish()
            return await self.async_step_control()

        existing = self._existing()
        if consumer_type is ConsumerType.HEAT_PUMP and CONF_HOT_WATER not in existing and existing:
            # Stored before the option existed: a hot water release has its operating mode.
            existing[CONF_HOT_WATER] = bool(existing.get(CONF_MODE_ENTITY))
        defaults = self._with_suggestions(existing, self._suggested)
        fields: dict = {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
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
        }
        if consumer_type is ConsumerType.HEAT_PUMP:
            fields[
                vol.Required(CONF_HOT_WATER, default=defaults.get(CONF_HOT_WATER, True))
            ] = selector.BooleanSelector()
        fields[
            vol.Required(
                CONF_CONTROL_MODE,
                default=defaults.get(CONF_CONTROL_MODE, ControlMode.NONE.value),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[m.value for m in ControlMode],
                translation_key=CONF_CONTROL_MODE,
            )
        )
        return self.async_show_form(step_id="consumer", data_schema=vol.Schema(fields))

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
            if user_input.get(CONF_MODE_ENTITY):
                return await self.async_step_hot_water_states()
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
        consumer_type = ConsumerType(self._data[CONF_CONSUMER_TYPE])
        hot_water = consumer_type is ConsumerType.HEAT_PUMP and self._data.get(CONF_HOT_WATER, False)
        if self._infos:
            defaults = self._with_suggestions(
                defaults, suggest_consumer_control(self._infos, consumer_type, mode, hot_water=hot_water)
            )

        wallbox = consumer_type is ConsumerType.WALLBOX
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
        elif mode is ControlMode.RELEASE:
            if hot_water:
                fields[
                    vol.Required(CONF_MODE_ENTITY, default=defaults.get(CONF_MODE_ENTITY, vol.UNDEFINED))
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "select", "input_select"])
                )
            fields[
                vol.Required(CONF_NOMINAL_POWER_W, default=defaults.get(CONF_NOMINAL_POWER_W, vol.UNDEFINED))
            ] = _watts(100_000)
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
        # A water heater counts with its current temperature.
        temperature = selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(domain="sensor", device_class="temperature"),
                    selector.EntityFilterSelectorConfig(domain="water_heater"),
                ]
            )
        )
        fields[_optional(CONF_TEMPERATURE_ENTITY, defaults)] = temperature
        fields[_optional(CONF_TEMPERATURE_2_ENTITY, defaults)] = temperature
        fields[
            vol.Required(CONF_PRIORITY, default=defaults.get(CONF_PRIORITY, DEFAULT_PRIORITY))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )
        return self.async_show_form(step_id="control", data_schema=vol.Schema(fields))

    async def async_step_hot_water_states(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """States of the heat pump's operating mode that mean hot water (preselected by name)."""
        entity_id = self._data[CONF_MODE_ENTITY]
        if user_input is not None:
            if not user_input.get(CONF_HOT_WATER_STATES):
                return self._hot_water_form(entity_id, user_input, {CONF_HOT_WATER_STATES: "hot_water_states_required"})
            self._data.update(user_input)
            return self._async_finish()
        state = self.hass.states.get(entity_id)
        options = [str(o) for o in (state.attributes.get("options") or [])] if state else []
        labels = await _option_labels(self.hass, entity_id, options)
        existing = self._existing()
        if existing.get(CONF_MODE_ENTITY) == entity_id and existing.get(CONF_HOT_WATER_STATES):
            chosen = list(existing[CONF_HOT_WATER_STATES])
        else:
            chosen = hot_water_guess(labels)
        self._hot_water_labels = labels
        return self._hot_water_form(entity_id, {CONF_HOT_WATER_STATES: chosen}, {})

    def _hot_water_form(self, entity_id: str, values: dict[str, Any], errors: dict[str, str]) -> SubentryFlowResult:
        labels = self._hot_water_labels
        state = self.hass.states.get(entity_id)
        return self.async_show_form(
            step_id="hot_water_states",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_HOT_WATER_STATES, description={"suggested_value": values.get(CONF_HOT_WATER_STATES) or []}
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels, multiple=True, custom_value=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "entity": f"{state.name} ({entity_id})" if state else entity_id,
                "state": state.state if state else "–",
            },
        )

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
        defaults = suggest_start_options(options)
        existing = self._existing()
        if existing.get(CONF_START_ENTITY) == entity_id:
            defaults.update({key: existing[key] for key in (CONF_START_ON, CONF_START_OFF) if key in existing})
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


def hot_water_guess(options: list[selector.SelectOptionDict]) -> list[str]:
    """Options of an operating mode that probably mean hot water (value or label)."""
    return [option["value"] for option in options if means_hot_water(option["value"]) or means_hot_water(option["label"])]


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



def _prompt_url(name: str, language: str | None) -> str:
    suffix = ".de" if (language or "").startswith("de") else ""
    return f"https://github.com/gojux/SLEMS/blob/main/docs/{name}{suffix}.md"


def tariff_prompt_url(language: str | None) -> str:
    """The guide to make a tariff from a bill with an AI, in German or English."""
    return _prompt_url("tariff-prompt", language)


def battery_profile_prompt_url(language: str | None) -> str:
    """The guide to make a battery profile from the manual with an AI, in German or English."""
    return _prompt_url("battery-profile-prompt", language)

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
            _optional("max_market_ct", current): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    step="any", unit_of_measurement=f"{minor}/kWh", mode=selector.NumberSelectorMode.BOX
                )
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
        max_market_ct=(
            float(user_input["max_market_ct"])
            if user_input.get("max_market_ct") is not None and Unit(user_input["unit"]).dynamic
            else None
        ),
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
            # Percent of the amount of the market price.
            price += f" {'+' if item.factor_pct > 0 else '−'} {short(abs(item.factor_pct))} %"
        if item.price:
            price += f" {'+' if item.price > 0 else '−'} {short(abs(item.price))} {symbols(currency)[1]}/kWh"
        if item.max_market_ct is not None:
            price += " · " + words["max_market_ct"].format(price=f"{short(item.max_market_ct)} {symbols(currency)[1]}/kWh")
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
        "months": "Monate", "weekdays": "Wochentage", "zero_when_negative": "0 bei negativem Börsenpreis", "max_market_ct": "Marktpreis höchstens {price}", "own": "eigene Vorlage", "no_template": "keine", "offer": "Angebot für Vertragsbeginn {month}",
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
        "months": "months", "weekdays": "weekdays", "zero_when_negative": "0 at a negative market price", "max_market_ct": "market price at most {price}", "own": "own template", "no_template": "none", "offer": "offer for contracts starting {month}",
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
