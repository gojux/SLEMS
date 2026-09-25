"""Consumption forecast for today and tomorrow.

The forecast covers the consumption behind the smart meter that SLEMS does not
control itself: base load plus heat pumps. Controllable consumers are left
out because the allocation decides when they run.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .accuracy import ConsumptionAccuracy, backtest_consumption
from .models import (
    HEAT_PUMP_LOOKBACK_DAYS,
    BaseLoadProfile,
    HeatPumpModel,
    HourlySeries,
    daily_mean_temperature,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForecastSources:
    """Statistic ids (= entity ids) the forecast is built from."""

    # House consumption in order of preference (SLEMS sensor first).
    house: tuple[str, ...]
    # Fallback: house = grid + pv - batteries.
    grid: str | None
    grid_inverted: bool
    pv: str | None
    batteries: tuple[str, ...]
    # Consumers behind the meter to subtract from the house consumption.
    heat_pumps: tuple[str, ...]
    controllable: tuple[str, ...]
    temperature: str | None
    weather: str | None


@dataclass
class ConsumptionForecast:
    """Hourly forecast (period start -> Wh) for today and tomorrow."""

    total: dict[datetime, float] = field(default_factory=dict)
    base: dict[datetime, float] = field(default_factory=dict)
    heat_pump: dict[datetime, float] = field(default_factory=dict)
    # Daily mean temperatures used for the heat pump.
    temperature: dict[date, float | None] = field(default_factory=dict)
    created: datetime | None = None

    def energy_on_day(self, day: date) -> float | None:
        """Forecast energy of a local day, None if the day is not covered."""
        values = [wh for start, wh in self.total.items() if dt_util.as_local(start).date() == day]
        return sum(values) if values else None


async def async_statistic_means(
    hass: HomeAssistant,
    statistic_ids: Iterable[str],
    start: datetime,
    end: datetime,
    period: Literal["5minute", "hour"] = "hour",
) -> dict[str, dict[datetime, float]]:
    """Means per period (default hourly; W is Wh per hour) of the given statistics."""
    ids = {statistic_id for statistic_id in statistic_ids if statistic_id}
    if not ids:
        return {}
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        ids,
        period,
        {"power": UnitOfPower.WATT, "temperature": UnitOfTemperature.CELSIUS},
        {"mean"},
    )
    return {
        statistic_id: {
            dt_util.utc_from_timestamp(row["start"]): row["mean"]
            for row in statistic_rows
            if row.get("mean") is not None
        }
        for statistic_id, statistic_rows in rows.items()
    }


def _sum_series(series: Iterable[HourlySeries]) -> dict[datetime, float]:
    total: dict[datetime, float] = {}
    for values in series:
        for start, value in values.items():
            total[start] = total.get(start, 0.0) + value
    return total


def build_house_history(
    sources: ForecastSources, means: dict[str, dict[datetime, float]]
) -> dict[datetime, float]:
    """House consumption per hour from the best available source."""
    house: dict[datetime, float] = {}
    grid = means.get(sources.grid or "", {})
    pv = means.get(sources.pv or "", {})
    batteries = _sum_series(means.get(b, {}) for b in sources.batteries)
    for start, grid_wh in grid.items():
        if sources.grid_inverted:
            grid_wh = -grid_wh
        house[start] = grid_wh + pv.get(start, 0.0) - batteries.get(start, 0.0)
    # Explicit sources win; the first one (SLEMS) has the highest priority.
    for statistic_id in reversed(sources.house):
        house.update(means.get(statistic_id, {}))
    return house


class ConsumptionForecaster:
    """Fits the models and produces the forecast."""

    def __init__(self, hass: HomeAssistant, sources: ForecastSources) -> None:
        self._hass = hass
        self.sources = sources
        self.forecast: ConsumptionForecast | None = None
        self.accuracy: ConsumptionAccuracy | None = None

    async def async_refresh(self, vacation: bool) -> ConsumptionForecast | None:
        """Refit all models from the statistics and update the forecast."""
        now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        sources = self.sources
        means = await async_statistic_means(
            self._hass,
            [
                *sources.house,
                sources.grid,
                sources.pv,
                *sources.batteries,
                *sources.heat_pumps,
                *sources.controllable,
                sources.temperature,
            ],
            now - timedelta(days=HEAT_PUMP_LOOKBACK_DAYS),
            now,
        )
        house = build_house_history(sources, means)
        heat_pump = _sum_series(means.get(h, {}) for h in sources.heat_pumps)
        controllable = _sum_series(means.get(c, {}) for c in sources.controllable)
        base = {
            start: wh - heat_pump.get(start, 0.0) - controllable.get(start, 0.0)
            for start, wh in house.items()
        }
        temperatures = means.get(sources.temperature or "", {})
        forecast_temperatures = await self._async_weather_temperatures()

        self.forecast = await self._hass.async_add_executor_job(
            self._compute, now, base, heat_pump, temperatures, forecast_temperatures, vacation
        )
        self.accuracy = await self._hass.async_add_executor_job(
            backtest_consumption,
            base,
            heat_pump if sources.heat_pumps else None,
            daily_mean_temperature(temperatures),
            now,
        )
        return self.forecast

    def _compute(
        self,
        now: datetime,
        base_history: HourlySeries,
        heat_pump_history: HourlySeries,
        temperature_history: HourlySeries,
        forecast_temperatures: HourlySeries,
        vacation: bool,
    ) -> ConsumptionForecast | None:
        base_model = BaseLoadProfile.fit(base_history, now)
        if base_model.is_empty:
            return None
        daily_temperature = daily_mean_temperature(temperature_history)
        heat_pump_model = (
            HeatPumpModel.fit(heat_pump_history, daily_temperature, now)
            if self.sources.heat_pumps
            else None
        )

        today = dt_util.start_of_local_day(dt_util.as_local(now))
        result = ConsumptionForecast(created=now)
        for day_offset in (0, 1):
            day_start = today + timedelta(days=day_offset)
            day = day_start.date()
            mean_temperature = _mean_temperature(
                day, temperature_history, forecast_temperatures, daily_temperature
            )
            result.temperature[day] = mean_temperature
            day_energy = heat_pump_model.daily(mean_temperature) if heat_pump_model else 0.0
            start = day_start
            while start < day_start + timedelta(days=1):
                base_wh = base_model.predict(start, vacation)
                hp_wh = (
                    heat_pump_model.hourly(day_energy, dt_util.as_local(start).hour)
                    if heat_pump_model
                    else 0.0
                )
                key = dt_util.as_utc(start)
                result.base[key] = base_wh
                result.heat_pump[key] = hp_wh
                result.total[key] = base_wh + hp_wh
                start += timedelta(hours=1)
        return result

    async def _async_weather_temperatures(self) -> dict[datetime, float]:
        """Hourly forecast temperatures of the weather entity, if configured."""
        weather = self.sources.weather
        if not weather or self._hass.states.get(weather) is None:
            return {}
        for forecast_type in ("hourly", "daily"):
            try:
                response = await self._hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": weather, "type": forecast_type},
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError:
                continue
            entries = (response or {}).get(weather, {}).get("forecast", [])
            temperatures = _forecast_temperatures(entries, forecast_type)
            if temperatures:
                return temperatures
        return {}


def _forecast_temperatures(entries: list[dict], forecast_type: str) -> dict[datetime, float]:
    """Hourly temperatures; a daily forecast yields its mean for all 24 hours."""
    result: dict[datetime, float] = {}
    for entry in entries:
        start = dt_util.parse_datetime(str(entry.get("datetime")))
        temperature = entry.get("temperature")
        if start is None or temperature is None:
            continue
        if forecast_type == "hourly":
            result[dt_util.as_utc(start)] = float(temperature)
            continue
        low = entry.get("templow")
        mean = (float(temperature) + float(low)) / 2 if low is not None else float(temperature)
        day_start = dt_util.start_of_local_day(dt_util.as_local(start))
        for hour in range(24):
            result[dt_util.as_utc(day_start + timedelta(hours=hour))] = mean
    return result


def _mean_temperature(
    day: date,
    history: HourlySeries,
    forecast: HourlySeries,
    daily_history: dict[date, float],
) -> float | None:
    """Daily mean from measured hours plus forecast hours; else persistence."""
    values = {
        start: value
        for series in (forecast, history)  # measured values override forecasts
        for start, value in series.items()
        if dt_util.as_local(start).date() == day
    }
    if len(values) >= 12:
        return sum(values.values()) / len(values)
    recent = [daily_history[d] for d in sorted(daily_history)[-3:]]
    return sum(recent) / len(recent) if recent else None
