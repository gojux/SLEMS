"""Problems reported to the user outside the dashboard.

Repairs (Settings → Repairs) for ongoing problems; they disappear by
themselves when the problem is gone:

* a battery does not deliver the commanded power and is excluded
  (``delivery_monitor``),
* a battery could not be read for ``UNREADABLE_AFTER_S``,
* the grid meter is stale in operating mode active (the batteries follow their
  own logic meanwhile),
* feed-in cap: the batteries are too small for the space needed, there is not
  enough time or power left to feed in before the peak, their charge power is
  too low (energy would be curtailed), or the export has been above the limit
  for ``CAP_EXCEEDED_AFTER_S``.

Notifications (bell) for events: the end of an active cell balancing run,
except when the user cancelled it.

Issues are stored by Home Assistant across restarts; the first update after a
start removes the ones that no longer apply.
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
CAP_ISSUES = {
    "battery_too_small": "feed_in_cap_battery_too_small",
    "too_late": "feed_in_cap_too_late",
    "charge_power_too_low": "feed_in_cap_charge_power",
}
CAP_EXCEEDED_ISSUE = "feed_in_cap_exceeded"


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

    @callback
    def update(self, now: float, snapshot: SystemSnapshot | None = None) -> None:
        """Create or delete the repair issues (``now``: monotonic time)."""
        coordinator = self._coordinator
        wanted: dict[str, tuple[str, dict[str, str]]] = {}
        possible = {GRID_STALE_ISSUE, CAP_EXCEEDED_ISSUE, *CAP_ISSUES.values()}
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
        cap = snapshot.feed_in_cap if snapshot is not None else None
        if cap is not None:
            block = cap.next_block
            placeholders = {
                "limit": f"{cap.limit_w:.0f}",
                "peak": _clock(block.start) if block else "–",
                "required": f"{cap.required_space_wh / 1000:.1f}",
                "needed": f"{cap.export_needed_wh / 1000:.1f}",
                "possible": f"{cap.export_possible_wh / 1000:.1f}",
                "curtailed": f"{cap.curtailed_wh / 1000:.1f}",
            }
            for problem in cap.problems:
                wanted[CAP_ISSUES[problem]] = (CAP_ISSUES[problem], placeholders)
        since = coordinator.cap_exceeded_since
        if since is not None and now - since >= CAP_EXCEEDED_AFTER_S:
            wanted[CAP_EXCEEDED_ISSUE] = (
                CAP_EXCEEDED_ISSUE,
                {"limit": f"{coordinator.settings.feed_in_cap_limit_w:.0f}"},
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
        for issue_id in possible - wanted.keys():
            if self._active is None or issue_id in self._active:
                ir.async_delete_issue(self._hass, DOMAIN, issue_id)
        self._active = set(wanted)

    async def async_notify_balancing(
        self,
        battery: BatteryRuntime,
        result: str,
        initial_delta_mv: float | None,
        final_delta_mv: float | None,
        duration_s: float,
    ) -> None:
        """Notification about the end of a cell balancing run."""
        strings = await async_get_translations(
            self._hass, self._hass.config.language, "exceptions", {DOMAIN}
        )

        def text(key: str, **placeholders: str) -> str:
            template = strings.get(f"component.{DOMAIN}.exceptions.{key}.message", key)
            return template.format(**placeholders)

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
            message = text("balancing_done_message", **placeholders)
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


def _clock(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%H:%M")
