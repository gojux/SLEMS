"""Tests for the distribution between several batteries."""

import pytest

from custom_components.slems.battery_distribution import (
    BatteryDistributor,
    BatteryUnit,
    LossModel,
    RotationSettings,
)
from custom_components.slems.efficiency import LossCurveLearner

SETTINGS = RotationSettings(soc_threshold_pct=5, min_interval_s=900, ramp_s=60)
# fixed 15 W, 4 %, 1e-5 per W: sharing pays off above about 1.7 kW
LOSSES = LossModel()


def unit(battery_id: str, soc: float) -> BatteryUnit:
    return BatteryUnit(battery_id, soc, 2500, 2500, LOSSES)


def settle(distributor, total, units, start=0.0, seconds=300, step=5):
    """Call repeatedly so that ramps complete; return the last result."""
    result = None
    t = start
    while t <= start + seconds:
        result = distributor.distribute(total, units, SETTINGS, t)
        t += step
    return result


def test_low_power_uses_one_battery_with_highest_soc() -> None:
    result = settle(BatteryDistributor(), -600, [unit("a", 60), unit("b", 80)])
    assert result.selected == ("b",)
    assert result.power_w == {"a": 0, "b": pytest.approx(-600)}


def test_high_power_is_shared() -> None:
    result = settle(BatteryDistributor(), -3000, [unit("a", 60), unit("b", 80)])
    assert set(result.selected) == {"a", "b"}
    assert result.power_w["a"] == pytest.approx(-1500)
    assert result.power_w["b"] == pytest.approx(-1500)


def test_charging_prefers_lowest_soc() -> None:
    result = settle(BatteryDistributor(), 500, [unit("a", 60), unit("b", 80)])
    assert result.selected == ("a",)


def test_total_is_always_delivered() -> None:
    distributor = BatteryDistributor()
    units = [unit("a", 60), unit("b", 80)]
    settle(distributor, -600, units)
    # Sudden large load: b alone cannot deliver, a helps at once.
    result = distributor.distribute(-4000, units, SETTINGS, 400)
    assert sum(result.power_w.values()) == pytest.approx(-4000)


def test_rotation_after_threshold_with_smooth_transition() -> None:
    distributor = BatteryDistributor()
    settle(distributor, -600, [unit("a", 70), unit("b", 72)], seconds=995)
    # b discharged below a by more than 5 %: switch to a.
    units = [unit("a", 70), unit("b", 64)]
    first = distributor.distribute(-600, units, SETTINGS, 1000)
    assert first.selected == ("a",)
    # During the ramp both deliver, the sum stays constant.
    assert first.power_w["b"] < 0 and first.power_w["a"] < 0
    assert sum(first.power_w.values()) == pytest.approx(-600)
    later = settle(distributor, -600, units, start=1005, seconds=120)
    assert later.power_w == {"a": pytest.approx(-600), "b": 0}


def test_no_rotation_within_threshold_or_interval() -> None:
    distributor = BatteryDistributor()
    settle(distributor, -600, [unit("a", 70), unit("b", 72)])
    assert distributor.distribute(-600, [unit("a", 70), unit("b", 67)], SETTINGS, 400).selected == ("b",)
    distributor.distribute(-600, [unit("a", 70), unit("b", 60)], SETTINGS, 1000)  # rotates to a
    # a now drops below b quickly, but the minimum interval is not over.
    result = distributor.distribute(-600, [unit("a", 50), unit("b", 60)], SETTINGS, 1300)
    assert result.selected == ("a",)


def test_empty_battery_is_skipped() -> None:
    result = settle(BatteryDistributor(), -600, [unit("a", 0), unit("b", 30)])
    assert result.power_w["a"] == 0


def test_loss_curve_learning() -> None:
    learner = LossCurveLearner()
    model = LossModel(fixed_w=10, linear=0.03, quadratic_per_w=2e-5)
    for power in (300, 800, 1300, 1800, 2300):
        for _ in range(30):
            # discharging: DC side delivers AC plus losses
            learner.add(-power, -(power + model.loss(power)))
    learned = learner.model(LossModel())
    assert learned.loss(1000) == pytest.approx(model.loss(1000), rel=0.05)
    assert LossCurveLearner().model(LOSSES) is LOSSES
