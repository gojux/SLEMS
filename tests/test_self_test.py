"""Tests for the self-test of a battery (simulated battery and meter)."""

import random

import pytest

from custom_components.slems.self_test import (
    HOLD_S,
    IDLE_S,
    STEP_MAX_S,
    Outcome,
    Phase,
    Sample,
    SelfTest,
    self_test_power_w,
    step_power_w,
)

HOUSE_W = 400.0


def run(
    test: SelfTest,
    *,
    sign: float = 1.0,
    scale: float = 1.0,
    effect: float = 1.0,
    delay_s: float = 4.0,
    counters: bool = True,
    swap_counters: bool = False,
    noise_w: float = 0.0,
    soc: float = 50.0,
    counter_digits: int = 2,
) -> SelfTest:
    """Drive a test with a simulated battery: 1 s samples until the release."""
    rng = random.Random(1)
    now = 0.0
    commands: list[tuple[float, float]] = []
    charged = discharged = 100.0
    power = 0.0
    while test.phase is not Phase.RELEASE and now < 3600:
        command = commands[-1][1] if commands else 0.0
        # The battery follows its command after the delay.
        actual = next((c for t, c in reversed(commands) if t <= now - delay_s), 0.0) * effect
        if actual > 0:
            charged += actual / 3600 / 1000
        elif actual < 0:
            discharged += -actual / 3600 / 1000
        soc += actual / 3600 / 5000 * 100
        meter = HOUSE_W + actual + (rng.uniform(-noise_w, noise_w) if noise_w else 0.0)
        reported = actual * sign * scale
        c, d = (discharged, charged) if swap_counters else (charged, discharged)
        sample = Sample(
            now, meter, reported, soc,
            round(c, counter_digits) if counters else None, round(d, counter_digits) if counters else None,
        )
        result = test.step(now, sample, ready=True)
        power = result if result is not None else power
        if result is not None and result != command:
            commands.append((now, result))
        now += 1.0
    test.released(True, 1000.0)
    return test


def outcomes(test: SelfTest) -> dict[str, str]:
    return {check.key: check.outcome.value for check in test.checks}


def test_power_of_the_steps() -> None:
    assert self_test_power_w(2500, 2500) == 625
    assert self_test_power_w(800, 800) == 300
    assert self_test_power_w(10_000, 5000) == 800
    assert self_test_power_w(200, 800) == 200
    assert step_power_w(625, 2500) == 625
    assert step_power_w(625, 80) == 0.0


def test_a_correct_battery_passes() -> None:
    test = run(SelfTest(600, 600, 0.0))
    assert test.result == "ok", outcomes(test)
    assert set(outcomes(test).values()) == {"ok"}
    response = next(c for c in test.checks if c.key == "response")
    assert response.values["charge_s"] == pytest.approx(5, abs=1.5)
    # IDLE, CHARGE (until reached plus HOLD_S), REST, DISCHARGE: a few minutes.
    assert test.finished_at == 1000.0


def test_inverted_power_sensor() -> None:
    test = run(SelfTest(600, 600, 0.0), sign=-1)
    assert outcomes(test)["power_sign"] == "failed"
    assert outcomes(test)["effect_charge"] == "ok"
    assert test.result == "failed"


def test_power_in_wrong_unit() -> None:
    assert outcomes(run(SelfTest(600, 600, 0.0), scale=10))["power_scale"] == "failed"
    assert outcomes(run(SelfTest(600, 600, 0.0), scale=0.92))["power_scale"] == "ok"


def test_command_without_effect() -> None:
    test = run(SelfTest(600, 600, 0.0), effect=0.0)
    checks = {check.key: check for check in test.checks}
    assert checks["effect_charge"].outcome is Outcome.FAILED
    assert checks["effect_charge"].values["reason"] == "none"
    # Waited the longest step before giving up.
    assert checks["response"].outcome is Outcome.UNCLEAR
    assert test.result == "failed"


def test_inverted_set_point() -> None:
    test = run(SelfTest(600, 600, 0.0), effect=-1.0, sign=-1)
    checks = {check.key: check for check in test.checks}
    assert checks["effect_charge"].values["reason"] == "reversed"
    assert checks["effect_discharge"].outcome is Outcome.FAILED


def test_swapped_counters() -> None:
    test = run(SelfTest(600, 600, 0.0), swap_counters=True)
    counters = next(check for check in test.checks if check.key == "counters")
    assert counters.outcome is Outcome.FAILED
    assert counters.values["reason"] == "swapped"


def test_without_counters_or_power_sensor() -> None:
    test = run(SelfTest(600, 600, 0.0), counters=False)
    assert outcomes(test)["counters"] == "skipped"
    assert test.result == "ok"


def test_restless_house_makes_it_unclear() -> None:
    test = run(SelfTest(600, 600, 0.0), effect=0.0, noise_w=600)
    assert outcomes(test)["effect_charge"] == "unclear"
    # Unclear is not passed: the result asks to repeat it.
    assert test.result == "warning"


def test_full_battery_only_discharges() -> None:
    test = run(SelfTest(0, 600, 0.0), soc=99.0)
    assert "effect_charge" not in outcomes(test)
    assert outcomes(test)["effect_discharge"] == "ok"


def test_slow_battery() -> None:
    test = run(SelfTest(600, 600, 0.0), delay_s=90.0)
    assert outcomes(test)["response"] == "warning"
    assert outcomes(test)["effect_charge"] == "ok"


def test_waits_for_the_ramp_out_and_stops() -> None:
    test = SelfTest(600, 600, 0.0)
    assert test.step(0.0, None, ready=False) is None
    assert test.phase is Phase.PREPARE
    assert test.step(1.0, None, ready=True) == 0.0
    assert test.holds_others
    test.stop("cancelled", 5.0)
    assert test.phase is Phase.DONE and test.result == "cancelled"
    assert not test.holds_others


def test_coarse_counters_are_not_a_problem() -> None:
    # Whole kWh: the counters do not move even in the longest step.
    test = run(SelfTest(600, 600, 0.0), counter_digits=0)
    counters = next(check for check in test.checks if check.key == "counters")
    assert counters.outcome is Outcome.SKIPPED
    assert test.result == "ok"


def test_waits_for_a_counter_in_10_wh_steps() -> None:
    # 200 W: 10 Wh take 3 minutes; the step waits for it instead of skipping the check.
    test = run(SelfTest(200, 200, 0.0), soc=50.0)
    assert outcomes(test)["counters"] == "ok"
