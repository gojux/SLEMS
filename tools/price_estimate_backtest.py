"""How well do the estimated day-ahead prices hit the real ones?

For every day of a stored price history the estimate is made as it would have
been the evening before (all prices up to the end of the previous day known)
and compared with the real prices of the day. Variants of the estimate run
side by side, so changes can be judged by numbers.

Usage (from the repository root, in the test container):
  docker compose --profile tests run --rm --entrypoint python \\
      -e PYTHONPATH=/repo tests tools/price_estimate_backtest.py \\
      dev/config/.storage/slems.<entry id>.market_prices [--country DE] [--days 300]

Measures per variant (ct/kWh, only days with a complete history):
* error: mean absolute deviation of the estimate,
* shape: the same after taking away each day's mean (does it hit the course
  of the day, what matters for shifting energy in time),
* rank: mean rank correlation per day (1: the order of cheap and expensive
  quarter hours is hit exactly),
* cheap: share of the 16 cheapest estimated quarter hours that are among the
  32 cheapest real ones.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import partial
from datetime import date, datetime, timedelta
import json
import statistics

import holidays

from homeassistant.util import dt as dt_util

from custom_components.slems import market_prices
from custom_components.slems.market_prices import SLOT_S, day_bounds


def load(path: str) -> dict[int, float]:
    data = json.load(open(path, encoding="utf-8"))["data"]
    start = int(data["start"])
    return {start + i * SLOT_S: float(v) for i, v in enumerate(data["values"]) if v is not None}


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    for rank, index in enumerate(order):
        result[index] = rank
    return result


def correlation(a: list[float], b: list[float]) -> float:
    if statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return 0.0
    return statistics.correlation(a, b)


def evaluate(
    prices: dict[int, float], days: list[date], estimate: Callable[[dict[int, float], int, int], dict[int, float]]
) -> dict[str, float]:
    errors, shapes, rank_corr, cheap = [], [], [], []
    for day in days:
        start, end = day_bounds(day)
        history = {slot: price for slot, price in prices.items() if slot < start}
        guess = estimate(history, start, end)
        slots = [slot for slot in range(start, end, SLOT_S) if slot in prices and slot in guess]
        if len(slots) < (end - start) // SLOT_S:
            continue
        real = [prices[s] / 10 for s in slots]
        est = [guess[s] / 10 for s in slots]
        errors.append(statistics.fmean(abs(r - e) for r, e in zip(real, est, strict=True)))
        real_mean, est_mean = statistics.fmean(real), statistics.fmean(est)
        shapes.append(statistics.fmean(abs((r - real_mean) - (e - est_mean)) for r, e in zip(real, est, strict=True)))
        rank_corr.append(correlation(ranks(real), ranks(est)))
        cheapest_real = set(sorted(range(len(real)), key=real.__getitem__)[:32])
        cheapest_est = sorted(range(len(est)), key=est.__getitem__)[:16]
        cheap.append(sum(1 for i in cheapest_est if i in cheapest_real) / 16)
    return {
        "days": len(errors),
        "error": statistics.fmean(errors),
        "shape": statistics.fmean(shapes),
        "rank": statistics.fmean(rank_corr),
        "cheap": statistics.fmean(cheap),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("store")
    parser.add_argument("--country", default="DE")
    parser.add_argument("--days", type=int, default=300)
    parser.add_argument("--holidays-only", action="store_true", help="only public holidays")
    args = parser.parse_args()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Berlin" if args.country == "DE" else "Europe/Vienna"))
    prices = load(args.store)
    last = dt_util.as_local(dt_util.utc_from_timestamp(max(prices))).date()
    first = dt_util.as_local(dt_util.utc_from_timestamp(min(prices))).date() + timedelta(days=15)
    days = [d for d in (last - timedelta(days=n) for n in range(args.days)) if d >= first]

    public = holidays.country_holidays(args.country)
    if args.holidays_only:
        days = [d for d in days if d in public]

    def day_off(day: date) -> bool:
        return day in public

    def yesterday(history: dict[int, float], start: int, end: int) -> dict[int, float]:
        return {slot: history[slot - 86400] for slot in range(start, end, SLOT_S) if slot - 86400 in history}

    estimate = market_prices.estimate_prices
    variants: dict[str, Callable] = {
        "as shipped (14 days, share 0.5)": estimate,
        "undamped (share 1)": partial(estimate, share=1.0),
        "undamped + public holidays": partial(estimate, share=1.0, day_off=day_off),
        "undamped + holidays, 7 days": partial(estimate, share=1.0, days=7, day_off=day_off),
        "undamped + holidays, 28 days": partial(estimate, share=1.0, days=28, day_off=day_off),
        "yesterday's prices (baseline)": yesterday,
    }
    print(f"{len(days)} days up to {last}, {args.country}")
    print(f"{'variant':44} {'days':>5} {'error':>7} {'shape':>7} {'rank':>6} {'cheap':>6}")
    for name, estimate in variants.items():
        result = evaluate(prices, days, estimate)
        print(
            f"{name:44} {result['days']:5} {result['error']:7.2f} {result['shape']:7.2f} "
            f"{result['rank']:6.2f} {result['cheap']:6.0%}"
        )


if __name__ == "__main__":
    main()
