"""Configured consumers and their measured state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BLOCK_ENTITY,
    CONF_CAP_MODE,
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_ENERGY_ENTITY,
    CONF_INCLUDED_IN_METER,
    CONF_MAX_POWER_W,
    CONF_MIN_OFF_MINUTES,
    CONF_MIN_ON_MINUTES,
    CONF_MIN_POWER_W,
    CONF_NOMINAL_POWER_W,
    CONF_POWER_ENTITY,
    CONF_PRIORITY,
    CONF_THERMOSTAT_CYCLES,
    DEFAULT_PRIORITY,
    CapMode,
    ConsumerType,
    ControlMode,
)
from .util import state_as_kwh, state_as_watts


@dataclass(frozen=True)
class ConsumerConfig:
    """Static configuration of one consumer (from its subentry)."""

    subentry_id: str
    name: str
    consumer_type: ConsumerType
    power_entity_id: str
    energy_entity_id: str
    # True if the consumer is behind the smart meter, i.e. already part of the
    # measured grid power.
    included_in_meter: bool
    control_mode: ControlMode
    control_entity_id: str | None
    # Switch consumers: power while on. Power consumers: set point range.
    nominal_power_w: int | None
    min_power_w: int | None
    max_power_w: int | None
    # The consumer must not be controlled while this entity is "on" (a
    # water heater: while its operation mode is "off").
    block_entity_id: str | None
    # 1 = highest priority.
    priority: int
    # Optional minimum runtime and minimum pause in seconds (0 = none).
    min_on_s: float = 0.0
    min_off_s: float = 0.0
    # Its own thermostat switches it on and off while it is commanded (e.g. a
    # heating rod that measures at the element): pauses are no saturation.
    thermostat_cycles: bool = False
    # Part in the feed-in cap.
    cap_mode: CapMode = CapMode.EMERGENCY

    @property
    def controllable(self) -> bool:
        return self.control_mode is not ControlMode.NONE and bool(self.control_entity_id)

    @classmethod
    def from_subentry(
        cls, subentry_id: str, title: str, data: Mapping[str, Any]
    ) -> ConsumerConfig:
        return cls(
            subentry_id=subentry_id,
            name=title,
            consumer_type=ConsumerType(data[CONF_CONSUMER_TYPE]),
            power_entity_id=data[CONF_POWER_ENTITY],
            energy_entity_id=data[CONF_ENERGY_ENTITY],
            included_in_meter=data[CONF_INCLUDED_IN_METER],
            control_mode=ControlMode(data[CONF_CONTROL_MODE]),
            control_entity_id=data.get(CONF_CONTROL_ENTITY),
            nominal_power_w=data.get(CONF_NOMINAL_POWER_W),
            min_power_w=data.get(CONF_MIN_POWER_W),
            max_power_w=data.get(CONF_MAX_POWER_W),
            block_entity_id=data.get(CONF_BLOCK_ENTITY),
            priority=data.get(CONF_PRIORITY, DEFAULT_PRIORITY),
            min_on_s=(data.get(CONF_MIN_ON_MINUTES) or 0) * 60,
            min_off_s=(data.get(CONF_MIN_OFF_MINUTES) or 0) * 60,
            thermostat_cycles=data.get(CONF_THERMOSTAT_CYCLES, False),
            cap_mode=CapMode(data.get(CONF_CAP_MODE, CapMode.EMERGENCY)),
        )


@dataclass
class ConsumerState:
    """Measured state of one consumer. Unknown values are None."""

    power_w: float | None = None
    energy_kwh: float | None = None
    blocked: bool = False


def read_consumer_state(hass: HomeAssistant, consumer: ConsumerConfig) -> ConsumerState:
    """Read the current state of a consumer from its entities."""
    blocked = False
    if consumer.block_entity_id:
        block_state = hass.states.get(consumer.block_entity_id)
        if block_state is None:
            blocked = False
        elif consumer.block_entity_id.startswith("water_heater."):
            blocked = block_state.state == STATE_OFF
        else:
            blocked = block_state.state == STATE_ON
    return ConsumerState(
        power_w=state_as_watts(hass.states.get(consumer.power_entity_id)),
        energy_kwh=state_as_kwh(hass.states.get(consumer.energy_entity_id)),
        blocked=blocked,
    )


class RuntimeTracker:
    """Tracks when SLEMS switched a consumer on or off.

    Enforces the optional minimum runtime and minimum pause. The tracked state
    is the one commanded by SLEMS (in simulation mode the virtual one).
    """

    def __init__(self) -> None:
        # subentry id -> (is on, monotonic time of the last change)
        self._states: dict[str, tuple[bool, float]] = {}

    def update(self, subentry_id: str, is_on: bool, now: float) -> None:
        previous = self._states.get(subentry_id)
        if previous is None or previous[0] != is_on:
            self._states[subentry_id] = (is_on, now)

    def must_stay_on(self, consumer: ConsumerConfig, now: float) -> bool:
        state = self._states.get(consumer.subentry_id)
        return state is not None and state[0] and now - state[1] < consumer.min_on_s

    def must_stay_off(self, consumer: ConsumerConfig, now: float) -> bool:
        state = self._states.get(consumer.subentry_id)
        return state is not None and not state[0] and now - state[1] < consumer.min_off_s
