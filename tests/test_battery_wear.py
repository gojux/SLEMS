"""Tests for the wear costs of a battery (made-up values)."""

import pytest

from custom_components.slems.battery_wear import ESTIMATED_CT_PER_KWH, wear_cost


def test_wear_from_price_and_cycles() -> None:
    wear = wear_cost(1200.0, 6000, 5000)
    # 1200 € ÷ (6000 × 5 kWh) = 4 ct/kWh
    assert wear.ct_per_kwh == pytest.approx(4.0)
    assert not wear.estimated


def test_price_zero_means_no_wear_costs() -> None:
    wear = wear_cost(0.0, None, 5000)
    assert wear.ct_per_kwh == 0 and not wear.estimated


@pytest.mark.parametrize(("price", "cycles"), [(None, 6000), (1200.0, None), (None, None), (1200.0, 0)])
def test_estimate_without_both_values(price, cycles) -> None:
    wear = wear_cost(price, cycles, 5000)
    assert wear.estimated and wear.ct_per_kwh == ESTIMATED_CT_PER_KWH
