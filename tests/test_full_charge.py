"""Tests for the regular full charge of the batteries."""

from custom_components.slems.full_charge import DAY_S, FullChargeCandidate, due_battery, is_due

NOW = 1_800_000_000.0


def test_due_after_the_interval_or_unknown() -> None:
    assert is_due(None, NOW, 7)
    assert not is_due(NOW - 6 * DAY_S, NOW, 7)
    assert is_due(NOW - 8 * DAY_S, NOW, 7)


def test_one_battery_at_a_time_oldest_first() -> None:
    fresh = FullChargeCandidate("a", "Venus 1", NOW - DAY_S)
    old = FullChargeCandidate("b", "Venus 2", NOW - 9 * DAY_S)
    older = FullChargeCandidate("c", "Venus 3", NOW - 12 * DAY_S)
    assert due_battery([fresh], NOW, 7) is None
    assert due_battery([fresh, old, older], NOW, 7) == "c"
    # Never full since recorded: first; several of them by name.
    unknown_2 = FullChargeCandidate("x", "venus 2", None)
    unknown_1 = FullChargeCandidate("y", "Venus 1", None)
    assert due_battery([older, unknown_2, unknown_1], NOW, 7) == "y"


def test_rest_once_the_due_battery_got_full() -> None:
    from types import SimpleNamespace

    from custom_components.slems.cell_balancing import CellMonitor
    from custom_components.slems.coordinator import ControlSettings, SlemsCoordinator
    from custom_components.slems.full_charge import REST_S

    def battery(battery_id: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            subentry_id=battery_id,
            name=name,
            plannable=True,
            driver=SimpleNamespace(capabilities=SimpleNamespace(controllable=True)),
            cell_monitor=CellMonitor(),
        )

    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    coordinator.batteries = [battery("a", "Venus 1"), battery("b", "Venus 2")]
    coordinator.settings = ControlSettings()
    coordinator.full_charge_battery = None
    coordinator._full_charge_rest = {}
    coordinator._last_full_seen = {}
    snapshot = SimpleNamespace(batteries={"a": object(), "b": object()})
    coordinator._update_full_charge(snapshot, 100.0)
    assert coordinator.full_charge_battery == "a"  # both never full: by name
    # Venus 1 gets full: it rests, and Venus 2 is next.
    coordinator.batteries[0].cell_monitor.observe_full(3.61, 100, 2_000_000_000.0)
    coordinator._update_full_charge(snapshot, 105.0)
    assert coordinator._full_charge_rest == {"a": 105.0 + REST_S}
    assert coordinator.full_charge_battery == "b"
    coordinator._update_full_charge(snapshot, 105.0 + REST_S + 1)
    assert coordinator._full_charge_rest == {}
    # Switched off: no battery is preferred.
    coordinator.settings.regular_full_charge = False
    coordinator._update_full_charge(snapshot, 300.0)
    assert coordinator.full_charge_battery is None
