"""Tests for the self-test of a battery (simulated battery and meter)."""

from collections.abc import Callable
import dataclasses
import random

import pytest

from custom_components.slems.phases import Phase as GridPhase
from custom_components.slems.self_test import (
    ABORT_S,
    HOLD_S,
    IDLE_S,
    QUIET_S,
    SETTLE_S,
    STEP_MAX_S,
    Outcome,
    Phase,
    Sample,
    SelfTest,
    TrialRegister,
    TrialSpec,
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
    registers: Callable[[float, float, bool], dict[int, float | None]] | None = None,
    soc_read: Callable[[float, float], float] | None = None,
    probe: Callable[[float], float] | None = None,
) -> SelfTest:
    """Drive a test with a simulated battery: 1 s samples until the end.

    After the release the battery's power is ``after_release(power before)``,
    by default its own logic covering the house (auto) or 0 (standby). A
    trial reads ``registers(now, command, released)`` whenever it asks for
    them; a complete write it asks for happens at once. ``soc_read(now, soc)``
    is the state of charge as reported, ``probe(now)`` the probe register.
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
        read = probed = None
        if registers is not None and test.registers_due(now):
            read = registers(now, command, released_at is not None)
            if probe is not None and test.reads_probe:
                probed = probe(now)
        sample = Sample(
            now, meter, reported, soc_read(now, soc) if soc_read is not None else soc,
            round(c, counter_digits) if counters else None, round(d, counter_digits) if counters else None,
            phases, others, read, probed,
        )
        result = test.step(now, sample, ready=True, wall=1000.0)
        if test.wants_refresh:
            test.refreshed(now)
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


def test_the_plan_of_a_test_without_trial() -> None:
    test = SelfTest(600, 600, 0.0)
    assert [phase.value for phase in test.plan()] == ["prepare", "idle", "charge", "rest", "discharge", "release"]
    assert test.current_step == 1
    assert [phase.value for phase in SelfTest(0, 600, 0.0).plan()] == ["prepare", "idle", "discharge", "release"]
    run(test)
    assert test.current_step == 6


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


# --- trial of a profile -------------------------------------------------------

RS485, MODE, CHARGE_W, DISCHARGE_W = 42000, 42010, 42020, 42021
ON, OFF = 0x55AA, 0x55BB


def venus_spec(keepalive_s: float | None = 60.0) -> TrialSpec:
    """The checks of the Venus profile (release to auto)."""
    return TrialSpec(
        (
            TrialRegister(RS485, lambda v: v in (ON, OFF), OFF, frozenset({OFF, ON})),
            TrialRegister(MODE, lambda v: 0 <= v <= 2, 0, frozenset({0})),
            TrialRegister(CHARGE_W, lambda v: 0 <= v <= 2500, 0, frozenset({0}), power=True),
            TrialRegister(DISCHARGE_W, lambda v: 0 <= v <= 2500, 0, frozenset({0}), power=True),
        ),
        keepalive_s=keepalive_s,
    )


def venus_registers(_now: float, command: float, released: bool) -> dict[int, float | None]:
    """The registers as a Venus holds them for the last command (at rest for 0 W)."""
    if released or command == 0:
        return {RS485: OFF, MODE: 0, CHARGE_W: 0, DISCHARGE_W: 0}
    return {RS485: ON, MODE: 1 if command > 0 else 2, CHARGE_W: max(0.0, command), DISCHARGE_W: max(0.0, -command)}


def trial_outcomes(test: SelfTest) -> dict[str, str]:
    return {check.key: check.outcome.value for check in test.trial_checks}


def trial_check(test: SelfTest, key: str):
    return next(check for check in test.trial_checks if check.key == key)


def late_after(test: SelfTest, phase: Phase, seconds: float, now: float) -> bool:
    record = test._records.get(phase)
    return record is not None and record.late_start is not None and now >= record.late_start + seconds


def test_a_plain_self_test_reads_no_registers() -> None:
    test = run(SelfTest(600, 600, 0.0), registers=lambda *_: pytest.fail("no registers without a trial"))
    assert test.result == "ok"
    assert test.as_dict()["trial"] is None


def test_a_correct_profile_passes_its_trial() -> None:
    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), registers=venus_registers)
    assert test.result == "ok", (outcomes(test), test.trial_checks)
    assert trial_outcomes(test) == {
        "trial_quiet": "ok",
        "trial_quiet_values": "ok",
        "trial_small_charge": "ok",
        "trial_late_charge": "ok",
        "trial_small_discharge": "ok",
        "trial_late_discharge": "ok",
        "trial_release": "ok",
    }
    assert set(outcomes(test).values()) == {"ok"}
    # Read every 10 s at rest; the late read back lasts until shortly before the keep-alive.
    assert trial_check(test, "trial_quiet").values["reads"] == QUIET_S / 10 + 1
    assert trial_check(test, "trial_late_charge").values["window_s"] == 55
    data = test.as_dict()["trial"]
    assert data["result"] == "ok" and data["self_test_result"] == "ok"
    reads = test.register_reads()
    assert len(reads["late"]["charge"]) >= 5 and len(reads["release"]) == 2


def test_the_small_step_comes_first_and_settles() -> None:
    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), registers=venus_registers)
    assert test._records[Phase.SMALL_CHARGE].target_w == 100
    assert test._records[Phase.SMALL_DISCHARGE].target_w == -100
    # The last settling (0 W) before the full discharge.
    assert test._records[Phase.DISCHARGE].start - test._records[Phase.SETTLE].start == pytest.approx(SETTLE_S, abs=1)


def test_a_register_changing_at_rest_ends_the_trial_without_writing() -> None:
    def registers(now: float, command: float, released: bool) -> dict[int, float | None]:
        assert command == 0, "nothing may be written"
        return {**venus_registers(now, command, released), CHARGE_W: 0 if now < 30 else 37}

    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), registers=registers)
    assert test.result == "failed"
    check = trial_check(test, "trial_quiet")
    assert check.values["reason"] == "changing"
    assert check.values["registers"][0]["address"] == CHARGE_W
    assert test.checks == [] and test.as_dict()["trial"]["self_test_result"] is None
    assert Phase.IDLE not in test._records


def test_a_register_outside_its_range_at_rest_ends_the_trial() -> None:
    test = run(
        SelfTest(600, 600, 0.0, trial=venus_spec()),
        registers=lambda now, c, r: {**venus_registers(now, c, r), MODE: 5600},
    )
    check = trial_check(test, "trial_quiet")
    assert check.outcome is Outcome.FAILED and check.values["reason"] == "outside_rest"
    assert Phase.IDLE not in test._records


def test_a_jumping_state_of_charge_or_probe_at_rest_is_a_warning() -> None:
    # A SoC register shifted onto the battery power: 0.1 % per W of its own logic.
    def shifted(now: float, _soc: float) -> float:
        return 12.0 if now < 25 else 48.5

    spec = dataclasses.replace(venus_spec(), probe_range=(0, 1000))
    test = run(SelfTest(600, 600, 0.0, trial=spec), registers=venus_registers, soc_read=shifted,
               probe=lambda now: 120 if now < 25 else 485)
    check = trial_check(test, "trial_quiet_values")
    assert check.outcome is Outcome.WARNING
    assert [(r["address"], r["before"], r["after"]) for r in check.values["registers"]] == [
        ("soc_pct", 12, 48.5), ("probe", 120, 485),
    ]
    # Only a mark: the trial goes on and writes.
    assert Phase.CHARGE in test._records and test.result == "warning"

    calm = run(SelfTest(600, 600, 0.0, trial=spec), registers=venus_registers, probe=lambda now: 500 + now / 10)
    assert trial_check(calm, "trial_quiet_values").outcome is Outcome.OK


def test_unreadable_registers_at_rest_are_unclear() -> None:
    test = run(
        SelfTest(600, 600, 0.0, trial=venus_spec()),
        registers=lambda now, c, r: {**venus_registers(now, c, r), MODE: None},
    )
    assert trial_outcomes(test) == {"trial_quiet": "unclear", "trial_quiet_values": "ok"}
    assert test.result == "unclear"


def test_a_small_step_ten_times_too_large_ends_the_test() -> None:
    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), effect=10.0, registers=venus_registers)
    check = trial_check(test, "trial_small_charge")
    assert check.outcome is Outcome.FAILED and check.values["reason"] == "too_large"
    assert check.values["aborted"]
    # Ended before the full power was sent, then released and watched.
    assert Phase.CHARGE not in test._records
    assert test.result == "failed"
    assert "trial_release" in trial_outcomes(test)


def test_the_firmware_falling_back_fails_the_late_read_back() -> None:
    test = SelfTest(600, 600, 0.0, trial=venus_spec())

    def registers(now: float, command: float, released: bool) -> dict[int, float | None]:
        values = venus_registers(now, command, released)
        if late_after(test, Phase.CHARGE, 30, now) and test.phase is Phase.CHARGE:
            values[MODE] = 0
        return values

    run(test, registers=registers)
    check = trial_check(test, "trial_late_charge")
    assert check.outcome is Outcome.FAILED and check.values["reason"] == "fallback"
    change = check.values["changes"][0]
    assert (change["address"], change["before"], change["after"]) == (MODE, 1, 0)
    assert change["after_s"] == pytest.approx(30, abs=10)


def test_a_power_cut_by_the_firmware_is_a_warning_with_its_limit() -> None:
    test = SelfTest(600, 600, 0.0, trial=venus_spec())

    def registers(now: float, command: float, released: bool) -> dict[int, float | None]:
        values = venus_registers(now, command, released)
        if late_after(test, Phase.CHARGE, 20, now) and test.phase is Phase.CHARGE:
            values[CHARGE_W] = 500
        return values

    run(test, registers=registers)
    check = trial_check(test, "trial_late_charge")
    assert check.outcome is Outcome.WARNING and check.values["reason"] == "clamped"
    assert check.values["changes"][0]["limit_w"] == 500
    assert test.result == "warning"


def test_nothing_is_written_during_the_late_read_back() -> None:
    test = SelfTest(600, 600, 0.0, trial=venus_spec(keepalive_s=30.0))
    paused: list[float] = []

    def registers(now: float, command: float, released: bool) -> dict[int, float | None]:
        if test.writes_paused(now):
            paused.append(now)
        return venus_registers(now, command, released)

    run(test, registers=registers)
    record = test._records[Phase.CHARGE]
    assert trial_check(test, "trial_late_charge").values["window_s"] == 25
    charge_paused = [t for t in paused if record.start <= t < test._records[Phase.REST].start]
    assert min(charge_paused) >= record.late_start and max(charge_paused) < record.late_start + 25
    assert not test.writes_paused(record.late_start + 25)


def test_a_wrong_release_fails() -> None:
    def registers(now: float, command: float, released: bool) -> dict[int, float | None]:
        values = venus_registers(now, command, released)
        if released:
            values[RS485] = ON
        return values

    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), registers=registers)
    check = trial_check(test, "trial_release")
    assert check.outcome is Outcome.FAILED and check.values["reason"] == "wrong"
    assert check.values["registers"][0] == {"address": RS485, "value": ON, "expected": OFF, "after_s": 0}


def test_a_release_not_confirmed_fails_the_trial_release() -> None:
    test = run(SelfTest(600, 600, 0.0, trial=venus_spec()), registers=venus_registers, release_ok=False)
    assert trial_check(test, "trial_release").values["reason"] == "readback"
