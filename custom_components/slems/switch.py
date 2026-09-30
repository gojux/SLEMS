"""Switch platform: runtime settings, battery enabling and cell balancing."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OperatingMode
from .consumers import ConsumerConfig
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsBatteryEntity, SlemsConsumerEntity, SlemsSystemEntity

# Switch key -> attribute of ControlSettings (defaults in ControlSettings).
SETTING_SWITCHES: dict[str, str] = {
    "vacation": "vacation",
    "peak_shaving": "peak_shaving",
    "night_discharge": "night_discharge",
    "auto_gain": "auto_gain",
    "grid_friendly_charging": "grid_friendly_charging",
    "temperature_limit": "temperature_limit",
    "peak_shaving_auto": "peak_shaving_auto",
    "feed_in_cap": "feed_in_cap",
    "feed_in_cap_auto_buffer": "feed_in_cap_auto_buffer",
    "grid_friendly_buffer_auto": "grid_friendly_buffer_auto",
    "charge_secured_buffer_auto": "charge_secured_buffer_auto",
    "grid_targets_auto": "grid_targets_auto",
    "timing_auto": "timing_auto",
    "night_reserve_auto": "night_reserve_auto",
    "regular_full_charge": "regular_full_charge",
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
        entities: list[SwitchEntity] = [
            BatteryEnabledSwitch(coordinator, battery),
            LearnCapacitySwitch(coordinator, battery),
        ]
        if battery.supports_balancing:
            entities.append(CellBalancingSwitch(coordinator, battery))
        if battery.driver.has_connection:
            entities.append(CommunicationPauseSwitch(coordinator, battery))
        async_add_entities(entities, config_subentry_id=battery.subentry_id)
    for consumer in coordinator.consumers:
        if consumer.controllable:
            async_add_entities(
                [
                    ConsumerControlSwitch(coordinator, consumer),
                    ConsumerLearningSwitch(coordinator, consumer),
                    ConsumerTargetPrioritySwitch(coordinator, consumer),
                    ConsumerTargetEarliestSwitch(coordinator, consumer),
                ],
                config_subentry_id=consumer.subentry_id,
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
        self.battery.leaving_until = None
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.battery.enabled = False
        coordinator = self.coordinator
        if coordinator.settings.operating_mode is OperatingMode.ACTIVE:
            # A discharging battery ramps out first, the others are released at once.
            if not coordinator.controller.disable_battery(self.battery):
                await coordinator.controller.async_release([self.battery])
        self.async_write_ha_state()


class CommunicationPauseSwitch(SlemsBatteryEntity, SwitchEntity):
    """Pause all communication with the battery, e.g. for a firmware update.

    On: the battery is handed back to its own logic and the connection is
    closed for the configured time, then SLEMS reconnects by itself. Also
    switched on automatically when the battery reports a firmware update.
    """

    def __init__(self, coordinator: SlemsCoordinator, battery: BatteryRuntime) -> None:
        super().__init__(coordinator, battery, "communication_paused")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.battery.communication_paused

    @property
    def extra_state_attributes(self) -> dict:
        until = self.battery.paused_until if self.battery.communication_paused else None
        return {
            "paused_until": dt_util.utc_from_timestamp(until).isoformat() if until else None,
            # manual / firmware_update
            "reason": self.battery.pause_reason if until else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_pause_communication(self.battery, "manual")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.resume_communication(self.battery)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()


class CellBalancingSwitch(SlemsBatteryEntity, SwitchEntity):
    """Start or cancel an active cell balancing run.

    On: the battery leaves the normal operation and runs the balancing profile.
    It can only be started in operating mode active; outside of it a running
    one pauses. Off: the run is cancelled and the battery returns to normal
    operation. It switches itself off when the run ends. The run survives a
    restart (stored by the coordinator).
    """

    def __init__(self, coordinator: SlemsCoordinator, battery: BatteryRuntime) -> None:
        super().__init__(coordinator, battery, "cell_balancing")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.battery.enabled

    @property
    def is_on(self) -> bool:
        return self.battery.balancing_requested

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.battery.balancing_requested:
            return
        if self.coordinator.settings.operating_mode is not OperatingMode.ACTIVE:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="balancing_requires_active"
            )
        if self.battery.communication_paused:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="balancing_communication_paused"
            )
        self.coordinator.start_balancing(self.battery)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.end_balancing(self.battery, "cancelled")
        self.async_write_ha_state()


class ConsumerControlSwitch(SlemsConsumerEntity, SwitchEntity, RestoreEntity):
    """Let SLEMS control the consumer; off: measured only.

    Switching it off in operating mode active sets the consumer to 0 W (or
    off) once; afterwards SLEMS leaves it alone and plans it like an
    uncontrolled load.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "consumer_control")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state != STATE_ON:
            self.coordinator.consumer_control_disabled.add(self.consumer.subentry_id)

    @property
    def is_on(self) -> bool:
        return self.consumer.subentry_id not in self.coordinator.consumer_control_disabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.consumer_control_disabled.discard(self.consumer.subentry_id)
        self.coordinator.controller.request()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.consumer_control_disabled.add(self.consumer.subentry_id)
        await self.coordinator.controller.async_release_consumer(self.consumer)
        self.async_write_ha_state()


class LearnCapacitySwitch(SlemsBatteryEntity, SwitchEntity, RestoreEntity):
    """Plan with the learned usable capacity instead of the configured one."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, battery: BatteryRuntime) -> None:
        super().__init__(coordinator, battery, "learn_capacity")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self.battery.learn_capacity = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.battery.learn_capacity

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.battery.learn_capacity = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.battery.learn_capacity = False
        self.async_write_ha_state()


class ConsumerLearningSwitch(SlemsConsumerEntity, SwitchEntity, RestoreEntity):
    """Use the learned power and thermostat behaviour of the consumer."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "consumer_learning")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None and last_state.state == STATE_ON:
            self.coordinator.consumer_learning.add(self.consumer.subentry_id)

    @property
    def is_on(self) -> bool:
        return self.consumer.subentry_id in self.coordinator.consumer_learning

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.consumer_learning.add(self.consumer.subentry_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.consumer_learning.discard(self.consumer.subentry_id)
        self.async_write_ha_state()


class ConsumerTargetPrioritySwitch(SlemsConsumerEntity, SwitchEntity, RestoreEntity):
    """Daily target: surplus before the batteries when the forecast is short (see consumer_targets)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_priority")

    @property
    def _settings(self):
        return self.coordinator.consumer_targets[self.consumer.subentry_id]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._settings.priority = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self._settings.priority

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._settings.priority = True
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._settings.priority = False
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()


class ConsumerTargetEarliestSwitch(SlemsConsumerEntity, SwitchEntity, RestoreEntity):
    """Daily target: not switched on before the earliest start (see consumer_targets)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SlemsCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "target_earliest_enabled")

    @property
    def _settings(self):
        return self.coordinator.consumer_targets[self.consumer.subentry_id]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._settings.earliest_enabled = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self._settings.earliest_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._settings.earliest_enabled = True
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._settings.earliest_enabled = False
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
