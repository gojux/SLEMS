"""Switch platform: vacation mode and import peak shaving."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import OperatingMode
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsBatteryEntity, SlemsSystemEntity

# Switch key -> attribute of ControlSettings. All default to off.
SETTING_SWITCHES: dict[str, str] = {
    "vacation": "vacation",
    "peak_shaving": "peak_shaving",
    "night_discharge": "night_discharge",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setting switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        SettingSwitch(coordinator, key, attribute)
        for key, attribute in SETTING_SWITCHES.items()
    )
    for battery in coordinator.batteries:
        async_add_entities(
            [BatteryEnabledSwitch(coordinator, battery)],
            config_subentry_id=battery.subentry_id,
        )


class SettingSwitch(SlemsSystemEntity, SwitchEntity, RestoreEntity):
    """Boolean runtime setting, restored after a restart."""

    def __init__(self, coordinator: SlemsCoordinator, key: str, attribute: str) -> None:
        super().__init__(coordinator, key)
        self._attribute = attribute

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._set(last_state.state == STATE_ON)

    def _set(self, value: bool) -> None:
        setattr(self.coordinator.settings, self._attribute, value)
        if self._attribute == "vacation" and self.hass is not None:
            self.hass.async_create_task(self.coordinator.async_refresh_forecast())

    @property
    def is_on(self) -> bool:
        return getattr(self.coordinator.settings, self._attribute)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
        self.async_write_ha_state()


class BatteryEnabledSwitch(SlemsBatteryEntity, SwitchEntity, RestoreEntity):
    """Temporarily exclude a battery from planning and control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, battery: BatteryRuntime) -> None:
        super().__init__(coordinator, battery, "battery_enabled")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self.battery.enabled = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.battery.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.battery.enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.battery.enabled = False
        if self.coordinator.settings.operating_mode is OperatingMode.ACTIVE:
            await self.coordinator.async_release_battery(self.battery)
        self.async_write_ha_state()
