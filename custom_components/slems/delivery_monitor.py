"""Detection of batteries that accept commands but do not deliver power.

After Omnibattery's non-responsive tracker. Judged with every battery poll in
operating mode active:

* A failure is a command of at least ``MIN_JUDGED_W`` in one direction, given
  longer than ``ENGAGE_GRACE_S`` ago in that direction, while the battery
  delivers less than ``DELIVERY_RATIO`` of it; also a failed write or a set
  point that reads back differently (see the driver).
* Not a failure: no charging at ``FULL_SOC_PCT`` or more (BMS full) or at the
  top of the charge (see below), no discharging at
  ``BMS_DISCHARGE_CUTOFF_SOC_PCT`` or less (the BMS may cut off early, e.g.
  with a weak cell sagging under a high load).
* A battery that does not charge at the top of the charge (highest cell at
  ``BMS_END_CELL_V`` or more, without cell voltages a SoC of
  ``BMS_END_SOC_PCT`` or more) for ``FAIL_THRESHOLD`` polls is *full by the
  BMS*: the BMS ends the charge once one cell is full, with unequal cells
  well below 100 % SoC, and some firmware keeps the SoC then. Such a battery
  may alternate between charging and standby every few seconds, so only
  ``FAIL_THRESHOLD`` charging polls in a row count it again. Charging stays
  blocked until it discharged or left the top (highest cell below
  ``TOP_EXIT_CELL_V``, without cell voltages the SoC
  ``BMS_STOP_RELEASE_PCT`` below the SoC at the stop).
* A battery that does not discharge at that low SoC for ``FAIL_THRESHOLD``
  consecutive polls is *stopped by the BMS* (communication works, nothing is
  excluded). The BMS stops earlier under a high load, so the first stop only
  limits its discharging to ``BMS_RETRY_RATIO`` of the command (at least
  ``BMS_RETRY_MIN_W``); a stop at that power, or a command too small to
  lower, blocks it. Both last until it charged or its SoC rose by
  ``BMS_STOP_RELEASE_PCT`` above the SoC at the first stop. Without them the
  battery would stay planned with power it does not deliver.
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

from .cell_balancing import BMS_END_CELL_V, BMS_END_SOC_PCT, TOP_EXIT_CELL_V

_LOGGER = logging.getLogger(__name__)

FAIL_THRESHOLD = 3
COOLDOWN_S = 300.0
ENGAGE_GRACE_S = 30.0
MIN_JUDGED_W = 100.0
DELIVERY_RATIO = 0.1
FULL_SOC_PCT = 99.0
BMS_DISCHARGE_CUTOFF_SOC_PCT = 20.0
BMS_STOP_RELEASE_PCT = 2.0
BMS_RETRY_RATIO = 0.5
BMS_RETRY_MIN_W = 300.0


class Action(StrEnum):
    NONE = "none"
    WAKE = "wake"
    EXCLUDE = "exclude"
    BMS_STOP = "bms_stop"
    BMS_FULL = "bms_full"


class DeliveryMonitor:
    """Failure count and exclusion of one battery (monotonic time)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.fail_count = 0
        self.reason: str | None = None
        self.excluded_until: float | None = None
        self._wake_used = False
        # SoC (%) at which the BMS first stopped discharging and the allowed
        # discharge power since (0: blocked); None while not stopped.
        self.bms_stop_soc: float | None = None
        self.bms_limit_w: float | None = None
        self._bms_limited_at = 0.0
        self._stop_count = 0
        # SoC (%) at which the BMS ended the charge at the top; None while not
        # full by the BMS. Refused and accepted charging polls in a row before.
        self.bms_full_soc: float | None = None
        self._refused_top = 0
        self._accepted_top = 0

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
        self._stop_count = 0

    @property
    def bms_full(self) -> bool:
        return self.bms_full_soc is not None

    def update_full(
        self, soc_pct: float | None, delivered_w: float | None, max_cell_v: float | None
    ) -> bool:
        """End a full stop once the battery discharged or left the top; True if ended."""
        if self.bms_full_soc is None:
            return False
        discharged = delivered_w is not None and delivered_w <= -MIN_JUDGED_W
        if max_cell_v is not None:
            left_top = max_cell_v < TOP_EXIT_CELL_V
        else:
            left_top = soc_pct is not None and soc_pct <= self.bms_full_soc - BMS_STOP_RELEASE_PCT
        if not (discharged or left_top):
            return False
        _LOGGER.info("Battery %s: charging allowed again after the BMS ended the charge", self._name)
        self.bms_full_soc = None
        return True

    def update_stop(self, soc_pct: float | None, delivered_w: float | None) -> bool:
        """End a BMS stop once the battery charged or its SoC rose; True if ended.

        ``delivered_w`` is the measured AC power (+charge / -discharge).
        """
        if self.bms_stop_soc is None:
            return False
        charged = delivered_w is not None and delivered_w >= MIN_JUDGED_W
        risen = soc_pct is not None and soc_pct >= self.bms_stop_soc + BMS_STOP_RELEASE_PCT
        if not (charged or risen):
            return False
        _LOGGER.info("Battery %s: discharging allowed again after the BMS stop", self._name)
        self.bms_stop_soc = None
        self.bms_limit_w = None
        return True

    def observe(
        self,
        now: float,
        commanded_w: float,
        direction_since: float,
        delivered_w: float | None,
        soc_pct: float | None,
        max_cell_v: float | None = None,
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
            if charging:
                self._accepted_top += 1
                if self._accepted_top >= FAIL_THRESHOLD:
                    self._refused_top = 0
            return Action.NONE
        if charging and soc_pct is not None and soc_pct >= FULL_SOC_PCT:
            # The BMS protects the battery; communication works.
            self.clear()
            return Action.NONE
        if charging and self._at_top(soc_pct, max_cell_v):
            # The BMS ended the charge below 100 % SoC; communication works.
            self.fail_count = 0
            self.reason = None
            self._accepted_top = 0
            self._refused_top += 1
            if self._refused_top < FAIL_THRESHOLD or self.bms_full_soc is not None:
                return Action.NONE
            self._refused_top = 0
            self.bms_full_soc = soc_pct if soc_pct is not None else 100.0
            _LOGGER.info(
                "Battery %s: the BMS ended the charge at %s %% (highest cell %s V), charging blocked",
                self._name, soc_pct, max_cell_v,
            )
            return Action.BMS_FULL
        if not charging and soc_pct is not None and soc_pct <= BMS_DISCHARGE_CUTOFF_SOC_PCT:
            # The BMS protects the battery; communication works, but the
            # battery must not stay planned with power it does not deliver.
            self.fail_count = 0
            self.reason = None
            if self.bms_limit_w and now - self._bms_limited_at < ENGAGE_GRACE_S:
                return Action.NONE  # the lower power is just taking effect
            self._stop_count += 1
            if self._stop_count < FAIL_THRESHOLD:
                return Action.NONE
            self._stop_count = 0
            retry_w = max(BMS_RETRY_RATIO * -commanded_w, BMS_RETRY_MIN_W)
            if self.bms_limit_w is None and retry_w < -commanded_w:
                self.bms_limit_w = retry_w
                self._bms_limited_at = now
                outcome = f"retrying with {retry_w:.0f} W"
            else:
                self.bms_limit_w = 0.0
                outcome = "discharging blocked"
            if self.bms_stop_soc is None:
                self.bms_stop_soc = soc_pct
            _LOGGER.info(
                "Battery %s: the BMS stopped discharging at %.1f %% "
                "(commanded %.0f W, delivered %.0f W), %s",
                self._name, soc_pct, commanded_w, delivered_w, outcome,
            )
            return Action.BMS_STOP
        detail = f"commanded {commanded_w:.0f} W, delivered {delivered_w:.0f} W"
        return self._fail(now, "charge_not_delivered" if charging else "discharge_not_delivered", detail, True)

    @staticmethod
    def _at_top(soc_pct: float | None, max_cell_v: float | None) -> bool:
        if max_cell_v is not None:
            return max_cell_v >= BMS_END_CELL_V
        return soc_pct is not None and soc_pct >= BMS_END_SOC_PCT

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
