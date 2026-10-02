"""Wear costs of a battery per kWh it stores and delivers again.

From the purchase price and the rated cycles (manufacturer) if known:
price ÷ (cycles × usable capacity). A price of 0 means no wear costs: field
measurements of home storage systems show that they age mostly with time,
temperature and state of charge, and often reach their end of life long
before their rated cycles. Without values a low estimate applies, so price
aware actions that cost a cycle (charging from the grid) are not blocked by
an assumed high wear; the dashboard then suggests entering the values.
"""

from __future__ import annotations

from dataclasses import dataclass

ESTIMATED_CT_PER_KWH = 1.0


@dataclass(frozen=True)
class Wear:
    ct_per_kwh: float
    # True: the low estimate, price or cycles are not entered.
    estimated: bool = True


def wear_cost(price_eur: float | None, cycles: float | None, capacity_wh: float | None) -> Wear:
    if price_eur is not None and price_eur <= 0:
        return Wear(0.0, estimated=False)
    if price_eur and cycles and capacity_wh and cycles > 0 and capacity_wh > 0:
        return Wear(price_eur * 100 / (cycles * capacity_wh / 1000), estimated=False)
    return Wear(ESTIMATED_CT_PER_KWH)
