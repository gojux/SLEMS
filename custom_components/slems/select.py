"""Select platform: global operating mode; role in the feed-in cap and daily target of each consumer."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import LEGACY_CAP_MODES, CapMode, OperatingMode, TargetSource, TargetType
from .consumers import ConsumerConfig
from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsConsumerEntity, SlemsSystemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the operating mode and the feed-in cap mode of every controllable consumer."""
    coordinator = entry.runtime_data
    async_add_entities([OperatingModeSelect(coordinator)])
    for consumer in coordinator.consumers:
        if consumer.controllable:
            async_add_entities(
                [
                    ConsumerCapModeSelect(coordinator, consumer),
                    ConsumerTargetTypeSelect(coordinator, consumer),
                    ConsumerTargetSourceSelect(coordinator, consumer),
                ],
                config_subentry_id=consumer.subentry_id,
            )


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


class ConsumerCapModeSelect(SlemsConsumerEntity, SelectEntity, RestoreEntity):
    """Part of the consumer in the feed-in cap: supporting, normal or never."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [mode.value for mode in CapMode]

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "cap_mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        mode = LEGACY_CAP_MODES.get(last_state.state)
        if mode is None and last_state.state in self._attr_options:
            mode = CapMode(last_state.state)
        if mode is not None:
            self.coordinator.consumer_cap_modes[self.consumer.subentry_id] = mode

    @property
    def current_option(self) -> str:
        return self.coordinator.cap_mode(self.consumer.subentry_id).value

    async def async_select_option(self, option: str) -> None:
        self.coordinator.consumer_cap_modes[self.consumer.subentry_id] = CapMode(option)
        self.async_write_ha_state()


class _TargetSelect(SlemsConsumerEntity, SelectEntity, RestoreEntity):
    """Option of a consumer's daily target (see consumer_targets), restored."""

    _attr_entity_category = EntityCategory.CONFIG
    _attribute: str
    _enum: type

    @property
    def _settings(self):
        return self.coordinator.consumer_targets[self.consumer.subentry_id]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            setattr(self._settings, self._attribute, self._enum(last_state.state))

    @property
    def current_option(self) -> str:
        return getattr(self._settings, self._attribute).value

    async def async_select_option(self, option: str) -> None:
        setattr(self._settings, self._attribute, self._enum(option))
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()


class ConsumerTargetTypeSelect(_TargetSelect):
    """Kind of daily target; the temperature target needs temperature sensors."""

    _attribute = "type"
    _enum = TargetType

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_type")
        self._attr_options = [
            t.value
            for t in TargetType
            if t is not TargetType.TEMPERATURE or consumer.temperature_entity_ids
        ]


class ConsumerTargetSourceSelect(_TargetSelect):
    """What may cover the rest of the daily target in time."""

    _attribute = "source"
    _enum = TargetSource
    _attr_options = [s.value for s in TargetSource]

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_source")
