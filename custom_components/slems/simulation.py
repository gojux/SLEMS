"""Simulation for the dashboard: day plans with other settings.

The simulation tab sends settings, battery values and forecast changes; they
are never stored or used for the control. The plans are calculated like the
real ones (``SlemsCoordinator.forecast_plan``) from the current state of
charge and the current forecasts:

* ``settings``: fields of ``ControlSettings`` (only those that change the
  plans, ``SETTINGS``),
* ``battery``: total capacity (kWh), minimum and maximum SoC (%), charge and
  discharge power (W) of all batteries; the state of charge in % stays,
* ``pv_pct`` / ``consumption_pct``: change of the forecasts in %.

The reply contains the day plans of today and tomorrow, key figures per day
for the simulation and for the real plan, and the starting values (the real
settings and batteries).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import ControlSettings, SlemsCoordinator

# Settings that change the plans (the others act in the real-time allocation).
SETTINGS = (
    "grid_friendly_charging",
    "grid_friendly_buffer_kwh",
    "night_discharge",
    "night_reserve_pct",
    "charge_secured_buffer_kwh",
    "discharge_max_grid_export_w",
    "peak_shaving",
    "peak_shaving_grid_limit_w",
    "peak_shaving_soc_threshold_pct",
    "peak_shaving_auto",
    "peak_shaving_reserve_pct",
    "feed_in_cap",
    "pv_peak_power_kwp",
    "feed_in_cap_limit_pct",
    "feed_in_cap_buffer_pct",
    "feed_in_cap_min_buffer_pct",
    "feed_in_cap_auto_buffer",
)
BATTERY_KEYS = ("capacity_kwh", "min_soc_pct", "max_soc_pct", "max_charge_w", "max_discharge_w")
HOUR = timedelta(hours=1)


def _metrics(rows: list[dict], start: datetime, cap_limit_w: float | None) -> dict:
    """Key figures of the projected hours of a day plan (from ``start`` on)."""
    export = imported = curtailed = 0.0
    max_import = max_export = 0.0
    soc_end = None
    for row in rows:
        if row.get("soc_pct") is not None:
            soc_end = row["soc_pct"]
        grid = row.get("grid_w")
        hour = dt_util.parse_datetime(row["start"])
        if grid is None or hour is None:
            continue
        share = min(1.0, (hour + HOUR - max(hour, start)) / HOUR)
        exported = max(0.0, -grid)
        if cap_limit_w is not None and exported > cap_limit_w:
            curtailed += (exported - cap_limit_w) * share
            exported = cap_limit_w
        export += exported * share
        imported += max(0.0, grid) * share
        max_import = max(max_import, grid)
        max_export = max(max_export, exported)
    return {
        "export_kwh": round(export / 1000, 2),
        "import_kwh": round(imported / 1000, 2),
        "curtailed_kwh": round(curtailed / 1000, 2),
        "max_import_w": round(max_import),
        "max_export_w": round(max_export),
        "soc_end_pct": soc_end,
    }


def _day_metrics(day_plan: list[dict], tomorrow: list[dict], now: datetime, settings) -> dict:
    cap = settings.feed_in_cap_limit_w if settings.feed_in_cap else None
    return {
        "today": _metrics(day_plan, now, cap),
        "tomorrow": _metrics(tomorrow, now, cap),
    }


def simulate(coordinator: SlemsCoordinator, request: dict[str, Any]) -> dict[str, Any]:
    """Day plans and key figures with the settings of ``request``."""
    snapshot = coordinator.data
    now = dt_util.now()
    real: ControlSettings = coordinator.settings
    battery = coordinator._battery_group(snapshot) if snapshot is not None else None
    base = {
        "settings": {key: getattr(real, key) for key in SETTINGS},
        "battery": None
        if battery is None
        else {
            "capacity_kwh": round(battery.capacity_wh / 1000, 2),
            "min_soc_pct": round(battery.min_soc_pct, 1),
            "max_soc_pct": round(battery.full_soc_pct, 1),
            "max_charge_w": round(battery.max_charge_w),
            "max_discharge_w": round(battery.max_discharge_w),
        },
    }
    if snapshot is None or battery is None or snapshot.pv_forecast is None:
        return {"available": False, "base": base}

    changes = {}
    for key, value in request.get("settings", {}).items():
        if key in SETTINGS:
            current = getattr(real, key)
            changes[key] = bool(value) if isinstance(current, bool) else float(value)
    settings = replace(real, **changes)
    values = base["battery"] | {
        key: float(value) for key, value in request.get("battery", {}).items() if key in BATTERY_KEYS
    }
    battery = replace(
        battery,
        capacity_wh=values["capacity_kwh"] * 1000,
        min_soc_pct=values["min_soc_pct"],
        full_soc_pct=values["max_soc_pct"],
        max_charge_w=values["max_charge_w"],
        max_discharge_w=values["max_discharge_w"],
    )
    pv_factor = 1 + request.get("pv_pct", 0) / 100
    load_factor = 1 + request.get("consumption_pct", 0) / 100
    forecast = snapshot.consumption_forecast
    sim_snapshot = replace(
        snapshot,
        pv_forecast={k: v * pv_factor for k, v in snapshot.pv_forecast.items()},
        consumption_forecast=None
        if forecast is None
        else replace(forecast, total={k: v * load_factor for k, v in forecast.total.items()}),
    )
    house = snapshot.house_power_w
    load = None if house is None else house * load_factor
    plan = coordinator.forecast_plan(
        sim_snapshot,
        battery,
        now,
        load,
        settings,
        power_w=(values["max_charge_w"], values["max_discharge_w"]),
    )
    return {
        "available": True,
        "base": base,
        "day_plan": plan.day_plan,
        "day_plan_tomorrow": plan.day_plan_tomorrow,
        "cap_limit_w": settings.feed_in_cap_limit_w if settings.feed_in_cap else None,
        "metrics": _day_metrics(plan.day_plan, plan.day_plan_tomorrow, now, settings),
        "real_metrics": _day_metrics(snapshot.day_plan, snapshot.day_plan_tomorrow, now, real),
    }


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register ``slems/simulate`` once per Home Assistant run."""
    key = f"{DOMAIN}_simulate_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    websocket_api.async_register_command(hass, _ws_simulate)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/simulate",
        vol.Optional("settings", default={}): dict,
        vol.Optional("battery", default={}): dict,
        vol.Optional("pv_pct", default=0): vol.Coerce(float),
        vol.Optional("consumption_pct", default=0): vol.Coerce(float),
    }
)
@callback
def _ws_simulate(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "SLEMS is not loaded")
        return
    connection.send_result(msg["id"], simulate(entries[0].runtime_data, msg))
