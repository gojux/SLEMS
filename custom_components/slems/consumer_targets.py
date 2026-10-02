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

An earliest start (runtime, enabled time, energy; optional) restricts the
period to the time from the last earliest start before the deadline: before
it SLEMS keeps the consumer off, also with surplus (*waiting*). A temperature
target always starts at midnight: after its deadline the consumer waits for
the next day.

The planning (day chart, SoC projection, night discharge) counts the forced
run as an extra load from the latest start on (``forced_load``): the worst
case, as if no surplus covered any of the rest; it shrinks as the surplus
does.

*Boost*: the consumer gets the surplus before the batteries. For runtime,
enabled time and energy only with its priority option and when the forecast
surplus until the deadline is short for the rest of the target plus filling
the batteries. Below the minimum temperature always. From the target
temperature on the consumer is off for the rest of the day (*done*), unless
the temperature falls below the minimum again: then the target is open again.
The target counts for the sensor chosen when it was reached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from .const import TargetSensor, TargetSource, TargetType

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
    WAITING = "waiting"
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
    # Temperature target with two sensors: the mean or one of them.
    sensor: TargetSensor = TargetSensor.MEAN
    # Not switched on before this time of the period (runtime, enabled time, energy).
    earliest_enabled: bool = False
    earliest: time = time(8, 0)


def window_start(settings: TargetSettings, end: datetime) -> datetime | None:
    """Earliest start before the deadline ``end``, None without one.

    A temperature target applies to the calendar day: from midnight to the
    deadline, off after the deadline until midnight.
    """
    if settings.type is TargetType.TEMPERATURE:
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        return start if start < end else start - timedelta(days=1)
    if not settings.earliest_enabled:
        return None
    start = end.replace(hour=settings.earliest.hour, minute=settings.earliest.minute)
    return start if start < end else start - timedelta(days=1)


def target_temperature(
    settings: TargetSettings, mean_c: float | None, temperatures_c: tuple[float | None, ...]
) -> float | None:
    """Temperature the temperature target applies to."""
    if settings.sensor is TargetSensor.FIRST and len(temperatures_c) >= 1:
        return temperatures_c[0]
    if settings.sensor is TargetSensor.SECOND and len(temperatures_c) >= 2:
        return temperatures_c[1]
    return mean_c


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
    # Sensor choice (``TargetSensor`` value) the temperature flags apply to.
    temperature_sensor: str | None = None
    # "met" / "missed" of the last period.
    last_result: str | None = None
    # SLEMS could not control the consumer at some time of the period
    # (operating mode not active, its control switched off).
    uncontrolled: bool = False
    # The same for the last period: a missed target is only notified if SLEMS
    # controlled the consumer all the time.
    last_controlled: bool = True
    # Time the consumer was blocked externally in the period, and in the last
    # one (the notification of a missed target names it).
    blocked_s: float = 0.0
    last_blocked_s: float = 0.0
    # The consumer declined power in the period although commanded (saturated
    # or resting: its own thermostat is satisfied); a missed target is then
    # not notified, the device did not need more.
    declined: bool = False
    last_declined: bool = False
    # The previous sample: its state applies until this one.
    _last: tuple[float, float | None, bool, bool] | None = field(default=None, repr=False)

    def update(
        self,
        now: float,
        local_now: datetime,
        settings: TargetSettings,
        power_w: float | None,
        commanded_on: bool,
        controlled: bool = True,
        blocked: bool = False,
        declined: bool = False,
    ) -> str | None:
        """Count one poll; returns the result when a period ended.

        ``controlled``: SLEMS may control the consumer right now (operating
        mode, its control switch), ``blocked``: it is blocked externally,
        ``declined``: it is saturated or resting.
        """
        result = None
        end = period_end(local_now, settings.deadline)
        if self.end is None:
            self.end = end
        elif local_now >= self.end:
            result = "met" if self.met(settings) else "missed"
            self.last_result = result
            self.last_controlled = not self.uncontrolled
            self.last_blocked_s = self.blocked_s
            self.last_declined = self.declined
            self._reset(end)
        if not controlled:
            self.uncontrolled = True
        if declined:
            self.declined = True
        elif end != self.end:
            # The deadline was changed: the counters stay.
            self.end = end
        previous, self._last = self._last, (now, power_w, commanded_on, blocked)
        if previous is None:
            return result
        last_time, last_power, last_on, last_blocked = previous
        elapsed = now - last_time
        if elapsed > MAX_GAP_S or elapsed <= 0:
            return result
        if last_power is not None and last_power >= RUNNING_W:
            self.runtime_s += elapsed
        if last_on:
            self.enabled_s += elapsed
        if last_blocked:
            self.blocked_s += elapsed
        if last_power is not None:
            self.energy_wh += max(0.0, last_power) * elapsed / 3600
        return result

    def _reset(self, end: datetime) -> None:
        self.end = end
        self.uncontrolled = self.declined = False
        self.runtime_s = self.enabled_s = self.energy_wh = self.blocked_s = 0.0
        self.min_reached = self.done = False
        self.done_target_c = None

    def use_sensor(self, sensor: TargetSensor) -> None:
        """Temperature flags of another sensor do not count: a new choice starts open."""
        if self.temperature_sensor is not None and self.temperature_sensor != sensor.value:
            self.min_reached = self.done = False
            self.done_target_c = None
        self.temperature_sensor = sensor.value

    def track_temperature(self, settings: TargetSettings, temperature_c: float) -> None:
        """Note a temperature of the period: minimum and target reached.

        Below the minimum again (e.g. after drawing hot water) the target is
        open again, so the surplus fills the storage up to it once more.
        """
        if temperature_c < settings.min_temp_c:
            self.done = False
            self.done_target_c = None
            return
        self.min_reached = True
        if temperature_c >= settings.target_temp_c:
            self.mark_done(settings.target_temp_c)

    def mark_done(self, target_c: float) -> None:
        """The target temperature ``target_c`` was reached in this period."""
        self.done = True
        self.done_target_c = max(target_c, self.done_target_c or target_c)

    def done_for(self, settings: TargetSettings) -> bool:
        """Reached the target temperature that is set now, with the sensor chosen now."""
        return (
            self.done
            and (self.done_target_c is None or settings.target_temp_c <= self.done_target_c)
            and self.temperature_sensor in (None, settings.sensor.value)
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
            "temperature_sensor": self.temperature_sensor,
            "last_result": self.last_result,
            "uncontrolled": self.uncontrolled,
            "last_controlled": self.last_controlled,
            "blocked_s": self.blocked_s,
            "last_blocked_s": self.last_blocked_s,
            "declined": self.declined,
            "last_declined": self.last_declined,
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
            temperature_sensor=data.get("temperature_sensor"),
            last_result=data.get("last_result"),
            uncontrolled=data.get("uncontrolled", False),
            last_controlled=data.get("last_controlled", True),
            blocked_s=data.get("blocked_s", 0.0),
            last_blocked_s=data.get("last_blocked_s", 0.0),
            declined=data.get("declined", False),
            last_declined=data.get("last_declined", False),
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
    # The latest start was moved to a cheaper window (see price_hold).
    price_window: bool = False


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
        earliest = window_start(settings, end)
        if earliest is not None and local_now < earliest:
            return TargetState(TargetMode.WAITING, end=end)
        if temperature_c is None:
            return TargetState(TargetMode.SURPLUS, end=end)
        # Below the minimum it heats in any case; a reached target is open
        # again then (see TargetProgress.track_temperature).
        if temperature_c >= settings.min_temp_c:
            if progress.done_for(settings) or temperature_c >= settings.target_temp_c:
                return TargetState(TargetMode.DONE, end=end)
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
    earliest = window_start(settings, end)
    if settings.type is TargetType.ENERGY:
        energy = missing
        seconds = missing / power_w * 3600 if power_w > 0 else None
    else:
        seconds = missing
        energy = missing / 3600 * power_w
    latest = (
        end - timedelta(seconds=seconds * TIME_FACTOR) - START_MARGIN if seconds is not None else None
    )
    if earliest is not None and latest is not None:
        latest = max(latest, earliest)
    if earliest is not None and local_now < earliest:
        return TargetState(TargetMode.WAITING, missing, latest, end)
    if may_force and latest is not None and local_now >= latest:
        return TargetState(TargetMode.FORCED, missing, latest, end)
    if settings.priority and expected_surplus_wh < energy + battery_need_wh:
        return TargetState(TargetMode.BOOST, missing, latest, end)
    return TargetState(TargetMode.SURPLUS, missing, latest, end)


def energy_to_target(
    settings: TargetSettings,
    progress: TargetProgress,
    *,
    power_w: float,
    temperature_c: float | None,
    wh_per_k: float | None,
) -> float | None:
    """Energy (Wh) the consumer still needs for its target, None when not known.

    Exact for an energy target; for a runtime or enabled time the remaining
    time at ``power_w`` (less if its own thermostat stops it earlier); for a
    temperature target up to the target temperature with the learned energy
    per kelvin (heat losses left out).
    """
    if settings.type is TargetType.NONE:
        return None
    if settings.type is TargetType.TEMPERATURE:
        # Reached and not below the minimum again (see track_temperature).
        if temperature_c is not None and (
            temperature_c >= settings.target_temp_c
            or (progress.done_for(settings) and temperature_c >= settings.min_temp_c)
        ):
            return 0.0
        if temperature_c is None or not wh_per_k:
            return None
        return (settings.target_temp_c - temperature_c) * wh_per_k
    missing = max(0.0, remaining(settings, progress))
    if settings.type is TargetType.ENERGY:
        return missing
    return missing / 3600 * power_w if power_w > 0 else None


@dataclass(frozen=True)
class SurplusDemand:
    """Energy a daily target takes from the surplus between ``start`` and ``end``."""

    energy_wh: float
    power_w: float
    start: datetime
    end: datetime


def surplus_demand(
    settings: TargetSettings,
    state: TargetState,
    local_now: datetime,
    *,
    energy_wh: float | None,
    forced_wh: float,
    power_w: float,
) -> SurplusDemand | None:
    """Part of the target the planning expects from the surplus.

    What the forced run (``forced_wh``, see ``forced_load``) does not cover,
    from now (or the earliest start) until the deadline at most at ``power_w``.
    """
    if (
        energy_wh is None
        or state.mode in (TargetMode.NONE, TargetMode.DONE)
        or state.end is None
        or power_w <= 0
    ):
        return None
    rest = energy_wh - forced_wh
    earliest = window_start(settings, state.end)
    start = max(local_now, earliest) if earliest is not None else local_now
    if rest <= 0 or start >= state.end:
        return None
    return SurplusDemand(rest, power_w, start, state.end)


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


# A consumer with a daily target that SLEMS switched on for at least
# NO_POWER_COMMANDED_S a day (or the target's own duration, if shorter) but
# that drew no power for NO_POWER_DAYS days is probably switched off or broken.
NO_POWER_DAYS = 3
NO_POWER_COMMANDED_S = 1800.0


@dataclass
class NoPowerWatch:
    """Days in a row a consumer drew no power although SLEMS switched it on; stored."""

    day: date | None = None
    commanded_s: float = 0.0
    drew: bool = False
    days: int = 0
    since: date | None = None
    _last: tuple[float, bool] | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.days >= NO_POWER_DAYS

    def update(
        self, now: float, local_now: datetime, commanded_on: bool, power_w: float | None, min_commanded_s: float
    ) -> None:
        """One poll. A day counts if it was switched on for ``min_commanded_s``."""
        day = local_now.date()
        if self.day is None:
            self.day = day
        elif day != self.day:
            if not self.drew and self.commanded_s >= min_commanded_s:
                self.days += 1
                self.since = self.since or self.day
            self.day, self.commanded_s, self.drew = day, 0.0, False
        previous, self._last = self._last, (now, commanded_on)
        if previous is not None and previous[1] and 0 < now - previous[0] <= MAX_GAP_S:
            self.commanded_s += now - previous[0]
        if power_w is not None and power_w >= RUNNING_W:
            self.drew = True
            self.days, self.since = 0, None

    def reset(self) -> None:
        self.commanded_s, self.drew, self.days, self.since = 0.0, False, 0, None

    def as_dict(self) -> dict:
        return {
            "day": self.day.isoformat() if self.day else None,
            "commanded_s": self.commanded_s,
            "drew": self.drew,
            "days": self.days,
            "since": self.since.isoformat() if self.since else None,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> NoPowerWatch:
        data = data or {}
        try:
            return cls(
                day=date.fromisoformat(data["day"]) if data.get("day") else None,
                commanded_s=float(data.get("commanded_s", 0.0)),
                drew=bool(data.get("drew", False)),
                days=int(data.get("days", 0)),
                since=date.fromisoformat(data["since"]) if data.get("since") else None,
            )
        except (TypeError, ValueError):
            return cls()


def target_duration_s(settings: TargetSettings, power_w: float) -> float | None:
    """Time the target needs: runtime / enabled time, or the energy at full power."""
    if settings.type in (TargetType.RUNTIME, TargetType.ENABLED):
        return settings.hours * 3600
    if settings.type is TargetType.ENERGY and power_w > 0:
        return settings.energy_kwh * 1000 / power_w * 3600
    return None


def target_window_s(settings: TargetSettings, end: datetime) -> float:
    """Length of the period the target may use: from the earliest start (or the
    previous deadline) to the deadline ``end``."""
    start = window_start(settings, end)
    return (end - start).total_seconds() if start is not None else 86400.0


def target_fits(settings: TargetSettings, end: datetime, power_w: float) -> bool:
    """Whether the target fits into its window (runtime, enabled time, energy)."""
    duration = target_duration_s(settings, power_w)
    return duration is None or duration <= target_window_s(settings, end)


def no_power_threshold_s(settings: TargetSettings, power_w: float, end: datetime | None = None) -> float:
    """Time switched on that makes a day count: 30 minutes, or less if the target
    or its window (earliest start to deadline) is shorter."""
    limits = [NO_POWER_COMMANDED_S]
    if (duration := target_duration_s(settings, power_w)) is not None:
        limits.append(duration)
    if end is not None and settings.type is not TargetType.TEMPERATURE:
        limits.append(target_window_s(settings, end))
    return min(limits)
