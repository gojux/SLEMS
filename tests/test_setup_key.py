"""A change of tariffs needs no reload, other changes do."""

from types import SimpleNamespace

from custom_components.slems import _setup_key


def entry(tariff_price: float, consumer_power: int) -> SimpleNamespace:
    subentries = {
        "t": SimpleNamespace(subentry_id="t", subentry_type="tariff", title="Tariff", data={"items": [{"price": tariff_price}]}),
        "c": SimpleNamespace(subentry_id="c", subentry_type="consumer", title="Rod", data={"max_power_w": consumer_power}),
    }
    return SimpleNamespace(data={"grid": "sensor.grid"}, options={}, subentries=subentries)


def test_only_non_tariff_changes_change_the_setup_key() -> None:
    assert _setup_key(entry(10, 3000)) == _setup_key(entry(12, 3000))
    assert _setup_key(entry(10, 3000)) != _setup_key(entry(10, 3500))
