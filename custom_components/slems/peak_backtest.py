"""What peak shaving would have saved with a power price (an estimate, pure computations).

From the recorded quarter hours of a month (see grid_quarters): the lowest
limit the batteries could have held, where every interval above it needs no
more than their discharge power and each day's energy above it fits into
their usable energy (above the minimum state of charge; held back for the
peaks, topped up from the grid below the limit, see peak_charge). The saving
is the power price of the recorded peak minus that of the limit, minus the
losses and the wear of the energy for the peaks.

An estimate on the safe side for the peaks, optimistic for the batteries:
the recorded import already shows what they did, and at a peak they may
have been discharging at their limit or have been empty.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from homeassistant.util import dt as dt_util

from .grid_quarters import SLOT_S, GridQuarters
from .power_peak import PowerPrice
from .price_backtest import BacktestBattery

STEP_KW = 0.1


@dataclass(frozen=True)
class PeakEstimate:
    # Billed power as recorded and with peak shaving at ``limit_kw``.
    recorded_kw: float
    limit_kw: float
    billed_kw: float
    # Energy above the limit the batteries deliver in the month (kWh).
    energy_kwh: float
    saving: float


def month_powers(quarters: GridQuarters, interval_s: int) -> dict[date, dict[datetime, float]]:
    """Mean import power (kW) of the recorded intervals, per first day of their local month."""
    months: dict[date, dict[datetime, float]] = {}
    starts = sorted(slot for slot in quarters.covered if slot % interval_s == 0)
    for start in starts:
        slots = range(start, start + interval_s, SLOT_S)
        if not all(quarters.complete(slot) for slot in slots):
            continue
        moment = dt_util.utc_from_timestamp(start)
        first = dt_util.as_local(moment).date().replace(day=1)
        wh = sum(quarters.imported.get(slot, 0.0) for slot in slots)
        months.setdefault(first, {})[moment] = wh / 1000 * 3600 / interval_s
    return months


def estimate(
    price: PowerPrice,
    powers: Mapping[datetime, float],
    battery: BacktestBattery,
    import_ct: float,
) -> PeakEstimate | None:
    """The estimate for one month (``powers``: kW per interval); None without intervals that count."""
    counted = {start: power for start, power in powers.items() if price.counts(start)}
    if not counted:
        return None
    hours = price.interval_s / 3600
    recorded = price.billed_kw(counted)
    usable_kwh = max(0.0, battery.full_wh - battery.min_wh) * battery.efficiency / 1000
    discharge_kw = battery.max_discharge_w / 1000
    days: dict[date, list[float]] = {}
    for start, power in counted.items():
        days.setdefault(dt_util.as_local(start).date(), []).append(power)
    peak = max(counted.values())
    limit = price.base.billed_kw(0.0)
    while limit < peak:
        if peak - limit <= discharge_kw and all(
            sum(max(0.0, power - limit) for power in values) * hours <= usable_kwh for values in days.values()
        ):
            break
        limit += STEP_KW
    limit = min(limit, peak)
    billed = price.billed_kw({start: min(power, limit) for start, power in counted.items()})
    energy = sum(max(0.0, power - limit) for power in counted.values()) * hours
    efficiency = battery.efficiency or 1.0
    # Charged back: both conversion losses and the wear, per kWh delivered.
    losses = energy * (import_ct * (1 / efficiency**2 - 1) + battery.wear_ct) / 100
    return PeakEstimate(
        recorded_kw=recorded,
        limit_kw=limit,
        billed_kw=billed,
        energy_kwh=energy,
        saving=price.cost(recorded) - price.cost(billed) - losses,
    )
