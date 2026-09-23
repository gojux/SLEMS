"""Switch platform: vacation mode and import peak shaving."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsSystemEntity

# Switch key -> attribute of ControlSettings. Both default to off.
SETTING_SWITCHES: dict[str, str] = {
    "vacation": "vacation",
    "peak_shaving": "peak_shaving",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setting switches."""
    async_add_entities(
        SettingSwitch(entry.runtime_data, key, attribute)
        for key, attribute in SETTING_SWITCHES.items()
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

    @property
    def is_on(self) -> bool:
        return getattr(self.coordinator.settings, self._attribute)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
        self.async_write_ha_state()
