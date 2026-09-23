"""Base entities for SLEMS."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .consumers import ConsumerConfig
from .coordinator import BatteryRuntime, SlemsCoordinator


class SlemsSystemEntity(CoordinatorEntity[SlemsCoordinator]):
    """Entity belonging to the central SLEMS system device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SlemsCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="SLEMS",
            manufacturer=MANUFACTURER,
            model="Energy manager",
        )


class SlemsBatteryEntity(CoordinatorEntity[SlemsCoordinator]):
    """Entity belonging to one battery device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SlemsCoordinator, battery: BatteryRuntime, key: str
    ) -> None:
        super().__init__(coordinator)
        self.battery = battery
        self._attr_translation_key = key
        self._attr_unique_id = f"{battery.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery.subentry_id)},
            name=battery.name,
            manufacturer=MANUFACTURER,
            model=battery.driver.model_name,
            via_device_id=coordinator.system_device_id,
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.battery.subentry_id in self.coordinator.data.batteries
        )


class SlemsConsumerEntity(CoordinatorEntity[SlemsCoordinator]):
    """Entity belonging to one consumer device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SlemsCoordinator, consumer: ConsumerConfig, key: str
    ) -> None:
        super().__init__(coordinator)
        self.consumer = consumer
        self._attr_translation_key = key
        self._attr_unique_id = f"{consumer.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, consumer.subentry_id)},
            name=consumer.name,
            manufacturer=MANUFACTURER,
            model="Consumer",
            via_device_id=coordinator.system_device_id,
        )
