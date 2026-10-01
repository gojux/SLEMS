"""Grid power read directly from a SunSpec meter over Modbus.

The smart meter entity of an inverter integration is updated once per polling
cycle of that integration (often about once per second). Reading the few
meter registers directly every ``interval_s`` gives the controller each new
value sooner. The connection comes from the Home Assistant Modbus backend
(``async_get_unit``), so it is shared with other integrations using it; while
the inverter integration keeps its own connection, a Modbus proxy in front of
the inverter is needed.

* ``find_meters`` walks the SunSpec model chain and returns the meters
  (models 201-204) with the address of their total real power.
* ``detect_inversion`` compares readings with the grid entity to find the sign.
* ``ModbusGridMeter`` polls the power. A value counts as fresh for
  ``max(FRESH_INTERVALS × interval, FRESH_MIN_S)``; otherwise the coordinator
  uses the entity. After ``BACKOFF_AFTER`` failed reads in a row it waits
  ``BACKOFF_MIN_S``, doubling up to ``BACKOFF_MAX_S``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import statistics
import time

_LOGGER = logging.getLogger(__name__)

type Reader = Callable[[int, int], Awaitable[list[int]]]

SUNSPEC_ID = [0x5375, 0x6E53]  # "SunS"
SUNSPEC_BASES = (40000, 0, 50000)
COMMON_MODEL = 1
METER_MODELS = frozenset({201, 202, 203, 204})
END_MODEL = 0xFFFF
MAX_MODELS = 40
# Total real power (W) in the meter models, after the two header registers;
# W_SF follows the three phase values.
POWER_OFFSET = 16
POWER_BLOCK = 5
NOT_IMPLEMENTED = 0x8000

READ_TIMEOUT_S = 1.0
FRESH_INTERVALS = 3
FRESH_MIN_S = 5.0
BACKOFF_AFTER = 3
BACKOFF_MIN_S = 5.0
BACKOFF_MAX_S = 60.0
ROUND_TRIPS_KEPT = 300

# Sign check against the entity: readings below this say nothing about the sign.
SIGN_MIN_W = 100.0
SIGN_TOLERANCE_W = 150.0
SIGN_TOLERANCE_SHARE = 0.25


class SunSpecError(Exception):
    """No SunSpec model chain or no meter in it."""


class SignUndecidableError(Exception):
    """The grid power is too small to compare the signs."""


class SignMismatchError(Exception):
    """Neither sign matches the entity."""


@dataclass(frozen=True)
class SunSpecMeter:
    """A meter in the SunSpec model chain."""

    power_register: int
    model_id: int
    manufacturer: str
    model: str
    option: str


def _int16(word: int) -> int:
    return word - 0x10000 if word >= 0x8000 else word


def _text(words: list[int]) -> str:
    raw = b"".join(bytes(((word >> 8) & 0xFF, word & 0xFF)) for word in words)
    return raw.decode("ascii", errors="ignore").replace("\x00", "").strip()


async def find_meters(read: Reader) -> list[SunSpecMeter]:
    """Meters of the SunSpec model chain.

    Raises ``SunSpecError`` without a chain, or the error of the reads if the
    device answered none of them.
    """
    answered = False
    failure: Exception | None = None
    for base in SUNSPEC_BASES:
        try:
            words = await read(base, 2)
        except Exception as err:  # noqa: BLE001 - an unsupported base may answer with an error
            failure = err
            continue
        answered = True
        if words != SUNSPEC_ID:
            continue
        meters: list[SunSpecMeter] = []
        # Manufacturer, model and option of the last common block: a meter
        # follows the common block describing it.
        common = ("", "", "")
        address = base + 2
        for _ in range(MAX_MODELS):
            model_id, length = await read(address, 2)
            if model_id == END_MODEL or length == 0:
                break
            if model_id == COMMON_MODEL:
                words = await read(address + 2, 40)
                common = (_text(words[0:16]), _text(words[16:32]), _text(words[32:40]))
            elif model_id in METER_MODELS:
                meters.append(SunSpecMeter(address + 2 + POWER_OFFSET, model_id, *common))
            address += 2 + length
        return meters
    if not answered and failure is not None:
        raise failure
    raise SunSpecError("no SunSpec model chain")


def decode_power(words: list[int]) -> float | None:
    """Total real power (W) from the block W, WphA, WphB, WphC, W_SF."""
    if len(words) < POWER_BLOCK or words[0] == NOT_IMPLEMENTED or words[4] == NOT_IMPLEMENTED:
        return None
    return _int16(words[0]) * 10.0 ** _int16(words[4])


async def read_power(read: Reader, power_register: int) -> float | None:
    return decode_power(await read(power_register, POWER_BLOCK))


def detect_inversion(pairs: list[tuple[float, float]]) -> bool:
    """Whether the Modbus value has the opposite sign of the entity.

    ``pairs``: (Modbus value, entity value with the SLEMS sign) read at about
    the same time.
    """
    usable = [(m, e) for m, e in pairs if min(abs(m), abs(e)) >= SIGN_MIN_W]
    if not usable:
        raise SignUndecidableError
    same = sum(abs(m - e) for m, e in usable)
    inverted = sum(abs(m + e) for m, e in usable)
    tolerance = sum(max(SIGN_TOLERANCE_W, SIGN_TOLERANCE_SHARE * abs(e)) for _, e in usable)
    if min(same, inverted) > tolerance:
        raise SignMismatchError
    return inverted < same


class ModbusGridMeter:
    """Polls the grid power of a SunSpec meter."""

    def __init__(
        self,
        read: Reader,
        power_register: int,
        interval_s: float,
        inverted: bool,
        on_value: Callable[[float], None],
    ) -> None:
        self._read = read
        self.power_register = power_register
        self.interval_s = interval_s
        self._inverted = inverted
        self._on_value = on_value
        self.value: float | None = None
        # Monotonic time of the last value and since when reads fail.
        self.updated: float | None = None
        self.failing_since: float | None = None
        self.started = time.monotonic()
        self.reads = 0
        self.errors = 0
        self.last_error: str | None = None
        self._failures = 0
        self._round_trips: deque[float] = deque(maxlen=ROUND_TRIPS_KEPT)

    @property
    def fresh_s(self) -> float:
        return max(FRESH_INTERVALS * self.interval_s, FRESH_MIN_S)

    def fresh(self, now: float) -> bool:
        return self.updated is not None and now - self.updated <= self.fresh_s

    def unavailable_for(self, now: float) -> float:
        """How long no fresh value has been available (0 while fresh)."""
        if self.fresh(now):
            return 0.0
        since = self.updated + self.fresh_s if self.updated is not None else self.started
        return max(0.0, now - since)

    async def poll(self) -> bool:
        """Read the power once; True if a value arrived."""
        start = time.monotonic()
        try:
            value = await asyncio.wait_for(
                read_power(self._read, self.power_register), READ_TIMEOUT_S
            )
            if value is None:
                raise ValueError("register not implemented")
        except Exception as err:  # noqa: BLE001 - every failure falls back to the entity
            self.errors += 1
            self._failures += 1
            self.last_error = f"{type(err).__name__}: {err}"
            if self.failing_since is None:
                self.failing_since = start
            _LOGGER.debug("Grid meter read failed: %s", self.last_error)
            return False
        end = time.monotonic()
        self.reads += 1
        self._failures = 0
        self.failing_since = None
        self._round_trips.append(end - start)
        self.value = -value if self._inverted else value
        self.updated = end
        self._on_value(self.value)
        return True

    async def run(self) -> None:
        """Poll every ``interval_s`` until cancelled."""
        next_tick = time.monotonic()
        while True:
            await self.poll()
            if self._failures >= BACKOFF_AFTER:
                wait = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** (self._failures - BACKOFF_AFTER))
                await asyncio.sleep(wait)
                next_tick = time.monotonic()
                continue
            next_tick += self.interval_s
            delay = next_tick - time.monotonic()
            if delay < 0:
                # A read took longer than the interval: continue from now.
                next_tick = time.monotonic()
                delay = 0
            await asyncio.sleep(delay)

    def diagnostics(self, now: float) -> dict:
        trips = sorted(self._round_trips)
        return {
            "power_register": self.power_register,
            "interval_s": self.interval_s,
            "fresh": self.fresh(now),
            "age_s": None if self.updated is None else round(now - self.updated, 2),
            "reads": self.reads,
            "errors": self.errors,
            "last_error": self.last_error,
            "round_trip_median_ms": round(statistics.median(trips) * 1000) if trips else None,
            "round_trip_p95_ms": round(trips[int(0.95 * (len(trips) - 1))] * 1000) if trips else None,
        }
