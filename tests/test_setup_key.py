"""A change of tariffs needs no reload, other changes do; the caches of the
old tariffs are forgotten."""

from types import SimpleNamespace

from custom_components.slems import _setup_key
from custom_components.slems.coordinator import SlemsCoordinator


def entry(tariff_price: float, consumer_power: int) -> SimpleNamespace:
    subentries = {
        "t": SimpleNamespace(subentry_id="t", subentry_type="tariff", title="Tariff", data={"items": [{"price": tariff_price}]}),
        "c": SimpleNamespace(subentry_id="c", subentry_type="consumer", title="Rod", data={"max_power_w": consumer_power}),
    }
    return SimpleNamespace(data={"grid": "sensor.grid"}, options={}, subentries=subentries)


def test_only_non_tariff_changes_change_the_setup_key() -> None:
    assert _setup_key(entry(10, 3000)) == _setup_key(entry(12, 3000))
    assert _setup_key(entry(10, 3000)) != _setup_key(entry(10, 3500))


def test_tariff_change_forgets_what_was_computed_with_the_old_tariffs() -> None:
    refreshed = []
    coordinator = SimpleNamespace(
        _import_price_cache={"x": 1},
        _grid_charge_cache={"y": 2},
        tariff_comparison_cache=(0.0, (), {}),
        _wanted_references=lambda: None,
        market_prices=SimpleNamespace(references_update=1, refresh=lambda: refreshed.append(True)),
    )
    SlemsCoordinator.tariffs_changed(coordinator)
    assert coordinator.tariff_comparison_cache is None
    assert not coordinator._import_price_cache and not coordinator._grid_charge_cache
    assert refreshed
