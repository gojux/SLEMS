"""Select platform: global operating mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import OperatingMode
from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsSystemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the operating mode select."""
    async_add_entities([OperatingModeSelect(entry.runtime_data)])


class OperatingModeSelect(SlemsSystemEntity, SelectEntity, RestoreEntity):
    """Off / simulation (read-only) / active."""

    _attr_options = [mode.value for mode in OperatingMode]

    def __init__(self, coordinator: SlemsCoordinator) -> None:
        super().__init__(coordinator, "operating_mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self.coordinator.settings.operating_mode = OperatingMode(last_state.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.settings.operating_mode.value

    async def async_select_option(self, option: str) -> None:
        mode = OperatingMode(option)
        settings = self.coordinator.settings
        previous = settings.operating_mode
        settings.operating_mode = mode
        if previous is OperatingMode.ACTIVE and mode is not OperatingMode.ACTIVE:
            await self.coordinator.async_release_batteries()
        self.coordinator.controller.request()
        # Also refresh the other entities (control status, plans) right away.
        self.coordinator.async_update_listeners()
