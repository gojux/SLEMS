"""Public holidays of a country or one of its regions (``holidays`` package).

The price estimates use the holidays of the bidding zone's country, the
consumption forecast those of the home: the country of Home Assistant and the
region (state, canton) chosen in the system settings. Without a region the
national holidays count; in countries whose national holidays leave out days
off in nearly all regions (``REGIONAL_HOLIDAY_COUNTRIES``) a day counts if at
least half of the regions have it off.

The region is suggested once while setting up, from a reverse lookup of the
home's coordinates at Nominatim (OpenStreetMap), the service the Home
Assistant onboarding uses for the country: its ISO 3166-2 code (``AT-8``,
``DE-BY``, ``CH-ZH``) ends in the region code of the ``holidays`` package.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import date
import logging

import aiohttp
import holidays

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Countries whose national holidays leave out days off in nearly all regions
# (in Switzerland Good Friday, Easter and Whit Monday, St Stephen's Day are
# cantonal): there a day counts if at least half of the regions have it off.
REGIONAL_HOLIDAY_COUNTRIES = {"CH"}

# Stored as the region when the national holidays are chosen explicitly.
NATIONAL = "national"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_TIMEOUT_S = 10
# Region level of the reverse lookup; the coordinates are rounded to about 100 m.
NOMINATIM_ZOOM = 5
COORDINATE_DECIMALS = 3
USER_AGENT = "SLEMS (+https://github.com/gojux/SLEMS)"


class PublicHolidays:
    """Public holidays of a country, or of one region (``day in public_holidays``).

    The years are computed by ``load`` in an executor: the holidays package
    imports the country's module on its first use, which blocks. A year not
    loaded yet has no holidays.
    """

    def __init__(self, country: str, region: str | None = None) -> None:
        self.country = country
        # A stored region of another country (Home Assistant moved) is ignored.
        self.region = None if region in (None, NATIONAL) else region
        self._days: dict[int, set[date]] = {}

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and day in self._days.get(day.year, ())

    @property
    def years(self) -> set[int]:
        return set(self._days)

    def load(self, years: Iterable[int]) -> None:
        for year in years:
            if year not in self._days:
                self._days[year] = self._of_year(year)

    def _of_year(self, year: int) -> set[date]:
        regions = _regions(self.country)
        if self.region and self.region in regions:
            return set(holidays.country_holidays(self.country, subdiv=self.region, years=year))
        try:
            national = set(holidays.country_holidays(self.country, years=year))
        except NotImplementedError:
            return set()
        if self.country not in REGIONAL_HOLIDAY_COUNTRIES:
            return national
        regions = tuple(region for region in regions if region.isupper() and len(region) == 2)
        counts: dict[date, int] = {}
        for region in regions:
            for day in holidays.country_holidays(self.country, subdiv=region, years=year):
                counts[day] = counts.get(day, 0) + 1
        return national | {day for day, count in counts.items() if 2 * count >= len(regions)}


def _regions(country: str) -> tuple[str, ...]:
    try:
        return tuple(holidays.country_holidays(country).subdivisions)
    except NotImplementedError:
        return ()


def region_choices(country: str | None) -> list[tuple[str, str]]:
    """(code, name) of the regions of ``country``, empty if it has none or is unknown.

    Blocks on the first use of a country (see ``PublicHolidays``)."""
    if not country:
        return []
    codes = _regions(country)
    if not codes:
        return []
    names: dict[str, str] = {}
    aliases = getattr(holidays.country_holidays(country).__class__, "subdivisions_aliases", None) or {}
    for name, code in aliases.items():
        # The first alias is the full name (``Vorarlberg`` before ``Vbg``).
        names.setdefault(code, name)
    return sorted(((code, names.get(code, code)) for code in codes), key=lambda choice: choice[1])


def region_from_lookup(data: object, country: str) -> str | None:
    """Region code from a Nominatim reverse lookup, if it lies in ``country``."""
    address = data.get("address") if isinstance(data, dict) else None
    code = address.get("ISO3166-2-lvl4") if isinstance(address, dict) else None
    if not isinstance(code, str) or "-" not in code:
        return None
    prefix, region = code.split("-", 1)
    return region if prefix.upper() == country.upper() and region else None


async def async_suggest_region(hass: HomeAssistant) -> str | None:
    """Region of the home from Nominatim, None if it cannot be found."""
    country = hass.config.country
    if not country or not await hass.async_add_executor_job(region_choices, country):
        return None
    params = {
        "lat": f"{hass.config.latitude:.{COORDINATE_DECIMALS}f}",
        "lon": f"{hass.config.longitude:.{COORDINATE_DECIMALS}f}",
        "zoom": str(NOMINATIM_ZOOM),
        "format": "jsonv2",
    }
    try:
        async with asyncio.timeout(NOMINATIM_TIMEOUT_S):
            response = await async_get_clientsession(hass).get(
                NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.info("Region of the home not found at Nominatim: %s", err)
        return None
    return region_from_lookup(data, country)
