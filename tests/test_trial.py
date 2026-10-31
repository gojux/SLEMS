"""Tests for the trial of a battery from a Modbus profile: its state, and the
whole trial of the Venus profile against the simulator's register model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.slems import coordinator as coordinator_module
from custom_components.slems.const import ReleaseState
from custom_components.slems.coordinator import SlemsCoordinator
from custom_components.slems.drivers.base import BatteryDriverError
from custom_components.slems.problems import _check_line, _trial_issues
from custom_components.slems.self_test import REGISTER_READ_S, Check, Outcome, Phase, Sample, SelfTest
from custom_components.slems.battery_limits import PowerLimits
from custom_components.slems.trial import (
    OBSERVE_DIRECTION_S,
    OBSERVE_S,
    RETRY_S,
    Observation,
    TrialReason,
    TrialState,
    TrialStatus,
    judge,
)

# The confirm delay is skipped here as well (autouse fixture of the driver tests).
from test_profile_driver import SIM, SimLink, no_confirm_delay, venus_profile_driver  # noqa: F401

HOUSE_W = 400.0


def result(*checks: tuple[str, str], trial: list[tuple[str, str]] | None = None, outcome: str = "ok") -> dict:
    return {
        "result": outcome,
        "checks": [{"key": key, "outcome": value} for key, value in checks],
        "trial": None
        if trial is None
        else {"checks": [{"key": key, "outcome": value} for key, value in trial]},
    }


PASSED_TRIAL = [("trial_quiet", "ok"), ("trial_small_charge", "unclear"), ("trial_late_charge", "ok"), ("trial_release", "ok")]


# --- state ------------------------------------------------------------------------


def test_judging_a_test() -> None:
    assert judge(result(("effect_charge", "ok"), trial=PASSED_TRIAL)) is TrialStatus.PASSED
    assert judge(result(("effect_charge", "warning"), trial=PASSED_TRIAL)) is TrialStatus.PASSED
    # A jumping state of charge at rest is only a mark.
    assert judge(result(trial=[*PASSED_TRIAL, ("trial_quiet_values", "warning")])) is TrialStatus.PASSED
    assert judge(result(("effect_charge", "unclear"), trial=PASSED_TRIAL)) is TrialStatus.UNCLEAR
    assert judge(result(("effect_charge", "ok"), trial=[*PASSED_TRIAL, ("trial_late_discharge", "failed")])) is (
        TrialStatus.FAILED
    )
    # Ended at rest (nothing written): no pass.
    assert judge(result(trial=[("trial_quiet", "unclear")], outcome="unclear")) is TrialStatus.UNCLEAR
    assert judge(result(trial=[("trial_quiet", "failed")], outcome="failed")) is TrialStatus.FAILED
    assert judge({"result": "cancelled"}) is None


def test_a_new_battery_and_a_changed_profile_begin_a_trial() -> None:
    new = TrialState.restore(None, "marstek_venus_e3@1", 100.0)
    assert new.status is TrialStatus.TRIAL and new.reason is TrialReason.NEW and new.due(100.0)
    new.status = TrialStatus.PASSED
    stored = new.as_dict()
    assert TrialState.restore(stored, "marstek_venus_e3@1", 200.0).status is TrialStatus.PASSED
    changed = TrialState.restore(stored, "marstek_venus_e3@2", 200.0)
    assert changed.status is TrialStatus.TRIAL and changed.reason is TrialReason.PROFILE
    assert changed.since == 200.0


def test_a_firmware_update_begins_an_observation_once_passed() -> None:
    state = TrialState("p@1")
    assert not state.device_info_read({"ems_version": "148", "mac_address": "AA"}, 0.0)
    state.status = TrialStatus.PASSED
    # A key not read this time changes nothing, nor do the same values.
    assert not state.device_info_read({"ems_version": "148"}, 10.0)
    assert state.device_info_read({"ems_version": "150", "mac_address": "AA"}, 20.0)
    assert state.status is TrialStatus.OBSERVING and state.reason is TrialReason.FIRMWARE
    assert state.firmware_change == {"ems_version": ["148", "150"]}
    # Not controlled before the registers were read at rest.
    assert state.in_trial and not state.due(10**9)
    # Meanwhile a further change only updates the reference.
    assert not state.device_info_read({"ems_version": "151"}, 30.0)
    assert state.device_info["ems_version"] == "151"
    restored = TrialState.restore(state.as_dict(), "p@1", 40.0)
    assert restored.status is TrialStatus.OBSERVING and restored.observation.started == 20.0
    # A finding: the full trial; the registers changing at rest: failed at once.
    state.observation_failed(50.0)
    assert state.status is TrialStatus.TRIAL and state.observation is None
    assert state.firmware_change == {"ems_version": ["148", "150"]}
    state.observe(60.0, {"ems_version": ["150", "151"]})
    state.observation_failed(70.0, quiet=True)
    assert state.status is TrialStatus.FAILED


def test_unclear_is_retried_failed_only_by_hand() -> None:
    state = TrialState("p@1")
    state.finished(result(("effect_charge", "unclear"), trial=PASSED_TRIAL, outcome="unclear"), 1000.0)
    assert state.status is TrialStatus.UNCLEAR and state.in_trial
    assert not state.due(1000.0 + RETRY_S - 1) and state.due(1000.0 + RETRY_S)
    state.finished(result(("effect_charge", "failed"), trial=PASSED_TRIAL, outcome="failed"), 2000.0)
    assert state.status is TrialStatus.FAILED and not state.due(10**9)
    assert not state.confirm()
    # A test started by hand that passes ends the trial.
    state.finished(result(("effect_charge", "ok"), trial=PASSED_TRIAL), 3000.0)
    assert state.status is TrialStatus.PASSED and not state.in_trial


def test_an_unclear_trial_can_be_confirmed() -> None:
    state = TrialState("p@1", status=TrialStatus.UNCLEAR)
    assert state.confirm()
    assert state.status is TrialStatus.CONFIRMED and not state.in_trial


def test_a_stopped_test_is_started_again_later() -> None:
    state = TrialState("p@1")
    state.finished({"result": "grid_stale"}, 1000.0)
    assert state.status is TrialStatus.TRIAL
    assert not state.due(1000.0) and state.due(1000.0 + RETRY_S)


# --- repairs ----------------------------------------------------------------------


def test_the_repairs_of_a_trial() -> None:
    battery = SimpleNamespace(name="Venus 4", subentry_id="b4", trial=TrialState("p@1"))
    assert _trial_issues(battery) == {
        "profile_trial_b4": ("profile_trial_new", {"name": "Venus 4", "reason": "new", "changes": "–"})
    }
    battery.trial.begin(TrialReason.FIRMWARE, 0.0, {"ems_version": ["148", "150"]})
    assert _trial_issues(battery)["profile_trial_b4"] == (
        "profile_trial_firmware", {"name": "Venus 4", "reason": "firmware", "changes": "ems_version 148 → 150"}
    )
    battery.trial.status, battery.trial.details = TrialStatus.FAILED, "- x"
    assert _trial_issues(battery) == {"profile_trial_failed_b4": ("profile_trial_failed", {"name": "Venus 4", "details": "- x"})}
    battery.trial.status = TrialStatus.PASSED
    assert _trial_issues(battery) == {}


def test_a_check_with_its_registers() -> None:
    def text(key: str, **_: str) -> str:
        return key

    check = Check(
        "trial_late_charge", Outcome.FAILED,
        {"changes": [{"address": 42010, "before": 1, "after": 0, "after_s": 48, "kind": "fallback"}]},
    )
    assert _check_line(text, check) == "self_test_check_trial_late_charge: self_test_outcome_failed (42010: 1 → 0 (48 s))"
    check = Check("trial_release", Outcome.FAILED, {"registers": [{"address": 42000, "value": 21930, "expected": 21947}]})
    assert _check_line(text, check).endswith("(42000 = 21930 (21947))")


# --- coordinator ------------------------------------------------------------------


async def test_a_battery_in_its_trial_is_released_only_after_its_test_wrote() -> None:
    driver = SimpleNamespace(capabilities=SimpleNamespace(controllable=True), release_control=AsyncMock())
    battery = SimpleNamespace(
        name="Venus 4", driver=driver, trial=TrialState("p@1"), release_pending_since=5.0, communication_paused=False
    )
    coordinator = SimpleNamespace(_store=MagicMock(), _data_to_store=dict)
    assert await SlemsCoordinator.async_release_battery(coordinator, battery)
    driver.release_control.assert_not_awaited()
    assert battery.release_pending_since is None
    battery.trial.wrote = True
    assert await SlemsCoordinator.async_release_battery(coordinator, battery)
    driver.release_control.assert_awaited_once()
    assert not battery.trial.wrote


async def test_the_device_information_is_compared_before_control(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {"ems_version": "148"}
    driver = SimpleNamespace(read_device_info=AsyncMock(side_effect=lambda: dict(info)))
    trial = TrialState("p@1", status=TrialStatus.PASSED, device_info={"ems_version": "148"})
    battery = SimpleNamespace(
        name="Venus 4", driver=driver, trial=trial, device_info={}, device_info_read=None,
        device_info_checked=False, release_pending_since=None,
    )
    controls = MagicMock(return_value=False)
    coordinator = SimpleNamespace(
        controls=controls, controller=MagicMock(), _store=MagicMock(), _data_to_store=dict,
        _check_probe_fingerprint=lambda _battery: None,
    )
    await SlemsCoordinator._async_read_device_info(coordinator, battery, 100.0)
    assert battery.device_info_checked and trial.status is TrialStatus.PASSED
    # Every few hours while controlled: a new firmware begins an observation, the battery
    # is handed back first (its registers are read at rest before it is controlled again).
    info["ems_version"] = "150"
    controls.return_value = True
    await SlemsCoordinator._async_read_device_info(coordinator, battery, 100.0 + coordinator_module.DEVICE_INFO_INTERVAL_S)
    assert trial.status is TrialStatus.OBSERVING and trial.reason is TrialReason.FIRMWARE and trial.in_trial
    assert trial.wrote and battery.release_pending_since is not None
    # Not read: not compared, still to be checked.
    battery.device_info_checked = False
    driver.read_device_info = AsyncMock(side_effect=BatteryDriverError("silent"))
    await SlemsCoordinator._async_read_device_info(coordinator, battery, 200.0)
    assert not battery.device_info_checked


# --- the whole trial against the simulator ----------------------------------------


async def run_trial(
    model, release_state: ReleaseState = ReleaseState.AUTO, max_s: float = 1800.0, test: SelfTest | None = None
):
    """A trial of the Venus profile against the simulator's register model, written
    the way the controller does (keep-alive, complete write before the late read
    back, nothing while it runs); the meter is the house plus the battery."""
    link = SimLink(model)
    driver = venus_profile_driver(link, release_state)
    test = test or SelfTest(625, 625, 0.0, release_state.value, driver.trial_spec)
    await driver.connect()
    now = 0.0
    previous: tuple[float, float] | None = None
    while test.phase is not Phase.DONE and now < max_s:
        model.tick()
        if test.awaiting_release:
            ok = None
            if previous is not None:
                try:
                    await driver.release_control()
                    ok = True
                except BatteryDriverError:
                    ok = False
            test.released(ok, 1000.0, now)
        telemetry = await driver.read_telemetry(complete=True)
        registers = await driver.read_control_registers() if test.registers_due(now) else None
        probe = await driver.read_probe() if registers is not None and test.reads_probe else None
        sample = Sample(
            now, HOUSE_W + telemetry.ac_power_w, telemetry.ac_power_w, telemetry.soc_pct,
            telemetry.extra.get("total_charging_energy"), telemetry.extra.get("total_discharging_energy"),
            registers=registers, probe=probe,
        )
        target = test.step(now, sample, ready=True, wall=1000.0)
        if target is not None and test.commanding and not test.writes_paused(now):
            wants = test.wants_refresh
            refresh = previous is None or now - previous[1] >= 60 or wants
            if refresh or round(previous[0]) != round(target):  # type: ignore[index]
                if await driver.apply_power(round(target), refresh=refresh):
                    previous = (target, now if refresh else previous[1])  # type: ignore[index]
                    if wants:
                        test.refreshed(now)
        now += 1.0
    return test, link


async def test_the_steps_of_a_trial_follow_its_plan(monkeypatch) -> None:
    seen: list[tuple[str, int]] = []
    step = SelfTest.step

    def recording(self, *args, **kwargs):
        result = step(self, *args, **kwargs)
        if not seen or seen[-1] != (self.phase.value, self.current_step):
            seen.append((self.phase.value, self.current_step))
        return result

    monkeypatch.setattr(SelfTest, "step", recording)
    test, _ = await run_trial(SIM.VenusModel(5120, 50.0))
    plan = [phase.value for phase in test.plan()]
    assert plan == [
        "prepare", "quiet", "idle", "small_charge", "settle", "charge", "rest",
        "small_discharge", "settle", "discharge", "release", "release_late",
    ]
    # Every phase in its order, the settling once per direction, ending at the last step.
    assert [phase for phase, _ in seen if phase != "done"] == plan[1:]
    assert [number for phase, number in seen if phase != "done"] == list(range(2, len(plan) + 1))
    assert seen[-1] == ("done", len(plan))


async def test_a_trial_of_a_nearly_empty_battery_charges_it_first() -> None:
    model = SIM.VenusModel(5120, 18.0)
    link = SimLink(model)
    driver = venus_profile_driver(link)
    test = SelfTest(625, 625, 0.0, "auto", driver.trial_spec, soc_range=(20.0, 90.0))
    test, _ = await run_trial(model, max_s=3600.0, test=test)
    assert test.phase is Phase.DONE
    assert outcomes(test)["soc_room"] == "ok" and outcomes(test)["effect_discharge"] == "ok"
    assert outcomes(test)["trial_small_discharge"] == "ok"
    assert [phase.value for phase in test.plan()][5:9] == ["charge", "charge_to", "rest", "small_discharge"]


def outcomes(test: SelfTest) -> dict[str, str]:
    return {check.key: check.outcome.value for check in test.checks + test.trial_checks}


def test_the_trial_spec_of_the_venus_profile() -> None:
    spec = venus_profile_driver(SimLink(SIM.VenusModel(5120, 50.0))).trial_spec
    registers = {r.address: r for r in spec.registers}
    assert list(registers) == [42000, 42021, 42020, 42010]
    assert registers[42000].released == 0x55BB and registers[42000].fallback == {0x55BB}
    assert registers[42020].power and not registers[42010].power
    assert spec.small_w == 100 and spec.late_s == 55
    standby = venus_profile_driver(SimLink(SIM.VenusModel(5120, 50.0)), ReleaseState.STANDBY).trial_spec
    # Released to standby the RS485 control stays on.
    assert {r.address: r for r in standby.registers}[42000].released is None


async def test_the_venus_profile_passes_its_trial() -> None:
    test, link = await run_trial(SIM.VenusModel(5120, 50.0))
    assert test.result == "ok", outcomes(test)
    assert judge(test.as_dict()) is TrialStatus.PASSED
    assert set(outcomes(test).values()) == {"ok"}, outcomes(test)


async def test_a_register_written_by_another_system_at_rest_stops_the_trial(tmp_path, monkeypatch) -> None:
    foreign = tmp_path / "foreign"
    foreign.touch()
    monkeypatch.setattr(SIM, "FOREIGN_FILE", str(foreign))
    test, link = await run_trial(SIM.VenusModel(5120, 50.0))
    assert outcomes(test) == {"trial_quiet": "failed", "trial_quiet_values": "ok"}
    assert test.trial_checks[0].values["reason"] == "changing"
    assert link.writes() == []


async def test_a_firmware_time_out_shorter_than_the_keep_alive_fails(monkeypatch) -> None:
    model = SIM.VenusModel(5120, 50.0)
    model.watchdog_s = 30.0
    test, _ = await run_trial(model)
    assert outcomes(test)["trial_late_charge"] == "failed", outcomes(test)
    late = next(c for c in test.trial_checks if c.key == "trial_late_charge")
    assert late.values["reason"] == "fallback"
    assert {change["address"] for change in late.values["changes"]} == {42000, 42010}
    assert all(abs(change["after_s"] - 30) <= REGISTER_READ_S + 1 for change in late.values["changes"])
    assert judge(test.as_dict()) is TrialStatus.FAILED


async def test_a_set_point_cut_by_the_firmware_is_a_warning(monkeypatch) -> None:
    model = SIM.VenusModel(5120, 50.0)
    model.clamp_w = 500
    test, _ = await run_trial(model)
    late = next(c for c in test.trial_checks if c.key == "trial_late_charge")
    assert late.outcome is Outcome.WARNING and late.values["changes"][0]["limit_w"] == 500


async def test_a_pause_is_followed_by_a_comparison_of_the_device_information() -> None:
    # The pause ends by the clock, before the next poll resumes the battery
    # and reads its device information: nothing may be controlled meanwhile.
    battery = SimpleNamespace(
        name="Venus 4", paused_until=None, trial=TrialState("p@1", status=TrialStatus.PASSED),
        device_info_checked=True, driver=SimpleNamespace(close=AsyncMock(), capabilities=SimpleNamespace(controllable=True)),
    )
    coordinator = SimpleNamespace(
        settings=SimpleNamespace(communication_pause_min=5), controller=MagicMock(), _store=MagicMock(),
        _data_to_store=dict, async_update_listeners=MagicMock(),
    )
    await SlemsCoordinator.async_pause_communication(coordinator, battery, "firmware_update")
    assert battery.pause_reason == "firmware_update"
    assert not battery.device_info_checked



# --- observation after a firmware update ------------------------------------------

VENUS_REST = {42000: 0x55BB, 42010: 0, 42020: 0, 42021: 0}


def venus_spec():
    return venus_profile_driver(SimLink(SIM.VenusModel(5120, 50.0))).trial_spec


def quiet_observation() -> Observation:
    observation = Observation(0.0)
    spec = venus_spec()
    for t in range(0, 70, 10):
        observation.quiet(spec, float(t), VENUS_REST)
    assert observation.quiet_ok
    return observation


def test_the_observation_reads_the_registers_at_rest_first() -> None:
    spec = venus_spec()
    observation = Observation(0.0)
    assert observation.registers_due(0.0, None, released=True)
    assert observation.quiet(spec, 0.0, VENUS_REST) is None
    assert not observation.registers_due(5.0, None, released=True)
    check = observation.quiet(spec, 10.0, {**VENUS_REST, 42020: 37})
    assert check.outcome is Outcome.FAILED and check.values["reason"] == "changing"
    assert not observation.quiet_ok
    assert quiet_observation().quiet_ok


def test_the_first_commands_of_a_direction_are_small() -> None:
    observation = quiet_observation()
    limits = PowerLimits(2500.0, 2500.0)
    assert observation.cap(limits, 100.0) == PowerLimits(100.0, 100.0, "power", "power")
    for t in range(12):
        assert observation.observe_power(100.0 + t, 100.0, 98.0, 100.0) is None
    assert observation.shown == {"charge"}
    assert observation.cap(limits, 100.0) == PowerLimits(2500.0, 100.0, None, "power")
    # Time counts only once shown and while it runs as commanded.
    for t in range(12, 40):
        observation.observe_power(100.0 + t, 1000.0, 990.0, 100.0)
    assert observation.charge_s == pytest.approx(28, abs=1)


def test_a_wrong_factor_or_direction_is_a_finding() -> None:
    observation = quiet_observation()
    found = [observation.observe_power(100.0 + t, 100.0, 1000.0, 100.0) for t in range(7)]
    assert found[-1] == {"reason": "too_large", "command_w": 100, "power_w": 1000}
    assert found[4] is None
    observation = quiet_observation()
    found = [observation.observe_power(100.0 + t, -500.0, 480.0, 100.0) for t in range(12)]
    assert found[-1]["reason"] == "reversed"


def test_the_registers_against_what_was_written() -> None:
    spec = venus_spec()
    observation = quiet_observation()
    written = {42000: 0x55AA, 42021: 0, 42020: 625, 42010: 1}
    read = {**written, 42010: 0}
    # Once may be a write the read crossed; twice in a row is a finding.
    assert observation.observe_registers(spec, 100.0, read, written) is None
    assert observation.observe_registers(spec, 110.0, read, written) == {
        "reason": "fallback", "address": 42010, "written": 1, "read": 0
    }
    observation = quiet_observation()
    cut = {**written, 42020: 500}
    assert observation.observe_registers(spec, 100.0, cut, written) is None
    assert observation.observe_registers(spec, 110.0, cut, written) is None
    assert observation.notes == [{"address": 42020, "written": 625, "read": 500}]


def test_the_release_is_checked_and_the_observation_ends() -> None:
    spec = venus_spec()
    observation = quiet_observation()
    observation.charge_s = observation.discharge_s = OBSERVE_DIRECTION_S
    observation.released(1000.0)
    assert observation.registers_due(1000.0, None, released=True)
    assert observation.observe_release(spec, 1005.0, VENUS_REST) is None
    assert not observation.registers_due(1050.0, None, released=True)
    assert observation.registers_due(1095.0, None, released=True)
    assert observation.observe_release(spec, 1095.0, VENUS_REST) is None
    assert observation.release_ok and not observation.release_pending
    assert not observation.complete(OBSERVE_S - 1) and observation.complete(OBSERVE_S)
    wrong = quiet_observation()
    wrong.released(1000.0)
    assert wrong.observe_release(spec, 1005.0, {**VENUS_REST, 42000: 0x55AA})["reason"] == "release"
    # Controlled again before the late read: the next release counts.
    again = quiet_observation()
    again.released(1000.0)
    again.observe_release(spec, 1005.0, VENUS_REST)
    again.taken_over()
    assert not again.release_pending and not again.release_ok


async def test_the_observation_finds_a_firmware_time_out_at_the_simulator() -> None:
    model = SIM.VenusModel(5120, 50.0)
    model.watchdog_s = 30.0
    link = SimLink(model)
    driver = venus_profile_driver(link)
    spec = driver.trial_spec
    await driver.connect()
    observation = quiet_observation()
    found = None
    # Normal operation: a constant command, written completely only every 60 s.
    for t in range(0, 120):
        model.tick()
        if t % 60 == 0:
            assert await driver.apply_power(600, refresh=True)
        if t % 10 == 0:
            count = driver.write_count
            registers = await driver.read_control_registers()
            if driver.write_count == count:
                found = observation.observe_registers(spec, float(t), registers, driver.written_values())
        if found:
            break
    assert found is not None and found["reason"] == "fallback"
    assert 30 <= t < 60
