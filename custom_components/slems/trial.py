"""Trial of a battery with a Modbus profile (see self_test for its checks).

A battery from a profile is not controlled before its self-test with the
checks of the profile passed. It is measured meanwhile like a disabled one
and runs on its own logic; SLEMS writes to it only during the test.

* trial: waiting for the test or running it. It starts by itself as soon as
  a self-test is possible.
* unclear: nothing failed, but not everything was checkable (e.g. a restless
  house). Tried again after ``RETRY_S``; the user may confirm it (repair).
* failed: a check failed. It stays so until the profile changes or a test
  started by hand passes; it cannot be confirmed.
* passed / confirmed: controlled like any other battery.
* observing: after a firmware update of a battery that had passed. The
  profile is proven, only the firmware is new, so no full test with the
  others held: the write registers are read at rest first (as in the trial,
  nothing written), then the battery is controlled while SLEMS watches it
  closely for ``OBSERVE_S`` (``Observation``). Anything that does not fit
  hands it back and begins the full trial.

A trial begins for a new battery, when the profile changed (its key: id and
revision, for an own profile with the hash of its content) and when the
battery got another address (it may be another device). A firmware update shows in the device
information: a value differs from the one read before (only keys read both
times count, a failed read changes nothing).

The observation, after the registers stayed unchanged at rest:

* the first commands of each direction are limited to the small step of the
  trial until the battery delivered at least half of one for ``SHOWN_S``;
  its own power beyond three times the command (two times once shown) or
  against it for ``ABORT_S`` is a wrong factor or direction,
* while it charges or discharges, the write registers are read every
  ``REGISTER_READ_S`` until it did so for ``OBSERVE_DIRECTION_S``: a value
  other than the one written, seen twice in a row with no write between the
  reads, falls back or was changed (a power cut by the firmware is noted),
* after the next release they are read at once and after
  ``RELEASE_LATE_S`` (only while it is not controlled again meanwhile),
* not delivering the commanded power (``DeliveryMonitor``) is a finding too.

It ends after ``OBSERVE_S`` once both directions ran for
``OBSERVE_DIRECTION_S`` and a release was checked; until then the battery
stays controlled.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from .battery_limits import PowerLimits
from .self_test import (
    ABORT_S,
    QUIET_S,
    REACHED_SHARE,
    REGISTER_READ_S,
    RELEASE_LATE_S,
    SMALL_ABORT_S,
    SMALL_TOO_LARGE_FACTOR,
    TOO_LARGE_FACTOR,
    Check,
    Outcome,
    Registers,
    TrialSpec,
    change_kind,
    quiet_check,
    release_problems,
)

RETRY_S = 600.0
OBSERVE_S = 24 * 3600.0
OBSERVE_DIRECTION_S = 3600.0
SHOWN_S = 10.0
# Gaps between two samples longer than this do not count as time in a direction.
MAX_SAMPLE_GAP_S = 30.0
# Checks that may stay unclear: the small step of a device without an effect
# at that power (the full step checks the effect).
MAY_BE_UNCLEAR = frozenset({"trial_small_charge", "trial_small_discharge"})
_FINISHED = frozenset({"ok", "warning", "unclear", "failed"})


class TrialStatus(StrEnum):
    TRIAL = "trial"
    UNCLEAR = "unclear"
    FAILED = "failed"
    PASSED = "passed"
    CONFIRMED = "confirmed"
    OBSERVING = "observing"


class TrialReason(StrEnum):
    NEW = "new"
    PROFILE = "profile"
    FIRMWARE = "firmware"


def firmware_change(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """Device information that changed: key -> [before, after]."""
    return {key: [before[key], after[key]] for key in sorted(before.keys() & after.keys()) if before[key] != after[key]}


def judge(result: dict[str, Any]) -> TrialStatus | None:
    """The status after a test (``SelfTest.as_dict`` of a trial); None if it
    stopped early (cancelled, not active ...)."""
    if result.get("result") not in _FINISHED:
        return None
    checks = list(result.get("checks") or []) + list((result.get("trial") or {}).get("checks") or [])
    outcomes = {check["key"]: check["outcome"] for check in checks}
    if "failed" in outcomes.values():
        return TrialStatus.FAILED
    if any(outcome == "unclear" and key not in MAY_BE_UNCLEAR for key, outcome in outcomes.items()):
        return TrialStatus.UNCLEAR
    trial_keys = {key for key in outcomes if key.startswith("trial_")}
    # A test without the checks of the profile (e.g. ended at rest) proves nothing.
    if "trial_quiet" not in trial_keys or "trial_release" not in trial_keys:
        return TrialStatus.UNCLEAR
    return TrialStatus.PASSED


@dataclass
class Observation:
    """Normal operation watched closely after a firmware update (see the module docstring)."""

    # Wall clock (UNIX time) of the begin.
    started: float
    # The registers stayed unchanged at rest: controlled from then on.
    quiet_ok: bool = False
    # Time it charged and discharged as commanded, the directions shown at the
    # small step, a release checked, and what was noticed without a finding.
    charge_s: float = 0.0
    discharge_s: float = 0.0
    shown: set[str] = field(default_factory=set)
    release_ok: bool = False
    notes: list[dict[str, Any]] = field(default_factory=list)
    _quiet_reads: list[tuple[float, Registers]] = field(default_factory=list, repr=False)
    _read_at: float | None = field(default=None, repr=False)
    _sample_at: float | None = field(default=None, repr=False)
    _shown_since: dict[str, float] = field(default_factory=dict, repr=False)
    _excess_since: float | None = field(default=None, repr=False)
    _against_since: float | None = field(default=None, repr=False)
    # Registers read once with another value than written (address -> value).
    _suspect: dict[int, float] = field(default_factory=dict, repr=False)
    _release_at: float | None = field(default=None, repr=False)
    _release_reads: list[tuple[float, Registers]] = field(default_factory=list, repr=False)

    def seconds(self, direction: str) -> float:
        return self.charge_s if direction == "charge" else self.discharge_s

    def registers_due(self, now: float, direction: str | None, released: bool) -> bool:
        """Whether the write registers are to be read with the next poll: at rest
        first, while running a direction not watched long enough yet, after a release
        (``direction``: of the command, None while not controlled)."""
        due = self._read_at is None or now - self._read_at >= REGISTER_READ_S
        if not self.quiet_ok:
            return due
        if self._release_at is not None and released:
            if not self._release_reads:
                return True
            return now - self._release_at >= RELEASE_LATE_S and len(self._release_reads) < 2
        return direction is not None and self.seconds(direction) < OBSERVE_DIRECTION_S and due

    def quiet(self, spec: TrialSpec, now: float, registers: Registers) -> Check | None:
        """The registers at rest before anything is written (as in the trial);
        an unclear outcome starts the reads again."""
        self._read_at = now
        self._quiet_reads.append((now, registers))
        check = quiet_check(spec, self._quiet_reads, final=now - self._quiet_reads[0][0] >= QUIET_S)
        if check is None:
            return None
        if check.outcome is Outcome.OK:
            self.quiet_ok = True
        self._quiet_reads.clear()
        return check

    def cap(self, limits: PowerLimits, small_w: float) -> PowerLimits:
        """The allowed power with the directions not shown yet limited to the small step."""
        charge = limits.charge_w if "charge" in self.shown else min(limits.charge_w, small_w)
        discharge = limits.discharge_w if "discharge" in self.shown else min(limits.discharge_w, small_w)
        if (charge, discharge) == (limits.charge_w, limits.discharge_w):
            return limits
        return replace(
            limits,
            charge_w=charge,
            discharge_w=discharge,
            charge_reason=limits.charge_reason if charge == limits.charge_w else "power",
            discharge_reason=limits.discharge_reason if discharge == limits.discharge_w else "power",
        )

    def observe_power(
        self, now: float, command_w: float | None, power_w: float | None, small_w: float
    ) -> dict[str, Any] | None:
        """The battery's own power against the command; a finding or None."""
        gap = 0.0 if self._sample_at is None else min(now - self._sample_at, MAX_SAMPLE_GAP_S)
        self._sample_at = now
        if command_w is None or power_w is None or abs(command_w) < 1:
            self._excess_since = self._against_since = None
            self._shown_since.clear()
            return None
        direction = "charge" if command_w > 0 else "discharge"
        size = abs(command_w)
        along = power_w if command_w > 0 else -power_w
        if along >= REACHED_SHARE * size and size >= REACHED_SHARE * small_w:
            if direction in self.shown:
                if direction == "charge":
                    self.charge_s += gap
                else:
                    self.discharge_s += gap
            elif now - self._shown_since.setdefault(direction, now) >= SHOWN_S:
                self.shown.add(direction)
        else:
            self._shown_since.pop(direction, None)
        factor, hold = (
            (TOO_LARGE_FACTOR, ABORT_S) if direction in self.shown else (SMALL_TOO_LARGE_FACTOR, SMALL_ABORT_S)
        )
        values = {"command_w": round(command_w), "power_w": round(power_w)}
        if along >= factor * max(size, small_w):
            self._excess_since = now if self._excess_since is None else self._excess_since
            if now - self._excess_since >= hold:
                return {"reason": "too_large", **values}
        else:
            self._excess_since = None
        if size >= REACHED_SHARE * small_w and along <= -REACHED_SHARE * size:
            self._against_since = now if self._against_since is None else self._against_since
            if now - self._against_since >= ABORT_S:
                return {"reason": "reversed", **values}
        else:
            self._against_since = None
        return None

    def observe_registers(
        self, spec: TrialSpec, now: float, registers: Registers, written: dict[int, float]
    ) -> dict[str, Any] | None:
        """The registers against what was written (no write during the read); a finding or None."""
        self._read_at = now
        suspect: dict[int, float] = {}
        for register in spec.registers:
            before, after = written.get(register.address), registers.get(register.address)
            if before is None or after is None or after == before:
                continue
            kind = change_kind(register, before, after)
            change = {"address": register.address, "written": before, "read": after}
            if kind == "clamped":
                if change not in self.notes:
                    self.notes.append(change)
                continue
            if self._suspect.get(register.address) == after:
                return {"reason": kind, **change}
            suspect[register.address] = after
        self._suspect = suspect
        return None

    def released(self, now: float) -> None:
        """Handed back to its own logic: its registers are read next."""
        if self.quiet_ok and not self.release_ok:
            self._release_at = now
            self._release_reads = []

    def taken_over(self) -> None:
        """Controlled again before the release was read completely: the next release counts."""
        self._release_at = None
        self._release_reads = []

    @property
    def release_pending(self) -> bool:
        return self._release_at is not None

    def observe_release(self, spec: TrialSpec, now: float, registers: Registers) -> dict[str, Any] | None:
        """The registers after a release; a finding or None."""
        assert self._release_at is not None
        self._read_at = now
        self._release_reads.append((now, registers))
        wrong, unread = release_problems(spec, self._release_reads, self._release_at)
        if wrong:
            return {"reason": "release", "registers": wrong}
        if len(self._release_reads) >= 2:
            # Unread: tried again with the next release.
            self.release_ok = not unread
            self.taken_over()
        return None

    def complete(self, wall: float) -> bool:
        return (
            self.quiet_ok
            and self.release_ok
            and wall - self.started >= OBSERVE_S
            and min(self.charge_s, self.discharge_s) >= OBSERVE_DIRECTION_S
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "quiet_ok": self.quiet_ok,
            "charge_s": round(self.charge_s),
            "discharge_s": round(self.discharge_s),
            "shown": sorted(self.shown),
            "release_ok": self.release_ok,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        """A stored observation; the registers at rest are read again first."""
        return cls(
            started=data.get("started") or 0.0,
            charge_s=data.get("charge_s") or 0.0,
            discharge_s=data.get("discharge_s") or 0.0,
            shown=set(data.get("shown") or ()),
            release_ok=bool(data.get("release_ok")),
            notes=list(data.get("notes") or ()),
        )


@dataclass
class TrialState:
    """Trial of one battery (stored)."""

    profile_key: str
    status: TrialStatus = TrialStatus.TRIAL
    reason: TrialReason = TrialReason.NEW
    # Wall clock (UNIX time) of the begin, and of the next automatic test (None: as soon as possible).
    since: float | None = None
    retry_at: float | None = None
    # Device information read last (the reference for a firmware update) and what changed.
    device_info: dict[str, str] = field(default_factory=dict)
    firmware_change: dict[str, list[str]] = field(default_factory=dict)
    # SLEMS wrote during the trial and has not confirmed the release since:
    # only then a release is written (it writes the registers under trial).
    wrote: bool = False
    # The problems of the last test, as text for the repair.
    details: str | None = None
    # Normal operation watched after a firmware update (status observing).
    observation: Observation | None = None
    # The device checked (``BatteryDriver.trial_device``).
    device: str = ""

    @property
    def in_trial(self) -> bool:
        """Not controlled: waiting for or running the test, unclear or failed, or
        observed but its registers not read at rest yet."""
        if self.status is TrialStatus.OBSERVING:
            return self.observation is None or not self.observation.quiet_ok
        return self.status in (TrialStatus.TRIAL, TrialStatus.UNCLEAR, TrialStatus.FAILED)

    def due(self, wall: float) -> bool:
        """Whether a test is to start by itself now (if possible)."""
        if self.status not in (TrialStatus.TRIAL, TrialStatus.UNCLEAR):
            return False
        return self.retry_at is None or wall >= self.retry_at

    def begin(self, reason: TrialReason, wall: float, change: dict[str, list[str]] | None = None) -> None:
        self.status = TrialStatus.TRIAL
        self.reason = reason
        self.since = wall
        self.retry_at = None
        self.details = None
        self.firmware_change = change or {}
        self.observation = None

    def observe(self, wall: float, change: dict[str, list[str]]) -> None:
        """A firmware update of a battery that had passed: observed instead of a full trial."""
        self.begin(TrialReason.FIRMWARE, wall, change)
        self.status = TrialStatus.OBSERVING
        self.observation = Observation(wall)

    def observation_failed(self, wall: float, *, quiet: bool = False) -> None:
        """Something did not fit: the full trial (failed at once if the registers changed at rest)."""
        self.begin(TrialReason.FIRMWARE, wall, self.firmware_change)
        if quiet:
            self.status = TrialStatus.FAILED

    def observation_passed(self) -> None:
        self.status = TrialStatus.PASSED
        self.observation = None

    def finished(self, result: dict[str, Any], wall: float) -> None:
        """The test of the trial ended (``SelfTest.as_dict``)."""
        status = judge(result)
        if status is None:
            # Stopped early: started again later, a failed one only by hand.
            self.retry_at = wall + RETRY_S
            return
        self.status = status
        self.retry_at = wall + RETRY_S if status is TrialStatus.UNCLEAR else None

    def confirm(self) -> bool:
        """The user confirms an unclear trial; False if it cannot be confirmed."""
        if self.status is not TrialStatus.UNCLEAR:
            return False
        self.status = TrialStatus.CONFIRMED
        self.retry_at = None
        return True

    def device_info_read(self, info: dict[str, str], wall: float) -> bool:
        """Compare newly read device information; True if a trial began (firmware update)."""
        change = firmware_change(self.device_info, info)
        self.device_info = {**self.device_info, **info}
        if change and not self.in_trial:
            self.observe(wall, change)
            return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "status": self.status.value,
            "reason": self.reason.value,
            "since": self.since,
            "retry_at": self.retry_at,
            "device_info": dict(self.device_info),
            "firmware_change": dict(self.firmware_change),
            "wrote": self.wrote,
            "details": self.details,
            "observation": self.observation.as_dict() if self.observation is not None else None,
            "device": self.device,
        }

    @classmethod
    def restore(cls, data: dict[str, Any] | None, profile_key: str, wall: float, device: str = "") -> TrialState:
        """The stored state, or a new trial (no state yet, the profile or the device changed)."""
        if not data:
            return cls(profile_key, since=wall, device=device)
        state = cls(
            profile_key=data.get("profile_key", ""),
            status=TrialStatus(data.get("status", TrialStatus.TRIAL)),
            reason=TrialReason(data.get("reason", TrialReason.NEW)),
            since=data.get("since"),
            retry_at=data.get("retry_at"),
            device_info=dict(data.get("device_info") or {}),
            firmware_change=dict(data.get("firmware_change") or {}),
            wrote=bool(data.get("wrote")),
            details=data.get("details"),
            observation=Observation.from_dict(data["observation"]) if data.get("observation") else None,
            device=data.get("device", ""),
        )
        if state.status is TrialStatus.OBSERVING and state.observation is None:
            state.observation = Observation(wall)
        if state.profile_key != profile_key:
            state.profile_key = profile_key
            state.begin(TrialReason.PROFILE, wall)
        # A state stored before the device was kept adopts it.
        if device and state.device and state.device != device:
            # Another address may be another battery: its device information is no reference.
            state.begin(TrialReason.NEW, wall)
            state.device_info = {}
        state.device = device
        return state
