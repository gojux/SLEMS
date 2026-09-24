"""Real-time controller: executes the plan in operating mode *active*.

It is the only component that sends commands. A control cycle runs on every
change of the grid meter and after every coordinator update, at most every
*control interval* (setting). It plans with the current measurements and does
not wait for the slow battery polling.

Two measures keep the loop stable although the meter shows the effect of a
command only after a while:
* The battery power assumed in the energy balance is the one the meter can
  already see: the last command older than the learned battery response time
  (see ``response``). Newer commands are not counted twice.
* Each cycle corrects only a share of the remaining deviation (control gain),
  like a proportional controller. The gain adapts itself to the observed
  behaviour (see ``adaptive_gain``) unless the user fixed it.

Safety:
* Without a report of the grid meter for ``max(GRID_STALE_S, 10 × meter
  interval)`` all batteries are handed back to their internal logic until the
  meter reports again.
* Battery commands are only sent when the set point changes by more than a
  dead band, and repeated completely as keep-alive.
* Consumers get at most one command per ``CONSUMER_COMMAND_INTERVAL_S``.
* A consumer that draws (almost) nothing although commanded, e.g. because its
  own thermostat switched off, is treated as saturated for
  ``SATURATION_HOLD_S`` and planned like an uncontrolled load meanwhile.
  A consumer whose thermostat cycles by itself (option) is never saturated:
  it keeps its command and is only *resting* while it draws nothing; the
  batteries get its unused power meanwhile.
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

from .adaptive_gain import AdaptiveGain
from .battery_distribution import LEAVE_RAMP_S
from .delivery_monitor import Action
from .drivers import BatteryDriverError
from .const import ControlMode, OperatingMode
from .response import (
    DEFAULT_BATTERY_RESPONSE_S,
    DEFAULT_CONSUMER_RESPONSE_S,
    MeterCadence,
    StepResponse,
)
from .util import state_as_float

if TYPE_CHECKING:
    from .coordinator import BatteryRuntime, SlemsCoordinator, SystemSnapshot

_LOGGER = logging.getLogger(__name__)

GRID_STALE_S = 60.0
BATTERY_DEADBAND_W = 25.0
BATTERY_KEEPALIVE_S = 60.0
# Commands kept per battery to know which one the meter already shows.
BATTERY_HISTORY = 8
CONSUMER_DEADBAND_W = 50.0
CONSUMER_COMMAND_INTERVAL_S = 10.0
CONSUMER_MIN_STEP_W = 100.0
SATURATION_RATIO = 0.1
SATURATION_HOLD_S = 900.0
# Saturation is assumed after this many consumer response times (bounded).
SATURATION_RESPONSE_FACTOR = 5
SATURATION_DELAY_RANGE_S = (30.0, 300.0)
# Resting after this many consumer response times without power (bounded).
RESTING_RESPONSE_FACTOR = 2
RESTING_DELAY_RANGE_S = (10.0, 60.0)
RAMP_STEP_S = 1.0


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
        # battery id -> [(monotonic time, commanded power)], oldest first
        self._battery_history: dict[str, list[tuple[float, float]]] = {}
        # battery id -> monotonic time of the last complete write
        self._battery_refreshed: dict[str, float] = {}
        # battery id -> monotonic time since the commands have their direction
        self._direction_since: dict[str, float] = {}
        # battery id -> (balancing power, monotonic time of the last full write)
        self._balancing_commands: dict[str, tuple[float, float]] = {}
        # consumer id -> (commanded power, monotonic time of the command)
        self._consumer_commands: dict[str, tuple[float, float]] = {}
        # consumer id -> monotonic time until which it counts as saturated
        self._saturated_until: dict[str, float] = {}
        # Consumers with a cycling thermostat: since when they draw nothing,
        # and the ones resting right now.
        self._low_since: dict[str, float] = {}
        self._resting: set[str] = set()
        self._last_grid_w: float | None = None
        self.meter = MeterCadence()
        self.battery_response = StepResponse(DEFAULT_BATTERY_RESPONSE_S)
        self.consumer_response: dict[str, StepResponse] = {}
        self.gain_adapter = AdaptiveGain(coordinator.settings.control_gain)
        self.status = ControlStatus.INACTIVE

    # --- observations ---------------------------------------------------------

    @callback
    def observe_meter_report(self) -> None:
        """The grid meter reported a value (changed or not)."""
        self.meter.report(time.monotonic())

    @callback
    def observe_grid(self, grid_w: float) -> None:
        """A new grid power value arrived."""
        self._last_grid_w = grid_w
        self.battery_response.sample(time.monotonic(), grid_w)

    @callback
    def observe_consumers(self, snapshot: SystemSnapshot) -> None:
        """Feed the consumers' measured power into their response learners."""
        now = time.monotonic()
        for subentry_id, learner in self.consumer_response.items():
            state = snapshot.consumers.get(subentry_id)
            if state is not None and state.power_w is not None:
                learner.sample(now, state.power_w)

    @property
    def gain(self) -> float:
        """Gain used for the next control cycle."""
        settings = self._coordinator.settings
        return self.gain_adapter.gain if settings.auto_gain else settings.control_gain

    def consumer_response_s(self, subentry_id: str) -> float | None:
        learner = self.consumer_response.get(subentry_id)
        return learner.response_s if learner else None

    # --- scheduling -----------------------------------------------------------

    @property
    def resting(self) -> frozenset[str]:
        return frozenset(self._resting)

    @property
    def saturated(self) -> frozenset[str]:
        now = time.monotonic()
        return frozenset(c for c, until in self._saturated_until.items() if until > now)

    @property
    def _active(self) -> bool:
        return self._coordinator.settings.operating_mode is OperatingMode.ACTIVE

    @callback
    def request(self) -> None:
        """Ask for a control cycle as soon as the control interval allows."""
        if not self._active:
            self.status = ControlStatus.INACTIVE
            # Batteries were released; the next activation must send again.
            self._battery_history.clear()
            self._battery_refreshed.clear()
            self._direction_since.clear()
            self._balancing_commands.clear()
            self._consumer_commands.clear()
            return
        if self._lock.locked():
            self._rerun = True
            return
        now = time.monotonic()
        wait = self._coordinator.settings.control_interval_s - (now - self._last_run)
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
    def disable_battery(self, battery: BatteryRuntime) -> bool:
        """Start ramping out a discharging battery; False if not applicable.

        Only in active mode and only while SLEMS lets the battery discharge;
        otherwise the caller releases the battery at once.
        """
        latest = self._latest_command(battery.subentry_id)
        if not self._active or latest is None or latest >= 0:
            return False
        battery.leaving_until = time.monotonic() + LEAVE_RAMP_S
        self.request()
        return True

    def command_state(self, battery_id: str) -> tuple[float, float] | None:
        """Latest command of a battery and since when it has this direction."""
        latest = self._latest_command(battery_id)
        since = self._direction_since.get(battery_id)
        if latest is None or since is None:
            return None
        return latest, since

    @callback
    def force_refresh(self, battery_id: str) -> None:
        """Write all control registers of a battery again with the next command."""
        self._battery_refreshed.pop(battery_id, None)
        self.request()

    @callback
    def _on_ramp_timer(self, _now) -> None:
        self.request()

    @callback
    def shutdown(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    # --- control cycle --------------------------------------------------------

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
                self._battery_history.clear()
                self._battery_refreshed.clear()
                self._direction_since.clear()
            self.status = ControlStatus.GRID_STALE
            return
        self.status = ControlStatus.ACTIVE
        start = time.monotonic()
        if await self._async_release_ramped_out():
            # Only the handover in this cycle; the meter first has to catch up.
            return

        # Batteries outside the planning (disabled and released, balancing or
        # not responding) must not count in the commanded total.
        for battery in coordinator.batteries:
            if not battery.participating:
                had_commands = self._battery_history.pop(battery.subentry_id, None)
                self._battery_refreshed.pop(battery.subentry_id, None)
                self._direction_since.pop(battery.subentry_id, None)
                if had_commands and battery.not_responding:
                    # Back to its own logic until it is retried.
                    await self._release(battery)

        seen_before = start - self.battery_response.value
        seen = {
            battery_id: power
            for battery_id in self._battery_history
            if (power := self._seen_command(battery_id, seen_before)) is not None
        }
        latest_total = sum(
            power
            for battery_id in self._battery_history
            if (power := self._latest_command(battery_id)) is not None
        )
        snapshot = coordinator.fast_snapshot(
            seen,
            self.saturated,
            previous_total_w=latest_total if self._battery_history else None,
            gain=self.gain,
        )
        if snapshot is None or snapshot.distribution is None or snapshot.allocation is None:
            return
        # Before the normal set points: a battery whose balancing run ended is
        # released first and then planned again.
        await self._async_apply_balancing()
        new_total = await self._async_apply_batteries(snapshot, latest_total)
        if new_total is not None and coordinator.settings.auto_gain:
            self.gain_adapter.observe(start, new_total - latest_total)
        await self._async_apply_consumers(snapshot)
        self.observe_consumers(snapshot)
        coordinator.publish(snapshot)

        leaving = [b.leaving_until for b in coordinator.batteries if b.leaving_until is not None]
        if leaving:
            # Follow a ramp-out step by step, even without meter updates: every
            # RAMP_STEP_S from the start of this cycle, the last step exactly
            # at the end of the ramp, then the release.
            end = min(leaving)
            due = min(start + RAMP_STEP_S, end) if start < end else time.monotonic()
            async_call_later(self._hass, max(0.05, due - time.monotonic()), self._on_ramp_timer)

    def _grid_stale(self) -> bool:
        state = self._hass.states.get(self._coordinator.grid_entity_id)
        if state is None or state_as_float(state) is None:
            return True
        age = time.time() - state.last_reported.timestamp()
        return age > max(GRID_STALE_S, 10 * self.meter.value)

    # --- batteries ------------------------------------------------------------

    @staticmethod
    async def _apply(battery: BatteryRuntime, power: int, refresh: bool) -> bool:
        """Send a set point; an unreachable battery counts as a failed write."""
        try:
            return await battery.driver.apply_power(power, refresh=refresh)
        except BatteryDriverError as err:
            _LOGGER.debug("Battery %s: set point not sent: %s", battery.name, err)
            return False

    @staticmethod
    async def _release(battery: BatteryRuntime) -> None:
        try:
            await battery.driver.release_control()
        except BatteryDriverError as err:
            _LOGGER.debug("Battery %s: not released: %s", battery.name, err)

    def _latest_command(self, battery_id: str) -> float | None:
        history = self._battery_history.get(battery_id)
        return history[-1][1] if history else None

    def _seen_command(self, battery_id: str, before: float) -> float | None:
        """Last command the meter can already show (issued before ``before``)."""
        for issued, power in reversed(self._battery_history.get(battery_id, [])):
            if issued <= before:
                return power
        return None

    def _record_command(self, battery_id: str, power: float, now: float) -> None:
        latest = self._latest_command(battery_id)
        if latest is None or (latest > 0) != (power > 0) or (latest < 0) != (power < 0):
            self._direction_since[battery_id] = now
        history = self._battery_history.setdefault(battery_id, [])
        history.append((now, power))
        del history[:-BATTERY_HISTORY]

    async def _async_release_ramped_out(self) -> bool:
        """Release batteries whose ramp-out ended and that got 0 W; True if any."""
        now = time.monotonic()
        released = False
        for battery in self._coordinator.batteries:
            if (
                battery.leaving_until is not None
                and now >= battery.leaving_until
                and self._latest_command(battery.subentry_id) == 0
            ):
                battery.leaving_until = None
                self._battery_history.pop(battery.subentry_id, None)
                self._battery_refreshed.pop(battery.subentry_id, None)
                self._direction_since.pop(battery.subentry_id, None)
                if battery.balancing_requested:
                    # The balancing run takes over with its next step.
                    _LOGGER.debug("Battery %s: ramp-out finished, cell balancing", battery.name)
                else:
                    _LOGGER.debug("Battery %s: ramp-out finished, released", battery.name)
                    await self._release(battery)
                released = True
        return released

    async def _async_apply_batteries(
        self, snapshot: SystemSnapshot, previous_total: float
    ) -> float | None:
        """Send the set points; return the new total if anything was sent."""
        now = time.monotonic()

        def magnitude_change(battery: BatteryRuntime) -> float:
            target = abs(snapshot.distribution.power_w.get(battery.subentry_id, 0.0))
            latest = self._latest_command(battery.subentry_id)
            return target - (abs(latest) if latest is not None else 0.0)

        new_total = 0.0
        changed = False
        # Batteries that reduce their power are written first: while power
        # moves between batteries, the short gap between the writes then
        # causes a little import instead of feeding battery energy into the grid.
        for battery in sorted(self._coordinator.batteries, key=magnitude_change):
            if not battery.participating or not battery.driver.capabilities.controllable:
                continue
            target = round(snapshot.distribution.power_w.get(battery.subentry_id, 0.0))
            latest = self._latest_command(battery.subentry_id)
            last_refresh = self._battery_refreshed.get(battery.subentry_id, 0.0)
            refresh = now - last_refresh >= BATTERY_KEEPALIVE_S
            if latest is not None and not refresh:
                same_direction = (latest > 0) == (target > 0) and (latest < 0) == (target < 0)
                if abs(latest - target) < BATTERY_DEADBAND_W and same_direction:
                    new_total += latest
                    continue
            _LOGGER.debug("Battery %s: set point %d W", battery.name, target)
            if await self._apply(battery, target, refresh):
                self._record_command(battery.subentry_id, target, now)
                if refresh:
                    self._battery_refreshed[battery.subentry_id] = now
                new_total += target
                changed = True
            else:
                if battery.delivery.record_comm_failure(now) is not Action.NONE:
                    self.request()
                if latest is not None:
                    new_total += latest
        if not changed:
            return None
        # The grid power moves by the change of the battery power.
        self.battery_response.command(now, self._last_grid_w, new_total - previous_total)
        return new_total

    async def _async_apply_balancing(self) -> None:
        """Send the powers of active cell balancing runs.

        A balancing battery is not part of the planning; its power shows up in
        the grid meter like an uncontrolled load or source (see ``plan``).
        When a run ends, the battery is released (as in Omnibattery) and taken
        over again by the normal operation if it can be read.
        """
        now = time.monotonic()
        for battery in self._coordinator.batteries:
            battery_id = battery.subentry_id
            if not battery.balancing_requested:
                if self._balancing_commands.pop(battery_id, None) is not None:
                    _LOGGER.debug("Battery %s: cell balancing ended, released", battery.name)
                    await self._release(battery)
                continue
            target = battery.balancing_power_w
            if target is None:
                continue
            target = round(target)
            previous = self._balancing_commands.get(battery_id)
            refresh = previous is None or now - previous[1] >= BATTERY_KEEPALIVE_S
            if previous is not None and not refresh:
                same_direction = (previous[0] > 0) == (target > 0) and (previous[0] < 0) == (target < 0)
                if abs(previous[0] - target) < BATTERY_DEADBAND_W and same_direction:
                    continue
            _LOGGER.debug("Battery %s: cell balancing set point %d W", battery.name, target)
            if await self._apply(battery, target, refresh):
                # The time of the last complete write is kept for the keep-alive.
                self._balancing_commands[battery_id] = (
                    target, now if refresh else previous[1]
                )

    # --- consumers ------------------------------------------------------------

    async def _async_apply_consumers(self, snapshot: SystemSnapshot) -> None:
        now = time.monotonic()
        for consumer in self._coordinator.consumers:
            subentry_id = consumer.subentry_id
            target = snapshot.allocation.consumer_power_w.get(subentry_id)
            if target is None or not snapshot.is_controllable_now(subentry_id):
                self._low_since.pop(subentry_id, None)
                self._resting.discard(subentry_id)
                continue
            if consumer.thermostat_cycles:
                self._check_resting(subentry_id, snapshot, now)
            else:
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
            measured = snapshot.consumers[subentry_id].power_w
            learner = self.consumer_response.setdefault(
                subentry_id, StepResponse(DEFAULT_CONSUMER_RESPONSE_S, CONSUMER_MIN_STEP_W)
            )
            learner.command(now, measured, target - (measured or 0.0))

    def _check_resting(self, subentry_id: str, snapshot: SystemSnapshot, now: float) -> None:
        """Resting: a consumer with a cycling thermostat draws nothing for a while."""
        previous = self._consumer_commands.get(subentry_id)
        measured = snapshot.consumers[subentry_id].power_w
        if (
            previous is None
            or measured is None
            or previous[0] <= 0
            or measured >= previous[0] * SATURATION_RATIO
        ):
            self._low_since.pop(subentry_id, None)
            if subentry_id in self._resting:
                _LOGGER.debug("Consumer %s draws power again", subentry_id)
                self._resting.discard(subentry_id)
            return
        since = self._low_since.setdefault(subentry_id, now)
        learner = self.consumer_response.get(subentry_id)
        response = learner.value if learner else DEFAULT_CONSUMER_RESPONSE_S
        low, high = RESTING_DELAY_RANGE_S
        if now - since > min(high, max(low, RESTING_RESPONSE_FACTOR * response)):
            if subentry_id not in self._resting:
                _LOGGER.debug("Consumer %s rests (thermostat)", subentry_id)
            self._resting.add(subentry_id)

    def _check_saturation(self, subentry_id: str, snapshot: SystemSnapshot, now: float) -> None:
        """Mark a consumer saturated if it ignores its command for a while."""
        previous = self._consumer_commands.get(subentry_id)
        measured = snapshot.consumers[subentry_id].power_w
        if previous is None or measured is None:
            return
        commanded, since = previous
        learner = self.consumer_response.get(subentry_id)
        response = learner.value if learner else DEFAULT_CONSUMER_RESPONSE_S
        low, high = SATURATION_DELAY_RANGE_S
        delay = min(high, max(low, SATURATION_RESPONSE_FACTOR * response))
        if commanded > 0 and now - since > delay and measured < commanded * SATURATION_RATIO:
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
