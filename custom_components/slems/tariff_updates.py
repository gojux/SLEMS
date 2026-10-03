"""Corrections, newer price levels and successors of the templates a tariff was made from.

A tariff made from templates remembers each of them (``meta.templates``): the
family (the template's ``id``, else its path without the date of the price
level), the price level (``valid_from``, ``valid_to``) and the items exactly
as taken over (``items``, the snapshot). Candidates for one of them:

* a *correction*: the same price level changed since (e.g. a typo fixed),
* *newer price levels* of the same family,
* *successors*: templates naming the family in ``replaces``, possibly several.

Offers for new contracts (``offer``) only get corrections: a contract keeps
its price, a newer offer is for new contracts.

A repair issue tells about them, or that a current tariff is no longer valid
(``meta.valid_to``). A correction changes the items in place (their dates
stay, so past bills use the right price), but not items the user changed.
A newer level or a successor keeps the old prices for the past: the items of
the old level end the day before the new one starts and its items are added
from then on (every level in between in turn; a correction of the old level
first). Own items stay as they are. Declining the candidates of a template
(``declined``) keeps them from being offered again until something newer
comes. The snapshot also allows resetting the items to the template values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import elcom
from .const import DOMAIN, SUBENTRY_TYPE_TARIFF
from .tariff import Role
from .tariff_templates import OWN_DIR_NAME, SHIPPED_DIR, Template, load_templates, origin_of, taken_items

TARIFF_ISSUE_PREFIX = "tariff_"
CORRECTION = "correction"
UPDATE = "update"
SUCCESSOR = "successor"


@dataclass(frozen=True)
class Candidate:
    """What one template of a tariff can be updated to."""

    origin: str
    family: str
    kind: str
    # The price levels to take over, oldest first (none for a correction).
    levels: tuple[Template, ...]
    # The tariff's own price level if it changed since it was taken over.
    corrected: Template | None

    @property
    def starts(self) -> date:
        level = self.levels[0] if self.levels else self.corrected
        return date.fromisoformat(level.meta["valid_from"])

    @property
    def newest(self) -> str:
        return self.levels[-1].meta["valid_from"] if self.levels else ""

    @property
    def name(self) -> str:
        return (self.levels[-1] if self.levels else self.corrected).name


@dataclass
class UpdateChanges:
    """What an update changes, for the confirmation: per item name."""

    # name -> (old price, new price, unit); old None: a new item.
    prices: dict[str, tuple[float | None, float, str]] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    # Items the user had changed: replaced (newer level) or kept (correction, reset).
    own_changes: list[str] = field(default_factory=list)
    own_kept: list[str] = field(default_factory=list)


def _identity(item: Mapping[str, Any]) -> tuple:
    """An item of a template in a tariff: everything but its values."""
    return (
        item["name"], item["side"], item["group"], item.get("valid_from"),
        tuple(item.get("months") or ()), tuple(item.get("weekdays") or ()),
        item.get("time_from"), item.get("time_to"),
    )


def _values(item: Mapping[str, Any]) -> tuple:
    return (
        item["unit"], float(item["price"]), float(item.get("factor_pct") or 0.0),
        tuple(sorted((item.get("month_prices") or {}).items())),
    )


def _set_values(item: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key in ("unit", "price", "factor_pct", "month_prices"):
        item[key] = source.get(key)


def _fingerprint(items: list[dict[str, Any]], valid_to: str | None) -> str:
    return hashlib.sha1(json.dumps([items, valid_to], sort_keys=True).encode()).hexdigest()[:12]


def _correction_key(origin: Mapping[str, Any]) -> str:
    return f"{CORRECTION}:{origin['family']}@{origin.get('valid_from') or ''}"


def find_candidates(data: Mapping[str, Any], templates: Iterable[Template]) -> dict[str, list[Candidate]]:
    """Per origin family of the tariff its candidates: a correction, newer price
    levels of the same template, successors; declined ones left out."""
    meta = data.get("meta") or {}
    declined = meta.get("declined") or {}
    by_family: dict[str, list[Template]] = {}
    for template in templates:
        if template.meta.get("valid_from"):
            by_family.setdefault(template.family, []).append(template)
    for levels in by_family.values():
        levels.sort(key=lambda t: t.meta["valid_from"])
    result: dict[str, list[Candidate]] = {}
    for origin in meta.get("templates") or []:
        family, since = origin["family"], origin.get("valid_from") or ""
        own_level = next((t for t in by_family.get(family, []) if t.meta["valid_from"] == since), None)
        corrected = None
        if own_level is not None and "items" in origin:
            new_items = taken_items(own_level, origin.get("from_start", False), origin.get("options") or ())
            changed = (new_items, own_level.meta.get("valid_to")) != (origin["items"], origin.get("valid_to"))
            fingerprint = _fingerprint(new_items, own_level.meta.get("valid_to"))
            if changed and declined.get(_correction_key(origin)) != fingerprint:
                corrected = own_level
        candidates = [Candidate(family, family, CORRECTION, (), corrected)] if corrected else []
        if any(t.meta.get("offer") for t in by_family.get(family, [])):
            # An offer: the contract keeps its price, newer offers are no update.
            if candidates:
                result[family] = candidates
            continue
        families = [(family, UPDATE)] + sorted(
            (other, SUCCESSOR)
            for other, levels in by_family.items()
            if other != family and any(family in (t.meta.get("replaces") or []) for t in levels)
        )
        for other, kind in families:
            levels = tuple(t for t in by_family.get(other, []) if t.meta["valid_from"] > since)
            if kind == SUCCESSOR:
                # A successor from its first level that replaces the origin on.
                first = next((i for i, t in enumerate(levels) if family in (t.meta.get("replaces") or [])), None)
                levels = levels[first:] if first is not None else ()
            if levels and levels[-1].meta["valid_from"] > (declined.get(other) or ""):
                candidates.append(Candidate(family, other, kind, levels, corrected))
        if candidates:
            result[family] = candidates
    return result


def expired(data: Mapping[str, Any], today: date) -> date | None:
    """The end of the tariff if it has passed (``meta.valid_to``)."""
    end = (data.get("meta") or {}).get("valid_to")
    return date.fromisoformat(end) if end and date.fromisoformat(end) < today else None


def _correct(items: list[dict[str, Any]], origin: dict[str, Any], level: Template, changes: UpdateChanges) -> None:
    """The corrected price level in place: changed values, added and removed items."""
    new_items = taken_items(level, origin.get("from_start", False), origin.get("options") or ())
    old = {_identity(item): item for item in origin.get("items") or []}
    new = {_identity(item): item for item in new_items}
    in_tariff = {_identity(item): item for item in items}
    for identity in [*old, *(i for i in new if i not in old)]:
        before, after, item = old.get(identity), new.get(identity), in_tariff.get(identity)
        if before is not None and after is not None and _values(before) == _values(after):
            continue
        if before is not None and item is not None and _values(item) != _values(before):
            # Changed by the user: kept.
            changes.own_kept.append(item["name"])
            continue
        if before is not None and after is not None:
            if _values(before) != _values(after) and item is not None:
                _set_values(item, after)
                changes.prices[after["name"]] = (before["price"], after["price"], after["unit"])
        elif after is not None:
            items.append(dict(after))
            changes.prices[after["name"]] = (None, after["price"], after["unit"])
        elif item is not None:
            items.remove(item)
            changes.removed.append(item["name"])
    origin["items"] = new_items
    origin["valid_to"] = level.meta.get("valid_to")


def _take_level(items: list[dict[str, Any]], origin: dict[str, Any], level: Template, changes: UpdateChanges) -> None:
    """A newer price level: the items of the old one end the day before."""
    start = level.meta["valid_from"]
    taken = {_identity(item): item for item in origin.get("items") or []}
    old_prices: dict[str, float] = {}
    for item in list(items):
        snapshot = taken.get(_identity(item))
        if snapshot is None:
            continue
        if _values(item) != _values(snapshot) and item["name"] not in changes.own_changes:
            changes.own_changes.append(item["name"])
        if (item.get("valid_from") or "") >= start:
            # A change the old level had planned for later: replaced by the new level.
            items.remove(item)
            continue
        if not item.get("valid_to") or item["valid_to"] >= start:
            item["valid_to"] = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
            old_prices.setdefault(item["name"], item["price"])
    new_items = taken_items(level, True, origin.get("options") or ())
    items += [dict(item) for item in new_items]
    for item in new_items:
        old = changes.prices.get(item["name"], (old_prices.get(item["name"]),))[0]
        changes.prices[item["name"]] = (old, item["price"], item["unit"])
    new_names = {item["name"] for item in new_items}
    changes.removed += [name for name in old_prices if name not in new_names and name not in changes.removed]
    origin.update(origin_of(level, True, origin.get("options") or ()))


def apply_updates(
    data: Mapping[str, Any],
    chosen: Iterable[Candidate],
    declined: Iterable[Candidate],
    templates: Iterable[Template],
) -> tuple[dict[str, Any], UpdateChanges]:
    """The tariff data with the ``chosen`` candidates taken over (a correction of
    the old level always first) and the ``declined`` ones not offered again."""
    items = [dict(item) for item in data.get("items") or []]
    meta = dict(data.get("meta") or {})
    origins = [dict(origin) for origin in meta.get("templates") or []]
    declined_levels = dict(meta.get("declined") or {})
    changes = UpdateChanges()
    for candidate in chosen:
        origin = next(o for o in origins if o["family"] == candidate.origin)
        if candidate.corrected is not None:
            _correct(items, origin, candidate.corrected, changes)
        for level in candidate.levels:
            _take_level(items, origin, level, changes)
        if candidate.levels and candidate.levels[-1].meta.get("year"):
            meta["year"] = max(meta.get("year") or 0, candidate.levels[-1].meta["year"])
    changes.removed = [name for name in changes.removed if name not in changes.prices]
    for candidate in declined:
        if candidate.kind == CORRECTION:
            origin = next(o for o in origins if o["family"] == candidate.origin)
            level = candidate.corrected
            declined_levels[_correction_key(origin)] = _fingerprint(
                taken_items(level, origin.get("from_start", False), origin.get("options") or ()),
                level.meta.get("valid_to"),
            )
        else:
            declined_levels[candidate.family] = max(declined_levels.get(candidate.family) or "", candidate.newest)
    meta["templates"] = origins
    if declined_levels:
        meta["declined"] = declined_levels
    # The tariff ends with the first of its price levels that ends.
    ends = [origin["valid_to"] for origin in origins if origin.get("valid_to")]
    meta.pop("valid_to", None)
    if ends:
        meta["valid_to"] = min(ends)
    return {**data, "items": items, "meta": meta}, changes


def template_differences(data: Mapping[str, Any]) -> list[str]:
    """Names of the template items the user changed or removed (current price levels)."""
    in_tariff = {_identity(item): item for item in data.get("items") or []}
    names = []
    for origin in (data.get("meta") or {}).get("templates") or []:
        for taken in origin.get("items") or []:
            item = in_tariff.get(_identity(taken))
            if (item is None or _values(item) != _values(taken)) and taken["name"] not in names:
                names.append(taken["name"])
    return names


def reset_to_templates(data: Mapping[str, Any]) -> dict[str, Any]:
    """The tariff with the template items of its current price levels as taken
    over (changed values restored, removed items back); own items stay."""
    items = [dict(item) for item in data.get("items") or []]
    in_tariff = {_identity(item): item for item in items}
    for origin in (data.get("meta") or {}).get("templates") or []:
        for taken in origin.get("items") or []:
            item = in_tariff.get(_identity(taken))
            if item is None:
                items.append(dict(taken))
            elif _values(item) != _values(taken):
                _set_values(item, taken)
    return {**data, "items": items}


def template_directories(hass: HomeAssistant) -> list[tuple[str, Path]]:
    return [("shipped", SHIPPED_DIR), ("own", Path(hass.config.path(OWN_DIR_NAME)))]


async def async_check_tariff_updates(hass: HomeAssistant, entry) -> None:
    """Repair issues for current tariffs with corrected or newer prices or
    successors, or past their end."""
    templates = await hass.async_add_executor_job(load_templates, template_directories(hass))
    today = dt_util.now().date()
    coordinator = getattr(entry, "runtime_data", None)
    consent = coordinator is not None and coordinator.market_prices.enabled
    wanted: dict[str, tuple[str, dict[str, str]]] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_TARIFF or subentry.data.get("role", Role.CURRENT) != Role.CURRENT:
            continue
        placeholders = {"name": subentry.title}
        levels = templates
        origin = elcom.supply_of(subentry.data.get("meta") or {})
        if origin is not None and consent:
            # A Swiss tariff: its years at ElCom (the next one is published in September).
            try:
                levels = templates + await elcom.async_levels(
                    async_get_clientsession(hass), *origin, int((subentry.data.get("meta") or {}).get("year") or today.year)
                )
            except elcom.ElcomError:
                pass
        if candidates := [c for options in find_candidates(subentry.data, levels).values() for c in options]:
            kinds = {c.kind for c in candidates}
            key = "tariff_successor" if SUCCESSOR in kinds else "tariff_update" if UPDATE in kinds else "tariff_correction"
            wanted[f"{TARIFF_ISSUE_PREFIX}update_{subentry.subentry_id}"] = (
                key,
                placeholders
                | {
                    "date": min(c.starts for c in candidates).isoformat(),
                    "successors": ", ".join(c.name for c in candidates if c.kind == SUCCESSOR),
                },
            )
        elif (end := expired(subentry.data, today)) is not None:
            wanted[f"{TARIFF_ISSUE_PREFIX}expired_{subentry.subentry_id}"] = (
                "tariff_expired", placeholders | {"date": end.isoformat()}
            )
    for issue_id, (key, placeholders) in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
        )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(TARIFF_ISSUE_PREFIX) and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
