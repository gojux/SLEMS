"""Prices of a day for the price chart below the day chart.

Per quarter hour of the local day: the price of an imported and of an exported
kWh with the current tariff (incl. VAT, without yearly items) and the
day-ahead price. A monthly market price that is not entered is the plain mean
of the stored prices of the month so far.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .market_prices import SLOT_S
from .tariff import Side, Tariff, Unit, kwh_price
from .tariff_comparison import configured_tariffs, month_start

if TYPE_CHECKING:
    from .market_prices import MarketPrices


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _month_ct(tariff: Tariff, prices: MarketPrices, day: date, end: datetime) -> float | None:
    """Mean market price (ct/kWh) of the month of ``day`` until ``end``, if the tariff uses it."""
    if not any(item.unit is Unit.MARKET_MONTH for item in tariff.items):
        return None
    zone = dt_util.get_default_time_zone()
    means = prices.hourly_means(datetime.combine(month_start(day), time(), zone), end)
    return sum(means.values()) / len(means) / 10 if means else None


def hourly_import_prices(
    tariff: Tariff, prices: MarketPrices, start: datetime, end: datetime, side: Side = Side.IMPORT
) -> dict[datetime, float | None]:
    """Import price (or with ``side`` export the credit; ct/kWh incl. VAT) per
    local hour start, the mean of its quarters.

    None for an hour with a quarter without price (missing market price).
    """
    return period_import_prices(tariff, prices, start, end, side, timedelta(hours=1))


def period_import_prices(
    tariff: Tariff,
    prices: MarketPrices,
    start: datetime,
    end: datetime,
    side: Side = Side.IMPORT,
    period: timedelta = timedelta(seconds=SLOT_S),
) -> dict[datetime, float | None]:
    """Import price (or with ``side`` export the credit; ct/kWh incl. VAT) per
    local start of a quarter hour or an hour (``period``), the mean of its
    quarters; None with a quarter without price (missing market price)."""
    local = dt_util.as_local(start)
    minutes = int(period.total_seconds() // 60)
    first = local.replace(minute=local.minute - local.minute % minutes, second=0, microsecond=0)
    month_ct = _month_ct(tariff, prices, first.date(), end)
    result: dict[datetime, float | None] = {}
    moment = dt_util.as_utc(first)
    end = dt_util.as_utc(end)
    quarters = int(period.total_seconds() // SLOT_S)
    while moment < end:
        values = []
        for quarter in range(quarters):
            slot = moment + timedelta(seconds=quarter * SLOT_S)
            spot = prices.price_at(slot)
            values.append(
                kwh_price(tariff, side, dt_util.as_local(slot), None if spot is None else spot / 10, month_ct)
            )
        result[dt_util.as_local(moment)] = None if None in values else sum(values) / len(values)
        moment += period
    return result


def day_prices(tariff: Tariff, prices: MarketPrices, day: date) -> list[dict[str, Any]]:
    """Quarter hours of the local ``day`` with import, export and spot price (ct/kWh)."""
    zone = dt_util.get_default_time_zone()
    start = datetime.combine(day, time(), zone)
    end = datetime.combine(day + timedelta(days=1), time(), zone)
    month_ct = _month_ct(tariff, prices, day, end)
    has_export = any(item.side is Side.EXPORT and item.unit.per_kwh for item in tariff.items)
    slots = []
    # In UTC, so a day with a clock change has 92 or 100 quarter hours.
    moment, end = dt_util.as_utc(start), dt_util.as_utc(end)
    while moment < end:
        local = dt_util.as_local(moment)
        spot = prices.price_at(moment)
        spot_ct = None if spot is None else spot / 10
        slots.append(
            {
                "start": local.isoformat(),
                "import": _round(kwh_price(tariff, Side.IMPORT, local, spot_ct, month_ct)),
                "export": _round(kwh_price(tariff, Side.EXPORT, local, spot_ct, month_ct)) if has_export else None,
                "spot": _round(spot_ct),
            }
        )
        moment += timedelta(seconds=SLOT_S)
    return slots


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register ``slems/price_chart`` once per Home Assistant run."""
    key = f"{DOMAIN}_price_chart_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    websocket_api.async_register_command(hass, _ws_price_chart)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/price_chart",
        vol.Optional("day", default="today"): vol.In(["today", "tomorrow"]),
    }
)
@callback
def _ws_price_chart(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "SLEMS is not loaded")
        return
    entry = entries[0]
    tariffs = configured_tariffs(entry)
    if not tariffs:
        connection.send_result(msg["id"], {"available": False})
        return
    tariff = next(iter(tariffs.values()))
    coordinator = entry.runtime_data
    prices = coordinator.market_prices
    day = dt_util.now().date() + timedelta(days=1 if msg["day"] == "tomorrow" else 0)
    slots = day_prices(tariff, prices, day)
    if not prices.enabled:
        # Fetching switched off: the stored market prices are not shown any more.
        for slot in slots:
            slot["spot"] = None
    # Only fixed prices over the day and no price aware control: nothing to show.
    flat = all(
        len({slot[key] for slot in slots}) <= 1 for key in ("import", "export")
    ) and all(slot["spot"] is None for slot in slots)
    if flat and not coordinator.settings.price_control:
        connection.send_result(msg["id"], {"available": False})
        return
    connection.send_result(
        msg["id"],
        {
            "available": True,
            "tariff": tariff.name_for(Side.IMPORT),
            "export_tariff": tariff.name_for(Side.EXPORT),
            "slots": slots,
            "attribution": prices.attribution if any(slot["spot"] is not None for slot in slots) else None,
        },
    )
