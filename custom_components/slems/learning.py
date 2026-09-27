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
  power towards import while the batteries control the grid; the target keeps
  the grid on the export side for ``TARGET_QUANTILE`` of the time.
* Control interval and averaging window (``auto_timing``): from the learned
  report interval of the smart meter.
* Consumers (``ConsumerLearner``): power while switched on, and whether their
  own thermostat switches them off while they are commanded.
* Reserve of the night discharge (``MorningGapLearner``): the energy the house
  needed from battery or grid between the moment PV should have taken over
  (end of the night discharge as planned) and the moment it really did, as a
  share of the day's forecast consumption.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import math
import statistics

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


@dataclass
class GridTargetLearner:
    """Deviation of the grid power towards import while the batteries control it."""

    charge: deque = field(default_factory=lambda: deque(maxlen=TARGET_KEEP))
    discharge: deque = field(default_factory=lambda: deque(maxlen=TARGET_KEEP))

    def add(self, charging: bool, grid_w: float, target_w: float) -> None:
        """``target_w`` is the grid surplus aimed at (+export), ``grid_w`` +import."""
        (self.charge if charging else self.discharge).append(max(0.0, grid_w + target_w))

    def target_w(self, charging: bool) -> float | None:
        samples = self.charge if charging else self.discharge
        if len(samples) < TARGET_MIN_SAMPLES:
            return None
        low, high = TARGET_LIMITS_W
        return min(high, max(low, quantile(samples, TARGET_QUANTILE)))


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
CONSUMER_KEEP = 120
CONSUMER_MIN_SAMPLES = 30
# A pause while commanded on: below this share of the learned power ...
PAUSE_SHARE = 0.1
# ... for at least this long, then running again without a new command.
PAUSE_MIN_S = 30.0
CYCLES_FOR_THERMOSTAT = 2


@dataclass
class ConsumerLearner:
    """Power while on and pauses of the own thermostat of one consumer."""

    powers: deque = field(default_factory=lambda: deque(maxlen=CONSUMER_KEEP))
    cycles: int = 0
    _paused_since: float | None = None

    def update(self, now: float, commanded_on: bool, power_w: float | None) -> None:
        if power_w is None or not commanded_on:
            self._paused_since = None
            return
        if power_w >= CONSUMER_ON_W:
            if self._paused_since is not None and now - self._paused_since >= PAUSE_MIN_S:
                self.cycles += 1
            self._paused_since = None
            self.powers.append(power_w)
            return
        nominal = self.nominal_w
        if nominal is not None and power_w < PAUSE_SHARE * nominal and self._paused_since is None:
            self._paused_since = now

    @property
    def nominal_w(self) -> float | None:
        if len(self.powers) < CONSUMER_MIN_SAMPLES:
            return None
        return round(statistics.median(self.powers))

    @property
    def thermostat_cycles(self) -> bool:
        return self.cycles >= CYCLES_FOR_THERMOSTAT

    def as_dict(self) -> dict:
        return {"powers": list(self.powers), "cycles": self.cycles}

    @classmethod
    def from_dict(cls, data: dict | None) -> ConsumerLearner:
        data = data or {}
        learner = cls(cycles=data.get("cycles", 0))
        learner.powers.extend(data.get("powers", []))
        return learner


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
