"""Sensor platform for SLEMS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

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
from homeassistant.util import dt as dt_util

from .const import CONF_PV_FORECAST_ENTRIES, CONF_WEATHER_ENTITY
from .coordinator import SlemsConfigEntry, SlemsCoordinator, SystemSnapshot
from .drivers import BatteryTelemetry
from .allocation import Strategy
from .entity import SlemsBatteryEntity, SlemsConsumerEntity, SlemsSystemEntity
from .pv_forecast import energy_on_day


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
    """Capacity weighted SoC over all enabled, readable batteries."""
    energy = capacity = 0.0
    for battery in coordinator.batteries:
        telemetry = snapshot.batteries.get(battery.subentry_id)
        if not battery.enabled or telemetry is None or telemetry.soc_pct is None:
            continue
        battery_capacity = battery.driver.capabilities.capacity_wh
        energy += telemetry.soc_pct * battery_capacity
        capacity += battery_capacity
    return energy / capacity if capacity else None


def _pv_forecast_kwh(day_offset: int) -> Callable[[SystemSnapshot, SlemsCoordinator], float | None]:
    def value(snapshot: SystemSnapshot, _: SlemsCoordinator) -> float | None:
        if snapshot.pv_forecast is None:
            return None
        day = dt_util.now().date() + timedelta(days=day_offset)
        return energy_on_day(snapshot.pv_forecast, day) / 1000

    return value


def _energy_forecast(key: str, day_offset: int) -> SystemSensorDescription:
    return SystemSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_pv_forecast_kwh(day_offset),
    )


SYSTEM_SENSORS: tuple[SystemSensorDescription, ...] = (
    SystemSensorDescription(**_power("grid_power"), value_fn=lambda s, _: s.grid_power_w),
    SystemSensorDescription(**_power("pv_power"), value_fn=lambda s, _: s.pv_power_w),
    SystemSensorDescription(**_power("house_power"), value_fn=lambda s, _: s.house_power_w),
    SystemSensorDescription(**_power("base_load"), value_fn=lambda s, _: s.base_load_w),
    SystemSensorDescription(
        **_power("total_consumption"), value_fn=lambda s, _: s.total_consumption_w
    ),
    _energy_forecast("pv_forecast_today", 0),
    _energy_forecast("pv_forecast_tomorrow", 1),
    SystemSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s, _: s.outdoor_temperature_c,
    ),
    SystemSensorDescription(
        **_power("grid_power_filtered"),
        entity_registry_enabled_default=False,
        value_fn=lambda s, _: s.grid_power_filtered_w,
    ),
    SystemSensorDescription(
        **_power("available_power"), value_fn=lambda s, _: s.available_power_w
    ),
    SystemSensorDescription(
        **_power("planned_battery_power"),
        value_fn=lambda s, _: s.allocation.battery_power_w if s.allocation else None,
    ),
    SystemSensorDescription(
        key="allocation_strategy",
        translation_key="allocation_strategy",
        device_class=SensorDeviceClass.ENUM,
        options=[strategy.value for strategy in Strategy],
        value_fn=lambda s, _: s.allocation.strategy.value if s.allocation else None,
    ),
    SystemSensorDescription(
        key="night_discharge_target_soc",
        translation_key="night_discharge_target_soc",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda s, _: (
            s.night_discharge.target_soc_pct if s.night_discharge else None
        ),
    ),
    SystemSensorDescription(
        **_power("night_discharge_power"),
        value_fn=lambda s, _: s.night_discharge.power_w if s.night_discharge else None,
    ),
    SystemSensorDescription(
        key="expected_surplus_energy",
        translation_key="expected_surplus_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s, _: (
            None if s.expected_surplus_wh is None else s.expected_surplus_wh / 1000
        ),
    ),
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
    config = entry.options or entry.data
    async_add_entities(
        SystemSensor(coordinator, description)
        for description in SYSTEM_SENSORS
        if (config.get(CONF_PV_FORECAST_ENTRIES) or not description.key.startswith("pv_forecast"))
        and (config.get(CONF_WEATHER_ENTITY) or description.key != "outdoor_temperature")
    )
    async_add_entities(
        ConsumptionForecastSensor(coordinator, key, day_offset)
        for key, day_offset in (("consumption_forecast_today", 0), ("consumption_forecast_tomorrow", 1))
    )
    for battery in coordinator.batteries:
        extra_keys = battery.driver.extra_telemetry_keys
        descriptions = BATTERY_SENSORS + tuple(
            d for d in BATTERY_EXTRA_SENSORS if d.key in extra_keys
        )
        async_add_entities(
            [
                *(BatterySensor(coordinator, battery, d) for d in descriptions),
                EfficiencySensor(coordinator, battery),
            ],
            config_subentry_id=battery.subentry_id,
        )
    for consumer in coordinator.consumers:
        if consumer.controllable:
            async_add_entities(
                [PlannedConsumerPowerSensor(coordinator, consumer)],
                config_subentry_id=consumer.subentry_id,
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


class EfficiencySensor(SlemsBatteryEntity, SensorEntity):
    """Round trip efficiency currently used for the battery."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "round_trip_efficiency")

    @property
    def available(self) -> bool:
        # Also meaningful while the battery is temporarily unreachable.
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float:
        return self.battery.efficiency.round_trip * 100

    @property
    def extra_state_attributes(self) -> dict:
        efficiency = self.battery.efficiency
        return {"mode": efficiency.mode.value, "measured": efficiency.is_learned}


class PlannedConsumerPowerSensor(SlemsConsumerEntity, SensorEntity):
    """Power SLEMS assigns to a consumer (commanded only in active mode)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SlemsCoordinator, consumer) -> None:
        super().__init__(coordinator, consumer, "planned_power")

    @property
    def native_value(self) -> float | None:
        allocation = self.coordinator.data.allocation
        if allocation is None:
            return None
        return allocation.consumer_power_w.get(self.consumer.subentry_id)

    @property
    def extra_state_attributes(self) -> dict:
        state = self.coordinator.data.consumers.get(self.consumer.subentry_id)
        return {"blocked": state.blocked if state else None}


class ConsumptionForecastSensor(SlemsSystemEntity, SensorEntity):
    """Forecast consumption of a day; hourly values as attribute."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1
    _unrecorded_attributes = frozenset({"hourly"})

    def __init__(self, coordinator: SlemsCoordinator, key: str, day_offset: int) -> None:
        super().__init__(coordinator, key)
        self._day_offset = day_offset

    def _day(self):
        return dt_util.now().date() + timedelta(days=self._day_offset)

    @property
    def native_value(self) -> float | None:
        forecast = self.coordinator.data.consumption_forecast
        if forecast is None:
            return None
        return forecast.energy_on_day(self._day()) / 1000

    @property
    def extra_state_attributes(self) -> dict | None:
        forecast = self.coordinator.data.consumption_forecast
        if forecast is None:
            return None
        day = self._day()
        return {
            "mean_temperature": forecast.temperature.get(day),
            "hourly": [
                {
                    "start": dt_util.as_local(start).isoformat(),
                    "total_wh": round(total),
                    "base_wh": round(forecast.base[start]),
                    "heat_pump_wh": round(forecast.heat_pump[start]),
                }
                for start, total in forecast.total.items()
                if dt_util.as_local(start).date() == day
            ],
        }
