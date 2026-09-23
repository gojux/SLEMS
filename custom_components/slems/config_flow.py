"""Config flow for SLEMS.

The main entry holds the system level measurements (grid, PV, weather). Every
battery is a config subentry, so batteries can be added, edited and removed at
any time without touching the rest of the configuration.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

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
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAPACITY_WH,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HOST,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MODEL,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_SKIP_CONNECTION_TEST,
    CONF_SOC_ENTITY,
    CONF_UNIT_ID,
    CONF_WEATHER_ENTITY,
    DEFAULT_MODBUS_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    SUBENTRY_TYPE_BATTERY,
    BatteryModel,
)
from .drivers.marstek_venus_e3 import HARDWARE_MAX_POWER_W, MarstekVenusE3Driver

# Venus E 3.0 usable capacity.
DEFAULT_CAPACITY_WH = 5120

_POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)
)
_BATTERY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.BATTERY)
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


def _system_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for the system settings, shared by config and options flow."""

    def optional(key: str) -> vol.Optional:
        if key in defaults:
            return vol.Optional(key, description={"suggested_value": defaults[key]})
        return vol.Optional(key)

    return vol.Schema(
        {
            vol.Required(
                CONF_GRID_POWER_ENTITY, default=defaults.get(CONF_GRID_POWER_ENTITY, vol.UNDEFINED)
            ): _POWER_SENSOR,
            vol.Required(
                CONF_GRID_POWER_INVERTED,
                default=defaults.get(CONF_GRID_POWER_INVERTED, False),
            ): selector.BooleanSelector(),
            optional(CONF_PV_POWER_ENTITY): _POWER_SENSOR,
            optional(CONF_PV_FORECAST_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
        }
    )


class SlemsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup of the SLEMS system."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="SLEMS", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_system_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SlemsOptionsFlow:
        return SlemsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_BATTERY: BatterySubentryFlow}


class SlemsOptionsFlow(OptionsFlow):
    """Change the system settings (the update listener reloads the entry)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        # Options fully replace the initial data, so a cleared optional field
        # does not fall back to the value from the first setup.
        current = dict(self.config_entry.options or self.config_entry.data)
        return self.async_show_form(step_id="init", data_schema=_system_schema(current))


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a battery."""

    def __init__(self) -> None:
        self._model: BatteryModel | None = None

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
            return await self.async_step_marstek_venus_e3(user_input)
        return await self.async_step_ha_entities(user_input)

    async def async_step_marstek_venus_e3(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Connection and limits of a Marstek Venus E 3.0."""
        errors: dict[str, str] = {}
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
                **self._limits_schema(defaults, HARDWARE_MAX_POWER_W),
                vol.Optional(CONF_SKIP_CONNECTION_TEST, default=False): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="marstek_venus_e3", data_schema=schema, errors=errors
        )

    async def async_step_ha_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Read-only battery backed by existing entities."""
        if user_input is not None:
            return self._async_finish(user_input)

        defaults = self._defaults(None)
        power_key = (
            vol.Optional(CONF_POWER_ENTITY, description={"suggested_value": defaults[CONF_POWER_ENTITY]})
            if CONF_POWER_ENTITY in defaults
            else vol.Optional(CONF_POWER_ENTITY)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Battery")): str,
                vol.Required(
                    CONF_SOC_ENTITY, default=defaults.get(CONF_SOC_ENTITY, vol.UNDEFINED)
                ): _BATTERY_SENSOR,
                power_key: _POWER_SENSOR,
                vol.Required(
                    CONF_POWER_INVERTED, default=defaults.get(CONF_POWER_INVERTED, False)
                ): selector.BooleanSelector(),
                **self._limits_schema(defaults, 100_000),
            }
        )
        return self.async_show_form(step_id="ha_entities", data_schema=schema)

    @staticmethod
    def _limits_schema(defaults: dict[str, Any], max_power_w: int) -> dict:
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
        data = dict(user_input)
        title = data.pop(CONF_NAME)
        data[CONF_MODEL] = self._model.value
        for key in (CONF_CAPACITY_WH, CONF_MAX_CHARGE_POWER_W, CONF_MAX_DISCHARGE_POWER_W):
            data[key] = int(data[key])
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)

