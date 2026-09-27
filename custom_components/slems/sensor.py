"""Sensor platform for SLEMS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import time

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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_PV_FORECAST_ENTRIES, CONF_WEATHER_ENTITY
from .coordinator import SlemsConfigEntry, SlemsCoordinator, SystemSnapshot
from .drivers import BatteryTelemetry
from .allocation import Strategy
from .battery_distribution import LossModel
from .cell_balancing import TOP_ZONE_V, BalancingPhase, balance_status
from .controller import ControlStatus
from .entity import SlemsBatteryEntity, SlemsConsumerEntity, SlemsSystemEntity
from .forecast.accuracy import Accuracy
from .problems import CAP_EXCEEDED_AFTER_S
from .pv_forecast import energy_on_day


@dataclass(frozen=True, kw_only=True)
class SystemSensorDescription(SensorEntityDescription):
    """Describes a system level sensor."""

    value_fn: Callable[[SystemSnapshot, SlemsCoordinator], float | None]
    attributes_fn: Callable[[SystemSnapshot, SlemsCoordinator], dict] | None = None


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
        if not battery.plannable or telemetry is None or telemetry.soc_pct is None:
            continue
        battery_capacity = battery.capacity_wh
        energy += telemetry.soc_pct * battery_capacity
        capacity += battery_capacity
    return energy / capacity if capacity else None


def _stored_energy_kwh(snapshot: SystemSnapshot, coordinator: SlemsCoordinator) -> float | None:
    """Energy stored in all enabled, readable batteries (kWh)."""
    energy = None
    for battery in coordinator.batteries:
        telemetry = snapshot.batteries.get(battery.subentry_id)
        if not battery.plannable or telemetry is None or telemetry.soc_pct is None:
            continue
        stored = telemetry.soc_pct / 100 * battery.capacity_wh / 1000
        energy = (energy or 0.0) + stored
    return energy


def _capacity_kwh(snapshot: SystemSnapshot, coordinator: SlemsCoordinator) -> float | None:
    """Capacity of the batteries counted in the stored energy total (kWh)."""
    capacities = [
        battery.capacity_wh / 1000
        for battery in coordinator.batteries
        if battery.plannable
        and (telemetry := snapshot.batteries.get(battery.subentry_id)) is not None
        and telemetry.soc_pct is not None
    ]
    return round(sum(capacities), 2) if capacities else None


def _accuracy_value(accuracy: Accuracy | None) -> float | None:
    """Accuracy in %: 100 − mean daily error."""
    if accuracy is None or accuracy.daily_error is None:
        return None
    return round(max(0.0, 100 * (1 - accuracy.daily_error)), 1)


def _accuracy_attributes(accuracy: Accuracy | None, tomorrow_wh: float | None) -> dict:
    def pct(value: float | None) -> float | None:
        return None if value is None else round(100 * value, 1)

    tomorrow = dt_util.now().date() + timedelta(days=1)
    expected = accuracy.expected_error(tomorrow) if accuracy else None
    return {
        "evaluated_days": len(accuracy.days) if accuracy else 0,
        # Mean |error| of the daily energy and its signed mean (+: too high).
        "daily_error_pct": pct(accuracy.daily_error) if accuracy else None,
        "bias_pct": pct(accuracy.bias) if accuracy else None,
        "hourly_error_pct": pct(accuracy.hourly_error) if accuracy else None,
        "tomorrow_forecast_kwh": None if tomorrow_wh is None else round(tomorrow_wh / 1000, 2),
        "tomorrow_expected_error_kwh": (
            None
            if tomorrow_wh is None or expected is None
            else round(tomorrow_wh * expected / 1000, 2)
        ),
        "days": [
            {
                "date": d.day.isoformat(),
                "forecast_kwh": round(d.forecast_wh / 1000, 2),
                "actual_kwh": round(d.actual_wh / 1000, 2),
            }
            for d in (accuracy.days if accuracy else [])
        ],
    }


def _consumption_accuracy_attributes(s: SystemSnapshot, c: SlemsCoordinator) -> dict:
    accuracy = c.forecaster.accuracy
    tomorrow = dt_util.now().date() + timedelta(days=1)
    forecast = s.consumption_forecast
    attributes = _accuracy_attributes(
        accuracy, forecast.energy_on_day(tomorrow) if forecast else None
    )
    attributes["history_days"] = accuracy.history_days if accuracy else 0
    attributes["heat_pump_days"] = accuracy.heat_pump_days if accuracy else 0
    return attributes


def _kwh(wh: float) -> float:
    return round(wh / 1000, 2)


def _time(moment) -> str | None:
    return moment.isoformat() if moment is not None else None


def _cap_absorb_kwh(s: SystemSnapshot, _: SlemsCoordinator) -> float | None:
    if s.feed_in_cap is None:
        return None
    return sum(block.absorbed_wh for block in s.feed_in_cap.day_blocks()) / 1000


def _cap_exceeded(c: SlemsCoordinator) -> bool:
    since = c.cap_exceeded_since
    return since is not None and time.monotonic() - since >= CAP_EXCEEDED_AFTER_S


def _cap_attributes(s: SystemSnapshot, c: SlemsCoordinator) -> dict:
    cap = s.feed_in_cap
    if cap is None:
        return {"limit_w": round(c.settings.feed_in_cap_limit_w), "limit_exceeded": _cap_exceeded(c)}
    day = cap.day_blocks()
    return {
        "limit_w": round(cap.limit_w),
        "peak_start": _time(day[0].start) if day else None,
        "peak_end": _time(day[-1].end) if day else None,
        "excess_kwh": _kwh(sum(b.excess_wh for b in day)),
        "curtailed_kwh": _kwh(sum(b.curtailed_wh for b in day)),
        "required_space_kwh": _kwh(cap.required_space_wh),
        "free_now_kwh": _kwh(cap.free_now_wh),
        "export_needed_kwh": _kwh(cap.export_needed_wh),
        "export_possible_kwh": _kwh(cap.export_possible_wh),
        "export_start": _time(cap.export_start),
        "export_until": _time(cap.export_until),
        "export_power_w": round(cap.export_power_w),
        "hold_charging": cap.hold_charging,
        "buffer_pct": (
            None if s.feed_in_cap_buffer_pct is None else round(s.feed_in_cap_buffer_pct, 1)
        ),
        "buffer_source": s.feed_in_cap_buffer_source,
        "buffer_days": s.feed_in_cap_buffer_days,
        "peaks": [
            {
                "start": _time(b.start),
                "end": _time(b.end),
                "excess_kwh": _kwh(b.excess_wh),
                "absorbed_kwh": _kwh(b.absorbed_wh),
                "curtailed_kwh": _kwh(b.curtailed_wh),
            }
            for b in cap.blocks
        ],
        "problems": cap.problems,
        "limit_exceeded": _cap_exceeded(c),
    }


def _pct(share: float | None) -> float | None:
    return None if share is None else round(share * 100, 1)


def _learned_attributes(s: SystemSnapshot, c: SlemsCoordinator) -> dict:
    """Learned values and their basis (see learning); None: not enough data yet."""
    now = dt_util.now()
    pv_share, pv_days = c.pv_overestimate
    consumption_share, consumption_days = c.consumption_underestimate
    today_pv, today_consumption = c._rest_of_day_energy(s, now)
    next_pv, next_consumption = c._next_day_energy(s, now)
    both = pv_share is not None and consumption_share is not None
    timing = c.learned_timing

    def kwh(wh: float | None) -> float | None:
        return None if wh is None else round(wh / 1000, 2)

    return {
        "pv_overestimate_pct": _pct(pv_share),
        "pv_days": pv_days,
        "consumption_underestimate_pct": _pct(consumption_share),
        "consumption_days": consumption_days,
        "grid_friendly_buffer_kwh": kwh(pv_share * today_pv if pv_share is not None else None),
        "charge_secured_buffer_kwh": kwh(
            pv_share * today_pv + consumption_share * today_consumption if both else None
        ),
        "night_buffer_kwh": kwh(
            pv_share * next_pv + consumption_share * next_consumption if both else None
        ),
        "charge_grid_target_w": c.grid_targets.target_w(True),
        "discharge_grid_target_w": c.grid_targets.target_w(False),
        "grid_target_samples": [len(c.grid_targets.charge), len(c.grid_targets.discharge)],
        "control_interval_s": timing[0] if timing else None,
        "surplus_average_window_s": timing[1] if timing else None,
        "night_reserve_pct": c.morning_gap.reserve_pct(c.settings.night_reserve_coverage_pct),
        "morning_days": len(c.morning_gap.days),
    }


def _learned_in_use(s: SystemSnapshot, c: SlemsCoordinator) -> int:
    """Number of learned values in effect (switched on and enough data)."""
    settings = c.settings
    a = _learned_attributes(s, c)
    return sum(
        (
            settings.grid_friendly_buffer_auto and a["grid_friendly_buffer_kwh"] is not None,
            settings.charge_secured_buffer_auto and a["charge_secured_buffer_kwh"] is not None,
            settings.grid_targets_auto and a["charge_grid_target_w"] is not None,
            settings.grid_targets_auto and a["discharge_grid_target_w"] is not None,
            settings.timing_auto and a["control_interval_s"] is not None,
            settings.night_reserve_auto and a["night_reserve_pct"] is not None,
        )
    ) + sum(1 for b in c.batteries if b.learn_capacity and b.capacity_learner.capacity_wh is not None)


def _pv_accuracy_attributes(s: SystemSnapshot, c: SlemsCoordinator) -> dict:
    tomorrow = dt_util.now().date() + timedelta(days=1)
    tomorrow_wh = energy_on_day(s.pv_forecast, tomorrow) if s.pv_forecast else None
    attributes = _accuracy_attributes(c.pv_accuracy.accuracy(), tomorrow_wh)
    attributes["recorded_days"] = len(c.pv_accuracy.days)
    return attributes


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
        key="control_status",
        translation_key="control_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in ControlStatus],
        value_fn=lambda _, c: c.controller.status.value,
    ),
    SystemSensorDescription(
        key="control_gain_current",
        translation_key="control_gain_current",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, c: c.controller.gain,
    ),
    SystemSensorDescription(
        key="meter_interval",
        translation_key="meter_interval",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, c: c.controller.meter.interval_s,
    ),
    SystemSensorDescription(
        key="battery_response_time",
        translation_key="battery_response_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, c: c.controller.battery_response.response_s,
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
        **_power("feed_in_limit"),
        value_fn=lambda s, _: s.feed_in_limit_w,
        attributes_fn=lambda s, _: {
            # disabled / no_forecast / not_enough_surplus; None with a limit
            "reason": s.feed_in_limit_reason,
            "day_plan": s.day_plan,
            "day_plan_tomorrow": s.day_plan_tomorrow,
        },
    ),
    SystemSensorDescription(
        key="pv_correction",
        translation_key="pv_correction",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, _: s.pv_correction * 100,
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
    SystemSensorDescription(
        **_power("peak_shaving_limit"),
        value_fn=lambda s, _: s.peak_shaving_limit_w,
        attributes_fn=lambda s, c: {
            "automatic": c.settings.peak_shaving_auto,
            # For the note on the energy left above the minimum SoC.
            "min_soc_pct": round(c.min_soc_pct, 1),
            "capacity_kwh": round(
                sum(b.capacity_wh for b in c.batteries if b.plannable) / 1000, 2
            ),
        },
    ),
    SystemSensorDescription(
        key="feed_in_cap_energy",
        translation_key="feed_in_cap_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_cap_absorb_kwh,
        attributes_fn=_cap_attributes,
    ),
    SystemSensorDescription(
        key="feed_in_cap_export",
        translation_key="feed_in_cap_export",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s, _: (
            None if s.feed_in_cap is None else s.feed_in_cap.export_needed_wh / 1000
        ),
    ),
    SystemSensorDescription(
        key="consumption_forecast_accuracy",
        translation_key="consumption_forecast_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda _, c: _accuracy_value(c.forecaster.accuracy),
        attributes_fn=_consumption_accuracy_attributes,
    ),
    SystemSensorDescription(
        key="pv_forecast_accuracy",
        translation_key="pv_forecast_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda _, c: _accuracy_value(c.pv_accuracy.accuracy()),
        attributes_fn=_pv_accuracy_attributes,
    ),
    SystemSensorDescription(
        key="learned_values",
        translation_key="learned_values",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_learned_in_use,
        attributes_fn=_learned_attributes,
    ),
    SystemSensorDescription(
        key="battery_energy_total",
        translation_key="battery_energy_total",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_stored_energy_kwh,
        attributes_fn=lambda s, c: {"capacity_kwh": _capacity_kwh(s, c)},
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
        # Grid side power, +discharge / -charge: the convention of the Home
        # Assistant energy dashboard for battery power.
        value_fn=lambda t: -t.ac_power_w if t.ac_power_w is not None else None,
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
        key="max_cell_voltage",
        translation_key="max_cell_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda t: t.extra.get("max_cell_voltage"),
    ),
    BatterySensorDescription(
        key="min_cell_voltage",
        translation_key="min_cell_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda t: t.extra.get("min_cell_voltage"),
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
        key="cycle_count",
        translation_key="cycle_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda t: t.extra.get("cycle_count"),
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
                StoredEnergySensor(coordinator, battery),
                *([FirmwareSensor(coordinator, battery)] if battery.driver.has_connection else []),
                EfficiencySensor(coordinator, battery),
                PlannedBatteryPowerSensor(coordinator, battery),
                LearnedCapacitySensor(coordinator, battery),
                *(
                    [
                        CellDeltaSensor(coordinator, battery),
                        TopCellDeltaSensor(coordinator, battery),
                        BalancingPhaseSensor(coordinator, battery),
                    ]
                    if battery.supports_balancing
                    else []
                ),
                *(
                    [
                        AllowedPowerSensor(coordinator, battery, charging=True),
                        AllowedPowerSensor(coordinator, battery, charging=False),
                    ]
                    if battery.driver.capabilities.controllable
                    else []
                ),
            ],
            config_subentry_id=battery.subentry_id,
        )
    for consumer in coordinator.consumers:
        if consumer.controllable:
            async_add_entities(
                [
                    PlannedConsumerPowerSensor(coordinator, consumer),
                    LearnedConsumerPowerSensor(coordinator, consumer),
                ],
                config_subentry_id=consumer.subentry_id,
            )


class SystemSensor(SlemsSystemEntity, SensorEntity):
    """System level sensor."""

    entity_description: SystemSensorDescription
    _unrecorded_attributes = frozenset({"day_plan", "day_plan_tomorrow", "days", "peaks"})

    def __init__(
        self, coordinator: SlemsCoordinator, description: SystemSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data, self.coordinator)


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


class FirmwareSensor(SlemsBatteryEntity, SensorEntity):
    """EMS firmware version; all versions and device data as attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "firmware")

    @property
    def available(self) -> bool:
        # Kept from the last read, also while the communication is paused.
        return self.coordinator.last_update_success and bool(self.battery.device_info)

    @property
    def native_value(self) -> str | None:
        return self.battery.device_info.get("ems_version")

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self.battery.device_info)


class StoredEnergySensor(SlemsBatteryEntity, SensorEntity):
    """Energy stored in the battery (state of charge × capacity)."""

    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "stored_energy")

    @property
    def native_value(self) -> float | None:
        telemetry = self.coordinator.data.batteries.get(self.battery.subentry_id)
        if telemetry is None or telemetry.soc_pct is None:
            return None
        return telemetry.soc_pct / 100 * self.battery.capacity_wh / 1000

    @property
    def extra_state_attributes(self) -> dict:
        return {"capacity_kwh": round(self.battery.capacity_wh / 1000, 2)}


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
        data = self.coordinator.data
        state = data.consumers.get(self.consumer.subentry_id)
        return {
            "blocked": state.blocked if state else None,
            "saturated": self.consumer.subentry_id in data.saturated,
            "resting": self.consumer.subentry_id in data.resting,
            "response_time_s": self.coordinator.controller.consumer_response_s(
                self.consumer.subentry_id
            ),
            # Command until it shows up at the grid meter.
            "grid_response_time_s": self.coordinator.controller.consumer_grid_response_s(
                self.consumer.subentry_id
            ),
        }


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
        energy = forecast.energy_on_day(self._day())
        return None if energy is None else energy / 1000

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


class PlannedBatteryPowerSensor(SlemsBatteryEntity, SensorEntity):
    """Power SLEMS assigns to one battery (commanded only in active mode)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "planned_power")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        if self.battery.balancing_requested and not self.battery.participating:
            # Set point of the cell balancing run (None while it is paused).
            return self.battery.balancing_power_w
        distribution = self.coordinator.data.distribution
        if distribution is None or not self.battery.participating:
            return None
        return distribution.power_w.get(self.battery.subentry_id, 0.0)

    @property
    def extra_state_attributes(self) -> dict:
        distribution = self.coordinator.data.distribution
        model = self.battery.loss_curve.model(LossModel())
        return {
            "selected": bool(
                distribution and self.battery.subentry_id in distribution.selected
            ),
            "loss_fixed_w": round(model.fixed_w, 1),
            "loss_linear": round(model.linear, 4),
            "loss_quadratic_per_w": model.quadratic_per_w,
        }


class CellDeltaSensor(SlemsBatteryEntity, SensorEntity):
    """Current spread between highest and lowest cell voltage.

    Only meaningful near the top of the charge; see TopCellDeltaSensor.
    """

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.MILLIVOLT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "cell_delta")

    @property
    def native_value(self) -> float | None:
        telemetry = self.coordinator.data.batteries.get(self.battery.subentry_id)
        if telemetry is None:
            return None
        high = telemetry.extra.get("max_cell_voltage")
        low = telemetry.extra.get("min_cell_voltage")
        if high is None or low is None:
            return None
        return round((high - low) * 1000, 1)

    @property
    def extra_state_attributes(self) -> dict:
        telemetry = self.coordinator.data.batteries.get(self.battery.subentry_id)
        high = telemetry.extra.get("max_cell_voltage") if telemetry else None
        return {"in_top_window": high is not None and high >= TOP_ZONE_V}


class TopCellDeltaSensor(SlemsBatteryEntity, SensorEntity):
    """Last settled cell delta measured near the top of the charge."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.MILLIVOLT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "top_cell_delta")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        last = self.battery.cell_monitor.last
        return last.delta_mv if last else None

    @property
    def extra_state_attributes(self) -> dict:
        monitor = self.battery.cell_monitor
        last = monitor.last
        return {
            "status": balance_status(last.delta_mv) if last else None,
            "measured_at": (
                dt_util.utc_from_timestamp(last.timestamp).isoformat() if last else None
            ),
            "source": last.source if last else None,
            "suggest_balancing": monitor.suggest_balancing,
        }


class AllowedPowerSensor(SlemsBatteryEntity, SensorEntity):
    """Charge or discharge power SLEMS may use right now, with the limiting reason."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SlemsCoordinator, battery, *, charging: bool) -> None:
        super().__init__(
            coordinator, battery, "allowed_charge_power" if charging else "allowed_discharge_power"
        )
        self._charging = charging

    @property
    def native_value(self) -> float | None:
        limits = self.battery.power_limits
        if limits is None:
            return None
        return limits.charge_w if self._charging else limits.discharge_w

    @property
    def extra_state_attributes(self) -> dict:
        limits = self.battery.power_limits
        if limits is None:
            return {"reason": None}
        # soc / power / temperature, None when not limited
        return {"reason": limits.charge_reason if self._charging else limits.discharge_reason}


class BalancingPhaseSensor(SlemsBatteryEntity, SensorEntity):
    """Phase of the active cell balancing run."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [phase.value for phase in BalancingPhase]

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "balancing_phase")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> str:
        return self.battery.balancing_phase.value

    @property
    def extra_state_attributes(self) -> dict:
        balancer = self.battery.balancer
        return {
            "last_delta_mv": balancer.last_delta_mv if balancer else None,
            "retry_voltage": balancer.retry_voltage if balancer else None,
            "started_at": (
                dt_util.utc_from_timestamp(balancer.started_at).isoformat() if balancer else None
            ),
            # done / cancelled / timeout / telemetry
            "last_result": self.battery.balancing_result,
        }


class LearnedCapacitySensor(SlemsBatteryEntity, SensorEntity):
    """Usable capacity learned from charge and discharge legs (see learning)."""

    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SlemsCoordinator, battery) -> None:
        super().__init__(coordinator, battery, "learned_capacity")

    @property
    def native_value(self) -> float | None:
        learned = self.battery.capacity_learner.capacity_wh
        return None if learned is None else learned / 1000

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "estimates_kwh": [round(e / 1000, 2) for e in self.battery.capacity_learner.estimates],
            "configured_kwh": round(self.battery.driver.capabilities.capacity_wh / 1000, 2),
            "in_use": self.battery.learn_capacity and self.native_value is not None,
        }


class LearnedConsumerPowerSensor(SlemsConsumerEntity, SensorEntity):
    """Power of a consumer while on and its thermostat pauses, learned (see learning)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SlemsCoordinator, consumer) -> None:
        super().__init__(coordinator, consumer, "learned_power")

    @property
    def _learner(self):
        return self.coordinator.consumer_learners[self.consumer.subentry_id]

    @property
    def native_value(self) -> float | None:
        return self._learner.nominal_w

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "thermostat_pauses": self._learner.cycles,
            "thermostat_cycles": self._learner.thermostat_cycles,
            "in_use": self.consumer.subentry_id in self.coordinator.consumer_learning,
        }
