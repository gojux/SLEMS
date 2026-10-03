"""Tests for tariffs as YAML (made-up tariff)."""

import pytest

from custom_components.slems import tariff_yaml
from custom_components.slems.tariff import Group, Side, Unit, tariff_from_data
from custom_components.slems.tariff_yaml import TariffYamlError, export_yaml, parse_yaml

EXAMPLE = """
format: slems-tariff
version: 1
name: Example Fix 2026
supplier: Example Energy Ltd
country: AT
year: 2026
parts: [energy, grid]
valid_from: 2026-01-01
valid_to: 2026-12-31
source: price sheet 01/2026
vat_pct:
  import: {energy: 20, grid: 20, levies: 20}
  export: {energy: 0}
items:
  - {name: Base price, side: import, group: energy, unit: year, price: 30}
  - name: Energy price
    side: import
    group: energy
    unit: kwh
    price: 15.9   # uncertain: from the bill
  - name: Grid summer noon
    side: import
    group: grid
    unit: kwh
    price: 3.5
    months: [4, 5, 6, 7, 8, 9]
    weekdays: [mon, tue, wed, thu, fri]
    time_from: 10:00
    time_to: "16:00"
    valid_from: 2026-04-01
  - {name: Feed-in, side: export, group: energy, unit: market_month, price: -0.5, month_prices: {2026-01: 8.5}}
"""


def test_parse_and_export_round_trip() -> None:
    name, data = parse_yaml(EXAMPLE)
    assert name == "Example Fix 2026"
    assert data["role"] == "current"
    assert data["meta"] == {
        "supplier": "Example Energy Ltd",
        "country": "AT",
        "year": 2026,
        "parts": ["energy", "grid"],
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "source": "price sheet 01/2026",
    }
    # Missing VAT values take the defaults.
    assert data["vat"]["export.energy"] == 0.0 and data["vat"]["export.grid"] == 20.0
    tariff = tariff_from_data(name, data)
    noon = tariff.items[2]
    assert (noon.side, noon.group, noon.unit) == (Side.IMPORT, Group.GRID, Unit.KWH)
    assert noon.weekdays == frozenset(range(5)) and noon.time_from.hour == 10 and noon.time_to.hour == 16
    assert tariff.items[3].month_prices == (("2026-01", 8.5),)
    # The export reads back the same.
    again = parse_yaml(export_yaml(name, data))
    assert again == (name, data)
    assert export_yaml(name, data).startswith(f"format: slems-tariff\nversion: {tariff_yaml.VERSION}\n")


@pytest.mark.parametrize(
    ("text", "key", "detail"),
    [
        ("format: [", "yaml_syntax", "1"),
        ("name: x", "yaml_not_tariff", ""),
        ("format: slems-tariff\nname: x", "yaml_version_missing", ""),
        ("format: slems-tariff\nversion: 99\nname: x", "yaml_version_newer", "99"),
        ("format: slems-tariff\nversion: 1\nname: x\nitems: []", "yaml_no_items", ""),
        ("format: slems-tariff\nversion: 1\nname: x\nprice: 3\nitems: []", "yaml_unknown_field", "price"),
        (
            "format: slems-tariff\nversion: 1\nname: x\nitems:\n  - {name: a, side: import, group: energy, unit: kWh, price: 1}",
            "yaml_item_invalid",
            "1: unit",
        ),
        (
            "format: slems-tariff\nversion: 1\nname: x\nitems:\n  - {name: a, side: import, group: energy, unit: kwh, preis: 1}",
            "yaml_item_unknown_field",
            "1: preis",
        ),
        (
            "format: slems-tariff\nversion: 1\nname: x\nvalid_from: 2026-02-01\nvalid_to: 2026-01-01\n"
            "items:\n  - {name: a, side: import, group: energy, unit: kwh, price: 1}",
            "yaml_field_invalid",
            "valid_to",
        ),
    ],
)
def test_invalid_files(text: str, key: str, detail: str) -> None:
    with pytest.raises(TariffYamlError) as err:
        parse_yaml(text)
    assert (err.value.key, err.value.detail) == (key, detail)


def test_older_versions_are_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    # As if the format were at version 2 and version 1 called the items "lines".
    def migrate(document: dict) -> dict:
        document = dict(document)
        document["items"] = document.pop("lines")
        return document

    monkeypatch.setattr(tariff_yaml, "VERSION", 2)
    monkeypatch.setitem(tariff_yaml._MIGRATIONS, 1, migrate)
    name, data = parse_yaml(
        "format: slems-tariff\nversion: 1\nname: Old\nlines:\n  - {name: a, side: import, group: energy, unit: kwh, price: 1}"
    )
    assert name == "Old" and len(data["items"]) == 1


def test_percent_items_and_grid_area() -> None:
    name, data = parse_yaml(
        "format: slems-tariff\nversion: 1\nname: Grid area 7\ngrid_operator: Example Grid\n"
        "grid_area: Example area, level 7\nparts: [grid, levies]\nitems:\n"
        "  - {name: Grid use, side: import, group: grid, unit: kwh, price: 6}\n"
        "  - {name: Municipal levy, side: import, group: grid, unit: percent, price: 7}\n"
    )
    assert data["meta"]["grid_area"] == "Example area, level 7"
    assert data["items"][1]["unit"] == "percent"
    assert parse_yaml(export_yaml(name, data)) == (name, data)


def test_market_of_a_monthly_item() -> None:
    _, data = parse_yaml(
        "format: slems-tariff\nversion: 1\nname: PV\nitems:\n"
        "  - {name: Credit, side: export, group: energy, unit: market_month, price: -0.6, market: at-pv}\n"
    )
    assert data["items"][0]["market"] == "at-pv"
    with pytest.raises(TariffYamlError):
        parse_yaml(
            "format: slems-tariff\nversion: 1\nname: PV\nitems:\n"
            "  - {name: Credit, side: export, group: energy, unit: kwh, price: 7, market: at-pv}\n"
        )
