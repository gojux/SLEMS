"""Tariff templates: YAML files (see tariff_yaml) to start a tariff from.

Shipped with SLEMS (public list prices from price sheets, each with its
source) as ``templates/tariffs/<country>/<part>/<provider>/<valid_from>_<name>.yaml``:
``<part>`` is the first of its parts (``energy``, ``grid``, ``levies``) or
``complete`` for a template with all three; one file per price level, so
the price levels of a provider sort by date. Each has an ``id`` starting with
its country (e.g. ``at/example-energy/fix``); own templates should use ids
starting with ``own/``, unless they deliberately add a price level to a
shipped template (same id, newer ``valid_from``) and read from the folder
``slems_tariff_templates`` in the Home Assistant configuration (own
templates). A template covers some parts of a bill (``parts``): the energy
of a supplier, the grid fees of a grid area, the levies of a country, or all
of them. Templates for different parts are combined into one tariff.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path
from typing import Any

from .tariff import Group, TariffItem
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
    def currency(self) -> str | None:
        return self.meta.get("currency")

    @property
    def own(self) -> bool:
        return self.key.startswith("own/")

    @property
    def family(self) -> str:
        """The same tariff in all its price levels: its ``id``, else the key
        without the date of the price level."""
        if self.meta.get("id"):
            return str(self.meta["id"])
        folder, _, filename = self.key.rpartition("/")
        date_part, separator, rest = filename.partition("_")
        if separator and len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
            filename = rest
        return f"{folder}/{filename}"

    @property
    def optional_items(self) -> list[dict[str, Any]]:
        return [item for item in self.data["items"] if item.get("optional")]

    @property
    def feed_in(self) -> bool:
        """A feed-in tariff of its own (a separate contract): only export items."""
        return all(item["side"] == "export" for item in self.data["items"])

    @property
    def complete(self) -> bool:
        return len(self.parts) == len(PART_ORDER)

    def valid_on(self, day: date) -> bool:
        start, end = self.meta.get("valid_from"), self.meta.get("valid_to")
        return (start is None or start <= day.isoformat()) and (end is None or day.isoformat() <= end)

    @property
    def area_first(self) -> bool:
        """A grid template: listed by its grid area (known to everyone) before the operator."""
        return bool(self.parts) and self.parts[0] == "grid"

    def sort_key(self) -> tuple:
        year = self.meta.get("year") or 0
        first = str(self.meta.get("grid_area") or self.name) if self.area_first else self.provider
        return (PART_ORDER.index(self.parts[0]) if self.parts else 0, first.lower(), -year, self.name.lower())


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


def label(template: Template, part_names: dict[str, str], own: str, offer: str = "offer {month}") -> str:
    """'Energy · Example Energy – Fix (2026)', for grid templates
    'Grid · Area X, level 7 – Example Grid (2026)' for the selection."""
    parts = part_names.get("feed_in", "Feed-in") if template.feed_in else " + ".join(part_names[part] for part in template.parts)
    meta = template.meta
    detail = template.name
    if meta.get("grid_area") and meta["grid_area"] not in detail:
        detail += f", {meta['grid_area']}"
    if template.area_first:
        text = f"{parts} · {detail} – {template.provider}" if template.provider else f"{parts} · {detail}"
    else:
        text = f"{parts} · {template.provider} – {detail}" if template.provider else f"{parts} · {detail}"
    if meta.get("offer") and meta.get("valid_from"):
        # An offer for new contracts: the month of the contract start.
        text += f" ({offer.format(month=f'{meta['valid_from'][5:7]}/{meta['valid_from'][:4]}')})"
    elif meta.get("year"):
        text += f" ({meta['year']})"
    return f"{text} · {own}" if template.own else text


def energy_choices(templates: Iterable[Template]) -> list[Template]:
    """Templates to start with: energy (with or without further parts)."""
    return [t for t in templates if t.parts and t.parts[0] == "energy"]


def grid_choices(templates: Iterable[Template]) -> list[Template]:
    return [t for t in templates if t.parts and t.parts[0] == "grid"]


def levies_choices(templates: Iterable[Template]) -> list[Template]:
    return [t for t in templates if t.parts == ("levies",)]


def _current(templates: Iterable[Template], day: date) -> Template | None:
    """The one template in effect on ``day`` (the latest start), None if none or
    several equally likely ones (e.g. two network levels)."""
    valid = [t for t in templates if t.valid_on(day)]
    if not valid:
        return None
    latest = max(t.meta.get("valid_from") or "" for t in valid)
    newest = [t for t in valid if (t.meta.get("valid_from") or "") == latest]
    household = [t for t in newest if t.meta.get("household")]
    if len(household) == 1:
        return household[0]
    return newest[0] if len(newest) == 1 else None


def suggest_grid(energy: Template | None, templates: Iterable[Template], day: date) -> Template | None:
    """The grid template usually combined with ``energy`` (its ``suggest``)."""
    operator = ((energy.meta.get("suggest") or {}) if energy else {}).get("grid_operator")
    if not operator:
        return None
    return _current(
        [t for t in grid_choices(templates) if str(t.meta.get("grid_operator", "")).casefold() == operator.casefold()],
        day,
    )


def suggest_levies(templates: Iterable[Template], day: date) -> Template | None:
    """The levies of the country in effect on ``day``."""
    return _current(levies_choices(templates), day)


def taken_items(level: Template, from_start: bool, options: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The items of a price level as they are put into a tariff, of the
    optional ones those named in ``options``; ``from_start``: not before the
    level starts (a level added to an existing tariff)."""
    start = level.meta.get("valid_from") or ""
    chosen = set(options)
    items = []
    for template_item in level.data["items"]:
        if template_item.get("optional") and template_item["name"] not in chosen:
            continue
        item = TariffItem.from_dict(template_item).as_dict()
        if from_start and (item.get("valid_from") or "") < start:
            item["valid_from"] = start
        items.append(item)
    return items


def origin_of(level: Template, from_start: bool, options: Iterable[str] = ()) -> dict[str, Any]:
    """What a tariff remembers of a template it took over (see tariff_updates)."""
    return {
        "family": level.family,
        "valid_from": level.meta.get("valid_from"),
        "valid_to": level.meta.get("valid_to"),
        "from_start": from_start,
        "options": sorted(set(options)),
        "items": taken_items(level, from_start, options),
    }


class TemplatePartTwice(ValueError):
    """Two chosen templates cover the same part of the bill."""


def combine(
    templates: Sequence[Template], options: Mapping[str, Iterable[str]] | None = None
) -> tuple[str, dict[str, Any]]:
    """One tariff (name, subentry data) of templates for different parts.

    Each template brings the items and the VAT of its parts, of its optional
    items those chosen (``options``: template key -> item names); the name is
    the one of the template with the energy part (else the first).
    """
    options = options or {}
    seen: set[str] = set()
    for template in templates:
        if seen & set(template.parts):
            raise TemplatePartTwice
        seen |= set(template.parts)
    ordered = sorted(templates, key=Template.sort_key)
    items: list[dict[str, Any]] = []
    vat: dict[str, float] = {}
    for template in ordered:
        items += taken_items(template, False, options.get(template.key, ()))
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
        "currency": first("currency"),
        # Where the tariff came from (see tariff_updates).
        "templates": [origin_of(template, False, options.get(template.key, ())) for template in ordered],
    }
    return ordered[0].name, {
        "role": "current",
        "vat": vat,
        "items": items,
        "meta": {key: value for key, value in meta.items() if value is not None},
    }
