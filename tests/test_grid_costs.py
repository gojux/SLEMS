"""Tests for the running costs of the grid import and credit of the feed-in."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.const import CONF_GRID_IMPORT_ENERGY_ENTITY
from custom_components.slems.grid_costs import MAX_WAIT_S, CostCounter, fixed_per_day, spread_cost
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def ts(day: int, hour: int, minute: int = 0, month: int = 11) -> float:
    return datetime(2026, month, day, hour, minute, tzinfo=dt_util.get_default_time_zone()).timestamp()


def flat(ct: float):
    return lambda slot: ct


def test_energy_is_priced_per_quarter_hour() -> None:
    prices = {int(ts(3, 18)): 20.0, int(ts(3, 18, 15)): 40.0}
    # 1 kWh over 18:10 to 18:20: half of it in each quarter hour.
    assert spread_cost(1.0, ts(3, 18, 10), ts(3, 18, 20), prices.get) == pytest.approx(0.30)
    assert spread_cost(1.0, ts(3, 18, 10), ts(3, 18, 40), prices.get) is None


def test_counter_follows_the_reported_values() -> None:
    counter = CostCounter()
    counter.add_energy("sensor.import", 100.0, ts(3, 18), flat(30.0))
    assert counter.total == 0.0  # the first value is the starting point
    counter.add_energy("sensor.import", 102.0, ts(3, 18, 5), flat(30.0))
    assert counter.total == pytest.approx(0.60)
    # Nothing new reported.
    counter.add_energy("sensor.import", 103.0, ts(3, 18, 5), flat(30.0))
    assert counter.total == pytest.approx(0.60)
    # A small step back waits until it is made up, a reset starts anew.
    counter.add_energy("sensor.import", 101.9, ts(3, 18, 10), flat(30.0))
    counter.add_energy("sensor.import", 103.0, ts(3, 18, 15), flat(30.0))
    assert counter.total == pytest.approx(0.90)
    counter.add_energy("sensor.import", 0.5, ts(3, 18, 20), flat(30.0))
    counter.add_energy("sensor.import", 1.5, ts(3, 18, 25), flat(30.0))
    assert counter.total == pytest.approx(1.20)
    # Another counter is a new starting point.
    counter.add_energy("sensor.other", 500.0, ts(3, 18, 30), flat(30.0))
    assert counter.total == pytest.approx(1.20)


def test_energy_waits_for_a_missing_price() -> None:
    counter = CostCounter()
    known: dict[int, float] = {}
    counter.add_energy("sensor.import", 10.0, ts(3, 18), known.get)
    counter.add_energy("sensor.import", 11.0, ts(3, 18, 10), known.get)
    assert counter.total == 0.0 and counter.energy_kwh == 10.0
    known[int(ts(3, 18))] = 25.0
    counter.add_energy("sensor.import", 12.0, ts(3, 18, 14), known.get)
    assert counter.total == pytest.approx(0.50) and counter.unpriced_kwh == 0.0
    # Without a price for too long the energy is left out.
    counter.add_energy("sensor.import", 15.0, ts(3, 18, 14) + MAX_WAIT_S + 60, lambda slot: None)
    assert counter.total == pytest.approx(0.50) and counter.unpriced_kwh == pytest.approx(3.0)
    assert counter.energy_kwh == 15.0


def test_fixed_costs_by_the_time_passed() -> None:
    counter = CostCounter()
    per_day = {date(2026, 11, 3): 1.0, date(2026, 11, 4): 2.0}
    counter.add_fixed(ts(3, 12), per_day.__getitem__)
    assert counter.total == 0.0  # from now on
    counter.add_fixed(ts(4, 6), per_day.__getitem__)
    assert counter.total == pytest.approx(0.5 * 1.0 + 0.25 * 2.0)
    # The day of the clock change in autumn has 25 hours.
    long_day = CostCounter()
    long_day.add_fixed(ts(25, 0, month=10), lambda day: 1.0)
    long_day.add_fixed(ts(25, 12, month=10), lambda day: 1.0)
    assert long_day.total == pytest.approx(13 / 25)


def test_fixed_costs_of_a_day_with_the_minimum_power() -> None:
    tariff = Tariff(
        "Grid",
        Role.CURRENT,
        (
            TariffItem("Base fee", Side.IMPORT, Group.GRID, Unit.YEAR, 36.5),
            TariffItem("Power", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 3.0, min_kw=2.0),
            TariffItem("Meter", Side.EXPORT, Group.GRID, Unit.YEAR, 7.3),
        ),
        {(Side.IMPORT, Group.GRID): 20.0},
    )
    # 0.10 € of the base fee and a 30th of 2 kW × 3 €, with 20 % VAT.
    assert fixed_per_day(tariff, Side.IMPORT, date(2026, 11, 10)) == pytest.approx((0.1 + 6.0 / 30) * 1.2)
    assert fixed_per_day(tariff, Side.IMPORT, date(2026, 12, 10)) == pytest.approx((0.1 + 6.0 / 31) * 1.2)
    assert fixed_per_day(tariff, Side.EXPORT, date(2026, 11, 10)) == pytest.approx(-0.02)
    # Without a minimum power nothing of the power price is due in advance.
    no_minimum = Tariff("Grid", Role.CURRENT, (TariffItem("Power", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 3.0),), {})
    assert fixed_per_day(no_minimum, Side.IMPORT, date(2026, 11, 10)) == 0.0


def test_power_price_above_the_minimum_is_added_when_the_peak_rises() -> None:
    counter = CostCounter()
    counter.add_peak("2026-11", 1.2)
    assert counter.total == 0.0  # as billed so far: the starting point
    counter.add_peak("2026-11", 4.8)
    counter.add_peak("2026-11", 3.0)
    assert counter.total == pytest.approx(3.6)
    # A new month starts at the minimum.
    counter.add_peak("2026-12", 0.0)
    counter.add_peak("2026-12", 0.8)
    assert counter.total == pytest.approx(3.6 + 0.8)


def test_pause_keeps_the_total_and_round_trip() -> None:
    counter = CostCounter()
    counter.add_energy("sensor.import", 100.0, ts(3, 18), flat(30.0))
    counter.add_energy("sensor.import", 101.0, ts(3, 18, 5), flat(30.0))
    counter.add_fixed(ts(3, 18), lambda day: 1.0)
    counter.add_peak("2026-11", 7.2)
    assert CostCounter.from_dict(counter.as_dict()) == counter
    counter.pause()
    assert counter.total == pytest.approx(0.30)
    # Nothing is added for the time in between.
    counter.add_energy("sensor.import", 150.0, ts(20, 18), flat(30.0))
    counter.add_fixed(ts(20, 18), lambda day: 1.0)
    counter.add_peak("2026-11", 20.0)
    assert counter.total == pytest.approx(0.30)
    assert CostCounter.from_dict(None) == CostCounter()


def _coordinator(tariff: Tariff | None, options: dict, enabled: set[str]):
    from custom_components.slems.coordinator import SlemsCoordinator

    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    coordinator.config_entry = SimpleNamespace(entry_id="entry", options=options, data={})
    coordinator.grid_costs = {side: CostCounter() for side in Side}
    coordinator.grid_cost_missing = frozenset()
    coordinator._contract_cache = (tariff,)
    coordinator._slot_prices = (None, {})
    coordinator.market_prices = SimpleNamespace(last_update=None, price_at=lambda moment: None, references={})
    coordinator._own_sensor_enabled = lambda key: key in enabled
    coordinator.peak_state = lambda now: None
    coordinator.saves = []
    coordinator._schedule_save = coordinator.saves.append
    coordinator.states = {}
    coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: coordinator.states.get(entity_id)))
    return coordinator


def _energy(kwh: float, reported: float) -> SimpleNamespace:
    return SimpleNamespace(
        state=str(kwh),
        attributes={"unit_of_measurement": "kWh"},
        last_reported=dt_util.utc_from_timestamp(reported),
    )


def test_coordinator_counts_only_for_enabled_sensors() -> None:
    tariff = Tariff(
        "Fix",
        Role.CURRENT,
        (
            TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 25.0),
            TariffItem("Credit", Side.EXPORT, Group.ENERGY, Unit.KWH, 8.0),
        ),
        {(Side.IMPORT, Group.ENERGY): 20.0},
    )
    coordinator = _coordinator(tariff, {CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.import"}, {"import_cost"})
    assert coordinator.kwh_price_now(Side.IMPORT) == pytest.approx(30.0)
    assert coordinator.kwh_price_now(Side.EXPORT) == pytest.approx(8.0)
    assert coordinator.grid_cost_ready(Side.IMPORT) and not coordinator.grid_cost_ready(Side.EXPORT)

    coordinator.states["sensor.import"] = _energy(100.0, ts(3, 18))
    coordinator._update_grid_costs()
    coordinator.states["sensor.import"] = _energy(102.0, ts(3, 18, 5))
    coordinator._update_grid_costs()
    assert coordinator.grid_costs[Side.IMPORT].total == pytest.approx(0.60)
    assert coordinator.saves and not coordinator.grid_cost_missing

    # The export sensor is enabled without its counter: reported, nothing counted.
    coordinator._own_sensor_enabled = lambda key: True
    coordinator._update_grid_costs()
    assert coordinator.grid_cost_missing == {Side.EXPORT}
    assert coordinator.grid_costs[Side.EXPORT] == CostCounter()

    # Disabled: the counter forgets where it stood and keeps its total.
    coordinator._own_sensor_enabled = lambda key: False
    coordinator._update_grid_costs()
    assert not coordinator.grid_cost_missing
    paused = coordinator.grid_costs[Side.IMPORT]
    assert paused.total == pytest.approx(0.60) and paused.energy_kwh is None

    # A tariff of one side only has no price for the other.
    one_sided = _coordinator(Tariff("Fix", Role.CURRENT, tariff.items[:1], {}), {}, set())
    assert one_sided.kwh_price_now(Side.EXPORT) is None
    assert _coordinator(None, {}, set()).kwh_price_now(Side.IMPORT) is None
