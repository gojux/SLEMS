"""What other hardware would have changed in the recorded months (simulation tab).

The recorded hourly house consumption and PV (as for the backtest of the price
aware control, see tariff_comparison) are played through the battery model of
price_backtest as usual, twice: with the current batteries and with the
values of the simulation (capacity, minimum and maximum state of charge,
charge and discharge power), PV and consumption scaled by the simulation's
changes. Both runs are priced with each tariff (``tariff_comparison.compare``);
the difference per month and tariff is what the other hardware would have
brought. Both sides come from the model, so unchanged values differ by nothing.

Limits: hourly values; grid friendly charging, the feed-in cap and the price
aware control are not modelled; the PV factor scales the recorded curve
(another orientation of the modules changes its shape, not only its height).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import time as monotonic_time
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .currency import market_prices_usable
from .price_backtest import BacktestBattery, play
from .simulation import BATTERY_KEYS, base_battery
from .tariff import Tariff
from .tariff_comparison import CACHE_S, MONTHS, async_backtest_history, compare, configured_tariffs, month_start

if TYPE_CHECKING:
    from .coordinator import SlemsConfigEntry


def simulated_battery(
    base: BacktestBattery, real: Mapping[str, float], values: Mapping[str, float]
) -> BacktestBattery:
    """The backtest battery ``base`` with the simulation's battery ``values``
    (fields of ``BATTERY_KEYS``); ``real`` are their starting values.

    Only changed values are applied, so the unchanged ones keep the exact
    backtest values. A changed capacity keeps the states of charge in %; a
    changed power shifts the backtest power by the change (the backtest
    leaves out batteries with own PV when charging).
    """

    def changed(key: str) -> bool:
        return key in values and abs(values[key] - real[key]) > 1e-9

    capacity = values["capacity_kwh"] * 1000 if changed("capacity_kwh") else base.capacity_wh
    scale = capacity / base.capacity_wh if base.capacity_wh else 1.0
    min_wh = values["min_soc_pct"] / 100 * capacity if changed("min_soc_pct") else base.min_wh * scale
    full_wh = values["max_soc_pct"] / 100 * capacity if changed("max_soc_pct") else base.full_wh * scale
    full_wh = max(min_wh, min(capacity, full_wh))

    def power(key: str, current: float) -> float:
        return max(0.0, current + values[key] - real[key]) if changed(key) else current

    return replace(
        base,
        capacity_wh=capacity,
        min_wh=min_wh,
        full_wh=full_wh,
        grid_max_wh=min(full_wh, base.grid_max_wh * scale),
        max_charge_w=power("max_charge_w", base.max_charge_w),
        max_discharge_w=power("max_discharge_w", base.max_discharge_w),
    )


def hardware_rows(
    tariffs: Mapping[str, Tariff],
    first: date,
    last: date,
    load_wh: Mapping[datetime, float],
    pv_wh: Mapping[datetime, float],
    current: BacktestBattery,
    simulated: BacktestBattery,
    pv_factor: float,
    load_factor: float,
    market: Mapping[datetime, float],
    references: Mapping[str, Mapping[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Per month (only months with energy in both runs): grid import and
    export (kWh) and the costs per tariff (import minus feed-in credit, with
    VAT) with the ``current`` and the ``simulated`` hardware."""
    hours = sorted(load_wh)
    before = compare(tariffs, first, last, *play(hours, load_wh, pv_wh, current, None, 0.0), market, references)
    scaled_load = {hour: value * load_factor for hour, value in load_wh.items()}
    scaled_pv = {hour: value * pv_factor for hour, value in pv_wh.items()}
    after = {
        row["month"]: row
        for row in compare(
            tariffs, first, last, *play(hours, scaled_load, scaled_pv, simulated, None, 0.0), market, references
        )
    }

    def side(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "import_kwh": row["import_kwh"],
            "export_kwh": row["export_kwh"],
            "costs": {key: cost["total"] for key, cost in row["costs"].items()},
        }

    return [
        {"month": row["month"], "days": row["days"], "current": side(row), "simulated": side(after[row["month"]])}
        for row in before
        if row["month"] in after
    ]


async def async_hardware_comparison(
    hass: HomeAssistant, entry: SlemsConfigEntry, request: Mapping[str, Any]
) -> dict[str, Any]:
    """The reply of ``slems/hardware_comparison`` for the simulation's values in ``request``."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    group = coordinator._battery_group(snapshot, planning=True) if snapshot is not None else None
    real = base_battery(group)
    backtest = coordinator.backtest_battery()
    values = (real or {}) | {
        key: float(value) for key, value in request.get("battery", {}).items() if key in BATTERY_KEYS
    }
    pv_factor = max(0.0, 1 + request.get("pv_pct", 0) / 100)
    load_factor = max(0.0, 1 + request.get("consumption_pct", 0) / 100)
    usable = market_prices_usable(hass)
    tariffs = {
        key: tariff
        for key, tariff in configured_tariffs(entry).items()
        if usable or not tariff.market_priced
    }
    result: dict[str, Any] = {
        "available": real is not None and backtest is not None and bool(tariffs),
        "changed": pv_factor != 1 or load_factor != 1 or any(abs(values[k] - real[k]) > 1e-9 for k in real or {}),
        "tariffs": [
            {"id": key, "name": tariff.name, "role": tariff.role.value} for key, tariff in tariffs.items()
        ],
        "months": [],
    }
    if not result["available"] or not result["changed"]:
        return result
    key = (backtest, tuple(sorted(values.items())), pv_factor, load_factor, usable)
    cached = coordinator.hardware_comparison_cache
    if cached is not None and cached[1] == key and monotonic_time.monotonic() - cached[0] < CACHE_S:
        return cached[2]
    zone = dt_util.get_default_time_zone()
    today = dt_util.now().date()
    first = month_start(today, MONTHS - 1)
    start = datetime.combine(first, time(), zone)
    end = datetime.combine(today + timedelta(days=1), time(), zone)
    history = await async_backtest_history(hass, coordinator, start, end)
    if history is None:
        result["available"] = False
        return result
    load, pv = history
    prices = coordinator.market_prices
    market = prices.period_means({hour: 3600 for hour in load}) if any(t.dynamic for t in tariffs.values()) else {}
    simulated = simulated_battery(backtest, real, values)
    result["months"] = await hass.async_add_executor_job(
        hardware_rows, tariffs, first, today, load, pv, backtest, simulated, pv_factor, load_factor, market,
        prices.references,
    )
    coordinator.hardware_comparison_cache = (monotonic_time.monotonic(), key, result)
    return result


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register ``slems/hardware_comparison`` once per Home Assistant run."""
    key = f"{DOMAIN}_hardware_comparison_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    websocket_api.async_register_command(hass, _ws_hardware_comparison)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/hardware_comparison",
        vol.Optional("battery", default={}): dict,
        vol.Optional("pv_pct", default=0): vol.Coerce(float),
        vol.Optional("consumption_pct", default=0): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def _ws_hardware_comparison(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "SLEMS is not loaded")
        return
    connection.send_result(msg["id"], await async_hardware_comparison(hass, entries[0], msg))
