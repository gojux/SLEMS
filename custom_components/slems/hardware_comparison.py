"""What other hardware would have changed in the recorded months (simulation tab).

The recorded hourly house consumption and PV (as for the backtest of the price
aware control, see tariff_comparison) are played through the battery model of
price_backtest as usual, twice: with the current batteries and with the
values of the simulation (capacity, minimum and maximum state of charge,
charge and discharge power), PV and consumption scaled by the simulation's
changes. Both runs are priced with each tariff (``tariff_comparison.compare``);
the difference per month and tariff is what the other hardware would have
brought. Both sides come from the model, so unchanged values differ by nothing.

With the price aware control switched on in the simulation, both batteries
are also played planned like the price aware control with each tariff's
hourly prices (the simulation's price settings for both): the costs then
include it, and its share (the planned run's costs minus the usual one's) is
given apart.

Limits: hourly values; grid friendly charging and the feed-in cap are not
modelled, the price plan knows consumption and PV exactly; the PV factor
scales the recorded curve (another orientation of the modules changes its
shape, not only its height).
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

from .const import DOMAIN
from .currency import market_prices_usable
from .price_backtest import BacktestBattery, play
from .simulation import base_battery, battery_values, simulated_group, simulated_settings
from .tariff import Side, Tariff
from .tariff_comparison import CACHE_S, MONTHS, async_backtest_history, compare, configured_tariffs, month_start

if TYPE_CHECKING:
    from .coordinator import SlemsConfigEntry

# Hourly import prices and feed-in credits (None: no feeding in from the batteries) of a tariff.
TariffPrices = tuple[Mapping[datetime, float | None], Mapping[datetime, float | None] | None]


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
    prices: Mapping[str, TariffPrices] | None = None,
    min_gain_ct: float = 0.0,
) -> list[dict[str, Any]]:
    """Per month (only months with energy in both runs): grid import and
    export (kWh) and the costs per tariff (import minus feed-in credit, with
    VAT) with the ``current`` and the ``simulated`` hardware.

    With ``prices`` (per tariff) ``price_costs``: the costs per tariff with
    the price aware control, for the tariffs in ``prices``.
    """
    hours = sorted(load_wh)
    scaled_load = {hour: value * load_factor for hour, value in load_wh.items()}
    scaled_pv = {hour: value * pv_factor for hour, value in pv_wh.items()}
    runs = {"current": (load_wh, pv_wh, current), "simulated": (scaled_load, scaled_pv, simulated)}
    sides: dict[str, dict[str, dict[str, Any]]] = {}
    for side, (load, pv, battery) in runs.items():
        usual = compare(tariffs, first, last, *play(hours, load, pv, battery, None, 0.0), market, references)
        months = sides[side] = {
            row["month"]: {
                "import_kwh": row["import_kwh"],
                "export_kwh": row["export_kwh"],
                "costs": {key: cost["total"] for key, cost in row["costs"].items()},
            }
            for row in usual
        }
        for key, (import_prices, export_prices) in (prices or {}).items():
            aware = play(hours, load, pv, battery, import_prices, min_gain_ct, export_prices=export_prices)
            for row in compare({key: tariffs[key]}, first, last, *aware, market, references):
                if row["month"] in months:
                    months[row["month"]].setdefault("price_costs", {})[key] = row["costs"][key]["total"]
    return [
        {"month": month, "current": current_side, "simulated": sides["simulated"][month]}
        for month, current_side in sides["current"].items()
        if month in sides["simulated"]
    ]


async def async_hardware_comparison(
    hass: HomeAssistant, entry: SlemsConfigEntry, request: Mapping[str, Any]
) -> dict[str, Any]:
    """The reply of ``slems/hardware_comparison`` for the simulation's values in ``request``."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    group = coordinator._battery_group(snapshot, planning=True) if snapshot is not None else None
    real = base_battery(group)
    settings = simulated_settings(coordinator.settings, request.get("settings", {}))
    values = battery_values(real or {}, request.get("battery", {}))
    pv_factor = max(0.0, 1 + request.get("pv_pct", 0) / 100)
    load_factor = max(0.0, 1 + request.get("consumption_pct", 0) / 100)
    usable = market_prices_usable(hass)
    tariffs = {
        key: tariff
        for key, tariff in configured_tariffs(entry).items()
        if usable or not tariff.market_priced
    }
    current = coordinator.backtest_battery(settings, group) if group is not None else None
    result: dict[str, Any] = {
        "available": current is not None and bool(tariffs),
        "changed": pv_factor != 1 or load_factor != 1 or any(abs(values[k] - real[k]) > 1e-9 for k in real or {}),
        "price_control": settings.price_control,
        "tariffs": [
            {"id": key, "name": tariff.name, "role": tariff.role.value} for key, tariff in tariffs.items()
        ],
        "months": [],
    }
    if not result["available"] or not result["changed"]:
        return result
    simulated = coordinator.backtest_battery(settings, simulated_group(group, real, values))
    min_gain = settings.price_min_gain_ct
    key = (current, simulated, pv_factor, load_factor, settings.price_control, min_gain, usable)
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

    def run() -> list[dict[str, Any]]:
        tariff_prices = None
        if settings.price_control:
            # price_chart imports tariff_comparison.
            from .price_chart import hourly_import_prices

            tariff_prices = {
                key: (
                    hourly_import_prices(tariff, prices, start, end),
                    hourly_import_prices(tariff, prices, start, end, Side.EXPORT) if settings.battery_export else None,
                )
                for key, tariff in tariffs.items()
            }
        return hardware_rows(
            tariffs, first, today, load, pv, current, simulated, pv_factor, load_factor, market,
            prices.references, tariff_prices, min_gain,
        )

    result["months"] = await hass.async_add_executor_job(run)
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
        vol.Optional("settings", default={}): dict,
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
