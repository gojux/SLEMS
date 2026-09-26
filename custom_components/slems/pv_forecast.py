"""PV forecast from any solar forecast provider of the HA energy platform.

Providers such as Forecast.Solar or Solcast implement
``async_get_solar_forecast`` in their ``energy`` platform and return energy per
period (``wh_hours``: ISO timestamp -> Wh). SLEMS keys every period by its
start. Forecast.Solar keys a period by its end ("the value is always for the
period from last timestamp to the timestamp in the key", API documentation);
its timestamps are converted (``PERIOD_END_DOMAINS``).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
import logging

from homeassistant.components.energy.websocket_api import async_get_energy_platforms
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

HOUR = timedelta(hours=1)
# Providers whose timestamps mark the end of a period.
PERIOD_END_DOMAINS = frozenset({"forecast_solar"})

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
            wh_hours = result["wh_hours"]
            if entry.domain in PERIOD_END_DOMAINS:
                wh_hours = keyed_by_start(wh_hours)
            forecasts.append(wh_hours)
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


def keyed_by_start(wh_hours: Mapping[str, float]) -> dict[str, float]:
    """Re-key periods given by their end to their start (the previous timestamp).

    A period starts at the previous timestamp if that is at most one hour
    earlier, otherwise one hour before its end (first period of a day).
    """
    ends = sorted(
        (moment, wh)
        for timestamp, wh in wh_hours.items()
        if (moment := dt_util.parse_datetime(timestamp)) is not None
    )
    result: dict[str, float] = {}
    previous: datetime | None = None
    for end, wh in ends:
        start = previous if previous is not None and end - previous <= HOUR else end - HOUR
        result[start.isoformat()] = result.get(start.isoformat(), 0.0) + wh
        previous = end
    return result


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


def power_lookup(series: Mapping[datetime, float]) -> Callable[[datetime], float]:
    """Mean power (W) of the forecast period a moment falls in.

    A period lasts until the next timestamp, at most one hour. Providers add
    irregular timestamps (sunrise, sunset); the last period gets the typical
    length.
    """
    starts = sorted(series)
    gaps = sorted(b - a for a, b in zip(starts, starts[1:], strict=False))
    default = min(gaps[len(gaps) // 2], HOUR) if gaps else HOUR
    durations = [
        min(starts[i + 1] - start, HOUR) if i + 1 < len(starts) else default
        for i, start in enumerate(starts)
    ]

    def power(moment: datetime) -> float:
        index = bisect_right(starts, moment) - 1
        if index < 0:
            return 0.0
        start, duration = starts[index], durations[index]
        if moment >= start + duration:
            return 0.0
        return series[start] / (duration / HOUR)

    return power


def mean_power(
    power: Callable[[datetime], float], start: datetime, length: timedelta
) -> float:
    """Mean of ``power`` over a period, sampled every 5 minutes."""
    step = timedelta(minutes=5)
    count = max(1, length // step)
    return sum(power(start + step * i + step / 2) for i in range(count)) / count
