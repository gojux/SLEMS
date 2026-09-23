"""Consumption forecast models (pure computations, no Home Assistant access).

All series are hourly: period start (aware datetime, full hour) -> Wh.

Base load: weighted mean per (day type, local hour) with exponentially
decaying weights, so recent behaviour dominates. A short-term correction factor
from the last days absorbs effects the profile does not know.

Heat pump: daily energy E = a + b * max(0, T_base - T_mean) fitted by weighted
least squares on (daily mean outdoor temperature, daily energy). The forecast
temperature of the day drives the heating share, so a warm spell in the
heating season immediately predicts less energy. The daily energy is spread
over the hours with the recent hourly shape.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import math

import numpy as np

from homeassistant.util import dt as dt_util

type HourlySeries = Mapping[datetime, float]

WORKDAY = "workday"
WEEKEND = "weekend"

BASE_HALF_LIFE_DAYS = 14
BASE_LOOKBACK_DAYS = 56
HEAT_PUMP_HALF_LIFE_DAYS = 21
HEAT_PUMP_LOOKBACK_DAYS = 365
SHAPE_LOOKBACK_DAYS = 14
BIAS_LOOKBACK_HOURS = 72
BIAS_LIMITS = (0.8, 1.25)
# Candidate heating limits (°C) for the heat pump model.
HEATING_LIMITS = tuple(range(10, 21))
MIN_HEAT_PUMP_DAYS = 7
# Hours whose level defines the absence (vacation) profile.
NIGHT_HOURS = range(1, 5)


def day_type(day: date) -> str:
    return WEEKEND if day.weekday() >= 5 else WORKDAY


def _decay_weight(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (age_days / half_life_days)


@dataclass
class BaseLoadProfile:
    """Hourly base load per day type."""

    profile: dict[tuple[str, int], float] = field(default_factory=dict)
    correction: float = 1.0

    @classmethod
    def fit(cls, history: HourlySeries, now: datetime) -> BaseLoadProfile:
        sums: dict[tuple[str, int], float] = defaultdict(float)
        weights: dict[tuple[str, int], float] = defaultdict(float)
        oldest = now - timedelta(days=BASE_LOOKBACK_DAYS)
        for start, wh in history.items():
            if not oldest <= start < now or wh < 0:
                continue
            local = dt_util.as_local(start)
            key = (day_type(local.date()), local.hour)
            weight = _decay_weight((now - start).total_seconds() / 86400, BASE_HALF_LIFE_DAYS)
            sums[key] += wh * weight
            weights[key] += weight
        model = cls({key: sums[key] / weights[key] for key in sums})
        model.correction = model._recent_correction(history, now)
        return model

    def _recent_correction(self, history: HourlySeries, now: datetime) -> float:
        since = now - timedelta(hours=BIAS_LOOKBACK_HOURS)
        actual = predicted = 0.0
        for start, wh in history.items():
            if since <= start < now and (value := self.raw(start)) is not None:
                actual += wh
                predicted += value
        if predicted <= 0:
            return 1.0
        return min(BIAS_LIMITS[1], max(BIAS_LIMITS[0], actual / predicted))

    @property
    def is_empty(self) -> bool:
        return not self.profile

    def raw(self, start: datetime) -> float | None:
        local = dt_util.as_local(start)
        key = (day_type(local.date()), local.hour)
        if key in self.profile:
            return self.profile[key]
        # Fall back to the other day type for the same hour.
        other = (WORKDAY if key[0] == WEEKEND else WEEKEND, key[1])
        return self.profile.get(other)

    def predict(self, start: datetime, vacation: bool = False) -> float:
        value = self.raw(start)
        if value is None:
            return 0.0
        if vacation:
            return min(value, self.night_level)
        return value * self.correction

    @property
    def night_level(self) -> float:
        values = [v for (_, hour), v in self.profile.items() if hour in NIGHT_HOURS]
        return sum(values) / len(values) if values else 0.0


@dataclass
class HeatPumpModel:
    """Daily heat pump energy as a function of the daily mean temperature."""

    # Temperature independent share (hot water), Wh per day.
    base_wh: float
    # Additional Wh per day and Kelvin below the heating limit.
    slope_wh_per_k: float
    heating_limit_c: float
    # Hourly shares of the daily energy (sum 1).
    shape: dict[int, float]
    # Additive correction from the last days, Wh per day.
    correction_wh: float = 0.0
    # True if the temperature model could be fitted; otherwise the mean of the
    # last days is used for every day.
    temperature_based: bool = True

    def daily(self, mean_temperature: float | None) -> float:
        if not self.temperature_based or mean_temperature is None:
            return max(0.0, self.base_wh + self.correction_wh)
        heating = self.slope_wh_per_k * max(0.0, self.heating_limit_c - mean_temperature)
        return max(0.0, self.base_wh + heating + self.correction_wh)

    def hourly(self, day_energy_wh: float, hour: int) -> float:
        return day_energy_wh * self.shape.get(hour, 1 / 24)

    @classmethod
    def fit(
        cls,
        history: HourlySeries,
        daily_temperature: Mapping[date, float],
        now: datetime,
    ) -> HeatPumpModel | None:
        """Fit the model; None if there is no history at all."""
        today = dt_util.as_local(now).date()
        daily_energy = _daily_sums(history, before=today)
        if not daily_energy:
            return None
        shape = _hourly_shape(history, now)

        days = sorted(
            day
            for day in daily_energy
            if day in daily_temperature
            and (today - day).days <= HEAT_PUMP_LOOKBACK_DAYS
        )
        if len(days) < MIN_HEAT_PUMP_DAYS:
            recent = [daily_energy[d] for d in sorted(daily_energy)[-7:]]
            return cls(
                base_wh=sum(recent) / len(recent),
                slope_wh_per_k=0.0,
                heating_limit_c=0.0,
                shape=shape,
                temperature_based=False,
            )

        energy = np.array([daily_energy[d] for d in days])
        temperature = np.array([daily_temperature[d] for d in days])
        weights = np.array(
            [_decay_weight((today - d).days, HEAT_PUMP_HALF_LIFE_DAYS) for d in days]
        )
        best: tuple[float, float, float, float] | None = None
        for limit in HEATING_LIMITS:
            degree = np.maximum(0.0, limit - temperature)
            base, slope = _weighted_linear_fit(degree, energy, weights)
            if slope < 0:
                base, slope = float(np.average(energy, weights=weights)), 0.0
            base = max(0.0, base)
            error = float(np.sum(weights * (energy - base - slope * degree) ** 2))
            if best is None or error < best[0]:
                best = (error, base, slope, limit)
        _, base, slope, limit = best
        model = cls(base_wh=base, slope_wh_per_k=slope, heating_limit_c=limit, shape=shape)

        recent_days = [d for d in days if (today - d).days <= 3]
        if recent_days:
            residuals = [daily_energy[d] - model.daily(daily_temperature[d]) for d in recent_days]
            correction = sum(residuals) / len(residuals)
            limit_wh = 0.3 * max(1.0, model.daily(float(np.mean(temperature[-3:]))))
            model.correction_wh = max(-limit_wh, min(limit_wh, correction))
        return model


def _weighted_linear_fit(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray
) -> tuple[float, float]:
    """Return (intercept, slope) of a weighted least squares line."""
    if np.allclose(x, x[0]):
        return float(np.average(y, weights=weights)), 0.0
    design = np.column_stack([np.ones_like(x), x])
    root = np.sqrt(weights)
    solution, *_ = np.linalg.lstsq(design * root[:, None], y * root, rcond=None)
    return float(solution[0]), float(solution[1])


def _daily_sums(history: HourlySeries, before: date) -> dict[date, float]:
    """Energy per local day; only days with (almost) complete data."""
    sums: dict[date, float] = defaultdict(float)
    counts: dict[date, int] = defaultdict(int)
    for start, wh in history.items():
        day = dt_util.as_local(start).date()
        if day < before:
            sums[day] += wh
            counts[day] += 1
    return {day: value for day, value in sums.items() if counts[day] >= 22}


def _hourly_shape(history: HourlySeries, now: datetime) -> dict[int, float]:
    since = now - timedelta(days=SHAPE_LOOKBACK_DAYS)
    per_hour: dict[int, float] = defaultdict(float)
    for start, wh in history.items():
        if since <= start < now and wh > 0:
            per_hour[dt_util.as_local(start).hour] += wh
    total = sum(per_hour.values())
    if total <= 0:
        return {}
    return {hour: value / total for hour, value in per_hour.items()}


def daily_mean_temperature(temperatures: HourlySeries) -> dict[date, float]:
    """Mean of the hourly temperatures per local day."""
    sums: dict[date, float] = defaultdict(float)
    counts: dict[date, int] = defaultdict(int)
    for start, value in temperatures.items():
        if value is None or math.isnan(value):
            continue
        day = dt_util.as_local(start).date()
        sums[day] += value
        counts[day] += 1
    return {day: sums[day] / counts[day] for day in sums if counts[day] >= 12}
