"""Helpers for reading Home Assistant states."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfPower
from homeassistant.core import State

_POWER_FACTORS: dict[str, float] = {
    UnitOfPower.WATT: 1.0,
    UnitOfPower.KILO_WATT: 1000.0,
    UnitOfPower.MEGA_WATT: 1_000_000.0,
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
