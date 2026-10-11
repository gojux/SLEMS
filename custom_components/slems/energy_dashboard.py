"""What the energy dashboard of Home Assistant already knows: grid, PV and device sensors.

The config flow suggests SLEMS's settings from it. ``read_energy_dashboard`` is
a pure function over the stored preferences; it reads the unified grid source
and the legacy one (``flow_from`` / ``flow_to`` / ``power`` lists).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.energy.data import async_get_manager
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardBattery:
    """A battery of the energy dashboard."""

    soc: str | None = None
    power: str | None = None
    # The dashboard's battery power is positive for discharging (SLEMS: charging); "inverted" sensors are the opposite.
    power_discharge_positive: bool = True
    charged: str | None = None
    discharged: str | None = None


@dataclass(frozen=True)
class EnergyDashboard:
    """Sensors of the energy dashboard SLEMS can use."""

    grid_power: str | None = None
    # The dashboard's grid power is positive for import like SLEMS's; "inverted" sensors are the opposite.
    grid_power_inverted: bool = False
    grid_import: str | None = None
    grid_export: str | None = None
    pv_power: tuple[str, ...] = ()
    pv_energy: tuple[str, ...] = ()
    pv_forecast_entries: tuple[str, ...] = ()
    batteries: tuple[DashboardBattery, ...] = ()
    # Energy sensor of an individual device -> its power sensor (None if not set).
    devices: Mapping[str, str | None] = field(default_factory=dict)


def _signed_power(source: Mapping[str, Any]) -> tuple[str | None, bool]:
    """A single signed power sensor and whether it is "inverted" against the dashboard's sign (grid: positive
    = import, battery: positive = discharge); two sensors, one per direction, do not fit."""
    if rate := source.get("stat_rate"):
        return rate, False
    config = source.get("power_config") or {}
    if rate := config.get("stat_rate"):
        return rate, False
    if rate := config.get("stat_rate_inverted"):
        return rate, True
    return None, False


def read_energy_dashboard(prefs: Mapping[str, Any] | None) -> EnergyDashboard:
    """The sensors of the stored energy preferences; the first grid source counts."""
    if not prefs:
        return EnergyDashboard()
    grid_power: str | None = None
    inverted = False
    grid_import: str | None = None
    grid_export: str | None = None
    pv_power: list[str] = []
    pv_energy: list[str] = []
    forecasts: list[str] = []
    batteries: list[DashboardBattery] = []
    for source in prefs.get("energy_sources") or ():
        kind = source.get("type")
        if kind == "grid":
            imports = [source.get("stat_energy_from")] + [f.get("stat_energy_from") for f in source.get("flow_from") or ()]
            exports = [source.get("stat_energy_to")] + [f.get("stat_energy_to") for f in source.get("flow_to") or ()]
            grid_import = grid_import or next((s for s in imports if s), None)
            grid_export = grid_export or next((s for s in exports if s), None)
            for candidate in (source, *(source.get("power") or ())):
                if grid_power is None:
                    grid_power, inverted = _signed_power(candidate)
        elif kind == "solar":
            if energy := source.get("stat_energy_from"):
                pv_energy.append(energy)
            if power := source.get("stat_rate"):
                pv_power.append(power)
            forecasts.extend(e for e in source.get("config_entry_solar_forecast") or () if e not in forecasts)
        elif kind == "battery":
            power, charge_positive = _signed_power(source)
            batteries.append(
                DashboardBattery(
                    soc=source.get("stat_soc"),
                    power=power,
                    power_discharge_positive=not charge_positive,
                    charged=source.get("stat_energy_to"),
                    discharged=source.get("stat_energy_from"),
                )
            )
    devices = {
        device["stat_consumption"]: device.get("stat_rate")
        for device in prefs.get("device_consumption") or ()
        if device.get("stat_consumption")
    }
    return EnergyDashboard(
        grid_power=grid_power,
        grid_power_inverted=inverted,
        grid_import=grid_import,
        grid_export=grid_export,
        pv_power=tuple(pv_power),
        pv_energy=tuple(pv_energy),
        pv_forecast_entries=tuple(forecasts),
        batteries=tuple(batteries),
        devices=devices,
    )


async def async_energy_dashboard(hass: HomeAssistant) -> EnergyDashboard:
    """The energy dashboard's sensors; empty if it is not set up."""
    try:
        manager = await async_get_manager(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Energy preferences not available", exc_info=True)
        return EnergyDashboard()
    return read_energy_dashboard(manager.data)
