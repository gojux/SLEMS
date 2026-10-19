"""Solar forecast of the energy platform (Wh per hour, keyed by the hour start)."""

from __future__ import annotations

from datetime import timedelta
import math

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

SUNRISE_H = 6
SUNSET_H = 20
# Hours the day curve of all entries is shifted by, e.g. to have sun at night.
SHIFT_ENTITY = "input_number.sim_solar_forecast_shift"


async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str) -> dict | None:
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None:
        return None
    peak = float(entry.data["peak_w"])
    try:
        shift = float(hass.states.get(SHIFT_ENTITY).state)
    except (AttributeError, ValueError):
        shift = 0.0
    start = dt_util.start_of_local_day()
    wh_hours = {}
    for hour in range(48):
        moment = start + timedelta(hours=hour)
        middle = (moment.hour + 0.5 - shift) % 24
        if SUNRISE_H <= middle < SUNSET_H:
            share = math.sin(math.pi * (middle - SUNRISE_H) / (SUNSET_H - SUNRISE_H))
            wh_hours[dt_util.as_utc(moment).isoformat()] = round(peak * share)
    return {"wh_hours": wh_hours}
