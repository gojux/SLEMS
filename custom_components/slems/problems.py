"""Problems reported to the user outside the dashboard.

Repairs (Settings → Repairs) for ongoing problems; they disappear by
themselves when the problem is gone:

* a battery does not deliver the commanded power and is excluded
  (``delivery_monitor``),
* a battery could not be read for ``UNREADABLE_AFTER_S``,
* the grid meter is stale in operating mode active (the batteries follow their
  own logic meanwhile),
* the grid power over Modbus is configured but has not been available for
  ``GRID_MODBUS_AFTER_S`` (SLEMS uses the entity meanwhile).

Notifications (bell) for events: the end of an active cell balancing run,
except when the user cancelled it. The problems of the feed-in cap come from
the forecast and are notifications as well; each one is created when the
problem appears and dismissed when it is gone: the batteries are too small for
the space needed, there is not enough time or power left to feed in before
the peak, their charge power is too low (energy would be curtailed), or the
export has been above the limit for ``CAP_EXCEEDED_AFTER_S``.

Issues are stored by Home Assistant across restarts; the first update after a
start (also after a reload, e.g. when a battery was removed) removes every
SLEMS issue that no longer applies, including those of removed batteries.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OperatingMode
from .controller import ControlStatus

if TYPE_CHECKING:
    from .coordinator import BatteryRuntime, SlemsCoordinator, SystemSnapshot

UNREADABLE_AFTER_S = 300.0
CAP_EXCEEDED_AFTER_S = 300.0
GRID_STALE_ISSUE = "grid_meter_stale"
GRID_MODBUS_ISSUE = "grid_modbus_unavailable"
GRID_MODBUS_AFTER_S = 300.0
# Feed-in cap problem -> translation key of its notification.
CAP_NOTIFICATIONS = {
    "battery_too_small": "feed_in_cap_battery_too_small",
    "too_late": "feed_in_cap_too_late",
    "charge_power_too_low": "feed_in_cap_charge_power",
    "limit_exceeded": "feed_in_cap_exceeded",
}


@callback
def async_remove_issues(hass: HomeAssistant) -> None:
    """Remove all SLEMS repair issues (the integration is removed)."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def _not_responding_issue(battery: BatteryRuntime) -> str:
    return f"battery_not_responding_{battery.subentry_id}"


def _unreadable_issue(battery: BatteryRuntime) -> str:
    return f"battery_unreadable_{battery.subentry_id}"


class ProblemReporter:
    """Keeps the repair issues of one SLEMS entry in line with the current state."""

    def __init__(self, hass: HomeAssistant, coordinator: SlemsCoordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        # Issue ids created by this instance; None until the first update.
        self._active: set[str] | None = None
        # Feed-in cap problems with a notification shown.
        self._cap_notified: set[str] = set()

    @callback
    def update(self, now: float, snapshot: SystemSnapshot | None = None) -> None:
        """Create or delete the repair issues (``now``: monotonic time)."""
        coordinator = self._coordinator
        wanted: dict[str, tuple[str, dict[str, str]]] = {}
        # Feed-in cap problems are notifications; issues with their ids are removed.
        possible = {GRID_STALE_ISSUE, GRID_MODBUS_ISSUE, *CAP_NOTIFICATIONS.values()}
        for battery in coordinator.batteries:
            possible |= {_not_responding_issue(battery), _unreadable_issue(battery)}
            placeholders = {"name": battery.name}
            if battery.not_responding:
                wanted[_not_responding_issue(battery)] = ("battery_not_responding", placeholders)
            since = battery.unreadable_since
            if since is not None and now - since >= UNREADABLE_AFTER_S:
                wanted[_unreadable_issue(battery)] = ("battery_unreadable", placeholders)
        if (
            coordinator.settings.operating_mode is OperatingMode.ACTIVE
            and coordinator.controller.status is ControlStatus.GRID_STALE
        ):
            wanted[GRID_STALE_ISSUE] = ("grid_meter_stale", {})
        meter = coordinator.grid_meter
        if coordinator.grid_meter_error is not None:
            wanted[GRID_MODBUS_ISSUE] = (
                "grid_modbus_unavailable", {"error": coordinator.grid_meter_error}
            )
        elif meter is not None and meter.unavailable_for(now) >= GRID_MODBUS_AFTER_S:
            wanted[GRID_MODBUS_ISSUE] = (
                "grid_modbus_unavailable", {"error": meter.last_error or "–"}
            )

        for issue_id, (translation_key, placeholders) in wanted.items():
            if self._active is None or issue_id not in self._active:
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=translation_key,
                    translation_placeholders=placeholders,
                )
        if self._active is None:
            # Also the issues of batteries that are no longer configured.
            possible |= {
                issue_id
                for domain, issue_id in ir.async_get(self._hass).issues
                if domain == DOMAIN
            }
        for issue_id in possible - wanted.keys():
            if self._active is None or issue_id in self._active:
                ir.async_delete_issue(self._hass, DOMAIN, issue_id)
        self._active = set(wanted)
        self._update_cap_notifications(now, snapshot)

    @callback
    def _update_cap_notifications(self, now: float, snapshot: SystemSnapshot | None) -> None:
        coordinator = self._coordinator
        cap = snapshot.feed_in_cap if snapshot is not None else None
        # Decimal comma in the languages that use it (the texts are German or English).
        comma = self._hass.config.language.startswith("de")

        def kwh(wh: float) -> str:
            text = f"{wh / 1000:.1f}"
            return text.replace(".", ",") if comma else text

        placeholders = {"limit": f"{coordinator.settings.feed_in_cap_limit_w:.0f}"}
        problems = set(cap.problems) if cap is not None else set()
        if cap is not None:
            block = cap.next_block
            placeholders |= {
                "peak": _clock(block.start) if block else "–",
                "required": kwh(cap.required_space_wh),
                "needed": kwh(cap.export_needed_wh),
                "possible": kwh(cap.export_possible_wh),
                "curtailed": kwh(cap.curtailed_wh),
            }
        since = coordinator.cap_exceeded_since
        if since is not None and now - since >= CAP_EXCEEDED_AFTER_S:
            problems.add("limit_exceeded")
        for problem in problems - self._cap_notified:
            self._hass.async_create_task(self._async_notify_cap(problem, placeholders))
        for problem in self._cap_notified - problems:
            persistent_notification.async_dismiss(self._hass, _cap_notification_id(problem))
        self._cap_notified = problems

    async def _async_notify_cap(self, problem: str, placeholders: dict[str, str]) -> None:
        key = CAP_NOTIFICATIONS[problem]
        text = await self._texts()
        # Batteries left out of the planning (e.g. cell balancing) explain a lack of room.
        missing = self._coordinator.missing_batteries(self._coordinator.data) if self._coordinator.data else []
        placeholders = placeholders | {
            "missing": text(
                "feed_in_cap_missing",
                batteries=", ".join(
                    text(f"battery_missing_{reason}", name=name) for name, reason in missing
                ),
            )
            if missing
            else ""
        }
        persistent_notification.async_create(
            self._hass,
            text(f"{key}_message", **placeholders),
            title=text(f"{key}_title", **placeholders),
            notification_id=_cap_notification_id(problem),
        )

    async def _texts(self):
        """Formatter for the translated exception strings (used as notification texts)."""
        strings = await async_get_translations(
            self._hass, self._hass.config.language, "exceptions", {DOMAIN}
        )

        def text(key: str, **placeholders: str) -> str:
            template = strings.get(f"component.{DOMAIN}.exceptions.{key}.message", key)
            return template.format(**placeholders)

        return text

    async def async_notify_balancing(
        self,
        battery: BatteryRuntime,
        result: str,
        initial_delta_mv: float | None,
        final_delta_mv: float | None,
        duration_s: float,
        end_reason: str | None = None,
    ) -> None:
        """Notification about the end of a cell balancing run."""
        text = await self._texts()

        def delta(value: float | None) -> str:
            return "–" if value is None else f"{value:.0f}"

        placeholders = {
            "name": battery.name,
            "before": delta(initial_delta_mv),
            "after": delta(final_delta_mv),
            "duration": f"{int(duration_s // 3600)} h {int(duration_s % 3600 // 60)} min",
        }
        if result == "done":
            title = text("balancing_done_title", **placeholders)
            ending = text(f"balancing_end_{end_reason or 'target'}")
            message = text("balancing_done_message", ending=ending, **placeholders)
        else:
            title = text("balancing_stopped_title", **placeholders)
            reason = text(f"balancing_reason_{result}")
            message = text("balancing_stopped_message", reason=reason, **placeholders)
        persistent_notification.async_create(
            self._hass,
            message,
            title=title,
            notification_id=f"{DOMAIN}_balancing_{battery.subentry_id}",
        )


    async def async_notify_target_missed(
        self, subentry_id: str, name: str, got: float, goal: float, unit: str, deadline: str
    ) -> None:
        """Notification: a consumer did not reach its daily target by the deadline."""
        text = await self._texts()
        comma = self._hass.config.language.startswith("de")

        def number(value: float) -> str:
            formatted = f"{value:.1f}"
            return formatted.replace(".", ",") if comma else formatted

        placeholders = {
            "name": name,
            "got": f"{number(got)} {unit}",
            "goal": f"{number(goal)} {unit}",
            "deadline": deadline,
        }
        persistent_notification.async_create(
            self._hass,
            text("target_missed_message", **placeholders),
            title=text("target_missed_title", **placeholders),
            notification_id=f"{DOMAIN}_target_{subentry_id}",
        )


def _clock(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%H:%M")


def _cap_notification_id(problem: str) -> str:
    return f"{DOMAIN}_{CAP_NOTIFICATIONS[problem]}"
