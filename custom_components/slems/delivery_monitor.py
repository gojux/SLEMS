"""Detection of batteries that accept commands but do not deliver power.

After Omnibattery's non-responsive tracker. Judged with every battery poll in
operating mode active:

* A failure is a command of at least ``MIN_JUDGED_W`` in one direction, given
  longer than ``ENGAGE_GRACE_S`` ago in that direction, while the battery
  delivers less than ``DELIVERY_RATIO`` of it; also a failed write or a set
  point that reads back differently (see the driver).
* Not a failure: no charging at ``FULL_SOC_PCT`` or more (BMS full), no
  discharging at ``BMS_DISCHARGE_CUTOFF_SOC_PCT`` or less (the BMS may cut off
  early, e.g. with a weak cell).
* After ``FAIL_THRESHOLD`` consecutive failures the first time a wake attempt
  (all control registers are written again, RS485 control included) and the
  count starts again; the next time the battery is excluded for
  ``COOLDOWN_S``: treated like a disabled battery and handed back to its own
  logic, then retried. Communication failures skip the wake attempt. A
  battery that delivers resets everything.
"""

from __future__ import annotations

from enum import StrEnum
import logging

_LOGGER = logging.getLogger(__name__)

FAIL_THRESHOLD = 3
COOLDOWN_S = 300.0
ENGAGE_GRACE_S = 30.0
MIN_JUDGED_W = 100.0
DELIVERY_RATIO = 0.1
FULL_SOC_PCT = 99.0
BMS_DISCHARGE_CUTOFF_SOC_PCT = 20.0


class Action(StrEnum):
    NONE = "none"
    WAKE = "wake"
    EXCLUDE = "exclude"


class DeliveryMonitor:
    """Failure count and exclusion of one battery (monotonic time)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.fail_count = 0
        self.reason: str | None = None
        self.excluded_until: float | None = None
        self._wake_used = False

    def excluded(self, now: float) -> bool:
        return self.excluded_until is not None and now < self.excluded_until

    def tick(self, now: float) -> None:
        """End an exclusion whose cooldown is over (the battery is retried)."""
        if self.excluded_until is not None and now >= self.excluded_until:
            _LOGGER.info("Battery %s: retrying after it did not respond", self._name)
            self.excluded_until = None
            self.fail_count = 0
            self._wake_used = False

    def clear(self) -> None:
        if self.fail_count or self.reason:
            _LOGGER.debug("Battery %s delivers again", self._name)
        self.fail_count = 0
        self.reason = None
        self._wake_used = False

    def observe(
        self,
        now: float,
        commanded_w: float,
        direction_since: float,
        delivered_w: float | None,
        soc_pct: float | None,
    ) -> Action:
        """Judge one poll: commanded and delivered AC power (+charge / -discharge)."""
        if self.excluded(now) or delivered_w is None:
            return Action.NONE
        charging = commanded_w > 0
        if abs(commanded_w) < MIN_JUDGED_W or now - direction_since < ENGAGE_GRACE_S:
            return Action.NONE
        delivered = delivered_w if charging else -delivered_w
        if delivered >= DELIVERY_RATIO * abs(commanded_w):
            self.clear()
            return Action.NONE
        if soc_pct is not None and (
            (charging and soc_pct >= FULL_SOC_PCT)
            or (not charging and soc_pct <= BMS_DISCHARGE_CUTOFF_SOC_PCT)
        ):
            # The BMS protects the battery; communication works.
            self.clear()
            return Action.NONE
        detail = f"commanded {commanded_w:.0f} W, delivered {delivered_w:.0f} W"
        return self._fail(now, "charge_not_delivered" if charging else "discharge_not_delivered", detail, True)

    def record_comm_failure(self, now: float) -> Action:
        """A write failed or the set point did not read back as written."""
        if self.excluded(now):
            return Action.NONE
        return self._fail(now, "write_failed", "command not confirmed", False)

    def _fail(self, now: float, reason: str, detail: str, allow_wake: bool) -> Action:
        self.fail_count += 1
        self.reason = reason
        _LOGGER.debug(
            "Battery %s: %s (%d/%d)", self._name, detail, self.fail_count, FAIL_THRESHOLD
        )
        if self.fail_count < FAIL_THRESHOLD:
            return Action.NONE
        self.fail_count = 0
        if allow_wake and not self._wake_used:
            self._wake_used = True
            _LOGGER.info("Battery %s does not deliver (%s), writing control again", self._name, detail)
            return Action.WAKE
        self.excluded_until = now + COOLDOWN_S
        _LOGGER.warning(
            "Battery %s does not respond (%s), excluded for %d minutes",
            self._name,
            reason,
            COOLDOWN_S / 60,
        )
        return Action.EXCLUDE
