"""Tariff comparison for the dashboard: what the recorded months cost with each tariff.

A passive comparison: the recorded grid import and export of every hour are
priced with each tariff (dynamic items with the stored day-ahead prices). It
does not show what SLEMS would have done differently with another tariff,
e.g. charging the batteries in cheap hours.
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
from .tariff import Role, Side, Tariff, compute_bill, tariff_from_data

if TYPE_CHECKING:
    from .coordinator import SlemsConfigEntry

MONTHS = 12
# A result is reused for this long (the hourly statistics change once an hour).
CACHE_S = 600


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
        imported, exported = await async_grid_energy(
            hass,
            start,
            end,
            import_entity=config.get(CONF_GRID_IMPORT_ENERGY_ENTITY),
            export_entity=config.get(CONF_GRID_EXPORT_ENERGY_ENTITY),
            grid_power_entity=config[CONF_GRID_POWER_ENTITY],
            grid_inverted=config.get(CONF_GRID_POWER_INVERTED, False),
        )
        prices = coordinator.market_prices
        market = prices.hourly_means(start, end) if any(t.dynamic for t in tariffs.values()) else {}
        result["months"] = await hass.async_add_executor_job(
            compare, tariffs, first, today, imported, exported, market
        )
        result["energy_counters"] = bool(config.get(CONF_GRID_IMPORT_ENERGY_ENTITY))
        if market:
            result["attribution"] = prices.attribution
        result["market_prices"] = prices.enabled
    coordinator.tariff_comparison_cache = (monotonic_time.monotonic(), result)
    return result


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
