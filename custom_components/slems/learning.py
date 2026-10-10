"""Values SLEMS learns from its measurements instead of fixed settings.

Every learned value has a switch; without it, or until enough data is there,
the value set by the user applies.

* Buffers against forecast errors (grid friendly charging, charge secured and
  night discharge): from the recorded PV forecasts (``PvAccuracyTracker``) and
  the backtest of the consumption forecast. The buffer is the energy by which
  the forecast was too optimistic on ``ERROR_QUANTILE`` of the days it was
  too optimistic at all: PV forecast too high, consumption forecast too low.
* Usable capacity of a battery (``CapacityLearner``): DC energy of a charge or
  discharge divided by the change of the state of charge.
* Grid surplus targets (``GridTargetLearner``): the deviation of the grid
  power towards import while the batteries control the grid. Charging: the
  target keeps the grid on the export side for ``TARGET_QUANTILE`` of the
  time (an import buys grid energy for the batteries). Discharging: the
  median, the grid swings around zero; a short import costs little there
  (the batteries keep that energy for later), a permanent export would give
  battery energy away for the feed-in credit.
* Control interval and averaging window (``auto_timing``): from the learned
  report interval of the smart meter.
* Consumers (``ConsumerLearner``): power while switched on (a power
  controlled consumer: its highest power, measured while commanded at nearly
  full power), and whether their own thermostat switches them off while they
  are commanded.
* Thermal storage of a consumer (``ThermalLearner``): energy per kelvin of its
  temperature sensors and the temperatures at which it starts cycling and is
  full, for the capacity it has left.
* Cooling of a storage (``CoolingLearner``): its temperature drop per hour of
  the day while it is not heated (heat losses and water draws), to forecast
  its temperature at the deadline of a temperature target.
* Reserve of the night discharge (``MorningGapLearner``): the energy the house
  needed from battery or grid between the moment PV should have taken over
  (end of the night discharge as planned) and the moment it really did, as a
  share of the day's forecast consumption. Days with the vacation mode are
  left out.
* Power of an on/off consumer against the outdoor temperature
  (``OutdoorPowerLearner``): the mean power and outdoor temperature of each
  run; e.g. the hot water charge of an air source heat pump draws more when it
  is cold. Used for the cost of a run at the forecast temperature (see the
  daily targets), only with a clear dependency.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import math
import statistics
import time

# --- forecast errors ------------------------------------------------------------

ERROR_QUANTILE = 0.8
PV_MIN_DAYS = 14
CONSUMPTION_MIN_DAYS = 7


def quantile(values: Iterable[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def pv_overestimate(days: Mapping[str, Mapping[str, float]]) -> tuple[float | None, int]:
    """Share by which the PV forecast was too high: (share or None, complete days).

    Only days with less production than forecast count; 0 if there were none.
    None until ``PV_MIN_DAYS`` complete days are recorded.
    """
    complete = [
        (entry["forecast_wh"], entry["actual_wh"])
        for entry in days.values()
        if entry.get("actual_wh") is not None and entry.get("forecast_wh")
    ]
    if len(complete) < PV_MIN_DAYS:
        return None, len(complete)
    over = [1 - actual / forecast for forecast, actual in complete if actual < forecast]
    return (quantile(over, ERROR_QUANTILE) if over else 0.0), len(complete)


def consumption_underestimate(
    days: Iterable[tuple[float, float]],
) -> tuple[float | None, int]:
    """Share by which the consumption forecast was too low: (share or None, days).

    ``days`` are (forecast Wh, actual Wh) of the backtest.
    """
    days = list(days)
    if len(days) < CONSUMPTION_MIN_DAYS:
        return None, len(days)
    under = [actual / forecast - 1 for forecast, actual in days if forecast > 0 and actual > forecast]
    return (quantile(under, ERROR_QUANTILE) if under else 0.0), len(days)


# --- usable capacity --------------------------------------------------------------

# A leg counts from this change of the state of charge on.
CAPACITY_MIN_SOC_CHANGE_PCT = 20.0
# Below this DC power (W) the battery rests; a longer rest ends the leg.
CAPACITY_IDLE_W = 30.0
CAPACITY_MAX_IDLE_S = 600.0
# A jump of the state of charge (BMS recalibration) discards the leg.
CAPACITY_MAX_SOC_JUMP_PCT = 3.0
# Samples further apart are not integrated (missed polls).
CAPACITY_MAX_GAP_S = 60.0
CAPACITY_KEEP = 10
CAPACITY_MIN_ESTIMATES = 3
# Estimates outside this share of the configured capacity are implausible.
CAPACITY_PLAUSIBLE = (0.5, 1.3)


@dataclass
class CapacityLearner:
    """Usable capacity from the DC energy of one-directional charge/discharge legs."""

    estimates: list[float] = field(default_factory=list)
    _direction: int = 0
    _start_soc: float | None = None
    _last_soc: float | None = None
    _energy_wh: float = 0.0
    _last_time: float | None = None
    _idle_since: float | None = None

    def update(self, now: float, soc_pct: float | None, power_w: float | None, configured_wh: float) -> None:
        if soc_pct is None or power_w is None:
            self._reset()
            return
        if self._last_soc is not None and abs(soc_pct - self._last_soc) > CAPACITY_MAX_SOC_JUMP_PCT:
            self._reset()
        direction = 1 if power_w > CAPACITY_IDLE_W else -1 if power_w < -CAPACITY_IDLE_W else 0
        if direction == 0:
            if self._idle_since is None:
                self._idle_since = now
            if now - self._idle_since > CAPACITY_MAX_IDLE_S:
                self._finish(configured_wh)
        else:
            self._idle_since = None
            if direction != self._direction:
                self._finish(configured_wh)
                self._direction = direction
                self._start_soc = soc_pct
                self._energy_wh = 0.0
        if (
            self._direction
            and self._last_time is not None
            and now - self._last_time <= CAPACITY_MAX_GAP_S
        ):
            self._energy_wh += power_w * (now - self._last_time) / 3600
        self._last_time = now
        self._last_soc = soc_pct

    def _finish(self, configured_wh: float) -> None:
        if self._direction and self._start_soc is not None and self._last_soc is not None:
            change = abs(self._last_soc - self._start_soc)
            if change >= CAPACITY_MIN_SOC_CHANGE_PCT:
                estimate = abs(self._energy_wh) / (change / 100)
                low, high = CAPACITY_PLAUSIBLE
                if low * configured_wh <= estimate <= high * configured_wh:
                    self.estimates = [*self.estimates, round(estimate)][-CAPACITY_KEEP:]
        self._direction = 0
        self._start_soc = None
        self._energy_wh = 0.0

    def _reset(self) -> None:
        self._direction = 0
        self._start_soc = None
        self._energy_wh = 0.0
        self._last_time = None
        self._last_soc = None
        self._idle_since = None

    @property
    def capacity_wh(self) -> float | None:
        """Median of the estimates; None until there are enough."""
        if len(self.estimates) < CAPACITY_MIN_ESTIMATES:
            return None
        return statistics.median(self.estimates)

    def as_dict(self) -> dict:
        return {"estimates": list(self.estimates)}

    @classmethod
    def from_dict(cls, data: dict | None) -> CapacityLearner:
        return cls(estimates=list((data or {}).get("estimates", [])))


# --- grid surplus targets ---------------------------------------------------------

TARGET_QUANTILE = 0.9
TARGET_KEEP = 3000
TARGET_MIN_SAMPLES = 300
TARGET_LIMITS_W = (20.0, 1000.0)
DISCHARGE_TARGET_QUANTILE = 0.5
DISCHARGE_TARGET_LIMITS_W = (-100.0, 300.0)


@dataclass
class GridTargetLearner:
    """Deviation of the grid power towards import while the batteries control it."""

    charge: deque = field(default_factory=lambda: deque(maxlen=TARGET_KEEP))
    discharge: deque = field(default_factory=lambda: deque(maxlen=TARGET_KEEP))

    def add(self, charging: bool, grid_w: float, target_w: float) -> None:
        """``target_w`` is the grid surplus aimed at (+export), ``grid_w`` +import."""
        deviation = grid_w + target_w
        if charging:
            self.charge.append(max(0.0, deviation))
        else:
            self.discharge.append(deviation)

    def target_w(self, charging: bool, no_import: bool = False) -> float | None:
        """``no_import``: the discharge target aims at no grid import (at least 0 W)."""
        samples = self.charge if charging else self.discharge
        if len(samples) < TARGET_MIN_SAMPLES:
            return None
        if charging:
            low, high = TARGET_LIMITS_W
            return min(high, max(low, quantile(samples, TARGET_QUANTILE)))
        low, high = DISCHARGE_TARGET_LIMITS_W
        if no_import:
            low = max(low, 0.0)
        return min(high, max(low, quantile(samples, DISCHARGE_TARGET_QUANTILE)))


# --- control timing ------------------------------------------------------------------


def auto_timing(meter_interval_s: float | None) -> tuple[float, float] | None:
    """(control interval, averaging window) in s from the smart meter interval.

    The control does not act faster than the meter reports; the window spans
    about three reports.
    """
    if meter_interval_s is None:
        return None
    interval = min(10.0, max(0.5, 0.8 * meter_interval_s))
    window = min(60.0, max(3.0, 3 * meter_interval_s))
    return round(interval, 1), round(window)


# --- consumers --------------------------------------------------------------------------

CONSUMER_ON_W = 50.0
# One power sample per this many seconds of running: the kept samples span an
# hour, so a start phase (a heat pump ramping up) weighs only its share.
CONSUMER_SAMPLE_S = 30.0
CONSUMER_KEEP = 120
CONSUMER_MIN_SAMPLES = 30
# A pause while commanded on: below this share of the learned power ...
PAUSE_SHARE = 0.1
# ... for at least this long, then running again without a new command.
PAUSE_MIN_S = 30.0
# Longer pauses are no thermostat cycling: a device with its own long control
# (a dehumidifier reaching its target humidity) is better treated as saturated.
PAUSE_MAX_S = 600.0
# Right after switching on, a device may take this long to start (compressor):
# no pause before it ran once, unless nothing happens for longer.
START_DELAY_MAX_S = 300.0
CYCLES_FOR_THERMOSTAT = 2
# Pauses count this long (s, wall clock): a replaced device or thermostat is relearned.
PAUSE_MEMORY_S = 30 * 86400


@dataclass
class ConsumerLearner:
    """Power while on and pauses of the own thermostat of one consumer.

    The power is learned only while the consumer is commanded at (nearly) its
    full power: for a power controlled consumer the median of throttled set
    points would say nothing, at full command it is the power it really takes.
    """

    powers: deque = field(default_factory=lambda: deque(maxlen=CONSUMER_KEEP))
    # Wall clock times (epoch s) of the thermostat pauses.
    pauses: deque = field(default_factory=lambda: deque(maxlen=CONSUMER_KEEP))
    _paused_since: float | None = None
    # Ran since it was switched on: before, no power is its start delay.
    _ran: bool = False
    _last_sample: float | None = None

    def update(
        self,
        now: float,
        commanded_on: bool,
        power_w: float | None,
        full_command: bool = True,
        wall_now: float | None = None,
    ) -> None:
        if power_w is None or not commanded_on:
            self._paused_since = None
            self._ran = False
            return
        if power_w >= CONSUMER_ON_W:
            if self._paused_since is not None and PAUSE_MIN_S <= now - self._paused_since <= PAUSE_MAX_S:
                self.pauses.append(time.time() if wall_now is None else wall_now)
            self._paused_since = None
            self._ran = True
            if full_command and (self._last_sample is None or now - self._last_sample >= CONSUMER_SAMPLE_S):
                self.powers.append(power_w)
                self._last_sample = now
            return
        nominal = self.nominal_w
        if (
            self._ran
            and nominal is not None
            and power_w < PAUSE_SHARE * nominal
            and self._paused_since is None
        ):
            self._paused_since = now

    @property
    def nominal_w(self) -> float | None:
        if len(self.powers) < CONSUMER_MIN_SAMPLES:
            return None
        return round(statistics.median(self.powers))

    def cycles_at(self, wall_now: float) -> int:
        """Thermostat pauses within ``PAUSE_MEMORY_S`` before ``wall_now``."""
        return sum(1 for moment in self.pauses if moment >= wall_now - PAUSE_MEMORY_S)

    @property
    def cycles(self) -> int:
        return self.cycles_at(time.time())

    @property
    def thermostat_cycles(self) -> bool:
        return self.cycles >= CYCLES_FOR_THERMOSTAT

    def as_dict(self) -> dict:
        return {"powers": list(self.powers), "pauses": list(self.pauses), "full_command": True}

    @classmethod
    def from_dict(cls, data: dict | None, power_controlled: bool = False) -> ConsumerLearner:
        """``power_controlled``: stored powers without the marker ``full_command``
        may hold throttled set points and are dropped."""
        data = data or {}
        learner = cls()
        learner.pauses.extend(data.get("pauses", []))
        # Earlier versions stored a count: counted as pauses of today.
        learner.pauses.extend([time.time()] * min(int(data.get("cycles", 0)), CONSUMER_KEEP))
        if not power_controlled or data.get("full_command"):
            learner.powers.extend(data.get("powers", []))
        return learner


# --- thermal storage of a consumer -----------------------------------------------------

# A heating run counts for the energy per kelvin from this rise and energy on.
THERMAL_MIN_RISE_K = 3.0
THERMAL_MIN_ENERGY_WH = 300.0
# While cycling, a window with less than this share of the learned power
# means the storage is full.
THERMAL_WINDOW_S = 30 * 60
THERMAL_FULL_SHARE = 0.15
# Cycling phases shorter than this give no mean power.
THERMAL_MIN_CYCLING_S = 30 * 60
THERMAL_KEEP = 20
THERMAL_MIN_RUNS = 3
THERMAL_MIN_MARKS = 2
# Samples further apart are not integrated (missed polls, restart).
THERMAL_MAX_GAP_S = 120.0


@dataclass
class ThermalLearner:
    """Storage of a consumer heating water (or a room) from its temperature sensors.

    Uses the mean of the configured sensors, so it needs to know neither
    which sensor switches the thermostat nor where they sit. Learned from
    the runs while the consumer is commanded on:

    * energy per kelvin of the mean temperature (``wh_per_k``), and of every
      single sensor (``sensor_wh_per_k``, for a target of one sensor: a
      sensor at the heating element rises much faster than one higher up),
    * mean temperature at which the own thermostat first pauses the consumer
      (``pause_temp``), i.e. from which on it cycles,
    * mean power while cycling (``cycling_w``),
    * mean temperature at which it hardly takes anything any more
      (``full_temp``).
    """

    wh_per_k: list[float] = field(default_factory=list)
    pause_temps: list[float] = field(default_factory=list)
    cycling_powers: list[float] = field(default_factory=list)
    full_temps: list[float] = field(default_factory=list)
    # Per sensor (configuration order): energy per kelvin of that sensor.
    sensor_wh_per_k: list[list[float]] = field(default_factory=list)
    _start_temp: float | None = None
    _start_temps: tuple[float | None, ...] = ()
    _energy_wh: float = 0.0
    _last: float | None = None
    _paused_since: float | None = None
    _cycling_since: float | None = None
    _cycling_wh: float = 0.0
    _window_start: float | None = None
    _window_wh: float = 0.0
    _full: bool = False
    _throttled: bool = False
    _ran: bool = False

    def update(
        self,
        now: float,
        commanded_on: bool,
        power_w: float | None,
        temperature: float | None,
        nominal_w: float | None,
        full_command: bool = True,
        temperatures: tuple[float | None, ...] = (),
    ) -> None:
        """``temperature`` is the mean of the sensors, ``temperatures`` each of
        them, ``nominal_w`` the power while on.

        ``full_command``: commanded at (nearly) its full power; a lower set
        point makes the cycling power and the full detection meaningless, so
        such a cycling phase is not used.
        """
        if not commanded_on or power_w is None or temperature is None:
            self._finish(temperature, temperatures)
            return
        if not full_command:
            self._throttled = True
        if self._start_temp is None:
            self._start_temp = temperature
            self._start_temps = temperatures
            self._last = now
            return
        elapsed = now - (self._last or now)
        self._last = now
        if elapsed > THERMAL_MAX_GAP_S:
            self._finish(None)
            return
        energy = max(0.0, power_w) * elapsed / 3600
        self._energy_wh += energy
        running = power_w >= CONSUMER_ON_W
        if running:
            if self._ran and self._paused_since is not None and now - self._paused_since >= PAUSE_MIN_S:
                self._start_cycling(self._paused_since, temperature)
            self._paused_since = None
            self._ran = True
        elif self._paused_since is None:
            self._paused_since = now
        # Before it ran, no power is its start delay; only a much longer one
        # means the storage is already warm.
        wait = PAUSE_MIN_S if self._ran else START_DELAY_MAX_S
        if (
            self._cycling_since is None
            and not running
            and self._paused_since is not None
            and now - self._paused_since >= wait
        ):
            self._start_cycling(self._paused_since, temperature)
        if self._cycling_since is not None:
            self._cycling_wh += energy
            self._window_wh += energy
            if now - self._window_start >= THERMAL_WINDOW_S:
                mean = self._window_wh / ((now - self._window_start) / 3600)
                if (
                    nominal_w
                    and not self._full
                    and not self._throttled
                    and mean < THERMAL_FULL_SHARE * nominal_w
                ):
                    self._full = True
                    self.full_temps = [*self.full_temps, round(temperature, 1)][-THERMAL_KEEP:]
                self._window_start = now
                self._window_wh = 0.0

    def _start_cycling(self, since: float, temperature: float) -> None:
        if self._cycling_since is not None:
            return
        self._cycling_since = since
        self._window_start = since
        self.pause_temps = [*self.pause_temps, round(temperature, 1)][-THERMAL_KEEP:]

    def _finish(
        self, temperature: float | None, temperatures: tuple[float | None, ...] = ()
    ) -> None:
        """End of a run: energy per kelvin and mean cycling power."""
        if self._start_temp is not None and temperature is not None:
            rise = temperature - self._start_temp
            if rise >= THERMAL_MIN_RISE_K and self._energy_wh >= THERMAL_MIN_ENERGY_WH:
                self.wh_per_k = [*self.wh_per_k, round(self._energy_wh / rise, 1)][-THERMAL_KEEP:]
        if self._energy_wh >= THERMAL_MIN_ENERGY_WH:
            for index, (start, end) in enumerate(zip(self._start_temps, temperatures, strict=False)):
                if start is None or end is None or end - start < THERMAL_MIN_RISE_K:
                    continue
                while len(self.sensor_wh_per_k) <= index:
                    self.sensor_wh_per_k.append([])
                values = self.sensor_wh_per_k[index]
                self.sensor_wh_per_k[index] = [*values, round(self._energy_wh / (end - start), 1)][-THERMAL_KEEP:]
        if (
            self._cycling_since is not None
            and self._last is not None
            and not self._full
            and not self._throttled
        ):
            duration = self._last - self._cycling_since
            if duration >= THERMAL_MIN_CYCLING_S:
                mean = self._cycling_wh / (duration / 3600)
                self.cycling_powers = [*self.cycling_powers, round(mean)][-THERMAL_KEEP:]
        self._start_temp = None
        self._start_temps = ()
        self._energy_wh = 0.0
        self._last = None
        self._paused_since = None
        self._cycling_since = None
        self._cycling_wh = 0.0
        self._window_start = None
        self._window_wh = 0.0
        self._full = False
        self._throttled = False
        self._ran = False

    @property
    def energy_per_k(self) -> float | None:
        return statistics.median(self.wh_per_k) if len(self.wh_per_k) >= THERMAL_MIN_RUNS else None

    def sensor_energy_per_k(self, index: int) -> float | None:
        """Energy per kelvin of one sensor (0: the first); None until learned."""
        if index >= len(self.sensor_wh_per_k) or len(self.sensor_wh_per_k[index]) < THERMAL_MIN_RUNS:
            return None
        return statistics.median(self.sensor_wh_per_k[index])

    @property
    def pause_temp(self) -> float | None:
        return statistics.median(self.pause_temps) if len(self.pause_temps) >= THERMAL_MIN_MARKS else None

    @property
    def full_temp(self) -> float | None:
        return statistics.median(self.full_temps) if len(self.full_temps) >= THERMAL_MIN_MARKS else None

    @property
    def cycling_w(self) -> float | None:
        return statistics.median(self.cycling_powers) if self.cycling_powers else None

    def capacity(self, temperature: float | None) -> tuple[float, float] | None:
        """(energy until it cycles, energy until it is full) in Wh at ``temperature``.

        Until the full temperature is learned, only the energy until it cycles
        counts. None while the energy per kelvin or the pause temperature is
        not learned yet.
        """
        per_k, pause = self.energy_per_k, self.pause_temp
        if per_k is None or pause is None or temperature is None:
            return None
        until_pause = max(0.0, (pause - temperature) * per_k)
        full = self.full_temp
        until_full = until_pause if full is None else max(until_pause, (full - temperature) * per_k)
        return until_pause, until_full

    def as_dict(self) -> dict:
        return {
            "wh_per_k": list(self.wh_per_k),
            "pause_temps": list(self.pause_temps),
            "cycling_powers": list(self.cycling_powers),
            "full_temps": list(self.full_temps),
            "sensor_wh_per_k": [list(values) for values in self.sensor_wh_per_k],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ThermalLearner:
        data = data or {}
        return cls(
            wh_per_k=list(data.get("wh_per_k", [])),
            pause_temps=list(data.get("pause_temps", [])),
            sensor_wh_per_k=[list(values) for values in data.get("sensor_wh_per_k", [])],
            cycling_powers=list(data.get("cycling_powers", [])),
            full_temps=list(data.get("full_temps", [])),
        )


# --- cooling of a storage ------------------------------------------------------------

# Drops kept per hour of the day (about two weeks), and needed for its own value.
COOLING_KEEP = 14
COOLING_MIN_HOUR = 3
# Hours with a value of their own needed before the forecast is used at all;
# other hours take the mean of the known ones.
COOLING_MIN_HOURS = 12
# Samples further apart make the hour unusable (missed polls, restart).
COOLING_MAX_GAP_S = 120.0


@dataclass
class CoolingLearner:
    """Temperature drop of a storage per local hour of the day while it is not
    heated: heat losses and the usual water draws (K per hour, positive when it
    cools). An hour counts only if the storage was not heated in it and its
    temperature was read throughout; the drop is its first temperature minus
    its last one.
    """

    drops: dict[int, deque] = field(default_factory=dict)
    # Temperature it learns (``TargetSensor`` value of the daily target).
    sensor: str | None = None
    _hour: datetime | None = None
    _start_c: float | None = None
    _last_c: float | None = None
    _usable: bool = False
    _last: float | None = None

    def use_sensor(self, sensor: str) -> None:
        """Drops of another temperature do not apply: a new choice starts anew."""
        if self.sensor is not None and self.sensor != sensor:
            self.drops = {}
            self._usable = False
        self.sensor = sensor

    def update(self, now: float, local_now: datetime, temperature_c: float | None, heating: bool) -> None:
        hour = local_now.replace(minute=0, second=0, microsecond=0)
        if hour != self._hour:
            if (
                self._hour is not None
                and self._usable
                and self._start_c is not None
                and self._last_c is not None
                and hour - self._hour == timedelta(hours=1)
                and self._last is not None
                and now - self._last <= COOLING_MAX_GAP_S
            ):
                self.drops.setdefault(self._hour.hour, deque(maxlen=COOLING_KEEP)).append(
                    round(self._start_c - self._last_c, 2)
                )
            # The first hour is only usable from its start on.
            self._usable = self._hour is not None
            self._hour, self._start_c = hour, temperature_c
        if (
            temperature_c is None
            or heating
            or (self._last is not None and now - self._last > COOLING_MAX_GAP_S)
        ):
            self._usable = False
        self._last = now
        self._last_c = temperature_c

    def drop_k(self, hour: int) -> float | None:
        """Expected drop in the local hour ``hour`` (K), None while not learned."""
        own = {h: statistics.median(v) for h, v in self.drops.items() if len(v) >= COOLING_MIN_HOUR}
        if len(own) < COOLING_MIN_HOURS:
            return None
        return own[hour] if hour in own else statistics.fmean(own.values())

    def forecast_c(self, temperature_c: float, local_now: datetime, until: datetime) -> float | None:
        """Temperature at ``until`` without heating, None while not learned."""
        if self.drop_k(local_now.hour) is None:
            return None
        moment, value = local_now, temperature_c
        while moment < until:
            hour_end = moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            step = min(hour_end, until)
            value -= (self.drop_k(moment.hour) or 0.0) * (step - moment) / timedelta(hours=1)
            moment = step
        return value

    @property
    def hours_learned(self) -> int:
        return sum(1 for v in self.drops.values() if len(v) >= COOLING_MIN_HOUR)

    def as_dict(self) -> dict:
        return {"drops": {str(h): list(v) for h, v in self.drops.items()}, "sensor": self.sensor}

    @classmethod
    def from_dict(cls, data: dict | None) -> CoolingLearner:
        data = data or {}
        return cls(
            drops={
                int(h): deque(values, maxlen=COOLING_KEEP)
                for h, values in (data.get("drops") or {}).items()
            },
            sensor=data.get("sensor"),
        )


# --- reserve of the night discharge ---------------------------------------------------

MORNING_MIN_DAYS = 14
MORNING_KEEP_DAYS = 60
# PV covers the consumption for this long: it has taken over.
TAKEOVER_S = 15 * 60
# The morning is over at this local hour at the latest.
MORNING_END_HOUR = 12
# Samples further apart are not integrated (missed polls, restart).
MORNING_MAX_GAP_S = 120.0


@dataclass
class MorningGapLearner:
    """Morning gap per day in % of the day's forecast consumption."""

    days: dict[str, float] = field(default_factory=dict)
    # Planned takeover of the coming morning and the day's forecast consumption.
    planned: datetime | None = None
    forecast_wh: float | None = None
    _gap_wh: float = 0.0
    _covered_since: float | None = None
    _last: float | None = None

    def plan(self, takeover: datetime, forecast_wh: float | None) -> None:
        """The takeover as planned; called until it is reached, the last one counts."""
        if self.planned is None or takeover.date() != self.planned.date():
            self._gap_wh = 0.0
            self._covered_since = None
            self._last = None
        self.planned = takeover
        self.forecast_wh = forecast_wh

    def update(self, now: float, local_now: datetime, pv_w: float | None, consumption_w: float | None) -> None:
        """Measure from the planned takeover until PV covers the consumption."""
        planned = self.planned
        if planned is None or local_now < planned or pv_w is None or consumption_w is None:
            return
        key = planned.date().isoformat()
        if key in self.days:
            return
        end = planned.replace(hour=MORNING_END_HOUR, minute=0, second=0, microsecond=0)
        deficit = max(0.0, consumption_w - pv_w)
        if self._last is not None and now - self._last <= MORNING_MAX_GAP_S and deficit > 0:
            self._gap_wh += deficit * (now - self._last) / 3600
        self._last = now
        if deficit == 0:
            if self._covered_since is None:
                self._covered_since = now
            taken_over = now - self._covered_since >= TAKEOVER_S
        else:
            self._covered_since = None
            taken_over = False
        if taken_over or local_now >= end:
            if self.forecast_wh:
                self.days[key] = round(self._gap_wh / self.forecast_wh * 100, 2)
                for old in sorted(self.days)[:-MORNING_KEEP_DAYS]:
                    del self.days[old]
            self.planned = None

    def forget(self, days: Iterable[date]) -> None:
        """Drop the gaps of ``days`` (vacation: the house needs less in the morning)."""
        for day in days:
            self.days.pop(day.isoformat(), None)

    def reserve_pct(self, coverage_pct: float) -> float | None:
        """Reserve that covers ``coverage_pct`` of the mornings (above 100: the
        largest gap times the coverage); None until ``MORNING_MIN_DAYS``."""
        if len(self.days) < MORNING_MIN_DAYS:
            return None
        values = list(self.days.values())
        if coverage_pct > 100:
            return round(max(values) * coverage_pct / 100, 1)
        return round(quantile(values, coverage_pct / 100), 1)

    def as_dict(self) -> dict:
        return {
            "days": dict(self.days),
            "planned": self.planned.isoformat() if self.planned else None,
            "forecast_wh": self.forecast_wh,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> MorningGapLearner:
        data = data or {}
        planned = data.get("planned")
        return cls(
            days=dict(data.get("days", {})),
            planned=datetime.fromisoformat(planned) if planned else None,
            forecast_wh=data.get("forecast_wh"),
        )


# Runs of at least this length count for the outdoor temperature model; a run
# ends when the consumer draws nothing for longer than the gap.
OUTDOOR_RUN_MIN_S = 10 * 60
OUTDOOR_RUN_GAP_S = 120.0
OUTDOOR_KEEP_RUNS = 60
# The model is used only from this many runs over at least this span of
# outdoor temperatures, if the power changes by this share over the span.
OUTDOOR_MIN_RUNS = 6
OUTDOOR_MIN_SPAN_K = 8.0
OUTDOOR_MIN_EFFECT = 0.1


@dataclass
class OutdoorPowerLearner:
    """Mean power of a consumer's runs against the outdoor temperature.

    A straight line P = a + b × T through the runs (mean outdoor temperature,
    mean power). The source of the heat is not asked for: an air source heat
    pump shows the dependency, a ground source one or a heating rod do not,
    and then ``power_at`` stays None (no effect).
    """

    runs: list[tuple[float, float]] = field(default_factory=list)
    _start: float | None = None
    _last: float | None = None
    _last_on: float | None = None
    _seconds: float = 0.0
    _energy: float = 0.0
    _temperature: float = 0.0

    def update(self, now: float, power_w: float | None, outdoor_c: float | None) -> bool:
        """One poll: ``power_w`` of the consumer, ``outdoor_c`` the outdoor
        temperature. True when a run was recorded."""
        running = power_w is not None and power_w >= CONSUMER_ON_W and outdoor_c is not None
        if running:
            if self._start is None:
                self._start = now
            elif self._last is not None and 0 < now - self._last <= OUTDOOR_RUN_GAP_S:
                elapsed = now - self._last
                self._seconds += elapsed
                self._energy += power_w * elapsed
                self._temperature += outdoor_c * elapsed
            self._last = self._last_on = now
        elif self._start is not None and self._last_on is not None and now - self._last_on > OUTDOOR_RUN_GAP_S:
            return self._finish()
        return False

    def _finish(self) -> bool:
        recorded = self._seconds >= OUTDOOR_RUN_MIN_S
        if recorded:
            self.runs.append(
                (round(self._temperature / self._seconds, 2), round(self._energy / self._seconds, 1))
            )
            del self.runs[:-OUTDOOR_KEEP_RUNS]
        self._start = self._last = self._last_on = None
        self._seconds = self._energy = self._temperature = 0.0
        return recorded

    def model(self) -> tuple[float, float] | None:
        """(a, b) of P = a + b × T, None without a clear dependency."""
        if len(self.runs) < OUTDOOR_MIN_RUNS:
            return None
        temperatures = [t for t, _ in self.runs]
        powers = [p for _, p in self.runs]
        span = max(temperatures) - min(temperatures)
        if span < OUTDOOR_MIN_SPAN_K:
            return None
        mean_t = statistics.fmean(temperatures)
        mean_p = statistics.fmean(powers)
        variance = sum((t - mean_t) ** 2 for t in temperatures)
        slope = sum((t - mean_t) * (p - mean_p) for t, p in self.runs) / variance
        if mean_p <= 0 or abs(slope) * span < OUTDOOR_MIN_EFFECT * mean_p:
            return None
        return mean_p - slope * mean_t, slope

    def power_at(self, outdoor_c: float | None) -> float | None:
        """Expected power at ``outdoor_c``, within the learned span; None without a model."""
        model = self.model()
        if model is None or outdoor_c is None:
            return None
        temperatures = [t for t, _ in self.runs]
        clamped = min(max(outdoor_c, min(temperatures)), max(temperatures))
        return max(0.0, model[0] + model[1] * clamped)

    def as_dict(self) -> dict:
        return {"runs": [list(run) for run in self.runs]}

    @classmethod
    def from_dict(cls, data: dict | None) -> OutdoorPowerLearner:
        runs = []
        for run in (data or {}).get("runs", []):
            try:
                runs.append((float(run[0]), float(run[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return cls(runs=runs[-OUTDOOR_KEEP_RUNS:])
