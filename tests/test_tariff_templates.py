"""Tests for tariff templates (made-up templates; the shipped ones are only validated)."""

from pathlib import Path

import pytest

from custom_components.slems.tariff import tariff_from_data
from custom_components.slems.tariff_templates import (
    SHIPPED_DIR,
    TemplatePartTwice,
    combine,
    label,
    load_templates,
)

ENERGY = """format: slems-tariff
version: 1
name: Fix {year}
supplier: Example Energy
country: at
year: {year}
parts: [energy]
valid_from: {year}-01-01
source: price sheet
vat_pct: {{import: {{energy: 20}}, export: {{energy: 0}}}}
items:
  - {{name: Energy, side: import, group: energy, unit: kwh, price: 10}}
"""
GRID = """format: slems-tariff
version: 1
name: Example area level 7
grid_operator: Example Grid
grid_area: level 7
country: AT
year: 2026
parts: [grid, levies]
valid_from: 2026-02-01
valid_to: 2026-12-31
source: grid fee ordinance
vat_pct: {import: {grid: 20, levies: 20, energy: 99}}
items:
  - {name: Grid use, side: import, group: grid, unit: kwh, price: 6}
  - {name: Levy, side: import, group: levies, unit: year, price: 20}
"""
NAMES = {"energy": "Energy", "grid": "Grid", "levies": "Levies"}


@pytest.fixture
def folders(tmp_path: Path) -> list[tuple[str, Path]]:
    shipped = tmp_path / "shipped"
    (shipped / "at").mkdir(parents=True)
    (shipped / "at" / "fix-2025.yaml").write_text(ENERGY.format(year=2025))
    (shipped / "at" / "fix-2026.yaml").write_text(ENERGY.format(year=2026))
    (shipped / "at" / "grid.yaml").write_text(GRID)
    (shipped / "at" / "broken.yaml").write_text("format: slems-tariff\nversion: 1\n")
    own = tmp_path / "own"
    own.mkdir()
    (own / "mine.yaml").write_text(ENERGY.format(year=2024).replace("Fix 2024", "Mine"))
    return [("shipped", shipped), ("own", own), ("missing", tmp_path / "nope")]


def test_load_sorted_by_part_provider_and_year(folders) -> None:
    templates = load_templates(folders)
    # The broken file is skipped; energy before grid, newest year first.
    assert [t.key for t in templates] == [
        "shipped/at/fix-2026.yaml",
        "shipped/at/fix-2025.yaml",
        "own/mine.yaml",
        "shipped/at/grid.yaml",
    ]
    assert {t.country for t in templates} == {"AT"}
    assert label(templates[0], NAMES, "own") == "Energy · Example Energy – Fix 2026 (2026)"
    assert label(templates[2], NAMES, "own") == "Energy · Example Energy – Mine (2024) · own"
    assert label(templates[3], NAMES, "own") == "Grid + Levies · Example Grid – Example area level 7 (2026)"


def test_combine_parts_into_one_tariff(folders) -> None:
    energy, _, _, grid = load_templates(folders)
    name, data = combine([grid, energy])
    assert name == "Fix 2026"
    assert [item["name"] for item in data["items"]] == ["Energy", "Grid use", "Levy"]
    # Each template brings the VAT of its parts (the grid template's 99 % energy VAT is ignored).
    assert data["vat"]["import.energy"] == 20 and data["vat"]["import.grid"] == 20
    assert data["meta"] == {
        "supplier": "Example Energy",
        "grid_operator": "Example Grid",
        "grid_area": "level 7",
        "country": "at",
        "year": 2026,
        "parts": ["energy", "grid", "levies"],
        "valid_from": "2026-02-01",
        "valid_to": "2026-12-31",
        "source": "price sheet · grid fee ordinance",
    }
    assert len(tariff_from_data(name, data).items) == 3
    with pytest.raises(TemplatePartTwice):
        combine([energy, load_templates(folders)[1]])


def test_shipped_templates_are_valid() -> None:
    """Every shipped template: valid, with parts, year, source and validity,
    stored as <country>/<part>/<provider>/<valid_from>_<name>.yaml."""
    for template in load_templates([("shipped", SHIPPED_DIR)]):
        meta = template.meta
        _, country, part, *_, filename = template.key.split("/")
        assert meta.get("parts") and meta.get("year") and meta.get("source"), template.key
        assert meta.get("valid_from") and filename.startswith(f"{meta['valid_from']}_"), template.key
        assert template.country == country.upper(), template.key
        expected = "complete" if len(template.parts) == 3 else template.parts[0]
        assert part == expected, template.key
    files = list(SHIPPED_DIR.rglob("*.yaml")) if SHIPPED_DIR.is_dir() else []
    assert len(load_templates([("shipped", SHIPPED_DIR)])) == len(files), "a shipped template is invalid"
