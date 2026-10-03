"""Day-ahead market prices from public sources, fetched only with the user's consent.

Sources (prices in €/MWh per quarter hour; hourly prices from before the
market switched to quarter hours are repeated for each quarter):

* APG transparency platform (bidding zone AT): one day per request, rows in
  local time,
* SMARD of the Bundesnetzagentur (DE-LU, CC BY 4.0): weekly files,
* Energy-Charts of Fraunhofer ISE (AT or DE-LU).

Nothing is fetched until the user switches the fetching on. The prices are
kept in a local store: the last year is filled once, then the next day is
fetched after the day-ahead auction (published around 13:00 local time).
With the same consent the official monthly market values a tariff refers to
are fetched (see reference_values), at most every ``REFERENCE_REFRESH``.

After the last known price the price aware control plans with estimates
(``estimate_prices``): per local quarter hour of the day the median of the
last ``ESTIMATE_DAYS`` days of the same kind (working day or weekend), its
deviation from the mean of that day profile reduced to ``ESTIMATE_SHARE``,
so an uncertain swing counts less than a known one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import statistics
from datetime import date, datetime, time, timedelta
from enum import StrEnum
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .reference_values import ReferenceError, async_fetch_at_reference
from .reference_values import ATTRIBUTION as REFERENCE_ATTRIBUTIONS

REFERENCE_ATTRIBUTION = next(iter(REFERENCE_ATTRIBUTIONS.values()))

_LOGGER = logging.getLogger(__name__)

SLOT_S = 900
HISTORY_DAYS = 365
# The day-ahead prices of the next day are published after the auction.
AUCTION_HOUR = 13
STORAGE_VERSION = 1
REQUEST_TIMEOUT_S = 30
# Pause between two requests while filling the history.
REQUEST_PAUSE_S = 1.0
# Days per request of the sources that take a period.
CHUNK_DAYS = 28
REFERENCE_REFRESH = timedelta(hours=12)
ESTIMATE_DAYS = 14
# Days of the same kind needed for its own profile, else all days count.
ESTIMATE_MIN_DAYS = 3
ESTIMATE_SHARE = 0.5

APG_URL = "https://transparency.apg.at/api/v1/EXAAD1P/Data/English/PT15M"
SMARD_URL = "https://www.smard.de/app/chart_data/4169/DE-LU"
ENERGY_CHARTS_URL = "https://api.energy-charts.info/price"


class PriceSource(StrEnum):
    APG = "apg"
    SMARD = "smard"
    ENERGY_CHARTS_AT = "energy_charts_at"
    ENERGY_CHARTS_DE_LU = "energy_charts_de_lu"


ATTRIBUTION = {
    PriceSource.APG: "Austrian Power Grid AG (transparency.apg.at)",
    PriceSource.SMARD: "Bundesnetzagentur | SMARD.de (CC BY 4.0)",
    PriceSource.ENERGY_CHARTS_AT: "Energy-Charts.info (Fraunhofer ISE)",
    PriceSource.ENERGY_CHARTS_DE_LU: "Energy-Charts.info (Fraunhofer ISE)",
}


class PriceError(Exception):
    """A source did not deliver usable prices."""


def default_source(country: str | None) -> PriceSource:
    return PriceSource.APG if country == "AT" else PriceSource.SMARD


def quarter_slots(points: Iterable[tuple[int, float | None]]) -> dict[int, float]:
    """Quarter hour slots (unix start -> price) from (unix start, price) points.

    A price lasts until the next point, at most an hour; the last one as long
    as the shortest step between the points. Points without a price are gaps.
    """
    ordered = sorted(points)
    steps = [b[0] - a[0] for a, b in zip(ordered, ordered[1:]) if b[0] > a[0]]
    last_step = min(min(steps, default=SLOT_S), 3600)
    slots: dict[int, float] = {}
    for index, (start, price) in enumerate(ordered):
        if price is None:
            continue
        end = ordered[index + 1][0] if index + 1 < len(ordered) else start + last_step
        end = min(end, start + 3600)
        slot = start - start % SLOT_S
        while slot < end:
            slots[slot] = float(price)
            slot += SLOT_S
    return slots


def day_bounds(day: date) -> tuple[int, int]:
    """Unix start of the local day and of the next one."""
    zone = dt_util.get_default_time_zone()
    start = datetime.combine(day, time(), zone)
    end = datetime.combine(day + timedelta(days=1), time(), zone)
    return int(start.timestamp()), int(end.timestamp())


def parse_apg(day: date, payload: Mapping[str, Any]) -> dict[int, float]:
    """One local day of APG rows; row ``i`` is the ``i``-th quarter hour of the day.

    The rows are labelled in local time ("2A:00" / "2B:00" when the clock goes
    back), so their position gives the time.
    """
    try:
        rows = payload["ResponseData"]["ValueRows"]
    except (KeyError, TypeError) as err:
        raise PriceError(str(payload.get("Message") if isinstance(payload, Mapping) else err)) from err
    if not rows:
        return {}
    start, end = day_bounds(day)
    if len(rows) != (end - start) // SLOT_S:
        raise PriceError(f"APG: {len(rows)} quarter hours on {day}")
    return quarter_slots((start + index * SLOT_S, row["V"][0]["V"]) for index, row in enumerate(rows))


def parse_smard(payload: Mapping[str, Any]) -> dict[int, float]:
    try:
        series = payload["series"]
    except (KeyError, TypeError) as err:
        raise PriceError("SMARD: no series") from err
    return quarter_slots((int(ms) // 1000, price) for ms, price in series)


def parse_energy_charts(payload: Mapping[str, Any]) -> dict[int, float]:
    try:
        return quarter_slots(zip(payload["unix_seconds"], payload["price"], strict=True))
    except (KeyError, TypeError, ValueError) as err:
        raise PriceError("Energy-Charts: no prices") from err


def smard_weeks(timestamps_ms: Iterable[int], start: int, end: int) -> list[int]:
    """Week files (start in ms) that overlap [start, end) in unix seconds."""
    ordered = sorted(timestamps_ms)
    weeks = []
    for index, week in enumerate(ordered):
        week_end = ordered[index + 1] if index + 1 < len(ordered) else week + 7 * 86400 * 1000
        if week < end * 1000 and week_end > start * 1000:
            weeks.append(week)
    return weeks


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> Any:
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_S):
            async with session.get(url, params=params) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise PriceError(f"{url}: HTTP {response.status} {text[:200]}")
                return await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise PriceError(f"{url}: {err}") from err


async def async_fetch(
    session: aiohttp.ClientSession, source: PriceSource, first: date, last: date
) -> dict[int, float]:
    """Prices of the local days ``first`` to ``last`` (fewer if not published yet)."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    prices: dict[int, float] = {}
    if source is PriceSource.APG:
        day = first
        while day <= last:
            payload = await _get_json(
                session, f"{APG_URL}/{day:%Y-%m-%d}T000000/{day + timedelta(days=1):%Y-%m-%d}T000000"
            )
            prices.update(parse_apg(day, payload))
            day += timedelta(days=1)
            if day <= last:
                await asyncio.sleep(REQUEST_PAUSE_S)
    elif source is PriceSource.SMARD:
        index = await _get_json(session, f"{SMARD_URL}/index_quarterhour.json")
        for week in smard_weeks(index.get("timestamps") or [], start, end):
            prices.update(parse_smard(await _get_json(session, f"{SMARD_URL}/4169_DE-LU_quarterhour_{week}.json")))
            await asyncio.sleep(REQUEST_PAUSE_S)
    else:
        zone = "AT" if source is PriceSource.ENERGY_CHARTS_AT else "DE-LU"
        chunk = start
        while chunk < end:
            chunk_end = min(end, chunk + CHUNK_DAYS * 86400)
            payload = await _get_json(
                session,
                ENERGY_CHARTS_URL,
                {"bzn": zone, "start": _iso(chunk), "end": _iso(chunk_end - SLOT_S)},
            )
            prices.update(parse_energy_charts(payload))
            chunk = chunk_end
            if chunk < end:
                await asyncio.sleep(REQUEST_PAUSE_S)
    return {slot: price for slot, price in prices.items() if start <= slot < end}


def _iso(timestamp: int) -> str:
    return dt_util.utc_from_timestamp(timestamp).strftime("%Y-%m-%dT%H:%MZ")


def missing_days(prices: Mapping[int, float], first: date, last: date) -> list[date]:
    """Days without their first or last quarter hour."""
    days = []
    day = first
    while day <= last:
        start, end = day_bounds(day)
        if start not in prices or end - SLOT_S not in prices:
            days.append(day)
        day += timedelta(days=1)
    return days


def day_runs(days: list[date]) -> list[tuple[date, date]]:
    """Consecutive days as (first, last)."""
    runs: list[tuple[date, date]] = []
    for day in days:
        if runs and runs[-1][1] + timedelta(days=1) == day:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def hourly_means(prices: Mapping[int, float], start: datetime, end: datetime) -> dict[datetime, float]:
    """Mean price (€/MWh) per hour start (UTC) of the hours with prices."""
    sums: dict[int, tuple[float, int]] = {}
    first, last = int(start.timestamp()), int(end.timestamp())
    for slot, price in prices.items():
        if first <= slot < last:
            hour = slot - slot % 3600
            total, count = sums.get(hour, (0.0, 0))
            sums[hour] = (total + price, count + 1)
    return {dt_util.utc_from_timestamp(hour): total / count for hour, (total, count) in sums.items()}


def estimate_prices(prices: Mapping[int, float], start: int, end: int) -> dict[int, float]:
    """Estimated €/MWh of the quarter hours from ``start`` to ``end`` (epoch s)
    after the last known price, from the profile of the recent days."""
    if not prices:
        return {}
    last = max(prices)
    first = max(start - start % SLOT_S, last + SLOT_S)
    if first >= end:
        return {}
    zone = dt_util.get_default_time_zone()

    def key(slot: int) -> tuple[bool, int]:
        local = datetime.fromtimestamp(slot, zone)
        return local.weekday() >= 5, local.hour * 60 + local.minute

    samples: dict[tuple[bool, int], list[float]] = {}
    for slot, price in prices.items():
        if slot > last - ESTIMATE_DAYS * 86400:
            samples.setdefault(key(slot), []).append(price)

    def profile(weekend: bool) -> dict[int, float]:
        """Median per minute of the day for one kind of day (or both if too few)."""
        result = {}
        for (kind, minute), values in samples.items():
            if kind == weekend and len(values) >= ESTIMATE_MIN_DAYS:
                result[minute] = statistics.median(values)
        if len(result) < 24 * 4:
            merged: dict[int, list[float]] = {}
            for (_kind, minute), values in samples.items():
                merged.setdefault(minute, []).extend(values)
            result = {minute: statistics.median(values) for minute, values in merged.items()}
        return result

    profiles = {weekend: profile(weekend) for weekend in (False, True)}
    means = {weekend: statistics.fmean(values.values()) if values else None for weekend, values in profiles.items()}
    estimates = {}
    for slot in range(first, end, SLOT_S):
        weekend, minute = key(slot)
        median, mean = profiles[weekend].get(minute), means[weekend]
        if median is not None and mean is not None:
            estimates[slot] = mean + ESTIMATE_SHARE * (median - mean)
    return estimates


def period_means(prices: Mapping[int, float], lengths: Mapping[datetime, int]) -> dict[datetime, float]:
    """Mean price (€/MWh) of each period (start -> length in s) with prices."""
    means = {}
    for start, length in lengths.items():
        first = int(start.timestamp())
        values = [prices[slot] for slot in range(first - first % SLOT_S, first + length, SLOT_S) if slot in prices]
        if values:
            means[start] = sum(values) / len(values)
    return means


class MarketPrices:
    """The cached prices of the chosen source and their regular update."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"slems.{entry_id}.market_prices")
        self.enabled = False
        self.source = default_source(hass.config.country)
        self.prices: dict[int, float] = {}
        self.last_update: datetime | None = None
        self.last_error: str | None = None
        self._cached_source: PriceSource | None = None
        self._task: asyncio.Task | None = None
        self._started = False
        self._unsub: list = []
        # Official monthly market values (reference_values): market -> month -> ct/kWh,
        # the ones the tariffs use, and when they were fetched.
        self.references: dict[str, dict[str, float]] = {}
        self.wanted_references: set[str] = set()
        self.references_update: datetime | None = None

    @property
    def attribution(self) -> str:
        if self.references:
            return f"{ATTRIBUTION[self.source]}; {REFERENCE_ATTRIBUTION}"
        return ATTRIBUTION[self.source]

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        try:
            self._cached_source = PriceSource(data["source"])
            start = int(data["start"])
            self.prices = {
                start + index * SLOT_S: float(price)
                for index, price in enumerate(data.get("values") or [])
                if price is not None
            }
            if data.get("last_update"):
                self.last_update = dt_util.parse_datetime(data["last_update"])
        except (KeyError, TypeError, ValueError):
            self.prices = {}
        references = data.get("references") or {}
        self.references = {
            str(market): {str(month): float(value) for month, value in values.items()}
            for market, values in (references.get("values") or {}).items()
        }
        if references.get("updated"):
            self.references_update = dt_util.parse_datetime(references["updated"])

    def start(self) -> None:
        """Begin the regular update (after the entities restored the settings)."""
        self._started = True
        self._unsub.append(
            async_track_time_change(self._hass, self._on_time, minute=17, second=0)
        )
        self._sync()

    async def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        await self._cancel()
        if self._cached_source is not None:
            await self._store.async_save(self._data_to_save())

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self._sync()
        else:
            self._hass.async_create_task(self._cancel())

    def set_source(self, source: PriceSource) -> None:
        if source is self.source and self._cached_source in (None, source):
            return
        self.source = source
        self._hass.async_create_task(self._async_switch_source())

    async def _async_switch_source(self) -> None:
        await self._cancel()
        if self._cached_source is not None and self._cached_source is not self.source:
            self.prices = {}
            self.last_update = None
            self._cached_source = None
        self._sync()

    def refresh(self) -> None:
        """Fetch what is missing now (e.g. reference values a new tariff needs)."""
        self._sync()

    def price_at(self, moment: datetime) -> float | None:
        """€/MWh of the quarter hour of ``moment``."""
        timestamp = int(moment.timestamp())
        return self.prices.get(timestamp - timestamp % SLOT_S)

    def estimates(self, start: datetime, end: datetime) -> dict[int, float]:
        """Estimated €/MWh per quarter hour (epoch s) after the last known price."""
        return estimate_prices(self.prices, int(start.timestamp()), int(end.timestamp()))

    def hourly_means(self, start: datetime, end: datetime) -> dict[datetime, float]:
        return hourly_means(self.prices, start, end)

    def period_means(self, lengths: Mapping[datetime, int]) -> dict[datetime, float]:
        return period_means(self.prices, lengths)

    @callback
    def _on_time(self, _now: datetime) -> None:
        self._sync()

    def _sync(self) -> None:
        if not (self.enabled and self._started) or (self._task and not self._task.done()):
            return
        self._task = self._hass.async_create_background_task(
            self._async_fetch_missing(), "slems market prices"
        )

    async def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _async_fetch_missing(self) -> None:
        if self._cached_source is not None and self._cached_source is not self.source:
            self.prices = {}
        self._cached_source = self.source
        now = dt_util.now()
        today = now.date()
        last = today + timedelta(days=1) if now.hour >= AUCTION_HOUR else today
        first = today - timedelta(days=HISTORY_DAYS)
        # Newest first, so the coming hours are there before the history.
        runs = day_runs(missing_days(self.prices, first, last))[::-1]
        session = async_get_clientsession(self._hass)
        source = self.source
        # A small first request, so the current prices are there quickly.
        size = 2
        try:
            for run_first, run_last in runs:
                chunk_last = run_last
                while chunk_last >= run_first:
                    chunk_first = max(run_first, chunk_last - timedelta(days=size - 1))
                    size = CHUNK_DAYS
                    fetched = await async_fetch(session, source, chunk_first, chunk_last)
                    if source is not self.source:
                        return
                    self.prices.update(fetched)
                    self.last_update = dt_util.utcnow()
                    self._prune(first)
                    self._store.async_delay_save(self._data_to_save, 10)
                    chunk_last = chunk_first - timedelta(days=1)
        except PriceError as err:
            if str(err) != self.last_error:
                _LOGGER.warning("Market prices (%s) not available: %s", source, err)
            self.last_error = str(err)
            return
        self.last_error = None
        await self._async_fetch_references(session)

    async def _async_fetch_references(self, session: aiohttp.ClientSession) -> None:
        """The official monthly market values the tariffs use (see reference_values)."""
        if not self.wanted_references:
            return
        now = dt_util.utcnow()
        if self.references_update and now - self.references_update < REFERENCE_REFRESH:
            return
        try:
            fetched = await async_fetch_at_reference(session)
        except ReferenceError as err:
            _LOGGER.warning("Reference market values not available: %s", err)
            return
        self.references = {market: fetched[market] for market in self.wanted_references if market in fetched}
        self.references_update = now
        self._store.async_delay_save(self._data_to_save, 10)

    def _prune(self, first: date) -> None:
        oldest, _ = day_bounds(first - timedelta(days=1))
        for slot in [slot for slot in self.prices if slot < oldest]:
            del self.prices[slot]

    def _data_to_save(self) -> dict:
        references = {
            "values": self.references,
            "updated": self.references_update.isoformat() if self.references_update else None,
        }
        if not self.prices:
            return {"source": self.source.value, "start": 0, "values": [], "references": references}
        start, end = min(self.prices), max(self.prices)
        return {
            "source": self.source.value,
            "start": start,
            "values": [self.prices.get(slot) for slot in range(start, end + SLOT_S, SLOT_S)],
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "references": references,
        }
