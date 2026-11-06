"""Tariff comparison for the dashboard: what the recorded months cost with each tariff.

A passive comparison: the recorded grid import and export of every hour are
priced with each tariff (dynamic items with the stored day-ahead prices).

With batteries and recorded house consumption and PV, an estimate of what the
price aware control would have saved with each tariff is added per month (see
price_backtest): both runs of the battery model priced with the tariff. For
a tariff with a power price, an estimate of what peak shaving would have
saved (see peak_backtest).
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
from .currency import market_prices_usable
from .energy_history import async_grid_energy
from .forecast import async_statistic_means, sum_series
from .grid_quarters import GridQuarters
from .peak_backtest import estimate, month_powers
from .power_peak import power_price
from .price_backtest import BacktestBattery, play
from .tariff import Role, Side, Tariff, combine_tariffs, compute_bill, mixed_tariff, tariff_from_data

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
    references: Mapping[str, Mapping[str, float]] | None = None,
    lengths: Mapping[datetime, int] | None = None,
) -> list[dict[str, Any]]:
    """Per month from ``first`` to ``last`` (both included): energy and the costs per tariff.

    A cost is the amount due with VAT: import minus the feed-in credit. Months
    without recorded energy are left out. ``lengths``: the length (s) of each
    period (see tariff.compute_bill), for a power price.
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
                bill = compute_bill(tariff, start, end, month_import, month_export, month_market, references, lengths)
                energy = (bill.import_kwh, bill.export_kwh)
                costs[key] = {
                    "import": round(bill.side_gross(tariff, Side.IMPORT), 2),
                    "export": round(-bill.side_gross(tariff, Side.EXPORT), 2),
                    "total": round(bill.side_gross(tariff, Side.IMPORT) + bill.side_gross(tariff, Side.EXPORT), 2),
                    "unpriced_kwh": round(bill.unpriced_kwh, 1),
                }
                if bill.billed_kw:
                    costs[key]["billed_kw"] = round(max(bill.billed_kw.values()), 2)
                    costs[key]["peak_from_hours"] = bill.peak_from_hours
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
    references: Mapping[str, Mapping[str, float]] | None = None,
    export_prices: Mapping[str, Mapping[datetime, float | None]] | None = None,
) -> dict[str, dict[str, float]]:
    """Per month ("2026-05") and tariff the estimated saving (€) of the price
    aware control; ``export_prices``: the feed-in credit per tariff and hour
    (feeding in from the batteries)."""
    hours = sorted(load_wh)
    usual = play(hours, load_wh, pv_wh, battery, None, min_gain_ct)
    savings: dict[str, dict[str, float]] = {}
    for key, tariff in tariffs.items():
        aware = play(
            hours, load_wh, pv_wh, battery, import_prices[key], min_gain_ct,
            export_prices=(export_prices or {}).get(key),
        )
        before = compare({key: tariff}, first, last, *usual, market, references)
        after = {row["month"]: row for row in compare({key: tariff}, first, last, *aware, market, references)}
        for row in before:
            if row["month"] in after:
                saving = row["costs"][key]["total"] - after[row["month"]]["costs"][key]["total"]
                savings.setdefault(row["month"], {})[key] = round(saving, 2)
    return savings


def add_peak_savings(
    rows: list[dict[str, Any]],
    tariffs: Mapping[str, Tariff],
    quarters: GridQuarters,
    battery: BacktestBattery,
) -> None:
    """Per month and tariff with a power price: what peak shaving would have saved
    (``peak_saving``) at which limit (``peak_limit_kw``), from the recorded quarter hours."""
    powers: dict[int, dict[date, dict[datetime, float]]] = {}
    for row in rows:
        month = date.fromisoformat(f"{row['month']}-01")
        for key, tariff in tariffs.items():
            cost = row["costs"].get(key)
            price = power_price(tariff, month)
            if cost is None or price is None or not row.get("import_kwh"):
                continue
            if price.interval_s not in powers:
                powers[price.interval_s] = month_powers(quarters, price.interval_s)
            # Mean import price of the month (with VAT), for the losses.
            import_ct = cost["import"] / row["import_kwh"] * 100
            result = estimate(price, powers[price.interval_s].get(month, {}), battery, import_ct)
            if result is not None and result.saving >= 0.005:
                cost["peak_saving"] = round(result.saving, 2)
                cost["peak_limit_kw"] = round(result.limit_kw, 1)


def configured_tariffs(entry: SlemsConfigEntry) -> dict[str, Tariff]:
    """The tariffs of the entry (see tariff.combine_tariffs): the current
    contract first (several current tariffs together), then the comparison
    tariffs, each completed with the current contract's missing side."""
    return combine_tariffs(
        {
            subentry.subentry_id: tariff_from_data(subentry.title, subentry.data)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_TARIFF
        }
    )


def _comparison_tariffs(entry: SlemsConfigEntry) -> dict[str, Tariff]:
    """The comparison tariffs of the entry as entered (without the current contract's sides)."""
    return {
        subentry.subentry_id: tariff
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_TARIFF
        and (tariff := tariff_from_data(subentry.title, subentry.data)).role is Role.COMPARISON
    }


def tariff_choices(entry: SlemsConfigEntry) -> dict[str, list[dict[str, str]]]:
    """Comparison tariffs per side that has items in them, for the simulation."""
    tariffs = _comparison_tariffs(entry)
    return {
        side.value: [
            {"id": key, "name": tariff.name, "power_price": tariff.power_priced}
            for key, tariff in tariffs.items()
            if any(item.side is side for item in tariff.items)
        ]
        for side in Side
    }


def chosen_tariff(
    entry: SlemsConfigEntry, import_id: str | None, export_id: str | None
) -> Tariff | None:
    """The current contract with the import and/or feed-in side of a comparison
    tariff (None or an unknown id: the current side); None without tariff."""
    tariffs = configured_tariffs(entry)
    if not tariffs:
        return None
    current = next(iter(tariffs.values()))
    if not import_id and not export_id:
        return current
    comparison = _comparison_tariffs(entry)

    def source(key: str | None, side: Side) -> Tariff:
        tariff = comparison.get(key or "")
        if tariff is None or not any(item.side is side for item in tariff.items):
            return current
        return tariff

    return mixed_tariff(source(import_id, Side.IMPORT), source(export_id, Side.EXPORT))


async def async_tariff_comparison(hass: HomeAssistant, entry: SlemsConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    # The backtest follows the battery and price settings: a change of them computes anew.
    usable = market_prices_usable(hass)
    key = (coordinator.backtest_battery(), coordinator.settings.price_min_gain_ct, usable)
    cached = coordinator.tariff_comparison_cache
    if cached is not None and cached[1] == key and monotonic_time.monotonic() - cached[0] < CACHE_S:
        return cached[2]
    tariffs = configured_tariffs(entry)
    # Market prices in another currency than the tariffs: those following them are not computed.
    blocked = set() if usable else {key for key, tariff in tariffs.items() if tariff.market_priced}
    result: dict[str, Any] = {
        "tariffs": [
            {
                "id": key,
                "name": tariff.name,
                "role": tariff.role.value,
                "dynamic": tariff.dynamic,
                "foreign_currency": key in blocked,
            }
            for key, tariff in tariffs.items()
        ],
        "months": [],
    }
    tariffs = {key: tariff for key, tariff in tariffs.items() if key not in blocked}
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
            compare, tariffs, first, today, energy.imported, energy.exported, market, prices.references, energy.lengths
        )
        # Hours only known from the hourly mean of the grid power (less exact).
        result["power_hours"] = energy.hours_from_power
        if market:
            result["attribution"] = prices.attribution
        result["market_prices"] = prices.enabled
        measured = coordinator.price_savings
        result["measured"] = {
            "total_eur": round(measured.total_now(dt_util.now()), 2),
            "runs": measured.runs if measured.month == f"{today:%Y-%m}" else 0,
        }
        peak_battery = coordinator.backtest_battery()
        if peak_battery is not None and any(tariff.power_priced for tariff in tariffs.values()):
            await hass.async_add_executor_job(
                add_peak_savings, result["months"], tariffs, coordinator.grid_quarters, peak_battery
            )
        savings = await _async_savings(hass, coordinator, tariffs, first, today, start, end)
        if savings is not None:
            for row in result["months"]:
                row["savings"] = savings.get(row["month"], {})
            result["backtest"] = {
                "grid_charge": coordinator.settings.grid_charge,
                "battery_export": coordinator.settings.battery_export,
                "min_gain_ct": coordinator.settings.price_min_gain_ct,
            }
    coordinator.tariff_comparison_cache = (monotonic_time.monotonic(), key, result)
    return result


async def async_backtest_history(
    hass: HomeAssistant, coordinator, start: datetime, end: datetime
) -> tuple[dict[datetime, float], dict[datetime, float]] | None:
    """Recorded hourly house consumption and PV (Wh, not negative) for the
    battery model; None without house consumption."""
    sources = coordinator.history_sources()
    ids = [*sources.house, *sources.pv]
    means = await async_statistic_means(hass, ids, start, end)
    house = next((means[i] for i in sources.house if means.get(i)), None)
    if not house:
        return None
    pv = sum_series(means.get(p, {}) for p in sources.pv)
    return (
        {hour: max(0.0, value) for hour, value in house.items()},
        {hour: max(0.0, value) for hour, value in pv.items()},
    )


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
    history = await async_backtest_history(hass, coordinator, start, end)
    if history is None:
        return None
    load, pv = history
    prices = coordinator.market_prices
    market = prices.period_means({hour: 3600 for hour in load})

    def run() -> dict[str, dict[str, float]]:
        # price_chart imports this module.
        from .price_chart import hourly_import_prices

        import_prices = {key: hourly_import_prices(tariff, prices, start, end) for key, tariff in tariffs.items()}
        export_prices = (
            {key: hourly_import_prices(tariff, prices, start, end, Side.EXPORT) for key, tariff in tariffs.items()}
            if battery.battery_export
            else None
        )
        return backtest_savings(
            tariffs, first, today, load, pv, battery, import_prices, market,
            coordinator.settings.price_min_gain_ct, prices.references, export_prices,
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
