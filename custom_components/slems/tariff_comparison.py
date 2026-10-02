"""Tariff comparison for the dashboard: what the recorded months cost with each tariff.

A passive comparison: the recorded grid import and export of every hour are
priced with each tariff (dynamic items with the stored day-ahead prices).

With batteries and recorded house consumption and PV, an estimate of what the
price aware control would have saved with each tariff is added per month (see
price_backtest): both runs of the battery model priced with the tariff.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
import time as monotonic_time
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GRID_EXPORT_ENERGY_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    DOMAIN,
    SUBENTRY_TYPE_TARIFF,
)
from .energy_history import async_grid_energy
from .forecast import async_statistic_means
from .price_backtest import BacktestBattery, play
from .tariff import Role, Side, Tariff, compute_bill, tariff_from_data

if TYPE_CHECKING:
    from .coordinator import SlemsConfigEntry

MONTHS = 12
# A result is reused for this long (the hourly statistics change once an hour;
# the backtest takes a few seconds).
CACHE_S = 1800


def month_start(day: date, back: int = 0) -> date:
    """First day of the month ``back`` months before the month of ``day``."""
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def _split_by_month(series: Mapping[datetime, float]) -> dict[date, dict[datetime, float]]:
    months: dict[date, dict[datetime, float]] = {}
    for hour, value in series.items():
        months.setdefault(month_start(dt_util.as_local(hour).date()), {})[hour] = value
    return months


def compare(
    tariffs: Mapping[str, Tariff],
    first: date,
    last: date,
    imported: Mapping[datetime, float],
    exported: Mapping[datetime, float],
    market: Mapping[datetime, float],
) -> list[dict[str, Any]]:
    """Per month from ``first`` to ``last`` (both included): energy and the costs per tariff.

    A cost is the amount due with VAT: import minus the feed-in credit. Months
    without recorded energy are left out.
    """
    by_month = [_split_by_month(series) for series in (imported, exported, market)]
    rows = []
    start = month_start(first)
    while start <= last:
        end = min(last, month_start(start, -1) - timedelta(days=1))
        month_import, month_export, month_market = (part.get(start, {}) for part in by_month)
        if any(value > 0 for value in (*month_import.values(), *month_export.values())):
            costs = {}
            energy = None
            for key, tariff in tariffs.items():
                bill = compute_bill(tariff, start, end, month_import, month_export, month_market)
                energy = (bill.import_kwh, bill.export_kwh)
                costs[key] = {
                    "import": round(bill.side_gross(tariff, Side.IMPORT), 2),
                    "export": round(-bill.side_gross(tariff, Side.EXPORT), 2),
                    "total": round(bill.side_gross(tariff, Side.IMPORT) + bill.side_gross(tariff, Side.EXPORT), 2),
                    "unpriced_kwh": round(bill.unpriced_kwh, 1),
                }
            rows.append(
                {
                    "month": f"{start:%Y-%m}",
                    "days": (end - start).days + 1,
                    "import_kwh": round(energy[0], 1) if energy else None,
                    "export_kwh": round(energy[1], 1) if energy else None,
                    "costs": costs,
                }
            )
        start = month_start(start, -1)
    return rows


def backtest_savings(
    tariffs: Mapping[str, Tariff],
    first: date,
    last: date,
    load_wh: Mapping[datetime, float],
    pv_wh: Mapping[datetime, float],
    battery: BacktestBattery,
    import_prices: Mapping[str, Mapping[datetime, float | None]],
    market: Mapping[datetime, float],
    min_gain_ct: float,
) -> dict[str, dict[str, float]]:
    """Per month ("2026-05") and tariff the estimated saving (€) of the price aware control."""
    hours = sorted(load_wh)
    usual = play(hours, load_wh, pv_wh, battery, None, min_gain_ct)
    savings: dict[str, dict[str, float]] = {}
    for key, tariff in tariffs.items():
        aware = play(hours, load_wh, pv_wh, battery, import_prices[key], min_gain_ct)
        before = compare({key: tariff}, first, last, *usual, market)
        after = {row["month"]: row for row in compare({key: tariff}, first, last, *aware, market)}
        for row in before:
            if row["month"] in after:
                saving = row["costs"][key]["total"] - after[row["month"]]["costs"][key]["total"]
                savings.setdefault(row["month"], {})[key] = round(saving, 2)
    return savings


def configured_tariffs(entry: SlemsConfigEntry) -> dict[str, Tariff]:
    """The tariffs of the entry, the current one(s) first."""
    tariffs = {
        subentry.subentry_id: tariff_from_data(subentry.title, subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_TARIFF
    }
    return dict(sorted(tariffs.items(), key=lambda item: item[1].role is not Role.CURRENT))


async def async_tariff_comparison(hass: HomeAssistant, entry: SlemsConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    cached = coordinator.tariff_comparison_cache
    if cached is not None and monotonic_time.monotonic() - cached[0] < CACHE_S:
        return cached[1]
    tariffs = configured_tariffs(entry)
    result: dict[str, Any] = {
        "tariffs": [
            {"id": key, "name": tariff.name, "role": tariff.role.value, "dynamic": tariff.dynamic}
            for key, tariff in tariffs.items()
        ],
        "months": [],
    }
    if tariffs:
        config = entry.options or entry.data
        zone = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        first = month_start(today, MONTHS - 1)
        start = datetime.combine(first, time(), zone)
        end = datetime.combine(today + timedelta(days=1), time(), zone)
        energy = await async_grid_energy(
            hass,
            start,
            end,
            import_entity=config.get(CONF_GRID_IMPORT_ENERGY_ENTITY),
            export_entity=config.get(CONF_GRID_EXPORT_ENERGY_ENTITY),
            grid_power_entity=config[CONF_GRID_POWER_ENTITY],
            grid_inverted=config.get(CONF_GRID_POWER_INVERTED, False),
            quarters=coordinator.grid_quarters,
        )
        prices = coordinator.market_prices
        market = prices.period_means(energy.lengths) if any(t.dynamic for t in tariffs.values()) else {}
        result["months"] = await hass.async_add_executor_job(
            compare, tariffs, first, today, energy.imported, energy.exported, market
        )
        # Hours only known from the hourly mean of the grid power (less exact).
        result["power_hours"] = energy.hours_from_power
        if market:
            result["attribution"] = prices.attribution
        result["market_prices"] = prices.enabled
        savings = await _async_savings(hass, coordinator, tariffs, first, today, start, end)
        if savings is not None:
            for row in result["months"]:
                row["savings"] = savings.get(row["month"], {})
            result["backtest"] = {
                "grid_charge": coordinator.settings.grid_charge,
                "min_gain_ct": coordinator.settings.price_min_gain_ct,
            }
    coordinator.tariff_comparison_cache = (monotonic_time.monotonic(), result)
    return result


async def _async_savings(
    hass: HomeAssistant,
    coordinator,
    tariffs: Mapping[str, Tariff],
    first: date,
    today: date,
    start: datetime,
    end: datetime,
) -> dict[str, dict[str, float]] | None:
    """Backtest of the price aware control; None without batteries or history."""
    battery = coordinator.backtest_battery()
    if battery is None:
        return None
    sources = coordinator.history_sources()
    ids = [*sources.house, sources.pv]
    means = await async_statistic_means(hass, ids, start, end)
    house = next((means[i] for i in sources.house if means.get(i)), None)
    if not house:
        return None
    pv = means.get(sources.pv, {}) if sources.pv else {}
    load = {hour: max(0.0, value) for hour, value in house.items()}
    pv = {hour: max(0.0, value) for hour, value in pv.items()}
    prices = coordinator.market_prices
    market = prices.period_means({hour: 3600 for hour in load})

    def run() -> dict[str, dict[str, float]]:
        # price_chart imports this module.
        from .price_chart import hourly_import_prices

        import_prices = {key: hourly_import_prices(tariff, prices, start, end) for key, tariff in tariffs.items()}
        return backtest_savings(
            tariffs, first, today, load, pv, battery, import_prices, market,
            coordinator.settings.price_min_gain_ct,
        )

    return await hass.async_add_executor_job(run)


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register ``slems/tariff_comparison`` once per Home Assistant run."""
    key = f"{DOMAIN}_tariff_comparison_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    websocket_api.async_register_command(hass, _ws_tariff_comparison)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/tariff_comparison"})
@websocket_api.async_response
async def _ws_tariff_comparison(
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
    connection.send_result(msg["id"], await async_tariff_comparison(hass, entries[0]))
