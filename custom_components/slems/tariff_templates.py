"""Tariff templates: YAML files (see tariff_yaml) to start a tariff from.

Shipped with SLEMS (public list prices from price sheets, each with its
source) as ``templates/tariffs/<country>/<part>/<provider>/<valid_from>_<name>.yaml``:
``<part>`` is the first of its parts (``energy``, ``grid``, ``levies``) or
``complete`` for a template with all three; one file per price level, so
the price levels of a provider sort by date and read from the folder
``slems_tariff_templates`` in the Home Assistant configuration (own
templates). A template covers some parts of a bill (``parts``): the energy
of a supplier, the grid fees of a grid area, the levies of a country, or all
of them. Templates for different parts are combined into one tariff.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from .tariff import Group
from .tariff_yaml import TariffYamlError, parse_yaml

_LOGGER = logging.getLogger(__name__)

SHIPPED_DIR = Path(__file__).parent / "templates" / "tariffs"
OWN_DIR_NAME = "slems_tariff_templates"
PART_ORDER = (Group.ENERGY.value, Group.GRID.value, Group.LEVIES.value)


@dataclass(frozen=True)
class Template:
    # "shipped/at/…/x.yaml" or "own/x.yaml".
    key: str
    name: str
    data: dict[str, Any]

    @property
    def meta(self) -> dict[str, Any]:
        return self.data.get("meta") or {}

    @property
    def parts(self) -> tuple[str, ...]:
        parts = self.meta.get("parts") or list(PART_ORDER)
        return tuple(part for part in PART_ORDER if part in parts)

    @property
    def country(self) -> str:
        return str(self.meta.get("country") or "–").upper()

    @property
    def provider(self) -> str:
        meta = self.meta
        return str(meta.get("supplier") or meta.get("grid_operator") or meta.get("country") or "")

    @property
    def own(self) -> bool:
        return self.key.startswith("own/")

    def sort_key(self) -> tuple:
        year = self.meta.get("year") or 0
        return (PART_ORDER.index(self.parts[0]) if self.parts else 0, self.provider.lower(), -year, self.name.lower())


def load_templates(directories: Iterable[tuple[str, Path]]) -> list[Template]:
    """All valid templates of the directories (prefix, path), sorted by part,
    provider and year (newest first); invalid files are logged and skipped."""
    templates = []
    for prefix, directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.yaml")):
            try:
                name, data = parse_yaml(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, TariffYamlError) as err:
                _LOGGER.warning("Tariff template %s skipped: %s", path, err)
                continue
            templates.append(Template(f"{prefix}/{path.relative_to(directory).as_posix()}", name, data))
    return sorted(templates, key=Template.sort_key)


def label(template: Template, part_names: dict[str, str], own: str) -> str:
    """'Energy · Example Energy – Fix (2026)' for the selection."""
    parts = " + ".join(part_names[part] for part in template.parts)
    meta = template.meta
    detail = template.name
    if meta.get("grid_area") and meta["grid_area"] not in detail:
        detail += f", {meta['grid_area']}"
    text = f"{parts} · {template.provider} – {detail}" if template.provider else f"{parts} · {detail}"
    if meta.get("year"):
        text += f" ({meta['year']})"
    return f"{text} · {own}" if template.own else text


class TemplatePartTwice(ValueError):
    """Two chosen templates cover the same part of the bill."""


def combine(templates: Sequence[Template]) -> tuple[str, dict[str, Any]]:
    """One tariff (name, subentry data) of templates for different parts.

    Each template brings the items and the VAT of its parts; the name is the
    one of the template with the energy part (else the first).
    """
    seen: set[str] = set()
    for template in templates:
        if seen & set(template.parts):
            raise TemplatePartTwice
        seen |= set(template.parts)
    ordered = sorted(templates, key=Template.sort_key)
    items: list[dict[str, Any]] = []
    vat: dict[str, float] = {}
    for template in ordered:
        items += template.data["items"]
        for key, value in template.data["vat"].items():
            if key.split(".", 1)[1] in template.parts:
                vat[key] = value
    for template in ordered:
        for key, value in template.data["vat"].items():
            vat.setdefault(key, value)
    metas = [template.meta for template in ordered]

    def first(key: str) -> Any:
        return next((meta[key] for meta in metas if meta.get(key)), None)

    starts = [meta["valid_from"] for meta in metas if meta.get("valid_from")]
    ends = [meta["valid_to"] for meta in metas if meta.get("valid_to")]
    meta = {
        "supplier": first("supplier"),
        "grid_operator": first("grid_operator"),
        "grid_area": first("grid_area"),
        "country": first("country"),
        "year": max((meta["year"] for meta in metas if meta.get("year")), default=None),
        "parts": [part for part in PART_ORDER if part in seen],
        "valid_from": max(starts) if starts else None,
        "valid_to": min(ends) if ends else None,
        "source": " · ".join(str(meta["source"]) for meta in metas if meta.get("source")) or None,
    }
    return ordered[0].name, {
        "role": "current",
        "vat": vat,
        "items": items,
        "meta": {key: value for key, value in meta.items() if value is not None},
    }
