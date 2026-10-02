"""Tests for corrections, newer price levels and successors of tariff templates (made-up templates)."""

from datetime import date
from pathlib import Path

from custom_components.slems.tariff import tariff_from_data
from custom_components.slems.tariff_templates import combine, load_templates
from custom_components.slems.tariff_updates import (
    apply_updates,
    expired,
    find_candidates,
    reset_to_templates,
    template_differences,
)


def origins(data: dict) -> list[tuple[str, str]]:
    return [(o["family"], o["valid_from"]) for o in data["meta"]["templates"]]


def level(valid_from: str, energy: float, extra: str = "", valid_to: str | None = None) -> str:
    end = f"valid_to: {valid_to}\n" if valid_to else ""
    return (
        "format: slems-tariff\nversion: 1\nname: Fix\nsupplier: Example Energy\ncountry: AT\nyear: "
        f"{valid_from[:4]}\nparts: [energy]\nvalid_from: {valid_from}\n{end}source: price sheet\nitems:\n"
        f"  - {{name: Energy, side: import, group: energy, unit: kwh, price: {energy}}}\n"
        "  - {name: Base fee, side: import, group: energy, unit: year, price: 30}\n" + extra
    )


def test_update_to_a_newer_price_level(tmp_path: Path) -> None:
    folder = tmp_path / "at" / "energy" / "example"
    folder.mkdir(parents=True)
    (folder / "2026-01-01_fix.yaml").write_text(
        level("2026-01-01", 10, "  - {name: Bonus, side: import, group: energy, unit: kwh, price: -1}\n", "2026-06-30")
    )
    templates = load_templates([("shipped", tmp_path)])
    name, data = combine(templates)
    assert origins(data) == [("shipped/at/energy/example/fix.yaml", "2026-01-01")]
    assert find_candidates(data, templates) == {}
    assert expired(data, date(2026, 6, 30)) is None
    assert expired(data, date(2026, 7, 1)) == date(2026, 6, 30)
    # The user lowered the base fee and added an own line.
    next(item for item in data["items"] if item["name"] == "Base fee")["price"] = 25
    data["items"].append({"name": "Own", "side": "import", "group": "grid", "unit": "kwh", "price": 5})

    (folder / "2026-07-01_fix.yaml").write_text(level("2026-07-01", 12))
    templates = load_templates([("shipped", tmp_path)])
    candidates = find_candidates(data, templates)["shipped/at/energy/example/fix.yaml"]
    assert [(c.family, c.starts, c.kind) for c in candidates] == [
        ("shipped/at/energy/example/fix.yaml", date(2026, 7, 1), "update")
    ]
    updated, changes = apply_updates(data, candidates, [], templates)
    assert changes.prices == {"Energy": (10, 12, "kwh"), "Base fee": (25, 30, "year")}
    assert changes.removed == ["Bonus"] and changes.own_changes == ["Base fee"]
    assert updated["meta"]["templates"][0]["valid_from"] == "2026-07-01"
    assert "valid_to" not in updated["meta"]
    tariff = tariff_from_data(name, updated)

    def prices(day: date) -> dict[str, float]:
        return {item.name: item.price for item in tariff.active_items(day)}

    # The old prices stay for the past, the new ones apply from the new level on.
    assert prices(date(2026, 6, 30)) == {"Energy": 10, "Bonus": -1, "Base fee": 25, "Own": 5}
    assert prices(date(2026, 7, 1)) == {"Energy": 12, "Base fee": 30, "Own": 5}
    assert find_candidates(updated, templates) == {}


def test_successors_levels_in_between_and_declining(tmp_path: Path) -> None:
    folder = tmp_path / "at" / "energy" / "example"
    folder.mkdir(parents=True)
    ident = "id: at/example/fix\n"
    (folder / "2026-01-01_fix.yaml").write_text(level("2026-01-01", 10).replace("parts:", ident + "parts:"))
    templates = load_templates([("shipped", tmp_path)])
    _, data = combine(templates)
    assert origins(data) == [("at/example/fix", "2026-01-01")]
    # Renamed file, same id: two more price levels; and two successors from 2027.
    (folder / "2026-04-01_fix-renamed.yaml").write_text(level("2026-04-01", 11).replace("parts:", ident + "parts:"))
    (folder / "2026-07-01_fix-renamed.yaml").write_text(level("2026-07-01", 12).replace("parts:", ident + "parts:"))
    for name, price in (("fix-12", 13), ("fix-24", 14)):
        (folder / f"2027-01-01_{name}.yaml").write_text(
            level("2027-01-01", price).replace("parts:", f"id: at/example/{name}\nreplaces: [at/example/fix]\nparts:")
        )
    templates = load_templates([("shipped", tmp_path)])
    candidates = find_candidates(data, templates)["at/example/fix"]
    assert [(c.family, c.kind, len(c.levels)) for c in candidates] == [
        ("at/example/fix", "update", 2),
        ("at/example/fix-12", "successor", 1),
        ("at/example/fix-24", "successor", 1),
    ]
    # Taking over the newer levels of the same tariff: both in turn.
    updated, changes = apply_updates(data, candidates[:1], [], templates)
    assert changes.prices["Energy"] == (10, 12, "kwh")
    tariff = tariff_from_data("Fix", updated)
    assert [i.price for i in tariff.active_items(date(2026, 5, 1)) if i.name == "Energy"] == [11]
    # The successors are still offered (they replace the tariff from 2027).
    assert [c.family for c in find_candidates(updated, templates)["at/example/fix"]] == [
        "at/example/fix-12", "at/example/fix-24"
    ]
    # Taking over a successor: the tariff follows it from then on.
    following, _ = apply_updates(updated, find_candidates(updated, templates)["at/example/fix"][1:2], [], templates)
    assert origins(following) == [("at/example/fix-24", "2027-01-01")]
    assert find_candidates(following, templates) == {}
    # Declining: not offered again until a newer level comes.
    declined, _ = apply_updates(updated, [], find_candidates(updated, templates)["at/example/fix"], templates)
    assert declined["meta"]["declined"] == {"at/example/fix-12": "2027-01-01", "at/example/fix-24": "2027-01-01"}
    assert find_candidates(declined, templates) == {}
    (folder / "2027-07-01_fix-12.yaml").write_text(
        level("2027-07-01", 15).replace("parts:", "id: at/example/fix-12\nparts:")
    )
    again = find_candidates(declined, load_templates([("shipped", tmp_path)]))["at/example/fix"]
    assert [(c.family, len(c.levels)) for c in again] == [("at/example/fix-12", 2)]


def test_correction_reset_and_declining_a_correction(tmp_path: Path) -> None:
    folder = tmp_path / "at" / "energy" / "example"
    folder.mkdir(parents=True)
    path = folder / "2026-01-01_fix.yaml"
    path.write_text(level("2026-01-01", 1.05).replace("parts:", "id: at/example/fix\nparts:"))
    _, data = combine(load_templates([("shipped", tmp_path)]))
    assert template_differences(data) == []
    # The user corrected the base fee himself and removed nothing.
    next(item for item in data["items"] if item["name"] == "Base fee")["price"] = 31
    assert template_differences(data) == ["Base fee"]
    # The template is corrected: misplaced decimal point, wrong base fee, an item added.
    path.write_text(
        level("2026-01-01", 10.5, "  - {name: Levy, side: import, group: levies, unit: kwh, price: 1}\n").replace(
            "parts:", "id: at/example/fix\nparts:"
        ).replace("price: 30}", "price: 32}")
    )
    templates = load_templates([("shipped", tmp_path)])
    candidates = find_candidates(data, templates)["at/example/fix"]
    assert [(c.kind, c.starts) for c in candidates] == [("correction", date(2026, 1, 1))]
    corrected, changes = apply_updates(data, candidates, [], templates)
    assert changes.prices == {"Energy": (1.05, 10.5, "kwh"), "Levy": (None, 1, "kwh")}
    assert changes.own_kept == ["Base fee"]
    prices = {item["name"]: (item["price"], item["valid_from"]) for item in corrected["items"]}
    # The dates stay (the correction applies to the past as well); own change kept.
    assert prices == {"Energy": (10.5, None), "Base fee": (31, None), "Levy": (1, None)}
    assert find_candidates(corrected, templates) == {}
    # Reset: the base fee back to the template value; own items would stay.
    assert template_differences(corrected) == ["Base fee"]
    corrected["items"].append({"name": "Own", "side": "import", "group": "grid", "unit": "kwh", "price": 5})
    reset = reset_to_templates(corrected)
    assert {item["name"]: item["price"] for item in reset["items"]} == {
        "Energy": 10.5, "Base fee": 32, "Levy": 1, "Own": 5
    }
    assert template_differences(reset) == []
    # Declining a correction: not offered again until the template changes once more.
    declined, _ = apply_updates(data, [], candidates, templates)
    assert find_candidates(declined, templates) == {}
    path.write_text(level("2026-01-01", 10.6).replace("parts:", "id: at/example/fix\nparts:"))
    assert [c.kind for c in find_candidates(declined, load_templates([("shipped", tmp_path)]))["at/example/fix"]] == [
        "correction"
    ]
