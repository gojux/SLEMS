"""Binary sensor platform."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsSystemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities([ChargeSecuredSensor(entry.runtime_data)])


class ChargeSecuredSensor(SlemsSystemEntity, BinarySensorEntity):
    """On when the expected PV surplus will fill the batteries."""

    def __init__(self, coordinator: SlemsCoordinator) -> None:
        super().__init__(coordinator, "charge_secured")

    @property
    def is_on(self) -> bool | None:
        allocation = self.coordinator.data.allocation
        return None if allocation is None else allocation.charge_secured
