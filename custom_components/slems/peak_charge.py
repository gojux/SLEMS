"""Charging from the grid for peak shaving (pure computations).

With a power price, import below the limit costs no power price, so the
batteries are topped up to the peak shaving threshold before the next
expected peak: in the cheapest quarter hours (with equal prices the latest,
home storage ages with time at a high state of charge), only with the room
the forecast import leaves below the limit. The control keeps the import
within the budget of the interval anyway (see power_peak), so the charging
itself never makes a peak.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .grid_charge import QUARTER, slot_start


def plan_peak_charge(
    now: datetime,
    missing_wh: float,
    efficiency: float,
    max_charge_w: float,
    room_w: Mapping[datetime, float],
    prices: Mapping[datetime, float | None],
    deadline: datetime,
) -> dict[datetime, float]:
    """AC charge power (W) per local quarter hour start from now until ``deadline``.

    ``missing_wh``: stored energy missing to the threshold; ``room_w``: import
    the forecast leaves below the limit per quarter hour; ``prices``: import
    price per quarter hour (a quarter hour without one comes last).
    """
    if missing_wh <= 0 or max_charge_w <= 0:
        return {}
    local_now = dt_util.as_local(now)
    first = slot_start(local_now)
    periods: list[tuple[datetime, float]] = []  # (start, AC Wh possible)
    start = first
    while start < deadline:
        share = (start + QUARTER - local_now) / QUARTER if start == first else 1.0
        power = min(max_charge_w, max(0.0, room_w.get(start, 0.0)))
        if power > 0 and share > 0:
            periods.append((start, power * share * QUARTER / timedelta(hours=1)))
        start += QUARTER
    order = sorted(
        periods,
        key=lambda entry: (
            prices.get(entry[0]) is None,
            prices.get(entry[0]) or 0.0,
            -entry[0].timestamp(),
        ),
    )
    plan: dict[datetime, float] = {}
    needed = missing_wh / (efficiency or 1.0)
    for start, possible in order:
        if needed <= 0:
            break
        energy = min(possible, needed)
        share = (start + QUARTER - local_now) / QUARTER if start == first else 1.0
        plan[start] = energy / (share * QUARTER / timedelta(hours=1))
        needed -= energy
    return plan
