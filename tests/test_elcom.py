"""Tests for Swiss tariffs from ElCom (values of the city of Zurich, H4, as published)."""

from datetime import date

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems import elcom
from custom_components.slems.tariff import tariff_from_data
from custom_components.slems.tariff_templates import combine
from custom_components.slems.tariff_updates import apply_updates, find_candidates

SUPPLY = elcom.Supply("261", "Zürich", "565", "Elektrizitätswerk der Stadt Zürich")
ROWS = {
    2026: {"period": "2026", "gridusage": "10.735", "energy": "7.884", "energyworking": "7.884", "energyfix": "0.0",
           "meteringyear": "82.8", "charge": "2.0", "aidfee": "2.3", "total": "24.758"},
    2027: {"period": "2027", "gridusage": "11.122", "energy": "7.884", "energyworking": "7.884", "energyfix": "0.0",
           "meteringyear": "91.2", "charge": "1.3", "aidfee": "2.3", "total": "24.632"},
}


@pytest.fixture(autouse=True)
def zurich() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Zurich"))


def test_a_year_is_a_complete_template_matching_the_published_total() -> None:
    template = elcom.template_of(SUPPLY, "H4", ROWS[2026])
    assert template.complete and template.currency == "CHF" and template.family == "ch/elcom/565/261/h4"
    items = {item["name"]: (item["unit"], item["price"]) for item in template.data["items"]}
    assert items == {
        "Energie Arbeitspreis": ("kwh", 7.884),
        "Netznutzung": ("kwh", 10.735),
        "Abgaben an das Gemeinwesen": ("kwh", 2.0),
        "Netzzuschlag": ("kwh", 2.3),
        "Messtarif": ("year", 82.8),
    }
    # The ElCom total of the category (4'500 kWh a year): per kWh parts plus the metering share.
    per_kwh = sum(price for unit, price in items.values() if unit == "kwh")
    assert per_kwh + 82.8 * 100 / 4500 == pytest.approx(24.758, abs=0.002)
    assert template.data["vat"]["import.grid"] == 8.1


def test_the_next_year_is_offered_as_new_prices() -> None:
    levels = [elcom.template_of(SUPPLY, "H4", ROWS[year]) for year in (2026, 2027)]
    _, data = combine([levels[0]])
    assert elcom.supply_of(data["meta"]) == (SUPPLY, "H4")
    candidates = find_candidates(data, levels)["ch/elcom/565/261/h4"]
    assert [(c.kind, c.starts) for c in candidates] == [("update", date(2027, 1, 1))]
    updated, changes = apply_updates(data, candidates, [], levels)
    assert changes.prices["Netznutzung"] == (10.735, 11.122, "kwh")
    tariff = tariff_from_data("Zürich", updated)
    assert {i.name: i.price for i in tariff.active_items(date(2027, 3, 1))}["Messtarif"] == 91.2
    assert {i.name: i.price for i in tariff.active_items(date(2026, 3, 1))}["Messtarif"] == 82.8


def test_search_text_and_category_are_safe() -> None:
    query = elcom.search_query("Zü'rich\" } DROP", 2026)
    assert "zü\\'rich" in query and '"' not in query.split("FILTER")[1]
    with pytest.raises(elcom.ElcomError):
        elcom.levels_query(SUPPLY, "X1", 2026)
