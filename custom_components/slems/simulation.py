"""Simulation for the dashboard: day plans with other settings.

The simulation tab sends settings, battery values and forecast changes; they
are never stored or used for the control. The plans are calculated like the
real ones (``SlemsCoordinator.forecast_plan``) from the current state of
charge and the current forecasts:

* ``settings``: fields of ``ControlSettings`` (only those that change the
  plans, ``SETTINGS``),
* ``battery``: total capacity (kWh), minimum and maximum SoC (%), charge and
  discharge power (W) of all batteries; the state of charge in % stays,
* ``pv_pct`` / ``consumption_pct``: change of the forecasts in %,
* ``import_tariff`` / ``export_tariff``: id of a comparison tariff whose import
  or feed-in side the price aware control plans with (empty: the current
  contract's).

The reply contains the day plans of today and tomorrow, key figures per day
for the simulation and for the real plan (with a tariff also the import costs
and the feed-in credit of the planned grid energy: per kWh items incl. VAT,
the simulation with its tariff, the real plan with the current one), and the starting values (the real
settings and batteries, the comparison tariffs per side).
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
from .price_chart import period_import_prices
from .tariff import Side, Tariff
from .tariff_comparison import chosen_tariff, tariff_choices

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
    "feed_in_cap_auto_buffer",
    "price_control",
    "price_min_gain_ct",
    "grid_charge",
    "grid_charge_max_soc_pct",
    "grid_charge_max_w",
    "battery_export",
    "grid_import_max_w",
)
BATTERY_KEYS = ("capacity_kwh", "min_soc_pct", "max_soc_pct", "max_charge_w", "max_discharge_w")
HOUR = timedelta(hours=1)


# Hourly import prices and feed-in credits (ct/kWh) per local hour start.
Prices = tuple[dict[datetime, float | None], dict[datetime, float | None]]


def _metrics(
    rows: list[dict], start: datetime, cap_limit_w: float | None, prices: Prices | None = None
) -> dict:
    """Key figures of the projected hours of a day plan (from ``start`` on);
    with ``prices`` the import costs and the feed-in credit (currency of Home Assistant, None with an
    hour without price)."""
    export = imported = curtailed = 0.0
    max_import = max_export = 0.0
    cost: float | None = 0.0
    credit: float | None = 0.0
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
            # The day plan knows the consumers taking surplus above the limit.
            lost = row.get("cap_lost_wh", exported - cap_limit_w)
            curtailed += lost * share
            exported = cap_limit_w
        export += exported * share
        imported += max(0.0, grid) * share
        max_import = max(max_import, grid)
        max_export = max(max_export, exported)
        if prices is not None:
            cost = _add_cost(cost, max(0.0, grid) * share, prices[0].get(hour))
            credit = _add_cost(credit, exported * share, prices[1].get(hour))
    result = {
        "export_kwh": round(export / 1000, 2),
        "import_kwh": round(imported / 1000, 2),
        "curtailed_kwh": round(curtailed / 1000, 2),
        "max_import_w": round(max_import),
        "max_export_w": round(max_export),
        "soc_end_pct": soc_end,
    }
    if prices is not None:
        result["import_cost"] = None if cost is None else round(cost, 2)
        result["export_credit"] = None if credit is None else round(credit, 2)
    return result


def _add_cost(total: float | None, wh: float, ct_per_kwh: float | None) -> float | None:
    if total is None or wh <= 0:
        return total
    if ct_per_kwh is None:
        return None
    return total + wh / 1000 * ct_per_kwh / 100


def _prices(coordinator: SlemsCoordinator, tariff: Tariff | None, now: datetime) -> Prices | None:
    """Hourly prices of today and tomorrow with ``tariff`` (estimated after the
    last known market price, as the price aware control plans)."""
    if tariff is None:
        return None
    start = dt_util.start_of_local_day(now)
    end = start + timedelta(days=2)
    market = coordinator.market_prices
    return (
        period_import_prices(tariff, market, start, end, Side.IMPORT, HOUR, estimate=market.enabled),
        period_import_prices(tariff, market, start, end, Side.EXPORT, HOUR, estimate=market.enabled),
    )


def _day_metrics(
    day_plan: list[dict], tomorrow: list[dict], now: datetime, settings, prices: Prices | None
) -> dict:
    cap = settings.feed_in_cap_limit_w if settings.feed_in_cap else None
    return {
        "today": _metrics(day_plan, now, cap, prices),
        "tomorrow": _metrics(tomorrow, now, cap, prices),
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
        "tariffs": tariff_choices(coordinator.config_entry),
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
    tariff = chosen_tariff(
        coordinator.config_entry, request.get("import_tariff"), request.get("export_tariff")
    )
    current = chosen_tariff(coordinator.config_entry, None, None)
    house = snapshot.house_power_w
    load = None if house is None else house * load_factor
    plan = coordinator.forecast_plan(
        sim_snapshot,
        battery,
        now,
        load,
        settings,
        power_w=(values["max_charge_w"], values["max_discharge_w"]),
        tariff=tariff,
    )
    return {
        "available": True,
        "base": base,
        "day_plan": plan.day_plan,
        "day_plan_tomorrow": plan.day_plan_tomorrow,
        "cap_limit_w": settings.feed_in_cap_limit_w if settings.feed_in_cap else None,
        "metrics": _day_metrics(
            plan.day_plan, plan.day_plan_tomorrow, now, settings, _prices(coordinator, tariff, now)
        ),
        "real_metrics": _day_metrics(
            snapshot.day_plan, snapshot.day_plan_tomorrow, now, real, _prices(coordinator, current, now)
        ),
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
        vol.Optional("import_tariff", default=""): str,
        vol.Optional("export_tariff", default=""): str,
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
