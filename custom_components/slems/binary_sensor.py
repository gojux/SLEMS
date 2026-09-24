"""Binary sensor platform."""

from __future__ import annotations

import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .entity import SlemsBatteryEntity, SlemsSystemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities([ChargeSecuredSensor(coordinator)])
    for battery in coordinator.batteries:
        if battery.driver.capabilities.controllable:
            async_add_entities(
                [NotRespondingSensor(coordinator, battery)],
                config_subentry_id=battery.subentry_id,
            )


class ChargeSecuredSensor(SlemsSystemEntity, BinarySensorEntity):
    """On when the expected PV surplus will fill the batteries."""

    def __init__(self, coordinator: SlemsCoordinator) -> None:
        super().__init__(coordinator, "charge_secured")

    @property
    def is_on(self) -> bool | None:
        allocation = self.coordinator.data.allocation
        return None if allocation is None else allocation.charge_secured


class NotRespondingSensor(SlemsBatteryEntity, BinarySensorEntity):
    """On while the battery is excluded because it did not deliver the commanded power."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SlemsCoordinator, battery: BatteryRuntime) -> None:
        super().__init__(coordinator, battery, "not_responding")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.battery.not_responding

    @property
    def extra_state_attributes(self) -> dict:
        delivery = self.battery.delivery
        retry_at = None
        if self.battery.not_responding and delivery.excluded_until is not None:
            remaining = delivery.excluded_until - time.monotonic()
            retry_at = dt_util.utc_from_timestamp(
                dt_util.utcnow().timestamp() + remaining
            ).isoformat()
        return {
            # charge_not_delivered / discharge_not_delivered / write_failed
            "reason": delivery.reason,
            "failures": delivery.fail_count,
            "retry_at": retry_at,
        }
