"""Helpers for reading Home Assistant states."""

from __future__ import annotations

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import State

_POWER_FACTORS: dict[str, float] = {
    UnitOfPower.WATT: 1.0,
    UnitOfPower.KILO_WATT: 1000.0,
    UnitOfPower.MEGA_WATT: 1_000_000.0,
}

_ENERGY_FACTORS: dict[str, float] = {
    UnitOfEnergy.WATT_HOUR: 0.001,
    UnitOfEnergy.KILO_WATT_HOUR: 1.0,
    UnitOfEnergy.MEGA_WATT_HOUR: 1000.0,
}


def state_as_float(state: State | None) -> float | None:
    """Return the numeric value of a state, or None if it is not usable."""
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def state_as_watts(state: State | None) -> float | None:
    """Return a power state converted to W (accepts W, kW and MW)."""
    value = state_as_float(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get("unit_of_measurement", UnitOfPower.WATT)
    return value * _POWER_FACTORS.get(unit, 1.0)


def state_as_kwh(state: State | None) -> float | None:
    """Return an energy state converted to kWh (accepts Wh, kWh and MWh)."""
    value = state_as_float(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get("unit_of_measurement", UnitOfEnergy.KILO_WATT_HOUR)
    return value * _ENERGY_FACTORS.get(unit, 1.0)
