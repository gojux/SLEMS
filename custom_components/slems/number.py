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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsSystemEntity


@dataclass(frozen=True, kw_only=True)
class SettingNumberDescription(NumberEntityDescription):
    """Numeric setting stored in ControlSettings."""

    attribute: str
    # Dynamic upper limit, overrides native_max_value.
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
    _percentage("night_reserve", "night_reserve_pct"),
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
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setting numbers."""
    async_add_entities(
        SettingNumber(entry.runtime_data, description) for description in SETTING_NUMBERS
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
    def native_max_value(self) -> float:
        if self.entity_description.max_fn is not None:
            return self.entity_description.max_fn(self.coordinator)
        return super().native_max_value

    @property
    def native_value(self) -> float:
        value = getattr(self.coordinator.settings, self.entity_description.attribute)
        return min(value, self.native_max_value)

    async def async_set_native_value(self, value: float) -> None:
        self._set(value)
        self.async_write_ha_state()
