"""Tests for the distribution between several batteries."""

import pytest

from custom_components.slems.battery_distribution import (
    BatteryDistributor,
    BatteryUnit,
    LossModel,
    RotationSettings,
)
from custom_components.slems.efficiency import LossCurveLearner

SETTINGS = RotationSettings(
    soc_threshold_pct=5, min_interval_s=900, ramp_rate_w_per_s=100, ramp_max_s=30
)
# fixed 15 W, 4 %, 1e-5 per W: sharing pays off above about 1.7 kW
LOSSES = LossModel()


def unit(battery_id: str, soc: float, leaving: float | None = None) -> BatteryUnit:
    return BatteryUnit(battery_id, soc, 2500, 2500, LOSSES, leaving_fraction=leaving)


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
        # The first sample after a change is not steady and is skipped.
        for _ in range(31):
            # discharging: DC side delivers AC plus losses
            learner.add(-power, -(power + model.loss(power)))
    learned = learner.model(LossModel())
    assert learned.loss(1000) == pytest.approx(model.loss(1000), rel=0.05)
    assert LossCurveLearner().model(LOSSES) is LOSSES


def _rotation_duration(total: float) -> float:
    """Seconds until a rotation has moved all power (5 s control steps)."""
    distributor = BatteryDistributor()
    settle(distributor, total, [unit("a", 70), unit("b", 72)], seconds=995)
    units = [unit("a", 70), unit("b", 60)]
    t = 1000.0
    while True:
        result = distributor.distribute(total, units, SETTINGS, t)
        if result.power_w["b"] == 0:
            return t - 995
        t += 5


def test_ramp_duration_follows_rate_with_maximum() -> None:
    # 100 W/s with 5 s steps: 400 W within one step, 1500 W in 15 s.
    assert _rotation_duration(-400) == 5
    assert _rotation_duration(-1500) == 15
    # A slow rate is capped by the maximum ramp time.
    settings = RotationSettings(5, 900, ramp_rate_w_per_s=10, ramp_max_s=30)
    distributor = BatteryDistributor()
    for t in range(0, 1000, 5):
        distributor.distribute(-600, [unit("a", 70), unit("b", 72)], settings, t)
    units = [unit("a", 70), unit("b", 60)]
    t = 1000
    while distributor.distribute(-600, units, settings, t).power_w["b"] != 0:
        t += 5
    # 10 W/s would take 60 s, the maximum limits it to 30 s.
    assert t - 995 == 30


def test_disabled_battery_ramps_out_with_remaining_fraction() -> None:
    distributor = BatteryDistributor()
    settle(distributor, -2000, [unit("a", 60), unit("b", 80)], seconds=100)
    # 1.2 s into the 5 s ramp-out: b keeps at most its remaining fraction.
    first = distributor.distribute(-2000, [unit("a", 60), unit("b", 80, 0.76)], SETTINGS, 102)
    assert -2000 * 0.76 / 1.76 <= first.power_w["b"] < 0
    assert sum(first.power_w.values()) == pytest.approx(-2000)
    later = distributor.distribute(-2000, [unit("a", 60), unit("b", 80, 0.0)], SETTINGS, 106)
    assert later.power_w == {"a": pytest.approx(-2000), "b": 0}


def test_leaving_battery_never_helps_out() -> None:
    distributor = BatteryDistributor()
    settle(distributor, -600, [unit("a", 60), unit("b", 80)], seconds=100)
    units = [unit("a", 60), unit("b", 80, leaving=0.5)]
    distributor.distribute(-600, units, SETTINGS, 110)
    result = distributor.distribute(-4000, units, SETTINGS, 115)
    assert result.power_w["b"] == 0
    assert result.power_w["a"] == -2500


def test_loss_curve_ignores_transients() -> None:
    learner = LossCurveLearner()
    for power in (500, 1500, 500, 1500) * 30:
        learner.add(-power, -power * 0.5)  # nonsense values during changes
    assert learner.bins == {}
