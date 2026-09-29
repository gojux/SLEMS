"""Daily targets of consumers (pure computations, no Home Assistant access).

A consumer may have a target per period, from one deadline to the next (also
across midnight): a runtime (time it draws power), an enabled time (time SLEMS
has it switched on, for devices with their own control such as a
dehumidifier with a hygrostat), an energy, or a minimum and a target
temperature of its storage (temperature sensors).

The target is met from the surplus first. What may cover the rest in time
(``TargetSource``): only the surplus, also the batteries (as long as they can
deliver), also the batteries and the grid. From the latest start
(deadline − remaining time × ``TIME_FACTOR`` − ``START_MARGIN``) on the
consumer runs regardless of the surplus (*forced*).

The planning (day chart, SoC projection, night discharge) counts the forced
run as an extra load from the latest start on (``forced_load``): the worst
case, as if no surplus covered any of the rest; it shrinks as the surplus
does.

*Boost*: the consumer gets the surplus before the batteries. For runtime,
enabled time and energy only with its priority option and when the forecast
surplus until the deadline is short for the rest of the target plus filling
the batteries. Below the minimum temperature always. From the target
temperature on the consumer is off until the next period (*done*).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from .const import TargetSource, TargetType

TIME_FACTOR = 1.2
START_MARGIN = timedelta(minutes=10)
# Latest start of a storage below its minimum temperature while its energy
# per kelvin is not learned yet.
TEMPERATURE_FALLBACK = timedelta(hours=2)
# Samples further apart are not integrated (missed polls, restart).
MAX_GAP_S = 120.0
# Draws power: at least this much (as the consumer learner).
RUNNING_W = 50.0


class TargetMode(StrEnum):
    NONE = "none"
    DONE = "done"
    SURPLUS = "surplus"
    BOOST = "boost"
    FORCED = "forced"


@dataclass
class TargetSettings:
    """Set on the consumer card (restored entities)."""

    type: TargetType = TargetType.NONE
    hours: float = 2.0
    energy_kwh: float = 2.0
    min_temp_c: float = 40.0
    target_temp_c: float = 55.0
    deadline: time = time(22, 0)
    source: TargetSource = TargetSource.SURPLUS
    priority: bool = False


def period_end(now: datetime, deadline: time) -> datetime:
    """End of the period ``now`` (local, aware) falls in: the next deadline."""
    end = now.replace(hour=deadline.hour, minute=deadline.minute, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


@dataclass
class TargetProgress:
    """What the consumer got in the current period; stored."""

    end: datetime | None = None
    runtime_s: float = 0.0
    enabled_s: float = 0.0
    energy_wh: float = 0.0
    # Temperature target: reached the minimum / the target in this period,
    # and the target temperature it was reached with (a higher one set later
    # in the period is not reached yet).
    min_reached: bool = False
    done: bool = False
    done_target_c: float | None = None
    # "met" / "missed" of the last period.
    last_result: str | None = None
    # The previous sample: its state applies until this one.
    _last: tuple[float, float | None, bool] | None = field(default=None, repr=False)

    def update(
        self,
        now: float,
        local_now: datetime,
        settings: TargetSettings,
        power_w: float | None,
        commanded_on: bool,
    ) -> str | None:
        """Count one poll; returns the result when a period ended."""
        result = None
        end = period_end(local_now, settings.deadline)
        if self.end is None:
            self.end = end
        elif local_now >= self.end:
            result = "met" if self.met(settings) else "missed"
            self.last_result = result
            self._reset(end)
        elif end != self.end:
            # The deadline was changed: the counters stay.
            self.end = end
        previous, self._last = self._last, (now, power_w, commanded_on)
        if previous is None:
            return result
        last_time, last_power, last_on = previous
        elapsed = now - last_time
        if elapsed > MAX_GAP_S or elapsed <= 0:
            return result
        if last_power is not None and last_power >= RUNNING_W:
            self.runtime_s += elapsed
        if last_on:
            self.enabled_s += elapsed
        if last_power is not None:
            self.energy_wh += max(0.0, last_power) * elapsed / 3600
        return result

    def _reset(self, end: datetime) -> None:
        self.end = end
        self.runtime_s = self.enabled_s = self.energy_wh = 0.0
        self.min_reached = self.done = False
        self.done_target_c = None

    def mark_done(self, target_c: float) -> None:
        """The target temperature ``target_c`` was reached in this period."""
        self.done = True
        self.done_target_c = max(target_c, self.done_target_c or target_c)

    def done_for(self, settings: TargetSettings) -> bool:
        """Reached the target temperature that is set now."""
        return self.done and (
            self.done_target_c is None or settings.target_temp_c <= self.done_target_c
        )

    def met(self, settings: TargetSettings) -> bool:
        if settings.type is TargetType.TEMPERATURE:
            return self.done_for(settings) or self.min_reached
        return remaining(settings, self) <= 0

    def as_dict(self) -> dict:
        return {
            "end": self.end.isoformat() if self.end else None,
            "runtime_s": self.runtime_s,
            "enabled_s": self.enabled_s,
            "energy_wh": self.energy_wh,
            "min_reached": self.min_reached,
            "done": self.done,
            "done_target_c": self.done_target_c,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> TargetProgress:
        data = data or {}
        end = data.get("end")
        return cls(
            end=datetime.fromisoformat(end) if end else None,
            runtime_s=data.get("runtime_s", 0.0),
            enabled_s=data.get("enabled_s", 0.0),
            energy_wh=data.get("energy_wh", 0.0),
            min_reached=data.get("min_reached", False),
            done=data.get("done", False),
            done_target_c=data.get("done_target_c"),
            last_result=data.get("last_result"),
        )


def remaining(settings: TargetSettings, progress: TargetProgress) -> float:
    """What is still missing: seconds (runtime, enabled time) or Wh (energy)."""
    if settings.type is TargetType.RUNTIME:
        return settings.hours * 3600 - progress.runtime_s
    if settings.type is TargetType.ENABLED:
        return settings.hours * 3600 - progress.enabled_s
    if settings.type is TargetType.ENERGY:
        return settings.energy_kwh * 1000 - progress.energy_wh
    return 0.0


@dataclass(frozen=True)
class TargetState:
    mode: TargetMode
    # Seconds, Wh or K still missing (0 when met or without a target).
    missing: float = 0.0
    latest_start: datetime | None = None
    end: datetime | None = None


def evaluate(
    settings: TargetSettings,
    progress: TargetProgress,
    local_now: datetime,
    *,
    power_w: float,
    temperature_c: float | None,
    wh_per_k: float | None,
    expected_surplus_wh: float,
    battery_need_wh: float,
    battery_can_supply: bool,
) -> TargetState:
    """Mode of the consumer right now.

    ``power_w`` is its power while on, ``expected_surplus_wh`` the forecast
    surplus until the deadline, ``battery_need_wh`` the energy that fills the
    batteries, ``battery_can_supply`` whether the batteries can deliver its
    power now.
    """
    if settings.type is TargetType.NONE:
        return TargetState(TargetMode.NONE)
    end = progress.end or period_end(local_now, settings.deadline)
    may_force = settings.source is TargetSource.GRID or (
        settings.source is TargetSource.BATTERY and battery_can_supply
    )

    if settings.type is TargetType.TEMPERATURE:
        if temperature_c is None:
            return TargetState(TargetMode.SURPLUS, end=end)
        if progress.done_for(settings) or temperature_c >= settings.target_temp_c:
            return TargetState(TargetMode.DONE, end=end)
        if temperature_c >= settings.min_temp_c:
            return TargetState(TargetMode.SURPLUS, end=end)
        missing_k = settings.min_temp_c - temperature_c
        if wh_per_k and power_w > 0:
            duration = timedelta(hours=missing_k * wh_per_k / power_w * TIME_FACTOR)
        else:
            duration = TEMPERATURE_FALLBACK
        latest = end - duration - START_MARGIN
        mode = TargetMode.FORCED if may_force and local_now >= latest else TargetMode.BOOST
        return TargetState(mode, missing_k, latest, end)

    missing = remaining(settings, progress)
    if missing <= 0:
        return TargetState(TargetMode.DONE, end=end)
    if settings.type is TargetType.ENERGY:
        energy = missing
        seconds = missing / power_w * 3600 if power_w > 0 else None
    else:
        seconds = missing
        energy = missing / 3600 * power_w
    latest = (
        end - timedelta(seconds=seconds * TIME_FACTOR) - START_MARGIN if seconds is not None else None
    )
    if may_force and latest is not None and local_now >= latest:
        return TargetState(TargetMode.FORCED, missing, latest, end)
    if settings.priority and expected_surplus_wh < energy + battery_need_wh:
        return TargetState(TargetMode.BOOST, missing, latest, end)
    return TargetState(TargetMode.SURPLUS, missing, latest, end)


def forced_load(
    settings: TargetSettings,
    state: TargetState,
    local_now: datetime,
    *,
    power_w: float,
    wh_per_k: float | None,
) -> dict[datetime, float]:
    """Energy (Wh) per local hour of the forced run the target may still need.

    From the latest start (or now) at ``power_w`` until the rest is covered or
    the deadline; nothing with the source "surplus only" or once met.
    """
    if (
        settings.source is TargetSource.SURPLUS
        or state.mode in (TargetMode.NONE, TargetMode.DONE)
        or state.latest_start is None
        or state.end is None
        or power_w <= 0
    ):
        return {}
    if settings.type is TargetType.TEMPERATURE:
        energy = (
            state.missing * wh_per_k
            if wh_per_k
            else power_w * TEMPERATURE_FALLBACK / timedelta(hours=1)
        ) if state.missing > 0 else 0.0
    elif settings.type is TargetType.ENERGY:
        energy = state.missing
    else:
        energy = state.missing / 3600 * power_w
    result: dict[datetime, float] = {}
    moment = max(state.latest_start, local_now)
    while energy > 0 and moment < state.end:
        hour = moment.replace(minute=0, second=0, microsecond=0)
        until = min(hour + timedelta(hours=1), state.end)
        wh = min(energy, power_w * (until - moment) / timedelta(hours=1))
        result[hour] = result.get(hour, 0.0) + wh
        energy -= wh
        moment = until
    return result
