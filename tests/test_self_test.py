"""Tests for the self-test of a battery (simulated battery and meter)."""

from collections.abc import Callable
import random

import pytest

from custom_components.slems.phases import Phase as GridPhase
from custom_components.slems.self_test import (
    ABORT_S,
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
    on_phase: int | None = None,
    handover_at: float | None = None,
    handover_until: float = 3600.0,
    release_ok: bool | None = True,
    after_release: Callable[[float], float] | None = None,
    house: Callable[[float], float] | None = None,
) -> SelfTest:
    """Drive a test with a simulated battery: 1 s samples until the end.

    After the release the battery's power is ``after_release(power before)``,
    by default its own logic covering the house (auto) or 0 (standby).
    """
    rng = random.Random(1)
    now = 0.0
    commands: list[tuple[float, float]] = []
    charged = discharged = 100.0
    power = 0.0
    released_at: float | None = None
    before_release = 0.0
    if after_release is None:
        after_release = (lambda _: 0.0) if test.release_state == "standby" else (lambda _: -HOUSE_W)
    while test.phase is not Phase.DONE and now < 3600:
        if test.awaiting_release:
            test.released(release_ok, 1000.0, now)
            released_at = now
        command = commands[-1][1] if commands else 0.0
        # The battery follows its command after the delay.
        actual = next((c for t, c in reversed(commands) if t <= now - delay_s), 0.0) * effect
        if released_at is None:
            before_release = actual
        elif now >= released_at + delay_s:
            actual = after_release(before_release)
        else:
            actual = before_release
        if actual > 0:
            charged += actual / 3600 / 1000
        elif actual < 0:
            discharged += -actual / 3600 / 1000
        soc += actual / 3600 / 5000 * 100
        house_w = house(now) if house is not None else HOUSE_W
        meter = house_w + actual + (rng.uniform(-noise_w, noise_w) if noise_w else 0.0)
        reported = actual * sign * scale
        c, d = (discharged, charged) if swap_counters else (charged, discharged)
        phases = None
        # Two other batteries hand 1300 W over (L1 takes it from L3), the meter sum stays.
        handing = handover_at is not None and handover_at <= now < handover_until
        others = (-1300.0, 0.0) if handing else (0.0, -1300.0)
        if on_phase is not None:
            moved = (-1300.0, 0.0, 1300.0) if handing else (0.0, 0.0, 0.0)
            phases = tuple(
                meter / 3 + (actual * 2 / 3 if k == on_phase else -actual / 3) + moved[k] for k in range(3)
            )
        sample = Sample(
            now, meter, reported, soc,
            round(c, counter_digits) if counters else None, round(d, counter_digits) if counters else None,
            phases, others,
        )
        result = test.step(now, sample, ready=True, wall=1000.0)
        power = result if result is not None else power
        if result is not None and result != command:
            commands.append((now, result))
        now += 1.0
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
    # Nothing found, but not passed either: the result asks to repeat it.
    assert test.result == "unclear"


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


def test_the_phase_of_the_battery() -> None:
    assert run(SelfTest(600, 600, 0.0), on_phase=1).grid_phase() is GridPhase.L2
    # Without the phases of the meter.
    assert run(SelfTest(600, 600, 0.0)).grid_phase() is None
    # Another battery handed over after the first rest: charge and discharge compared directly.
    assert run(SelfTest(600, 600, 0.0), on_phase=2, handover_at=70).grid_phase() is GridPhase.L3
    # It handed back between charge and discharge: nothing to tell.
    assert run(SelfTest(600, 600, 0.0), on_phase=2, handover_at=70, handover_until=200).grid_phase() is None
    # A full battery charges only a little: the discharge alone shows the phase.
    test = run(SelfTest(200, 600, 0.0), on_phase=2)
    assert test.grid_phase() is GridPhase.L3
    assert test.as_dict()["grid_phase"] == "l3"


def test_a_full_battery_is_unclear_not_conspicuous() -> None:
    # Only 200 W charging against a house that changes by a few hundred watts.
    test = run(SelfTest(200, 600, 0.0), noise_w=250)
    checks = {check.key: check for check in test.checks}
    assert checks["effect_charge"].outcome is Outcome.UNCLEAR
    assert checks["effect_charge"].values["reason"] == "small"
    # The noisy charge step does not count for the power checks.
    assert checks["power_scale"].outcome is Outcome.OK
    assert test.result == "unclear"


def test_a_wrong_direction_ends_the_test_at_once() -> None:
    # Inverted set point, the battery reports its power right: no discharge step after the charge.
    test = run(SelfTest(600, 600, 0.0), effect=-1.0)
    checks = {check.key: check for check in test.checks}
    assert checks["effect_charge"].outcome is Outcome.FAILED
    assert checks["effect_charge"].values["reason"] == "reversed"
    assert checks["effect_charge"].values["aborted"]
    assert "effect_discharge" not in checks
    assert test.result == "failed"
    # Released right after ABORT_S (plus the battery's delay), not after STEP_MAX_S.
    assert test.phase_start <= IDLE_S + 4 + ABORT_S + 2


def test_a_wrong_direction_is_judged_late_when_the_battery_disagrees() -> None:
    # The battery's power says otherwise (its sign is inverted too): the steps run to the end.
    test = run(SelfTest(600, 600, 0.0), effect=-1.0, sign=-1)
    checks = {check.key: check for check in test.checks}
    assert "aborted" not in checks["effect_charge"].values
    assert checks["effect_discharge"].values["reason"] == "reversed"


def test_a_too_large_effect_ends_the_test() -> None:
    # A wrong factor of the set point: three times the command.
    test = run(SelfTest(600, 600, 0.0), effect=3.0)
    checks = {check.key: check for check in test.checks}
    assert checks["effect_charge"].values["reason"] == "too_large"
    assert checks["effect_charge"].values["aborted"]
    assert "effect_discharge" not in checks
    # Up to twice is no error (e.g. own limits of the meter averaging).
    assert outcomes(run(SelfTest(600, 600, 0.0), effect=1.8))["effect_charge"] == "ok"


def test_a_switched_on_kettle_does_not_end_the_test() -> None:
    # 2 kW for 30 s while charging: the meter runs far, the battery's own power does not.
    test = run(SelfTest(600, 600, 0.0), house=lambda t: HOUSE_W + (2000.0 if 70 <= t < 100 else 0.0))
    checks = {check.key: check for check in test.checks}
    assert "aborted" not in checks["effect_charge"].values
    assert "effect_discharge" in checks


def test_the_release_is_watched_at_the_meter() -> None:
    test = run(SelfTest(600, 600, 0.0))
    release = next(c for c in test.checks if c.key == "release")
    assert release.outcome is Outcome.OK
    assert release.values["state"] == "auto"
    # Its own logic covered the house: up to 200 W less discharge than the test power.
    assert 120 <= release.values["change_w"] <= 200
    assert release.values["step_w"] == -600


def test_release_to_standby() -> None:
    test = run(SelfTest(600, 600, 0.0, "standby"))
    release = next(c for c in test.checks if c.key == "release")
    assert release.outcome is Outcome.OK and release.values["state"] == "standby"
    # Still in remote control at the last set point although the driver confirmed the release.
    test = run(SelfTest(600, 600, 0.0, "standby"), after_release=lambda before: before)
    release = next(c for c in test.checks if c.key == "release")
    assert (release.outcome, release.values["reason"]) == (Outcome.FAILED, "held")
    assert test.result == "failed"
    # Halfway down.
    test = run(SelfTest(600, 600, 0.0, "standby"), after_release=lambda before: before * 0.6)
    assert next(c for c in test.checks if c.key == "release").values["reason"] == "partial"


def test_release_to_its_own_logic_holding_the_same_power_is_unclear() -> None:
    test = run(SelfTest(600, 600, 0.0), after_release=lambda before: before)
    release = next(c for c in test.checks if c.key == "release")
    assert (release.outcome, release.values["reason"]) == (Outcome.UNCLEAR, "same")
    assert test.result == "unclear"


def test_release_watching_holds_the_others_and_ends() -> None:
    test = SelfTest(600, 0, 0.0)
    now = 0.0
    while not test.awaiting_release:
        test.step(now, Sample(now, HOUSE_W, 0.0, 50.0), ready=True)
        now += 1.0
    assert test.holds_others and not test.commanding
    test.released(True, 500.0, now)
    assert test.holds_others and test.phase is Phase.RELEASE
    # The meter did not show the charge step: nothing to compare, decided at once.
    test.step(now + 1, Sample(now + 1, HOUSE_W, 0.0, 50.0), ready=True, wall=501.0)
    assert test.phase is Phase.DONE and test.finished_at == 501.0
    release = next(c for c in test.checks if c.key == "release")
    assert release.values["reason"] == "no_step"
    assert not test.holds_others


def test_release_not_confirmed_or_not_needed() -> None:
    test = run(SelfTest(600, 600, 0.0), release_ok=False)
    release = next(c for c in test.checks if c.key == "release")
    assert (release.outcome, release.values["reason"]) == (Outcome.FAILED, "readback")
    assert "release" not in outcomes(run(SelfTest(600, 600, 0.0), release_ok=None))
