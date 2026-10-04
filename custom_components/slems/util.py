"""Helpers for reading Home Assistant states and calling services."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

# A service call SLEMS waits for (a write to another integration's device)
# fails after this long, so a hanging integration does not stall the control.
SERVICE_TIMEOUT_S = 10.0


class ServiceCallError(Exception):
    """A service call failed, was rejected or did not finish in time."""


async def async_call_service(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any]
) -> None:
    """Call a service and wait until it is done (raises ``ServiceCallError``).

    Any error of the called integration counts as a failed call, so one faulty
    device cannot stop the control of the others in the same cycle.
    """
    try:
        async with asyncio.timeout(SERVICE_TIMEOUT_S):
            await hass.services.async_call(domain, service, data, blocking=True)
    except TimeoutError as err:
        raise ServiceCallError(f"{domain}.{service}: no answer within {SERVICE_TIMEOUT_S:.0f} s") from err
    except (HomeAssistantError, vol.Invalid) as err:
        raise ServiceCallError(f"{domain}.{service}: {err}") from err
    except Exception as err:  # noqa: BLE001 - the called integration may raise anything
        raise ServiceCallError(f"{domain}.{service}: {type(err).__name__}: {err}") from err

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
        value = float(state.state)
    except ValueError:
        return None
    # "nan" and "inf" parse as floats but are no measurement.
    return value if math.isfinite(value) else None


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


def clamp_to_entity(value: float, state: State) -> float:
    """Keep a set point within the min/max/step of a number entity."""
    minimum = state.attributes.get("min")
    maximum = state.attributes.get("max")
    step = state.attributes.get("step") or 1
    low = float(minimum) if minimum is not None else -math.inf
    high = float(maximum) if maximum is not None else math.inf
    value = min(high, max(low, value))
    rounded = round(value / step) * step
    # A step outside the range (bounds not on the step grid) goes one step inwards;
    # if no step fits into the range, the bound itself.
    if rounded > high:
        rounded -= step
    elif rounded < low:
        rounded += step
    return rounded if low <= rounded <= high else value
