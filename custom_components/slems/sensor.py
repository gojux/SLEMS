"""Sensor platform for SLEMS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SlemsConfigEntry, SlemsCoordinator, SystemSnapshot
from .drivers import BatteryTelemetry
from .entity import SlemsBatteryEntity, SlemsSystemEntity


@dataclass(frozen=True, kw_only=True)
class SystemSensorDescription(SensorEntityDescription):
    """Describes a system level sensor."""

    value_fn: Callable[[SystemSnapshot, SlemsCoordinator], float | None]


@dataclass(frozen=True, kw_only=True)
class BatterySensorDescription(SensorEntityDescription):
    """Describes a per battery sensor."""

    value_fn: Callable[[BatteryTelemetry], float | str | bool | None]


def _power(key: str, **kwargs) -> dict:
    return {
        "key": key,
        "translation_key": key,
        "device_class": SensorDeviceClass.POWER,
        "state_class": SensorStateClass.MEASUREMENT,
        "native_unit_of_measurement": UnitOfPower.WATT,
        "suggested_display_precision": 0,
        **kwargs,
    }


def _average_soc(snapshot: SystemSnapshot, coordinator: SlemsCoordinator) -> float | None:
    """Capacity weighted SoC over all readable batteries."""
    energy = capacity = 0.0
    for battery in coordinator.batteries:
        telemetry = snapshot.batteries.get(battery.subentry_id)
        if telemetry is None or telemetry.soc_pct is None:
            continue
        battery_capacity = battery.driver.capabilities.capacity_wh
        energy += telemetry.soc_pct * battery_capacity
        capacity += battery_capacity
    return energy / capacity if capacity else None


SYSTEM_SENSORS: tuple[SystemSensorDescription, ...] = (
    SystemSensorDescription(**_power("grid_power"), value_fn=lambda s, _: s.grid_power_w),
    SystemSensorDescription(**_power("pv_power"), value_fn=lambda s, _: s.pv_power_w),
    SystemSensorDescription(**_power("house_power"), value_fn=lambda s, _: s.house_power_w),
    SystemSensorDescription(
        **_power("battery_power_total"), value_fn=lambda s, _: s.battery_power_w
    ),
    SystemSensorDescription(
        key="battery_soc_total",
        translation_key="battery_soc_total",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_average_soc,
    ),
)

BATTERY_SENSORS: tuple[BatterySensorDescription, ...] = (
    BatterySensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda t: t.soc_pct,
    ),
    BatterySensorDescription(**_power("battery_power"), value_fn=lambda t: t.power_w),
)

# Optional sensors, created only if the driver reports the key.
BATTERY_EXTRA_SENSORS: tuple[BatterySensorDescription, ...] = (
    BatterySensorDescription(
        **_power("ac_power"),
        entity_registry_enabled_default=False,
        value_fn=lambda t: t.extra.get("ac_power"),
    ),
    BatterySensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda t: t.extra.get("battery_voltage"),
    ),
    BatterySensorDescription(
        key="internal_temperature",
        translation_key="internal_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda t: t.extra.get("internal_temperature"),
    ),
    BatterySensorDescription(
        key="inverter_state",
        translation_key="inverter_state",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "sleep",
            "standby",
            "charge",
            "discharge",
            "backup",
            "ota_upgrade",
            "bypass",
            "unknown",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda t: t.extra.get("inverter_state"),
    ),
    BatterySensorDescription(
        key="total_charging_energy",
        translation_key="total_charging_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda t: t.extra.get("total_charging_energy"),
    ),
    BatterySensorDescription(
        key="total_discharging_energy",
        translation_key="total_discharging_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda t: t.extra.get("total_discharging_energy"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SlemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SLEMS sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SystemSensor(coordinator, description) for description in SYSTEM_SENSORS
    )
    for battery in coordinator.batteries:
        extra_keys = battery.driver.extra_telemetry_keys
        descriptions = BATTERY_SENSORS + tuple(
            d for d in BATTERY_EXTRA_SENSORS if d.key in extra_keys
        )
        async_add_entities(
            (BatterySensor(coordinator, battery, d) for d in descriptions),
            config_subentry_id=battery.subentry_id,
        )


class SystemSensor(SlemsSystemEntity, SensorEntity):
    """System level sensor."""

    entity_description: SystemSensorDescription

    def __init__(
        self, coordinator: SlemsCoordinator, description: SystemSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)


class BatterySensor(SlemsBatteryEntity, SensorEntity):
    """Per battery sensor."""

    entity_description: BatterySensorDescription

    def __init__(self, coordinator, battery, description: BatterySensorDescription) -> None:
        super().__init__(coordinator, battery, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | bool | None:
        telemetry = self.coordinator.data.batteries.get(self.battery.subentry_id)
        if telemetry is None:
            return None
        return self.entity_description.value_fn(telemetry)
