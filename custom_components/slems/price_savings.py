"""Measured saving of the price aware control, added up per month.

A *run* starts when a price plan (hold, grid charging, feed-in) exists and
ends when it is gone (PV takes over) or after ``MAX_RUN``. Only runs in which
the control acted (strategy price hold, grid charging or feed-in from the
batteries in active mode) are evaluated: once the hourly statistics of their
hours exist, their recorded costs are compared with the same hours played *as
usual* from the measured state of charge at their start (see
``price_backtest.measured_saving``). Runs without an action count nothing, so
the battery model's inaccuracy does not add up on ordinary days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

MAX_RUN = timedelta(hours=24)
# Pending runs older than this are dropped (statistics missing).
DROP_AFTER = timedelta(days=3)


def _hour(moment: datetime) -> datetime:
    return dt_util.as_local(moment).replace(minute=0, second=0, microsecond=0)


@dataclass
class PriceRun:
    start: datetime
    start_wh: float
    end: datetime | None = None
    acted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "start_wh": round(self.start_wh),
            "end": self.end.isoformat() if self.end else None,
            "acted": self.acted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceRun:
        return cls(
            start=dt_util.parse_datetime(data["start"]),
            start_wh=float(data["start_wh"]),
            end=dt_util.parse_datetime(data["end"]) if data.get("end") else None,
            acted=bool(data.get("acted")),
        )


@dataclass
class PriceSavings:
    month: str | None = None
    total_eur: float = 0.0
    runs: int = 0
    last: dict[str, Any] | None = None
    run: PriceRun | None = None
    pending: list[PriceRun] = field(default_factory=list)

    def observe(self, now: datetime, plan_active: bool, stored_wh: float | None, acted: bool) -> bool:
        """Follow the current run; True when a run ended (to be saved)."""
        hour = _hour(now)
        ended = False
        if self.run is not None and (not plan_active or hour >= self.run.start + MAX_RUN):
            ended = self._finish(hour)
        if plan_active and self.run is None and stored_wh is not None:
            self.run = PriceRun(start=hour, start_wh=stored_wh)
        if self.run is not None and acted:
            self.run.acted = True
        return ended

    def _finish(self, hour: datetime) -> bool:
        run, self.run = self.run, None
        if run is None or not run.acted or hour <= run.start:
            return False
        run.end = hour
        self.pending.append(run)
        return True

    def due(self, now: datetime, statistics_delay: timedelta) -> list[PriceRun]:
        """Pending runs whose hours all have statistics; drops runs that are too old."""
        self.pending = [run for run in self.pending if run.end and now - run.end < DROP_AFTER]
        return [run for run in self.pending if run.end and now - statistics_delay >= run.end]

    def add(self, run: PriceRun, saving_eur: float) -> None:
        month = f"{run.start:%Y-%m}"
        if month != self.month:
            self.month, self.total_eur, self.runs = month, 0.0, 0
        self.total_eur += saving_eur
        self.runs += 1
        self.last = {**run.as_dict(), "saving_eur": round(saving_eur, 2)}
        self.pending = [other for other in self.pending if other is not run]

    def total_now(self, now: datetime) -> float:
        """The total of the current month (0 after a new month began)."""
        return self.total_eur if self.month == f"{dt_util.as_local(now):%Y-%m}" else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "total_eur": round(self.total_eur, 2),
            "runs": self.runs,
            "last": self.last,
            "run": self.run.as_dict() if self.run else None,
            "pending": [run.as_dict() for run in self.pending],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PriceSavings:
        if not data:
            return cls()
        try:
            return cls(
                month=data.get("month"),
                total_eur=float(data.get("total_eur") or 0.0),
                runs=int(data.get("runs") or 0),
                last=data.get("last"),
                run=PriceRun.from_dict(data["run"]) if data.get("run") else None,
                pending=[PriceRun.from_dict(run) for run in data.get("pending") or []],
            )
        except (KeyError, TypeError, ValueError):
            return cls()
