"""Simulated solar forecast for the development and end-to-end instances.

A fixed day curve (sunrise 06:00, sunset 20:00 local time, highest at 13:00)
with the peak power of the entry, for today and tomorrow, as a solar forecast
of the energy platform, so SLEMS sees a forecast provider without internet.
``input_number.sim_solar_forecast_shift`` shifts the curve by hours.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "sim_solar_forecast"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
