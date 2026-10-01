"""Diagnostics download (Settings → Devices & services → SLEMS → ⋮).

Besides the configuration and the current measurements it contains internal
states that no entity shows, e.g. the counters and timers of a cell balancing
run or of the top cell delta measurement. Times of the monotonic clock are
given as seconds before (negative: after) the download. IP addresses and MAC
addresses are redacted.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_HOST, DOMAIN
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator

TO_REDACT = {CONF_HOST, "mac_address"}


def _plain(value: Any) -> Any:
    """JSON friendly copy of settings, plans and telemetry."""
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            (k.isoformat() if isinstance(k, datetime | date) else str(k)): _plain(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 3)
    return value


def _ago(moment: float | None, now: float) -> float | None:
    return None if moment is None else round(now - moment, 1)


def _battery(coordinator: SlemsCoordinator, battery: BatteryRuntime) -> dict:
    now = time.monotonic()
    data = coordinator.data
    telemetry = data.batteries.get(battery.subentry_id) if data else None
    caps = battery.driver.capabilities
    monitor = battery.cell_monitor
    balancer = battery.balancer
    command = coordinator.controller.command_state(battery.subentry_id)
    distribution = data.distribution if data else None
    return {
        "name": battery.name,
        "capabilities": _plain(caps),
        "enabled": battery.enabled,
        "participating": battery.participating,
        "plannable": battery.plannable,
        "leaving_in_s": None if battery.leaving_until is None else round(battery.leaving_until - now, 1),
        "unreadable_for_s": _ago(battery.unreadable_since, now),
        "communication_paused": battery.communication_paused,
        "paused_until": battery.paused_until,
        "pause_reason": battery.pause_reason,
        "device_info": async_redact_data(battery.device_info, TO_REDACT),
        "limits": _plain(battery.limits),
        "soc_window": {
            "charge_blocked": battery.soc_window.charge_blocked,
            "discharge_blocked": battery.soc_window.discharge_blocked,
        },
        "power_limits": _plain(battery.power_limits),
        "telemetry": _plain(telemetry),
        "planned_power_w": distribution.power_w.get(battery.subentry_id) if distribution else None,
        "last_command": None
        if command is None
        else {"power_w": command[0], "seconds_ago": _ago(command[1], now)},
        "efficiency": {
            "mode": _plain(battery.efficiency.mode),
            "round_trip": round(battery.efficiency.round_trip, 4),
            "learned": battery.efficiency.is_learned,
        },
        "loss_curve": _plain(battery.loss_curve.as_dict()),
        "delivery": {
            "fail_count": battery.delivery.fail_count,
            "reason": battery.delivery.reason,
            "excluded_for_s": None
            if battery.delivery.excluded_until is None
            else round(battery.delivery.excluded_until - now, 1),
        },
        "cell_monitor": {
            "last": _plain(monitor.last),
            "last_full": monitor.last_full,
            "at_top": monitor._at_top,
            "top_reached": monitor._top_reached,
            "armed": monitor._armed,
            "resting_for_s": _ago(monitor._rest_since, now),
        },
        "balancing": None
        if balancer is None
        else {
            **_plain(balancer.as_dict()),
            "phase_shown": _plain(battery.balancing_phase),
            "error": balancer.error,
            "rejections": balancer._rejections,
            "leg_for_s": _ago(balancer._leg_started, now),
            "measuring_for_s": _ago(balancer._measure_since, now),
            "power_w": battery.balancing_power_w,
        },
        "balancing_result": battery.balancing_result,
        "learn_capacity": battery.learn_capacity,
        "capacity_in_use_wh": battery.capacity_wh,
        "capacity_learner": battery.capacity_learner.as_dict(),
    }


def _system(coordinator: SlemsCoordinator) -> dict:
    data = coordinator.data
    controller = coordinator.controller
    forecast = coordinator.forecaster.forecast
    snapshot = None
    if data is not None:
        snapshot = {
            "grid_power_w": data.grid_power_w,
            "grid_power_filtered_w": data.grid_power_filtered_w,
            "pv_power_w": data.pv_power_w,
            "house_power_w": data.house_power_w,
            "battery_power_w": data.battery_power_w,
            "available_power_w": data.available_power_w,
            "expected_surplus_wh": data.expected_surplus_wh,
            "pv_correction": data.pv_correction,
            "feed_in_limit_w": data.feed_in_limit_w,
            "feed_in_limit_reason": data.feed_in_limit_reason,
            "peak_shaving_limit_w": data.peak_shaving_limit_w,
            "night_discharge": data.night_discharge,
            "feed_in_cap": data.feed_in_cap,
            "feed_in_cap_buffer": {
                "pct": data.feed_in_cap_buffer_pct,
                "source": data.feed_in_cap_buffer_source,
                "days": data.feed_in_cap_buffer_days,
            },
            "allocation": data.allocation,
            "distribution": data.distribution,
            "consumers": data.consumers,
            "saturated": data.saturated,
            "resting": data.resting,
            "control_disabled": data.control_disabled,
            "day_plan": data.day_plan,
            "day_plan_tomorrow": data.day_plan_tomorrow,
        }
    return {
        "settings": _plain(coordinator.settings),
        "controller": {
            "status": _plain(controller.status),
            "gain": controller.gain,
            "meter_interval_s": controller.meter.interval_s,
            "battery_response_s": controller.battery_response.response_s,
            "battery_response_by_battery_s": {
                battery.name: controller.battery_responses.learned(battery.subentry_id)
                for battery in coordinator.batteries
            },
        },
        "cap_exceeded_for_s": _ago(coordinator.cap_exceeded_since, time.monotonic()),
        "snapshot": _plain(snapshot),
        "consumption_forecast": None
        if forecast is None
        else {"created": _plain(forecast.created), "hours": len(forecast.total)},
        "pv_accuracy_days": _plain(coordinator.pv_accuracy.days),
        "consumers": [_plain(consumer) for consumer in coordinator.consumers],
        "consumer_learning": {
            consumer.subentry_id: {
                "in_use": consumer.subentry_id in coordinator.consumer_learning,
                "nominal_w": coordinator.consumer_learners[consumer.subentry_id].nominal_w,
                "thermostat_pauses": coordinator.consumer_learners[consumer.subentry_id].cycles,
            }
            for consumer in coordinator.consumers
        },
        "thermal_storage": {
            subentry_id: learner.as_dict()
            | {"capacity_wh": coordinator.thermal_capacity(subentry_id)}
            for subentry_id, learner in coordinator.thermal_learners.items()
        },
        "learned": {
            "pv_overestimate": coordinator.pv_overestimate,
            "consumption_underestimate": coordinator.consumption_underestimate,
            "charge_grid_target_w": coordinator.grid_targets.target_w(True),
            "discharge_grid_target_w": coordinator.grid_targets.target_w(False),
            "grid_target_samples": [len(coordinator.grid_targets.charge), len(coordinator.grid_targets.discharge)],
            "timing": coordinator.learned_timing,
            "control_interval_s": coordinator.control_interval_s,
            "morning_gap": coordinator.morning_gap.as_dict(),
            "night_reserve_pct": coordinator.night_reserve_pct(coordinator.settings),
            "average_window_s": coordinator.average_window_s,
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SlemsConfigEntry
) -> dict[str, Any]:
    """Configuration, internal state of the system and of every battery."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "subentries": [
                {
                    "type": subentry.subentry_type,
                    "title": subentry.title,
                    "data": async_redact_data(dict(subentry.data), TO_REDACT),
                }
                for subentry in entry.subentries.values()
            ],
        },
        **_system(coordinator),
        "batteries": [_battery(coordinator, battery) for battery in coordinator.batteries],
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: SlemsConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """A battery device: its internal state; other devices: the whole entry."""
    coordinator = entry.runtime_data
    ids = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
    for battery in coordinator.batteries:
        if battery.subentry_id in ids:
            subentry = entry.subentries.get(battery.subentry_id)
            return {
                "subentry": async_redact_data(dict(subentry.data), TO_REDACT) if subentry else None,
                "settings": _plain(coordinator.settings),
                "battery": _battery(coordinator, battery),
            }
    return await async_get_config_entry_diagnostics(hass, entry)
