"""PV forecast from any solar forecast provider of the HA energy platform.

Providers such as Forecast.Solar or Solcast implement
``async_get_solar_forecast`` in their ``energy`` platform and return energy per
period (``wh_hours``: ISO timestamp of the period start -> Wh).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
import logging

from homeassistant.components.energy.websocket_api import async_get_energy_platforms
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Period start (aware datetime) -> energy in Wh.
type PvForecast = dict[datetime, float]


async def async_forecast_provider_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all loaded config entries that can deliver a solar forecast."""
    platforms = await async_get_energy_platforms(hass)
    return [
        entry
        for entry in hass.config_entries.async_entries()
        if entry.domain in platforms
    ]


async def async_get_pv_forecast(
    hass: HomeAssistant, entry_ids: Iterable[str]
) -> PvForecast | None:
    """Return the summed forecast of the given provider entries.

    None if no provider delivered data. Providers that are not loaded (yet) or
    fail are skipped, a provider must never break SLEMS.
    """
    platforms = await async_get_energy_platforms(hass)
    forecasts: list[Mapping[str, float]] = []
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.state is not ConfigEntryState.LOADED
            or entry.domain not in platforms
        ):
            continue
        try:
            result = await platforms[entry.domain](hass, entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Solar forecast of %s failed", entry.title)
            continue
        if result and "wh_hours" in result:
            forecasts.append(result["wh_hours"])
    if not forecasts:
        return None
    return merge_forecasts(forecasts)


def merge_forecasts(forecasts: Iterable[Mapping[str, float]]) -> PvForecast:
    """Sum several ``wh_hours`` mappings (e.g. one per roof plane)."""
    merged: PvForecast = {}
    for forecast in forecasts:
        for timestamp, wh in forecast.items():
            start = dt_util.parse_datetime(timestamp)
            if start is None:
                continue
            merged[start] = merged.get(start, 0.0) + float(wh)
    return dict(sorted(merged.items()))


def energy_on_day(forecast: PvForecast, day: date) -> float:
    """Forecast energy in Wh for a local calendar day."""
    return sum(
        wh for start, wh in forecast.items() if dt_util.as_local(start).date() == day
    )


def hourly(series: Mapping[datetime, float]) -> dict[datetime, float]:
    """Sum periods of any length (e.g. 30 min) into hourly local buckets."""
    buckets: dict[datetime, float] = {}
    for start, wh in series.items():
        hour = dt_util.as_local(start).replace(minute=0, second=0, microsecond=0)
        buckets[hour] = buckets.get(hour, 0.0) + wh
    return buckets
