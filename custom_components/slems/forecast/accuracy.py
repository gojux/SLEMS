"""Accuracy of the forecasts (pure computations, no Home Assistant access).

Consumption: backtest. For each of the last ``BACKTEST_DAYS`` complete days the
models are fitted with the history before that day's midnight, as the forecast
would have been made then, and compared with the measured consumption. The heat
pump uses the measured mean temperature of the day, not the weather forecast of
that time, so its share looks somewhat better than it was.

PV: the solar forecast integrations only provide the current forecast, so the
forecast of every day is recorded at the start of the day (``PvAccuracyTracker``)
and compared with the energy produced once the day is over.

Error measures: the daily error is |forecast − actual| / actual of the day's
energy (mean over the days); the bias is its signed mean (positive: forecast
too high); the hourly error is Σ|forecast − actual| / Σ actual over all hours
(how well the course of the day is hit). The expected error of tomorrow is the
mean daily error (of the same day type if available) applied to tomorrow's
forecast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from .models import (
    BASE_LOOKBACK_DAYS,
    BaseLoadProfile,
    HeatPumpModel,
    HourlySeries,
    day_type,
)

BACKTEST_DAYS = 14
# Hours with data a day needs to count.
MIN_DAY_HOURS = 22
PV_HISTORY_DAYS = 60
# The forecast of a day is recorded only before this local hour, so it is the
# forecast known at the start of the day.
PV_RECORD_UNTIL_HOUR = 6


@dataclass(frozen=True)
class DayResult:
    day: date
    forecast_wh: float
    actual_wh: float

    @property
    def error(self) -> float:
        """Relative error of the day's energy, +: forecast too high."""
        return (self.forecast_wh - self.actual_wh) / self.actual_wh


@dataclass
class Accuracy:
    """Accuracy over the evaluated days and the expected error of tomorrow."""

    days: list[DayResult] = field(default_factory=list)
    # Σ|hourly error| / Σ actual; None without hourly comparison.
    hourly_error: float | None = None

    @property
    def daily_error(self) -> float | None:
        if not self.days:
            return None
        return sum(abs(d.error) for d in self.days) / len(self.days)

    @property
    def bias(self) -> float | None:
        if not self.days:
            return None
        return sum(d.error for d in self.days) / len(self.days)

    def expected_error(self, day: date) -> float | None:
        """Typical relative daily error for ``day`` (same day type if possible)."""
        same = [abs(d.error) for d in self.days if day_type(d.day) == day_type(day)]
        values = same if len(same) >= 2 else [abs(d.error) for d in self.days]
        return sum(values) / len(values) if values else None


@dataclass
class ConsumptionAccuracy(Accuracy):
    # Days with (almost) complete consumption data in the profile's look-back.
    history_days: int = 0
    # Days with heat pump energy and temperature the heat pump model uses.
    heat_pump_days: int = 0


def history_days(history: HourlySeries, now: datetime, lookback_days: int) -> int:
    """Local days with at least MIN_DAY_HOURS hours of data before ``now``."""
    counts: dict[date, int] = {}
    oldest = now - timedelta(days=lookback_days)
    for start in history:
        if oldest <= start < now:
            day = dt_util.as_local(start).date()
            counts[day] = counts.get(day, 0) + 1
    return sum(1 for count in counts.values() if count >= MIN_DAY_HOURS)


def backtest_consumption(
    base_history: HourlySeries,
    heat_pump_history: HourlySeries | None,
    daily_temperature: dict[date, float],
    now: datetime,
) -> ConsumptionAccuracy:
    """Recalculate the forecast of the last days and compare with the measurement."""
    today = dt_util.start_of_local_day(dt_util.as_local(now))
    result = ConsumptionAccuracy(history_days=history_days(base_history, now, BASE_LOOKBACK_DAYS))
    abs_error = actual_total = 0.0
    for offset in range(BACKTEST_DAYS, 0, -1):
        day_start = today - timedelta(days=offset)
        hours = [day_start + timedelta(hours=h) for h in range(24)]
        actual = {
            h: base_history[h] + (heat_pump_history or {}).get(h, 0.0)
            for h in hours
            if h in base_history
        }
        if len(actual) < MIN_DAY_HOURS:
            continue
        base_model = BaseLoadProfile.fit(base_history, day_start)
        if base_model.is_empty:
            continue
        heat_pump_model = (
            HeatPumpModel.fit(heat_pump_history, daily_temperature, day_start)
            if heat_pump_history
            else None
        )
        day_energy = (
            heat_pump_model.daily(daily_temperature.get(day_start.date()))
            if heat_pump_model
            else 0.0
        )
        forecast = {
            h: base_model.predict(h)
            + (heat_pump_model.hourly(day_energy, h.hour) if heat_pump_model else 0.0)
            for h in actual
        }
        actual_wh = sum(actual.values())
        if actual_wh <= 0:
            continue
        result.days.append(DayResult(day_start.date(), sum(forecast.values()), actual_wh))
        abs_error += sum(abs(forecast[h] - actual[h]) for h in actual)
        actual_total += actual_wh
    if actual_total > 0:
        result.hourly_error = abs_error / actual_total
    if heat_pump_history:
        model = HeatPumpModel.fit(heat_pump_history, daily_temperature, now)
        result.heat_pump_days = model.days_used if model else 0
    return result


class PvAccuracyTracker:
    """Recorded PV forecasts of past days and the energy produced."""

    def __init__(self) -> None:
        # ISO date -> {"forecast_wh": .., "actual_wh": ..}
        self.days: dict[str, dict[str, float]] = {}

    def record_forecast(self, day: date, forecast_wh: float, now: datetime) -> None:
        """Record the forecast of ``day`` once, at the start of the day."""
        key = day.isoformat()
        if key in self.days or dt_util.as_local(now).hour >= PV_RECORD_UNTIL_HOUR:
            return
        self.days[key] = {"forecast_wh": forecast_wh}
        self._trim()

    def record_actual(self, day: date, actual_wh: float) -> None:
        entry = self.days.get(day.isoformat())
        if entry is not None and "actual_wh" not in entry:
            entry["actual_wh"] = actual_wh

    def accuracy(self) -> Accuracy:
        result = Accuracy()
        for key in sorted(self.days):
            entry = self.days[key]
            actual = entry.get("actual_wh")
            if actual is not None and actual > 0:
                result.days.append(DayResult(date.fromisoformat(key), entry["forecast_wh"], actual))
        return result

    def _trim(self) -> None:
        for key in sorted(self.days)[:-PV_HISTORY_DAYS]:
            del self.days[key]

    def as_dict(self) -> dict:
        return dict(self.days)

    def restore(self, data: dict | None) -> None:
        if data:
            self.days = {key: dict(value) for key, value in data.items()}
