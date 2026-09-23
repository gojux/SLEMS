"""Number platform: numeric runtime settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsSystemEntity


@dataclass(frozen=True, kw_only=True)
class SettingNumberDescription(NumberEntityDescription):
    """Numeric setting stored in ControlSettings."""

    attribute: str


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
    _percentage("charge_secured_margin", "charge_secured_margin_pct", 100, 300),
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
    def native_value(self) -> float:
        return getattr(self.coordinator.settings, self.entity_description.attribute)

    async def async_set_native_value(self, value: float) -> None:
        self._set(value)
        self.async_write_ha_state()
