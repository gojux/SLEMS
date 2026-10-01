"""Grid import and export per hour from the recorder (for tariffs and bills).

From the energy counters of the smart meter (kWh, long-term statistics:
the change per hour), otherwise from the hourly mean of the grid power
(+ import / − export). The latter nets import and export within an hour
and is less exact.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .forecast import async_statistic_means


async def async_hourly_changes(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Change of an energy counter per hour in Wh."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {statistic_id},
        "hour",
        {"energy": UnitOfEnergy.WATT_HOUR},
        {"change"},
    )
    return {
        dt_util.utc_from_timestamp(row["start"]): max(0.0, row["change"])
        for row in rows.get(statistic_id, [])
        if row.get("change") is not None
    }


async def async_grid_energy(
    hass: HomeAssistant,
    start: datetime,
    end: datetime,
    *,
    import_entity: str | None,
    export_entity: str | None,
    grid_power_entity: str,
    grid_inverted: bool,
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """(import, export) in Wh per hour between ``start`` and ``end``."""
    if import_entity:
        imported = await async_hourly_changes(hass, import_entity, start, end)
        exported = (
            await async_hourly_changes(hass, export_entity, start, end) if export_entity else {}
        )
        return imported, exported
    means = (await async_statistic_means(hass, [grid_power_entity], start, end)).get(
        grid_power_entity, {}
    )
    sign = -1.0 if grid_inverted else 1.0
    imported = {hour: max(0.0, sign * mean) for hour, mean in means.items()}
    exported = {hour: max(0.0, -sign * mean) for hour, mean in means.items()}
    return imported, exported
