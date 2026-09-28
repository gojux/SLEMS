"""Number platform: numeric runtime settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsBatteryEntity, SlemsSystemEntity


@dataclass(frozen=True, kw_only=True)
class SettingNumberDescription(NumberEntityDescription):
    """Numeric setting stored in ControlSettings."""

    attribute: str
    # Dynamic limits, override native_min_value / native_max_value.
    min_fn: Callable[[SlemsCoordinator], float] | None = None
    max_fn: Callable[[SlemsCoordinator], float] | None = None


def _percentage(key: str, attribute: str, minimum: float = 0, maximum: float = 100):
    return SettingNumberDescription(
        key=key,
        translation_key=key,
        attribute=attribute,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=minimum,
        native_max_value=maximum,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


def _watts(key: str, attribute: str, minimum: float, maximum: float, **kwargs):
    return SettingNumberDescription(
        key=key,
        translation_key=key,
        attribute=attribute,
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=minimum,
        native_max_value=maximum,
        native_step=10,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        **kwargs,
    )


def _celsius(key: str, attribute: str, minimum: float, maximum: float):
    return SettingNumberDescription(
        key=key,
        translation_key=key,
        attribute=attribute,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=minimum,
        native_max_value=maximum,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


def _total_max_discharge_w(coordinator: SlemsCoordinator) -> float:
    return sum(b.driver.capabilities.max_discharge_power_w for b in coordinator.batteries)


SETTING_NUMBERS: tuple[SettingNumberDescription, ...] = (
    SettingNumberDescription(
        key="surplus_average_window",
        translation_key="surplus_average_window",
        attribute="surplus_average_window_s",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _percentage("battery_priority_soc", "battery_priority_soc_pct"),
    _percentage("battery_share_when_secured", "battery_share_when_secured_pct"),
    SettingNumberDescription(
        key="charge_secured_buffer",
        translation_key="charge_secured_buffer",
        attribute="charge_secured_buffer_kwh",
        device_class=NumberDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_min_value=0,
        native_max_value=50,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="grid_friendly_buffer",
        translation_key="grid_friendly_buffer",
        attribute="grid_friendly_buffer_kwh",
        device_class=NumberDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_min_value=0,
        native_max_value=50,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _percentage("night_reserve", "night_reserve_pct"),
    _percentage("night_reserve_coverage", "night_reserve_coverage_pct", 50, 200),
    _percentage("rotation_soc_threshold", "rotation_soc_threshold_pct", 1, 50),
    SettingNumberDescription(
        key="rotation_min_interval",
        translation_key="rotation_min_interval",
        attribute="rotation_min_interval_min",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0,
        native_max_value=240,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="control_interval",
        translation_key="control_interval",
        attribute="control_interval_s",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0.5,
        native_max_value=30,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="control_gain",
        translation_key="control_gain",
        attribute="control_gain",
        native_min_value=0.1,
        native_max_value=1.0,
        native_step=0.05,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="rotation_ramp_rate",
        translation_key="rotation_ramp_rate",
        attribute="rotation_ramp_rate_w_per_s",
        native_unit_of_measurement="W/s",
        native_min_value=10,
        native_max_value=2500,
        native_step=10,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="rotation_ramp_max",
        translation_key="rotation_ramp_max",
        attribute="rotation_ramp_max_s",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _watts("charge_grid_target", "charge_grid_target_w", 0, 5000),
    _watts("discharge_grid_target", "discharge_grid_target_w", -1000, 1000),
    _watts(
        "discharge_max_grid_export",
        "discharge_max_grid_export_w",
        0,
        0,
        max_fn=_total_max_discharge_w,
    ),
    SettingNumberDescription(
        key="peak_shaving_grid_limit",
        translation_key="peak_shaving_grid_limit",
        attribute="peak_shaving_grid_limit_w",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=30000,
        native_step=100,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="peak_shaving_soc_threshold",
        translation_key="peak_shaving_soc_threshold",
        attribute="peak_shaving_soc_threshold_pct",
        # Below the minimum SoC there is nothing left to shave with.
        min_fn=lambda coordinator: round(coordinator.min_soc_pct),
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    _percentage("peak_shaving_reserve", "peak_shaving_reserve_pct", 0, 80),
    SettingNumberDescription(
        key="communication_pause",
        translation_key="communication_pause",
        attribute="communication_pause_min",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=5,
        native_max_value=120,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SettingNumberDescription(
        key="pv_peak_power",
        translation_key="pv_peak_power",
        attribute="pv_peak_power_kwp",
        native_unit_of_measurement="kWp",
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _percentage("feed_in_cap_limit", "feed_in_cap_limit_pct"),
    _percentage("feed_in_cap_buffer", "feed_in_cap_buffer_pct", -50, 100),
    _celsius("temperature_high", "temperature_high_c", 20, 70),
    _celsius("temperature_band", "temperature_band_c", 1, 30),
    _percentage("temperature_floor", "temperature_floor_pct"),
    _celsius("temperature_low", "temperature_low_c", -20, 20),
)


@dataclass(frozen=True, kw_only=True)
class BatteryNumberDescription(NumberEntityDescription):
    """Setting of one battery stored in BatteryRuntime.limits."""

    attribute: str
    # Capability attribute that is the maximum (and the default) of a power limit.
    capability: str | None = None


def _battery_power(key: str, capability: str) -> BatteryNumberDescription:
    return BatteryNumberDescription(
        key=key,
        translation_key=key,
        attribute=key.replace("max_", "") + "_w",
        capability=capability,
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_step=10,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


BATTERY_NUMBERS: tuple[BatteryNumberDescription, ...] = (
    BatteryNumberDescription(
        key="min_soc",
        translation_key="min_soc",
        attribute="min_soc_pct",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    BatteryNumberDescription(
        key="max_soc",
        translation_key="max_soc",
        attribute="max_soc_pct",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _battery_power("max_charge_limit", "max_charge_power_w"),
    _battery_power("max_discharge_limit", "max_discharge_power_w"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setting numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        SettingNumber(coordinator, description) for description in SETTING_NUMBERS
    )
    for battery in coordinator.batteries:
        if battery.driver.capabilities.controllable:
            async_add_entities(
                (BatteryNumber(coordinator, battery, d) for d in BATTERY_NUMBERS),
                config_subentry_id=battery.subentry_id,
            )


class SettingNumber(SlemsSystemEntity, RestoreNumber):
    """Numeric runtime setting, restored after a restart."""

    entity_description: SettingNumberDescription

    def __init__(
        self, coordinator: SlemsCoordinator, description: SettingNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._set(last.native_value)

    def _set(self, value: float) -> None:
        setattr(self.coordinator.settings, self.entity_description.attribute, value)

    @property
    def native_min_value(self) -> float:
        if self.entity_description.min_fn is not None:
            return self.entity_description.min_fn(self.coordinator)
        return super().native_min_value

    @property
    def native_max_value(self) -> float:
        if self.entity_description.max_fn is not None:
            return self.entity_description.max_fn(self.coordinator)
        return super().native_max_value

    @property
    def native_value(self) -> float:
        value = getattr(self.coordinator.settings, self.entity_description.attribute)
        return max(self.native_min_value, min(value, self.native_max_value))

    async def async_set_native_value(self, value: float) -> None:
        self._set(value)
        if self.entity_description.attribute == "control_gain":
            # A new start value restarts the automatic adaptation from there.
            self.coordinator.controller.gain_adapter.reset(value)
        self.async_write_ha_state()


class BatteryNumber(SlemsBatteryEntity, RestoreNumber):
    """Limit of one battery (SoC window, power), restored after a restart."""

    entity_description: BatteryNumberDescription

    def __init__(
        self,
        coordinator: SlemsCoordinator,
        battery: BatteryRuntime,
        description: BatteryNumberDescription,
    ) -> None:
        super().__init__(coordinator, battery, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._set(last.native_value)

    @property
    def _capability_w(self) -> float | None:
        if self.entity_description.capability is None:
            return None
        return getattr(self.battery.driver.capabilities, self.entity_description.capability)

    def _set(self, value: float) -> None:
        maximum = self._capability_w
        if maximum is not None and value >= maximum:
            # At the maximum: follows the battery's capability.
            value = None
        setattr(self.battery.limits, self.entity_description.attribute, value)

    @property
    def native_max_value(self) -> float:
        maximum = self._capability_w
        return maximum if maximum is not None else super().native_max_value

    @property
    def native_value(self) -> float:
        value = getattr(self.battery.limits, self.entity_description.attribute)
        return self._capability_w if value is None else value

    async def async_set_native_value(self, value: float) -> None:
        self._set(value)
        self.async_write_ha_state()
