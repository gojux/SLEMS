"""Real-time controller: executes the plan in operating mode *active*.

It is the only component that sends commands. A control cycle runs on every
change of the grid meter (at most every ``MIN_INTERVAL_S``) and after every
coordinator update. It plans with the current measurements and the last
commanded battery powers, so it does not wait for the slow battery polling.

After a command the controller waits for the settle time (setting) before it
corrects again: until the grid meter shows the effect of the command, the
measured grid power and the assumed battery power do not match, and
correcting in between makes the loop oscillate.

Safety:
* Without a fresh grid meter value for ``GRID_STALE_S`` all batteries are
  handed back to their internal logic until the meter reports again.
* Battery commands are only sent when the set point changes by more than a
  dead band, and repeated as keep-alive.
* Consumers get at most one command per ``CONSUMER_COMMAND_INTERVAL_S``.
* A consumer that draws (almost) nothing although commanded, e.g. because its
  own thermostat switched off, is treated as saturated for
  ``SATURATION_HOLD_S`` and planned like an uncontrolled load meanwhile.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .battery_distribution import LEAVE_RAMP_S
from .const import ControlMode, OperatingMode
from .util import state_as_float

if TYPE_CHECKING:
    from .coordinator import SlemsCoordinator, SystemSnapshot

_LOGGER = logging.getLogger(__name__)

MIN_INTERVAL_S = 2.0
GRID_STALE_S = 60.0
BATTERY_DEADBAND_W = 25.0
BATTERY_KEEPALIVE_S = 60.0
CONSUMER_DEADBAND_W = 50.0
CONSUMER_COMMAND_INTERVAL_S = 10.0
SATURATION_RATIO = 0.1
SATURATION_DELAY_S = 120.0
SATURATION_HOLD_S = 900.0


class ControlStatus(StrEnum):
    """State of the real-time controller."""

    INACTIVE = "inactive"
    ACTIVE = "active"
    GRID_STALE = "grid_stale"


class RealTimeController:
    """Sends the planned powers to batteries and consumers."""

    def __init__(self, coordinator: SlemsCoordinator) -> None:
        self._coordinator = coordinator
        self._hass = coordinator.hass
        self._lock = asyncio.Lock()
        self._last_run = 0.0
        self._pending: CALLBACK_TYPE | None = None
        self._pending_at = 0.0
        self._rerun = False
        # battery id -> (commanded power, monotonic time of the command)
        self._battery_commands: dict[str, tuple[float, float]] = {}
        # consumer id -> (commanded power, monotonic time of the command)
        self._consumer_commands: dict[str, tuple[float, float]] = {}
        # consumer id -> monotonic time until which it counts as saturated
        self._saturated_until: dict[str, float] = {}
        self._last_command = 0.0
        self.status = ControlStatus.INACTIVE

    @property
    def saturated(self) -> frozenset[str]:
        now = time.monotonic()
        return frozenset(c for c, until in self._saturated_until.items() if until > now)

    @property
    def _active(self) -> bool:
        return self._coordinator.settings.operating_mode is OperatingMode.ACTIVE

    @callback
    def request(self) -> None:
        """Ask for a control cycle as soon as the minimum interval allows."""
        if not self._active:
            self.status = ControlStatus.INACTIVE
            # Batteries were released; the next activation must send again.
            self._battery_commands.clear()
            self._consumer_commands.clear()
            return
        if self._lock.locked():
            self._rerun = True
            return
        now = time.monotonic()
        settle = self._coordinator.settings.control_settle_s
        if any(
            b.leaving_until is not None and now <= b.leaving_until + MIN_INTERVAL_S
            for b in self._coordinator.batteries
        ):
            # Moving power between batteries keeps the sum, the meter does not
            # need to settle; follow the ramp-out closely.
            settle = 0.0
        min_interval = 0.0 if settle == 0.0 else MIN_INTERVAL_S
        wait = max(min_interval - (now - self._last_run), settle - (now - self._last_command))
        if self._pending is not None:
            if now + wait >= self._pending_at:
                return
            # An earlier cycle is needed (e.g. a battery ramps out).
            self._pending()
            self._pending = None
        if wait > 0:
            self._pending_at = now + wait
            self._pending = async_call_later(self._hass, wait, self._on_timer)
        else:
            self._start()

    @callback
    def _on_timer(self, _now) -> None:
        self._pending = None
        self._start()

    @callback
    def _start(self) -> None:
        self._coordinator.config_entry.async_create_background_task(
            self._hass, self._async_run(), "slems control cycle"
        )

    @callback
    def disable_battery(self, battery) -> bool:
        """Start ramping out a discharging battery; False if not applicable.

        Only in active mode and only while SLEMS lets the battery discharge;
        otherwise the caller releases the battery at once.
        """
        commanded = self._battery_commands.get(battery.subentry_id)
        if not self._active or commanded is None or commanded[0] >= 0:
            return False
        battery.leaving_until = time.monotonic() + LEAVE_RAMP_S
        self.request()
        return True

    @callback
    def _on_ramp_timer(self, _now) -> None:
        self.request()

    @callback
    def shutdown(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    async def _async_run(self) -> None:
        async with self._lock:
            self._last_run = time.monotonic()
            try:
                await self._async_cycle()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Control cycle failed")
        if self._rerun:
            self._rerun = False
            self.request()

    async def _async_cycle(self) -> None:
        if not self._active:
            self.status = ControlStatus.INACTIVE
            return
        coordinator = self._coordinator
        if self._grid_stale():
            if self.status is not ControlStatus.GRID_STALE:
                _LOGGER.warning("Grid meter stale, handing batteries back to their own logic")
                await coordinator.async_release_batteries()
                self._battery_commands.clear()
            self.status = ControlStatus.GRID_STALE
            return
        self.status = ControlStatus.ACTIVE
        start = time.monotonic()
        if await self._async_release_ramped_out():
            # Only the handover in this cycle; the meter first has to catch up.
            return

        commanded = {battery_id: power for battery_id, (power, _) in self._battery_commands.items()}
        snapshot = coordinator.fast_snapshot(commanded, self.saturated)
        if snapshot is None or snapshot.distribution is None or snapshot.allocation is None:
            return
        await self._async_apply_batteries(snapshot)
        await self._async_apply_consumers(snapshot)
        coordinator.publish(snapshot)

        leaving = [b.leaving_until for b in coordinator.batteries if b.leaving_until is not None]
        if leaving:
            # Follow a ramp-out step by step, even without meter updates: every
            # MIN_INTERVAL_S from the start of this cycle, the last step exactly
            # at the end of the ramp, then the release.
            end = min(leaving)
            due = min(start + MIN_INTERVAL_S, end) if start < end else time.monotonic()
            async_call_later(self._hass, max(0.05, due - time.monotonic()), self._on_ramp_timer)

    async def _async_release_ramped_out(self) -> bool:
        """Release batteries whose ramp-out ended and that got 0 W; True if any."""
        now = time.monotonic()
        released = False
        for battery in self._coordinator.batteries:
            previous = self._battery_commands.get(battery.subentry_id)
            if (
                battery.leaving_until is not None
                and now >= battery.leaving_until
                and previous is not None
                and previous[0] == 0
            ):
                _LOGGER.debug("Battery %s: ramp-out finished, released", battery.name)
                battery.leaving_until = None
                self._battery_commands.pop(battery.subentry_id, None)
                await battery.driver.release_control()
                released = True
        return released

    def _grid_stale(self) -> bool:
        state = self._hass.states.get(self._coordinator.grid_entity_id)
        if state is None or state_as_float(state) is None:
            return True
        age = (dt_util.utcnow() - state.last_reported).total_seconds()
        return age > GRID_STALE_S

    async def _async_apply_batteries(self, snapshot: SystemSnapshot) -> None:
        now = time.monotonic()

        def magnitude_change(battery) -> float:
            target = abs(snapshot.distribution.power_w.get(battery.subentry_id, 0.0))
            previous = self._battery_commands.get(battery.subentry_id)
            return target - (abs(previous[0]) if previous else 0.0)

        # Batteries that reduce their power are written first: while power
        # moves between batteries, the short gap between the writes then
        # causes a little import instead of feeding battery energy into the grid.
        for battery in sorted(self._coordinator.batteries, key=magnitude_change):
            if not battery.driver.capabilities.controllable:
                continue
            if not battery.participating:
                continue
            previous = self._battery_commands.get(battery.subentry_id)
            target = round(snapshot.distribution.power_w.get(battery.subentry_id, 0.0))
            if previous is not None:
                power, sent = previous
                same_direction = (power > 0) == (target > 0) and (power < 0) == (target < 0)
                if (
                    abs(power - target) < BATTERY_DEADBAND_W
                    and same_direction
                    and now - sent < BATTERY_KEEPALIVE_S
                ):
                    continue
            _LOGGER.debug("Battery %s: set point %d W", battery.name, target)
            if await battery.driver.apply_power(target):
                self._battery_commands[battery.subentry_id] = (target, now)
                self._last_command = time.monotonic()

    async def _async_apply_consumers(self, snapshot: SystemSnapshot) -> None:
        now = time.monotonic()
        for consumer in self._coordinator.consumers:
            subentry_id = consumer.subentry_id
            target = snapshot.allocation.consumer_power_w.get(subentry_id)
            if target is None or not snapshot.is_controllable_now(subentry_id):
                continue
            self._check_saturation(subentry_id, snapshot, now)
            previous = self._consumer_commands.get(subentry_id)
            if previous is not None and now - previous[1] < CONSUMER_COMMAND_INTERVAL_S:
                continue
            state = self._hass.states.get(consumer.control_entity_id)
            if state is None:
                continue
            domain = consumer.control_entity_id.split(".", 1)[0]
            if consumer.control_mode is ControlMode.SWITCH:
                want_on = target > 0
                if (state.state == STATE_ON) == want_on:
                    continue
                await self._hass.services.async_call(
                    domain,
                    "turn_on" if want_on else "turn_off",
                    {ATTR_ENTITY_ID: consumer.control_entity_id},
                )
            else:
                current = state_as_float(state)
                value = _clamp_to_entity(target, state)
                if current is not None and abs(current - value) < CONSUMER_DEADBAND_W and (
                    value != 0 or current == 0
                ):
                    continue
                await self._hass.services.async_call(
                    domain,
                    "set_value",
                    {ATTR_ENTITY_ID: consumer.control_entity_id, "value": value},
                )
            self._consumer_commands[subentry_id] = (target, now)
            self._last_command = time.monotonic()

    def _check_saturation(self, subentry_id: str, snapshot: SystemSnapshot, now: float) -> None:
        """Mark a consumer saturated if it ignores its command for a while."""
        previous = self._consumer_commands.get(subentry_id)
        measured = snapshot.consumers[subentry_id].power_w
        if previous is None or measured is None:
            return
        commanded, since = previous
        if (
            commanded > 0
            and now - since > SATURATION_DELAY_S
            and measured < commanded * SATURATION_RATIO
        ):
            _LOGGER.debug("Consumer %s saturated (draws %.0f W)", subentry_id, measured)
            self._saturated_until[subentry_id] = now + SATURATION_HOLD_S
            del self._consumer_commands[subentry_id]


def _clamp_to_entity(value: float, state) -> float:
    """Keep a set point within the min/max/step of a number entity."""
    minimum = state.attributes.get("min")
    maximum = state.attributes.get("max")
    step = state.attributes.get("step") or 1
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return round(value / step) * step
