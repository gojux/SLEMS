"""Select platform: global operating mode; battery support, role in the feed-in cap and daily target of each consumer."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .battery_support import BatterySupport
from .const import LEGACY_CAP_MODES, CapMode, OperatingMode, TargetSensor, TargetSource, TargetType
from .consumers import ConsumerConfig
from .coordinator import SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsConsumerEntity, SlemsSystemEntity
from .market_prices import PriceSource


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the operating mode and the options of the consumers."""
    coordinator = entry.runtime_data
    async_add_entities([OperatingModeSelect(coordinator), PriceSourceSelect(coordinator)])
    for consumer in coordinator.consumers:
        # The batteries can only cover consumers behind the smart meter.
        if consumer.included_in_meter:
            async_add_entities(
                [ConsumerBatterySupportSelect(coordinator, consumer)],
                config_subentry_id=consumer.subentry_id,
            )
        if consumer.controllable:
            async_add_entities(
                [
                    ConsumerCapModeSelect(coordinator, consumer),
                    ConsumerTargetTypeSelect(coordinator, consumer),
                    ConsumerTargetSourceSelect(coordinator, consumer),
                    *(
                        [ConsumerTargetSensorSelect(coordinator, consumer)]
                        if len(consumer.temperature_entity_ids) >= 2
                        else []
                    ),
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
            await self.coordinator.controller.async_release(self.coordinator.batteries)
        self.coordinator.controller.request()
        # Also refresh the other entities (control status, plans) right away.
        self.coordinator.async_update_listeners()


class PriceSourceSelect(SlemsSystemEntity, SelectEntity, RestoreEntity):
    """Where the day-ahead prices come from (default by the country of Home Assistant)."""

    _attr_options = [source.value for source in PriceSource]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:database-arrow-down"

    def __init__(self, coordinator: SlemsCoordinator) -> None:
        super().__init__(coordinator, "price_source")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self.coordinator.market_prices.set_source(PriceSource(last_state.state))

    @property
    def current_option(self) -> str:
        return self.coordinator.market_prices.source.value

    async def async_select_option(self, option: str) -> None:
        self.coordinator.market_prices.set_source(PriceSource(option))
        self.async_write_ha_state()


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


class ConsumerBatterySupportSelect(SlemsConsumerEntity, SelectEntity, RestoreEntity):
    """How far the batteries may cover the consumer (see battery_support)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [support.value for support in BatterySupport]

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "battery_support")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self.coordinator.battery_support[self.consumer.subentry_id] = BatterySupport(
                last_state.state
            )

    @property
    def current_option(self) -> str:
        return self.coordinator.support_of(self.consumer.subentry_id).value

    @property
    def extra_state_attributes(self) -> dict:
        budget = self.coordinator.support_budget.budget_wh
        return {
            # The batteries may cover the consumer right now.
            "battery_covers": self.consumer.subentry_id not in self.coordinator.unsupported,
            "budget_kwh": None if budget is None else round(budget / 1000, 2),
        }

    async def async_select_option(self, option: str) -> None:
        self.coordinator.battery_support[self.consumer.subentry_id] = BatterySupport(option)
        self.coordinator.controller.request()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


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


class ConsumerTargetSensorSelect(_TargetSelect):
    """Temperature sensor (or the mean) the temperature target applies to."""

    _attribute = "sensor"
    _enum = TargetSensor
    _attr_options = [s.value for s in TargetSensor]

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_sensor")
