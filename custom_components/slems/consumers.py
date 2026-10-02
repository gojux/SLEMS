"""Configured consumers and their measured state.

A consumer with current control (A) is planned in watts like a power
controlled one: its power range is the current range × voltage × the active
phases (fixed, or from a phases entity, e.g. of a wallbox or an evcc
loadpoint that switches between one and three phases itself). The planned
power is sent as whole amperes, rounded down so the charging stays within
the power planned for it, to a number entity or a select with ampere
options (the maximum current of an evcc loadpoint in ha-evcc).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State

from .const import (
    CONF_BLOCK_ENTITY,
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_ENERGY_ENTITY,
    CONF_INCLUDED_IN_METER,
    CONF_MAX_CURRENT_A,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT_A,
    CONF_MIN_OFF_MINUTES,
    CONF_MIN_ON_MINUTES,
    CONF_MIN_POWER_W,
    CONF_NOMINAL_POWER_W,
    CONF_PHASES,
    CONF_PHASES_ENTITY,
    CONF_POWER_ENTITY,
    CONF_PRIORITY,
    CONF_SHOW_IN_FLOW,
    CONF_START_ENTITY,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_TEMPERATURE_2_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    CONF_THERMOSTAT_CYCLES,
    CONF_VOLTAGE_V,
    DEFAULT_MAX_CURRENT_A,
    DEFAULT_MIN_CURRENT_A,
    DEFAULT_PHASES,
    DEFAULT_PRIORITY,
    DEFAULT_VOLTAGE_V,
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
    # Optional temperature sensors of its storage (a boiler); their mean is
    # used to learn how much energy it can still take (ThermalLearner).
    temperature_entity_ids: tuple[str, ...] = ()
    # Shown as a box in the energy flow of the dashboard.
    show_in_flow: bool = True
    # Current control: range (A), phases (fixed or from an entity), voltage.
    min_current_a: float = DEFAULT_MIN_CURRENT_A
    max_current_a: float = DEFAULT_MAX_CURRENT_A
    phases: int = DEFAULT_PHASES
    phases_entity_id: str | None = None
    voltage_v: float = DEFAULT_VOLTAGE_V
    # Optional start/stop entity (switch, or select with the on / off option).
    start_entity_id: str | None = None
    start_on: str | None = None
    start_off: str | None = None

    @property
    def controllable(self) -> bool:
        return self.control_mode is not ControlMode.NONE and bool(self.control_entity_id)

    def with_phases(self, phases: int) -> ConsumerConfig:
        """A current controlled consumer with ``phases`` active: its power range follows."""
        if self.control_mode is not ControlMode.CURRENT:
            return self
        return replace(
            self,
            phases=phases,
            min_power_w=round(self.min_current_a * self.voltage_v * phases),
            max_power_w=round(self.max_current_a * self.voltage_v * phases),
        )

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
            show_in_flow=data.get(CONF_SHOW_IN_FLOW, True),
            temperature_entity_ids=tuple(
                entity_id
                for entity_id in (data.get(CONF_TEMPERATURE_ENTITY), data.get(CONF_TEMPERATURE_2_ENTITY))
                if entity_id
            ),
            min_current_a=data.get(CONF_MIN_CURRENT_A, DEFAULT_MIN_CURRENT_A),
            max_current_a=data.get(CONF_MAX_CURRENT_A, DEFAULT_MAX_CURRENT_A),
            phases=int(data.get(CONF_PHASES, DEFAULT_PHASES)),
            phases_entity_id=data.get(CONF_PHASES_ENTITY),
            voltage_v=data.get(CONF_VOLTAGE_V, DEFAULT_VOLTAGE_V),
            start_entity_id=data.get(CONF_START_ENTITY),
            start_on=data.get(CONF_START_ON),
            start_off=data.get(CONF_START_OFF),
        ).with_phases(int(data.get(CONF_PHASES, DEFAULT_PHASES)))


def amps_for(power_w: float, voltage_v: float, phases: int) -> int:
    """Whole amperes for ``power_w``, rounded down (never above the planned power)."""
    if power_w <= 0 or voltage_v <= 0 or phases <= 0:
        return 0
    return math.floor(power_w / (voltage_v * phases) + 1e-6)


def current_option(state: State, amps: float) -> str | None:
    """Option of a select with ampere options: the highest not above ``amps``,
    else the lowest (None without numeric options)."""
    options: list[tuple[float, str]] = []
    for option in state.attributes.get("options", []):
        try:
            options.append((float(option), option))
        except (TypeError, ValueError):
            continue
    if not options:
        return None
    options.sort()
    below = [option for value, option in options if value <= amps + 1e-6]
    return below[-1] if below else options[0][1]


def active_phases(hass: HomeAssistant, consumer: ConsumerConfig) -> int:
    """Phases in use: from the phases entity (1-3), otherwise the configured number."""
    if consumer.phases_entity_id:
        state = hass.states.get(consumer.phases_entity_id)
        try:
            value = round(float(state.state)) if state is not None else None
        except ValueError:
            value = None
        if value is not None and 1 <= value <= 3:
            return value
    return consumer.phases


@dataclass
class ConsumerState:
    """Measured state of one consumer. Unknown values are None."""

    power_w: float | None = None
    energy_kwh: float | None = None
    blocked: bool = False
    # Mean of its temperature sensors (°C); None without sensors or if one is unknown.
    temperature_c: float | None = None
    # Each sensor in the order of the configuration (None if unknown).
    temperatures_c: tuple[float | None, ...] = ()


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
    temperatures = [_temperature(hass.states.get(e)) for e in consumer.temperature_entity_ids]
    return ConsumerState(
        power_w=state_as_watts(hass.states.get(consumer.power_entity_id)),
        energy_kwh=state_as_kwh(hass.states.get(consumer.energy_entity_id)),
        blocked=blocked,
        temperature_c=(
            sum(temperatures) / len(temperatures)
            if temperatures and None not in temperatures
            else None
        ),
        temperatures_c=tuple(temperatures),
    )


def _temperature(state) -> float | None:
    """Temperature in °C (sensors in °F are converted)."""
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if state.attributes.get("unit_of_measurement") == "°F":
        return (value - 32) * 5 / 9
    return value


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
        if previous is None and not is_on:
            # Off since before the start: no pause to wait for.
            self._states[subentry_id] = (False, -math.inf)
        elif previous is None or previous[0] != is_on:
            self._states[subentry_id] = (is_on, now)

    def is_on(self, subentry_id: str) -> bool:
        """Switched on by SLEMS (or virtually, in simulation mode)."""
        state = self._states.get(subentry_id)
        return state is not None and state[0]

    def must_stay_on(self, consumer: ConsumerConfig, now: float) -> bool:
        state = self._states.get(consumer.subentry_id)
        return state is not None and state[0] and now - state[1] < consumer.min_on_s

    def must_stay_off(self, consumer: ConsumerConfig, now: float) -> bool:
        state = self._states.get(consumer.subentry_id)
        return state is not None and not state[0] and now - state[1] < consumer.min_off_s
