"""Time platform: deadline of each consumer's daily target (see consumer_targets)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .consumers import ConsumerConfig
from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsConsumerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the deadline of every controllable consumer."""
    coordinator = entry.runtime_data
    for consumer in coordinator.consumers:
        if consumer.controllable:
            async_add_entities(
                [ConsumerTargetDeadline(coordinator, consumer)],
                config_subentry_id=consumer.subentry_id,
            )


class ConsumerTargetDeadline(SlemsConsumerEntity, TimeEntity, RestoreEntity):
    """End of the daily target's period (local time), restored after a restart."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_deadline")

    @property
    def _settings(self):
        return self.coordinator.consumer_targets[self.consumer.subentry_id]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._settings.deadline = time.fromisoformat(last_state.state)
            except ValueError:
                pass

    @property
    def native_value(self) -> time:
        return self._settings.deadline

    async def async_set_value(self, value: time) -> None:
        self._settings.deadline = value.replace(second=0, microsecond=0)
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
