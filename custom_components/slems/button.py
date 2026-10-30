"""Buttons of SLEMS: learn the phase of a battery or consumer anew, probe a battery's response."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsBatteryEntity, SlemsConsumerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SLEMS buttons."""
    coordinator = entry.runtime_data
    for battery in coordinator.batteries:
        if battery.driver.capabilities.controllable:
            async_add_entities([BatteryProbeButton(coordinator, battery)], config_subentry_id=battery.subentry_id)
    if not coordinator.phases_measured:
        return
    for battery in coordinator.batteries:
        async_add_entities([BatteryRelearnPhaseButton(coordinator, battery)], config_subentry_id=battery.subentry_id)
    for consumer in coordinator.consumers:
        if consumer.included_in_meter:
            async_add_entities(
                [ConsumerRelearnPhaseButton(coordinator, consumer)], config_subentry_id=consumer.subentry_id
            )


class _RelearnPhase:
    """Forgets the phase and learns it anew; if the same comes out, Repairs asks to confirm it."""

    _attr_entity_category = EntityCategory.CONFIG
    coordinator: SlemsCoordinator
    _subentry_id: str

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        self.coordinator.relearn_phase(self._subentry_id)


class BatteryRelearnPhaseButton(_RelearnPhase, SlemsBatteryEntity, ButtonEntity):
    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "relearn_phase")
        self._subentry_id = battery.subentry_id


class ConsumerRelearnPhaseButton(_RelearnPhase, SlemsConsumerEntity, ButtonEntity):
    def __init__(self, coordinator: SlemsCoordinator, consumer) -> None:
        super().__init__(coordinator, consumer, "relearn_phase")
        self._subentry_id = consumer.subentry_id


# Reasons a probe step cannot be asked for at all right now (the others only delay it).
_PROBE_REFUSED = frozenset({"not_controllable", "slow", "not_active", "not_participating"})


class BatteryProbeButton(SlemsBatteryEntity, ButtonEntity):
    """Measures the battery's dead time and phase with a probe step as soon as the grid is quiet
    (see response_probe)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "probe_response")

    async def async_press(self) -> None:
        if (blocked := self.coordinator.probe_blocked(self.battery)) in _PROBE_REFUSED:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=f"probe_{blocked}")
        self.coordinator.request_probe(self.battery)
