"""Configured consumers and their measured state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BLOCK_ENTITY,
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_ENERGY_ENTITY,
    CONF_INCLUDED_IN_METER,
    CONF_MAX_POWER_W,
    CONF_MIN_POWER_W,
    CONF_NOMINAL_POWER_W,
    CONF_POWER_ENTITY,
    CONF_PRIORITY,
    DEFAULT_PRIORITY,
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
    # Entity whose "on" state means the consumer must not be controlled.
    block_entity_id: str | None
    # 1 = highest priority.
    priority: int

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
        blocked = block_state is not None and block_state.state == STATE_ON
    return ConsumerState(
        power_w=state_as_watts(hass.states.get(consumer.power_entity_id)),
        energy_kwh=state_as_kwh(hass.states.get(consumer.energy_entity_id)),
        blocked=blocked,
    )
