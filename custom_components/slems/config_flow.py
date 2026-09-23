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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_BLOCK_ENTITY,
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
from .drivers.marstek_venus_e3 import HARDWARE_MAX_POWER_W, MarstekVenusE3Driver
from .pv_forecast import async_forecast_provider_entries

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
    CONF_HOUSE_HISTORY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
                default=defaults.get(CONF_GRID_POWER_INVERTED, False),
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


class SlemsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup of the SLEMS system."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="SLEMS", data=user_input)
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
        return self.async_show_form(
            step_id="init", data_schema=await _async_system_schema(self.hass, current)
        )


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
                **self._limits_schema(defaults, HARDWARE_MAX_POWER_W, list(EfficiencyMode)),
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
                **self._limits_schema(
                    defaults, 100_000, [EfficiencyMode.LEARNED, EfficiencyMode.MANUAL]
                ),
            }
        )
        return self.async_show_form(step_id="ha_entities", data_schema=schema)

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
        data = dict(user_input)
        title = data.pop(CONF_NAME)
        data[CONF_MODEL] = self._model.value
        for key in (
            CONF_CAPACITY_WH,
            CONF_MAX_CHARGE_POWER_W,
            CONF_MAX_DISCHARGE_POWER_W,
            CONF_ROUND_TRIP_EFFICIENCY_PCT,
        ):
            data[key] = int(data[key])
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)


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
        """Control entity, power range, external block and priority."""
        if user_input is not None:
            self._data.update(user_input)
            return self._async_finish()

        defaults = self._existing()
        # Control entity and power fields are only reused if the mode is unchanged.
        mode = ControlMode(self._data[CONF_CONTROL_MODE])
        if defaults.get(CONF_CONTROL_MODE) != mode.value:
            keep = (CONF_BLOCK_ENTITY, CONF_PRIORITY, CONF_MIN_ON_MINUTES, CONF_MIN_OFF_MINUTES)
            defaults = {k: v for k, v in defaults.items() if k in keep}

        fields: dict = {}
        if mode is ControlMode.SWITCH:
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
            selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch"])
        )
        fields[
            vol.Required(CONF_PRIORITY, default=defaults.get(CONF_PRIORITY, DEFAULT_PRIORITY))
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )
        return self.async_show_form(step_id="control", data_schema=vol.Schema(fields))

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
        ):
            if key in data:
                data[key] = int(data[key])
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
            )
        return self.async_create_entry(title=title, data=data)
